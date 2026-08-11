"""
tests/test_abuse_guard.py

Modification():
- check_and_record() / is_restricted() / clear_restriction() 現在是
  async def（因應 database/repository/user_repository.py 全面套用
  utils.async_db.to_thread，core/ai/abuse_guard.py 呼叫的 repo 函式
  都變成需要 await 的函式）。原本這裡直接同步呼叫
  abuse_guard.check_and_record("u1")，現在會拿到一個 coroutine
  物件而不是實際的 (bool, str | None)，對其 tuple unpack 會直接
  丟出 TypeError。比照全專案既有慣例（不依賴 pytest-asyncio），
  在每個測試函式內定義一個 async def _test()，於函式最後用
  asyncio.run(_test()) 執行，並在裡面對所有呼叫加上 await。
- 修正 _reset()：原本 monkeypatch.setattr(abuse_guard, "_MAX_REQUESTS", ...)
  等三行，設定的是已經不存在的模組層級常數——abuse_guard.py 早已改為
  每次呼叫 check_and_record() 時透過 get_int() 即時讀取
  settings.json（見 abuse_guard.py 的「所有閾值...可熱更新」設計說明），
  模組內根本沒有 _MAX_REQUESTS / _WINDOW_SECONDS / _RESTRICT_MINUTES
  這幾個屬性了。monkeypatch.setattr 對不存在的屬性一律會拋出
  AttributeError，導致這個測試檔案的 5 個測試全數失敗。
  改為直接攔截 abuse_guard.get_int（check_and_record 內實際呼叫的
  名稱），依 key 回傳測試指定的固定值，其餘 key 回退給預設值，
  不需要依賴 settings.json 的實際內容。

測試 core.ai.abuse_guard：
- 滑動視窗計數與門檻判斷
- 觸發限制後的拒絕訊息與 is_restricted()
- clear_restriction() 手動解除
"""

from __future__ import annotations

import asyncio

import core.ai.abuse_guard as abuse_guard


def _reset(monkeypatch, max_requests=3, window=60, restrict_minutes=5):
    """重設滑動視窗狀態，並固定 get_int() 讀到的門檻值，確保測試互不影響。"""
    abuse_guard._request_log.clear()

    fixed_values = {
        "ai.abuse_max_requests":    max_requests,
        "ai.abuse_window_seconds": window,
        "ai.abuse_restrict_minutes": restrict_minutes,
    }

    def _fake_get_int(key: str, default: int = 0) -> int:
        return fixed_values.get(key, default)

    monkeypatch.setattr(abuse_guard, "get_int", _fake_get_int)


def test_requests_within_threshold_are_allowed(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=3)

    async def _test():
        for _ in range(3):
            allowed, reason = await abuse_guard.check_and_record("u1")
            assert allowed is True
            assert reason is None

    asyncio.run(_test())


def test_exceeding_threshold_triggers_restriction(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=2)

    async def _test():
        await abuse_guard.check_and_record("u1")
        await abuse_guard.check_and_record("u1")
        allowed, reason = await abuse_guard.check_and_record("u1")   # 第 3 次，超過門檻 2

        assert allowed is False
        assert reason is not None
        assert await abuse_guard.is_restricted("u1") is True

    asyncio.run(_test())


def test_restriction_blocks_subsequent_requests_without_recounting(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)

    async def _test():
        await abuse_guard.check_and_record("u1")
        allowed1, _ = await abuse_guard.check_and_record("u1")   # 觸發限制
        assert allowed1 is False

        allowed2, reason2 = await abuse_guard.check_and_record("u1")   # 限制中再次請求
        assert allowed2 is False
        assert "限制中" in reason2

    asyncio.run(_test())


def test_other_users_are_not_affected(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)

    async def _test():
        await abuse_guard.check_and_record("u1")
        await abuse_guard.check_and_record("u1")   # u1 觸發限制

        allowed, reason = await abuse_guard.check_and_record("u2")   # 不同使用者
        assert allowed is True
        assert reason is None

    asyncio.run(_test())


def test_clear_restriction_allows_requests_again(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)

    async def _test():
        await abuse_guard.check_and_record("u1")
        await abuse_guard.check_and_record("u1")   # 觸發限制
        assert await abuse_guard.is_restricted("u1") is True

        await abuse_guard.clear_restriction("u1")
        assert await abuse_guard.is_restricted("u1") is False

        abuse_guard._request_log.clear()
        allowed, _ = await abuse_guard.check_and_record("u1")
        assert allowed is True

    asyncio.run(_test())
