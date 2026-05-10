"""Unit tests for the `get_eco_climate` tool.

Covers:
- ``fetch_climate`` dataset fan-out, candidate-name fallthrough, and
  graceful degradation when the admin token is absent.
- ``compute_climate_payload`` math: % change, status classification, real-
  world Earth CO2 anchor.
- Polluter attribution via the action-exporter CSV stream.
- The MCP tool wiring end-to-end (call_tool returns markdown + JSON +
  the iframe fragment via ``_meta.ui.fragment``).
- The ``get_eco_map`` pollution overlay pulls ``Layers/Pollution.gif`` and
  exposes it as ``pollutionDataUri``.
"""

from __future__ import annotations

import json

import httpx
import mcp.types as mt
import pytest
import respx

from eco_mcp_app import climate as climate_mod
from eco_mcp_app import map as eco_map
from eco_mcp_app import server as eco_server
from eco_mcp_app.climate import (
    CO2_DATASET_CANDIDATES,
    EARTH_CO2_HISTORY,
    POLLUTION_ACTION_TYPES,
    POLLUTION_DATASET_CANDIDATES,
    SEA_LEVEL_DATASET_CANDIDATES,
    ClimateSnapshot,
    _earth_year_for_ppm,
    _parse_layer_pct,
    _percent_change,
    compute_climate_payload,
    fetch_climate,
)
from eco_mcp_app.server import (
    DEFAULT_ECO_INFO_URL,
    UI_META,
    build_server,
)

_DEFAULT_BASE = DEFAULT_ECO_INFO_URL.rsplit("/info", 1)[0]
_DATASET_URL = f"{_DEFAULT_BASE}/datasets/get"
_WORLDLAYERS_URL = f"{_DEFAULT_BASE}/api/v1/worldlayers/layers"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with a clean cache + a known admin token."""
    eco_server._info_cache.clear()
    eco_server._economy_cache.clear()
    eco_server._admin_token_cache.clear()
    climate_mod._clear_cache()
    monkeypatch.setenv("ECO_ADMIN_TOKEN", "test-token")


def _info(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Description": "Eco via Sirens",
        "Category": "Test",
        "DaysRunning": 4,
        "TimeSinceStart": 4 * 3600,
        "EconomyDesc": "100 trades, 0 contracts",
        "TotalCulture": 50.0,
    }
    base.update(overrides)
    return base


def _series(values: list[float]) -> list[dict[str, float]]:
    return [{"Time": float(i * 3600), "Value": float(v)} for i, v in enumerate(values)]


def _route_datasets(values: dict[str, list[float]]) -> None:
    """Mock /datasets/get with a per-dataset value table.

    Datasets not in `values` return an empty list (mirrors the live
    behavior for unknown / disabled stat names).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("dataset", "")
        if name in values:
            return httpx.Response(200, json=_series(values[name]))
        return httpx.Response(200, json=[])

    respx.get(_DATASET_URL).mock(side_effect=handler)


def _route_worldlayers_empty() -> None:
    respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))


def _route_actions_empty() -> None:
    """Stub every pollution action endpoint with an empty CSV (just header)."""
    for action in POLLUTION_ACTION_TYPES:
        url = f"{_DEFAULT_BASE}/api/v1/exporter/actions?actionName={action}"
        respx.get(url).mock(return_value=httpx.Response(200, text="Time,Citizen,Count\n"))


