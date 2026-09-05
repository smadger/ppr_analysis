from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def ppr_csv_path() -> Path:
    return FIXTURES / "ppr_sample.csv"


def daft_html_page(page: int = 1) -> str:
    payload = json.loads((FIXTURES / "daft_next_data.json").read_text(encoding="utf-8"))
    paging = payload["props"]["pageProps"]["paging"]
    paging["currentPage"] = page
    if page > 1:
        payload["props"]["pageProps"]["listings"] = []
    return (
        "<!DOCTYPE html><html><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )


def daft_gateway_page(page: int = 1) -> str:
    payload = json.loads((FIXTURES / "daft_next_data.json").read_text(encoding="utf-8"))
    page_props = payload["props"]["pageProps"]
    listings = page_props["listings"]
    paging = dict(page_props["paging"])
    paging["currentPage"] = page
    if page > 1:
        listings = []
    return json.dumps({"listings": listings, "paging": paging})


class FakeResponse:
    def __init__(
        self,
        text: str = "",
        content: bytes = b"",
        status_code: int = 200,
        headers: dict | None = None,
    ) -> None:
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text or "null")


class FakeClient:
    def __init__(
        self,
        html_by_url: dict[str, str] | None = None,
        zip_bytes: bytes | None = None,
        fail_remaining: dict[str, int] | None = None,
    ) -> None:
        self.html_by_url = html_by_url or {}
        self.zip_bytes = zip_bytes
        self.fail_remaining = fail_remaining or {}
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, url: str, headers: dict | None = None) -> FakeResponse:
        self.gets.append(url)
        remaining = self.fail_remaining.get(url, 0)
        if remaining > 0:
            self.fail_remaining[url] = remaining - 1
            return FakeResponse(status_code=429, headers={"Retry-After": "2"})
        if url in self.html_by_url:
            return FakeResponse(text=self.html_by_url[url])
        if self.zip_bytes is not None and url.endswith(".zip"):
            return FakeResponse(content=self.zip_bytes)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> FakeResponse:
        payload = json or {}
        self.posts.append((url, payload))
        frm = str((payload.get("paging") or {}).get("from", "0"))
        fail_key = f"{url}#{frm}"
        remaining = self.fail_remaining.get(fail_key, self.fail_remaining.get(url, 0))
        if remaining > 0:
            if fail_key in self.fail_remaining:
                self.fail_remaining[fail_key] = remaining - 1
            elif url in self.fail_remaining:
                self.fail_remaining[url] = remaining - 1
            return FakeResponse(status_code=429, headers={"Retry-After": "2"})
        terms = payload.get("terms")
        if terms and terms in self.html_by_url:
            return FakeResponse(text=self.html_by_url[terms])
        key = f"from:{frm}"
        if key in self.html_by_url:
            return FakeResponse(text=self.html_by_url[key])
        if url in self.html_by_url:
            return FakeResponse(text=self.html_by_url[url])
        return FakeResponse(text='{"listings": []}')

    def close(self) -> None:
        return None
