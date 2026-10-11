.PHONY: help config-check
##@ General
config-check: ## Validate the root .env (the one settings file; template .env.example) and list what is set, secrets masked
	@$(PY) scripts/config_check.py
help: ## Show this help (grouped), with the current ENV
	@echo "Ask the Tower — make targets  (ENV=$(ENV))"
	@awk -v c=$(COLOR) 'BEGIN{FS=":.*## "; b=c?"\033[1m":""; t=c?"\033[36m":""; r=c?"\033[0m":""} \
	  /^##@/{printf "\n%s%s%s\n", b, substr($$0,5), r; next} \
	  /^[a-zA-Z0-9_.%-]+:.*## /{printf "  %s%-20s%s %s\n", t, $$1, r, $$2}' $(MAKEFILE_LIST)
	@echo; echo "ENV=local|eks|aws selects the environment; FORCE=1 skips confirmations; QUIET=1; NO_COLOR=1"
