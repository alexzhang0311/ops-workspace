import asyncio,json,time
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from ops.api import read_logs
from ops.db import Session,Run,User,Schedule,Audit,now
from ops.auth import token
from ops.resources import validate_content
from ops.parsers import parse_output
from ops.worker import execute,schedule_tick,TERMINAL


def create(client,person,code='print(\'{"ok":true}\')',**extra):
    body={'kind':'script','name':'test','content':{'language':'python','code':code,'timeout':10,'parameters':[],'dependencies':[]},**extra}
    response=client.post('/api/resources',json=body,headers=person['headers']);assert response.status_code==200,response.text
    return response.json()

def publish(client,person,r):
    response=client.post('/api/resources/'+r['id']+'/publish',headers=person['headers']);assert response.status_code==200,response.text
    return response.json()

def start(client,person,r,inputs=None,test=False):
    response=client.post('/api/resources/'+r['id']+'/run',headers=person['headers'],json={'inputs':inputs or {},'test':test});assert response.status_code==200,response.text
    return response.json()

def execute_one(rid):
    with Session() as db:
        r=db.get(Run,rid);r.status='running';r.started_at=now();db.commit()
    asyncio.run(execute(r))
    with Session() as db:return db.get(Run,rid)

def test_private_resources_runs_versions_and_logs_are_isolated(client,people):
    admin,alice,bob=people;r=publish(client,alice,create(client,alice));run=start(client,alice,r)
    assert client.get('/api/resources',headers=bob['headers']).json()==[]
    for path in ['/resources/'+r['id'],'/resources/'+r['id']+'/versions','/runs/'+run['id'],'/runs/'+run['id']+'/logs']:
        assert client.get('/api'+path,headers=bob['headers']).status_code==404
    assert client.put('/api/resources/'+r['id'],json=r,headers=bob['headers']).status_code==404
    assert client.get('/api/resources/'+r['id'],headers=admin['headers']).status_code==200

def test_platform_is_shared_but_not_writable(client,people):
    admin,alice,_=people;r=publish(client,admin,create(client,admin,scope='platform'))
    assert client.get('/api/resources/'+r['id'],headers=alice['headers']).status_code==200
    assert client.put('/api/resources/'+r['id'],json=r,headers=alice['headers']).status_code==404
    assert client.delete('/api/resources/'+r['id'],headers=alice['headers']).status_code==404
    assert client.post('/api/resources',json={'kind':'script','name':'illegal','scope':'platform','content':r['content']},headers=alice['headers']).status_code==403

@pytest.mark.parametrize(('kind','content'),[
    ('script',{'language':'python','code':'print(1)','timeout':10,'parameters':[],'variables':{},'dependencies':[]}),
    ('app',{'nodes':[{'id':'parse','kind':'parser','source':'$.inputs','parser':{'mode':'json'}}],'edges':[],'timeout':10,'parameters':[],'variables':{}}),
])
def test_delete_script_or_app_hides_resource_disables_schedule_and_preserves_history(client,people,kind,content):
    alice=people[1]
    response=client.post('/api/resources',json={'kind':kind,'name':'to-delete','content':content},headers=alice['headers'])
    assert response.status_code==200,response.text
    resource=publish(client,alice,response.json())
    run=start(client,alice,resource)
    schedule=client.post('/api/schedules',headers=alice['headers'],json={'resource_id':resource['id'],'cron':'* * * * *'})
    assert schedule.status_code==200,schedule.text

    deleted=client.delete('/api/resources/'+resource['id'],headers=alice['headers'])
    assert deleted.status_code==200,deleted.text
    assert deleted.json()=={'ok':True,'disabled_schedules':1}
    assert all(item['id']!=resource['id'] for item in client.get('/api/resources',headers=alice['headers']).json())
    assert client.get('/api/resources/'+resource['id'],headers=alice['headers']).status_code==404
    assert client.put('/api/resources/'+resource['id'],json=resource,headers=alice['headers']).status_code==404
    assert client.post('/api/resources/'+resource['id']+'/publish',headers=alice['headers']).status_code==404
    assert client.post('/api/resources/'+resource['id']+'/run',json={'inputs':{}},headers=alice['headers']).status_code==404
    assert client.delete('/api/resources/'+resource['id'],headers=alice['headers']).status_code==404
    assert client.get('/api/runs/'+run['id'],headers=alice['headers']).status_code==200
    with Session() as db:
        assert db.get(Schedule,schedule.json()['id']).enabled is False
        record=db.scalar(select(Audit).where(Audit.action=='resource.delete',Audit.target==resource['id']))
        assert record and record.detail['kind']==kind and record.detail['disabled_schedules']==1

