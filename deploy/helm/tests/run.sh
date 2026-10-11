#!/usr/bin/env bash
# helm-unittest for every chart (tests live here, outside the charts, so they are never packaged):
#   deploy/helm/tests/run.sh            needs: helm plugin install https://github.com/helm-unittest/helm-unittest
# The umbrella's dependencies must be built first (helm dependency update deploy/helm/umbrella --skip-refresh) and
# its seed files synced (uv run python scripts/k8s_seed.py --sync-chart); `make helm-lint` does both.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
helm="$(cd "$here/.." && pwd)"
rc=0
for dir in "$here"/*/; do
  chart="$(basename "$dir")"
  args=()
  for f in "$dir"*_test.yaml; do args+=(-f "$f"); done
  extra=()
  [ "$chart" = umbrella ] && extra=(--with-subchart=false)
  helm unittest "${extra[@]}" "${args[@]}" "$helm/$chart" || rc=1
done
exit $rc
