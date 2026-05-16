[![Eco by Strange Loop Games](https://cdn.cloudflare.steamstatic.com/steam/apps/382310/header.jpg)](https://store.steampowered.com/app/382310/Eco/)

<sub>Banner: Steam header for Eco by [Strange Loop Games](https://strangeloopgames.com/). Attribution use, not my artwork.</sub>

# eco-mcp-app

An inline Claude Desktop widget for any public **Eco** game server [1]. Point at the "Eco via Sirens" [2] server (default) or any other Eco server by IP/hostname. Ask Claude "what's the Eco server doing?" and you get a live card: meteor countdown, online/total, world size, laws, economy, Discord CTA, Steam link.

Also a tech demo - a minimal, hand-rolled MCP Apps implementation [3] without a bundler or React. Whole iframe is one 300-line HTML file. Useful as a reference for building an MCP App in Python rather than the TypeScript/ext-apps [4] stack.

![](https://img.shields.io/badge/python-3.13-3776ab)
![](https://img.shields.io/badge/mcp-1.14+-ff6b35)
![](https://img.shields.io/badge/MCP_Apps-spec_2026--01--26-7cb342)

[![Live card](docs/preview.png)](https://eco-mcp.coilysiren.me/preview)

<sub>Live at [eco-mcp.coilysiren.me/preview](https://eco-mcp.coilysiren.me/preview).</sub>

## How it works

`src/eco_mcp_app/server.py` exposes tools that hit Eco's public `/info` endpoint and admin exporters, redact player names, and return content blocks: markdown fallback for text-only hosts + JSON for the iframe. The tool's `_meta.ui.resourceUri` points at `ui://eco/status.html`, registered as an MCP resource.

The iframe (`src/eco_mcp_app/ui/eco.html`) is plain HTML/CSS/JS - no build step. Hand-rolls the MCP Apps handshake per spec [5]:

1. Iframe → host: `ui/initialize` (with `protocolVersion: 2026-01-26`)
2. Host → iframe: initialize result
3. Iframe → host: `ui/notifications/initialized`
4. Host → iframe: `ui/notifications/tool-result` whenever a matching tool fires

The handshake is ~30 lines. The ext-apps SDK [4] does more, but for a read-only dashboard we don't need it.

## Tools

See [docs/FEATURES.md](docs/FEATURES.md) for the full inventory. Headliner: `get_eco_server_status`. Plus tools for economy, world map, milestones, species, items, crafting, fair-price, ecoregions, government, climate. All accept an optional `server` arg (host, host:port, full URL).

## Quick start

```sh
uv sync --group dev
coily smoke      # stdio test of all tools
coily http       # HTTP transport on :4000
coily harness    # browser dev harness on :8765
```

Add to Claude Desktop:

```sh
coily install-desktop
```

## Deploy

Cloned from `coilysiren/backend` template. Dockerfile + `deploy/main.yml` (k3s) + `.github/workflows/build-and-publish.yml`. Target: `https://eco-mcp.coilysiren.me/mcp/`.

## License & credits

MIT. Eco is a trademark of **Strange Loop Games** [1]; unofficial fan tool, not affiliated.

## References

1. <https://play.eco/>
2. <https://eco.coilysiren.me/>
3. <https://github.com/modelcontextprotocol/ext-apps>
4. <https://github.com/modelcontextprotocol/ext-apps/tree/main/sdk>
5. <https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx>

## See also

- [AGENTS.md](AGENTS.md) - agent-facing operating rules.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [.coily/coily.yaml](.coily/coily.yaml) - allowlisted commands.

Cross-reference convention from [coilysiren/agentic-os#59](https://github.com/coilysiren/agentic-os/issues/59).
