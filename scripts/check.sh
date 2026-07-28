#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

uv sync --all-extras
uv run ruff check apps/api tests services scripts/kind-live-evaluation.py scripts/external-model-evaluation.py
uv run python -m compileall -q apps/api scripts/kind-live-evaluation.py scripts/external-model-evaluation.py
uv run pytest -q
npm --prefix apps/web install
npm --prefix apps/web run lint
npm --prefix apps/web run build

uv run python - <<'PY'
from fastapi.testclient import TestClient
from runguard_api.main import app

client = TestClient(app)
assert client.get("/api/health").status_code == 200
assert client.get("/api/ready").status_code == 200
assert client.get("/").status_code == 200
assert client.get("/metrics").status_code == 200
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
assert client.get("/.well-known/agent-card.json").json()["protocolVersion"] == "1.0"
print("API smoke checks passed")
PY

uv run python - <<'PY'
from pathlib import Path

import yaml

files = [
    Path("docker-compose.yml"),
    Path("deploy/kind/cluster.yaml"),
    Path("deploy/kind/platform.yaml"),
    Path("deploy/kind/values.yaml"),
    Path("deploy/observability/prometheus.yml"),
    Path("deploy/observability/loki.yml"),
    Path("deploy/observability/tempo.yml"),
    Path("deploy/observability/otel-collector.yml"),
    Path("deploy/observability/grafana-datasources.yml"),
    Path("deploy/policies/verify-runguard-images.yaml"),
]
for path in files:
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    assert documents and all(document is not None for document in documents), path
print("Deployment YAML checks passed")
PY

bash -n scripts/*.sh

if awk '/^FROM / && $0 !~ /@sha256:[0-9a-f]{64}/ {print FILENAME ":" FNR ":" $0; failed=1} END {exit failed}' \
  deploy/docker/*.Dockerfile; then
  :
else
  echo "Every Dockerfile base stage must be pinned by SHA-256 digest"
  exit 1
fi
if rg -n 'uses:\s+[^ ]+@v[0-9]' .github/workflows; then
  echo "GitHub Actions must be pinned to immutable commit SHAs"
  exit 1
fi
if rg -n -P \
  '^\s*image:\s+(?!.*@sha256:[0-9a-f]{64}$)(?:pgvector|redis|openpolicyagent|prom|grafana|otel)/.+' \
  docker-compose.yml deploy/kind/platform.yaml; then
  echo "Third-party runtime images must be pinned by SHA-256 digest"
  exit 1
fi

if command -v helm >/dev/null 2>&1; then
  helm_args=(
    --set-string image.digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    --set-string runnerImage.digest=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    --set-string reviewerImage.digest=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
    --set reviewer.enabled=true
    --set-string secrets.databaseUrl=postgresql://user:pass@db/runguard
    --set-string secrets.openaiApiKey=validation-only
    --set-string secrets.a2aReviewerToken=validation-reviewer
    --set-string secrets.diagnosticsToken=validation-diagnostics
    --set-string secrets.prometheusWebhookSecret=validation-webhook
    --set-string secrets.langgraphAesKey=validation-key-16
    --set-string auth.oidcIssuer=https://issuer.example
    --set-string auth.oidcAudience=runguard
    --set-string auth.oidcJwksUrl=https://issuer.example/.well-known/jwks.json
    --set-string config.targetInventory.order-api.environment=production
    --set-string config.targetInventory.order-api.namespace=runguard-system
    --set-string config.targetInventory.order-api.name=order-api
    --set-string config.targetInventory.order-api.canary_name=order-api-canary
    --set-string config.targetInventory.order-api.http_route_name=order-api
    --set-string config.targetInventory.order-api.stable_service=order-api
    --set-string config.targetInventory.order-api.canary_service=order-api-canary
    --set-string config.executionStrategy=canary
  )
  helm lint deploy/helm/runguard "${helm_args[@]}"
  rendered_chart="$(mktemp)"
  helm template runguard deploy/helm/runguard \
    --namespace runguard-system \
    "${helm_args[@]}" >"$rendered_chart"
  grep -q \
    'RUNGUARD_A2A_REVIEWER_URL: "http://runguard-runguard-reviewer.runguard-system.svc/a2a/reviewer"' \
    "$rendered_chart"
  if grep -q 'namespaceSelector: {}' "$rendered_chart"; then
    echo "Rendered NetworkPolicy contains an unrestricted namespace selector"
    exit 1
  fi
  if grep -A2 'egress:' "$rendered_chart" | grep -qE 'ports:\s*$'; then
    echo "Review rendered egress policy: an unrestricted destination may be present"
    exit 1
  fi
  grep -q 'kubernetes.io/metadata.name: "egress-system"' "$rendered_chart"
  grep -q 'cidr: "10.96.0.1/32"' "$rendered_chart"
  grep -A12 'resources:.*httproutes' "$rendered_chart" | grep -q -- '- "order-api"'
  rm -f "$rendered_chart"
fi

if command -v docker >/dev/null 2>&1; then
  docker compose config --quiet
fi

if command -v opa >/dev/null 2>&1; then
  opa test policies
fi

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
