"""Initialize bundled PostgreSQL as the current unprivileged account."""
import os, secrets, shlex, subprocess
from pathlib import Path
from urllib.parse import quote
root=Path(__file__).resolve().parent.parent
os.chdir(root)
if os.geteuid()==0: raise SystemExit('请使用 app 账号安装，不要使用 root')
pg=root/'vendor'/'postgres'; data=root/'var'; data.mkdir(exist_ok=True,mode=0o700)
if not (pg/'bin'/'initdb').exists(): raise SystemExit('离线包缺少 PostgreSQL 运行时，请在兼容的 Linux 构建机打包')
pgdata=data/'postgres'; socket=data/'pgsocket'; socket.mkdir(exist_ok=True,mode=0o700)
password=secrets.token_urlsafe(32); pw=data/'.pg-init-password'; pw.write_text(password); pw.chmod(0o600)
try:
    subprocess.run([str(pg/'bin'/'initdb'),'-D',str(pgdata),'--username=ops','--pwfile='+str(pw),'--auth-local=scram-sha-256','--auth-host=scram-sha-256','--encoding=UTF8','--locale=C'],check=True)
finally: pw.unlink(missing_ok=True)
with (pgdata/'postgresql.conf').open('a') as f:
    f.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\nmax_connections = 60\nshared_buffers = '256MB'\n")
    f.write("unix_socket_directories = '"+str(socket).replace("'","''")+"'\n")
subprocess.run([str(pg/'bin'/'pg_ctl'),'-D',str(pgdata),'-l',str(data/'postgres.log'),'-w','start'],check=True)
env={**os.environ,'PGPASSWORD':password}
subprocess.run([str(pg/'bin'/'createdb'),'-h','127.0.0.1','-p','55432','-U','ops','opsstudio'],env=env,check=True)
values={'OPS_HOST':'127.0.0.1','OPS_PORT':'8765','OPS_BASE_URL':'http://127.0.0.1:8765','OPS_DATABASE_URL':f'postgresql+psycopg://ops:{quote(password)}@127.0.0.1:55432/opsstudio','OPS_MAX_CONCURRENCY':'32','OPS_PER_USER_CONCURRENCY':'16'}
fd=os.open(root/'.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
    for k,v in values.items(): f.write(k+'='+shlex.quote(v)+'\n')
print('PostgreSQL 已初始化，仅监听本机 55432；初始并发 32，可在容量验收后调至 300。')
