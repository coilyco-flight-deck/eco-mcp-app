# Agent instructions

See `../AGENTS.md` for workspace conventions. This file covers what's specific.

---

## Project layout

- `src/eco_mcp_app/server.py` - transport-agnostic MCP server. One tool: `get_eco_server_status` (host, host:port, or full URL).
- `src/eco_mcp_app/__main__.py` - stdio entry for Claude Desktop.
- `src/eco_mcp_app/http_app.py` - Starlette ASGI wrapping the MCP server via `StreamableHTTPSessionManager` (stateless). Routes: `/`, `/healthz`, `/mcp/`.
- `src/eco_mcp_app/ui/eco.html` - iframe for MCP Apps hosts; hand-rolled handshake, no bundler. Eco Steam banner inlined as data URI (Claude Desktop CSP blocks external origins, `claude-ai-mcp#40`).
- `scripts/install-desktop-config.py` - registers server in Claude Desktop's config.
- `static/harness.html` - browser MCP Apps host simulator. Wired into `.claude/launch.json` as `eco-harness` preview.
- `Makefile` - `smoke`, `http`, `harness`, `ruff`, `fmt`, `precommit`, `install-desktop`, `test`, deploy targets. Coily wraps each.
- `Dockerfile` / `config.yml` / `deploy/main.yml` / `.github/workflows/build-and-publish.yml` - deploy rig, cloned from `coilysiren/backend`.
- `investigation/` - chronological post-mortem of the debugging session that produced this repo. Read before questioning weird-looking decisions.

## Dev loop

- `uv sync --group dev` - runtime + dev deps.
- `pre-commit install` (once) - ruff + mypy on every commit.
- `coily smoke` - stdio test: initialize → list tools → read resource → call tool.
- `coily http` - HTTP transport locally. Endpoint: `POST /mcp/`.
- `coily harness` - dev harness at `http://localhost:8765/static/harness.html`.
- `coily ruff` / `coily fmt` - lint/format.
- `coily build-docker` / `coily deploy` - build/push + k3s rollout.

After each commit to `main`, run tests. Pass = `git push` immediately. Fail = fix first.

## Sibling Eco repos

Read directly rather than asking.

- `backend` (public) - canonical deploy template (k3s + GHCR + Tailscale + cert-manager). Source of this repo's Dockerfile/Makefile/deploy.
- `eco-cycle-prep` (public) - per-cycle setup (worldgen, Discord, mod sync). Same coily pattern.
- `eco-mods` (private) - third-party mods + configs. C#.
- `eco-mods-public` (public) - own C# mods (BunWulf family + others).
- `eco-configs` (private) - server config diffs.
- `infrastructure` (public) - k3s + coily + external-secrets + Traefik.

## Key references

- MCP Apps spec (2026-01-26): https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx
- Eco `/info`: live at `http://eco.coilysiren.me:3001/info`.
- Upstream issues: `claude-ai-mcp#71` (dual `_meta.ui.resourceUri`), `#61` (handshake), `#40` (CSP), `#69` (size-changed vs documentElement).

## Adversarial testing

To harden this service, run gauntlet against `https://eco-mcp.coilysiren.me/` using `~/projects/coilysiren/gauntlet/docs/hardening-prompt-template.md`. Worked example at the bottom is the filled-in version - replay as-is.

## Post-push follow-up

- **Cadence**: 720s.
- **Verify CI**: `coily gh run list --repo coilysiren/eco-mcp-app --limit 1`. Re-schedule once at +300s if in progress.
- **Verify rollout**: `coily kubectl --context=kai-server -n coilysiren-eco-mcp-app rollout status deployment/coilysiren-eco-mcp-app-app --timeout=2m`.
- **Skip** for docs-only pushes.

## See also

- [README.md](README.md) - human-facing intro.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [.coily/coily.yaml](.coily/coily.yaml) - allowlisted commands.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilysiren/agentic-os/issues/59).
