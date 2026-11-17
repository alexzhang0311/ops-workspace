import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent.parent
manifest=root/'manifest.json'
if not manifest.exists(): raise SystemExit('缺少离线包校验清单 manifest.json')
for relative,expected in json.loads(manifest.read_text())['sha256'].items():
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise SystemExit('文件缺失或路径错误: '+relative)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected: raise SystemExit('文件校验失败: '+relative)
print('离线包文件校验通过')
