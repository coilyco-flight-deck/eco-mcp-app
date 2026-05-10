"""Climate / atmosphere visibility — CO2 ppm, sea level, ground pollution.

Players who don't run polluting machines can't see the global atmospheric
state until it bites them (sea-level inundation, eco-collapse). This module
surfaces that data through MCP so any player can query the trend before
they're affected.

Three independent slices, each tolerant to absence — empty data is a valid
state, not an error:

1. **Time-series via ``/datasets/get``** — CO2 ppm, sea-level height, average
   ground pollution. Eco's stat catalog has shifted across versions and mods
   can register their own, so we try a list of candidate dataset names and
   use whichever returns data. Same fan-out + 200/500-tolerant pattern as
   ``server.fetch_economy``.

2. **Polluter attribution via ``/api/v1/exporter/actions``** — same stream-
   parse approach as ``crafting.py``. Tries several action-name candidates
   so the card stays useful regardless of which actions a given Eco version
   emits. Aggregates emitted-pollution count by citizen and station.

3. **Worldlayers ``Summary`` text** — for a headline ground-pollution
   percentage when no ``/datasets`` series exists. Same endpoint that
   ``ecoregion.py`` already hits.

Real-world Earth CO2 anchor: bundled NOAA Mauna Loa annual averages so the
card can say "412 ppm — Earth crossed that in 2019." Public-domain values
trimmed to ~10 inflection points so the bundle stays small.
"""

from __future__ import annotations

import asyncio
import csv
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from cachetools import TTLCache

# Candidate dataset names. We try each in order and use the first that
# returns at least one point. An empty result simply means the server
# doesn't expose that name — not an error.
#
# Live catalog discovery on Eco 0.13.0.3 (cycle 13) confirmed the
# canonical names listed first; older Eco builds used different forms
# (`CO2PPM`, `AverageCO2PPM`) so both are kept for backward compat.
CO2_DATASET_CANDIDATES: tuple[str, ...] = (
    "TotalCO2",
    "LifetimeCO2FromPollution",
    "LifetimeCO2FromAnimals",
    "LifetimeCO2FromPlants",
    "CO2PPM",
    "AverageCO2PPM",
    "AtmosphereCO2",
    "CO2",
)
SEA_LEVEL_DATASET_CANDIDATES: tuple[str, ...] = (
    "SeaLevel",
    "SeaLevelHeight",
    "OceanLevel",
)
POLLUTION_DATASET_CANDIDATES: tuple[str, ...] = (
    "TotalGroundPollution",
    "GroundPollution",
    "AveragePollution",
    "Pollution",
)

# Action names for polluter attribution. `PolluteAir` is the canonical
# event-stat on Eco 0.13.0.3 ("Air pollution was released (triggered every
# 30 sec of operation)"). Older candidate names are kept for backward
# compatibility with modded / older servers.
POLLUTION_ACTION_TYPES: tuple[str, ...] = (
    "PolluteAir",
    "PollutionAction",
    "EmitPollutionAction",
    "EmitCO2Action",
    "BurnFuelAction",
)

# Real-world Mauna Loa annual mean CO2, ppm. NOAA, public domain, 2024 update:
# https://gml.noaa.gov/ccgg/trends/. Trimmed to inflection points so the
# anchor is meaningful without bloating the module.
EARTH_CO2_HISTORY: tuple[tuple[int, float], ...] = (
    (1750, 277.0),
    (1850, 285.0),
    (1900, 296.0),
    (1958, 315.0),
    (1980, 339.0),
    (1990, 354.0),
    (2000, 369.0),
    (2010, 389.0),
    (2015, 401.0),
    (2020, 414.0),
    (2024, 422.0),
)

# Pre-industrial baseline for the warming-status heuristic. Anything above
# 350 ppm is "warming"; >500 ppm tips into "critical" because Eco's default
# Sea Level Rise mod starts triggering above that threshold.
_PREINDUSTRIAL_PPM = 280.0
_WARMING_PPM = 350.0
_CRITICAL_PPM = 500.0

