"""Parse Irish register strings: euro prices, dates, yes/no flags."""

from __future__ import annotations

import re
from datetime import date, datetime

_PRICE_RE = re.compile(r"[^\d.]")


def parse_price(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    cleaned = _PRICE_RE.sub("", text.replace(",", ""))
    if not cleaned:
        return None
    return float(cleaned)


def parse_irish_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_yes_no(value: object) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "1"}


def parse_count(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else None


def parse_floor_area_m2(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).replace("²", "2").replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*m2", text, re.I)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None
