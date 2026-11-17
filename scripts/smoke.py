"""Real API + worker smoke test with an isolated database and subprocesses."""
import json,os,secrets,subprocess,sys,tempfile,time
from pathlib import Path
import httpx
from websockets.sync.client import connect
root=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='ops-smoke-') as directory:
    data=Path(directory); password=secrets.token_urlsafe(24)
    env={**os.environ,'OPS_DATA_DIR':directory,'OPS_DATABASE_URL':os.getenv('OPS_SMOKE_DATABASE_URL','sqlite:///'+str(data/'test.db')),'OPS_PORT':'18765','OPS_BASE_URL':'http://127.0.0.1:18765','OPS_MAX_CONCURRENCY':'3','OPS_PER_USER_CONCURRENCY':'2','OPS_BOOTSTRAP_PASSWORD':password}
    subprocess.run([sys.executable,'-m','ops.manage','init-admin','--password-env','OPS_BOOTSTRAP_PASSWORD'],cwd=root,env=env,check=True,stdout=subprocess.DEVNULL)
    api_log=(data/'api.log').open('w+');worker_log=(data/'worker.log').open('w+')
    api_proc=subprocess.Popen([sys.executable,'-m','uvicorn','ops.api:app','--host','127.0.0.1','--port','18765'],cwd=root,env=env,stdout=api_log,stderr=api_log)
    worker=subprocess.Popen([sys.executable,'-m','ops.worker'],cwd=root,env=env,stdout=worker_log,stderr=worker_log)
    try:
        client=httpx.Client(base_url='http://127.0.0.1:18765',timeout=20,trust_env=False)
        for _ in range(80):
            try:
                if client.get('/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise RuntimeError('API failed to start')
        login=client.post('/api/auth/login',json={'username':'admin','password':password});login.raise_for_status();jwt=login.json()['access_token'];client.headers['Authorization']='Bearer '+jwt
        def post(path,body=None):
            response=client.post('/api'+path,json=body);response.raise_for_status();return response.json()
        def script(name,code,deps=(),timeout=20):
            r=post('/resources',{'kind':'script','name':name,'content':{'language':'python','code':code,'dependencies':list(deps),'timeout':timeout}})
            post('/resources/'+r['id']+'/publish');return r
        def run(r):return post('/resources/'+r['id']+'/run',{})
        def wait(r):
            for _ in range(300):
                response=client.get('/api/runs/'+r['id']);response.raise_for_status();data=response.json()
                if data['status'] in ('success','failed','cancelled','interrupted'):return data
                time.sleep(.1)
            raise RuntimeError('run did not complete '+r['id'])
        child=script('child','from ops_sdk import result\nresult({"value":42})')
        parent=script('parent',f'from ops_sdk import result,run_script\nresult(run_script({child["id"]!r}))',[child['id']])
        parents=[run(parent) for _ in range(4)]
        for r in parents:
            result=wait(r);assert result['status']=='success',result;assert result['result']['value']=={'value':42};assert len(result['children'])==1
        print('PASS: queued executions and nested SDK calls (saturated user quota)')
        app=post('/resources',{'kind':'app','name':'workflow','content':{'nodes':[{'id':'s','kind':'script','script_id':child['id']},{'id':'p','kind':'parser','source':'$.nodes.s','parser':{'mode':'json','fields':[{'name':'number','path':'$.value','type':'integer'}]}}],'edges':[{'source':'s','target':'p'}],'timeout':20}})
        post('/resources/'+app['id']+'/publish');app_run=wait(run(app));assert app_run['status']=='success',app_run;assert app_run['result']['outputs']['p']=={'number':42}
        print('PASS: workflow -> script -> parser and child log drill-down')
        with connect('ws://127.0.0.1:18765/ws/runs/'+parents[0]['id'],proxy=None) as ws:
            ws.send(json.dumps({'token':jwt}));data=json.loads(ws.recv(timeout=5));assert data['status']=='success'
        print('PASS: actual Uvicorn WebSocket authentication and log stream')
        failure=script('failure','raise RuntimeError("expected smoke failure")');failed=wait(run(failure));assert failed['status']=='failed';assert 'expected smoke failure' in client.get('/api/runs/'+failed['id']+'/logs').text
        print('PASS: failure traceback persisted')
        long=script('cancel','import time\ntime.sleep(30)');longrun=run(long);time.sleep(.8);post('/runs/'+longrun['id']+'/cancel');assert wait(longrun)['status']=='cancelled'
        print('PASS: running process cancellation')
        assert client.get('/').status_code==200;assert '运维助手' in client.get('/').text
        print('PASS: built frontend served offline')
        interrupted=run(long)
        for _ in range(60):
            state=client.get('/api/runs/'+interrupted['id']).json()
            if state.get('process_pid'):break
            time.sleep(.1)
        assert state.get('process_pid'),state
        worker.kill();worker.wait(timeout=10)
        worker=subprocess.Popen([sys.executable,'-m','ops.worker'],cwd=root,env=env,stdout=worker_log,stderr=worker_log)
        assert wait(interrupted)['status']=='interrupted'
        print('PASS: abrupt worker restart marks interrupted work without replay')
    except Exception:
        for label,stream in [('API',api_log),('WORKER',worker_log)]:
            stream.flush();stream.seek(0);print(label,stream.read()[-6000:])
        raise
    finally:
        for process in (worker,api_proc):
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        api_log.close();worker_log.close()
