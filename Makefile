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

help:
	@awk '/^## / \
		{ if (c) {print c}; c=substr($$0, 4); next } \
			c && /(^[[:alpha:]][[:alnum:]_-]+:)/ \
		{printf "%-30s %s\n", $$1, c; c=0} \
			END { print c }' $(MAKEFILE_LIST)

# rebuild requirements.txt whenever pyproject.toml changes
.build: pyproject.toml
	uv lock
	uv export --no-hashes --no-dev --no-emit-project --format requirements-txt -o requirements.txt
	touch .build

## build project on your plain old machine
#  see also: build-docker
build-native: .build
	uv sync

.build-docker:
	docker build \
		--progress plain \
		--build-arg BUILDKIT_INLINE_CACHE=1 \
		--cache-from $(name):latest \
		-t $(name):$(git-hash) \
		-t $(name):latest \
		.

## build project inside of a docker container
#  see also: build-native
build-docker: .build .build-docker

.publish:
	docker tag $(name):$(git-hash) $(image-url)
	docker push $(image-url)

## publish the docker image to the registry
publish: build-docker .publish

## deploy the namespace for the application
deploy-namespace:
	kubectl create namespace $(name-dashed)

## deploy the cert secrets utilized by the application
deploy-secrets-cert:
	env \
		NAME=$(name-dashed) \
		envsubst < deploy/secrets-cert.yml | kubectl apply -f -

## deploy the docker registry secret utilized by the application
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

## deploy the application to the cluster
deploy: publish .deploy

## run project on your plain old machine (HTTP transport on $(port))
#  see also: run-docker
run-native:
	uv run uvicorn eco_mcp_app.http_app:app --reload --port $(port) --host 0.0.0.0

## run project inside of a docker container
#  see also: run-native
run-docker:
	docker run --expose $(port) -p $(port):$(port) -it --rm $(name):latest

## install deps via uv
sync:
	uv sync

## end-to-end smoke test the MCP server via stdio
smoke:
	(printf '%s\n' \
	  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{"extensions":{"io.modelcontextprotocol/ui":{"mimeTypes":["text/html;profile=mcp-app"]}}},"clientInfo":{"name":"claude-ai","version":"0.1.0"}}}' \
	  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
	  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
	  '{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"ui://eco/status.html"}}' \
	  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_eco_server_status","arguments":{}}}' \
	  '{"jsonrpc":"2.0","id":5,"method":"resources/read","params":{"uri":"ui://eco/economy.html"}}' \
	  '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"get_eco_economy","arguments":{}}}'; sleep 8) | uv run python -m eco_mcp_app

## wire eco-mcp-app into Claude Desktop's claude_desktop_config.json
install-desktop:
	python scripts/install-desktop-config.py

## serve static/harness.html, the local Claude-Desktop-mimicking iframe host
#  vars: harness_port (default 8765)
harness:
	@echo "Harness: http://localhost:$(or $(harness_port),8765)/static/harness.html"
	python3 -m http.server $(or $(harness_port),8765)

## run pytest
test:
	uv run pytest

## lint + format check (no mutations)
ruff:
	uv run ruff check src
	uv run ruff format --check src

## apply ruff fixes and formatting in place
fmt:
	uv run ruff check --fix src
	uv run ruff format src

## run all pre-commit hooks against every file
precommit:
	uv run pre-commit run --all-files

## run the MCP server over HTTP on $(port)
#  vars: http_port (default $(port))
http:
	uv run uvicorn eco_mcp_app.http_app:app --reload --host 0.0.0.0 --port $(or $(http_port),$(port))