# Per-process cache. Climate moves slowly (one tick every game hour) so a 60s
# TTL is plenty and prevents the iframe-on-refresh case from hammering the
# admin endpoint.
_CACHE_TTL_S = float(os.environ.get("ECO_CLIMATE_CACHE_TTL", "60"))
_climate_cache: TTLCache[str, "ClimateSnapshot"] = TTLCache(maxsize=64, ttl=_CACHE_TTL_S)

# Action exporter row cap — same defensive bound as crafting.py uses.
_MAX_ROWS_PER_ACTION = int(os.environ.get("ECO_CLIMATE_MAX_ROWS", "200000"))


@dataclass
class ClimateSnapshot:
    """Raw fetch result. JSON-serializable via :meth:`to_dict`."""

    fetched_at_iso: str
    source_base_url: str
    info: dict[str, Any]
    days_elapsed: int
    admin_ok: bool
    co2_series: list[tuple[float, float]] = field(default_factory=list)
    sea_level_series: list[tuple[float, float]] = field(default_factory=list)
    pollution_series: list[tuple[float, float]] = field(default_factory=list)
    co2_dataset_name: str | None = None
    sea_level_dataset_name: str | None = None
    pollution_dataset_name: str | None = None
    # Worldlayers fallback for ground-pollution headline (e.g. "4%").
    pollution_layer_summary: str | None = None
    # Polluter attribution. Sorted descending by emission count.
    top_polluter_citizens: list[tuple[str, float]] = field(default_factory=list)
    top_polluter_stations: list[tuple[str, float]] = field(default_factory=list)
    pollution_actions_total: int = 0
    pollution_action_types_seen: list[str] = field(default_factory=list)
    # Climate-related dataset names exposed by /datasets/flatlist on this
    # server. Surfaced on the card so an empty-data path is debuggable
    # without the user having to grep server logs.
    available_climate_datasets: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetchedAtISO": self.fetched_at_iso,
            "sourceBaseUrl": self.source_base_url,
            "daysElapsed": self.days_elapsed,
            "adminOk": self.admin_ok,
            "co2Series": [[t, v] for t, v in self.co2_series],
            "seaLevelSeries": [[t, v] for t, v in self.sea_level_series],
            "pollutionSeries": [[t, v] for t, v in self.pollution_series],
            "co2DatasetName": self.co2_dataset_name,
            "seaLevelDatasetName": self.sea_level_dataset_name,
            "pollutionDatasetName": self.pollution_dataset_name,
            "pollutionLayerSummary": self.pollution_layer_summary,
            "topPolluterCitizens": [[n, c] for n, c in self.top_polluter_citizens],
            "topPolluterStations": [[n, c] for n, c in self.top_polluter_stations],
            "pollutionActionsTotal": self.pollution_actions_total,
            "pollutionActionTypesSeen": list(self.pollution_action_types_seen),
            "availableClimateDatasets": list(self.available_climate_datasets),
            "warnings": list(self.warnings),
        }


def _admin_base(server: str | None, default_base: str) -> str:
    """Resolve a user-supplied server hint to an admin base URL.

    Same accepted shapes as the rest of the toolset: bare host, host:port,
    full URL, or ``None`` to use the configured default.
    """
    if not server:
        return default_base.rstrip("/")
    s = server.strip()
    if not s:
        return default_base.rstrip("/")
    if "://" not in s:
        s = f"http://{s}"
    parsed = urlparse(s)
    host = parsed.hostname or ""
    port = parsed.port or 3001
    return f"{parsed.scheme or 'http'}://{host}:{port}".rstrip("/")


