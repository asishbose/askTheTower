##@ EKS and Helm
TFE := terraform -chdir=deploy/terraform/eks
CHARTS := $(wildcard deploy/helm/*/Chart.yaml)
UMBRELLA := deploy/helm/umbrella
K8S_NS ?= ask-the-tower
HELM_RELEASE ?= att
KUBE_VERSION ?= 1.31.0
KUBECONFORM := kubeconform -strict -ignore-missing-schemas -summary -kubernetes-version $(KUBE_VERSION)
EKS_VALUES := -f $(UMBRELLA)/values-eks.yaml $(if $(wildcard $(UMBRELLA)/values-eks.generated.yaml),-f $(UMBRELLA)/values-eks.generated.yaml)
.PHONY: helm-lint helm-template helm-kind deploy-eks down-eks kube-context helm-deps
helm-deps:
	@$(PY) scripts/k8s_seed.py --sync-chart >/dev/null
	@helm dependency update $(UMBRELLA) --skip-refresh >/dev/null
helm-lint: helm-deps ## helm lint + helm-unittest + kubeconform (kind and eks values) for every chart and the umbrella
	for c in $(dir $(CHARTS)); do helm lint --quiet $$c || exit 1; done
	helm lint --quiet $(UMBRELLA) -f $(UMBRELLA)/values-kind.yaml && helm lint --quiet $(UMBRELLA) $(EKS_VALUES)
	@if helm plugin list 2>/dev/null | grep -q '^unittest'; then deploy/helm/tests/run.sh; \
	else echo "helm-unittest not installed: helm plugin install https://github.com/helm-unittest/helm-unittest"; exit 1; fi
	helm template $(HELM_RELEASE) $(UMBRELLA) -n $(K8S_NS) -f $(UMBRELLA)/values-kind.yaml | $(KUBECONFORM)
	helm template $(HELM_RELEASE) $(UMBRELLA) -n $(K8S_NS) $(EKS_VALUES) | $(KUBECONFORM)
helm-template: helm-deps ## Render the umbrella chart for the current ENV to artifacts/helm-$(ENV).yaml
	helm template $(HELM_RELEASE) $(UMBRELLA) -n $(K8S_NS) $(if $(filter eks,$(ENV)),$(EKS_VALUES),-f $(UMBRELLA)/values-kind.yaml) > artifacts/helm-$(ENV).yaml
helm-kind: ## kind cluster → load images → install umbrella → run the demo against it
	scripts/helm_kind.sh
deploy-eks: ## EKS module apply → render values → helm upgrade --install → seed Job → print URLs
	$(TFE) init -input=false && $(TFE) apply -input=false -auto-approve
	$(TFE) output -json > artifacts/tf-outputs-eks.json
	$(MAKE) --no-print-directory push ENV=eks
	aws eks update-kubeconfig --name $$($(TFE) output -raw cluster_name) --region $$($(TFE) output -raw region)
	$(PY) scripts/render_values.py --tag $(GIT_SHA) && $(MAKE) --no-print-directory helm-deps
	helm upgrade --install $(HELM_RELEASE) $(UMBRELLA) -n $(K8S_NS) --create-namespace $(EKS_VALUES) --wait --timeout 20m
	kubectl -n $(K8S_NS) logs job/$(HELM_RELEASE)-seed || true
	$(PY) scripts/render_env.py --env eks --outputs artifacts/tf-outputs-eks.json
	kubectl -n $(K8S_NS) get ingress
down-eks: ## Uninstall the chart and destroy the EKS module only (asks unless FORCE=1)
	@if [ "$(FORCE)" != "1" ]; then read -p "destroy the EKS cluster? [y/N] " a; [ "$$a" = "y" ] || exit 1; fi
	helm uninstall $(HELM_RELEASE) -n $(K8S_NS) --wait || true
	@# the load-balancer controller deletes the ALB when the Ingresses go; wait before Terraform removes the controller
	kubectl -n $(K8S_NS) wait --for=delete ingress --all --timeout=10m || true
	$(TFE) destroy -input=false -auto-approve
kube-context: ## Point kubectl at the EKS cluster
	aws eks update-kubeconfig --name $$($(TFE) output -raw cluster_name) --region $$($(TFE) output -raw region)
