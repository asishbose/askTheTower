# Local stack (docker compose)

The demo environment: the same five images and the same environment variable names as AWS
(`docs/architecture/components/10-scheduler-and-infra.md` §1, local column). Only the values differ.

```
make up      # writes deploy/compose/.env (random local secrets) on first use, builds, starts, waits healthy, seeds
make demo    # the three moments + the transplant story via the reference client; transcripts → artifacts/transcripts/
make seed    # back to the demo's starting state (idempotent)
make logs    # tail; Alerts' "SMS to=" lines are the phone buzz
make down    # compose down -v: no containers, volumes or networks left
```

Docker is the only requirement. `make demo` runs the reference client with `uv` on the host when it is installed,
and otherwise in its container. The seed always runs in a container.

## Topology

```
host :8080 ─► tower-mcp :8000 /mcp ──► mock-carrier :8443 (DirectClient; MOCK_ADMIN=1; demo.yaml)
                 │   └──► alerts :8082 /internal/watch (bearer INTERNAL_BEARER)
                 └──► dynamodb-local :8000  ◄── binding-page :8081 (BIND_ADMIN=1) ◄── host :8081
                                                        └──► alerts :8082 /internal/watch (after a watch-settings save)
127.0.0.1:8443 ─► mock-carrier ──CloudEvents, https──► alerts-tls :8443 (Caddy, internal CA) ──► alerts /hooks/*
host :8082 ─► alerts (ALERTS_MODE=local: in-process scheduler; SMS → log line; mock clock)
host :8000 ─► dynamodb-local (in memory; six consent/audit tables + AlertsState)
127.0.0.1:8090 ─► demo-ui (doc 11; scripted agent) ──► tower-mcp /mcp · mock /_admin (bearer MOCK_ADMIN_TOKEN)
                  · binding-page /_admin · alerts /internal/sent (bearer INTERNAL_BEARER) · dynamodb-local (read)
tools profile: seed (tower-mcp image + ./seed), ref-client (TOWER_URL=http://tower-mcp:8000/mcp)
```

**Loopback only.** The mock carrier and the demo UI are published on `127.0.0.1`, not on every interface: the
mock's `/_admin` can reset the demo, and the UI can revoke grants (doc 11 decisions 1 and 7). The mock's mutating
`/_admin` routes also need `Authorization: Bearer $MOCK_ADMIN_TOKEN` (generated into `.env` by `make up`; the seed,
`make demo`, the e2e helpers and the demo UI send it). Its GET routes stay open: Tower and Alerts read the clock.

The network is `tower`. Containers reach each other by service name.

**Why `alerts-tls`.** CAMARA subscription sinks must be `https://`, and the mock refuses any other sink with
400 `INVALID_SINK`. Caddy serves `https://alerts-tls:8443/hooks/*` with a certificate from its own local CA.
`run.sh` copies only that CA's root certificate to the `tls-trust` volume. The mock trusts that certificate
through `SSL_CERT_FILE`, and it calls no other https host. On AWS, API Gateway terminates TLS instead.

## Phone buzz

Locally an SMS is a log line from Alerts:

```
alerts.sms SMS to=chain:user-asish body='SIM moved to another device at 10:20 today. Not you? Call your carrier now.'
```

`make logs` shows these lines, and `make showcase` prints them after moment 3.

For a rehearsal with a real phone, use a free SMS-gateway app on an Android phone, or any SMS-gateway service
that accepts an HTTP POST. Alerts POSTs `{"to": "<E.164>", "body": "<text>"}`. If your gateway's API names the
fields differently, put a few-line relay in front of it. Set `SMS_GATEWAY_URL=http://<phone or relay>:<port>/<path>`
in `deploy/compose/.env`, then run `make up`. Alerts keeps writing the log line and also POSTs to that URL: a set
URL switches the sender to log + webhook, while `ALERTS_SENDER` stays `log`. The `to` field is the one place a
number leaves Alerts locally, as it does with the SNS call on AWS. This path exists only locally; on AWS, SNS
sends the SMS. It was not exercised in the autonomous build.

## Logs and privacy

`make demo` writes these files to `logs/`:

- `demo.log`: the reference client's output.
- `seed.log`: the seed's output.
- `stack.log`: `docker compose logs` of every service.

`make test-e2e` greps all three for phone numbers, keys, bearers and health words in SMS bodies
(`tests/e2e/test_privacy_logs.py`). Caddy's start-up JSON has epoch timestamps that look like phone numbers, so
`run.sh` strips them.

## Settings

Every variable, with what it does, is in `.env.example`. `make up` copies that file to `.env` and replaces each
`__generate__` with 32 random bytes. Any of these keys set (non-empty) in the repo-root `.env` — the one settings
file, template `/.env.example` — replaces the value here on every `make up`; keys compose doesn't use, such as AWS
credentials, are never copied here. Container-to-container wiring is fixed in `docker-compose.yml`, because it
is the shape of the stack rather than a setting.
