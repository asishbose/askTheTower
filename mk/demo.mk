##@ Demo and showcase
SHOWCASES := mock policy-table binding tower gateway alerts audit ref alexa infra ui
.PHONY: demo corpus policy-table showcase showcase-artifacts ops-ui $(addprefix showcase-,$(SHOWCASES))
# SHOWCASE_ARGS passes flags to one showcase script (e.g. --print-only, --fast); DEMO_ARGS to `ref-client demo`.
# Local: the reference client runs on the host with uv when present, else in its container (Docker only).
# Either way the mock admin/clock calls and the grant revoke come from the demo script itself.
DEMO_RUNNER ?= $(if $(shell command -v $(UV) 2>/dev/null),uv,docker)
demo: require-env ## The three moments + transplant story via the reference client; prints transcripts (honours ENV)
ifeq ($(ENV),local)
	@$(MAKE) --no-print-directory seed >/dev/null || { echo "demo: make seed failed (is the stack up? make up)"; exit 2; }
	@mkdir -p $(COMPOSE_LOGS) artifacts/transcripts
	@set -o pipefail; \
	if [ "$(DEMO_RUNNER)" = "uv" ]; then $(UV) run ref-client demo --env local $(DEMO_ARGS); \
	else HOST_UID=$$(id -u) HOST_GID=$$(id -g) $(COMPOSE) --profile tools run --rm -T --name att-ref-client-demo ref-client demo --env local $(DEMO_ARGS); fi 2>&1 \
	  | tee $(COMPOSE_LOGS)/demo.log; rc=$$?; \
	$(MAKE) --no-print-directory logs-save; exit $$rc
else
	$(UV) run ref-client demo --env $(ENV) $(DEMO_ARGS)
endif
corpus: ## Tool-selection table from the reference client → artifacts/corpus.md
	$(UV) run ref-client corpus --env $(ENV)
policy-table: ## Print the policy decision table → artifacts/policy-table.md
	POLICY_TABLE_OUT=artifacts/policy-table.md $(PYTEST) packages/tower-policy/tests/test_table.py -q
showcase-mock: ## Mock carrier on its own: Swagger UI, clock trick, subscriptions, faults
	$(PY) services/mock-carrier/scripts/showcase.py $(SHOWCASE_ARGS)
showcase-tower: ## Tower on its own: Inspector, three tools, refusals, latency
	$(PY) services/tower-mcp/scripts/showcase.py $(SHOWCASE_ARGS)
showcase-binding: ## Binding page on a phone (simulated mobile data locally)
	$(PY) services/binding-page/scripts/showcase.py $(SHOWCASE_ARGS)
showcase-gateway: require-env ## Carrier client: DirectClient locally, Gateway on AWS; backend swap
	$(PY) packages/camara-client/scripts/showcase.py --env $(ENV)
showcase-alerts: require-env ## Proactive path: watch, fire, buzz, revoke, suppressed, escalation
	$(PY) services/alerts/scripts/showcase.py --env $(ENV) $(SHOWCASE_ARGS)
showcase-audit: require-env ## Audit: Mom's view, chain verified, tamper detection
	$(PY) packages/tower-audit/scripts/showcase.py --env $(ENV) $(SHOWCASE_ARGS)
showcase-ref: require-env ## Reference client: three moments from the terminal, then the corpus
	$(UV) run ref-client demo --env $(ENV) && $(MAKE) corpus
# The compose demo-ui container (scripted, CI and `make demo`) publishes 127.0.0.1:8090, so the host run takes 8091
# and both can be open side by side against the same stack (11 §12, 10 §5). On ENV=aws the UI reads
# deploy/.env.aws itself (11 §6): its admin URLs and bearers win over the shell, and nothing is blanked but DynamoDB's
# local endpoint.
SHOWCASE_UI_PORT ?= 8091
showcase-ui: require-env ## Demo control room on the host at 127.0.0.1:8091 (needs make up): Bedrock when credentials resolve, else scripted
	@if (exec 3<>/dev/tcp/127.0.0.1/$(SHOWCASE_UI_PORT)) 2>/dev/null; then \
	  echo "showcase-ui: 127.0.0.1:$(SHOWCASE_UI_PORT) is already in use — stop that process or pass SHOWCASE_UI_PORT=<free port>"; exit 2; fi
	@echo "showcase-ui: http://127.0.0.1:$(SHOWCASE_UI_PORT) — the pill shows the agent mode and ENV=$(ENV); Ctrl-C to stop"
	@ALERTS_INTERNAL_BEARER="$$(sed -n 's/^INTERNAL_BEARER=//p' $(COMPOSE_ENV_FILE) 2>/dev/null)" \
	  REF_AGENT="$${REF_AGENT:-auto}" DEMO_UI_HOST=127.0.0.1 DEMO_UI_PORT=$(SHOWCASE_UI_PORT) \
	  $(if $(filter local,$(ENV)),,DYNAMO_ENDPOINT= DEMO_UI_AWS_ENV_FILE=deploy/.env.$(ENV)) $(UV) run demo-ui
# The Ops tab with its aws runner asked for (doc 11 §2, T4): the same host run as showcase-ui on the same port, plus
# DEMO_UI_AWS_OPS=1. No require-env: the tab is how you bring the stack (and the AWS deployment) up. The aws runner is
# on only when the whole gate holds (127.0.0.1, this flag, make and the checkout, the root .env, make config-check).
ops-ui: ## The demo UI on the host at 127.0.0.1:8091/#ops with the Ops tab's aws runner on (DEMO_UI_AWS_OPS=1; the gate of doc 11 §2); the stack need not be up
	@if (exec 3<>/dev/tcp/127.0.0.1/$(SHOWCASE_UI_PORT)) 2>/dev/null; then \
	  echo "ops-ui: 127.0.0.1:$(SHOWCASE_UI_PORT) is already in use — stop that process (make showcase-ui?) or pass SHOWCASE_UI_PORT=<free port>"; exit 2; fi
	@echo "ops-ui: http://127.0.0.1:$(SHOWCASE_UI_PORT)/#ops — AWS runs behind the gate (make config-check); Ctrl-C to stop"
	@ALERTS_INTERNAL_BEARER="$$(sed -n 's/^INTERNAL_BEARER=//p' $(COMPOSE_ENV_FILE) 2>/dev/null)" \
	  REF_AGENT="$${REF_AGENT:-auto}" DEMO_UI_HOST=127.0.0.1 DEMO_UI_PORT=$(SHOWCASE_UI_PORT) DEMO_UI_AWS_OPS=1 \
	  $(if $(filter local,$(ENV)),,DYNAMO_ENDPOINT= DEMO_UI_AWS_ENV_FILE=deploy/.env.$(ENV)) $(UV) run demo-ui
showcase-alexa: ## Alexa+ simulator script and Tower log tail
	$(PY) services/tower-mcp/scripts/showcase_alexa.py $(SHOWCASE_ARGS)
showcase-infra: ## Clean-machine timing; terraform plan; teardown
	scripts/clean_machine.sh
SHOWCASE_PAUSE ?= 1
showcase: ## testing-and-showcase.md §4 steps 1–9 in order against the running stack, pausing (SHOWCASE_PAUSE=0: no pauses)
	$(PY) scripts/showcase_order.py $(if $(filter 1,$(SHOWCASE_PAUSE)),--pause,--fast)
showcase-artifacts: ## Regenerate every generated artefact; fail if any is stale
	$(PY) scripts/showcase_artifacts.py
