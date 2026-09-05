from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ppr_analysis.cli import main
from ppr_analysis.daft import ingest_daft
from ppr_analysis.export import export_web, load_augmented, summarise
from ppr_analysis.geocode import geocode_sales
from ppr_analysis.matcher import run_matcher
from ppr_analysis.ppr import ingest_ppr
from tests.conftest import FakeClient, daft_gateway_page, ppr_csv_path
from tests.test_geocode import NominatimClient


def test_end_to_end_export(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    ingest_ppr(db_path, csv_path=ppr_csv_path(), since=date(2020, 1, 1))
    client = FakeClient(
        html_by_url={
            "from:0": daft_gateway_page(1),
            "from:20": daft_gateway_page(2),
        }
    )
    ingest_daft(db_path, client=client, sleeper=lambda _delay: None)
    run_matcher(db_path)
    frame = load_augmented(db_path)
    assert len(frame) == 3
    wheaton = frame[frame["address"].str.contains("Wheaton Hall")].iloc[0]
    assert wheaton["match_status"] in {"exact", "high"}
    assert wheaton["beds"] == 4
    assert abs(wheaton["eur_per_m2"] - (385000 / 127)) < 0.01
    kermon = frame[frame["address"].str.contains("Kermon")].iloc[0]
    assert kermon["match_status"] in {"exact", "high"}
    summary = summarise(frame)
    assert summary["ppr_rows"] == 3
    assert summary["match_rate_exact_or_high"] >= 2 / 3
    westcourt = frame.loc[frame["address"].str.contains("Westcourt")].iloc[0]
    assert int(westcourt["not_full_market_price"]) == 1
    assert westcourt["match_status"] == "unmatched"


def test_cli_ingest_ppr_and_export(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    out_dir = tmp_path / "exports"
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "--db",
                str(db_path),
                "ingest-ppr",
                "--csv",
                str(ppr_csv_path()),
                "--since",
                "2020-01-01",
            ]
        )
        == 0
    )
    ingest_daft(
        db_path,
        client=FakeClient(
            html_by_url={
                "from:0": daft_gateway_page(1),
                "from:20": daft_gateway_page(2),
            }
        ),
        sleeper=lambda _delay: None,
    )
    assert main(["--data-dir", str(tmp_path), "--db", str(db_path), "match"]) == 0
    assert main(["--data-dir", str(tmp_path), "--db", str(db_path), "export", "--out", str(out_dir)]) == 0
    assert (out_dir / "drogheda_sales.csv").exists()
    assert (out_dir / "drogheda_sales.parquet").exists()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["market_stats_exclude_not_full_market_price"] is True


def test_export_web_geojson(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    ingest_ppr(db_path, csv_path=ppr_csv_path(), since=date(2020, 1, 1))
    ingest_daft(
        db_path,
        client=FakeClient(
            html_by_url={
                "from:0": daft_gateway_page(1),
                "from:20": daft_gateway_page(2),
            }
        ),
        sleeper=lambda _delay: None,
    )
    run_matcher(db_path)
    geocode_sales(db_path, client=NominatimClient(), delay_seconds=0, sleeper=lambda _d: None)
    out_dir = tmp_path / "webdata"
    paths = export_web(db_path, out_dir)
    geojson = json.loads(paths["geojson"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    assert geojson["type"] == "FeatureCollection"
    assert summary["mapped_rows"] == len(geojson["features"])
    assert summary["mapped_rows"] >= 1
    feature = geojson["features"][0]
    props = feature["properties"]
    assert "ppr_id" in props
    assert "agent" not in props
    assert feature["geometry"]["type"] == "Point"
    lng, lat = feature["geometry"]["coordinates"]
    assert lng < 0
    assert lat > 53
