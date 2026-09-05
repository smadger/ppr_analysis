"""Geocode unique sale addresses with Nominatim and cache results in SQLite."""

from __future__ import annotations

import hashlib
import logging
import math
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from ppr_analysis import config
from ppr_analysis.warehouse import connect

LOGGER = logging.getLogger(__name__)


def query_key(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()


def build_query(address: str, eircode: str | None) -> str:
    street = (address or "").strip()
    code = "" if eircode is None else str(eircode).strip()
    if code.lower() in {"", "nan", "none", "<na>"}:
        code = ""
    if code and street:
        return f"{street}, {code}, Drogheda, County Louth, Ireland"
    if code:
        return f"{code}, Drogheda, County Louth, Ireland"
    return f"{street}, Drogheda, County Louth, Ireland"


def in_drogheda_bounds(lat: float, lng: float) -> bool:
    bounds = config.DROGHEDA_BOUNDS
    return bounds["min_lat"] <= lat <= bounds["max_lat"] and bounds["min_lon"] <= lng <= bounds["max_lon"]


def jitter_point(lat: float, lng: float, seed: str, meters: float = 8.0) -> tuple[float, float]:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    dx = (digest[0] / 255.0 - 0.5) * 2
    dy = (digest[1] / 255.0 - 0.5) * 2
    dlat = (meters * dy) / 111_320
    dlng = (meters * dx) / (111_320 * max(0.2, abs(math.cos(math.radians(lat)))))
    return lat + dlat, lng + dlng


def parse_nominatim_response(payload: list[dict]) -> tuple[float, float, str] | None:
    if not payload:
        return None
    hit = payload[0]
    try:
        lat = float(hit["lat"])
        lng = float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return lat, lng, str(hit.get("display_name") or "")


NOISE_PARTS = {
    "drogheda",
    "louth",
    "co louth",
    "co. louth",
    "county louth",
    "ireland",
    "eire",
}


def address_parts(address: str) -> list[str]:
    parts = []
    for raw in address.replace(";", ",").split(","):
        piece = " ".join(raw.strip().split())
        if piece and piece.lower() not in NOISE_PARTS:
            parts.append(piece)
    return parts


def candidate_queries(address: str, eircode: str | None = None) -> list[str]:
    ordered: list[str] = []
    for query in (
        build_query(address, eircode),
        build_query(address, None),
    ):
        if query not in ordered:
            ordered.append(query)
    parts = address_parts(address)
    extras: list[str] = []
    if parts:
        extras.append(f"{parts[-1]}, Drogheda, Ireland")
    if len(parts) >= 2:
        extras.append(f"{parts[-2]}, Drogheda, Ireland")
        extras.append(f"{parts[-2]}, {parts[-1]}, Drogheda, Ireland")
    if len(parts) >= 3:
        extras.append(f"{parts[1]}, Drogheda, Ireland")
    street = parts[0] if parts else ""
    street_no_num = " ".join(tok for tok in street.split() if not tok[:1].isdigit())
    if street_no_num and street_no_num.lower() != street.lower():
        extras.append(f"{street_no_num}, Drogheda, Ireland")
    for extra in extras:
        if extra not in ordered:
            ordered.append(extra)
    return ordered


def nominatim_search(client: httpx.Client, query: str) -> list[dict]:
    response = client.get(
        config.NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "ie",
        },
        headers={"User-Agent": config.USER_AGENT, "Accept-Language": "en"},
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def search_with_fallbacks(
    client: httpx.Client,
    address: str,
    eircode: str | None,
    *,
    delay_seconds: float,
    sleeper: Callable[[float], None],
    already_requested: int,
    skip: int = 0,
    lookup_cache: dict[str, tuple[float, float, str] | None] | None = None,
) -> tuple[tuple[float, float, str] | None, int]:
    network = 0
    shared = lookup_cache if lookup_cache is not None else {}
    for query in candidate_queries(address, eircode)[skip:]:
        if query in shared:
            parsed = shared[query]
            if parsed and in_drogheda_bounds(parsed[0], parsed[1]):
                return parsed, network
            continue
        if already_requested + network and delay_seconds > 0:
            sleeper(delay_seconds)
        parsed = parse_nominatim_response(nominatim_search(client, query))
        network += 1
        shared[query] = parsed
        if parsed and in_drogheda_bounds(parsed[0], parsed[1]):
            return parsed, network
    return None, network


def unique_queries(rows: list[dict]) -> list[tuple[str, str, str, str | None]]:
    seen: dict[str, tuple[str, str, str, str | None]] = {}
    for row in rows:
        address = str(row.get("address") or "")
        eircode = row.get("eircode")
        if eircode is not None and str(eircode).strip().lower() in {"", "nan", "none", "<na>"}:
            eircode = None
        query = build_query(address, eircode)
        key = query_key(query)
        seen.setdefault(key, (key, query, address, eircode))
    return list(seen.values())


def geocode_sales(
    db_path,
    *,
    client: httpx.Client | None = None,
    delay_seconds: float = config.NOMINATIM_DELAY_SECONDS,
    refresh: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    conn = connect(db_path)
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    close_http = client is None
    sales = [dict(row) for row in conn.execute("SELECT address, eircode FROM ppr_sales")]
    pending = unique_queries(sales)
    network = 0
    hits = 0
    misses = 0
    skipped = 0
    lookup_cache: dict[str, tuple[float, float, str] | None] = {}
    try:
        for index, (key, query, address, eircode) in enumerate(pending):
            cached = None if refresh else conn.execute(
                "SELECT lat, in_bounds FROM geocode_cache WHERE query_key = ?",
                (key,),
            ).fetchone()
            if cached is not None and cached["lat"] is not None:
                skipped += 1
                if cached["in_bounds"]:
                    hits += 1
                else:
                    misses += 1
                continue
            parsed, used = search_with_fallbacks(
                http,
                address,
                eircode,
                delay_seconds=delay_seconds,
                sleeper=sleeper,
                already_requested=network,
                skip=1 if cached is not None else 0,
                lookup_cache=lookup_cache,
            )
            network += used
            fetched_at = datetime.now(timezone.utc).isoformat()
            if parsed is None:
                conn.execute(
                    """
                    INSERT INTO geocode_cache (query_key, query, lat, lng, display_name, in_bounds, fetched_at)
                    VALUES (?, ?, NULL, NULL, NULL, 0, ?)
                    ON CONFLICT(query_key) DO UPDATE SET
                        query = excluded.query,
                        lat = NULL,
                        lng = NULL,
                        display_name = NULL,
                        in_bounds = 0,
                        fetched_at = excluded.fetched_at
                    """,
                    (key, query, fetched_at),
                )
                misses += 1
            else:
                lat, lng, display_name = parsed
                conn.execute(
                    """
                    INSERT INTO geocode_cache (query_key, query, lat, lng, display_name, in_bounds, fetched_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(query_key) DO UPDATE SET
                        query = excluded.query,
                        lat = excluded.lat,
                        lng = excluded.lng,
                        display_name = excluded.display_name,
                        in_bounds = 1,
                        fetched_at = excluded.fetched_at
                    """,
                    (key, query, lat, lng, display_name, fetched_at),
                )
                hits += 1
            conn.commit()
            if network and network % 25 == 0:
                LOGGER.info("Geocoded %s network lookups (%s/%s unique queries)", network, index + 1, len(pending))
    finally:
        conn.close()
        if close_http:
            http.close()
    LOGGER.info("Geocode done: %s hits in bounds, %s misses, %s cached, %s HTTP", hits, misses, skipped, network)
    return {"hits": hits, "misses": misses, "skipped": skipped, "network_calls": network, "unique_queries": len(pending)}