# ---------------------------------------------------------------------------
# fetch_climate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_picks_first_nonempty_candidate() -> None:
    """Of the 4 CO2 candidates, only ``AverageCO2PPM`` returns data."""
    _route_datasets({"AverageCO2PPM": [400, 410, 420]})
    _route_worldlayers_empty()
    _route_actions_empty()

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.co2_dataset_name == "AverageCO2PPM"
    assert [v for _, v in snap.co2_series] == [400.0, 410.0, 420.0]
    # Sea level + ground pollution are not in the values map → series stay empty.
    assert snap.sea_level_series == []
    assert snap.pollution_series == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_skips_admin_path_without_token() -> None:
    """No token → admin endpoints are never hit, only worldlayers is fetched."""
    ds_route = respx.get(_DATASET_URL).mock(return_value=httpx.Response(200, json=[]))
    wl_route = respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token=None,
        default_admin_base=_DEFAULT_BASE,
    )

    assert snap.admin_ok is False
    assert ds_route.call_count == 0
    assert wl_route.called
    assert snap.co2_series == [] and snap.sea_level_series == []


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_aggregates_polluter_attribution() -> None:
    _route_datasets({"CO2PPM": [400, 405]})
    _route_worldlayers_empty()
    # `PollutionAction` returns two rows; the rest 404.
    csv_body = (
        "Time,Citizen,Count,WorldObjectItem\n"
        "0,alice,5,GeneratorItem\n"
        "1,bob,3,GeneratorItem\n"
        "2,alice,2,SmelterItem\n"
    )
    base_actions = f"{_DEFAULT_BASE}/api/v1/exporter/actions"
    respx.get(f"{base_actions}?actionName=PollutionAction").mock(
        return_value=httpx.Response(200, text=csv_body)
    )
    for action in POLLUTION_ACTION_TYPES:
        if action == "PollutionAction":
            continue
        respx.get(f"{base_actions}?actionName={action}").mock(
            return_value=httpx.Response(404)
        )

    snap = await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )

    # alice: 5 + 2 = 7, bob: 3.
    assert snap.top_polluter_citizens[0] == ("alice", 7.0)
    assert snap.top_polluter_citizens[1] == ("bob", 3.0)
    # Stations: GeneratorItem 5+3=8, SmelterItem 2.
    assert snap.top_polluter_stations[0] == ("GeneratorItem", 8.0)
    assert snap.pollution_actions_total == 3
    assert "PollutionAction" in snap.pollution_action_types_seen
    # 404 is benign — the warnings list only carries non-404 problems.
    assert all("404" not in w for w in snap.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_climate_caches_within_ttl() -> None:
    """Repeat calls within the TTL hit the cache, not the network."""
    _route_datasets({"CO2PPM": [400]})
    wl = respx.get(_WORLDLAYERS_URL).mock(return_value=httpx.Response(200, json=[]))
    _route_actions_empty()

    await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    await fetch_climate(
        None,
        info=_info(),
        days_elapsed=4,
        admin_token="test-token",
        default_admin_base=_DEFAULT_BASE,
    )
    # Worldlayers hit only once — second call short-circuited by the TTLCache.
    assert wl.call_count == 1


# ---------------------------------------------------------------------------
# compute_climate_payload
# ---------------------------------------------------------------------------


def _snap(**overrides: object) -> ClimateSnapshot:
    base = ClimateSnapshot(
        fetched_at_iso="2026-05-10T00:00:00+00:00",
        source_base_url=_DEFAULT_BASE,
        info={"Description": "Eco", "_sourceUrl": DEFAULT_ECO_INFO_URL},
        days_elapsed=4,
        admin_ok=True,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_payload_status_critical_at_500_ppm() -> None:
    snap = _snap(co2_series=[(0.0, 480.0), (1.0, 510.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "critical"
    # Real-world anchor still returns the most recent year, flagged as
    # "beyond present-day Earth".
    assert p["earth_match"]["year"] == EARTH_CO2_HISTORY[-1][0]


def test_payload_status_warming_above_350_ppm() -> None:
    snap = _snap(co2_series=[(0.0, 360.0), (1.0, 390.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "warming"


def test_payload_status_stable_below_threshold() -> None:
    snap = _snap(co2_series=[(0.0, 280.0), (1.0, 290.0)])
    p = compute_climate_payload(snap)
    assert p["status"] == "stable"


def test_payload_status_unknown_without_co2_series() -> None:
    p = compute_climate_payload(_snap())
    assert p["status"] == "unknown"
    assert p["co2"]["current"] is None


def test_payload_co2_change_pct_computed_from_first_to_last() -> None:
    snap = _snap(co2_series=[(0.0, 400.0), (1.0, 412.0)])
    p = compute_climate_payload(snap)
    # (412 - 400) / 400 = 3.0 %
    assert p["co2"]["change_pct"] == 3.0


def test_payload_sea_level_rate_per_day() -> None:
    snap = _snap(
        sea_level_series=[(0.0, 100.0), (1.0, 100.4)],
        days_elapsed=4,
    )
    p = compute_climate_payload(snap)
    # rate = (100.4 - 100) / 4 days = 0.1
    assert p["sea_level"]["rate_per_day"] == 0.1


def test_payload_pollution_falls_back_to_worldlayers_when_series_empty() -> None:
    snap = _snap(pollution_layer_summary="7%")
    p = compute_climate_payload(snap)
    assert p["pollution"]["current"] == 7.0
    assert p["pollution"]["source"] == "worldlayers"


def test_payload_pollution_prefers_live_series_over_layer() -> None:
    snap = _snap(
        pollution_series=[(0.0, 12.0), (1.0, 14.0)],
        pollution_layer_summary="7%",
        pollution_dataset_name="GroundPollution",
    )
    p = compute_climate_payload(snap)
    assert p["pollution"]["current"] == 14.0
    assert p["pollution"]["source"] == "GroundPollution"


def test_payload_attribution_block_when_no_data() -> None:
    p = compute_climate_payload(_snap())
    assert p["attribution"]["has_data"] is False
    assert p["attribution"]["top_citizens"] == []


def test_payload_attribution_top_5_only() -> None:
    snap = _snap(
        top_polluter_citizens=[(f"player{i}", 10.0 - i) for i in range(8)],
    )
    p = compute_climate_payload(snap)
    assert len(p["attribution"]["top_citizens"]) == 5
    assert p["attribution"]["top_citizens"][0]["name"] == "player0"


def test_payload_admin_unavailable_narrative() -> None:
    p = compute_climate_payload(_snap(admin_ok=False))
    assert "Admin token unavailable" in p["narrative"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_percent_change_handles_zero_baseline() -> None:
    assert _percent_change(0.0, 100.0) is None
    assert _percent_change(100.0, 110.0) == 10.0


def test_earth_year_for_ppm_returns_closest() -> None:
    # 339 ppm matches 1980 exactly.
    match = _earth_year_for_ppm(339.0)
    assert match is not None and match["year"] == 1980


def test_earth_year_for_ppm_flags_beyond_present() -> None:
    match = _earth_year_for_ppm(500.0)
    assert match is not None
    assert "beyond present-day Earth" in match["note"]


def test_earth_year_for_ppm_returns_none_far_below_baseline() -> None:
    assert _earth_year_for_ppm(50.0) is None


def test_parse_layer_pct() -> None:
    assert _parse_layer_pct("4%") == 4.0
    assert _parse_layer_pct("  12.5 %  ") == 12.5
    assert _parse_layer_pct(None) is None
    assert _parse_layer_pct("not a number") is None


def test_dataset_candidate_constants_are_nonempty() -> None:
    """Defensive: ensure we still ship some candidate names if a refactor
    accidentally empties the tuples."""
    assert CO2_DATASET_CANDIDATES
    assert SEA_LEVEL_DATASET_CANDIDATES
    assert POLLUTION_DATASET_CANDIDATES


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_includes_get_eco_climate() -> None:
    mcp = build_server()
    handler = mcp.request_handlers[mt.ListToolsRequest]
    result = await handler(mt.ListToolsRequest(method="tools/list"))
    names = {tool.name for tool in result.root.tools}
    assert "get_eco_climate" in names


@pytest.mark.asyncio
@respx.mock
async def test_call_get_eco_climate_returns_iframe_fragment() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(return_value=httpx.Response(200, json=_info()))
    _route_datasets(
        {
            "CO2PPM": [400, 405, 410, 415],
            "SeaLevel": [100.0, 100.05, 100.1, 100.15],
            "GroundPollution": [3, 4, 5, 6],
        }
    )
    _route_worldlayers_empty()
    _route_actions_empty()

    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_eco_climate", arguments={}),
    )
    result = await handler(req)
    blocks = result.root.content
    assert len(blocks) == 2
    assert isinstance(blocks[0], mt.TextContent)
    assert isinstance(blocks[1], mt.TextContent)

    # Markdown mentions the headline.
    md = blocks[0].text
    assert "climate" in md.lower()
    assert "ppm" in md.lower()

    # JSON is parseable; carries the snapshot dataset names.
    snapshot = json.loads(blocks[1].text)
    assert snapshot["co2DatasetName"] == "CO2PPM"
    assert snapshot["seaLevelDatasetName"] == "SeaLevel"
    assert snapshot["pollutionDatasetName"] == "GroundPollution"

    # MCP Apps fragment is on _meta and contains card markup.
    meta = result.root.meta
    assert meta is not None
    assert meta["ui"]["resourceUri"] == UI_META["ui"]["resourceUri"]
    assert "Climate" in meta["ui"]["fragment"]


@pytest.mark.asyncio
@respx.mock
async def test_call_get_eco_climate_handles_info_failure() -> None:
    respx.get(DEFAULT_ECO_INFO_URL).mock(side_effect=httpx.ConnectError("refused"))
    mcp = build_server()
    handler = mcp.request_handlers[mt.CallToolRequest]
    req = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="get_eco_climate", arguments={}),
    )
    result = await handler(req)
    assert result.root.isError is True
    meta = result.root.meta
    assert meta is not None
    assert isinstance(meta["ui"]["fragment"], str)


# ---------------------------------------------------------------------------
# Map pollution-overlay extension
# ---------------------------------------------------------------------------


_TINY_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c000000000100010000020144003b"
)


@pytest.mark.asyncio
@respx.mock
async def test_map_bundle_includes_pollution_overlay_when_present() -> None:
    base = "http://eco.coilysiren.me:3001"
    respx.get(f"{base}/api/v1/map/dimension").mock(
        return_value=httpx.Response(200, json={"x": 720, "y": 200, "z": 720})
    )
    respx.get(f"{base}/api/v1/map/property").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{base}/Layers/WorldPreview.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )
    respx.get(f"{base}/Layers/Pollution.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )

    bundle = await eco_map.fetch_map_bundle()
    assert bundle["pollution_gif"] == _TINY_GIF
    payload = eco_map.build_map_payload(bundle)
    assert payload["pollutionDataUri"] is not None
    assert payload["pollutionDataUri"].startswith("data:image/gif;base64,")


@pytest.mark.asyncio
@respx.mock
async def test_map_bundle_omits_pollution_overlay_when_404() -> None:
    """Pollution.gif 404 is normal — the map renders fine without it."""
    base = "http://eco.coilysiren.me:3001"
    respx.get(f"{base}/api/v1/map/dimension").mock(
        return_value=httpx.Response(200, json={"x": 720, "y": 200, "z": 720})
    )
    respx.get(f"{base}/api/v1/map/property").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{base}/Layers/WorldPreview.gif").mock(
        return_value=httpx.Response(200, content=_TINY_GIF)
    )
    respx.get(f"{base}/Layers/Pollution.gif").mock(return_value=httpx.Response(404))

    bundle = await eco_map.fetch_map_bundle()
    assert bundle["pollution_gif"] is None
    payload = eco_map.build_map_payload(bundle)
    assert payload["pollutionDataUri"] is None
