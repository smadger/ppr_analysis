"""Command-line pipeline: ingest PPR, ingest Daft, match, export."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from ppr_analysis import config
from ppr_analysis.daft import CachedFetcher, ingest_daft
from ppr_analysis.export import export_outputs, export_web, summarise, load_augmented
from ppr_analysis.geocode import geocode_sales
from ppr_analysis.matcher import run_fallback_search, run_matcher
from ppr_analysis.ppr import ingest_ppr
from ppr_analysis.warehouse import connect


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppr-analysis",
        description="Drogheda PPR warehouse enriched with Daft sold-listing attributes (personal research).",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Local data directory")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: DATA_DIR/warehouse.sqlite)")
    sub = parser.add_subparsers(dest="command", required=True)

    ppr = sub.add_parser("ingest-ppr", help="Download/parse PPR-ALL.zip and keep Louth + Drogheda sales")
    ppr.add_argument("--zip", dest="zip_path", type=Path, default=None)
    ppr.add_argument("--csv", dest="csv_path", type=Path, default=None)
    ppr.add_argument("--url", default=config.PPR_ZIP_URL)
    ppr.add_argument("--since", type=_parse_iso_date, default=None)
    ppr.add_argument("--years", type=int, default=None, help="Keep sales from the last N years")

    daft = sub.add_parser("ingest-daft", help="Paginate Daft sold-properties/drogheda-louth with cache")
    daft.add_argument("--location", default=config.DEFAULT_LOCATION)
    daft.add_argument("--max-pages", type=int, default=None)
    daft.add_argument("--delay", type=float, default=config.DEFAULT_PAGE_DELAY_SECONDS)
    daft.add_argument("--refresh", action="store_true")

    match = sub.add_parser("match", help="Fuzzy-match PPR addresses to cached Daft listings")
    match.add_argument("--fallback-search", action="store_true", help="Search unmatched rows that have house number + eircode")
    match.add_argument("--location", default=config.DEFAULT_LOCATION)
    match.add_argument("--delay", type=float, default=config.DEFAULT_PAGE_DELAY_SECONDS)

    export = sub.add_parser("export", help="Write augmented CSV/Parquet and match-rate summaries")
    export.add_argument("--out", type=Path, default=None)

    geo = sub.add_parser("geocode", help="Geocode unique PPR addresses via Nominatim (cached)")
    geo.add_argument("--delay", type=float, default=config.NOMINATIM_DELAY_SECONDS)
    geo.add_argument("--refresh", action="store_true")

    web = sub.add_parser("export-web", help="Write GeoJSON + summary.json for the map site")
    web.add_argument("--out", type=Path, default=None)

    run = sub.add_parser("run", help="ingest-ppr, ingest-daft, match, export")
    run.add_argument("--zip", dest="zip_path", type=Path, default=None)
    run.add_argument("--csv", dest="csv_path", type=Path, default=None)
    run.add_argument("--since", type=_parse_iso_date, default=None)
    run.add_argument("--years", type=int, default=None)
    run.add_argument("--location", default=config.DEFAULT_LOCATION)
    run.add_argument("--max-pages", type=int, default=None)
    run.add_argument("--delay", type=float, default=config.DEFAULT_PAGE_DELAY_SECONDS)
    run.add_argument("--refresh", action="store_true")
    run.add_argument("--fallback-search", action="store_true")
    run.add_argument("--out", type=Path, default=None)
    return parser


def _db_path(args: argparse.Namespace) -> Path:
    if args.db is not None:
        return args.db
    return args.data_dir / "warehouse.sqlite"


def _since(args: argparse.Namespace) -> date | None:
    if getattr(args, "since", None):
        return args.since
    years = getattr(args, "years", None)
    if years:
        return date.today() - timedelta(days=365 * years)
    return None


def _cmd_ingest_ppr(args: argparse.Namespace) -> int:
    count = ingest_ppr(
        _db_path(args),
        zip_path=args.zip_path,
        csv_path=args.csv_path,
        url=args.url,
        since=_since(args),
    )
    print(f"ingested {count} PPR sales")
    return 0


def _cmd_ingest_daft(args: argparse.Namespace) -> int:
    stats = ingest_daft(
        _db_path(args),
        location=args.location,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
        refresh=args.refresh,
    )
    print(
        "ingested {listings} Daft listings "
        "({dropped_meath} Meath dropped, {pages} pages, {network_calls} HTTP calls)".format(**stats)
    )
    return 0


def _cmd_match(args: argparse.Namespace) -> int:
    db_path = _db_path(args)
    if args.fallback_search:
        conn = connect(db_path)
        http = httpx.Client(headers={"User-Agent": config.USER_AGENT}, timeout=60.0, follow_redirects=True)
        try:
            fetcher = CachedFetcher(conn, http, delay_seconds=args.delay)
            added = run_fallback_search(db_path, fetcher, location=args.location)
            print(f"fallback search added {added} listings")
        finally:
            conn.close()
            http.close()
    count = run_matcher(db_path)
    print(f"wrote {count} match rows")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out_dir = args.out or (args.data_dir / "exports")
    paths = export_outputs(_db_path(args), out_dir)
    summary = summarise(load_augmented(_db_path(args)))
    print(f"csv: {paths['csv']}")
    print(f"parquet: {paths['parquet']}")
    print(f"summary: {paths['summary']}")
    print(
        "match rate (exact/high): "
        f"{summary['match_rate_exact_or_high']:.1%} of {summary['ppr_rows']} PPR rows"
    )
    return 0


def _cmd_geocode(args: argparse.Namespace) -> int:
    stats = geocode_sales(
        _db_path(args),
        delay_seconds=args.delay,
        refresh=args.refresh,
    )
    print(
        "geocoded {hits} in-bounds / {unique_queries} unique queries "
        "({misses} misses, {skipped} cached, {network_calls} HTTP)".format(**stats)
    )
    return 0


def _cmd_export_web(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    out_dir = args.out or (repo_root / "web" / "public" / "data")
    paths = export_web(_db_path(args), out_dir)
    print(f"geojson: {paths['geojson']}")
    print(f"summary: {paths['summary']}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    ingest_ppr(
        _db_path(args),
        zip_path=args.zip_path,
        csv_path=args.csv_path,
        since=_since(args),
    )
    ingest_daft(
        _db_path(args),
        location=args.location,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
        refresh=args.refresh,
    )
    _cmd_match(args)
    _cmd_export(args)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    handlers = {
        "ingest-ppr": _cmd_ingest_ppr,
        "ingest-daft": _cmd_ingest_daft,
        "match": _cmd_match,
        "export": _cmd_export,
        "geocode": _cmd_geocode,
        "export-web": _cmd_export_web,
        "run": _cmd_run,
    }
    return handlers[args.command](args)
