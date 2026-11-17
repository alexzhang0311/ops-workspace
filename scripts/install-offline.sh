#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
umask 077
BASE_PYTHON=${OPS_BASE_PYTHON:-/data/app/anaconda3/bin/python3}
"$BASE_PYTHON" scripts/verify_bundle.py
"$BASE_PYTHON" -c 'import sys,platform; assert sys.version_info[:2]==(3,12), "Need Python 3.12"; assert platform.machine()=="x86_64" and sys.platform=="linux", "Need Linux x86_64"'
[ -f .env ] && { echo '已有 .env，请按升级文档操作，安装器不会覆盖生产配置。'; exit 1; }
"$BASE_PYTHON" -m venv .venv
.venv/bin/python -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements-offline.lock
.venv/bin/python scripts/build_postgres.py
.venv/bin/python scripts/init_postgres.py
sh server.sh db-init
printf '%s\n' '安装完成。下一步：sh server.sh init-admin，再执行 sh server.sh start。'
