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
.PHONY: ecr-up ecr-outputs plan deploy seed-aws outputs latency-aws register-gateway tf-check down-all \
	cognito-users web-chat-sync web-chat-url
ecr-up: ## Create the five ECR repositories (own state; idempotent; survives `make down`) → artifacts/tf-outputs-ecr.json
	$(TFR) init -input=false && $(TFR) apply -input=false -auto-approve $(TFR_VARS) && $(MAKE) --no-print-directory ecr-outputs
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
	@$(TFE) init -input=false >/dev/null && if [ -n "$$($(TFE) state list 2>/dev/null)" ]; then \
	  $(MAKE) --no-print-directory down-eks FORCE=$(FORCE); else echo "eks root: nothing in state"; fi
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
latency-aws: ## 200 calls per tool against the AgentCore deployment → artifacts/latency-aws.md
	ENV=aws $(PY) scripts/latency.py --out artifacts/latency-aws.md
register-gateway: ## Register the vendored CAMARA specs with AgentCore Gateway; write gateway-tools.json
	$(PY) scripts/register_gateway.py
tf-check: ## terraform fmt -check + validate of the main and ecr roots (init -backend=false; never touches AWS) + tables.auto.tfvars.json current
	$(PY) deploy/terraform/modules/dynamodb/generate.py --check
	terraform -chdir=deploy/terraform fmt -check -recursive
	$(TF) init -backend=false -input=false >/dev/null && $(TF) validate
	$(TFR) init -backend=false -input=false >/dev/null && $(TFR) validate
