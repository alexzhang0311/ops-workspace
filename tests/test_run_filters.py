import pytest
from ops.db import Session,Run


@pytest.fixture
def filter_runs(people):
    alice,bob=people[1],people[2]
    rows=[
        ('script-ok','script','数据库 DB_100% 巡检','success','manual','2026-11-17T00:00:00+00:00',None,alice),
        ('app-ok','app','数据库巡检','success','test','2026-11-17T00:30:00.123456+00:00',None,alice),
        ('script-failed','script','DB backup','failed','schedule','2026-11-17T01:00:00+00:00',None,alice),
        ('queued','app','等待任务','queued','manual',None,None,alice),
        ('child','script','数据库 DB_100% 巡检','success','workflow','2026-11-17T00:15:00+00:00','app-ok',alice),
        ('bob','script','数据库 DB_100% 巡检','success','manual','2026-11-17T00:20:00+00:00',None,bob),
    ]
    with Session() as db:
        for i,(rid,kind,name,status,trigger,started,parent,person) in enumerate(rows):
            db.add(Run(id=rid,owner_id=person['user'].id,resource_id='resource-'+rid,kind=kind,name=name,status=status,trigger=trigger,started_at=started,parent_id=parent,created_at=f'2026-11-17T02:00:0{i}+00:00',snapshot={'content':{'parameters':[],'nodes':[]}}))
        db.commit()
    return alice


@pytest.mark.parametrize('filters,expected',[
    ({'kind':'script'},{'script-ok','script-failed'}),
    ({'kind':'app'},{'app-ok','queued'}),
    ({'name':'数据库'},{'script-ok','app-ok'}),
    ({'name':' db '},{'script-ok','script-failed'}),
    ({'name':'_100%'},{'script-ok'}),
    ({'status':'failed'},{'script-failed'}),
    ({'trigger':'test'},{'app-ok'}),
    ({'trigger':'schedule'},{'script-failed'}),
    ({'kind':'script','status':'success','trigger':'manual','name':'巡检'},{'script-ok'}),
    ({'name':'not found'},set()),
])
def test_run_filters_combine_and_preserve_scope(client,filter_runs,filters,expected):
    response=client.get('/api/runs',params=filters,headers=filter_runs['headers'])
    assert response.status_code==200,response.text
    assert {r['id'] for r in response.json()}==expected


def test_started_time_filters_use_actual_start_utc_boundaries_and_exclude_queued(client,filter_runs):
    response=client.get('/api/runs',params={'started_from':'2026-11-17T08:00:00+08:00','started_to':'2026-11-17T09:00:00+08:00'},headers=filter_runs['headers'])
    assert response.status_code==200,response.text
    assert {r['id'] for r in response.json()}=={'script-ok','app-ok'}
    # Naive timestamps have the documented Beijing timezone; fractional bounds are supported.
    response=client.get('/api/runs',params={'started_from':'2026-11-17T08:30:00.123456','started_to':'2026-11-17T08:30:00.123457'},headers=filter_runs['headers'])
    assert [r['id'] for r in response.json()]==['app-ok']
    assert {r['id'] for r in client.get('/api/runs',params={'started_from':'2026-11-17T09:00:00+08:00'},headers=filter_runs['headers']).json()}=={'script-failed'}


def test_filters_run_before_limit_and_offset(client,filter_runs):
    with Session() as db:
        for i in range(105):
            db.add(Run(id=f'noise-{i}',owner_id=filter_runs['user'].id,resource_id='noise',name='noise',kind='script',created_at='2026-11-18T00:00:00+00:00',snapshot={'content':{}}))
        db.commit()
    first=client.get('/api/runs',params={'name':'数据库','limit':1,'offset':0},headers=filter_runs['headers']).json()
    second=client.get('/api/runs',params={'name':'数据库','limit':1,'offset':1},headers=filter_runs['headers']).json()
    assert [r['id'] for r in first]==['app-ok']
    assert [r['id'] for r in second]==['script-ok']
    assert client.get('/api/runs',params={'name':'数据库','limit':1,'offset':2},headers=filter_runs['headers']).json()==[]


def test_admin_filters_keep_descendants_hidden(client,filter_runs,people):
    rows=client.get('/api/runs',params={'status':'success','name':'DB_100%'},headers=people[0]['headers']).json()
    assert {r['id'] for r in rows}=={'script-ok','bob'}
    assert client.get('/api/runs',params={'kind':'script'}).status_code==401


@pytest.mark.parametrize('params,status',[
    ({'kind':'invalid'},422),({'status':'invalid'},422),({'trigger':'invalid'},422),
    ({'started_from':'not-a-date'},422),
    ({'started_from':'2026-11-17T09:00','started_to':'2026-11-17T08:00'},400),
])
def test_invalid_run_filters_are_rejected(client,people,params,status):
    assert client.get('/api/runs',params=params,headers=people[1]['headers']).status_code==status