def test_login_and_disable_revoke_token(client,people):
    admin,alice,_=people
    assert client.post('/api/auth/login',json={'username':'alice','password':'bad'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'alice','password':'test-password-123'}).status_code==200
    client.patch('/api/users/'+str(alice['user'].id),json={'active':False},headers=admin['headers'])
    assert client.get('/api/resources',headers=alice['headers']).status_code==401

def test_cannot_disable_self(client,people):
    admin=people[0]
    assert client.patch('/api/users/'+str(admin['user'].id),json={'active':False},headers=admin['headers']).status_code==400

def test_python_success_and_sdk_result(client,people):
    a=people[1];r=publish(client,a,create(client,a,'from ops_sdk import inputs,result\nprint("working",flush=True)\nresult({"n":inputs()["n"]+1})'))
    run=execute_one(start(client,a,r,{'n':3})['id']);assert run.status=='success',run.error
    assert run.result['value']=={'n':4}
    assert any('working' in e['text'] for e in read_logs(run.id,0)['events'])

def test_shell_and_interpreter(client,people):
    a=people[1];r=create(client,a,content={'language':'shell','code':'printf \'{"n":7}\\n\'','timeout':10,'interpreter':'/bin/sh'})
    run=execute_one(start(client,a,r,test=True)['id']);assert run.status=='success';assert run.result['value']=={'n':7}

def test_script_failure_and_traceback(client,people):
    a=people[1];r=create(client,a,'raise RuntimeError("boom")')
    run=execute_one(start(client,a,r,test=True)['id']);assert run.status=='failed'
    assert any('boom' in e['text'] for e in read_logs(run.id,0)['events'])

def test_timeout_and_cancel(client,people):
    a=people[1];r=create(client,a,content={'language':'python','code':'import time; time.sleep(30)','timeout':1})
    run=execute_one(start(client,a,r,test=True)['id']);assert run.status=='failed';assert '超时' in run.error
    next_run=start(client,a,r,test=True)
    client.post('/api/runs/'+next_run['id']+'/cancel',headers=a['headers'])
    run=execute_one(next_run['id']);assert run.status=='cancelled'

def test_secret_redaction(client,people):
    a=people[1];r=create(client,a,content={'language':'python','code':'from ops_sdk import inputs\nprint(inputs()["secret"])','parameters':[{'name':'secret','secret':True}],'timeout':10})
    run=execute_one(start(client,a,r,{'secret':'private-token-123'},test=True)['id'])
    assert 'private-token-123' not in json.dumps(read_logs(run.id,0))
    assert 'private-token-123' not in json.dumps(client.get('/api/runs/'+run.id,headers=a['headers']).json())

def test_published_revision_is_immutable(client,people):
    a=people[1];r=publish(client,a,create(client,a));r['content']['code']='print("changed")'
    assert client.put('/api/resources/'+r['id'],json=r,headers=a['headers']).status_code==200
    run=execute_one(start(client,a,r)['id']);assert run.result['value']=={'ok':True}

def test_parser_json_regex_csv_and_required():
    assert parse_output('{"a":"12"}',{'mode':'json','fields':[{'name':'n','path':'$.a','type':'integer','required':True}]})=={'n':12}
    assert parse_output('status=OK',{'mode':'regex','pattern':'status=(?P<state>\\w+)'})=={'state':'OK'}
    assert parse_output('a,b\n1,2',{'mode':'csv'})==[{'a':'1','b':'2'}]
    assert parse_output('a|b',{'mode':'split','delimiter':'|'})==['a','b']
    with pytest.raises(ValueError):parse_output('{}',{'mode':'json','fields':[{'name':'n','path':'$.n','required':True}]})

def test_dag_cycle_rejected():
    with pytest.raises(HTTPException):validate_content('app',{'nodes':[{'id':'a','kind':'parser'},{'id':'b','kind':'parser'}],'edges':[{'source':'a','target':'b'},{'source':'b','target':'a'}]})

def test_market_approval_copy_and_independence(client,people):
    admin,alice,bob=people;r=publish(client,alice,create(client,alice))
    entry=client.post('/api/market/submit/'+r['id'],headers=alice['headers']).json()
    assert client.post('/api/market/'+entry['id']+'/download',headers=bob['headers']).status_code==404
    assert client.post('/api/market/'+entry['id']+'/review',json={'status':'approved'},headers=alice['headers']).status_code==403
    assert client.post('/api/market/'+entry['id']+'/review',json={'status':'approved'},headers=admin['headers']).status_code==200
    copy=client.post('/api/market/'+entry['id']+'/download',headers=bob['headers']).json()
    assert copy['id']!=r['id'];assert copy['owner_id']==bob['user'].id
    assert execute_one(start(client,bob,copy)['id']).status=='success'
    assert client.get('/api/resources/'+r['id'],headers=bob['headers']).status_code==404

