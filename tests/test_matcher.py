from __future__ import annotations

from ppr_analysis.matcher import extract_unit_id, match_sales, normalize_address, score_pair


def _ppr(**overrides) -> dict:
    row = {
        "ppr_id": "p1",
        "sale_date": "2024-07-14",
        "address": "12 Barley Cove, Wheaton Hall, Drogheda, Co. Louth",
        "price": 385000.0,
        "eircode": None,
    }
    row.update(overrides)
    return row


def _daft(**overrides) -> dict:
    row = {
        "listing_id": "6517522",
        "url": "https://www.daft.ie/sold/12-barley-cove-wheaton-hall-drogheda-louth/x",
        "address": "12 Barley Cove, Wheaton Hall, Drogheda, Louth",
        "sold_date": "2024-07-14",
        "sold_price": 385000.0,
    }
    row.update(overrides)
    return row


def test_normalize_expands_abbreviations_and_strips_county_noise() -> None:
    messy = "12 BARLEY COVE WHEATON HALL DROGHEDA LOUTH LOUTH"
    assert normalize_address("14 Cord Rd, Drogheda, Co. Louth") == "14 cord road drogheda"
    assert "louth" not in normalize_address(messy)
    assert normalize_address("Apt 9c Kermon Hse, The Mall") == "apartment 9c kermon house mall"


def test_extract_apartment_and_house_numbers() -> None:
    assert extract_unit_id("Apt 9c Kermon House, The Mall, Drogheda") == "9c"
    assert extract_unit_id("12 Barley Cove, Wheaton Hall") == "12"


def test_wheaton_hall_golden_pair_is_exact() -> None:
    status, score = score_pair(_ppr(), _daft())
    assert status == "exact"
    assert score >= 95


def test_kermon_house_apartment_pair() -> None:
    ppr = _ppr(
        ppr_id="kermon",
        sale_date="2024-07-07",
        address="Apartment 9C, Kermon House, The Mall, Drogheda",
        price=180000.0,
    )
    daft = _daft(
        listing_id="6280375",
        address="Apt 9c Kermon House, The Mall, Drogheda, Louth",
        sold_date="2024-07-07",
        sold_price=180000.0,
        url="https://www.daft.ie/sold/kermon",
    )
    status, score = score_pair(ppr, daft)
    assert status in {"exact", "high"}
    assert score >= 88


def test_messy_ppr_string_still_matches() -> None:
    ppr = _ppr(address="12 BARLEY COVE WHEATON HALL DROGHEDA LOUTH LOUTH")
    matches = match_sales([ppr], [_daft()])
    assert matches[0]["match_status"] in {"exact", "high"}
    assert matches[0]["listing_id"] == "6517522"


def test_different_house_number_is_not_exact() -> None:
    daft = _daft(
        listing_id="other",
        address="8 Chestnut Grove, Wheaton Hall, Drogheda, Louth",
        sold_price=400000.0,
    )
    status, _score = score_pair(_ppr(), daft)
    assert status != "exact"
