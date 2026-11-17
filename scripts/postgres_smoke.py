"""Run smoke.py against a fresh local PostgreSQL cluster; never touch an existing DB."""
import os,secrets,shutil,subprocess,sys,tempfile
from pathlib import Path
from urllib.parse import quote
root=Path(__file__).resolve().parent.parent
binary=Path(os.environ.get('OPS_TEST_PG_BIN',str(root/'vendor'/'postgres'/'bin') if (root/'vendor'/'postgres'/'bin'/'initdb').exists() else '/opt/homebrew/bin'))
with tempfile.TemporaryDirectory(prefix='ops-pg-smoke-') as directory:
    path=Path(directory);pgdata=path/'pgdata';socket=path/'socket';socket.mkdir()
    password=secrets.token_urlsafe(24);pw=path/'password';pw.write_text(password);pw.chmod(0o600)
    subprocess.run([str(binary/'initdb'),'-D',str(pgdata),'--no-locale','--encoding=UTF8','--auth=scram-sha-256','--username=ops_test','--pwfile='+str(pw)],check=True,stdout=subprocess.DEVNULL)
    with (pgdata/'postgresql.conf').open('a') as f:f.write("\nlisten_addresses = ''\nport = 55439\nunix_socket_directories = '"+str(socket)+"'\n")
    subprocess.run([str(binary/'pg_ctl'),'-D',str(pgdata),'-l',str(path/'pg.log'),'-w','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        subprocess.run([str(binary/'createdb'),'-h',str(socket),'-p','55439','-U','ops_test','ops_smoke'],env={**os.environ,'PGPASSWORD':password},check=True)
        env={**os.environ,'OPS_SMOKE_DATABASE_URL':f'postgresql+psycopg://ops_test:{password}@/ops_smoke?host={quote(str(socket))}&port=55439'}
        subprocess.run([sys.executable,str(root/'scripts'/'smoke.py')],cwd=root,env=env,check=True)
        print('PASS: PostgreSQL integration (isolated local cluster)')
    finally:subprocess.run([str(binary/'pg_ctl'),'-D',str(pgdata),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