def test_market_transitive_dependencies_are_copied(client,people):
    admin,alice,bob=people;child=publish(client,alice,create(client,alice))
    parent=publish(client,alice,create(client,alice,content={'language':'python','code':'print("parent")','dependencies':[child['id']]}))
    mid=client.post('/api/market/submit/'+parent['id'],headers=alice['headers']).json()['id']
    client.post('/api/market/'+mid+'/review',json={'status':'approved'},headers=admin['headers'])
    clone=client.post('/api/market/'+mid+'/download',headers=bob['headers']).json()
    assert clone['content']['dependencies'][0]!=child['id']
    assert client.get('/api/resources/'+clone['content']['dependencies'][0],headers=bob['headers']).status_code==200

def test_schedules_and_metrics(client,people):
    a=people[1];r=publish(client,a,create(client,a))
    response=client.post('/api/schedules',headers=a['headers'],json={'resource_id':r['id'],'cron':'* * * * *'});assert response.status_code==200
    with Session() as db:
        s=db.get(Schedule,response.json()['id']);s.next_at=time.time()-30;db.commit();schedule_tick(db)
        assert db.get(Schedule,s.id).last_run
    assert client.get('/metrics').status_code==401
    from ops import config
    assert 'ops_runs_total' in client.get('/metric',headers={'Authorization':'Bearer '+config.key('.metrics-key')}).text

def test_websocket_authorization(client,people):
    a=people[1];r=create(client,a);run=execute_one(start(client,a,r,test=True)['id'])
    with client.websocket_connect('/ws/runs/'+run.id) as ws:
        ws.send_json({'token':people[2]['headers']['Authorization'][7:]})
        with pytest.raises(Exception):ws.receive_json()
    with client.websocket_connect('/ws/runs/'+run.id) as ws:
        ws.send_json({'token':a['headers']['Authorization'][7:]});assert ws.receive_json()['status']=='success'

def test_runtime_token_is_not_a_user_token(client,people):
    t=token(people[1]['user'],'runtime',run_id='unknown')
    assert client.get('/api/resources',headers={'Authorization':'Bearer '+t}).status_code==401

def test_ai_unconfigured_explicit_error(client,people):
    assert client.post('/api/ai/assist',json={'prompt':'create a script'},headers=people[1]['headers']).status_code==503

def test_ai_model_list_uses_selected_protocol_and_is_admin_only(client,people,monkeypatch):
    import httpx
    import ops.api as module
    captured=[]
    class FakeClient:
        def __init__(self,**kwargs): captured.append({'options':kwargs})
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,headers):
            captured[-1].update({'url':url,'headers':headers})
            return httpx.Response(200,json={'data':[{'id':'model-b'},{'id':'model-a'},{'id':'model-b'}]},request=httpx.Request('GET',url))
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    admin,alice,_=people
    body={'protocol':'anthropic','base_url':'https://model.internal/v1','api_version':'2023-06-01','api_key':'secret-key'}
    assert client.post('/api/settings/ai/models',json=body,headers=alice['headers']).status_code==403
    response=client.post('/api/settings/ai/models',json=body,headers=admin['headers'])
    assert response.status_code==200,response.text
    assert response.json()=={'models':['model-b','model-a']}
    assert captured[-1]['url']=='https://model.internal/v1/models'
    assert captured[-1]['headers']=={'x-api-key':'secret-key','anthropic-version':'2023-06-01'}
    response=client.post('/api/settings/ai/models',json={**body,'protocol':'openai'},headers=admin['headers'])
    assert response.status_code==200,response.text
    assert captured[-1]['headers']=={'Authorization':'Bearer secret-key'}

def test_ai_model_list_falls_back_from_anthropic_path_to_provider_root(client,people,monkeypatch):
    import httpx
    import ops.api as module
    captured=[]
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,headers):
            captured.append((url,headers))
            status=405 if url.endswith('/anthropic/v1/models') else 200
            data={'detail':'Method Not Allowed'} if status==405 else {'data':[{'id':'deepseek-flash'},{'id':'deepseek-v4-pro'}]}
            return httpx.Response(status,json=data,request=httpx.Request('GET',url))
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    response=client.post('/api/settings/ai/models',headers=people[0]['headers'],json={'protocol':'anthropic','base_url':'https://api.deepseek.com/anthropic','api_key':'deepseek-key'})
    assert response.status_code==200,response.text
    assert response.json()=={'models':['deepseek-flash','deepseek-v4-pro']}
    assert [item[0] for item in captured]==['https://api.deepseek.com/anthropic/v1/models','https://api.deepseek.com/models']
    assert captured[-1][1]=={'Authorization':'Bearer deepseek-key'}

