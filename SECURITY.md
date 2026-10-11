# Security

## What the system stores

Ask the Tower stores as little as the design allows. Everything below is in DynamoDB, with KMS-encrypted tables on AWS ([`components/04`](docs/architecture/components/04-consent-and-binding.md), [`components/07`](docs/architecture/components/07-audit-log.md)).

| Data | Form | Why |
|---|---|---|
| Line identity | `line_id = HMAC-SHA256(E.164, key)`. On AWS this is a KMS `HMAC_256` key that never leaves KMS. | Tables are keyed on it, so no table is keyed on a number. |
| The phone number | `msisdn_enc`, AES-GCM ciphertext under a KMS data key (envelope). | Decrypted in memory only for a carrier call or an SNS send. |
| An alert phone | `alert_phone_enc`, ciphertext, same scheme | For SMS to a consented watcher. |
| Grants | owner line, grantee user id, tool kind, alias, granted/revoked timestamps | Consent per line, per tool, revocable. |
| Watch state | the last booleans and timestamps seen for a watched line | Detecting a *change* for alerts. |
| Audit rows | who asked, which tool, which line (`line_id`), outcome and reason codes, hash-chained | "Who checked my line this week?" — rows are appended before any answer is released. |

**Never stored:** location, call or message content, any history beyond the audit, and any raw number outside the mock carrier's own state.

A raw number appears at runtime in exactly three places: the mock's state, inside a carrier call, and inside the SNS send. Tests grep logs, transcripts, tool results and artefacts for phone-number-shaped strings (`make privacy-grep`).

## Secrets

No key, token or secret is kept in the repository.
- `make up` generates the local secrets into a gitignored `deploy/compose/.env`.
- On AWS, carrier credentials live in AgentCore Identity and Secrets Manager, and generated values are in encrypted Terraform state, never in git.
- `gitleaks` runs in CI. `make secrets-check` runs it over the full history.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's **private vulnerability reporting** on this repository (Security → Report a vulnerability). Include what you did, what happened, and the commit.

This is a hackathon project with no production deployment and no real subscriber data. We aim to acknowledge reports within a week.
