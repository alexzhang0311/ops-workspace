"""Logical database backup plus runtime/config files. Run in a maintenance window."""
import os,shutil,sqlite3,subprocess,tarfile
from datetime import datetime
from sqlalchemy.engine import make_url
from sqlalchemy import select,func
from . import config
from .db import Session,Run
from .worker import TERMINAL

def main():
    with Session() as db:
        active=db.scalar(select(func.count()).select_from(Run).where(Run.status.not_in(TERMINAL)))
        if active: raise SystemExit('仍有排队或运行中的任务，请暂停计划并等待执行结束后备份')
    target=config.DATA/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
    target.mkdir(parents=True,mode=0o700)
    url=make_url(config.DB_URL)
    if url.drivername.startswith('sqlite'):
        source=sqlite3.connect(url.database);destination=sqlite3.connect(target/'database.sqlite')
        source.backup(destination);destination.close();source.close()
    else:
        binary=config.ROOT/'vendor'/'postgres'/'bin'/'pg_dump'
        executable=str(binary) if binary.exists() else shutil.which('pg_dump')
        if not executable: raise SystemExit('未找到 pg_dump')
        args=[executable,'--no-owner','--no-acl','-h',url.host or url.query.get('host','127.0.0.1'),'-p',str(url.port or url.query.get('port',5432)),'-U',url.username or 'ops','-d',url.database]
        with (target/'database.sql').open('wb') as f:subprocess.run(args,env={**os.environ,'PGPASSWORD':url.password or ''},stdout=f,check=True)
    with tarfile.open(target/'application-state.tar.gz','w:gz') as tar:
        if (config.ROOT/'.env').exists():tar.add(config.ROOT/'.env',arcname='.env')
        for name in ['.jwt-key','.metrics-key','.ai-key','logs','runs']:
            path=config.DATA/name
            if path.exists():tar.add(path,arcname='var/'+name)
    for path in target.iterdir():path.chmod(0o600)
    print('备份已完成:',target)

if __name__=='__main__':main()
