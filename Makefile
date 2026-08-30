SHELL := /usr/bin/env bash
.PHONY: start stop smoke test java tutorial full-sample manifest demo inspect pull-up pull-down pull-smoke help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

test: ## Run the server unit tests (stdlib unittest, no Docker needed)
	cd server && python3 -m unittest discover -s tests -v

start: ## Build and start the local OSDU-compatible façade (Docker)
	cd server && ./start.sh

stop: ## Stop the local façade
	cd server && ./stop.sh

smoke: ## Run health + smoke checks against the local façade
	cd server && ./smoke-test.sh

pull-up: ## Pull the published image from GHCR and start it
	cd server && docker compose -f compose.pull.yaml up -d

pull-down: ## Stop the image-based façade
	cd server && docker compose -f compose.pull.yaml down

pull-smoke: ## Health + smoke checks against the pulled image
	cd server && ./smoke-test.sh

java: ## Run the real os-core-common Java consumer (façade must be started)
	cd examples/well360/client && ./run.sh

tutorial: ## Demo 1: tutorial-style direct Storage ingestion
	./examples/well360/demo/run-tutorial.sh

full-sample: ## Demo 2: load the supplied CSV sample end to end
	./examples/well360/demo/run-full-sample.sh

manifest: ## Demo 3: Manifest / Workflow ingestion
	./examples/well360/demo/run-manifest.sh

demo: ## Full one-command demo (start + full sample + Java consumer)
	./demo-all.sh

inspect: ## Show the real os-core-common API signatures
	cd examples/well360/client && ./inspect-api.sh