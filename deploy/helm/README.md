# Helm charts

One chart per service plus an umbrella (`ask-the-tower`) that installs them as one release. They run the **same
five images** as `deploy/compose` and the AgentCore stack, with the same env names, ports and health checks; only
values differ (10 §1, §6). This is the portability proof: "production changes hosting, not architecture".

```
deploy/helm/
  mock-carrier/     Deployment (1 replica, in-memory state), Service :8443, scenarios ConfigMap (optional), Ingress (off)
  tower-mcp/        Deployment, Service :8000 (/mcp, /healthz), Ingress (ALB on EKS), HPA, PDB, ServiceAccount (IRSA)
  binding-page/     Deployment, Service :8081, Ingress (public, TLS), PDB, ServiceAccount (IRSA)
  alerts/           Deployment (ALERTS_MODE=k8s: hooks + internal API), CronJobs for 10 §3, Ingress (/hooks only),
                    https sidecar for in-cluster webhook sinks (kind), ServiceAccount (IRSA: DynamoDB, KMS, SNS)
  ref-client/       Job (`ref-client demo`, per release revision), optional toolbox Deployment, ServiceAccount (Bedrock),
                    `web`: the web chat agent (`serve`) Deployment + ClusterIP Service `ref-client-web` (09 §6; on in
                    values-kind/eks, Ingress off): kubectl port-forward svc/ref-client-web 8083:8080
  demo-ui/          the demo control room (doc 11): Deployment + ClusterIP Service, kind only (`values-kind.yaml`
                    enabled, `values-eks.yaml` enabled=false: laptop tooling, never on a cloud cluster)
  dynamodb-local/   kind only (disabled on EKS)
  umbrella/         the six as dependencies; generated Secret, local TLS, seed hook Job;
                    values.yaml (neutral) · values-kind.yaml · values-eks.yaml (+ values-eks.generated.yaml)
  tests/            helm-unittest suites, one folder per chart; run.sh runs them all
```

Every pod runs non-root (uid 10001; DynamoDB Local as its image user 1000) with a read-only root filesystem, no
privilege escalation, all capabilities dropped, a RuntimeDefault seccomp profile, resource requests and limits,
and no service-account token mounted. Object names equal the chart names (`mock-carrier`, `tower-mcp`, …), like the
compose service names, so `http://mock-carrier:8443` works unchanged: one Ask the Tower release per namespace.

Ingress NetworkPolicies: the mock accepts only its callers (Tower, Alerts and its CronJobs, the binding page, the
seed and the reference client); Alerts' `:8082` only Tower (and, on EKS, the ALB's VPC CIDR for `/hooks`); its
`:8443` only the mock; DynamoDB Local only the store users. Tower and the binding page are public endpoints. kind's
default CNI does not enforce NetworkPolicy; EKS does with the VPC CNI's network-policy agent (enabled in
`deploy/terraform/eks`).

## Three ways to install

**kind — no cloud** (`make helm-kind` = `scripts/helm_kind.sh`): creates the kind cluster `att`, builds the five
images as `ask-the-tower/*:kind`, `kind load`s them (plus Caddy and DynamoDB Local), installs the umbrella with
`values-kind.yaml`, waits for the seed hook, port-forwards Tower/mock/binding page/Alerts to 18080/18443/18081/18082,
runs `ref-client demo --env local` on the host, compares the transcripts with
`tests/e2e/golden/`, greps every pod log for phone numbers, and writes `artifacts/helm-kind.txt`.
`SKIP_BUILD=1` reuses the images; `KIND_DELETE=1` deletes the cluster at the end. About 3.5 minutes cold, 1 minute
warm.

**EKS** (`make deploy-eks`): applies `deploy/terraform/eks` (cluster, 2 × arm64 t4g.medium, IRSA roles, the AWS Load
Balancer Controller), writes `artifacts/tf-outputs-eks.json`, pushes the images (`make push ENV=eks`), runs
`scripts/render_values.py` (outputs → `umbrella/values-eks.generated.yaml`, gitignored), then
`helm upgrade --install att deploy/helm/umbrella -n ask-the-tower -f values-eks.yaml -f values-eks.generated.yaml`.
`make down-eks` uninstalls the release, waits for the ALB to go, and destroys only the EKS root. The cluster exists
for the showcase window only (cost line in `artifacts/cost.md`).

