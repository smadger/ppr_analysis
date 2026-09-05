from __future__ import annotations

import json
from pathlib import Path

from ppr_analysis.config import DAFT_GATEWAY_URL
from ppr_analysis.daft import CachedFetcher
from ppr_analysis.matcher import fallback_search_terms, run_fallback_search
from ppr_analysis.ppr import ingest_ppr
from ppr_analysis.warehouse import connect
from tests.conftest import FakeClient, ppr_csv_path


def test_fallback_requires_house_number_and_eircode() -> None:
    assert fallback_search_terms("Wheaton Hall, Drogheda", "A92 AB12") is None
    assert fallback_search_terms("12 Barley Cove, Drogheda", None) is None
    assert "A92" in fallback_search_terms("12 Barley Cove, Drogheda", "A92 AB12")


def test_fallback_search_inserts_new_listing(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    ingest_ppr(db_path, csv_path=ppr_csv_path())
    listing = {
        "listing": {
            "id": 999001,
            "title": "8 Westcourt, Drogheda, Louth",
            "price": "EUR310,000",
            "soldPrice": "EUR310,000",
            "soldDate": "15/06/2024",
            "numBedrooms": "3 Bed",
            "numBathrooms": "2 Bath",
            "propertyType": "Semi-D",
            "propertySize": "100.0 m2",
            "seoFriendlyPath": "/sold/8-westcourt-drogheda-louth/x",
            "seller": {"branch": "Test Agent"},
            "ber": {"rating": "C3"},
        }
    }
    client = FakeClient(
        html_by_url={
            "8 Westcourt, Drogheda A92 ZZ99": json.dumps({"listings": [listing]}),
        }
    )
    conn = connect(db_path)
    conn.execute("UPDATE ppr_sales SET eircode = 'A92 ZZ99' WHERE address LIKE '%Westcourt%'")
    conn.commit()
    fetcher = CachedFetcher(conn, client, delay_seconds=0, sleeper=lambda _d: None)
    added = run_fallback_search(db_path, fetcher)
    assert added == 1
    assert client.posts and client.posts[0][0] == DAFT_GATEWAY_URL
    row = conn.execute(
        "SELECT address, source FROM daft_listings WHERE listing_id = '999001'"
    ).fetchone()
    assert row["address"].startswith("8 Westcourt")
    assert row["source"] == "fallback"
    conn.close()
