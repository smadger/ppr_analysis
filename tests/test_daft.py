from __future__ import annotations

from pathlib import Path

from ppr_analysis.config import DAFT_GATEWAY_URL
from ppr_analysis.daft import ingest_daft, is_meath_listing, listings_from_html
from tests.conftest import FakeClient, daft_gateway_page, daft_html_page


def test_drop_meath_addresses() -> None:
    assert is_meath_listing("21 The Drive, Bryanstown Wood, Drogheda, Meath")
    assert is_meath_listing("Beabeg, Julianstown, Meath, Meath")
    assert not is_meath_listing("12 Barley Cove, Wheaton Hall, Drogheda, Louth")
    assert not is_meath_listing("20 Park Close, Grange Rath, Drogheda, Louth")


def test_parse_next_data_listings() -> None:
    listings, paging = listings_from_html(daft_html_page(1))
    assert paging["totalPages"] == 2
    addresses = [row["address"] for row in listings]
    assert "12 Barley Cove, Wheaton Hall, Drogheda, Louth" in addresses
    barley = next(row for row in listings if "Barley Cove" in row["address"])
    assert barley["sold_price"] == 385000.0
    assert barley["asking_price"] == 375000.0
    assert barley["beds"] == 4
    assert barley["floor_area_m2"] == 127.0
    assert barley["property_type"] == "Semi-D"


def test_ingest_caches_and_rate_limits(tmp_path: Path) -> None:
    html_by_url = {
        "from:0": daft_gateway_page(1),
        "from:20": daft_gateway_page(2),
    }
    client = FakeClient(html_by_url=html_by_url)
    sleeps: list[float] = []
    db_path = tmp_path / "warehouse.sqlite"
    stats = ingest_daft(
        db_path,
        delay_seconds=1.5,
        client=client,
        sleeper=sleeps.append,
    )
    assert stats["listings"] == 2
    assert stats["dropped_meath"] == 1
    assert stats["pages"] == 2
    assert stats["network_calls"] == 2
    assert sleeps == [1.5]
    assert len(client.posts) == 2

    client_again = FakeClient(html_by_url=html_by_url)
    sleeps_again: list[float] = []
    stats_again = ingest_daft(
        db_path,
        delay_seconds=1.5,
        client=client_again,
        sleeper=sleeps_again.append,
    )
    assert stats_again["network_calls"] == 0
    assert client_again.posts == []
    assert sleeps_again == []
    assert stats_again["listings"] == 2


def test_ingest_retries_on_429(tmp_path: Path) -> None:
    client = FakeClient(
        html_by_url={
            "from:0": daft_gateway_page(1),
            "from:20": daft_gateway_page(2),
        },
        fail_remaining={f"{DAFT_GATEWAY_URL}#20": 1},
    )
    sleeps: list[float] = []
    stats = ingest_daft(
        tmp_path / "warehouse.sqlite",
        delay_seconds=1.5,
        client=client,
        sleeper=sleeps.append,
    )
    assert stats["listings"] == 2
    assert stats["network_calls"] == 2
    assert sleeps == [1.5, 2.0]
    assert [item[1]["paging"]["from"] for item in client.posts] == ["0", "20", "20"]
