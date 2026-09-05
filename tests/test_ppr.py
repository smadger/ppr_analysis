from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

from ppr_analysis.ppr import filter_ppr_rows, ingest_ppr, make_ppr_id, parse_ppr_csv
from tests.conftest import ppr_csv_path


def test_parse_and_filter_louth_drogheda(tmp_path: Path) -> None:
    parsed = parse_ppr_csv(ppr_csv_path())
    filtered = filter_ppr_rows(parsed)
    addresses = set(filtered["address"])
    assert any("Wheaton Hall" in item for item in addresses)
    assert any("Kermon House" in item for item in addresses)
    assert any("Westcourt" in item for item in addresses)
    assert not any("Dundalk" in item for item in addresses)
    assert (filtered["county"] == "Louth").all()


def test_optional_date_window_drops_old_sales() -> None:
    parsed = parse_ppr_csv(ppr_csv_path())
    filtered = filter_ppr_rows(parsed, since=date(2020, 1, 1))
    assert not any("2015" in value for value in filtered["sale_date"])
    assert any("College Rise" in item for item in filter_ppr_rows(parsed)["address"])


def test_ppr_id_is_stable() -> None:
    first = make_ppr_id(date(2024, 7, 14), "12 Barley Cove, Wheaton Hall, Drogheda, Co. Louth", 385000.0)
    second = make_ppr_id(date(2024, 7, 14), "12 Barley Cove, Wheaton Hall, Drogheda, Co. Louth", 385000.0)
    assert first == second
    assert len(first) == 64


def test_ingest_from_zip(tmp_path: Path) -> None:
    csv_bytes = ppr_csv_path().read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("PPR-ALL.csv", csv_bytes)
    zip_path = tmp_path / "PPR-ALL.zip"
    zip_path.write_bytes(buffer.getvalue())
    db_path = tmp_path / "warehouse.sqlite"
    count = ingest_ppr(db_path, zip_path=zip_path, since=date(2020, 1, 1))
    assert count == 3