async def _fetch_dataset(
    client: httpx.AsyncClient,
    base: str,
    name: str,
    day_end: int,
    headers: dict[str, str],
) -> list[tuple[float, float]]:
    """GET ``/datasets/get`` for one series. Returns ``[]`` on any non-200.

    Mirrors ``server._fetch_dataset`` — duplicated rather than imported to
    keep this module free of a server.py dependency (server.py imports here
    via the call_tool path).
    """
    try:
        r = await client.get(
            f"{base}/datasets/get",
            params={"dataset": name, "dayStart": 0, "dayEnd": max(day_end, 1)},
            headers=headers,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    out: list[tuple[float, float]] = []
    if isinstance(data, list):
        for pt in data:
            parsed = _parse_dataset_point(pt)
            if parsed is not None:
                out.append(parsed)
    return out


# Time-key candidates ordered by how common each form is in the wild:
# - ``Time`` is the event-stat / older economy-stat convention.
# - ``_id`` is the ContinuousValue convention on Eco 0.13.0.3 (per the
#   stat catalog's ``TimeKey`` metadata).
_DATASET_TIME_KEYS: tuple[str, ...] = ("Time", "time", "_id", "Id", "T")


def _parse_dataset_point(pt: Any) -> tuple[float, float] | None:
    """Coerce one ``/datasets/get`` point to ``(time, value)``.

    Tolerates several shapes seen across Eco versions:

    - ``{"Time": t, "Value": v}`` — legacy economy stats, event stats.
    - ``{"_id": t, "Value": v}`` — ContinuousValue stats on 0.13.0.3.
    - ``{"_id": t, "<ShortName>": v}`` — same, but the value lives under
      the stat's short-name key (e.g. ``"tl"`` for ``TotalCO2``). We
      identify it as "the only numeric field that isn't the time key".
    - ``[t, v]`` — flat tuple pair, occasionally returned by mod stats.

    Returns ``None`` if neither time nor value can be recovered — the
    caller drops the point.
    """
    if isinstance(pt, list | tuple) and len(pt) >= 2:
        try:
            return float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            return None
    if not isinstance(pt, dict):
        return None
    time_key: str | None = None
    t_raw: Any = None
    for k in _DATASET_TIME_KEYS:
        if k in pt:
            time_key = k
            t_raw = pt[k]
            break
    if time_key is None:
        return None
    v_raw: Any = pt.get("Value", pt.get("value"))
    if v_raw is None:
        # Fall back to "the lone numeric field that isn't the time key" —
        # handles ContinuousValue points where the value lives under the
        # stat's ShortName (e.g. {"_id": 123, "tl": 414.2}).
        for k, val in pt.items():
            if k == time_key:
                continue
            if isinstance(val, int | float) and not isinstance(val, bool):
                v_raw = val
                break
    if v_raw is None:
        return None
    try:
        return float(t_raw), float(v_raw)
    except (TypeError, ValueError):
        return None


async def _fetch_first_nonempty(
    client: httpx.AsyncClient,
    base: str,
    candidates: tuple[str, ...],
    day_end: int,
    headers: dict[str, str],
    *,
    flatlist: list[str] | None = None,
    discovery_keywords: tuple[str, ...] = (),
) -> tuple[str | None, list[tuple[float, float]]]:
    """Try each candidate dataset name; return the first that has data.

    Probes are issued sequentially because we want to *stop* on the first hit
    rather than fan out. The catalog is small (3-4 names) and most servers
    will hit on the first probe, so the sequential cost is minimal.

    Fallback: if no explicit candidate matches and ``flatlist`` is given,
    scan it for any name containing one of the ``discovery_keywords`` and
    try that. Stat names drift across Eco versions (and mods can register
    their own), so the catalog-driven fallback is what keeps the card
    populated when our hard-coded candidates miss.
    """
    for name in candidates:
        pts = await _fetch_dataset(client, base, name, day_end, headers)
        if pts:
            return name, pts
    if flatlist and discovery_keywords:
        seen = {c.lower() for c in candidates}
        for name in flatlist:
            lower = str(name).lower()
            if lower in seen:
                continue
            if any(kw in lower for kw in discovery_keywords):
                pts = await _fetch_dataset(client, base, name, day_end, headers)
                if pts:
                    return name, pts
    return None, []


async def _fetch_dataset_flatlist(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> list[str]:
    """GET ``/datasets/flatlist`` — the catalog of all dataset stat names.

    Eco's stat catalog can shift across versions (and mods register their
    own names), so the explicit candidate lists in this module can miss.
    The flatlist is the source of truth — we use it to discover climate
    datasets when our hard-coded candidates don't match.

    Response shape varies by Eco version:
    - 0.13.0.3 returns ``list[dict]`` where each dict has rich metadata
      (``Name``, ``DisplayName``, ``Unit``, ``Tags``, ``StatType``, ...).
      We extract ``Name`` — that's what ``/datasets/get?dataset=...`` uses.
    - Older / modded builds may return ``list[str]`` directly.
    Both shapes are normalized to a plain list of canonical names.

    Returns ``[]`` on any non-200, missing endpoint, or unexpected shape.
    """
    try:
        r = await client.get(f"{base}/datasets/flatlist", headers=headers)
        if r.status_code != 200:
            return []
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return []
    # Some Eco builds wrap the list under a key.
    if isinstance(data, dict):
        for key in ("datasets", "Datasets", "values", "Values"):
            v = data.get(key)
            if isinstance(v, list):
                data = v
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for entry in data:
        if isinstance(entry, str):
            if entry:
                out.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("Name") or entry.get("name")
            if name:
                out.append(str(name))
    return out


# Substring keywords for the flatlist-based fallback. Lowercase, matched
# against the lowercased dataset name. Kept narrow so we don't accidentally
# pick up unrelated stats (e.g. "co2" alone instead of "carbon" because
# "carbonate" would also match the latter).
CO2_DISCOVERY_KEYWORDS: tuple[str, ...] = ("co2", "ppm", "carbondioxide")
SEA_LEVEL_DISCOVERY_KEYWORDS: tuple[str, ...] = ("sealevel", "sea level", "ocean level")
POLLUTION_DISCOVERY_KEYWORDS: tuple[str, ...] = (
    "groundpollution",
    "airpollution",
    "pollution",
)


async def _fetch_pollution_layer_summary(
    client: httpx.AsyncClient, base: str
) -> str | None:
    """Pull the ``Pollution`` layer's ``Summary`` (e.g. ``"4%"``) from worldlayers.

    The worldlayers endpoint is public — no admin token needed — which is
    why we use it as the fallback when the admin /datasets path is locked
    down. Returns ``None`` on any structural surprise.
    """
    try:
        r = await client.get(f"{base}/api/v1/worldlayers/layers")
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    for cat in data:
        if not isinstance(cat, dict):
            continue
        # Layer name varies: "Pollution", "GroundPollution", or a category
        # whose `List` contains a layer with one of those names.
        if cat.get("Category") in ("Pollution", "Atmosphere"):
            for entry in cat.get("List") or []:
                summary = entry.get("Summary")
                if summary:
                    return str(summary)
        for entry in cat.get("List") or []:
            name = (entry.get("LayerName") or "").lower()
            if "pollut" in name or "co2" in name:
                summary = entry.get("Summary")
                if summary:
                    return str(summary)
    return None


async def _stream_csv_rows(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> AsyncIterator[list[str]]:
    """Yield CSV rows from a streaming response. Header row is yielded too."""
    async with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        pending: list[str] = []
        async for line in resp.aiter_lines():
            pending.append(line)
            if len(pending) >= 256:
                for row in csv.reader(pending):
                    yield row
                pending.clear()
        if pending:
            for row in csv.reader(pending):
                yield row


async def _aggregate_pollution_action(
    client: httpx.AsyncClient,
    base: str,
    action_name: str,
    headers: dict[str, str],
    by_citizen: dict[str, float],
    by_station: dict[str, float],
) -> tuple[int, str | None]:
    """Stream one action CSV and fold pollution emissions into the totals.

    Returns ``(rows_consumed, warning_or_none)``. The warning string is set
    on a non-2xx so the caller can surface it on the rendered card without
    treating it as fatal.
    """
    url = f"{base}/api/v1/exporter/actions?actionName={action_name}"
    rows_consumed = 0
    try:
        header: list[str] | None = None
        col: dict[str, int] = {}
        async for row in _stream_csv_rows(client, url, headers):
            if header is None:
                header = row
                col = {name: i for i, name in enumerate(header)}
                continue
            if rows_consumed >= _MAX_ROWS_PER_ACTION:
                break
            count_s = _pick(row, col, "Count", "Amount", "Value")
            try:
                count = float(count_s) if count_s is not None else 1.0
            except ValueError:
                count = 1.0
            citizen = _pick(row, col, "Citizen", "Player", "User") or ""
            station = (
                _pick(row, col, "WorldObjectItem", "Station", "Source", "Object", "ToolUsed")
                or "(unknown)"
            )
            if citizen:
                by_citizen[citizen] = by_citizen.get(citizen, 0.0) + count
            if station:
                by_station[station] = by_station.get(station, 0.0) + count
            rows_consumed += 1
        return rows_consumed, None
    except httpx.HTTPStatusError as e:
        return 0, f"{action_name}: HTTP {e.response.status_code}"
    except httpx.HTTPError as e:
        return 0, f"{action_name}: {type(e).__name__}"


def _pick(row: list[str], col: dict[str, int], *keys: str) -> str | None:
    """Read the first non-empty value among ``keys`` from a CSV row.

    The exporter's column order shifts between Eco versions, so we key off
    the header instead of fixed positions — same approach as ``crafting``.
    """
    for k in keys:
        if k in col and col[k] < len(row):
            v = row[col[k]].strip()
            if v:
                return v
    return None


async def fetch_climate(
    server: str | None,
    *,
    info: dict[str, Any],
    days_elapsed: int,
    admin_token: str | None,
    default_admin_base: str,
) -> ClimateSnapshot:
    """Fetch all climate slices in parallel where possible.

    The caller owns ``info`` (already retrieved via ``fetch_eco_info``) — we
    don't fetch /info ourselves because most call sites already have it. This
    keeps the per-call request count down and matches how
    ``ecoregion.gather_ecoregion_payload`` is shaped.
    """
    base = _admin_base(server, default_admin_base)

    cache_key = base
    cached = _climate_cache.get(cache_key)
    if cached is not None:
        return cached

    snapshot = ClimateSnapshot(
        fetched_at_iso=_now_iso(),
        source_base_url=base,
        info=dict(info or {}),
        days_elapsed=max(1, int(days_elapsed or 1)),
        admin_ok=bool(admin_token),
    )

    headers = {"X-API-Key": admin_token} if admin_token else {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Public worldlayers fetch happens regardless of the admin token —
        # this is the fallback that gives non-admins *something* to look at.
        layer_task = asyncio.create_task(_fetch_pollution_layer_summary(client, base))

        if admin_token:
            # Pull the dataset catalog up front so the per-series probes can
            # fall back to keyword discovery when our hard-coded candidates
            # miss (Eco renames stats across versions; mods register their
            # own).
            flatlist = await _fetch_dataset_flatlist(client, base, headers)

            co2_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    CO2_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=CO2_DISCOVERY_KEYWORDS,
                )
            )
            sea_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    SEA_LEVEL_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=SEA_LEVEL_DISCOVERY_KEYWORDS,
                )
            )
            poll_task = asyncio.create_task(
                _fetch_first_nonempty(
                    client,
                    base,
                    POLLUTION_DATASET_CANDIDATES,
                    snapshot.days_elapsed,
                    headers,
                    flatlist=flatlist,
                    discovery_keywords=POLLUTION_DISCOVERY_KEYWORDS,
                )
            )

            (snapshot.co2_dataset_name, snapshot.co2_series) = await co2_task
            (snapshot.sea_level_dataset_name, snapshot.sea_level_series) = await sea_task
            (snapshot.pollution_dataset_name, snapshot.pollution_series) = await poll_task

            # Surface every climate-related dataset name the catalog
            # exposes so the empty-state card can hint at why we found
            # nothing, and so future debugging doesn't need server logs.
            climate_kws = (
                CO2_DISCOVERY_KEYWORDS
                + SEA_LEVEL_DISCOVERY_KEYWORDS
                + POLLUTION_DISCOVERY_KEYWORDS
            )
            snapshot.available_climate_datasets = sorted(
                {
                    name
                    for name in flatlist
                    if any(kw in name.lower() for kw in climate_kws)
                }
            )

            # Polluter attribution. Sequential per-action because the exporter
            # CSVs can be many MB late-cycle; we stream-parse each one in turn
            # to avoid spiking memory by N parallel streams.
            by_citizen: dict[str, float] = {}
            by_station: dict[str, float] = {}
            actions_seen: list[str] = []
            for action in POLLUTION_ACTION_TYPES:
                rows, warn = await _aggregate_pollution_action(
                    client, base, action, headers, by_citizen, by_station
                )
                if rows:
                    actions_seen.append(action)
                    snapshot.pollution_actions_total += rows
                if warn and "404" not in warn and "405" not in warn:
                    # 404/405 just means "this server doesn't emit that action
                    # type" — silent. Other failures are worth surfacing.
                    snapshot.warnings.append(warn)
            snapshot.pollution_action_types_seen = actions_seen
            snapshot.top_polluter_citizens = sorted(
                by_citizen.items(), key=lambda kv: kv[1], reverse=True
            )[:10]
            snapshot.top_polluter_stations = sorted(
                by_station.items(), key=lambda kv: kv[1], reverse=True
            )[:10]

        snapshot.pollution_layer_summary = await layer_task

    _climate_cache[cache_key] = snapshot
    return snapshot


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Payload computation — server.py renders the card from this shape.
# ---------------------------------------------------------------------------


