"""Explicit, non-business I/O-wait load test. Creates a private test script and runs."""
import argparse,getpass,json,time,urllib.request,urllib.error

def main():
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8765');p.add_argument('--username',default='admin');p.add_argument('--jobs',type=int,default=32);p.add_argument('--hold-seconds',type=int,default=30);p.add_argument('--deadline',type=int,default=900)
    a=p.parse_args()
    if not 1<=a.jobs<=300 or not 1<=a.hold_seconds<=300:raise SystemExit('jobs: 1–300; hold-seconds: 1–300')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));auth=''
    def request(path,body=None):
        req=urllib.request.Request(a.base_url.rstrip('/')+'/api'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+auth} if auth else {})})
        with opener.open(req,timeout=30) as response:return json.load(response)
    auth=request('/auth/login',{'username':a.username,'password':getpass.getpass('平台密码: ')})['access_token']
    r=request('/resources',{'kind':'script','name':'容量验证 '+time.strftime('%Y%m%d-%H%M%S'),'description':'仅模拟 I/O 等待，不访问外部业务系统。','tags':['容量验证'],'content':{'language':'python','code':f'import time,json\ntime.sleep({a.hold_seconds})\nprint(json.dumps({{"ok":True}}))\n','timeout':a.hold_seconds+120}})
    request('/resources/'+r['id']+'/publish',{})
    ids={request('/resources/'+r['id']+'/run',{})['id'] for _ in range(a.jobs)}
    started=time.monotonic();peak=0
    while time.monotonic()-started<a.deadline:
        runs=[r for r in request('/runs?limit=500') if r['id'] in ids]
        counts={}
        for r in runs:counts[r['status']]=counts.get(r['status'],0)+1
        peak=max(peak,counts.get('running',0));print(json.dumps({'elapsed':round(time.monotonic()-started,1),'states':counts,'peak_running':peak},ensure_ascii=False),flush=True)
        if len(runs)==a.jobs and all(r['status'] in ('success','failed','cancelled','interrupted') for r in runs):break
        time.sleep(1)
    else:raise SystemExit('测试超时；任务可能仍在排队或执行，请在运行记录中检查 / 取消')
    print(json.dumps({'jobs':a.jobs,'peak_running':peak,'success':counts.get('success',0),'elapsed_seconds':round(time.monotonic()-started,1),'note':'单用户模拟等待测试，不代表生产业务容量；实际峰值受全局和单用户配额限制。'},ensure_ascii=False,indent=2))
    if counts.get('success',0)!=a.jobs:raise SystemExit(1)

if __name__=="__main__":main()
