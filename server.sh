#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
umask 077
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PYTHON=${OPS_PYTHON:-"$PWD/.venv/bin/python"}
if [ "$#" -eq 0 ]; then set -- status; fi
case "$1" in
  backup) "$PYTHON" -m ops.backup ;;
  build) cd frontend; npm ci; npm run build ;;
  package) shift; "$PYTHON" scripts/package.py "$@" ;;
  deploy) shift; sh scripts/install-offline.sh "$@" ;;
  *) "$PYTHON" -m ops.manage "$@" ;;
esac