def _series_last(pts: list[tuple[float, float]]) -> float:
    return float(pts[-1][1]) if pts else 0.0


def _series_first(pts: list[tuple[float, float]]) -> float:
    return float(pts[0][1]) if pts else 0.0


def _percent_change(first: float, last: float) -> float | None:
    """Percent change from first to last. ``None`` if there's no baseline."""
    if first == 0:
        return None
    return round(((last - first) / first) * 100.0, 2)


def _earth_year_for_ppm(ppm: float) -> dict[str, Any] | None:
    """Find the closest historical Earth year for a given ppm.

    Returns ``{"year": int, "ppm": float, "note": str}`` or ``None`` if the
    in-game value is below the pre-industrial baseline (rare, but possible
    on heavily-modded peaceful servers).
    """
    if ppm < EARTH_CO2_HISTORY[0][1] - 5.0:
        return None
    # Above the most-recent recorded year — flag as "beyond present-day Earth".
    latest_year, latest_ppm = EARTH_CO2_HISTORY[-1]
    if ppm >= latest_ppm:
        return {
            "year": latest_year,
            "ppm": latest_ppm,
            "note": f"beyond present-day Earth ({latest_year}: {latest_ppm:.0f} ppm)",
        }
    best_year, best_ppm = EARTH_CO2_HISTORY[0]
    best_diff = abs(ppm - best_ppm)
    for year, hist_ppm in EARTH_CO2_HISTORY:
        diff = abs(ppm - hist_ppm)
        if diff < best_diff:
            best_year, best_ppm, best_diff = year, hist_ppm, diff
    return {
        "year": best_year,
        "ppm": best_ppm,
        "note": f"Earth crossed this in ~{best_year}",
    }


