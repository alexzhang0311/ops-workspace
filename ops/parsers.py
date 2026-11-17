import csv, io, json
import regex
from jsonpath_ng import parse as jsonpath

class ParseError(ValueError): pass

def get_path(data, path, default=None):
    if not path or path=='$': return data
    if len(path)>256: raise ParseError('字段路径过长')
    matches = jsonpath(path).find(data)
    if not matches: return default
    return matches[0].value if len(matches)==1 else [m.value for m in matches]

def convert(value, kind):
    if kind=='string': return str(value)
    if kind=='integer': return int(value)
    if kind=='number': return float(value)
    if kind=='boolean':
        if value in (True, 'true', '1', 1): return True
        if value in (False, 'false', '0', 0): return False
        raise ParseError('无法转换为布尔值')
    if kind=='json' and isinstance(value,str): return json.loads(value)
    return value

def parse_output(raw, spec):
    if len(str(raw))>1048576: raise ParseError('解析输入超过 1 MiB')
    mode=spec.get('mode','json')
    if mode=='json': data=json.loads(raw) if isinstance(raw,str) else raw
    elif mode=='regex':
        match=regex.search(spec.get('pattern',''), str(raw), timeout=0.2)
        if not match: raise ParseError('正则表达式未匹配')
        data=match.groupdict() or list(match.groups())
    elif mode=='csv':
        delimiter=spec.get('delimiter',',')
        if len(delimiter)!=1: raise ParseError('分隔符必须为单个字符')
        data=list(csv.DictReader(io.StringIO(str(raw)), delimiter=delimiter))
    elif mode=='split': data=str(raw).split(spec.get('delimiter','\n'))
    else: raise ParseError('不支持的解析类型')
    fields=spec.get('fields',[])
    if not fields: return data
    result={}
    for field in fields:
        value=get_path(data,field.get('path','$'),field.get('default'))
        if value is None and field.get('required'): raise ParseError('缺少必填字段: '+field['name'])
        result[field['name']]=convert(value,field.get('type')) if value is not None else None
    return result

def condition(value, op, expected):
    if op=='eq': return value==expected
    if op=='ne': return value!=expected
    if op=='exists': return value is not None
    if op=='contains': return expected in value
    if op=='gt': return float(value)>float(expected)
    if op=='lt': return float(value)<float(expected)
    raise ParseError('不支持的条件操作')
