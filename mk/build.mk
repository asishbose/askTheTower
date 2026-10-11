##@ Images
# Supply chain: base images are pinned by digest in each Dockerfile; SBOMs (syft, CycloneDX) and the CVE scan run
# against the local :latest images. The CLIs are used when installed (grype, else trivy for `scan`); otherwise the
# same tools run from their pinned container images, so Docker alone is enough.
SYFT_IMAGE ?= anchore/syft:v1.33.0
GRYPE_IMAGE ?= anchore/grype:v0.100.0
DOCKER_SOCK := -v /var/run/docker.sock:/var/run/docker.sock
SYFT ?= $(if $(shell command -v syft 2>/dev/null),syft,docker run --rm $(DOCKER_SOCK) $(SYFT_IMAGE))
SCANNER ?= $(if $(shell command -v grype 2>/dev/null),grype,$(if $(shell command -v trivy 2>/dev/null),trivy,grype-docker))
GRYPE := $(if $(filter grype,$(SCANNER)),grype,docker run --rm $(DOCKER_SOCK) -v att-grype-db:/root/.cache/grype $(GRYPE_IMAGE))
HAVE_IMAGE = docker image inspect ask-the-tower/$$s:latest >/dev/null 2>&1 || { echo "no image ask-the-tower/$$s:latest — run make build"; exit 2; }
# build-% is a pattern rule, so it must stay out of .PHONY (make skips implicit-rule search for phony targets)
.PHONY: build push sbom scan
build: $(addprefix build-,$(IMAGES)) ## Build all six images: the five services + demo-ui (tagged with git sha and latest; demo-ui is never pushed)
build-%: ## Build one image, e.g. make build-tower-mcp
	docker build -f services/$*/Dockerfile -t ask-the-tower/$*:$(GIT_SHA) -t ask-the-tower/$*:latest .
push: ## Build linux/arm64 (buildx) and push to ECR, tags <git sha> + latest (ENV=eks|aws; `make ecr-up` once first)
	@[ "$(ENV)" != "local" ] || (echo "push needs ENV=eks|aws" && exit 1)
	@mkdir -p artifacts
	$(PY) scripts/push_images.py --env $(ENV) --tag $(GIT_SHA) && echo $(GIT_SHA) > artifacts/image-tag
sbom: ## SBOM per image → artifacts/sbom/*.json (syft, CycloneDX)
	@mkdir -p artifacts/sbom
	@for s in $(IMAGES); do $(HAVE_IMAGE); \
	  $(SYFT) ask-the-tower/$$s:latest -q -o cyclonedx-json > artifacts/sbom/$$s.json || exit 1; \
	  echo "sbom → artifacts/sbom/$$s.json"; done
scan: ## CVE scan per image → artifacts/scan/*.txt; fails on critical (grype, else trivy)
	@mkdir -p artifacts/scan
	@rc=0; for s in $(IMAGES); do $(HAVE_IMAGE); \
	  if [ "$(SCANNER)" = "trivy" ]; then trivy image -q --severity HIGH,CRITICAL ask-the-tower/$$s:latest > artifacts/scan/$$s.txt; \
	    trivy image -q --exit-code 1 --severity CRITICAL ask-the-tower/$$s:latest >/dev/null; r=$$?; \
	  else $(GRYPE) ask-the-tower/$$s:latest -q --fail-on critical > artifacts/scan/$$s.txt; r=$$?; fi; \
	  n=$$(grep -ci ' critical ' artifacts/scan/$$s.txt || true); h=$$(grep -ci ' high ' artifacts/scan/$$s.txt || true); \
	  echo "scan $$s: critical=$$n high=$$h ($(SCANNER)) → artifacts/scan/$$s.txt"; [ $$r -eq 0 ] || rc=1; done; \
	exit $$rc
