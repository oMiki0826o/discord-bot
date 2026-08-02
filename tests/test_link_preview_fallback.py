"""
tests/test_link_preview_fallback.py

Modification():

- 新增本檔案：測試 core.link_preview.fallback 新增的「近期失敗網域
  短期冷卻」機制。專案目前沒有安裝 pytest-asyncio，也沒有其他測試
  檔案採用 async def test_...，因此比照全專案既有慣例，在一般同步
  測試函式內用 asyncio.run() 包裝要測試的 async 邏輯，不引入新的
  測試相依套件。

測試 core.link_preview.fallback.try_hosts()：
- 候選網域失敗後會被標記冷卻，之後的請求會優先跳過它
- 全部候選都在冷卻中時，仍會照樣全部嘗試一輪，不會直接放棄
- 請求成功的 host 會被移出冷卻名單
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import httpx

import core.link_preview.fallback as fallback


class _FakeClient:
    """
    模擬 httpx.AsyncClient：get(url) 依網址中是否含 fail_host 決定
    成功或丟出 ConnectTimeout，並記錄每一次實際呼叫的網址供斷言。
    """

    def __init__(self, fail_host: str, call_log: list[str]) -> None:
        self._fail_host = fail_host
        self._call_log  = call_log

    async def get(self, url: str) -> httpx.Response:
        self._call_log.append(url)
        if self._fail_host in url:
            raise httpx.ConnectTimeout("timeout", request=httpx.Request("GET", url))
        return httpx.Response(200, request=httpx.Request("GET", url))

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _run(coro):
    """在同步測試函式內執行 async 邏輯，不依賴 pytest-asyncio。"""
    return asyncio.run(coro)


def test_failed_host_enters_cooldown_and_successful_host_does_not():
    """host_a 失敗、host_b 成功：回傳 host_b 的回應，且只有 host_a 進入冷卻名單。"""
    fallback._dead_until.clear()
    call_log: list[str] = []

    with patch.object(fallback, "build_client", return_value=_FakeClient("host_a", call_log)):
        response = _run(fallback.try_hosts(
            lambda h: f"https://{h}/x", ["host_a", "host_b"], platform_label="Test",
        ))

    assert response is not None and response.status_code == 200
    assert "host_a" in fallback._dead_until
    assert "host_b" not in fallback._dead_until


def test_host_in_cooldown_is_skipped_on_next_call():
    """host_a 仍在冷卻中時，下一次呼叫應直接跳過它，只實際請求 host_b。"""
    fallback._dead_until.clear()
    fallback._dead_until["host_a"] = time.monotonic() + 10_000
    call_log: list[str] = []

    with patch.object(fallback, "build_client", return_value=_FakeClient("host_a", call_log)):
        _run(fallback.try_hosts(
            lambda h: f"https://{h}/x", ["host_a", "host_b"], platform_label="Test",
        ))

    assert call_log == ["https://host_b/x"]


def test_all_hosts_in_cooldown_still_retries_all_of_them():
    """
    全部候選都在冷卻中時，不能因此直接放棄（回傳 None）：冷卻只是
    「優先跳過」的加速手段，不是永久放棄，所有候選都無法排除時，
    仍必須照樣全部嘗試一輪，維持「還有機會成功」的行為。
    """
    fallback._dead_until.clear()
    fallback._dead_until["host_a"] = time.monotonic() + 10_000
    fallback._dead_until["host_b"] = time.monotonic() + 10_000
    call_log: list[str] = []

    # host_b 在這個 fake client 裡其實請求會成功，只是先前被標記冷卻
    with patch.object(fallback, "build_client", return_value=_FakeClient("host_a", call_log)):
        response = _run(fallback.try_hosts(
            lambda h: f"https://{h}/x", ["host_a", "host_b"], platform_label="Test",
        ))

    assert response is not None and response.status_code == 200
    assert set(call_log) == {"https://host_a/x", "https://host_b/x"}


def test_empty_host_list_returns_none_without_raising():
    """候選清單為空時直接回傳 None，不應嘗試建立連線或拋出例外。"""
    fallback._dead_until.clear()
    response = _run(fallback.try_hosts(lambda h: f"https://{h}/x", [], platform_label="Test"))
    assert response is None
