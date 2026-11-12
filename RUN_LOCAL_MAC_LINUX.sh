#!/bin/sh
cd "$(dirname "$0")" || exit 1
: "${STAYOS_ADMIN_PASSWORD:=local-demo-only-change-me}"
export STAYOS_ADMIN_PASSWORD
python3 -m pip install -r requirements.txt
exec python3 server.py
