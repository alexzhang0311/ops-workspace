import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from .resources import validate_content


SCRIPT_SUFFIXES = {'.py', '.pl', '.pm', '.sh', '.bash', '.ksh'}
IGNORED_DIRECTORIES = {'.git', '.svn', '__pycache__', 'node_modules', 'vendor', 'venv', '.venv'}
MAX_SOURCE_BYTES = 512 * 1024
MAX_SCAN_FILES = 200
MAX_INITIALIZE_FILES = 20


def _allowed_roots():
    value = os.getenv('OPS_SCRIPT_IMPORT_ROOTS', '').strip()
    return [Path(part).expanduser().resolve() for part in value.split(os.pathsep) if part.strip()]


def allowed_root_labels():
    roots = _allowed_roots()
    return [str(root) for root in roots] if roots else ['app 账号可读取的绝对路径']


def _resolve_path(value):
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise ValueError('请使用绝对路径')
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError('路径不存在或不可读取')
    roots = _allowed_roots()
    if roots and not any(path == root or path.is_relative_to(root) for root in roots):
        raise ValueError('路径不在管理员配置的导入目录中')
    return path


def _language(path, first_line=''):
    suffix = path.suffix.lower()
    if suffix == '.py' or 'python' in first_line:
        return 'python'
    if suffix in ('.pl', '.pm') or 'perl' in first_line:
        return 'perl'
    if suffix in ('.sh', '.bash', '.ksh') or re.search(r'\b(?:ba|k|z)?sh\b', first_line):
        return 'shell'
    return 'unknown'


def _metadata(path):
    try:
        stat = path.stat()
    except OSError:
        raise ValueError('无法读取文件信息')
    if not path.is_file():
        raise ValueError('不是普通文件')
    if stat.st_size <= 0:
        raise ValueError('脚本内容为空')
    if stat.st_size > MAX_SOURCE_BYTES:
        raise ValueError(f'单个脚本不能超过 {MAX_SOURCE_BYTES // 1024} KiB')
    try:
        with path.open('rb') as stream:
            head = stream.read(256)
    except OSError:
        raise ValueError('脚本不可读取')
    if b'\0' in head:
        raise ValueError('文件包含二进制内容')
    first_line = head.decode('utf-8', errors='ignore').splitlines()[0] if head else ''
    return {
        'path': str(path),
        'name': path.name,
        'size': stat.st_size,
        'language': _language(path, first_line),
        'modified_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def discover_scripts(values, limit=MAX_SCAN_FILES):
    if not isinstance(values, list) or not values:
        raise ValueError('请至少填写一个脚本或目录路径')
    if len(values) > 30:
        raise ValueError('一次最多扫描 30 个路径')
    found, warnings, seen = [], [], set()

    def add_file(path):
        try:
            resolved = _resolve_path(path)
        except ValueError as exc:
            warnings.append({'path': str(path), 'reason': str(exc)})
            return
        if str(resolved) in seen:
            return
        seen.add(str(resolved))
        try:
            found.append(_metadata(resolved))
        except ValueError as exc:
            warnings.append({'path': str(resolved), 'reason': str(exc)})

    for value in values:
        try:
            root = _resolve_path(value)
        except ValueError as exc:
            warnings.append({'path': str(value), 'reason': str(exc)})
            continue
        if root.is_file():
            add_file(root)
        elif root.is_dir():
            for base, directories, files in os.walk(root, followlinks=False):
                directories[:] = sorted(d for d in directories if d not in IGNORED_DIRECTORIES)
                for filename in sorted(files):
                    path = Path(base) / filename
                    if path.suffix.lower() in SCRIPT_SUFFIXES:
                        add_file(path)
                    if len(found) >= limit:
                        warnings.append({'path': str(root), 'reason': f'只展示前 {limit} 个可导入脚本'})
                        return found, warnings
    return found, warnings


def read_source(path_value):
    path = _resolve_path(path_value)
    meta = _metadata(path)
    try:
        raw = path.read_bytes()
    except OSError:
        raise ValueError('脚本不可读取')
    if b'\0' in raw:
        raise ValueError('文件包含二进制内容')
    text = raw.decode('utf-8-sig', errors='replace')
    meta.update({'content': text, 'sha256': hashlib.sha256(raw).hexdigest(), 'lines': len(text.splitlines())})
    return meta


def extract_json(text):
    if not isinstance(text, str):
        raise ValueError('AI 未返回文本')
    blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)```', text, re.I)
    candidates = blocks + [text]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except (json.JSONDecodeError, TypeError):
            pass
        for index, character in enumerate(candidate):
            if character != '{':
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
    raise ValueError('AI 返回内容中没有可用的 JSON 草稿')


def _tags(values, initialization_type):
    if not isinstance(values, list) or not any(str(value).strip() for value in values):
        raise ValueError('AI 未生成场景标签')
    result = ['AI初始化', '基础工具' if initialization_type == 'tool' else '监控编排']
    for value in values:
        tag = str(value).strip()[:30]
        if tag and tag not in result:
            result.append(tag)
        if len(result) >= 12:
            break
    return result


def normalize_draft(payload, initialization_type, allowed_script_ids):
    draft = payload.get('resource', payload)
    if not isinstance(draft, dict):
        raise ValueError('AI 草稿结构错误')
    expected_kind = 'script' if initialization_type == 'tool' else 'app'
    if draft.get('kind') != expected_kind:
        raise ValueError(f'AI 应生成 {expected_kind} 类型资源')
    name = str(draft.get('name') or '').strip()
    if not name:
        raise ValueError('AI 草稿缺少名称')
    content = draft.get('content')
    if not isinstance(content, dict):
        raise ValueError('AI 草稿缺少 content')
    content.setdefault('parameters', [])
    content.setdefault('variables', {})
    content.setdefault('timeout', 300)
    if expected_kind == 'script':
        content.setdefault('dependencies', [])
        if content.get('language') not in ('python', 'shell'):
            raise ValueError('标准化工具必须转换为 Python 或 Shell')
        dependencies = content.get('dependencies', [])
        unknown = [item for item in dependencies if item not in allowed_script_ids]
        if unknown:
            raise ValueError('AI 引用了不存在或未发布的平台工具: ' + ', '.join(map(str, unknown[:3])))
    else:
        nodes = content.get('nodes', [])
        kinds = {node.get('kind') for node in nodes if isinstance(node, dict)}
        missing = {'script', 'parser', 'condition'} - kinds
        if missing:
            labels = {'script': '基础工具', 'parser': '数据解析', 'condition': '逻辑分支'}
            raise ValueError('监控编排缺少节点: ' + '、'.join(labels[item] for item in sorted(missing)))
        references = [node.get('script_id') for node in nodes if isinstance(node, dict) and node.get('kind') == 'script']
        unknown = [item for item in references if item not in allowed_script_ids]
        if unknown:
            raise ValueError('AI 引用了不存在或未发布的平台工具: ' + ', '.join(map(str, unknown[:3])))
    try:
        validate_content(expected_kind, content)
    except HTTPException as exc:
        raise ValueError(str(exc.detail))
    return {
        'kind': expected_kind,
        'name': name[:160],
        'description': str(draft.get('description') or '').strip()[:4000],
        'tags': _tags(draft.get('tags'), initialization_type),
        'content': content,
        'warnings': payload.get('warnings', []),
        'summary': str(payload.get('summary') or '').strip(),
    }