def _classify_status(ppm: float, sea_change_pct: float | None) -> str:
    """Three-tier health classification matching the economy card's vocabulary.

    Picked to align with Eco's default Sea Level Rise mod thresholds: 350 ppm
    is the "noticeable warming" line, 500 ppm is where the mod typically
    starts inundating coastal property.
    """
    if ppm >= _CRITICAL_PPM or (sea_change_pct is not None and sea_change_pct >= 5.0):
        return "critical"
    if ppm >= _WARMING_PPM or (sea_change_pct is not None and sea_change_pct >= 1.0):
        return "warming"
    return "stable"


def _parse_layer_pct(summary: str | None) -> float | None:
    """``"4%"`` → ``4.0``. Tolerates whitespace + missing percent sign."""
    if not summary:
        return None
    s = str(summary).strip().rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


def compute_climate_payload(snapshot: ClimateSnapshot) -> dict[str, Any]:
    """Shape the snapshot for the Jinja card. Pure — no IO, no SVG.

    Sparkline SVGs are attached by the server.py render path so we don't pull
    the SVG renderer into this module (and circular-import on it).
    """
    info = snapshot.info or {}
    days_elapsed = snapshot.days_elapsed

    co2_last = _series_last(snapshot.co2_series)
    co2_first = _series_first(snapshot.co2_series)
    co2_change_pct = _percent_change(co2_first, co2_last) if snapshot.co2_series else None

    sea_last = _series_last(snapshot.sea_level_series)
    sea_first = _series_first(snapshot.sea_level_series)
    sea_change_pct = (
        _percent_change(sea_first, sea_last) if snapshot.sea_level_series else None
    )
    # Linear extrapolation: at the current rate, when does sea level cross +1
    # unit? The mod reads sea level in arbitrary units, so we project the
    # number of cycle-days, not meters — interpretation is the player's job.
    sea_rate_per_day = (
        (sea_last - sea_first) / max(days_elapsed, 1) if snapshot.sea_level_series else 0.0
    )

    pollution_last = _series_last(snapshot.pollution_series)
    pollution_layer_pct = _parse_layer_pct(snapshot.pollution_layer_summary)
    # Prefer the live series; fall back to the worldlayers summary number.
    pollution_value: float | None
    pollution_source: str
    if snapshot.pollution_series:
        pollution_value = pollution_last
        pollution_source = snapshot.pollution_dataset_name or "series"
    elif pollution_layer_pct is not None:
        pollution_value = pollution_layer_pct
        pollution_source = "worldlayers"
    else:
        pollution_value = None
        pollution_source = "none"

    earth_match = _earth_year_for_ppm(co2_last) if snapshot.co2_series else None
    status = _classify_status(co2_last, sea_change_pct) if snapshot.co2_series else "unknown"

    has_attribution = bool(
        snapshot.top_polluter_citizens or snapshot.top_polluter_stations
    )

    narrative = _narrative(
        status=status,
        co2_ppm=co2_last,
        co2_change_pct=co2_change_pct,
        sea_change_pct=sea_change_pct,
        admin_ok=snapshot.admin_ok,
        any_series=bool(snapshot.co2_series or snapshot.sea_level_series),
        available_climate_datasets=snapshot.available_climate_datasets,
    )

    return {
        "view": "eco_climate",
        "server": {
            "description": info.get("Description", ""),
            "category": info.get("Category"),
            "sourceUrl": info.get("_sourceUrl"),
        },
        "days_elapsed": days_elapsed,
        "admin_ok": snapshot.admin_ok,
        "status": status,
        "narrative": narrative,
        "co2": {
            "current": round(co2_last, 1) if snapshot.co2_series else None,
            "change_pct": co2_change_pct,
            "dataset_name": snapshot.co2_dataset_name,
            "series": [list(p) for p in snapshot.co2_series],
        },
        "sea_level": {
            "current": round(sea_last, 3) if snapshot.sea_level_series else None,
            "change_pct": sea_change_pct,
            "rate_per_day": round(sea_rate_per_day, 4),
            "dataset_name": snapshot.sea_level_dataset_name,
            "series": [list(p) for p in snapshot.sea_level_series],
        },
        "pollution": {
            "current": (
                round(pollution_value, 2) if pollution_value is not None else None
            ),
            "source": pollution_source,
            "dataset_name": snapshot.pollution_dataset_name,
            "layer_summary": snapshot.pollution_layer_summary,
            "series": [list(p) for p in snapshot.pollution_series],
        },
        "earth_match": earth_match,
        "attribution": {
            "has_data": has_attribution,
            "actions_total": snapshot.pollution_actions_total,
            "action_types_seen": list(snapshot.pollution_action_types_seen),
            "top_citizens": [
                {"name": n, "count": c} for n, c in snapshot.top_polluter_citizens[:5]
            ],
            "top_stations": [
                {"name": n, "count": c} for n, c in snapshot.top_polluter_stations[:5]
            ],
        },
        "warnings": list(snapshot.warnings),
        "available_climate_datasets": list(snapshot.available_climate_datasets),
        "fetched_at_iso": snapshot.fetched_at_iso,
        "source_base_url": snapshot.source_base_url,
    }


