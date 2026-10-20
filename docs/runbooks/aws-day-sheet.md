# AWS day sheet — from nothing to the three moments on AWS, in order

The complete sequence, as you will actually do it: console and terminal for the one-time setup, then the **Ops tab** for everything else. Each step says where it runs, what to press, what "done" looks like, and what to do if it isn't. Companion to [`aws-first-run.md`](aws-first-run.md) (the why) — this is the what.

Everything in WSL runs from the repo root:

```bash
cd /mnt/c/GitRepos/AskTheTower
set -a; . ./.env; set +a         # loads your AWS keys and the rest of .env into this shell
```

`.env` already has: your AWS key pair, `AWS_REGION=us-east-1`, `REF_AGENT=scripted`, `DEMO_UI_AWS_OPS=1`, the two Cognito passwords, `MOCK_ADMIN_TOKEN`.

---

## Part A — one-time setup (≈ 30 min of you, plus a DNS wait)

| # | Where | Do | Done when |
|---|---|---|---|
| A1 | console → Route 53 → **Registered domains** → Register | Buy a short, cheap domain (`.link`/`.click`, ~$3–5/yr; not covered by the credit) | Route 53 → Hosted zones lists it. Copy the **Hosted zone ID** (`Z0…`) |
| A2 | terminal | `ACCT=$(aws sts get-caller-identity --query Account --output text); echo $ACCT` | prints 12 digits |
| A3 | terminal | State bucket + lock table (paste as one block):<br>`B=ask-the-tower-tfstate-$ACCT`<br>`aws s3api create-bucket --bucket $B --region us-east-1`<br>`aws s3api put-bucket-versioning --bucket $B --versioning-configuration Status=Enabled`<br>`aws s3api put-bucket-encryption --bucket $B --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'`<br>`aws s3api put-public-access-block --bucket $B --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true`<br>`aws dynamodb create-table --table-name ask-the-tower-tflock --billing-mode PAY_PER_REQUEST --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH --region us-east-1`<br>`sed -i "s/REPLACE-ME-ask-the-tower-tfstate/$B/; s/REPLACE-ME-ask-the-tower-tflock/ask-the-tower-tflock/" deploy/terraform/backend.tf deploy/terraform/ecr/backend.tf` | `grep REPLACE-ME deploy/terraform/backend.tf deploy/terraform/ecr/backend.tf` prints nothing |
| A4 | console → Bedrock → **Model access** → Modify | Tick **Amazon Nova Micro** → Save | "Access granted" |
| A5 | terminal | `aws sns create-sms-sandbox-phone-number --region us-east-1 --phone-number +1XXXXXXXXXX` → read the SMS → `aws sns verify-sms-sandbox-phone-number --region us-east-1 --phone-number +1XXXXXXXXXX --one-time-password <code>` | `aws sns list-sms-sandbox-phone-numbers --region us-east-1` shows `Verified` |
| A6 | editor | `cp deploy/terraform/envs/aws.tfvars.example deploy/terraform/envs/aws.tfvars` then set:<br>`mock_domain_name = "mock.<your domain>"`<br>`mock_route53_zone_id = "<Z0… from A1>"`<br>`sms_sandbox_numbers = ["+1XXXXXXXXXX"]`<br>keep `mock_assume_mobile_data = true`, `demo_admin_cidrs = []` | file saved (it is gitignored) |
| A7 | terminal | `make config-check`<br>`terraform -version` (≥ 1.6)<br>`docker buildx ls \| grep -q arm64 \|\| docker buildx create --use --platform linux/arm64`<br>`jq --version` | all four print without error |
| A8 | terminal | `make up` — the local stack, needed by the UI and as the sanity baseline | ends with `stack up: …` |

Local proof before touching AWS (5 min): `make demo` → `demo ok: 4 stories`.

---

## Part B — the pipeline, from the Ops tab (an afternoon the first time)

```bash
make ops-ui
```

Open **http://127.0.0.1:8091/#ops**. The header must show `us-east-1 · <ACCT>` and the AWS buttons enabled. If they're greyed, the reason is printed next to them (not on 127.0.0.1, `DEMO_UI_AWS_OPS` unset, or `config-check` failing).

Press the steps **in order**; each turns green with its evidence before the next enables. The log streams in the pane; every job's transcript is also saved under `artifacts/ops-log/`.

| Press | Takes | Done when | If not |
|---|---|---|---|
| **1 · ECR repositories** | 1 min | five repository names shown | `NoSuchBucket` → A3 not done; `AccessDenied` → the IAM user lacks ECR/S3 rights |
| **2 · Push the five images** | 5–10 min | image tag shown (the git short sha; the tree must be committed) | `no match for platform` → A7 buildx |
| **3 · terraform plan** | 2 min | chips `N to add · 0 to change · 0 to destroy`; **read the log once**: expect `module.cognito`, `module.web_chat`, two `agentcore_runtime`, Gateway, Identity, the Fargate mock + ALB + ACM cert, Lambda alerts, scheduler, SNS, six tables, two KMS keys | certificate / zone error → A1/A6; a `*_secret` in clear → stop and paste the line to the agent |
| **4 · Deploy** | 10–20 min | apply ok → Gateway specs registered → mock seeded; evidence: the Tower runtime ARN | If it stops, read the last 30 log lines, fix the one thing it names (usually an IAM permission or the ACM validation still pending), press **4** again — apply is idempotent. The four known first-apply unknowns and their symptoms are in `build-log/20.md` §"Unverified"; paste the error to the VS Code agent: *"fix against this apply output; no git"* |
| **5 · Write deploy/.env.aws** | 10 s | URLs and tokens written (gitignored) | — |
| **6 · Cognito users** | 30 s | `asish`, `mom` with their `sub`s | passwords come from `.env` |
| **7 · Seed the demo rows** | 30 s | Mom bound, Mom→Asish watch; **Asish left unbound** (for Flow 1) | — |
| **8 · Upload the web chat page** | 1 min | S3 synced, CloudFront invalidated | — |
| **9 · Web chat URL** | 1 s | the `https://…cloudfront.net/` URL | — |

