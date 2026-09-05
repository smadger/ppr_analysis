from __future__ import annotations

from pathlib import Path

from ppr_analysis.geocode import (
    build_query,
    candidate_queries,
    geocode_sales,
    in_drogheda_bounds,
    jitter_point,
    parse_nominatim_response,
    query_key,
)
from ppr_analysis.ppr import ingest_ppr
from ppr_analysis.warehouse import connect
from tests.conftest import FakeResponse, ppr_csv_path


class NominatimClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> FakeResponse:
        query = (params or {}).get("q", "")
        self.queries.append(query)
        if "Dundalk" in query:
            return FakeResponse(text="[]")
        payload = '[{"lat":"53.717","lon":"-6.351","display_name":"Drogheda, Louth"}]'
        return FakeResponse(text=payload)

    def close(self) -> None:
        return None


def test_parse_and_bounds() -> None:
    parsed = parse_nominatim_response([{"lat": "53.717", "lon": "-6.351", "display_name": "Drogheda"}])
    assert parsed is not None
    lat, lng, name = parsed
    assert in_drogheda_bounds(lat, lng)
    assert not in_drogheda_bounds(53.3, -6.25)
    assert name == "Drogheda"


def test_candidate_queries_include_estate_fallbacks() -> None:
    queries = candidate_queries("12 Barley Cove, Wheaton Hall, Drogheda, Co. Louth", None)
    assert any("Wheaton Hall, Drogheda" in item for item in queries)
    assert queries[0].startswith("12 Barley Cove")
    a = jitter_point(53.717, -6.351, "seed-a")
    b = jitter_point(53.717, -6.351, "seed-a")
    c = jitter_point(53.717, -6.351, "seed-b")
    assert a == b
    assert a != c
    assert abs(a[0] - 53.717) < 0.001


def test_geocode_caches_and_rate_limits(tmp_path: Path) -> None:
    db_path = tmp_path / "warehouse.sqlite"
    ingest_ppr(db_path, csv_path=ppr_csv_path())
    client = NominatimClient()
    sleeps: list[float] = []
    stats = geocode_sales(db_path, client=client, delay_seconds=1.0, sleeper=sleeps.append)
    assert stats["network_calls"] == stats["unique_queries"]
    assert stats["hits"] >= 1
    assert len(sleeps) == stats["network_calls"] - 1
    conn = connect(db_path)
    cached = conn.execute("SELECT COUNT(*) AS n FROM geocode_cache").fetchone()["n"]
    conn.close()
    assert cached == stats["unique_queries"]

    client2 = NominatimClient()
    stats2 = geocode_sales(db_path, client=client2, delay_seconds=1.0, sleeper=sleeps.append)
    assert stats2["network_calls"] == 0
    assert client2.queries == []
    assert query_key(build_query("x", None)) == query_key("x, Drogheda, County Louth, Ireland")
