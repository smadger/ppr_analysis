"""Download and parse the Property Price Register zip, then keep Louth/Drogheda sales."""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from ppr_analysis import config
from ppr_analysis.parse import parse_irish_date, parse_price, parse_yes_no
from ppr_analysis.warehouse import connect, replace_table

LOGGER = logging.getLogger(__name__)

PPR_COLUMNS = [
    "ppr_id",
    "sale_date",
    "address",
    "eircode",
    "county",
    "price",
    "not_full_market_price",
    "vat_exclusive",
    "description",
    "size_band",
]


def make_ppr_id(sale_date: date, address: str, price: float) -> str:
    key = f"{sale_date.isoformat()}|{address.strip().upper()}|{price:.2f}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _norm_header(name: str) -> str:
    return (
        str(name)
        .replace("€", "eur")
        .replace("\x80", "eur")
        .replace("(", " ")
        .replace(")", " ")
        .lower()
        .strip()
    )


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for col in df.columns:
        header = _norm_header(col)
        if "date of sale" in header or header.startswith("date"):
            mapping[col] = "sale_date_raw"
        elif header == "address":
            mapping[col] = "address"
        elif "eircode" in header:
            mapping[col] = "eircode"
        elif "postal" in header:
            mapping[col] = "postal_code"
        elif header == "county":
            mapping[col] = "county"
        elif "not full market" in header:
            mapping[col] = "not_full_market_price_raw"
        elif "vat" in header:
            mapping[col] = "vat_exclusive_raw"
        elif "description of property" in header or header == "description":
            mapping[col] = "description"
        elif "size" in header:
            mapping[col] = "size_band"
        elif "price" in header:
            mapping[col] = "price_raw"
    return df.rename(columns=mapping)


def parse_ppr_csv(source: Path | io.BytesIO | io.StringIO) -> pd.DataFrame:
    df = pd.read_csv(source, encoding="cp1252", low_memory=False)
    df = _rename_columns(df)
    if "eircode" not in df.columns:
        df["eircode"] = pd.NA
    if "postal_code" in df.columns:
        df["eircode"] = df["eircode"].fillna(df["postal_code"])
    required = {"sale_date_raw", "address", "county", "price_raw"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"PPR CSV missing columns: {sorted(missing)}")
    return df


def is_drogheda_louth(county: str, address: str) -> bool:
    if str(county).strip().lower() != "louth":
        return False
    text = str(address).lower()
    if "drogheda" in text:
        return True
    return any(estate in text for estate in config.DROGHEDA_ESTATES)


def filter_ppr_rows(
    df: pd.DataFrame,
    *,
    since: date | None = None,
    until: date | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    for record in df.to_dict(orient="records"):
        county = str(record.get("county") or "")
        address = str(record.get("address") or "")
        if not is_drogheda_louth(county, address):
            continue
        sale_date = parse_irish_date(record.get("sale_date_raw"))
        price = parse_price(record.get("price_raw"))
        if sale_date is None or price is None:
            continue
        if since and sale_date < since:
            continue
        if until and sale_date > until:
            continue
        eircode = record.get("eircode")
        eircode_text = None if pd.isna(eircode) else str(eircode).strip() or None
        size_band = record.get("size_band")
        description = record.get("description")
        rows.append(
            {
                "ppr_id": make_ppr_id(sale_date, address, price),
                "sale_date": sale_date.isoformat(),
                "address": address.strip(),
                "eircode": eircode_text,
                "county": "Louth",
                "price": price,
                "not_full_market_price": int(parse_yes_no(record.get("not_full_market_price_raw"))),
                "vat_exclusive": int(parse_yes_no(record.get("vat_exclusive_raw"))),
                "description": None if pd.isna(description) else str(description),
                "size_band": None if pd.isna(size_band) else str(size_band),
            }
        )
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=PPR_COLUMNS)


def read_ppr_zip(zip_bytes: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("PPR zip does not contain a CSV file")
        with archive.open(csv_names[0]) as handle:
            return parse_ppr_csv(io.BytesIO(handle.read()))


def download_ppr_zip(client: httpx.Client, url: str = config.PPR_ZIP_URL) -> bytes:
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    except httpx.TransportError as exc:
        LOGGER.warning("PPR download failed with default TLS (%s); retrying without verification", exc)
        with httpx.Client(
            headers={"User-Agent": config.USER_AGENT},
            timeout=120.0,
            follow_redirects=True,
            verify=False,
        ) as insecure:
            response = insecure.get(url)
            response.raise_for_status()
            return response.content


def ingest_ppr(
    db_path: Path,
    *,
    zip_path: Path | None = None,
    csv_path: Path | None = None,
    url: str = config.PPR_ZIP_URL,
    since: date | None = None,
    until: date | None = None,
    client: httpx.Client | None = None,
) -> int:
    if csv_path is not None:
        parsed = parse_ppr_csv(csv_path)
    elif zip_path is not None:
        parsed = read_ppr_zip(zip_path.read_bytes())
    else:
        http = client or httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=120.0, follow_redirects=True)
        try:
            parsed = read_ppr_zip(download_ppr_zip(http, url))
        finally:
            if client is None:
                http.close()
    filtered = filter_ppr_rows(parsed, since=since, until=until)
    conn = connect(db_path)
    try:
        count = replace_table(conn, "ppr_sales", filtered.to_dict(orient="records"), PPR_COLUMNS)
    finally:
        conn.close()
    LOGGER.info("Loaded %s Drogheda/Louth PPR sales into %s", count, db_path)
    return count
