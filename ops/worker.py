"""Single-host async worker. Only one instance holds the worker lock."""
import asyncio, copy, contextlib, fcntl, json, os, signal, sys, time, traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from croniter import croniter
from sqlalchemy import select, update
from . import config
from .db import Session, Run, User, Resource, Schedule, Setting, now, init_db, audit
from .auth import token, can_read
from .resources import enqueue, validate_inputs, uid
from .parsers import parse_output, get_path, condition
from .run_results import application_final_result

TERMINAL={'success','failed','cancelled','interrupted','skipped'}
ACTIVE={'running','waiting_child'}
PROCESSES={}

def log_path(rid): return config.DATA / 'logs' / (rid+'.jsonl')
def log(rid, stream, text, secrets=()):
    path=log_path(rid); path.parent.mkdir(exist_ok=True,mode=0o700)
    if path.exists() and path.stat().st_size>=config.LOG_LIMIT: return
    text=str(text)
    for secret in secrets:
        if secret: text=text.replace(str(secret),'[REDACTED]')
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'time':now(),'stream':stream,'text':text[:65536]},ensure_ascii=False)+'\n')

def finish(rid,status,result=None,error=''):
    with Session() as db:
        r=db.get(Run,rid)
        if r:
            r.status=status
            if result is not None: r.result=result
            r.error=error[:8192]; r.finished_at=now(); db.commit()
    log(rid,'system',error or status)

async def terminate(proc):
    if proc.returncode is not None:
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
        return
    with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGTERM)
    try: await asyncio.wait_for(proc.wait(),3)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
        await proc.wait()

