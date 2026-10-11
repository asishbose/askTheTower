#!/usr/bin/env bash
# `make helm-kind` — the no-cloud check of the Helm charts (prompt 14 step 2; 10 §6):
#   kind cluster → build + `kind load` the six images (five services + demo-ui) (and caddy, dynamodb-local) → helm install the umbrella with
#   values-kind.yaml (the seed runs as a post-install hook) → port-forward → `ref-client demo` on the host →
#   transcripts compared with the golden files → artifacts/helm-kind.txt.
#
# Env: KIND_CLUSTER (att) · NAMESPACE (ask-the-tower) · RELEASE (att) · IMAGE_TAG (kind) · SKIP_BUILD=1 (reuse
#      ask-the-tower/*:$IMAGE_TAG) · KIND_DELETE=1 (delete the cluster at the end) · PF_BASE (18000: local ports
#      PF_BASE+80 Tower, +443 mock, +81 binding page, +82 alerts — clear of a running compose stack).
# Needs docker, kind, kubectl, helm, uv. kind's default CNI does not enforce NetworkPolicy (rendered, not tested).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/tools:$PATH"
CLUSTER=${KIND_CLUSTER:-att}
NS=${NAMESPACE:-ask-the-tower}
RELEASE=${RELEASE:-att}
TAG=${IMAGE_TAG:-kind}
PF_BASE=${PF_BASE:-18000}
SERVICES=(mock-carrier tower-mcp binding-page alerts ref-client demo-ui)  # demo-ui: kind only, never pushed (doc 11)
EXTRA_IMAGES=(caddy:2.10-alpine amazon/dynamodb-local:2.5.2)
OUT=artifacts/transcripts-kind
REPORT=artifacts/helm-kind.txt
KCTX="kind-$CLUSTER"
k() { kubectl --context "$KCTX" -n "$NS" "$@"; }
t0=$(date +%s)
step() { printf '\n== %s (%ss)\n' "$*" "$(( $(date +%s) - t0 ))"; }

for tool in docker kind kubectl helm uv; do
  command -v "$tool" >/dev/null || { echo "helm-kind: $tool not found on PATH" >&2; exit 2; }
done

step "kind cluster $CLUSTER"
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  step "build images (tag $TAG)"
  for s in "${SERVICES[@]}"; do
    docker build -q -f "services/$s/Dockerfile" -t "ask-the-tower/$s:$TAG" . >/dev/null
    echo "  built ask-the-tower/$s:$TAG"
  done
fi
step "kind load"
for i in "${EXTRA_IMAGES[@]}"; do docker image inspect "$i" >/dev/null 2>&1 || docker pull -q "$i" >/dev/null; done
for s in "${SERVICES[@]}"; do kind load docker-image --name "$CLUSTER" "ask-the-tower/$s:$TAG" >/dev/null; done
kind load docker-image --name "$CLUSTER" "${EXTRA_IMAGES[@]}" >/dev/null

step "helm upgrade --install $RELEASE (values-kind.yaml)"
uv run python scripts/k8s_seed.py --sync-chart >/dev/null
helm dependency update deploy/helm/umbrella --skip-refresh >/dev/null
helm --kube-context "$KCTX" upgrade --install "$RELEASE" deploy/helm/umbrella -n "$NS" --create-namespace \
  -f deploy/helm/umbrella/values-kind.yaml --set global.imageTag="$TAG" --wait --timeout 15m
# an upgrade replaces pods: wait until the old ones are gone, or a port-forward may bind to a terminating pod
for _ in $(seq 1 90); do k get pods --no-headers 2>/dev/null | grep -q Terminating || break; sleep 2; done
k get pods -o wide

step "seed log"
k logs "job/$RELEASE-seed" 2>/dev/null || true

