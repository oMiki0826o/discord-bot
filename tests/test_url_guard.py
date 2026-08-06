"""
tests/test_url_guard.py

Modification():

- 新增本檔案：測試 core.link_preview.url_guard 的 SSRF 防護邏輯，
  以及 core.link_preview.article.fetch_text() 的重定向逐跳驗證。
  比照全專案既有慣例，不依賴 pytest-asyncio，在一般同步測試函式
  內用 asyncio.run() 包裝要測試的 async 邏輯。

測試 core.link_preview.url_guard.is_safe_url()：
- 私有網段、迴路、連結本地（含雲端 metadata 端點）、非 http(s)
  協定皆應被拒絕
- 一般公開網域應通過

測試 core.link_preview.article.fetch_text()：
- 正常網頁：取得清理後的文字內容
- 安全的重定向：正常追隨並取得內容
- 重定向到內網／metadata 位址：應被擋下，回傳 None（這是本次修正
  最關鍵的情境——若只驗證最初的網址、不逐跳重新驗證，這裡會失敗）
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx

import core.link_preview.article as article
from core.link_preview.url_guard import is_safe_url


def _run(coro):
    return asyncio.run(coro)


# ── is_safe_url() ──────────────────────

def test_rejects_cloud_metadata_endpoint():
    """雲端 metadata 端點（AWS/GCP/Azure 皆使用此連結本地位址）必須被拒絕。"""
    safe, _ = is_safe_url("http://169.254.169.254/latest/meta-data/")
    assert safe is False


def test_rejects_localhost_and_loopback():
    safe, _ = is_safe_url("http://localhost:8080/admin")
    assert safe is False
    safe, _ = is_safe_url("http://127.0.0.1/")
    assert safe is False
    safe, _ = is_safe_url("http://[::1]/")
    assert safe is False


def test_rejects_private_network_ranges():
    for url in ("http://192.168.1.1/", "http://10.0.0.5/", "http://172.16.0.1/"):
        safe, _ = is_safe_url(url)
        assert safe is False, url


def test_rejects_non_http_schemes():
    safe, _ = is_safe_url("file:///etc/passwd")
    assert safe is False
    safe, _ = is_safe_url("ftp://example.com/")
    assert safe is False


def test_allows_public_domain():
    safe, _ = is_safe_url("https://www.example.com/page")
    assert safe is True


# ── fetch_text()：重定向逐跳驗證 ──────────────────────

class _FakeStreamResponse:
    """模擬 httpx 的串流回應，支援 is_redirect 與 aiter_bytes()。"""

    def __init__(self, status_code, url, headers=None, body=b""):
        self.status_code = status_code
        self.url = httpx.URL(url)
        self.headers = headers or {}
        self.encoding = "utf-8"
        self.is_redirect = 300 <= status_code < 400
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    async def aiter_bytes(self):
        yield self._body


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc_info):
        return False


class _FakeClient:
    """依序回放預先準備好的回應序列，模擬多跳重定向。"""

    def __init__(self, responses):
        self._responses = responses
        self.call_count = 0

    def stream(self, method, url):
        response = self._responses[self.call_count]
        self.call_count += 1
        return _FakeStreamCtx(response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def test_fetch_text_returns_cleaned_content_for_normal_page():
    fake_client = _FakeClient([
        _FakeStreamResponse(
            200, "https://example.com/", headers={"content-type": "text/html"},
            body=b"<html><body><p>Hello World test article content.</p></body></html>",
        )
    ])
    with patch.object(article, "build_client", return_value=fake_client):
        text = _run(article.fetch_text("https://example.com/", max_chars=1000))
    assert text is not None and "Hello World" in text


def test_fetch_text_follows_safe_redirect():
    fake_client = _FakeClient([
        _FakeStreamResponse(302, "https://example.com/old",
                             headers={"location": "https://example.com/new"}),
        _FakeStreamResponse(200, "https://example.com/new",
                             headers={"content-type": "text/html"},
                             body=b"<html><body>Redirected content for testing.</body></html>"),
    ])
    with patch.object(article, "build_client", return_value=fake_client):
        text = _run(article.fetch_text("https://example.com/old", max_chars=1000))
    assert text is not None and "Redirected content" in text


def test_fetch_text_blocks_redirect_to_internal_address():
    """
    最關鍵的情境：最初的網址是安全的公開網域，但伺服器用 3xx 導向
    內網／metadata 位址。若只驗證最初網址一次，這裡會被繞過；
    正確行為是逐跳重新驗證並擋下。
    """
    fake_client = _FakeClient([
        _FakeStreamResponse(
            302, "https://example.com/evil",
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
        ),
    ])
    with patch.object(article, "build_client", return_value=fake_client):
        text = _run(article.fetch_text("https://example.com/evil", max_chars=1000))
    assert text is None


def test_fetch_text_rejects_unsafe_initial_url_without_any_request():
    """最初網址本身就不安全時，不應該發送任何請求。"""
    fake_client = _FakeClient([])  # 空清單：一旦嘗試呼叫 stream() 就會 IndexError
    with patch.object(article, "build_client", return_value=fake_client):
        text = _run(article.fetch_text("http://127.0.0.1/admin", max_chars=1000))
    assert text is None
    assert fake_client.call_count == 0