**Smoke test (10 min, by hand):**
1. Open the URL, sign in as `asish` (password: `grep COGNITO_PASSWORD_ASISH .env`).
2. "Is my line OK?" → *Your line isn't connected yet* + a `/bind/` link.
3. Open the link **on your phone, Wi-Fi off** → Verify → *Line connected*.
4. Ask again → an answer. (`mock_assume_mobile_data=true` is the one simulation; the doc says so.)
5. `curl -s -o /dev/null -w '%{http_code}\n' -X POST "$(jq -r .agent_url.value artifacts/tf-outputs.json)"` → `401`.

| Press | Takes | Done when |
|---|---|---|
| **10 · Bind Asish for the stories** | 30 s | Asish bound (the four stories need it) |
| *Open the admin path to your /32* (admin section; plan + deploy ≈ 3 min, may ask for `deploy`) | 3–5 min | pill `admin on` — **11 needs it**: the stories fire carrier events through the mock's `/_admin` and save watch settings and grants through the binding page's admin, both reachable only while the path is open (first run, 2026-10-14: 11 fails fast with `…/_admin/watch-settings answered 404` without it). Close it after Part C |
| **11 · make demo on AWS** | 3 min | `demo ok: 4 stories` — the transcripts in `artifacts/transcripts/` match local on tool calls and reason codes: **that match is the slide's claim** |
| **12 · Latency: 50 calls per path** | 2 min | `artifacts/latency-aws.md`; the p95 is the number for the deck |
| **14 · Write artifacts/test-report.md** | 1 min | gates met incl. the AWS latency row |

(Step 13, `test-e2e ENV=aws`, is in the allowlist but not on the tab's stepper; run it from the terminal if you want the e2e layer against AWS: `make test-e2e ENV=aws`.)

---

## Part C — the three moments on AWS, from the control room (the video footage)

The on-camera run sheet with timings and narration: [`live-demo-3min.md`](live-demo-3min.md).

| Press | Done when |
|---|---|
| **Open the admin path to your /32** (admin section) | plan + deploy (~3 min; the mock task restarts, 1–2 min more) → `deploy/.env.aws` gains the admin URLs → the pill reads `ENV=aws · admin on` |

Switch to the **Control room** tab with `ENV=aws` (the pill shows it), press **Reset**, then:

1. **Moment 1** → Asish's line swaps → `SIM_SWAPPED_RECENT` chip, beacon coral on the Stage.
2. **Moment 2** → forwarding set → `CALL_FORWARDING_SET`.
3. **Moment 3** → "Is Mom's line OK?" OK → "Watch Mom's line" → fire Mom's SIM swap → **your phone buzzes** (real SNS) and the SMS lane fills → Mom revokes → fire again → no SMS, `SUPPRESSED_REVOKED` in the feed.

Record this. Then **Close the admin path** (type `close`) — don't leave it open overnight.

Optional, if there's time: `uv run pytest tests/aws/test_admin_gate.py -m nightly` while the path is open (403/401/404 as designed).

---

## Part D — end of day

| Press / do | Why |
|---|---|
| Write the hours up and the bill delta into `artifacts/cost.md` | submission evidence |
| **Destroy the stack, keep ECR** (type `down`) | ~$5–10/day while up; images stay, so tomorrow is **4 · Deploy** again (10–20 min) |
| After judging: **Destroy everything, ECR included** (type `down-all`); delete the sandbox number; delete the state bucket and lock table by hand | zero bill |

---

## If something refuses

| Symptom | Cause | Fix |
|---|---|---|
| AWS buttons greyed | gate | header says which of the three: 127.0.0.1 bind, `DEMO_UI_AWS_OPS=1`, `config-check` |
| 1 fails `NoSuchBucket` | A3 | run A3; both `backend.tf` files |
| 2 fails on platform | buildx | A7 |
| 2 fails with `exec /bin/sh: exec format error` | arm64 emulation lost after a Docker/WSL restart (`push` now re-registers it itself) | `docker run --privileged --rm tonistiigi/binfmt --install arm64`, then 2 again |
| 2 refuses "tree not clean" | tag = git sha | commit first (your single commit) |
| 3 fails on certificate / zone | A1/A6 | zone id must be the hosted zone of the registered domain; cert validation needs the zone live |
| 4 stops mid-apply | first-apply unknowns | read the last lines; press 4 again after the fix |
| Web chat 500 on every question | A4 | Nova Micro access |
| Web chat 401 after a valid sign-in | authorizer not passing `Authorization` | build-log/20 unverified #2 → agent |
| Bind page refuses the phone | Wi-Fi on, or `mock_assume_mobile_data` false | phone on mobile data; A6 |
| No SMS on the phone | A5 | number must be `Verified` in the sandbox |
| 11 shows Asish `NOT_BOUND` | step 10 skipped | press 10 |
| Moment 1 shows OK on AWS | mock clock not at wall time | `MOCK_CLOCK=wall` is in the task def; press **Reset** once (it reloads the scenario at wall time) |
