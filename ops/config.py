import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv('OPS_DATA_DIR', str(ROOT / 'var'))).resolve()
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
DB_URL = os.getenv('OPS_DATABASE_URL', 'sqlite:///' + str(DATA / 'ops.db'))
HOST = os.getenv('OPS_HOST', '127.0.0.1')
PORT = int(os.getenv('OPS_PORT', '8765'))
BASE_URL = os.getenv('OPS_BASE_URL', f'http://127.0.0.1:{PORT}')
MAX_CONCURRENCY = int(os.getenv('OPS_MAX_CONCURRENCY', '32'))
PER_USER_CONCURRENCY = int(os.getenv('OPS_PER_USER_CONCURRENCY', '16'))
MAX_DEPTH = 4
LOG_LIMIT = int(os.getenv('OPS_LOG_LIMIT_BYTES', '10485760'))
RESULT_LIMIT = 1048576

def key(name):
    path = DATA / name
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    import secrets
    value = secrets.token_urlsafe(48)
    with os.fdopen(fd, 'w') as f:
        f.write(value)
    return value

JWT_KEY = os.getenv('OPS_JWT_SECRET') or key('.jwt-key')
