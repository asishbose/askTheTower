#!/usr/bin/env bash
# `make showcase-infra`, part 1: the reproducibility claim on a clean machine.
#
# A fresh Docker daemon (docker:dind, privileged) with an empty image cache, a copy of this checkout (tracked
# files plus untracked-but-not-ignored ones — never .env, .venv or artifacts), and nothing else installed but
# make and bash: no uv, no Python on the "host". Inside it: `time (make up && make demo)`. The demo then runs in
# the ref-client container (mk/demo.mk falls back to it when uv is absent). Output → artifacts/clean-run.txt
# (BuildKit step lines and digests filtered out: digests are digit runs the privacy grep reads as numbers);
# the unfiltered log goes to $FULL_LOG (default: a temp file, path printed at the end).
#
#   scripts/clean_machine.sh            # needs Docker with --privileged; ~5 min incl. image pulls and builds
#   KEEP=1 scripts/clean_machine.sh     # leave the dind container running for a look around
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/artifacts/clean-run.txt}"
DIND_IMAGE="${DIND_IMAGE:-docker:29-dind}"
NAME="att-clean-$$"
FULL_LOG="${FULL_LOG:-$(mktemp -t att-clean-run.XXXXXX.log)}"
# keep: our own lines, compose's container states, seed/demo output; drop: BuildKit progress and digests
filter() { tee -a "$FULL_LOG" | grep --line-buffered -Ev '^#[0-9]+ |sha256:|^ *(=>|\[\+\])|^$' ; }
mkdir -p "$(dirname "$OUT")"

cleanup() { [ "${KEEP:-0}" = "1" ] || docker rm -f -v "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

{
  echo "# clean-machine run — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# host: $(uname -srm); docker $(docker version --format '{{.Server.Version}}' 2>/dev/null); dind: $DIND_IMAGE"
  echo "# commit: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo none)$(git -C "$ROOT" diff --quiet 2>/dev/null || echo ' + uncommitted changes')"
} | tee "$OUT"

docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= "$DIND_IMAGE" >/dev/null
for _ in $(seq 1 60); do docker exec "$NAME" docker info >/dev/null 2>&1 && break; sleep 1; done
docker exec "$NAME" docker info >/dev/null

# the checkout, as a fresh clone would have it (plus work not yet committed, so the run tests this tree)
(cd "$ROOT" && git ls-files -z --cached --others --exclude-standard | tar --null -T - -cf -) \
  | docker exec -i "$NAME" sh -c 'mkdir -p /src && tar -xf - -C /src'
docker exec "$NAME" apk add --no-cache make bash coreutils >/dev/null

set +e
docker exec -w /src "$NAME" bash -c '
  set -o pipefail
  echo "# inside: $(uname -m); uv: $(command -v uv || echo absent); images cached: $(docker images -q | wc -l)"
  TIMEFORMAT="TIMING make up && make demo: %R s wall"
  time (make up && make demo)
  rc=$?
  echo "# exit: $rc"
  make down >/dev/null 2>&1
  echo "# after make down: containers=$(docker ps -aq | wc -l) volumes=$(docker volume ls -q | wc -l) networks=$(docker network ls -q --filter type=custom | wc -l)"
  exit $rc
' 2>&1 | filter | tee -a "$OUT"
rc=${PIPESTATUS[0]}
set -e
echo "clean-machine run: exit $rc → $OUT (unfiltered: $FULL_LOG)"
exit "$rc"
