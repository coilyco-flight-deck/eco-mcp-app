# eco-mcp-app features

Baseline inventory of headline features. Use this to evaluate scope increases or decreases over time. When the surface area changes, update the relevant section here in the same commit.

## What this app is

A Model Context Protocol (MCP) server that exposes live data from Eco game servers as Claude Desktop widget cards. Doubles as a reference implementation of the MCP Apps spec in pure Python (no React, no bundler). Production deployment lives at `https://eco-mcp.coilysiren.me/mcp/`.

## MCP tools

Defined in [src/eco_mcp_app/server.py](../src/eco_mcp_app/server.py). All tools accept an optional `server` argument to target a non-default Eco server.

- **get_eco_server_status** - Live snapshot. Meteor countdown, players online, world dimensions, cycle progress, version, economy summary.
- **get_eco_economy** - Economic dashboard. Trades per day, contract completion ratio, loan default rate, wages, tax flow, volatility sparklines. Reads admin `/datasets/get`.
- **get_eco_map** - Interactive world map with property deed overlays. Translucent polygons, owner color-coding, hover labels, Deck.gl WebGL rendering.
- **get_eco_milestones** - Culture achievement tracker. Per-goal progress bars, server-wide culture stat.
- **get_eco_species** - Species profile card. Real-world taxonomy from iNaturalist or Wikipedia plus in-game population chart from admin exporter.
- **explain_eco_item** - Wikidata + Wikipedia lookup for Eco items. Images, descriptions, category-specific facts (materials, plants, animals, minerals, foods). 7-day cache.
- **get_eco_crafting_atlas** - Live crafting activity from action-log exporter. Top items produced, station utilization, per-citizen leaderboard. CSV stream-parsed.
- **fair_price** - Real-world commodity price advisor backed by FRED (copper, wheat, lumber, iron, crude oil). Percent-change at 7d/30d/90d cadences.
- **get_eco_ecoregion** - Biome classification against WWF ecoregions. Donut chart, top-3 matches, per-species boom/bust lists.
- **get_eco_government** - Civic org chart. Elected titles plus occupants, active elections with countdown, active laws (markup stripped).
- **list_public_eco_servers** - Curated roster of 6 known public servers with labels, host:port, notes.

## MCP resources

- **ui://eco/status.html** - Main iframe shell. Initial resource and Jinja2 template for per-tool card fragments.
- **ui://eco/economy.html** - Economy dashboard shell. Same transport pattern.

## Runtime surfaces

- **Stdio transport** - `python -m eco_mcp_app.__main__` for Claude Desktop. Entry: [src/eco_mcp_app/__main__.py](../src/eco_mcp_app/__main__.py).
- **HTTP transport** - MCP over Streamable-HTTP at `POST /mcp/`. Stateless session manager. Entry: [src/eco_mcp_app/http_app.py](../src/eco_mcp_app/http_app.py).
- **Health probe** - `GET /healthz`.
- **Dev preview** - `GET /preview` pre-renders Jinja2 templates for hot-reload iteration without MCP handshake.
- **Livereload WebSocket** - Debug-mode hot reload for the dev harness. [src/eco_mcp_app/livereload.py](../src/eco_mcp_app/livereload.py).

## UI rendering layer

- **Jinja2 server-side templates** at [src/eco_mcp_app/templates/](../src/eco_mcp_app/templates/). No bundler, no React.
- **Main shell** `eco.html` (~5KB). Hand-rolled MCP Apps handshake. Inlines Steam banner as data URI for CSP compliance.
- **CSS** `eco.css` (~26KB). Responsive, animated starfield, cycle ring visualization.
- **22 partial templates** for per-card fragments (status, economy, map, crafting, ecopedia, ecoregion, government, milestones, fair-price, errors).
- **Inline asset images** (Steam logo, Eco favicon) bundled with templates.

## External data sources

