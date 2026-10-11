#!/usr/bin/env bash
# Create deploy/compose/.env from .env.example on first use: every `__generate__` becomes 32 random bytes,
# base64 (letters, digits, + / =). Idempotent: an existing .env is kept, and keys it lacks are appended.
# The repo-root .env (the one settings file; see /.env.example) wins: a key compose uses that has a non-empty value
# there replaces the generated or stored value here, on every `make up`. Keys compose doesn't use (AWS
# credentials, TF_VAR_*) are never copied here. Portable to bash 3.2 (macOS): no associative arrays.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
example="$here/.env.example" env_file="$here/.env"
root_env="${ATT_ROOT_ENV:-$here/../../.env}"
rand() { head -c 32 /dev/urandom | base64 | tr -d '\n'; }
# Last non-empty value of KEY in the root .env ('' when absent, empty, or no root .env). Keys are [A-Za-z0-9_].
root_value() { [ -f "$root_env" ] && sed -n "s/^$1=//p" "$root_env" | tr -d '\r' | sed '/^$/d' | tail -n 1 || true; }
touch "$env_file"
added=0 replaced=0
tmp="$(mktemp "$here/.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
# 1) existing lines, with root overrides applied
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) printf '%s\n' "$line" >> "$tmp"; continue ;; esac
  key="${line%%=*}" value="${line#*=}"
  override="$(root_value "$key")"
  if [ -n "$override" ] && [ "$override" != "$value" ]; then
    printf '%s=%s\n' "$key" "$override" >> "$tmp"
    replaced=$((replaced + 1))
  else
    printf '%s\n' "$line" >> "$tmp"
  fi
done < "$env_file"
# 2) keys the example has and .env lacks: root value, else generated, else the example's default
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}" value="${line#*=}"
  grep -q "^${key}=" "$tmp" && continue
  override="$(root_value "$key")"
  if [ -n "$override" ]; then value="$override"
  elif [ "$value" = "__generate__" ]; then value="$(rand)"; fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  added=$((added + 1))
done < "$example"
chmod 600 "$tmp" 2>/dev/null || true
mv "$tmp" "$env_file"
trap - EXIT
[ "$added" -gt 0 ] && echo "deploy/compose/.env: $added setting(s) written (random local secrets; gitignored)" || true
[ "$replaced" -gt 0 ] && echo "deploy/compose/.env: $replaced setting(s) taken from the root .env" || true
