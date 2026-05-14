DEFAULT_GOAL := help

.PHONY: deploy

dns-name ?= $(shell cat config.yml | yq e '.dns-name')
email ?= $(shell cat config.yml | yq e '.email')
name ?= $(shell cat config.yml | yq e '.name')
port ?= $(shell cat config.yml | yq e '.port')
eco-info-url ?= $(shell cat config.yml | yq e '.eco-info-url')
name-dashed ?= $(subst /,-,$(name))
git-hash ?= $(shell git rev-parse HEAD)
image-url ?= ghcr.io/$(name)/$(name-dashed):$(git-hash)

echo:
	echo $(image-url)

help: ## Print this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "%-30s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build-native: ## uv lock + uv sync.
	uv lock
	uv sync

.build-docker:
	docker build \
		--progress plain \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(name):latest \
		-t $(name):$(git-hash) \
		-t $(name):latest \
		.

build-docker: .build-docker ## Build the docker image locally with BuildKit cache.

.publish:
	docker tag $(name):$(git-hash) $(image-url)
	docker push $(image-url)

publish: build-docker .publish ## Tag and push the docker image to ghcr.io.

deploy-namespace:
	kubectl create namespace $(name-dashed)

deploy-secrets-cert:
	env \
		NAME=$(name-dashed) \
		envsubst < deploy/secrets-cert.yml | kubectl apply -f -

deploy-secrets-docker-repo:
	$(eval github-token := $(shell aws ssm get-parameter --name "/github/pat" --with-decryption --query "Parameter.Value" --output text))
	echo $(github-token) | docker login ghcr.io -u $(name) --password-stdin
	kubectl create secret docker-registry docker-registry \
		--namespace="$(name-dashed)" \
		--docker-server=ghcr.io/$(name) \
		--docker-username=$(name) \
		--docker-password=$(github-token) \
		--dry-run=client -o yaml | kubectl apply -f -

.deploy:
	env \
		NAME=$(name-dashed) \
		DNS_NAME=$(dns-name) \
		IMAGE=$(image-url) \
		envsubst < deploy/main.yml | kubectl apply -f -
	kubectl rollout status deployment/$(name-dashed)-app -n $(name-dashed) --timeout=5m

deploy: publish .deploy ## Build, publish, and roll out the application to the k3s cluster.

run-native: ## Run the FastAPI server with autoreload on the configured port.
	uv run uvicorn eco_mcp_app.http_app:app --reload --port $(port) --host 0.0.0.0

run-docker: ## Run the published container locally on the configured port.
	docker run --expose $(port) -p $(port):$(port) -it --rm $(name):latest

sync: ## Install deps via uv.
	uv sync

smoke: ## End-to-end smoke test the MCP server via stdio.
	(printf '%s\n' \
	  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{"extensions":{"io.modelcontextprotocol/ui":{"mimeTypes":["text/html;profile=mcp-app"]}}},"clientInfo":{"name":"claude-ai","version":"0.1.0"}}}' \
	  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
	  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
	  '{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"ui://eco/status.html"}}' \
	  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_eco_server_status","arguments":{}}}' \
	  '{"jsonrpc":"2.0","id":5,"method":"resources/read","params":{"uri":"ui://eco/economy.html"}}' \
	  '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_eco_economy","arguments":{}}}'; sleep 8) | uv run python -m eco_mcp_app

install-desktop: ## Wire eco-mcp-app into Claude Desktop's claude_desktop_config.json.
	python scripts/install-desktop-config.py

harness: ## Serve static/harness.html, the local Claude-Desktop-mimicking iframe host. Args - harness_port=<int>.
	@echo "Harness: http://localhost:$(or $(harness_port),8765)/static/harness.html"
	python3 -m http.server $(or $(harness_port),8765)

test: ## Run the pytest suite.
	uv run pytest

ruff: ## Lint + format check (no mutations).
	uv run ruff check src
	uv run ruff format --check src

fmt: ## Apply ruff fixes and formatting in place.
	uv run ruff check --fix src
	uv run ruff format src

precommit: ## Run all pre-commit hooks against every file.
	uv run pre-commit run --all-files

http: ## Run the MCP server over HTTP. Args - http_port=<int>.
	uv run uvicorn eco_mcp_app.http_app:app --reload --host 0.0.0.0 --port $(or $(http_port),$(port))