**Any Kubernetes**: write your own values file on top of `values.yaml`, with `values-kind.yaml` (self-contained)
or `values-eks.yaml` (AWS-backed) as the template:

```bash
uv run python scripts/k8s_seed.py --sync-chart         # the seed scripts into umbrella/files/ (generated)
helm dependency update deploy/helm/umbrella --skip-refresh
helm upgrade --install att deploy/helm/umbrella -n ask-the-tower --create-namespace -f my-values.yaml --wait
```

## What differs per target

| Value | kind (`values-kind.yaml`) | EKS (`values-eks.yaml` + generated) |
|---|---|---|
| `global.storeEnv.TOWER_ENV` | `local` (local keys from the Secret, `X-Tower-User` accepted) | `aws` (KMS keys, JWT inbound) |
| Store | `dynamodb-local` chart, `DYNAMO_ENDPOINT=http://dynamodb-local:8000` | real DynamoDB, the AgentCore stack's tables (IRSA) |
| Crypto | `TOWER_LINE_ID_KEY` / `TOWER_MSISDN_KEY` from `att-secrets` | `TOWER_KMS_KEY_ID` / `TOWER_KMS_HMAC_KEY_ID` (outputs) |
| Images | `ask-the-tower/*:kind`, loaded into kind | ECR repositories, tag = git sha, arm64 nodes |
| Alerts | `ALERTS_MODE=local` (in-process scheduler at `ALERTS_CLOCK_SCALE=60`, as compose), SMS = log line, CronJobs off | `ALERTS_MODE=k8s`, CronJobs for 10 §3, `ALERTS_SENDER=sns` |
| Webhook sinks (https) | `https://alerts:8443`: Caddy sidecar, cert from a Helm-generated CA the mock trusts | `https://<hooks host>/hooks`: the ALB with ACM |
| Public endpoints | port-forward | one internet-facing ALB (ingress group), hosts `tower.`, `bind.`, `hooks.` |
| Binding admin | `BIND_ADMIN=1` (seed bind links, demo revoke) | off |
| Seed | compose seed: bind through the page's one-tap flow | AWS seed: mock reset + `seed_demo` with KMS line ids |
| Tower HA | 1 replica | 2 replicas, HPA 2–4, PDB |
| Demo UI (doc 11) | on, scripted agent; `kubectl port-forward svc/demo-ui 8090:8090` | off (never deployed to a cloud cluster) |

The generated file carries only what Terraform knows (repositories, tag, region, table prefix, key ARNs, SNS topic,
IRSA role per service account, ALB hosts, ACM certificate, public subnets, VPC CIDR, Bedrock model). The umbrella
makes its own Secret (`att-secrets`: bearers, session key, local keys, the mock's JWT key) with random values on
first install and keeps it on upgrade; nothing secret is in values or in git. The mock's client registry
placeholders (`local-dev-*`) are the in-cluster mock's own, not carrier credentials.

## Seed

The umbrella's `<release>-seed` Job (post-install/post-upgrade hook, tower-mcp image) runs `scripts/k8s_seed.py
--in-cluster`. Re-run it with `uv run python scripts/k8s_seed.py` (recreates the Job from `helm get hooks` and prints
its log). Its files come from `scripts/k8s_seed.py --sync-chart` (`make helm-lint`, `helm-kind` and `deploy-eks` do it).

## Checks

```bash
make helm-lint        # helm lint (each chart + umbrella with both values) + helm-unittest + helm template | kubeconform
make helm-template     # artifacts/helm-$(ENV).yaml
uv run pytest tests/helm -q    # render_values / k8s_seed units, env-name parity with compose, lint, kubeconform, unittest
```

helm-unittest: `helm plugin install https://github.com/helm-unittest/helm-unittest`.

## Known limits

- One release per namespace (fixed object names, as in compose).
- The CronJobs' `timeZone` needs Kubernetes ≥ 1.27.
- On EKS the demo's grant revoke uses the binding page's local admin, which is off outside `TOWER_ENV=local`, and
  Tower expects a JWT; `make demo ENV=eks` needs both settled (see `docs/submission/build-log/14.md`).
- Audit reconciliation is a CronJob (`alerts.cronJobs.jobs.reconcile`), off by default: it reads Tower's spans from
  CloudWatch (`aws/spans`), which Tower on EKS emits only under ADOT.
