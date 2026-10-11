# tower-consent

The consent store and line-binding library that Tower (`services/tower-mcp`), Alerts (`services/alerts`) and the
binding page (`services/binding-page`) share. Design: [`docs/architecture/components/04-consent-and-binding.md`](../../docs/architecture/components/04-consent-and-binding.md).

What it does:

- **Tables as data** (`tables.py`): the six DynamoDB tables (`Users`, `Lines`, `Grants`, `Watches`, `Audit`,
  `BindTokens`), their keys, GSIs, TTL and PITR flags. Terraform (prompt 13) and DynamoDB Local both read this file.
- **Crypto** (`crypto.py`): `line_id = HMAC-SHA256(E.164, key)` (`LineIdHasher`) and `msisdn_enc`
  (`MsisdnCipher`). Local implementations use keys from the environment; KMS implementations use GenerateMac and
  a GenerateDataKey envelope. Neither a `line_id` nor a ciphertext can look like a phone number, and nothing logs.
- **`resolve(store, user_id, line)`**: the one read on the hot path. `"self"` → owner; an alias → the grant kind;
  revoked → `grant="none"`; unknown → `grant="none"` with no line (`NO_CONSENT`, D18); `"self"` with no line → `bound=False`. Exactly one DynamoDB request, no cache.
- **Writes**: `bind_line` (idempotent per line; refuses a number owned by someone else), single-use 10-minute bind
  tokens, `grant` / `revoke` (only `watch` and `reachability`; alias collisions rejected; never by voice;
  revoking `watch` disables the grantee's Watch on that line — `disable_watch`, kept not deleted),
  Watches with a `last_state` write that is conditional on its `at` timestamp, and the line-holder's watch settings
  (`set_watch_settings`, 04 §9: profile + up to three contacts who hold an active `watch` grant; `requires_ack`
  derived; every refusal writes nothing; revoking a contact's `watch` grant drops them from the chain).

What it doesn't do: talk to a carrier, decide policy (that's `tower-policy`), cache, or store a phone number
anywhere but inside `msisdn_enc` / `alert_phone_enc` ciphertext.

## Use it

```python
from datetime import UTC, datetime
from tower_consent import Store, crypto_from_env, bind_line, grant, resolve

store = Store.from_env()  # TOWER_DYNAMODB_ENDPOINT=http://localhost:8000 for DynamoDB Local
store.ensure_tables()  # local/compose only; Terraform creates them on AWS
hasher, cipher = crypto_from_env()
now = datetime.now(UTC)  # the library never reads a clock; callers pass `now`

mom = bind_line(store, hasher, cipher, "mom", "<E.164 the carrier verified>", "auth_code", now=now)
grant(store, mom.line_id, "asish", "watch", "mom", granted_by="mom", now=now)
resolve(store, "asish", "mom").view  # ConsentView(bound=True, grant='watch', revoked_at=None, line_id=...)
```

`python -m tower_consent.tables --terraform [--prefix tower-dev-]` prints the table definitions as HCL-ready JSON.

## Config

| Variable | Meaning |
|---|---|
| `TOWER_ENV` | `local` (default) → local keys below; anything else → KMS |
| `TOWER_LINE_ID_KEY`, `TOWER_MSISDN_KEY` | base64, ≥ 32 bytes each (local only; generate with `openssl rand -base64 32`) |
| `TOWER_KMS_HMAC_KEY_ID`, `TOWER_KMS_KEY_ID` | KMS HMAC_256 key and symmetric key (AWS) |
| `TOWER_DYNAMODB_ENDPOINT` | DynamoDB Local URL; unset on AWS |
| `TOWER_TABLE_PREFIX` | physical table-name prefix, e.g. `tower-dev-` |
| `AWS_REGION` | default `us-east-1` |

## Tests

```
uv run pytest packages/tower-consent -q
```

Every store-backed test runs twice: on **moto** (in-process, always) and on **DynamoDB Local** via testcontainers
(when `docker info` succeeds; otherwise those cases skip with the reason). `TOWER_DDB_BACKENDS=moto` narrows the
matrix for a CI job without Docker. Each test's tables are swept on teardown for anything phone-number-shaped.

## Showcase

`resolve` is a single request: `test_resolve.py` hooks botocore's `before-call` event and asserts the exact
request list is `["Query"]` for both branches, and `[]` for anything number-shaped. Grant → revoke → `resolve`
flips to `none` on the very next call.

## Reuse with your own CAMARA client

The Open Source mini-challenge piece. Nothing here is tied to Alexa or to one carrier. Give `bind_line` the number
your **Number Verification** call returned (never a number the user typed), and call `resolve` before every CAMARA
request: `grant == "none"` or `bound == False` means no carrier call. Checked on 2026-10-06 from a fresh venv with
plain `pip` and DynamoDB Local 2.5.2; it is idempotent with the same keys.

```bash
pip install -e packages/tower-policy -e packages/tower-consent   # from a clone; tower-consent imports tower-policy's types
docker run -d --rm -p 8000:8000 amazon/dynamodb-local:2.5.2
export TOWER_DYNAMODB_ENDPOINT=http://localhost:8000 AWS_REGION=us-east-1 \
       AWS_ACCESS_KEY_ID=dynamodblocal AWS_SECRET_ACCESS_KEY=dynamodblocal \
       TOWER_LINE_ID_KEY="$(openssl rand -base64 32)" TOWER_MSISDN_KEY="$(openssl rand -base64 32)"
python example.py
```

```python
"""Consent before any CAMARA call: bind a line, grant, check, revoke, check again."""

from datetime import UTC, datetime

from tower_consent import Store, bind_line, crypto_from_env, grant, resolve, revoke

store = Store.from_env()  # TOWER_DYNAMODB_ENDPOINT points at DynamoDB Local
store.ensure_tables()
hasher, cipher = crypto_from_env()  # TOWER_LINE_ID_KEY / TOWER_MSISDN_KEY locally; KMS on AWS
now = datetime.now(UTC)  # the library never reads a clock

# The number comes from YOUR Number Verification call (a fictional short one here).
mom = bind_line(store, hasher, cipher, "mom", "+15550100", "auth_code", now=now)
grant(store, mom.line_id, "asish", "watch", "mom", granted_by="mom", now=now)

view = resolve(store, "asish", "mom").view
print("before revoke:", view.bound, view.grant)  # True watch -> make your CAMARA call
revoke(store, mom.line_id, "asish", "watch", revoked_by="mom", now=now)
view = resolve(store, "asish", "mom").view
print("after revoke: ", view.bound, view.grant)  # True none -> refuse; no carrier call
```

Expected output: `before revoke: True watch`, then `after revoke:  True none`.

- `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` above are DynamoDB Local's dummy values (letters and digits only), not
  AWS keys. On AWS, leave the endpoint unset, set `TOWER_ENV=aws` and the two KMS key ids (see Config).
- Keep the two local keys stable: a new `TOWER_LINE_ID_KEY` gives the same number a different `line_id`, and the
  grant then collides on its alias (`AliasCollision`).
- The phone-side bind page (Number Verification auth-code flow, grants, revoke, audit view) is
  [`services/binding-page`](../../services/binding-page/README.md#reuse-with-your-own-camara-client).