def test_app_parser_condition_and_branch(client,people):
    a=people[1];body={'kind':'app','name':'branch','content':{'nodes':[{'id':'parse','kind':'parser','source':'$.inputs','parser':{'mode':'json'}},{'id':'cond','kind':'condition','source':'$.nodes.parse.ok','op':'eq','expected':True},{'id':'yes','kind':'parser','source':'$.inputs','parser':{'mode':'json'}},{'id':'no','kind':'parser','source':'$.inputs','parser':{'mode':'json'}}],'edges':[{'source':'parse','target':'cond'},{'source':'cond','target':'yes','when':'true'},{'source':'cond','target':'no','when':'false'}]}}
    response=client.post('/api/resources',json=body,headers=a['headers']);assert response.status_code==200
    run=execute_one(start(client,a,response.json(),{'ok':True},test=True)['id'])
    assert run.status=='success',run.error
    assert run.result['nodes']['yes']=='success';assert run.result['nodes']['no']=='skipped'

def test_ai_anthropic_catalog_respects_permissions(client,people,monkeypatch):
    import httpx
    from ops.db import Setting
    import ops.api as module
    admin,alice,bob=people
    own=publish(client,alice,create(client,alice,name='allowed-alice'))
    private=publish(client,bob,create(client,bob,name='private-bob'))
    with Session() as db:
        db.add(Setting(key='ai',value={'protocol':'anthropic','base_url':'https://model.internal/v1','model':'intranet-model'}));db.commit()
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,headers,json):
            captured.update({'url':url,'headers':headers,'payload':json})
            return httpx.Response(200,json={'content':[{'type':'text','text':'draft response'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    response=client.post('/api/ai/assist',json={'prompt':'create a script'},headers=alice['headers'])
    assert response.status_code==200,response.text
    assert captured['url']=='https://model.internal/v1/messages'
    text=captured['payload']['messages'][0]['content']
    assert own['id'] in text and private['id'] not in text
    assert response.json()['draft_only'] is True

def test_admin_ai_batch_initializes_tool_drafts_with_tags_and_provenance(client,people,monkeypatch,tmp_path):
    import httpx
    import ops.api as module
    from ops.db import Setting,Resource
    admin,alice,_=people
    source=tmp_path/'send_message.pl'
    source.write_text('#!/usr/bin/perl\nprint "sent\\n";\n')
    with Session() as db:
        db.add(Setting(key='ai',value={'protocol':'anthropic','base_url':'https://model.internal','model':'intranet-model'}));db.commit()
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,headers,json):
            draft={'resource':{'kind':'script','name':'标准消息发送','description':'发送消息并返回结构化结果','tags':['消息','通知'],'content':{'language':'python','code':'from ops_sdk import inputs, result\nresult({"sent": True})\n','parameters':[],'variables':{},'dependencies':[],'timeout':30}},'summary':'已转换','warnings':['请核对消息网关']}
            return httpx.Response(200,json={'content':[{'type':'text','text':json_module.dumps(draft,ensure_ascii=False)}]},request=httpx.Request('POST',url))
    json_module=json
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    assert client.post('/api/ai/initialize/scan',headers=alice['headers'],json={'paths':[str(source)]}).status_code==403
    scan=client.post('/api/ai/initialize/scan',headers=admin['headers'],json={'paths':[str(tmp_path)]})
    assert scan.status_code==200,scan.text
    assert scan.json()['items'][0]['language']=='perl'
    response=client.post('/api/ai/initialize',headers=admin['headers'],json={'paths':[str(source)],'type':'tool'})
    assert response.status_code==200,response.text
    data=response.json();assert len(data['created'])==1 and data['failed']==[] and data['draft_only'] is True
    draft=data['created'][0]
    assert draft['scope']=='platform' and draft['version']==0
    assert {'AI初始化','基础工具','消息','通知'}.issubset(set(draft['tags']))
    assert draft['source']['source_path']==str(source.resolve())
    assert draft['source']['source_language']=='perl' and draft['source']['review_status']=='draft'
    with Session() as db: assert db.get(Resource,draft['id']).content['language']=='python'
    duplicate=client.post('/api/ai/initialize',headers=admin['headers'],json={'paths':[str(source)],'type':'tool'}).json()
    assert duplicate['created']==[] and duplicate['failed'][0]['resource_id']==draft['id']

def test_monitor_initialization_requires_real_published_platform_tool_and_full_dag(client,people,monkeypatch,tmp_path):
    import httpx
    import ops.api as module
    from ops.db import Setting
    admin=people[0]
    tool=publish(client,admin,create(client,admin,name='DB查询',scope='platform'))
    source=tmp_path/'check_db.py';source.write_text('rows = query_db()\nif not rows: alert()\n')
    with Session() as db:
        db.add(Setting(key='ai',value={'protocol':'anthropic','base_url':'https://model.internal','model':'intranet-model'}));db.commit()
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,headers,json):
            context=json_module.loads(json['messages'][0]['content'])
            assert any(item['id']==tool['id'] for item in context['available_platform_tools'])
            content={'nodes':[{'id':'query','kind':'script','script_id':tool['id'],'inputs':{}},{'id':'parse','kind':'parser','source':'$.nodes.query','parser':{'mode':'json'}},{'id':'check','kind':'condition','source':'$.nodes.parse.ok','op':'eq','expected':True}], 'edges':[{'source':'query','target':'parse'},{'source':'parse','target':'check'}], 'parameters':[], 'variables':{}, 'timeout':300}
            draft={'resource':{'kind':'app','name':'数据库监控','description':'查询、解析并判断数据库状态','tags':['数据库','可用性'],'content':content},'summary':'已编排','warnings':[]}
            return httpx.Response(200,json={'content':[{'type':'text','text':json_module.dumps(draft,ensure_ascii=False)}]},request=httpx.Request('POST',url))
    json_module=json
    monkeypatch.setattr(module.httpx,'AsyncClient',FakeClient)
    response=client.post('/api/ai/initialize',headers=admin['headers'],json={'paths':[str(source)],'type':'monitor'})
    assert response.status_code==200,response.text
    app=response.json()['created'][0]
    assert app['kind']=='app' and {'AI初始化','监控编排','数据库'}.issubset(set(app['tags']))
    assert {node['kind'] for node in app['content']['nodes']}=={'script','parser','condition'}
    assert app['content']['nodes'][0]['script_id']==tool['id']

def test_resource_search_supports_name_and_exact_tags(client,people):
    admin,alice,_=people
    first=create(client,alice,name='库存监控',tags=['监控','CMDB'])
    create(client,alice,name='消息工具',tags=['通知'])
    by_name=client.get('/api/resources?q=库存',headers=alice['headers']).json()
    by_tag=client.get('/api/resources?tag=CMDB',headers=alice['headers']).json()
    assert [item['id'] for item in by_name]==[first['id']]
    assert [item['id'] for item in by_tag]==[first['id']]

def test_branch_merge_runs_for_selected_branch(client,people):
    a=people[1]
    nodes=[{'id':'c','kind':'condition','source':'$.inputs.ok','op':'eq','expected':True}]+[{'id':i,'kind':'parser','source':'$.inputs','parser':{'mode':'json'}} for i in ['yes','no','merge']]
    edges=[{'source':'c','target':'yes','when':'true'},{'source':'c','target':'no','when':'false'},{'source':'yes','target':'merge'},{'source':'no','target':'merge'}]
    r=client.post('/api/resources',json={'kind':'app','name':'merge','content':{'nodes':nodes,'edges':edges}},headers=a['headers']).json()
    run=execute_one(start(client,a,r,{'ok':True},test=True)['id'])
    assert run.status=='success';assert run.result['nodes']['merge']=='success';assert run.result['nodes']['no']=='skipped'


def test_run_list_excludes_descendants_before_limit_and_keeps_drilldown(client,people):
    admin,alice,bob=people
    script=publish(client,alice,create(client,alice))
    app=client.post('/api/resources',headers=alice['headers'],json={'kind':'app','name':'parent','content':{'nodes':[{'id':'script','kind':'script','script_id':script['id']}],'edges':[]}}).json()
    root=start(client,alice,app,test=True)
    with Session() as db:
        parent=db.get(Run,root['id'])
        for rid,parent_id in [('child',parent.id),('grandchild','child')]:
            db.add(Run(id=rid,owner_id=parent.owner_id,resource_id=script['id'],name=rid,kind='script',parent_id=parent_id,node_id='script' if rid=='child' else None,snapshot=parent.snapshot['dependencies'][script['id']],inputs={'n':7},created_at='2099-01-01T00:00:00+00:00'))
        db.commit()
    for person in [admin,alice]:
        listed=client.get('/api/runs?limit=1',headers=person['headers']).json()
        assert [r['id'] for r in listed]==[root['id']]
        detail=client.get('/api/runs/'+root['id'],headers=person['headers']).json()
        assert [r['id'] for r in detail['children']]==['child']
        assert detail['nodes'][0]['run_id']=='child'
        child=client.get('/api/runs/child',headers=person['headers']).json()
        assert child['inputs']=={'n':7}
        assert child['children'][0]['id']=='grandchild'
    assert client.get('/api/runs',headers=bob['headers']).json()==[]
    assert client.get('/api/runs/child',headers=bob['headers']).status_code==404


def test_all_app_nodes_have_execution_details_and_preserve_false_output(client,people):
    a=people[1]
    nodes=[{'id':'parse','label':'解析输入','kind':'parser','source':'$.inputs','parser':{'mode':'json'}},
           {'id':'cond','kind':'condition','source':'$.nodes.parse.ok','op':'eq','expected':True},
           {'id':'skip','kind':'parser','source':'$.inputs'},
           {'id':'fail','kind':'parser','source':'$.inputs.raw','parser':{'mode':'json'}},
           {'id':'blocked','kind':'parser','source':'$.inputs'}]
    edges=[{'source':'parse','target':'cond'},{'source':'cond','target':'skip','when':'true'},{'source':'fail','target':'blocked'}]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'details','content':{'nodes':nodes,'edges':edges,'parameters':[{'name':'secret','secret':True}]}}).json()
    rid=start(client,a,app,{'ok':False,'raw':'invalid json','secret':'app-private-token'},test=True)['id']
    before=client.get('/api/runs/'+rid,headers=a['headers']).json()
    assert len(before['nodes'])==5
    assert all(n['status']=='pending' for n in before['nodes'])
    assert all('output' not in n for n in before['nodes'])
    run=execute_one(rid)
    assert run.status=='failed'
    response=client.get('/api/runs/'+rid,headers=a['headers'])
    assert 'app-private-token' not in response.text
    assert 'app-private-token' not in json.dumps(read_logs(rid,0))
    details={n['id']:n for n in response.json()['nodes']}
    assert details['parse']['inputs']['value']['ok'] is False
    assert details['parse']['output']['secret']=='[REDACTED]'
    assert details['cond']['output'] is False
    assert details['cond']['inputs']=={'value':False,'op':'eq','expected':True}
    assert details['fail']['inputs']['value']=='invalid json'
    assert details['fail']['error'] and any(e['stream']=='stderr' for e in details['fail']['logs'])
    for nid in ['skip','blocked']:
        assert details[nid]['status']=='skipped'
        assert 'output' not in details[nid]
        assert details[nid]['logs']
    assert all(n['recorded'] for n in details.values())


