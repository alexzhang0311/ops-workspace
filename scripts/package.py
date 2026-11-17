"""Create a self-contained Linux x86_64 / CPython 3.12 offline bundle."""
import argparse, hashlib, json, re, shutil, subprocess, sys, tarfile, urllib.request, zipfile
from email.parser import BytesParser
from pathlib import Path
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
ROOT=Path(__file__).resolve().parent.parent
VERSION=json.loads((ROOT/'frontend'/'package.json').read_text())['version']
PG_VERSION='16.16'

def download_postgres():
    directory=ROOT/'dist'/'sources'; directory.mkdir(parents=True,exist_ok=True)
    name=f'postgresql-{PG_VERSION}.tar.gz'; base=f'https://ftp.postgresql.org/pub/source/v{PG_VERSION}/'
    checksum=urllib.request.urlopen(base+name+'.sha256',timeout=60).read().decode().split()[0]
    path=directory/name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:
        with urllib.request.urlopen(base+name,timeout=120) as response, path.open('wb') as output: shutil.copyfileobj(response,output)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=checksum: raise SystemExit('PostgreSQL 源码校验失败')
    (directory/(name+'.sha256')).write_text(checksum+'  '+name+'\n')
    print('PostgreSQL source SHA-256 verified:',PG_VERSION)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepare',action='store_true',help='Download target wheels and PostgreSQL source on a networked build machine')
    parser.add_argument('--skip-build',action='store_true')
    parser.add_argument('--postgres-runtime',type=Path,help='Optional prebuilt PostgreSQL prefix from matching CentOS 7 x86_64 builder')
    parser.add_argument('--source-only-download',action='store_true')
    args=parser.parse_args()
    if args.source_only_download: download_postgres(); return
    if not args.skip_build:
        subprocess.run(['npm','ci'],cwd=ROOT/'frontend',check=True)
        subprocess.run(['npm','run','build'],cwd=ROOT/'frontend',check=True)
    wheels=ROOT/'dist'/'wheelhouse'
    if args.prepare:
        subprocess.run([sys.executable,'-m','pip','download','-r',str(ROOT/'requirements.lock'),'--dest',str(wheels),'--only-binary=:all:','--platform','manylinux2014_x86_64','--platform','manylinux_2_17_x86_64','--python-version','3.12','--implementation','cp','--abi','cp312'],check=True)
        download_postgres()
    if not list(wheels.glob('*.whl')): raise SystemExit('缺少 wheelhouse，请先使用 --prepare')
    if not (ROOT/'frontend'/'dist'/'index.html').exists(): raise SystemExit('请先构建前端')
    source=ROOT/'dist'/'sources'/f'postgresql-{PG_VERSION}.tar.gz'
    if not args.postgres_runtime and not source.exists(): raise SystemExit('缺少 PostgreSQL 源码，请使用 --prepare')
    if not args.postgres_runtime:
        expected=Path(str(source)+'.sha256').read_text().split()[0]
        if hashlib.sha256(source.read_bytes()).hexdigest()!=expected: raise SystemExit('PostgreSQL 源码校验失败')
    stage=ROOT/'dist'/f'ops-studio-{VERSION}'
    if stage.exists(): shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in ['ops','sdk','scripts','docs','frontend/dist']:
        shutil.copytree(ROOT/name,stage/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['server.sh','.env.example','requirements.lock','README.md']:
        shutil.copy2(ROOT/name,stage/name)
    shutil.copytree(wheels,stage/'wheelhouse')
    locked=[]; packages={}; dependency_edges=[]
    for wheel in sorted(wheels.glob('*.whl')):
        if not ('none-any.whl' in wheel.name or any(tag in wheel.name for tag in ('manylinux1_x86_64','manylinux2010_x86_64','manylinux2014_x86_64')) or any(int(v)<=17 for v in re.findall(r'manylinux_2_(\d+)_x86_64',wheel.name))): raise SystemExit('非目标平台 wheel: '+wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            metadata=BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
        packages[canonicalize_name(metadata['Name'])]=metadata['Version']
        dependency_edges.extend((metadata['Name'],r) for r in metadata.get_all('Requires-Dist',[]))
        locked.append(f'{metadata["Name"]}=={metadata["Version"]} --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}')
    target={**default_environment(),'sys_platform':'linux','os_name':'posix','platform_machine':'x86_64','platform_system':'Linux','platform_release':'3.10.0','python_version':'3.12','python_full_version':'3.12.7','platform_python_implementation':'CPython','implementation_name':'cpython','implementation_version':'3.12.7','extra':''}
    for parent,raw in dependency_edges:
        required=Requirement(raw)
        if required.marker and not required.marker.evaluate(target): continue
        actual=packages.get(canonicalize_name(required.name))
        if not actual or actual not in required.specifier: raise SystemExit(f'Linux 目标依赖不完整: {parent} -> {required}，已打包版本 {actual}')
    (stage/'target-dependencies.json').write_text(json.dumps({'environment':target,'packages':packages},indent=2))
    (stage/'requirements-offline.lock').write_text('\n'.join(locked)+'\n')
    vendor=stage/'vendor';vendor.mkdir()
    if args.postgres_runtime:
        header=(args.postgres_runtime/'bin'/'postgres').read_bytes()[:64]
        if header[:4]!=b'\x7fELF' or header[4:6]!=bytes([2,1]) or int.from_bytes(header[18:20],'little')!=62: raise SystemExit('PostgreSQL 必须为 Linux x86_64 ELF 运行时')
        shutil.copytree(args.postgres_runtime,vendor/'postgres',symlinks=False)
    else:
        (vendor/'sources').mkdir()
        shutil.copy2(source,vendor/'sources'/source.name)
        shutil.copy2(str(source)+'.sha256',vendor/'sources'/(source.name+'.sha256'))
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True)
    manifest={'version':VERSION,'source_commit':commit.stdout.strip() if commit.returncode==0 else None,'target':'Linux x86_64 glibc 2.17 CPython 3.12','postgres':PG_VERSION,'postgres_delivery':'binary' if args.postgres_runtime else 'source-build-offline','sha256':{str(p.relative_to(stage)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.rglob('*')) if p.is_file()}}
    (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    archive=ROOT/'dist'/f'ops-studio-{VERSION}-linux-x86_64-py312.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:tar.add(stage,arcname=stage.name)
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest();Path(str(archive)+'.sha256').write_text(checksum+'  '+archive.name+'\n')
    print(str(archive));print('SHA-256:',checksum)

if __name__=='__main__': main()
