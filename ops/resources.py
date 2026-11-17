import copy, json, uuid, os
from fastapi import HTTPException
from sqlalchemy import select
from .db import Resource, Revision, User, Run, now
from .auth import can_read, can_write
from .parsers import convert

def uid(): return str(uuid.uuid4())
def resource(db, user, rid, write=False):
    r=db.get(Resource,rid)
    if not (can_write(user,r) if write else can_read(user,r)):
        raise HTTPException(404, '资源不存在或无权访问')
    return r

def serialize(obj, exclude=()):
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name not in exclude}

def validate_content(kind, content, allow_empty=False):
    if not isinstance(content,dict): raise HTTPException(400,'资源内容必须为对象')
    if len(json.dumps(content))>1024*1024: raise HTTPException(400,'内容超过 1 MiB')
    if kind=='script':
        if content.get('language') not in ('python','shell'): raise HTTPException(400,'脚本语言需为 python 或 shell')
        if not isinstance(content.get('code'),str): raise HTTPException(400,'缺少脚本代码')
        if len(content.get('dependencies',[]))>30: raise HTTPException(400,'脚本依赖过多')
        interpreter=content.get('interpreter','')
        if interpreter and not os.path.isabs(interpreter): raise HTTPException(400,'解释器必须使用绝对路径')
    elif kind=='app':
        nodes=content.get('nodes',[]); edges=content.get('edges',[])
        if not isinstance(nodes,list) or not isinstance(edges,list): raise HTTPException(400,'节点和连线必须为数组')
        if len(nodes)>100 or (not nodes and not allow_empty): raise HTTPException(400,'应用须包含 1–100 个节点')
        import re
        for n in nodes:
            if not isinstance(n,dict) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}',str(n.get('id',''))): raise HTTPException(400,'节点 ID 须为字母开头的字母、数字或下划线')
            if n.get('kind')=='script' and not n.get('script_id'): raise HTTPException(400,'脚本节点缺少脚本 ID')
        ids=[n['id'] for n in nodes]
        if len(ids)!=len(set(ids)): raise HTTPException(400,'节点 ID 重复')
        incoming={i:0 for i in ids}; children={i:[] for i in ids}
        for e in edges:
            if not isinstance(e,dict) or 'source' not in e or 'target' not in e: raise HTTPException(400,'连线缺少 source 或 target')
            if e['source'] not in ids or e['target'] not in ids: raise HTTPException(400,'连线指向不存在的节点')
            incoming[e['target']]+=1; children[e['source']].append(e['target'])
        queue=[i for i,d in incoming.items() if d==0]; seen=[]
        while queue:
            n=queue.pop(); seen.append(n)
            for c in children[n]:
                incoming[c]-=1
                if incoming[c]==0: queue.append(c)
        if len(seen)!=len(ids): raise HTTPException(400,'应用不能包含循环')
        for n in nodes:
            if n.get('kind') not in ('script','parser','condition'): raise HTTPException(400,'不支持的节点类型')
    else: raise HTTPException(400,'未知资源类型')
    if not isinstance(content.get('parameters',[]),list) or not isinstance(content.get('variables',{}),dict): raise HTTPException(400,'参数须为数组，变量须为对象')
    names=set()
    for p in content.get('parameters',[]):
        if not isinstance(p,dict): raise HTTPException(400,'参数定义须为对象')
        name=p.get('name','')
        if not name or name in names: raise HTTPException(400,'参数名称为空或重复')
        names.add(name)
    timeout=content.get('timeout',300)
    if not isinstance(timeout,int) or not 1<=timeout<=86400: raise HTTPException(400,'超时需为 1–86400 秒')

def get_revision(db, r, version=None):
    version=version or r.version
    rev=db.scalar(select(Revision).where(Revision.resource_id==r.id, Revision.version==version))
    if not rev: raise HTTPException(400, f'{r.name} 尚未发布或版本不存在')
    return copy.deepcopy(rev.snapshot)

def make_snapshot(db,user,r,trail=None):
    trail=(trail or [])+[r.id]
    if len(trail)>5 or len(trail)!=len(set(trail)): raise HTTPException(400,'脚本依赖存在循环或超过 4 层')
    validate_content(r.kind,r.content)
    data={'id':r.id,'name':r.name,'kind':r.kind,'version':r.version+1,'scope':r.scope,'tags':r.tags,'description':r.description,'content':copy.deepcopy(r.content),'dependencies':{}}
    refs=r.content.get('dependencies',[]) if r.kind=='script' else [n['script_id'] for n in r.content['nodes'] if n['kind']=='script']
    for rid in refs:
        dep=resource(db,user,rid)
        if dep.kind!='script': raise HTTPException(400,'仅允许依赖脚本')
        if rid in trail: raise HTTPException(400,'脚本循环依赖')
        snap=get_revision(db,dep)
        def check(s,anc):
            if s['id'] in anc or len(anc)>5: raise HTTPException(400,'脚本循环依赖或层级过深')
            for c in s.get('dependencies',{}).values(): check(c,anc+[s['id']])
        check(snap,trail)
        data['dependencies'][rid]=snap
    return data

def check_snapshot_access(db,user,snapshot):
    for dep in snapshot.get('dependencies',{}).values():
        resource(db,user,dep['id'])
        check_snapshot_access(db,user,dep)

def validate_inputs(snapshot, inputs):
    if not isinstance(inputs,dict): raise HTTPException(400,'运行参数必须为 JSON 对象')
    if len(json.dumps(inputs))>65536: raise HTTPException(400,'运行参数超过 64 KiB')
    values=copy.deepcopy(inputs)
    for p in snapshot['content'].get('parameters',[]):
        name=p['name']
        if name not in values and 'default' in p: values[name]=p['default']
        if name not in values:
            if p.get('required'): raise HTTPException(400,'缺少参数: '+name)
            continue
        try: values[name]=convert(values[name],p.get('type'))
        except (ValueError,TypeError): raise HTTPException(400,'参数类型错误: '+name)
    return values

def enqueue(db,user,r,inputs=None,test=False,parent_id=None,depth=0,node_id=None,snapshot=None,trigger='manual'):
    snap=snapshot or (make_snapshot(db,user,r) if test else get_revision(db,r))
    check_snapshot_access(db,user,snap)
    values=validate_inputs(snap,inputs or {})
    run=Run(id=uid(),owner_id=user.id,resource_id=r.id,name=snap['name'],kind=snap['kind'],snapshot=snap,inputs=values,parent_id=parent_id,depth=depth,node_id=node_id,trigger='test' if test else trigger)
    db.add(run); db.flush()
    return run