step "port-forward"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT
for pair in "tower-mcp $((PF_BASE + 80)):8000" "mock-carrier $((PF_BASE + 443)):8443" \
            "binding-page $((PF_BASE + 81)):8081" "alerts $((PF_BASE + 82)):8082"; do
  set -- $pair
  k port-forward "svc/$1" "$2" >/dev/null 2>&1 &
  pids+=($!)
done
export TOWER_URL="http://127.0.0.1:$((PF_BASE + 80))/mcp" MOCK_URL="http://127.0.0.1:$((PF_BASE + 443))"
export BINDING_URL="http://127.0.0.1:$((PF_BASE + 81))" ALERTS_URL="http://127.0.0.1:$((PF_BASE + 82))"
TOWER_BEARER=$(k get secret att-secrets -o jsonpath='{.data.TOWER_BEARER}' | base64 -d)
export TOWER_BEARER
for u in "${TOWER_URL%/mcp}/healthz" "$MOCK_URL/healthz" "$BINDING_URL/healthz" "$ALERTS_URL/healthz"; do
  for _ in $(seq 1 60); do curl -fsS -o /dev/null "$u" 2>/dev/null && break; sleep 1; done
  curl -fsS -o /dev/null "$u" || { echo "helm-kind: $u not answering through port-forward" >&2; exit 1; }
done

step "ref-client demo (scripted agent unless AWS credentials) → $OUT"
mkdir -p "$OUT"
set +e
REF_AGENT=${REF_AGENT:-auto} uv run ref-client demo --env local --out "$OUT" 2>&1 | tee artifacts/helm-kind-demo.log
demo_rc=${PIPESTATUS[0]}
set -e

step "compare with golden"
set +e
uv run python - "$OUT" > artifacts/helm-kind-demo.cmp <<'PY'
import json, sys
from pathlib import Path
from ref_client.transcript import compare
out = Path(sys.argv[1]); golden = Path("tests/e2e/golden")
diffs = []
for g in sorted(golden.glob("*.json")):
    t = out / g.name
    if not t.exists():
        diffs.append(f"{g.stem}: no transcript"); continue
    diffs += [f"{g.stem}: {d}" for d in compare(json.loads(g.read_text()), json.loads(t.read_text()))]
print("transcripts match golden: " + ("yes (4/4)" if not diffs else "NO\n  " + "\n  ".join(diffs)))
sys.exit(1 if diffs else 0)
PY
cmp_rc=$?
cat artifacts/helm-kind-demo.cmp
set -e

step "privacy grep over pod logs"
for d in alerts tower-mcp binding-page mock-carrier dynamodb-local; do k logs "deploy/$d" --all-containers; done \
  > artifacts/helm-kind-pods.log 2>&1 || true
k logs "job/$RELEASE-seed" >> artifacts/helm-kind-pods.log 2>&1 || true
privacy=$(uv run python -c "from tests.privacy.patterns import phone_hits; import sys; print(len(phone_hits(open(sys.argv[1]).read())))" artifacts/helm-kind-pods.log)
echo "phone-number-shaped strings in pod logs: $privacy"

elapsed=$(( $(date +%s) - t0 ))
{
  echo "# make helm-kind — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "cluster: kind $CLUSTER ($(kubectl --context "$KCTX" version -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null || echo '?')); release $RELEASE in $NS; images ask-the-tower/*:$TAG"
  echo "elapsed: ${elapsed}s; demo exit $demo_rc; golden compare exit $cmp_rc; privacy grep hits $privacy"
  grep '^transcripts match golden' artifacts/helm-kind-demo.cmp 2>/dev/null || true
  k get pods --no-headers 2>/dev/null | awk '{print "pod: "$1" "$2" "$3}'
  tail -n 3 artifacts/helm-kind-demo.log | sed 's/^/demo: /'
} > "$REPORT"
cat "$REPORT"
if [ "${KIND_DELETE:-0}" = "1" ]; then kind delete cluster --name "$CLUSTER"; fi
[ "$demo_rc" = 0 ] && [ "$cmp_rc" = 0 ] && [ "$privacy" = 0 ]