async def run_script(r):
    c=r.snapshot['content']; work=config.DATA/'runs'/r.id; work.mkdir(parents=True,mode=0o700)
    language=c['language']; path=work/('script.py' if language=='python' else 'script.sh')
    path.write_text(c['code'],encoding='utf-8'); path.chmod(0o600)
    interpreter=c.get('interpreter') or (sys.executable if language=='python' else '/bin/sh')
    if not os.path.isfile(interpreter) or not os.access(interpreter,os.X_OK): raise ValueError('解释器不存在或不可执行: '+interpreter)
    with Session() as db:
        user=db.get(User,r.owner_id)
        auth=token(user,'runtime',seconds=c.get('timeout',300)+60,run_id=r.id)
    values={**c.get('variables',{}),**r.inputs}
    secrets=[str(values[p['name']]) for p in c.get('parameters',[]) if p.get('secret') and p['name'] in values]
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(work),'LANG':'en_US.UTF-8','PYTHONUNBUFFERED':'1','PYTHONPATH':str(config.ROOT/'sdk'),'OPS_INPUT_JSON':json.dumps(values,ensure_ascii=False),'OPS_RUN_ID':r.id,'OPS_API_URL':config.BASE_URL,'OPS_RUN_TOKEN':auth,'OPS_RESULT_FILE':str(work/'result.json')}
    args=[interpreter,str(path)]
    for arg in c.get('args',[]):
        args.append(str(values.get(arg[2:-1],'')) if isinstance(arg,str) and arg.startswith('${') and arg.endswith('}') else str(arg))
    proc=await asyncio.create_subprocess_exec(*args,cwd=work,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
    PROCESSES[r.id]=proc
    from .manage import fingerprint
    with Session() as db:
        entry=db.get(Run,r.id); entry.process_pid=proc.pid; entry.process_start=fingerprint(proc.pid); db.commit()
    stdout=[]; captured=0; log(r.id,'system',f'进程已启动，PID={proc.pid}，解释器={interpreter}')
    async def pump(reader,stream):
        nonlocal captured
        decoder=__import__('codecs').getincrementaldecoder('utf-8')(errors='replace')
        while chunk:=await reader.read(8192):
            text=decoder.decode(chunk)
            log(r.id,stream,text,secrets)
            if stream=='stdout' and captured<config.RESULT_LIMIT:
                stdout.append(text[:config.RESULT_LIMIT-captured]); captured+=len(text)
    pumps=[asyncio.create_task(pump(proc.stdout,'stdout')),asyncio.create_task(pump(proc.stderr,'stderr'))]
    start=time.monotonic(); reason=''
    try:
        while proc.returncode is None:
            await asyncio.sleep(.25)
            with Session() as db:
                current=db.get(Run,r.id)
                user=db.get(User,r.owner_id)
                if current.cancel_requested or not user.active: reason='cancelled'
            if time.monotonic()-start>c.get('timeout',300): reason='timeout'
            if reason: await terminate(proc); break
        # Close inherited pipe descriptors before waiting for log readers.
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
        await proc.wait()
        await asyncio.gather(*pumps)
        if reason=='cancelled': finish(r.id,'cancelled'); return
        if reason=='timeout': raise TimeoutError('脚本运行超时')
        if proc.returncode!=0: raise RuntimeError(f'脚本退出码 {proc.returncode}，请查看 stderr 日志')
        output=''.join(stdout)
        result_file=work/'result.json'
        if result_file.exists():
            if result_file.stat().st_size>config.RESULT_LIMIT: raise ValueError('结构化结果超过 1 MiB')
            data=json.loads(result_file.read_text())
        else:
            try: data=json.loads(output)
            except ValueError: data=output
        def redact(value):
            if isinstance(value,dict): return {k:redact(v) for k,v in value.items()}
            if isinstance(value,list): return [redact(v) for v in value]
            if str(value) in secrets: return '[REDACTED]'
            if isinstance(value,str):
                for secret in secrets:
                    if secret: value=value.replace(secret,'[REDACTED]')
            return value
        finish(r.id,'success',redact({'value':data,'stdout':output}))
    finally:
        await terminate(proc)
        for task in pumps:
            if not task.done(): task.cancel()
        PROCESSES.pop(r.id,None)

async def run_app(r):
    c=r.snapshot['content']; nodes={n['id']:n for n in c['nodes']}; edges=c.get('edges',[])
    states={i:'pending' for i in nodes}; outputs={}; children={}; details={}; started=time.monotonic()
    secrets=[str(r.inputs[p['name']]) for p in c.get('parameters',[]) if p.get('secret') and p['name'] in r.inputs]

    def redacted(value):
        if isinstance(value,dict): return {k:redacted(v) for k,v in value.items()}
        if isinstance(value,list): return [redacted(v) for v in value]
        if str(value) in secrets: return '[REDACTED]'
        if isinstance(value,str):
            for secret in secrets:
                if secret: value=value.replace(secret,'[REDACTED]')
        return value

    def result(final=False):
        status='success' if final and all(s in ('success','skipped') for s in states.values()) else 'running'
        final_result=application_final_result(c,states,outputs,status)
        return redacted(copy.deepcopy({'nodes':states,'outputs':outputs,'node_details':details,'final_result':final_result,**({'value':outputs} if final else {})}))

    def node_log(nid,stream,text):
        text=redacted(text)
        details.setdefault(nid,{}).setdefault('logs',[]).append({'time':now(),'stream':stream,'text':text})
        log(r.id,stream,f'{nodes[nid].get("label",nid)}: {text}')

    while any(s not in TERMINAL for s in states.values()):
        with Session() as db:
            current=db.get(Run,r.id); user=db.get(User,r.owner_id)
            cancel=current.cancel_requested or not user.active
            timeout=time.monotonic()-started>c.get('timeout',300)
            if cancel or timeout:
                db.execute(update(Run).where(Run.parent_id==r.id,Run.status.not_in(TERMINAL)).values(cancel_requested=True)); db.commit()
                finish(r.id,'cancelled' if cancel else 'failed',result(),'应用超时' if timeout else '应用已取消'); return
            for nid,cid in list(children.items()):
                child=db.get(Run,cid)
                if child.status in TERMINAL:
                    states[nid]=child.status; outputs[nid]=child.result.get('value'); del children[nid]
                    node_log(nid,'node',f'{child.status}；运行 {cid}')
            for nid,node in nodes.items():
                if states[nid]!='pending': continue
                inbound=[e for e in edges if e['target']==nid]
                if any(states[e['source']] not in TERMINAL for e in inbound): continue
                eligible=[e for e in inbound if states[e['source']]=='success' and (e.get('when') not in ('true','false') or bool(outputs.get(e['source']))==(e['when']=='true'))]
                if any(states[e['source']] in ('failed','cancelled','interrupted') for e in inbound) or (inbound and not eligible):
                    states[nid]='skipped'; node_log(nid,'system','未执行：上游失败或分支条件未满足'); continue
                context={'inputs':r.inputs,'nodes':outputs}
                details[nid]={'started_at':now()}
                node_log(nid,'system','开始执行')
                try:
                    if node['kind']=='script':
                        snap=r.snapshot['dependencies'][node['script_id']]
                        inputs={k:(get_path(context,v['path']) if isinstance(v,dict) and 'path' in v else v) for k,v in node.get('inputs',{}).items()}
                        # Record supplied inputs even when validation prevents creation of a child run.
                        secret_names={p['name'] for p in snap['content'].get('parameters',[]) if p.get('secret')}
                        details[nid]['inputs']={k:'[REDACTED]' if k in secret_names else v for k,v in inputs.items()}
                        inputs=validate_inputs(snap,inputs)
                        child=Run(id=uid(),owner_id=r.owner_id,resource_id=snap['id'],name=snap['name'],kind='script',snapshot=snap,inputs=inputs,parent_id=r.id,node_id=nid,depth=r.depth,trigger='workflow')
                        db.add(child); db.flush(); children[nid]=child.id; states[nid]='queued'
                        node_log(nid,'system','已创建子任务，等待执行')
                    else:
                        value=get_path(context,node.get('source','$.inputs'))
                        details[nid]['inputs']={'value':value,**({'op':node.get('op','eq'),'expected':node.get('expected')} if node['kind']=='condition' else {})}
                        outputs[nid]=parse_output(value,node.get('parser',{})) if node['kind']=='parser' else condition(value,node.get('op','eq'),node.get('expected'))
                        details[nid]['output']=outputs[nid]; states[nid]='success'
                        node_log(nid,'system','执行成功')
                except Exception as e:
                    states[nid]='failed'; details[nid]['error']=redacted(str(e))
                    node_log(nid,'stderr',traceback.format_exc())
                if states[nid] in TERMINAL: details[nid]['finished_at']=now()
            current.result=result(); db.commit()
        await asyncio.sleep(.25)
    failed=any(s in ('failed','cancelled','interrupted') for s in states.values())
    finish(r.id,'failed' if failed else 'success',result(final=True),'存在失败节点，请下钻查看日志' if failed else '')

async def execute(r):
    try:
        if r.kind=='script': await run_script(r)
        else: await run_app(r)
    except asyncio.CancelledError:
        finish(r.id,'interrupted',error='执行器停止，未自动重试以避免重复副作用'); raise
    except Exception as e:
        log(r.id,'stderr',traceback.format_exc())
        finish(r.id,'failed',error=str(e))


def schedule_tick(db):
    timestamp=time.time()
    for s in db.scalars(select(Schedule).where(Schedule.enabled==True,Schedule.next_at<=timestamp)).all():
        u=db.get(User,s.owner_id); r=db.get(Resource,s.resource_id)
        try:
            if not u or not u.active or not can_read(u,r):
                s.enabled=False; s.error='账号或资源不可用'; continue
            previous=db.get(Run,s.last_run) if s.last_run else None
            if previous and previous.status not in TERMINAL:
                s.error='上次运行未结束，本次已跳过'
            else:
                run=enqueue(db,u,r,s.inputs,trigger='schedule'); s.last_run=run.id; s.error=''
            # Coalesce missed triggers; never replay all missed production jobs.
            s.next_at=croniter(s.cron,datetime.fromtimestamp(timestamp,ZoneInfo(s.timezone))).get_next(datetime).timestamp()
        except Exception as e:
            s.enabled=False; s.error=str(e)[:1000]
    db.commit()

async def main():
    init_db()
    lock=(config.DATA/'worker.lock').open('w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('已有任务执行器运行')
    with Session() as db:
        for r in db.scalars(select(Run).where(Run.status.in_(ACTIVE))).all():
            from .manage import fingerprint
            if r.process_pid and r.process_start and fingerprint(r.process_pid)==r.process_start:
                with contextlib.suppress(ProcessLookupError): os.killpg(r.process_pid,signal.SIGKILL)
            r.status='interrupted'; r.finished_at=now(); r.error='执行器重启；未自动重试'
        # Queued descendants of interrupted applications must not be replayed.
        for r in db.scalars(select(Run).where(Run.status=='queued',Run.parent_id.is_not(None))).all():
            parent=db.get(Run,r.parent_id)
            if parent and parent.status in TERMINAL: r.status='cancelled'; r.finished_at=now()
        db.commit()
    tasks={}; loop=asyncio.get_running_loop(); stop=asyncio.Event()
    for sig in (signal.SIGTERM,signal.SIGINT): loop.add_signal_handler(sig,stop.set)
    last_schedule=0
    try:
        while not stop.is_set():
            tasks={k:v for k,v in tasks.items() if not v.done()}
            with Session() as db:
                if time.monotonic()-last_schedule>=1: schedule_tick(db); last_schedule=time.monotonic()
                # Waiting callers lend their execution slot to their child; resume only after a slot is available.
                running=db.scalars(select(Run).where(Run.kind=='script',Run.status=='running')).all()
                counts=Counter(x.owner_id for x in running); capacity=config.MAX_CONCURRENCY-len(running)
                for parent in db.scalars(select(Run).where(Run.status=='waiting_child')).all():
                    child=db.scalar(select(Run).where(Run.parent_id==parent.id).order_by(Run.created_at.desc()))
                    if child and child.status in TERMINAL and capacity>0 and counts[parent.owner_id]<config.PER_USER_CONCURRENCY:
                        parent.status='running'; capacity-=1; counts[parent.owner_id]+=1
                queued=db.scalars(select(Run).where(Run.status=='queued').order_by(Run.depth.desc(),Run.created_at).limit(2000)).all()
                for r in queued:
                    u=db.get(User,r.owner_id)
                    parent=db.get(Run,r.parent_id) if r.parent_id else None
                    if r.cancel_requested or not u or not u.active or (parent and (parent.cancel_requested or parent.status in TERMINAL)):
                        r.status='cancelled'; r.finished_at=now(); continue
                    if r.kind=='script' and (capacity<=0 or counts[r.owner_id]>=config.PER_USER_CONCURRENCY): continue
                    if r.kind=='app' and sum(1 for t in tasks.values() if not t.done())>=config.MAX_CONCURRENCY+100: continue
                    r.status='running'; r.started_at=now(); db.flush()
                    if r.kind=='script': capacity-=1; counts[r.owner_id]+=1
                    tasks[r.id]=asyncio.create_task(execute(r))
                heartbeat=db.get(Setting,'worker')
                if not heartbeat: heartbeat=Setting(key='worker'); db.add(heartbeat)
                heartbeat.value={'heartbeat':time.time(),'max_concurrency':config.MAX_CONCURRENCY,'active':len(tasks)}
                db.commit()
            await asyncio.sleep(.25)
    finally:
        for task in tasks.values(): task.cancel()
        await asyncio.gather(*tasks.values(),return_exceptions=True)
        for proc in list(PROCESSES.values()): await terminate(proc)
        lock.close()

if __name__=='__main__': asyncio.run(main())