def test_app_script_validation_failure_has_node_details(client,people):
    a=people[1]
    script=publish(client,a,create(client,a,content={'language':'python','code':'print(1)','parameters':[{'name':'required_value','required':True},{'name':'secret','secret':True}]}))
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'validation','content':{'nodes':[{'id':'script','kind':'script','script_id':script['id'],'inputs':{'secret':'hidden-token'}}],'edges':[]}}).json()
    run=execute_one(start(client,a,app,test=True)['id'])
    assert run.status=='failed'
    response=client.get('/api/runs/'+run.id,headers=a['headers'])
    assert 'hidden-token' not in response.text
    node=response.json()['nodes'][0]
    assert node['status']=='failed' and node['run_id'] is None
    assert node['inputs']=={'secret':'[REDACTED]'}
    assert 'required_value' in node['error']
    assert any(e['stream']=='stderr' for e in node['logs'])


def test_historical_app_nodes_remain_accessible_without_invented_details(client,people):
    a=people[1]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'old','content':{'nodes':[{'id':'parse','kind':'parser'},{'id':'waiting','kind':'parser'}],'edges':[]}}).json()
    rid=start(client,a,app,test=True)['id']
    with Session() as db:
        run=db.get(Run,rid);run.status='interrupted';run.result={'nodes':{'parse':'success','waiting':'pending'},'outputs':{'parse':0}};db.commit()
    nodes=client.get('/api/runs/'+rid,headers=a['headers']).json()['nodes']
    assert nodes[0]['output']==0 and nodes[0]['recorded'] is False
    assert 'inputs' not in nodes[0] and 'logs' not in nodes[0]
    assert nodes[1]['status']=='interrupted'
    assert 'output' not in nodes[1]


