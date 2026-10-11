#!/bin/sh
# alerts-tls entrypoint: run Caddy, then publish only its local CA's root certificate (world-readable) on the
# tls-trust volume for the mock carrier's SSL_CERT_FILE. The CA key stays in caddy-data, readable by root only.
# Caddy's start-up lines are JSON with epoch-second timestamps ("ts":1791321062.27), which the privacy grep of
# deploy/compose/logs reads as phone-number-shaped; they are dropped here (docker adds its own times if asked).
set -eu
fifo=/tmp/caddy.log
rm -f "$fifo" && mkfifo "$fifo"
awk '{ gsub(/"ts":[0-9.]+,?/, ""); print; fflush() }' < "$fifo" &
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile > "$fifo" 2>&1 &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null' TERM INT
root=/data/caddy/pki/authorities/local/root.crt
while [ ! -s "$root" ]; do
  kill -0 "$pid" 2>/dev/null || exit 1
  sleep 0.2
done
cp "$root" /trust/root.crt.tmp && chmod 0644 /trust/root.crt.tmp && mv /trust/root.crt.tmp /trust/root.crt
chmod 0755 /trust
wait "$pid"
