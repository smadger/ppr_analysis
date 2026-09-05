from __future__ import annotations

import io

from ppr_analysis.parse import parse_floor_area_m2, parse_irish_date, parse_price
from ppr_analysis.ppr import parse_ppr_csv


def test_parse_currency_and_irish_dates() -> None:
    assert parse_price("€325,000.00") == 325000.0
    assert parse_irish_date("14/07/2024").isoformat() == "2024-07-14"
    assert parse_floor_area_m2("127.0 m²") == 127.0


def test_cp1252_euro_price_column() -> None:
    csv_text = (
        "Date of Sale (dd/mm/yyyy),Address,Postal Code,County,Price (€),"
        "Not Full Market Price,VAT Exclusive,Description of Property,Property Size Description\n"
        '14/07/2024,"12 Barley Cove, Wheaton Hall, Drogheda",,Louth,"€385,000.00",No,No,Second-Hand,\n'
    )
    parsed = parse_ppr_csv(io.BytesIO(csv_text.encode("cp1252")))
    assert "price_raw" in parsed.columns
    assert parsed.iloc[0]["address"].startswith("12 Barley Cove")
