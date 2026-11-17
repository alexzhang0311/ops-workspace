import argparse, getpass, json, os, secrets, signal, subprocess, sys, time
from pathlib import Path
from sqlalchemy import select
from . import config
from .db import Session, User, Resource, Revision, init_db
from .auth import password_hash
from .resources import uid, make_snapshot


def fingerprint(pid):
    try:
        path=Path(f'/proc/{pid}/stat')
        if path.exists(): return path.read_text().rsplit(')',1)[1].split()[19]
        return subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError): return None

def status(name):
    path=config.DATA/(name+'.pid')
    if not path.exists(): return None
    data=json.loads(path.read_text())
    return data['pid'] if data.get('fingerprint') and fingerprint(data['pid'])==data['fingerprint'] else None

def start(name,args):
    if status(name): print(name+' 已运行'); return
    with (config.DATA/(name+'.log')).open('ab') as stream:
        proc=subprocess.Popen(args,cwd=config.ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    time.sleep(1)
    if proc.poll() is not None: raise SystemExit(f'{name} 启动失败，请查看 {config.DATA / (name+".log")}')
    (config.DATA/(name+'.pid')).write_text(json.dumps({'pid':proc.pid,'fingerprint':fingerprint(proc.pid)}))
    print(f'{name} 已启动，PID {proc.pid}')

def stop(name):
    pid=status(name)
    if not pid: print(name+' 未运行'); return
    os.kill(pid,signal.SIGTERM)
    for _ in range(100):
        if not status(name): break
        time.sleep(.2)
    else: raise SystemExit(f'{name} 尚未退出，请检查日志；未强制杀死任务')
    (config.DATA/(name+'.pid')).unlink(missing_ok=True); print(name+' 已停止')

def postgres(action):
    if not config.DB_URL.startswith('postgresql'): return
    binary=config.ROOT/'vendor'/'postgres'/'bin'/'pg_ctl'
    if not binary.exists(): return
    pgdata=config.DATA/'postgres'; socket=config.DATA/'pgsocket'; socket.mkdir(exist_ok=True,mode=0o700)
    if not pgdata.exists(): raise SystemExit('请先执行 sh scripts/install-offline.sh 初始化 PostgreSQL')
    result=subprocess.run([str(binary),'-D',str(pgdata),'status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if action=='start' and result.returncode:
        subprocess.run([str(binary),'-D',str(pgdata),'-l',str(config.DATA/'postgres.log'),'-w','start'],check=True)
    if action=='stop' and not result.returncode:
        subprocess.run([str(binary),'-D',str(pgdata),'-m','fast','-w','stop'],check=True)

def seed(user):
    with Session() as db:
        if db.scalar(select(Resource).where(Resource.name=='结构化巡检示例',Resource.owner_id==user.id)): return
        definitions=[('结构化巡检示例','演示输入参数、实时日志与 JSON 输出。','python','from ops_sdk import inputs, result\nimport datetime\np = inputs()\nprint("开始巡检：", p.get("service", "demo"), flush=True)\nresult({"service": p.get("service", "demo"), "healthy": True, "checked_at": datetime.datetime.now().isoformat()})\n',['基础工具','结构化输出'],[{'name':'service','type':'string','default':'demo'}]),('系统时间查询','无副作用的 Shell 示例，返回系统时间。','shell','#!/bin/sh\nset -eu\nprintf \'{"timestamp":"%s"}\\n\' "$(date +%s)"\n',['基础工具','Shell'],[])]
        first=None
        for name,description,language,code,tags,params in definitions:
            r=Resource(id=uid(),owner_id=user.id,kind='script',name=name,description=description,scope='platform',tags=tags,content={'language':language,'code':code,'parameters':params,'timeout':60,'dependencies':[],'variables':{}},version=0)
            db.add(r); db.flush(); snapshot=make_snapshot(db,user,r); r.version=1; db.add(Revision(resource_id=r.id,version=1,snapshot=snapshot)); db.flush()
            first=first or r
        app=Resource(id=uid(),owner_id=user.id,kind='app',name='服务巡检流程',description='执行巡检、解析结果，并根据健康状态判断。可替换为实际监控脚本。',scope='platform',tags=['业务监控','示例'],content={'timeout':120,'nodes':[{'id':'check','kind':'script','label':'执行巡检','script_id':first.id,'position':{'x':60,'y':140},'inputs':{'service':{'path':'$.inputs.service'}}},{'id':'parse','kind':'parser','label':'解析健康状态','position':{'x':300,'y':140},'source':'$.nodes.check','parser':{'mode':'json','fields':[{'name':'healthy','path':'$.healthy','type':'boolean','required':True}]}},{'id':'decision','kind':'condition','label':'判断服务健康','position':{'x':540,'y':140},'source':'$.nodes.parse.healthy','op':'eq','expected':True}],'edges':[{'id':'e1','source':'check','target':'parse'},{'id':'e2','source':'parse','target':'decision'}],'parameters':[{'name':'service','type':'string','default':'demo'}]},version=0)
        db.add(app); db.flush(); snapshot=make_snapshot(db,user,app); app.version=1; db.add(Revision(resource_id=app.id,version=1,snapshot=snapshot)); db.commit()

def main():
    parser=argparse.ArgumentParser(description='运维助手 management')
    parser.add_argument('command',choices=['start','stop','restart','status','init-admin','seed','db-init','doctor'])
    parser.add_argument('--username',default='admin'); parser.add_argument('--password-env')
    args=parser.parse_args()
    if args.command in ('init-admin','seed','db-init'):
        init_db()
        if args.command=='db-init': return
        with Session() as db:
            if args.command=='init-admin':
                if db.scalar(select(User).where(User.username==args.username)): raise SystemExit('账号已存在；请在界面重置密码')
                password=os.environ.get(args.password_env,'') if args.password_env else getpass.getpass('管理员密码（至少 10 位）: ')
                u=User(username=args.username,password=password_hash(password),role='admin'); db.add(u); db.commit(); print('管理员已创建，无默认密码')
            else:
                u=db.scalar(select(User).where(User.username==args.username,User.role=='admin'))
                if not u: raise SystemExit('请先创建管理员')
                seed(u); print('示例平台能力已创建')
        return
    if args.command=='doctor':
        import platform,ssl
        print(json.dumps({'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'libc':platform.libc_ver(),'openssl':ssl.OPENSSL_VERSION,'database':'postgresql' if config.DB_URL.startswith('postgresql') else 'sqlite (development only)','data':str(config.DATA),'shared_account':True,'max_concurrency':config.MAX_CONCURRENCY,'per_user_concurrency':config.PER_USER_CONCURRENCY},ensure_ascii=False,indent=2)); return
    if args.command=='status':
        for name in ('api','worker'): print(name+': '+('running '+str(status(name)) if status(name) else 'stopped'))
        return
    if args.command in ('stop','restart'):
        stop('worker'); stop('api'); postgres('stop')
    if args.command in ('start','restart'):
        postgres('start'); init_db()
        start('api',[sys.executable,'-m','uvicorn','ops.api:app','--host',config.HOST,'--port',str(config.PORT),'--workers','1','--no-access-log'])
        start('worker',[sys.executable,'-m','ops.worker'])
        print(f'管理台：http://{config.HOST}:{config.PORT}')

if __name__=='__main__': main()
