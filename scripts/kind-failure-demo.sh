#!/usr/bin/env bash
set -euo pipefail

api_url="${RUNGUARD_KIND_URL:-http://127.0.0.1:8088}"
namespace="runguard-system"

for command_name in kubectl curl python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

reset_fault() {
  kubectl -n "$namespace" exec deployment/fault-sentinel -- \
    python -c 'import urllib.request; request=urllib.request.Request("http://127.0.0.1:8090/faults", headers={"X-Fault-Token":"kind-demo-token"}, method="DELETE"); print(urllib.request.urlopen(request).read().decode())' \
    >/dev/null 2>&1 || true
}
trap reset_fault EXIT

kubectl -n "$namespace" exec deployment/fault-sentinel -- \
  python -c 'import json,urllib.request; request=urllib.request.Request("http://127.0.0.1:8090/faults", data=json.dumps({"latency_ms":0,"error_rate":0,"unhealthy":True}).encode(), headers={"Content-Type":"application/json","X-Fault-Token":"kind-demo-token"}, method="POST"); print(urllib.request.urlopen(request).read().decode())'

incident_json="$(
  curl -fsS -X POST "$api_url/api/incidents" \
    -H 'Content-Type: application/json' \
    -d '{
      "title": "kind order-api injected health failure",
      "severity": "P2",
      "service": "order-api",
      "environment": "runguard-system",
      "description": "Fault injector reports unhealthy to exercise compensation."
    }'
)"
incident_id="$(
  INCIDENT_JSON="$incident_json" python3 -c \
    'import json,os; print(json.loads(os.environ["INCIDENT_JSON"])["id"])'
)"
echo "Created $incident_id"

result_json="$(curl -fsS -X POST "$api_url/api/incidents/$incident_id/start")"
status="$(
  RESULT_JSON="$result_json" python3 -c \
    'import json,os; print(json.loads(os.environ["RESULT_JSON"])["status"])'
)"

if [[ "$status" != "ROLLED_BACK" ]]; then
  echo "Expected ROLLED_BACK, received $status" >&2
  exit 1
fi

echo "$incident_id reached ROLLED_BACK after failed verification."
echo "Postmortem: $api_url/api/incidents/$incident_id/postmortem"