def test_app_script_child_records_real_inputs_output_and_logs(client,people):
    a=people[1]
    script=publish(client,a,create(client,a,code='from ops_sdk import inputs,result\nprint("child log marker",flush=True)\nresult({"n":inputs()["n"]+1})',content={'language':'python','code':'from ops_sdk import inputs,result\nprint("child log marker",flush=True)\nresult({"n":inputs()["n"]+1})','parameters':[{'name':'n','type':'integer','default':6}],'timeout':10}))
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'workflow','content':{'nodes':[{'id':'script','kind':'script','script_id':script['id']},{'id':'parse','kind':'parser','source':'$.nodes.script','parser':{'mode':'json'}}],'edges':[{'source':'script','target':'parse'}]}}).json()
    rid=start(client,a,app,test=True)['id']
    async def workflow():
        with Session() as db:
            root=db.get(Run,rid);root.status='running';db.commit()
        task=asyncio.create_task(execute(root))
        try:
            for _ in range(100):
                with Session() as db:
                    child=db.scalar(select(Run).where(Run.parent_id==rid,Run.status=='queued'))
                    if child: child.status='running';db.commit()
                if child:
                    await execute(child)
                    break
                await asyncio.sleep(.02)
            else: pytest.fail('workflow child was not created')
            await asyncio.wait_for(task,5)
            return child.id
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
    child_id=asyncio.run(workflow())
    parent=client.get('/api/runs/'+rid,headers=a['headers']).json()
    assert parent['status']=='success'
    assert parent['nodes'][0]['status']=='success'
    assert parent['nodes'][0]['run_id']==child_id
    child=client.get('/api/runs/'+child_id,headers=a['headers']).json()
    assert child['inputs']=={'n':6}
    assert child['result']['value']=={'n':7}
    assert any('child log marker' in e['text'] for e in client.get('/api/runs/'+child_id+'/logs',headers=a['headers']).json()['events'])
    assert parent['nodes'][1]['inputs']=={'value':{'n':7}}
    assert parent['nodes'][1]['output']=={'n':7}


