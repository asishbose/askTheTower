##@ Docs and consistency
.PHONY: diagrams-png docs-check deck-check check-deliverables
diagrams-png: ## Export docs/architecture/diagrams/*.drawio to png/ (needs drawio CLI)
	$(PY) scripts/diagrams_png.py
docs-check: ## Every `make x` mentioned in docs/, prompts/, README.md must exist; links resolve
	$(PY) scripts/make_check.py && $(PY) scripts/link_check.py
deck-check: ## Numbers on the deck vs artifacts/ (docs/submission/deck-consistency.md)
	$(PY) scripts/deck_check.py
check-deliverables: ## Every path named in the prompts' Deliverables blocks exists
	$(PY) scripts/check_deliverables.py
