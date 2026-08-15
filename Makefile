# arc.ai — the web front end.
#
# This is the new, separate build. It does not touch the ARC application: `ARC`,
# `arc serve`, and arc/interface/webui/ all behave exactly as they did.
#
# The site itself is static — plain HTML, CSS, and ES modules with no build step — so
# what gets deployed is just the contents of web/. `make web` adds a small dev server
# that also proxies /api to a running ARC, which is what lets the browser talk to it
# without loosening anything on ARC's side.

PORT ?= 4173
PY   ?= $(shell [ -x venv/bin/python ] && echo venv/bin/python || \
	       ([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3))

.DEFAULT_GOAL := help
.PHONY: help web open check clean

help: ## Show this help
	@echo "arc.ai — targets:"
	@echo
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "  Chat and memory need a running ARC. Start one in another terminal"
	@echo "  with 'ARC' or 'arc serve'; the site works without it, but says so."

web: ## Serve the site locally (PORT=4173 by default)
	@$(PY) web/serve.py --port $(PORT)

open: ## Open the site in a browser (start 'make web' first)
	@open http://127.0.0.1:$(PORT)

check: ## Run the tests for the web build
	@$(PY) -m pytest tests/test_site.py -q

clean: ## Remove Python caches from the web build
	@find web -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