- **Eco public `/info`** - Real-time server state. Default `http://eco.coilysiren.me:3001/info`, override via `ECO_INFO_URL`.
- **Eco admin `/datasets/get`** - Economic time-series. Requires `ECO_ADMIN_API_KEY`.
- **Eco admin `/exporter/*`** - Action logs (crafting, harvesting, mining), species populations, property deeds. CSV stream-parsed.
- **Wikidata + Wikipedia** - Item taxonomy and images. 7-day TTL cache. [src/eco_mcp_app/wikidata.py](../src/eco_mcp_app/wikidata.py).
- **FRED** - Federal Reserve Economic Data for commodity prices. Requires `FRED_API_KEY`. [src/eco_mcp_app/fair_price.py](../src/eco_mcp_app/fair_price.py).
- **iNaturalist** - Species taxonomy and images. Wikipedia fallback. [src/eco_mcp_app/species.py](../src/eco_mcp_app/species.py).

## Bundled data assets

- **data/ecoregions.json** (~7KB) - WWF ecoregion definitions for biome matching.
- **data/ecopedia.json** (~4.8MB) - Eco item descriptions and metadata.
- **data/species_profiles.json** (~15MB) - Cached iNaturalist + Wikipedia profiles for all Eco species.
- Loaded by [src/eco_mcp_app/_preload.py](../src/eco_mcp_app/_preload.py).

## Source modules

- **server.py** - Core MCP server. Tool and resource definitions, handlers, TextMeshPro markup parsing, error rendering. ~2200 lines.
- **http_app.py** - Starlette ASGI app wrapping the MCP server. NormalizeMcpPath middleware.
- **crafting.py** - CSV stream-parser, top-items aggregation, leaderboard.
- **map.py** - Deed fetch, polygon build, Deck.gl WebGL payload.
- **ecoregion.py** - Biome classification via cosine-similarity ecoregion matching.
- **species.py** - iNaturalist + Wikipedia integration, species card renderer.
- **fair_price.py** - FRED integration, percent-change math.
- **wikidata.py** - Wikidata SPARQL queries, image fetch, TTL cache.
- **telemetry.py** - Sentry error tracking.
- **livereload.py** - Dev hot-reload WebSocket.
- **_preload.py** - Bundled data asset loading.

## Deployment

- **Docker image** - Alpine Python 3.13 + uv. Published to `ghcr.io/coilysiren/eco-mcp-app/coilysiren-eco-mcp-app:latest`. [Dockerfile](../Dockerfile).
- **Kubernetes manifest** - Namespace, Deployment, Service, Ingress, ExternalSecrets (GHCR pull-secret, FRED API key, Eco admin token from AWS SSM). [deploy/main.yml](../deploy/main.yml).
- **Tailscale + cert-manager** - Encrypted cluster access from GitHub Actions, auto TLS.
- **CI/CD** - GitHub Actions builds image, pushes to GHCR, deploys via kubectl over Tailscale. Trufflehog secret scan.
- **Public endpoint** - `https://eco-mcp.coilysiren.me/mcp/`. Local default port 4000 via `config.yml`.

## Dev tooling

- **Task runner** - pyinvoke. [tasks.py](../tasks.py).
  - `inv smoke` - Stdio end-to-end test exercising all 11 tools.
  - `inv http` - Local HTTP transport on port 4000 with hot reload.
  - `inv harness` - Browser dev harness at `localhost:8765/static/harness.html`. Mimics MCP Apps host with mocked tool-result payloads.
  - `inv test` - pytest.
  - `inv ruff` / `inv fmt` - Lint and format.
  - `inv precommit` - Pre-commit hooks.
  - `inv install-desktop` - Auto-register in Claude Desktop config.
- **Makefile** - `make build-native`, `make build-docker`, `make publish`, `make deploy`.
- **Pre-commit** - ruff (lint + format), mypy (type check).
- **Test stack** - pytest, pytest-asyncio, respx for HTTP mocking. [tests/](../tests/).

## Documentation

- **README.md** - Usage guide, config for 6 MCP clients, deploy instructions, MCP Apps gotchas (~458 lines).
- **AGENTS.md** - Agent instructions covering dev loop, sibling Eco repos, adversarial testing, post-push CI verification.
- **investigation/** - 8 chronological post-mortems from debugging sessions (bootstrap, GitHub issues, client identity, CSP sandbox).

## Scope at a glance

- 11 MCP tools
- 2 MCP resources
- 6 external data sources (3 Eco endpoints, Wikidata, FRED, iNaturalist)
- 3 bundled data assets (~20MB)
- 3 transport modes (stdio, HTTP, dev preview)
- 10+ source modules
- 1 k3s deployment with TLS, secrets, CI/CD
- 22 partial templates rendering per-card UI