def test_empty_workflow_draft_saves_but_cannot_publish_or_run(client,people):
    a=people[1]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'editable draft','content':{'nodes':[{'id':'only','kind':'parser'}],'edges':[]}}).json()
    app['content']={'nodes':[],'edges':[]}
    response=client.put('/api/resources/'+app['id'],headers=a['headers'],json=app)
    assert response.status_code==200,response.text
    assert client.get('/api/resources/'+app['id'],headers=a['headers']).json()['content']['nodes']==[]
    assert client.post('/api/resources/'+app['id']+'/publish',headers=a['headers']).status_code==400
    assert client.post('/api/resources/'+app['id']+'/run',headers=a['headers'],json={'test':True}).status_code==400


def test_final_result_uses_terminal_node_not_node_order_and_redacts_secrets(client,people):
    a=people[1]
    nodes=[{'id':'last','kind':'parser','source':'$.nodes.first','parser':{'mode':'json'}},
           {'id':'first','kind':'parser','source':'$.inputs','parser':{'mode':'json'}}]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'final output','content':{'nodes':nodes,'edges':[{'source':'first','target':'last'}],'parameters':[{'name':'secret','secret':True}]}}).json()
    rid=start(client,a,app,{'answer':42,'secret':'final-secret'},test=True)['id']
    assert client.get('/api/runs/'+rid,headers=a['headers']).json()['result']['final_result'] is None
    run=execute_one(rid)
    assert run.status=='success'
    assert run.result['final_result']=={'answer':42,'secret':'[REDACTED]'}
    assert set(run.result['outputs'])=={'first','last'}
    assert run.result['value']==run.result['outputs']
    response=client.get('/api/runs/'+rid,headers=a['headers'])
    assert response.json()['result']['final_result']==run.result['final_result']
    assert 'final-secret' not in response.text


def test_final_result_combines_parallel_endpoints_and_omits_skipped_branches(client,people):
    a=people[1]
    nodes=[{'id':'condition','kind':'condition','source':'$.inputs.ok','expected':True},
           {'id':'yes','kind':'parser','source':'$.inputs.left'},
           {'id':'no','kind':'parser','source':'$.inputs.right'},
           {'id':'parallel','kind':'parser','source':'$.inputs.right'}]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'parallel output','content':{'nodes':nodes,'edges':[{'source':'condition','target':'yes','when':'true'},{'source':'condition','target':'no','when':'false'}]}}).json()
    run=execute_one(start(client,a,app,{'ok':True,'left':{'n':1},'right':{'n':2}},test=True)['id'])
    assert run.status=='success'
    assert run.result['final_result']=={'yes':{'n':1},'parallel':{'n':2}}
    assert run.result['nodes']['no']=='skipped'


@pytest.mark.parametrize('value',[False,0,'',[],{},None])
def test_final_result_preserves_falsy_endpoint_values(value):
    from ops.run_results import application_final_result
    assert application_final_result({'nodes':[{'id':'end'}],'edges':[]},{'end':'success'},{'end':value},'success')==value


