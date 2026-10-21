##@ AWS (AgentCore)
TF := terraform -chdir=deploy/terraform
# The ECR root: its own state, created once by `ecr-up`, kept by `down`, destroyed only by `down-all`.
TFR := terraform -chdir=deploy/terraform/ecr
# envs/aws.tfvars (gitignored, from envs/aws.tfvars.example) when present. The ECR root reads the same file for
# region/name_prefix/environment; -compact-warnings folds the "undeclared variable" warnings for the rest.
TFVARS_FILE := $(if $(wildcard deploy/terraform/envs/aws.tfvars),envs/aws.tfvars)
# The tag the Runtime, the Lambdas and ECS run: the last tag `make push` completed (artifacts/image-tag), else the
# git sha. A commit between `make push` and `make deploy` therefore no longer points the stack at a tag that was
# never pushed. Override with `make deploy IMAGE_TAG=<tag>`.
IMAGE_TAG ?= $(or $(shell cat artifacts/image-tag 2>/dev/null),$(GIT_SHA))
TF_VARS := $(if $(TFVARS_FILE),-var-file=$(TFVARS_FILE)) -var=image_tag=$(IMAGE_TAG)
# destroy ignores image_tag, so `down` does not pass it: it works whatever tag is in state.
TF_DOWN_VARS := $(if $(TFVARS_FILE),-var-file=$(TFVARS_FILE))
TFR_VARS := $(if $(TFVARS_FILE),-var-file=../$(TFVARS_FILE)) -compact-warnings
.PHONY: ecr-up ecr-outputs ecr-prune plan deploy seed-aws outputs latency-aws register-gateway tf-check down-all \
	cognito-users web-chat-sync web-chat-url admin-open admin-close
ecr-up: ## Create the five ECR repositories (own state; idempotent; survives `make down`) → artifacts/tf-outputs-ecr.json
	$(TFR) init -input=false && $(TFR) apply -input=false -auto-approve $(TFR_VARS) && $(MAKE) --no-print-directory ecr-outputs
ecr-prune: ## Delete every image in the five repositories except the one tagged with artifacts/image-tag (the lifecycle policy does the same within 24 h)
	@tag=$$(cat artifacts/image-tag) && region=$$(jq -r .region.value artifacts/tf-outputs-ecr.json) && \
	for r in $$(jq -r '.ecr_repositories.value | keys[]' artifacts/tf-outputs-ecr.json); do \
	  repo=$$(jq -r ".ecr_repositories.value[\"$$r\"]" artifacts/tf-outputs-ecr.json | sed 's|^[^/]*/||'); \
	  ids=$$(aws ecr describe-images --region $$region --repository-name $$repo \
	    --query "imageDetails[?!contains(imageTags || \`[]\`, '$$tag')].imageDigest" --output text); \
	  [ -z "$$ids" ] && { echo "$$repo: only $$tag"; continue; }; \
	  args=""; for d in $$ids; do args="$$args imageDigest=$$d"; done; \
	  aws ecr batch-delete-image --region $$region --repository-name $$repo --image-ids $$args --query 'length(imageIds)' --output text | sed "s|^|$$repo: deleted |"; \
	done
ecr-outputs: ## Write the ECR root's outputs (repository URLs, registry, region) to artifacts/tf-outputs-ecr.json
	@mkdir -p artifacts
	$(TFR) output -json > artifacts/tf-outputs-ecr.json
plan: ## terraform plan only (main root; needs `make ecr-up`; image_tag = last pushed tag)
	@mkdir -p artifacts
	@echo "image_tag=$(IMAGE_TAG)"
	$(TF) init -input=false && $(TF) plan -input=false $(TF_VARS) -out=$(CURDIR)/artifacts/tfplan && $(TF) show -no-color $(CURDIR)/artifacts/tfplan > artifacts/terraform-plan.txt
deploy: ## Check the pushed tag exists; terraform apply; register Gateway specs; seed the mock on Fargate; print outputs
	$(PY) scripts/push_images.py --env aws --tag $(IMAGE_TAG) --verify
	$(TF) init -input=false && $(TF) apply -input=false -auto-approve $(TF_VARS) && $(MAKE) outputs ENV=aws && $(MAKE) register-gateway ENV=aws && $(MAKE) seed-aws ENV=aws && $(MAKE) outputs ENV=aws
down: ## Local: compose down -v. ENV=aws: destroy the main root — ECR repositories and images are kept (asks unless FORCE=1)
	@if [ "$(ENV)" = "aws" ]; then if [ "$(FORCE)" != "1" ]; then read -p "destroy AWS resources (ECR is kept)? [y/N] " a; [ "$$a" = "y" ] || exit 1; fi; \
	  $(TF) init -input=false && $(TF) destroy -input=false -auto-approve $(TF_DOWN_VARS); fi
	$(COMPOSE) down -v --remove-orphans || true
