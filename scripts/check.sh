#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

uv sync --extra dev
uv run ruff check apps/api
uv run python -m compileall -q apps/api
npm --prefix apps/web install
npm --prefix apps/web run lint
npm --prefix apps/web run build

uv run python - <<'PY'
from fastapi.testclient import TestClient
from runguard_api.main import app

client = TestClient(app)
assert client.get("/api/health").status_code == 200
assert client.get("/api/overview").json()["incidents"] >= 4
assert len(client.get("/api/incidents").json()) >= 4
assert client.post(
    "/api/policies/simulate",
    json={
        "environment": "production",
        "tool": "kubernetes.patch_deployment",
        "resource": "order-api",
        "risk_level": "R2",
        "has_rollback": True,
        "incident_severity": "P1"
    },
).json()["decision"] == "require_approval"
assert client.post(
    "/api/policies/simulate",
    json={
        "environment": "production",
        "tool": "shell.execute",
        "resource": "cluster",
        "risk_level": "R3",
        "has_rollback": False,
        "incident_severity": "P1"
    },
).json()["decision"] == "deny"
print("API smoke checks passed")
PY

tracked_large_files="$(
  git ls-files -z |
    xargs -0 -r du -b |
    awk '$1 > 10485760 {print $2}'
)"
if [[ -n "$tracked_large_files" ]]; then
  echo "Tracked files larger than 10 MiB:"
  echo "$tracked_large_files"
  exit 1
fi

if git ls-files -z | xargs -0 -r grep -IlE \
  '(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY)' \
  | grep -v '^scripts/check.sh$'; then
  echo "Potential credential found in tracked files"
  exit 1
fi

echo "RunGuard checks passed"