def _narrative(
    *,
    status: str,
    co2_ppm: float,
    co2_change_pct: float | None,
    sea_change_pct: float | None,
    admin_ok: bool,
    any_series: bool,
    available_climate_datasets: list[str] | None = None,
) -> str:
    """One-sentence summary for the card header."""
    if not admin_ok and not any_series:
        return (
            "Admin token unavailable — showing public worldlayers data only. "
            "CO2 and sea-level series require ECO_ADMIN_TOKEN."
        )
    if not any_series:
        # When the catalog *did* expose climate datasets but none had values,
        # call that out specifically — it's a different failure mode from
        # "this server's stat catalog has no climate stats at all" and the
        # latter usually means a mod is missing.
        if available_climate_datasets:
            sample = ", ".join(f"`{n}`" for n in available_climate_datasets[:5])
            return (
                f"Server exposes climate datasets ({sample}) but none have "
                "recorded values yet — early in the cycle, or the polluting "
                "machinery hasn't run."
            )
        return (
            "No atmospheric series exposed by this server's stat catalog. "
            "Likely a missing climate / pollution mod, or a vanilla ruleset."
        )

    bits: list[str] = []
    if co2_ppm:
        bits.append(f"CO2 at {co2_ppm:.0f} ppm")
    if co2_change_pct is not None and abs(co2_change_pct) >= 0.1:
        sign = "+" if co2_change_pct > 0 else ""
        bits.append(f"{sign}{co2_change_pct:.1f}% since cycle start")
    if sea_change_pct is not None and abs(sea_change_pct) >= 0.05:
        sign = "+" if sea_change_pct > 0 else ""
        bits.append(f"sea level {sign}{sea_change_pct:.2f}%")

    head = f"Climate is {status}"
    if not bits:
        return f"{head} — no measurable change yet."
    return f"{head} — {', '.join(bits)}."