@pytest.mark.parametrize('status',['running','failed','cancelled','interrupted'])
def test_incomplete_or_failed_run_has_no_final_result(status):
    from ops.run_results import application_final_result
    assert application_final_result({'nodes':[{'id':'end'}],'edges':[]},{'end':'success'},{'end':{'partial':True}},status) is None


def test_final_result_backfills_historical_run_without_rewriting_it(client,people):
    a=people[1]
    app=client.post('/api/resources',headers=a['headers'],json={'kind':'app','name':'historical output','content':{'nodes':[{'id':'final_result','kind':'parser'}],'edges':[]}}).json()
    rid=start(client,a,app,test=True)['id']
    historical={'nodes':{'final_result':'success'},'outputs':{'final_result':False},'value':{'final_result':False}}
    with Session() as db:
        run=db.get(Run,rid);run.status='success';run.result=historical;db.commit()
    result=client.get('/api/runs/'+rid,headers=a['headers']).json()['result']
    assert result['final_result'] is False
    assert result['outputs']=={'final_result':False}
    with Session() as db: assert db.get(Run,rid).result==historical


def test_last_inputs_without_history_or_resource_access(client,people):
    a=people[1];r=create(client,a)
    response=client.get('/api/resources/'+r['id']+'/last-inputs',headers=a['headers'])
    assert response.status_code==200
    assert response.json()=={'run_id':None,'inputs':{},'omitted_secrets':[]}
    assert client.get('/api/resources/'+r['id']+'/last-inputs',headers=people[2]['headers']).status_code==404
    assert client.get('/api/resources/'+r['id']+'/last-inputs').status_code==401


def test_last_inputs_are_latest_per_resource_and_user_not_limited_by_run_list(client,people):
    admin,alice,bob=people
    resource=publish(client,admin,create(client,admin,scope='platform'))
    first=start(client,alice,resource,{'number':1})
    second=start(client,alice,resource,{'number':0,'enabled':False,'nested':{'items':[1,2]}})
    with Session() as db:
        db.get(Run,first['id']).created_at='2026-01-01T00:00:00+00:00'
        latest=db.get(Run,second['id']);latest.created_at='2026-01-02T00:00:00+00:00';latest.status='failed'
        for i in range(105):
            db.add(Run(id='unrelated-'+str(i),owner_id=alice['user'].id,resource_id='another-resource',name='another',kind='script',snapshot=latest.snapshot,inputs={'wrong':True},created_at='2026-02-01T00:00:00+00:00'))
        db.add(Run(id='derived',owner_id=alice['user'].id,resource_id=resource['id'],parent_id=first['id'],name='child',kind='script',snapshot=latest.snapshot,inputs={'child':True},created_at='2026-03-01T00:00:00+00:00'))
        db.commit()
    start(client,bob,resource,{'another_user':True})
    response=client.get('/api/resources/'+resource['id']+'/last-inputs',headers=alice['headers']).json()
    assert response['run_id']==second['id']
    assert response['inputs']=={'number':0,'enabled':False,'nested':{'items':[1,2]}}
    # Administrators also default only to their own inputs, not the latest user's inputs.
    assert client.get('/api/resources/'+resource['id']+'/last-inputs',headers=admin['headers']).json()['run_id'] is None


def test_last_inputs_includes_previous_test_run_and_empty_parameters(client,people):
    a=people[1];r=create(client,a)
    old=start(client,a,r,{'old':True},test=True)
    with Session() as db:
        db.get(Run,old['id']).created_at='2020-01-01T00:00:00+00:00';db.commit()
    latest=start(client,a,r,{},test=True)
    response=client.get('/api/resources/'+r['id']+'/last-inputs',headers=a['headers']).json()
    assert response['run_id']==latest['id'] and response['inputs']=={}


def test_last_inputs_omits_secrets_from_both_snapshot_and_current_definition(client,people):
    a=people[1]
    r=create(client,a,content={'language':'python','code':'print(1)','parameters':[{'name':'old_secret','secret':True}]})
    run=start(client,a,r,{'old_secret':'old-password','new_secret':'new-password','visible':'hello'},test=True)
    r['content']['parameters']=[{'name':'new_secret','secret':True}]
    assert client.put('/api/resources/'+r['id'],headers=a['headers'],json=r).status_code==200
    response=client.get('/api/resources/'+r['id']+'/last-inputs',headers=a['headers'])
    assert response.json()=={'run_id':run['id'],'inputs':{'visible':'hello'},'omitted_secrets':['new_secret','old_secret']}
    assert 'old-password' not in response.text and 'new-password' not in response.text
