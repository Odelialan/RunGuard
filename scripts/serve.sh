#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

port="${RUNGUARD_PORT:-8000}"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"

uv sync --frozen
npm --prefix apps/web ci
npm --prefix apps/web run build

echo "RunGuard local: http://127.0.0.1:$port"
if [[ -n "$lan_ip" ]]; then
  echo "RunGuard LAN:   http://$lan_ip:$port"
fi

exec uv run uvicorn runguard_api.main:app --host 0.0.0.0 --port "$port"
