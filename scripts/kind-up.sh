#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

for command_name in docker kind kubectl helm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

if ! kind get clusters | grep -qx runguard; then
  kind create cluster --config deploy/kind/cluster.yaml
fi

docker build -f deploy/docker/api.Dockerfile -t runguard:kind .
docker build -f deploy/docker/fault-injector.Dockerfile -t runguard-fault-injector:kind .
kind load docker-image --name runguard runguard:kind runguard-fault-injector:kind

kubectl create namespace runguard-system --dry-run=client -o yaml | kubectl apply -f -
kubectl -n runguard-system create configmap runguard-policy \
  --from-file=runguard.rego=policies/runguard.rego \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f deploy/kind/platform.yaml
kubectl -n runguard-system rollout status deployment/postgres --timeout=180s
kubectl -n runguard-system rollout status deployment/redis --timeout=180s
kubectl -n runguard-system rollout status deployment/opa --timeout=180s

helm upgrade --install runguard deploy/helm/runguard \
  --namespace runguard-system \
  --values deploy/kind/values.yaml \
  --wait \
  --timeout 5m

kubectl -n runguard-system patch service runguard-runguard \
  --type merge \
  -p '{"spec":{"ports":[{"name":"http","port":80,"targetPort":"http","nodePort":30080}]}}'

echo "RunGuard kind environment: http://127.0.0.1:8088"
echo "Fault injection example:"
echo "kubectl -n runguard-system port-forward service/order-api 8090:80"
