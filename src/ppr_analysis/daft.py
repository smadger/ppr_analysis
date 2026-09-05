"""Bulk ingest Daft sold-properties pages with HTTP cache and rate limits."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import httpx

from ppr_analysis import config
from ppr_analysis.parse import parse_count, parse_floor_area_m2, parse_irish_date, parse_price
from ppr_analysis.warehouse import connect, get_http_cache, replace_table, upsert_http_cache

LOGGER = logging.getLogger(__name__)

DAFT_COLUMNS = [
    "listing_id",
    "url",
    "address",
    "sold_date",
    "sold_price",
    "asking_price",
    "beds",
    "baths",
    "property_type",
    "floor_area_m2",
    "ber",
    "agent",
]

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
MEATH_COUNTY_RE = re.compile(r",\s*meath(\s*,\s*meath)?\s*$", re.I)


def sold_index_url(location: str, page: int) -> str:
    return f"{config.DAFT_SOLD_BASE}/{location}?page={page}"


def is_meath_listing(address: str, path: str = "") -> bool:
    text = str(address).strip()
    lowered = text.lower()
    if MEATH_COUNTY_RE.search(lowered):
        return True
    path_l = path.lower()
    if "-meath/" in path_l or path_l.endswith("-meath"):
        return True
    if re.search(r",\s*louth", lowered):
        return False
    return "meath" in lowered and "louth" not in lowered


def parse_next_data(html: str) -> dict:
    match = NEXT_DATA_RE.search(html)
    if not match:
        raise ValueError("Daft HTML is missing __NEXT_DATA__; page layout may have changed")
    return json.loads(match.group(1))


def flatten_listing(item: dict) -> dict | None:
    listing = item.get("listing") or item
    address = listing.get("title") or listing.get("seoTitle")
    if not address:
        return None
    path = listing.get("seoFriendlyPath") or ""
    seller = listing.get("seller") or {}
    ber = listing.get("ber") or {}
    listing_id = str(listing.get("id") or listing.get("adId") or path)
    sold_date = parse_irish_date(listing.get("soldDate"))
    return {
        "listing_id": listing_id,
        "url": urljoin("https://www.daft.ie", path) if path else None,
        "address": address,
        "sold_date": sold_date.isoformat() if sold_date else None,
        "sold_price": parse_price(listing.get("soldPrice")),
        "asking_price": parse_price(listing.get("price")),
        "beds": parse_count(listing.get("numBedrooms")),
        "baths": parse_count(listing.get("numBathrooms")),
        "property_type": listing.get("propertyType"),
        "floor_area_m2": parse_floor_area_m2(listing.get("propertySize")),
        "ber": ber.get("rating") if isinstance(ber, dict) else ber,
        "agent": seller.get("branch") or seller.get("name"),
        "path": path,
    }


def listings_from_html(html: str) -> tuple[list[dict], dict]:
    payload = parse_next_data(html)
    page_props = payload.get("props", {}).get("pageProps", {})
    raw_listings = page_props.get("listings") or []
    paging = page_props.get("paging") or {}
    flattened = [row for item in raw_listings if (row := flatten_listing(item))]
    return flattened, paging


def listings_from_body(body: str) -> tuple[list[dict], dict]:
    stripped = body.lstrip()
    if stripped.startswith("<"):
        return listings_from_html(body)
    data = json.loads(body)
    if "props" in data:
        return listings_from_html(
            f'<script id="__NEXT_DATA__" type="application/json">{body}</script>'
        )
    raw_listings = data.get("listings") or []
    paging = data.get("paging") or {}
    flattened = [row for item in raw_listings if (row := flatten_listing(item))]
    return flattened, paging


def gateway_payload(location: str, from_offset: int, page_size: int = config.DEFAULT_PAGE_SIZE) -> dict:
    shape_id = config.LOCATION_SHAPE_IDS.get(location)
    payload: dict = {
        "section": "residential-sold",
        "filters": [],
        "andFilters": [],
        "ranges": [],
        "paging": {"from": str(from_offset), "pageSize": str(page_size)},
        "terms": "",
    }
    if shape_id:
        payload["geoFilter"] = {"storedShapeIds": [shape_id], "geoSearchType": "STORED_SHAPES"}
    return payload


def drop_meath(listings: list[dict]) -> tuple[list[dict], int]:
    kept: list[dict] = []
    dropped = 0
    for row in listings:
        if is_meath_listing(row["address"], row.get("path") or row.get("url") or ""):
            dropped += 1
            continue
        kept.append({k: v for k, v in row.items() if k != "path"})
    return kept, dropped


def _retry_after_seconds(response: object, attempt: int) -> float:
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw:
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            pass
    return min(120.0, config.DEFAULT_RETRY_AFTER_SECONDS * (2 ** (attempt - 1)))


class CachedFetcher:
    def __init__(
        self,
        conn,
        client: httpx.Client,
        *,
        delay_seconds: float = config.DEFAULT_PAGE_DELAY_SECONDS,
        refresh: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = config.MAX_HTTP_RETRIES,
    ) -> None:
        self.conn = conn
        self.client = client
        self.delay_seconds = delay_seconds
        self.refresh = refresh
        self.sleeper = sleeper
        self.max_retries = max_retries
        self._network_calls = 0

    @property
    def network_calls(self) -> int:
        return self._network_calls

    def _send(self, url: str, json_body: dict | None) -> object:
        headers = {
            "User-Agent": config.USER_AGENT,
            "brand": "daft",
            "platform": "web",
        }
        if json_body is None:
            return self.client.get(url, headers=headers)
        headers["Content-Type"] = "application/json"
        return self.client.post(url, headers=headers, json=json_body)

    def get(self, url: str, *, cache_key: str | None = None, json_body: dict | None = None) -> str:
        key = cache_key or url
        if not self.refresh:
            cached = get_http_cache(self.conn, key)
            if cached is not None:
                return cached
        attempt = 0
        while True:
            if attempt == 0 and self._network_calls and self.delay_seconds > 0:
                self.sleeper(self.delay_seconds)
            response = self._send(url, json_body)
            status = getattr(response, "status_code", 200)
            if status in {429, 503, 403} and attempt < self.max_retries:
                attempt += 1
                wait = _retry_after_seconds(response, attempt)
                LOGGER.warning("Daft returned %s for %s; waiting %.0fs then retry %s/%s", status, url, wait, attempt, self.max_retries)
                self.sleeper(wait)
                continue
            response.raise_for_status()
            body = response.text
            self._network_calls += 1
            upsert_http_cache(
                self.conn,
                key,
                url,
                datetime.now(timezone.utc).isoformat(),
                body,
            )
            return body


def ingest_daft(
    db_path: Path,
    *,
    location: str = config.DEFAULT_LOCATION,
    max_pages: int | None = None,
    delay_seconds: float = config.DEFAULT_PAGE_DELAY_SECONDS,
    refresh: bool = False,
    client: httpx.Client | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    conn = connect(db_path)
    http = client or httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=60.0, follow_redirects=True)
    close_http = client is None
    fetcher = CachedFetcher(conn, http, delay_seconds=delay_seconds, refresh=refresh, sleeper=sleeper)
    kept: list[dict] = []
    dropped_meath = 0
    pages_fetched = 0
    try:
        from_offset = 0
        page_size = config.DEFAULT_PAGE_SIZE
        while True:
            if max_pages is not None and pages_fetched >= max_pages:
                break
            payload = gateway_payload(location, from_offset, page_size)
            body = fetcher.get(
                config.DAFT_GATEWAY_URL,
                cache_key=f"gateway:{location}:{from_offset}",
                json_body=payload,
            )
            listings, paging = listings_from_body(body)
            page_kept, page_dropped = drop_meath(listings)
            kept.extend(page_kept)
            dropped_meath += page_dropped
            pages_fetched += 1
            next_from = paging.get("nextFrom")
            total_pages = int(paging.get("totalPages") or pages_fetched)
            current_page = int(paging.get("currentPage") or pages_fetched)
            if next_from is None or int(next_from) <= from_offset or current_page >= total_pages:
                break
            from_offset = int(next_from)
        # de-duplicate by listing_id, last write wins
        unique = {row["listing_id"]: row for row in kept}
        count = replace_table(conn, "daft_listings", list(unique.values()), DAFT_COLUMNS)
    finally:
        conn.close()
        if close_http:
            http.close()
    LOGGER.info(
        "Loaded %s Daft listings (%s Meath dropped) from %s pages",
        count,
        dropped_meath,
        pages_fetched,
    )
    return {
        "listings": count,
        "dropped_meath": dropped_meath,
        "pages": pages_fetched,
        "network_calls": fetcher.network_calls,
    }


def fallback_search_terms(address: str, eircode: str | None) -> str | None:
    """Only search unmatched PPR rows that already have a house/apt token plus eircode."""
    if not eircode:
        return None
    if not re.search(r"\d", address):
        return None
    return f"{address.strip()} {eircode.strip()}"


def search_sold_by_terms(
    fetcher: CachedFetcher,
    terms: str,
    *,
    location: str = config.DEFAULT_LOCATION,
) -> list[dict]:
    shape_id = config.LOCATION_SHAPE_IDS.get(location)
    payload = {
        "section": "residential-sold",
        "filters": [],
        "andFilters": [],
        "ranges": [],
        "paging": {"from": "0", "pageSize": str(config.DEFAULT_PAGE_SIZE)},
        "geoFilter": {"storedShapeIds": [shape_id], "geoSearchType": "STORED_SHAPES"} if shape_id else {},
        "terms": terms,
    }
    body = fetcher.get(
        config.DAFT_GATEWAY_URL,
        cache_key=f"gateway:{location}:{terms.lower()}",
        json_body=payload,
    )
    data = json.loads(body)
    listings = data.get("listings") or data.get("props", {}).get("pageProps", {}).get("listings") or []
    flattened = [row for item in listings if (row := flatten_listing(item))]
    kept, _dropped = drop_meath(flattened)
    return kept
