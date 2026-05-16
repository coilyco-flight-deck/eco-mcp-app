# eco-mcp-app features

Baseline inventory of headline features. Use to evaluate scope changes.

## What this app is

MCP server exposing live data from Eco game servers as Claude Desktop widget cards. Reference implementation of MCP Apps spec in pure Python (no React, no bundler). Production: `https://eco-mcp.coilysiren.me/mcp/`.

## MCP tools

Defined in [src/eco_mcp_app/server.py](../src/eco_mcp_app/server.py). All accept optional `server` arg.

- **get_eco_server_status** - Meteor countdown, players, world dims, cycle progress, version, economy summary.
- **get_eco_economy** - Trades/day, contract completion, loan defaults, wages, tax flow, volatility sparklines. Admin `/datasets/get`.
- **get_eco_map** - World map with property deeds. Translucent polygons, owner colors, Deck.gl WebGL.
- **get_eco_milestones** - Culture achievement tracker. Per-goal bars, server-wide culture.
- **get_eco_species** - Species card. iNaturalist/Wikipedia taxonomy + in-game population chart.
- **explain_eco_item** - Wikidata + Wikipedia lookup. Images, category facts. 7-day cache.
- **get_eco_crafting_atlas** - Live crafting from action-log exporter. Top items, station util, leaderboard.
- **fair_price** - Real-world commodity prices via FRED (copper, wheat, lumber, iron, crude). 7d/30d/90d.
- **get_eco_ecoregion** - WWF ecoregion classification. Donut, top-3 matches, boom/bust lists.
- **get_eco_government** - Civic org chart. Elected titles, active elections, active laws.
- **get_eco_climate** - CO2 ppm, sea-level + drift, ground pollution, NOAA Mauna Loa anchor, top polluters. Tolerant to dataset-name drift.
- **list_public_eco_servers** - 6 known public servers with labels + notes.

## MCP resources

- **ui://eco/status.html** - Main iframe shell + Jinja2 template for per-tool cards.
- **ui://eco/economy.html** - Economy dashboard shell.
- **ui://eco/climate.html** - Climate dashboard shell.

## Runtime surfaces

- **Stdio** - `python -m eco_mcp_app.__main__` for Claude Desktop.
- **HTTP** - MCP over Streamable-HTTP at `POST /mcp/`. Stateless.
- **Health probe** - `GET /healthz`.
- **Dev preview** - `GET /preview` pre-renders Jinja2 for hot-reload iteration.
- **Livereload WS** - Debug-mode hot reload.

## UI rendering

- **Jinja2 server-side** at [src/eco_mcp_app/templates/](../src/eco_mcp_app/templates/). No bundler, no React.
- **Main shell** `eco.html` (~5KB). Hand-rolled MCP Apps handshake. Steam banner data URI for CSP.
- **CSS** `eco.css` (~26KB). Responsive, animated starfield, cycle ring.
- **22 partial templates** for per-card fragments.

## External data sources

- **Eco public `/info`** - Default `http://eco.coilysiren.me:3001/info`, override `ECO_INFO_URL`.
- **Eco admin `/datasets/get`** - Economic time-series. `ECO_ADMIN_API_KEY`.
- **Eco admin `/exporter/*`** - Action logs (crafting, harvesting, mining), species, deeds. CSV stream-parsed.
- **Wikidata + Wikipedia** - Item taxonomy + images. 7-day TTL.
- **FRED** - Commodity prices. `FRED_API_KEY`.
- **iNaturalist** - Species taxonomy + images. Wikipedia fallback.

## Bundled data assets

- **data/ecoregions.json** (~7KB) - WWF ecoregion defs.
- **data/ecopedia.json** (~4.8MB) - Eco item descriptions.
- **data/species_profiles.json** (~15MB) - Cached species profiles.

## Source modules

- **server.py** - Core MCP server, tool/resource handlers, TMP markup parsing, error rendering. ~2200 lines.
- **http_app.py** - Starlette ASGI + NormalizeMcpPath middleware.
- **crafting.py** / **map.py** / **ecoregion.py** / **species.py** / **fair_price.py** / **wikidata.py** / **telemetry.py** / **livereload.py** / **_preload.py**.

## Deployment

- **Docker image** - Alpine Python 3.13 + uv. `ghcr.io/coilysiren/eco-mcp-app/coilysiren-eco-mcp-app:latest`.
- **k8s manifest** - Namespace, Deployment, Service, Ingress, ExternalSecrets (GHCR pull-secret, FRED key, Eco admin token from SSM).
- **Tailscale + cert-manager** - Encrypted cluster access from GHA, auto TLS.
- **CI/CD** - GHA builds, pushes GHCR, deploys via kubectl over Tailscale. Trufflehog secret scan.
- **Public endpoint** - `https://eco-mcp.coilysiren.me/mcp/`. Local default port 4000.

## Dev tooling

- **coily verbs** in [.coily/coily.yaml](../.coily/coily.yaml), each delegating to Make.
  - `coily smoke` - Stdio test of all 12 tools.
  - `coily http` - Local HTTP on 4000 with hot reload.
  - `coily harness` - Browser dev harness on `:8765`.
  - `coily install-desktop` - Auto-register in Claude Desktop config.
- **Pre-commit** - ruff + mypy.
- **Tests** - pytest, pytest-asyncio, respx.

## See also

- [README.md](../README.md) - human-facing intro.
- [AGENTS.md](../AGENTS.md) - agent-facing operating rules.
- [.coily/coily.yaml](../.coily/coily.yaml) - allowlisted commands.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilysiren/agentic-os/issues/59).