# ---------------------------------------------------------------------------
# Pollution heatmap GIF — used by the optional get_eco_map overlay.
# ---------------------------------------------------------------------------


async def fetch_pollution_overlay_gif(
    base_url: str, *, client: httpx.AsyncClient | None = None
) -> bytes | None:
    """GET ``/Layers/Pollution.gif``. Returns ``None`` on any non-200.

    Eco serves world-layer rasters under ``/Layers/<Name>.gif`` alongside the
    main ``WorldPreview.gif``. The pollution layer isn't always exposed (the
    server config can disable individual rasters), so a 404 is normal — the
    map card just renders without the overlay.
    """
    base = base_url.rstrip("/")
    url = f"{base}/Layers/Pollution.gif"
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=5.0)
    try:
        try:
            r = await http.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        return r.content
    finally:
        if owns_client:
            await http.aclose()


def _clear_cache() -> None:
    """Test hook — drop the per-process climate cache."""
    _climate_cache.clear()


__all__ = [
    "CO2_DATASET_CANDIDATES",
    "CO2_DISCOVERY_KEYWORDS",
    "EARTH_CO2_HISTORY",
    "POLLUTION_ACTION_TYPES",
    "POLLUTION_DATASET_CANDIDATES",
    "POLLUTION_DISCOVERY_KEYWORDS",
    "SEA_LEVEL_DATASET_CANDIDATES",
    "SEA_LEVEL_DISCOVERY_KEYWORDS",
    "ClimateSnapshot",
    "compute_climate_payload",
    "fetch_climate",
    "fetch_pollution_overlay_gif",
]