down-all: ## After judging: down-eks (if up) + down ENV=aws + destroy the ECR root — the only target that deletes images
	@if $(TFE) init -input=false >/dev/null 2>&1 && [ -n "$$($(TFE) state list 2>/dev/null)" ]; then \
	  $(MAKE) --no-print-directory down-eks FORCE=$(FORCE); else echo "eks root: not initialised or nothing in state (skipped)"; fi
	$(MAKE) --no-print-directory down ENV=aws FORCE=$(FORCE)
	@if [ "$(FORCE)" != "1" ]; then read -p "delete the five ECR repositories and every image in them? [y/N] " a; [ "$$a" = "y" ] || exit 1; fi
	$(TFR) init -input=false && $(TFR) destroy -input=false -auto-approve $(TFR_VARS)
	rm -f artifacts/tf-outputs-ecr.json artifacts/image-tag
seed-aws: ## Seed the Fargate mock, and the demo rows under the Cognito subs from `make cognito-users` (skipped, exit 0, without them)
	$(PY) scripts/aws_seed.py $(SEED_ARGS)
cognito-users: ## Create the web chat users asish + mom in the Cognito pool (password: COGNITO_PASSWORD_<NAME> or a prompt) → artifacts/cognito-users.json
	$(PY) scripts/cognito_user.py create asish && $(PY) scripts/cognito_user.py create mom
web-chat-sync: ## Render the web chat config.js from the outputs, upload services/web-chat/ to S3, invalidate CloudFront
	$(PY) scripts/web_chat_sync.py
web-chat-url: ## Print the web chat page URL (output web_chat_url)
	@$(PY) scripts/web_chat_sync.py --url
outputs: ## Print terraform outputs and write deploy/.env.aws
	@mkdir -p artifacts
	$(TF) output -json > artifacts/tf-outputs.json && $(PY) scripts/render_env.py --env aws
# 50 calls per path (200 in all): AgentCore recycled Tower's microVM after ~200 calls on one session (2026-10-14),
# and a longer run measures that restart, not Tower. LATENCY_AWS_N=200 for the full count.
LATENCY_AWS_N ?= 50
latency-aws: ## 50 calls per path (200 in all) against the AgentCore deployment → artifacts/latency-aws.md
	ENV=aws $(PY) scripts/latency.py -n $(LATENCY_AWS_N) --out artifacts/latency-aws.md
register-gateway: ## Register the vendored CAMARA specs with AgentCore Gateway; write gateway-tools.json
	$(PY) scripts/register_gateway.py
tf-check: ## terraform fmt -check + validate of the main and ecr roots (init -backend=false; never touches AWS) + tables.auto.tfvars.json current
	$(PY) deploy/terraform/modules/dynamodb/generate.py --check
	terraform -chdir=deploy/terraform fmt -check -recursive
	$(TF) init -backend=false -input=false >/dev/null && $(TF) validate
	$(TFR) init -backend=false -input=false >/dev/null && $(TFR) validate
# The demo admin path (10 §2.1; doc 11 §2 "admin-open / admin-close"): scripts/admin_cidr.py edits the one
# demo_admin_cidrs line of envs/aws.tfvars (refuses a missing file or a multi-line list), then plan, then deploy.
# Changing the list replaces DEMO_ADMIN_BEARER and rolls the mock task: the plan gate lets exactly that roll through
# and stops with exit 3 (read the plan, then `make deploy`) only when anything else would be destroyed, unless
# FORCE=1. The address is never printed.
ADMIN_CIDR ?=
admin-open: ## Open the demo admin path to your /32 (ADMIN_CIDR, else checkip.amazonaws.com): tfvars, plan, deploy (exit 3 if the plan destroys more than the bearer/mock roll, unless FORCE=1)
	$(PY) scripts/admin_cidr.py open --cidr "$(ADMIN_CIDR)"
	$(MAKE) --no-print-directory plan ENV=aws
	$(PY) scripts/admin_cidr.py plan-gate --name admin-open $(if $(filter 1,$(FORCE)),--force)
	$(MAKE) --no-print-directory deploy ENV=aws
admin-close: ## Close the demo admin path: demo_admin_cidrs = [] in envs/aws.tfvars, plan, deploy (asks unless FORCE=1)
	@if [ "$(FORCE)" != "1" ]; then read -p "close the demo admin path (plan, then deploy)? [y/N] " a; [ "$$a" = "y" ] || exit 1; fi
	$(PY) scripts/admin_cidr.py close
	$(MAKE) --no-print-directory plan ENV=aws
	$(PY) scripts/admin_cidr.py plan-gate --name admin-close $(if $(filter 1,$(FORCE)),--force)
	$(MAKE) --no-print-directory deploy ENV=aws
