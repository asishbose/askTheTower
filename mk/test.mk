##@ Tests and checks
.PHONY: test test-unit test-integration test-e2e test-nightly test-report lint typecheck fmt privacy-grep secrets-check
# `make test`: the three layers in order, then one report. Every layer runs even if an earlier one fails, so the
# report always shows all of them; the exit status is non-zero if any layer or any gate failed.
STACK_UP = $(if $(filter local,$(ENV)),$(shell $(COMPOSE) ps --status running 2>/dev/null | grep -q tower-mcp && echo 1),1)
test: ## Unit → integration → e2e (ENV; local only when the stack is up) → artifacts/test-report.md with the gates
	@rc=0; \
	$(MAKE) --no-print-directory test-unit || rc=1; \
	$(MAKE) --no-print-directory test-integration || rc=1; \
	if [ "$(STACK_UP)" = "1" ]; then $(MAKE) --no-print-directory test-e2e || rc=1; \
	else echo "e2e skipped: ENV=local stack not running (make up)"; fi; \
	$(MAKE) --no-print-directory test-report || rc=1; \
	exit $$rc
test-unit: ## Pure-code tests, no I/O (starts the coverage data; unit-only coverage → artifacts/coverage-unit.xml)
	@mkdir -p artifacts
	$(PYTEST) -m unit --cov --cov-report=xml:artifacts/coverage-unit.xml --junitxml=artifacts/junit-unit.xml
test-integration: ## Component pairs over real interfaces (in-process mock, moto/DynamoDB Local); conformance; privacy; latency
	@mkdir -p artifacts
	$(PYTEST) -m integration --cov --cov-append --cov-report=term:skip-covered --cov-report=xml:artifacts/coverage.xml --junitxml=artifacts/junit-integration.xml
test-e2e: require-env ## Full stack against ENV (local|eks|aws): demo vs golden, showcase, alerts, binding, chaos
	@mkdir -p artifacts
	ENV=$(ENV) $(PYTEST) -m e2e tests/e2e --junitxml=artifacts/junit-e2e-$(ENV).xml
test-nightly: ## Cloud and slow chaos (ENV=aws): Gateway conformance, latency, backend swap, Lambda cold starts, demo-UI pixels
	@mkdir -p artifacts
	ENV=aws $(PYTEST) -m nightly --junitxml=artifacts/junit-nightly.xml
test-report: ## junit + coverage (per-path gates) + latency + conformance + privacy → artifacts/test-report.md; fails on a missed gate
	$(PY) scripts/test_report.py
lint: ## ruff
	$(UV) run ruff check . && $(UV) run ruff format --check .
typecheck: ## mypy --strict on packages
	$(UV) run mypy packages
fmt: ## ruff format + fix
	$(UV) run ruff format . && $(UV) run ruff check --fix .
privacy-grep: ## Grep generated output for phone numbers, health words, keys (+ the privacy-test registry)
	$(PYTEST) tests/privacy tests/integration/privacy -q
secrets-check: ## gitleaks over the full git history + grep of artifacts/ and docs/ — pre-submission gate
	gitleaks git --redact -v . && $(PY) scripts/secrets_grep.py
