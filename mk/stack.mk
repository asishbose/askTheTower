##@ Stack (local compose)
.PHONY: up seed logs logs-save ps web-chat
up: ## Build and start mock, tower, binding, alerts, dynamodb-local; wait healthy; seed demo
	@deploy/compose/init-env.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 600
	@$(MAKE) --no-print-directory seed
	@echo "stack up: Tower $(TOWER_URL) · binding $(BINDING_URL) · mock $(MOCK_URL) · alerts $(ALERTS_URL) · web chat http://127.0.0.1:8083/ — next: make demo"
seed: ## Reload scenarios/demo.yaml and the demo users/lines/grants (idempotent; runs in a container)
	@mkdir -p $(COMPOSE_LOGS)
	$(COMPOSE) --profile tools run --rm --no-deps -T --name att-seed seed 2>&1 | tee $(COMPOSE_LOGS)/seed.log; exit $${PIPESTATUS[0]}
logs: ## Tail local stack logs (Alerts' "SMS to=" lines are the phone buzz)
	$(COMPOSE) logs -f --tail=200
logs-save: ## Write the stack's logs to deploy/compose/logs/stack.log (the privacy grep reads it)
	@mkdir -p $(COMPOSE_LOGS)
	@$(COMPOSE) logs --no-color > $(COMPOSE_LOGS)/stack.log 2>&1 && echo "logs → $(COMPOSE_LOGS)/stack.log"
ps: ## Local stack status
	$(COMPOSE) ps
WEB_CHAT_URL ?= http://127.0.0.1:$(or $(WEB_CHAT_HOST_PORT),8083)/
web-chat: ## Open the local web chat page (sign-in stub: Asish or Mom) at http://127.0.0.1:8083/ (needs make up)
	@echo "web chat: $(WEB_CHAT_URL)  (sign in as Asish or Mom; ask \"Is my line OK?\")"
	@curl -fsS -o /dev/null $(WEB_CHAT_URL)healthz || { echo "web chat is not answering: run make up"; exit 1; }
	@if command -v wslview >/dev/null 2>&1; then wslview $(WEB_CHAT_URL); \
	  elif command -v xdg-open >/dev/null 2>&1; then xdg-open $(WEB_CHAT_URL) >/dev/null 2>&1 || true; \
	  elif command -v open >/dev/null 2>&1; then open $(WEB_CHAT_URL); fi
shell-%: ## Shell into a local service container, e.g. make shell-tower-mcp
	$(COMPOSE) exec $* /bin/sh
