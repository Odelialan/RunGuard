#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if ! command -v uv >/dev/null 2>&1; then
  echo "RunGuard requires uv: https://docs.astral.sh/uv/"
  exit 1
fi

uv sync --extra dev
npm --prefix apps/web install

cleanup() {
  if [[ -n "${api_pid:-}" ]]; then
    kill "$api_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

uv run uvicorn runguard_api.main:app --reload --port 8000 &
api_pid=$!
npm --prefix apps/web run dev
