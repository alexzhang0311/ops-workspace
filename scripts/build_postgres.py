"""Offline PostgreSQL compilation. No downloads or root privileges required."""
import hashlib,os,shutil,subprocess,tarfile,tempfile
from pathlib import Path
root=Path(__file__).resolve().parent.parent
prefix=root/'vendor'/'postgres'
if (prefix/'bin'/'postgres').exists(): raise SystemExit(0)
if not shutil.which('gcc') or not shutil.which('make'): raise SystemExit('缺少 gcc 或 GNU make；请由系统管理员离线提供构建工具，或使用预编译 PostgreSQL 离线包')
source=root/'vendor'/'sources'/'postgresql-16.16.tar.gz'
if not source.exists(): raise SystemExit('缺少 PostgreSQL 源码包')
expected=Path(str(source)+'.sha256').read_text().split()[0]
if hashlib.sha256(source.read_bytes()).hexdigest()!=expected: raise SystemExit('PostgreSQL 源码校验失败')
with tempfile.TemporaryDirectory(prefix='ops-pg-build-') as temp:
    with tarfile.open(source) as archive: archive.extractall(temp,filter='data')
    build=Path(temp)/'postgresql-16.16'
    subprocess.run(['sh',str(build/'configure'),'--prefix='+str(prefix),'--without-readline','--without-zlib','--without-icu'],cwd=build,check=True)
    subprocess.run(['make','-j',str(min(os.cpu_count() or 2,8))],cwd=build,check=True)
    subprocess.run(['make','install'],cwd=build,check=True)
print('PostgreSQL 已离线编译到',prefix)
