"""Normalize messy Irish addresses and score PPR-to-Daft matches."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz, process

from ppr_analysis import config
from ppr_analysis.daft import DAFT_COLUMNS, CachedFetcher, fallback_search_terms, search_sold_by_terms
from ppr_analysis.warehouse import connect, replace_table

LOGGER = logging.getLogger(__name__)

MATCH_COLUMNS = ["ppr_id", "listing_id", "match_status", "match_score", "daft_url"]

ABBREVIATIONS = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "apt": "apartment",
    "apts": "apartment",
    "hse": "house",
    "gr": "grove",
    "dr": "drive",
    "pk": "park",
    "ln": "lane",
    "cres": "crescent",
    "ct": "court",
    "cl": "close",
    "sq": "square",
}

NOISE_TOKENS = {
    "co",
    "county",
    "louth",
    "ireland",
    "eire",
    "the",
    "of",
}


def _expand_token(token: str) -> str:
    return ABBREVIATIONS.get(token, token)


def normalize_address(address: str) -> str:
    text = address.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[./#,;:()]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = [_expand_token(tok) for tok in text.split()]
    tokens = [tok for tok in tokens if tok and tok not in NOISE_TOKENS]
    # collapse duplicated town names after expansion
    collapsed: list[str] = []
    for tok in tokens:
        if collapsed and tok == collapsed[-1]:
            continue
        collapsed.append(tok)
    return " ".join(collapsed)


def extract_unit_id(address: str) -> str | None:
    text = normalize_address(address)
    apt = re.search(r"\b(?:apartment|flat|unit)\s+([0-9]+[a-z]?)\b", text)
    if apt:
        return apt.group(1)
    leading = re.match(r"^(\d+[a-z]?)\b", text)
    if leading:
        return leading.group(1)
    numbered = re.search(r"\b(\d+[a-z]?)\b", text)
    return numbered.group(1) if numbered else None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def prices_close(ppr_price: float | None, daft_price: float | None) -> bool:
    if ppr_price is None or daft_price is None:
        return False
    delta = abs(ppr_price - daft_price)
    return delta <= config.PRICE_ABS_TOLERANCE or delta <= config.PRICE_REL_TOLERANCE * ppr_price


def dates_close(ppr_date: str | None, daft_date: str | None) -> bool:
    left = _parse_iso_date(ppr_date)
    right = _parse_iso_date(daft_date)
    if left is None or right is None:
        return False
    return abs((left - right).days) <= config.DATE_WINDOW_DAYS


def classify_match(
    *,
    addr_score: float,
    same_unit: bool,
    price_close: bool,
    date_close: bool,
) -> str:
    if same_unit and addr_score >= config.EXACT_FUZZ and (price_close or date_close):
        return "exact"
    if addr_score >= config.HIGH_FUZZ and (same_unit or price_close):
        return "high"
    if addr_score >= config.REVIEW_FUZZ or (same_unit and addr_score >= 70):
        return "review"
    return "unmatched"


def score_pair(ppr: dict, daft: dict) -> tuple[str, float]:
    ppr_norm = normalize_address(ppr["address"])
    daft_norm = normalize_address(daft["address"])
    addr_score = float(fuzz.token_set_ratio(ppr_norm, daft_norm))
    ppr_unit = extract_unit_id(ppr["address"])
    daft_unit = extract_unit_id(daft["address"])
    same_unit = bool(ppr_unit and daft_unit and ppr_unit == daft_unit)
    if same_unit:
        addr_score = min(100.0, addr_score + 5)
    close_price = prices_close(ppr.get("price"), daft.get("sold_price"))
    close_date = dates_close(ppr.get("sale_date"), daft.get("sold_date"))
    status = classify_match(
        addr_score=addr_score,
        same_unit=same_unit,
        price_close=close_price,
        date_close=close_date,
    )
    return status, addr_score


def match_sales(ppr_rows: list[dict], daft_rows: list[dict]) -> list[dict]:
    if not ppr_rows:
        return []
    if not daft_rows:
        return [
            {
                "ppr_id": row["ppr_id"],
                "listing_id": None,
                "match_status": "unmatched",
                "match_score": 0.0,
                "daft_url": None,
            }
            for row in ppr_rows
        ]

    daft_by_id = {row["listing_id"]: row for row in daft_rows}
    choices = {row["listing_id"]: normalize_address(row["address"]) for row in daft_rows}
    used: set[str] = set()
    results: list[dict] = []

    scored: list[tuple[float, dict, list[tuple[str, float]]]] = []
    for ppr in ppr_rows:
        ppr_norm = normalize_address(ppr["address"])
        ppr_unit = extract_unit_id(ppr["address"])
        subset = choices
        if ppr_unit:
            same_unit = {
                lid: addr
                for lid, addr in choices.items()
                if extract_unit_id(daft_by_id[lid]["address"]) == ppr_unit
            }
            if same_unit:
                subset = same_unit
        extracted = process.extract(
            ppr_norm,
            subset,
            scorer=fuzz.token_set_ratio,
            limit=8,
        )
        candidates = [(item[2], float(item[1])) for item in extracted]
        best_score = candidates[0][1] if candidates else 0.0
        scored.append((best_score, ppr, candidates))

    scored.sort(key=lambda item: item[0], reverse=True)
    for _best, ppr, candidates in scored:
        chosen_status = "unmatched"
        chosen_score = 0.0
        chosen_listing = None
        for listing_id, _fuzzy in candidates:
            daft = daft_by_id[listing_id]
            status, score = score_pair(ppr, daft)
            if status == "unmatched":
                continue
            if listing_id in used and status in {"exact", "high"}:
                continue
            chosen_status = status
            chosen_score = score
            chosen_listing = daft
            break
        if chosen_listing and chosen_status in {"exact", "high"}:
            used.add(chosen_listing["listing_id"])
        results.append(
            {
                "ppr_id": ppr["ppr_id"],
                "listing_id": None if chosen_listing is None else chosen_listing["listing_id"],
                "match_status": chosen_status,
                "match_score": round(chosen_score, 2),
                "daft_url": None if chosen_listing is None else chosen_listing.get("url"),
            }
        )
    return results


def run_matcher(db_path: Path) -> int:
    conn = connect(db_path)
    try:
        ppr_rows = [dict(row) for row in conn.execute("SELECT * FROM ppr_sales")]
        daft_rows = [dict(row) for row in conn.execute("SELECT * FROM daft_listings")]
        matches = match_sales(ppr_rows, daft_rows)
        count = replace_table(conn, "matches", matches, MATCH_COLUMNS)
    finally:
        conn.close()
    LOGGER.info("Wrote %s match rows", count)
    return count


def run_fallback_search(db_path: Path, fetcher: CachedFetcher, location: str = config.DEFAULT_LOCATION) -> int:
    """Second pass: Daft terms search for unmatched PPR rows with house number + eircode."""
    conn = fetcher.conn
    unmatched = conn.execute(
        """
        SELECT p.* FROM ppr_sales p
        LEFT JOIN matches m ON p.ppr_id = m.ppr_id
        WHERE m.match_status IS NULL OR m.match_status IN ('unmatched', 'review')
        """
    ).fetchall()
    existing = {row["listing_id"] for row in conn.execute("SELECT listing_id FROM daft_listings")}
    new_rows: list[dict] = []
    for ppr in unmatched:
        terms = fallback_search_terms(ppr["address"], ppr["eircode"])
        if not terms:
            continue
        for listing in search_sold_by_terms(fetcher, terms, location=location):
            if listing["listing_id"] not in existing:
                new_rows.append(listing)
                existing.add(listing["listing_id"])
    if new_rows:
        placeholders = ", ".join("?" * len(DAFT_COLUMNS))
        conn.executemany(
            f"INSERT INTO daft_listings ({', '.join(DAFT_COLUMNS)}) VALUES ({placeholders})",
            [tuple(row.get(col) for col in DAFT_COLUMNS) for row in new_rows],
        )
        conn.commit()
    return len(new_rows)
