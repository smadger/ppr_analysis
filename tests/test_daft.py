from __future__ import annotations

import json
from pathlib import Path

from ppr_analysis.config import DAFT_GATEWAY_URL
from ppr_analysis.daft import ingest_daft, is_meath_listing, listings_from_html
from ppr_analysis.warehouse import connect
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


def test_reingest_preserves_fallback_and_prunes_orphan_matches(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    ingest_daft(
        db_path,
        delay_seconds=0,
        client=FakeClient(
            html_by_url={
                "from:0": daft_gateway_page(1),
                "from:20": daft_gateway_page(2),
            }
        ),
        sleeper=lambda _delay: None,
    )
    conn = connect(db_path)
    conn.execute(
        """
        INSERT INTO ppr_sales (
            ppr_id, sale_date, address, county, price, not_full_market_price, vat_exclusive
        ) VALUES
            ('p1', '2024-07-14', '12 Barley Cove', 'Louth', 385000, 0, 0),
            ('p2', '2024-06-15', '8 Westcourt', 'Louth', 310000, 0, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO daft_listings (listing_id, address, source)
        VALUES ('999001', '8 Westcourt, Drogheda, Louth', 'fallback')
        """
    )
    conn.execute(
        """
        INSERT INTO matches (ppr_id, listing_id, match_status, match_score, daft_url)
        VALUES
            ('p1', '6517522', 'exact', 100, 'https://www.daft.ie/a'),
            ('p2', '999001', 'high', 90, 'https://www.daft.ie/b')
        """
    )
    conn.commit()
    conn.close()

    ingest_daft(
        db_path,
        delay_seconds=0,
        refresh=True,
        client=FakeClient(
            html_by_url={
                "from:0": json.dumps(
                    {"listings": [], "paging": {"currentPage": 1, "totalPages": 1}}
                )
            }
        ),
        sleeper=lambda _delay: None,
    )
    conn = connect(db_path)
    listing_ids = {row["listing_id"] for row in conn.execute("SELECT listing_id FROM daft_listings")}
    assert listing_ids == {"999001"}
    orphan = conn.execute("SELECT match_status, listing_id FROM matches WHERE ppr_id = 'p1'").fetchone()
    assert orphan["match_status"] == "unmatched"
    assert orphan["listing_id"] is None
    kept = conn.execute("SELECT match_status, listing_id FROM matches WHERE ppr_id = 'p2'").fetchone()
    assert kept["match_status"] == "high"
    assert kept["listing_id"] == "999001"
    conn.close()
