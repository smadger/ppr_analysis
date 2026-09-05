"""Join PPR sales to Daft attributes and write CSV/Parquet plus summary stats."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from ppr_analysis.geocode import jitter_point, query_key, build_query
from ppr_analysis.warehouse import connect

LOGGER = logging.getLogger(__name__)

EXPORT_COLUMNS = [
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
    "match_status",
    "match_score",
    "daft_url",
    "daft_address",
    "asking_price",
    "beds",
    "baths",
    "property_type",
    "floor_area_m2",
    "ber",
    "agent",
    "sold_price_daft",
    "sold_date_daft",
    "eur_per_m2",
    "sale_vs_asking",
]


def load_augmented(db_path: Path) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        frame = pd.read_sql_query(
            """
            SELECT
                p.ppr_id,
                p.sale_date,
                p.address,
                p.eircode,
                p.county,
                p.price,
                p.not_full_market_price,
                p.vat_exclusive,
                p.description,
                p.size_band,
                COALESCE(m.match_status, 'unmatched') AS match_status,
                m.match_score,
                m.daft_url,
                d.address AS daft_address,
                d.asking_price,
                d.beds,
                d.baths,
                d.property_type,
                d.floor_area_m2,
                d.ber,
                d.agent,
                d.sold_price AS sold_price_daft,
                d.sold_date AS sold_date_daft
            FROM ppr_sales p
            LEFT JOIN matches m ON p.ppr_id = m.ppr_id
            LEFT JOIN daft_listings d ON m.listing_id = d.listing_id
            """,
            conn,
        )
    finally:
        conn.close()
    if frame.empty:
        for col in EXPORT_COLUMNS:
            if col not in frame.columns:
                frame[col] = pd.Series(dtype="object")
        return frame
    frame["eur_per_m2"] = frame["price"] / frame["floor_area_m2"]
    frame.loc[frame["floor_area_m2"].fillna(0) <= 0, "eur_per_m2"] = pd.NA
    frame["sale_vs_asking"] = (frame["price"] / frame["asking_price"]) - 1
    frame.loc[frame["asking_price"].fillna(0) <= 0, "sale_vs_asking"] = pd.NA
    return frame[EXPORT_COLUMNS]


def market_subset(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.loc[frame["not_full_market_price"] == 0].copy()


def summarise(frame: pd.DataFrame) -> dict:
    total = len(frame)
    matched = int(frame["match_status"].isin(["exact", "high"]).sum()) if total else 0
    review = int((frame["match_status"] == "review").sum()) if total else 0
    market = market_subset(frame)
    by_type = {}
    if not market.empty and market["property_type"].notna().any():
        grouped = market.groupby(["property_type", "beds"], dropna=False)["price"].median()
        by_type = {
            f"{ptype or 'unknown'}|{'' if pd.isna(beds) else int(beds)}": float(median)
            for (ptype, beds), median in grouped.items()
        }
    eur_m2 = market["eur_per_m2"].dropna()
    vs_asking = market["sale_vs_asking"].dropna()
    return {
        "ppr_rows": total,
        "match_rate_exact_or_high": (matched / total) if total else 0.0,
        "review_share": (review / total) if total else 0.0,
        "unmatched_share": ((total - matched - review) / total) if total else 0.0,
        "coverage_note": "Daft attributes exist only for homes listed (or otherwise sourced) on Daft; unmatched PPR rows are expected.",
        "median_eur_per_m2": None if eur_m2.empty else float(eur_m2.median()),
        "median_sale_vs_asking": None if vs_asking.empty else float(vs_asking.median()),
        "median_price_by_type_and_beds": by_type,
        "market_stats_exclude_not_full_market_price": True,
    }


GEOJSON_FIELDS = [
    "ppr_id",
    "address",
    "sale_date",
    "price",
    "property_type",
    "beds",
    "baths",
    "floor_area_m2",
    "ber",
    "asking_price",
    "eur_per_m2",
    "sale_vs_asking",
    "daft_url",
    "not_full_market_price",
]


def _json_safe(value):
    if value is None or (isinstance(value, float) and value != value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (pd.Timestamp,)):
        return str(value)[:10]
    return value


def sales_to_geojson(frame: pd.DataFrame, geocodes: dict[str, tuple[float, float]]) -> dict:
    features = []
    for record in frame.to_dict(orient="records"):
        query = build_query(str(record.get("address") or ""), record.get("eircode"))
        point = geocodes.get(query_key(query))
        if point is None:
            continue
        lat, lng = jitter_point(point[0], point[1], str(record["ppr_id"]))
        properties = {field: _json_safe(record.get(field)) for field in GEOJSON_FIELDS}
        ptype = properties.get("property_type")
        if isinstance(ptype, str) and ptype.strip() in {"", "—", "-", "nan", "None"}:
            properties["property_type"] = None
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def load_geocodes(db_path: Path) -> dict[str, tuple[float, float]]:
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT query_key, lat, lng FROM geocode_cache WHERE lat IS NOT NULL AND in_bounds = 1"
        ).fetchall()
    finally:
        conn.close()
    return {row["query_key"]: (float(row["lat"]), float(row["lng"])) for row in rows}


def web_summary(frame: pd.DataFrame, mapped: int) -> dict:
    stats = summarise(frame)
    total = len(frame)
    return {
        "ppr_rows": stats["ppr_rows"],
        "mapped_rows": mapped,
        "unmapped_rows": total - mapped,
        "mapped_share": (mapped / total) if total else 0.0,
        "match_rate_exact_or_high": stats["match_rate_exact_or_high"],
        "median_price": None
        if market_subset(frame).empty
        else float(market_subset(frame)["price"].median()),
        "median_eur_per_m2": stats["median_eur_per_m2"],
        "median_sale_vs_asking": stats["median_sale_vs_asking"],
        "coverage_note": stats["coverage_note"],
    }


def export_web(db_path: Path, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = load_augmented(db_path)
    geocodes = load_geocodes(db_path)
    geojson = sales_to_geojson(frame, geocodes)
    summary = web_summary(frame, len(geojson["features"]))
    geo_path = out_dir / "sales.geojson"
    summary_path = out_dir / "summary.json"
    geo_path.write_text(json.dumps(geojson), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    LOGGER.info("Wrote %s map features to %s", len(geojson["features"]), geo_path)
    return {"geojson": geo_path, "summary": summary_path}


def export_outputs(db_path: Path, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = load_augmented(db_path)
    csv_path = out_dir / "drogheda_sales.csv"
    parquet_path = out_dir / "drogheda_sales.parquet"
    summary_path = out_dir / "summary.json"
    frame.to_csv(csv_path, index=False)
    frame.to_parquet(parquet_path, index=False)
    summary = summarise(frame)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    LOGGER.info("Wrote %s rows to %s and %s", len(frame), csv_path, parquet_path)
    return {"csv": csv_path, "parquet": parquet_path, "summary": summary_path}
