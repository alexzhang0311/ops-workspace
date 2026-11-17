"""Zero-dependency Python and Shell bridge, injected into script environments."""
import json, os, sys, urllib.request

def inputs(): return json.loads(os.environ.get('OPS_INPUT_JSON','{}'))
def result(value):
    with open(os.environ['OPS_RESULT_FILE'],'w',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False)

def run_script(script_id, parameters=None):
    body=json.dumps({'script_id':script_id,'inputs':parameters or {}}).encode()
    request=urllib.request.Request(os.environ['OPS_API_URL']+'/api/internal/call',data=body,headers={'Authorization':'Bearer '+os.environ['OPS_RUN_TOKEN'],'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=86400) as response: payload=json.load(response)
    if payload['status']!='success': raise RuntimeError(f"子脚本 {payload['id']} 失败: {payload['error']}")
    return payload['result'].get('value')

if __name__=='__main__':
    if sys.argv[1]=='call': print(json.dumps(run_script(sys.argv[2],json.loads(sys.argv[3]) if len(sys.argv)>3 else {}),ensure_ascii=False))
    elif sys.argv[1]=='input': print(json.dumps(inputs(),ensure_ascii=False))
    elif sys.argv[1]=='result': result(json.loads(sys.argv[2]))
