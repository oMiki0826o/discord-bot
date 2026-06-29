"""
tests/test_abuse_guard.py

測試 core.ai.abuse_guard：
- 滑動視窗計數與門檻判斷
- 觸發限制後的拒絕訊息與 is_restricted()
- clear_restriction() 手動解除

直接 monkeypatch 模組內的門檻常數（_MAX_REQUESTS 等），
不透過環境變數，避免受其他測試的 import 順序影響。
"""

from __future__ import annotations

import core.ai.abuse_guard as abuse_guard


def _reset(monkeypatch, max_requests=3, window=60, restrict_minutes=5):
    """重設滑動視窗狀態與門檻，確保測試之間互不影響。"""
    abuse_guard._request_log.clear()
    monkeypatch.setattr(abuse_guard, "_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(abuse_guard, "_WINDOW_SECONDS", window)
    monkeypatch.setattr(abuse_guard, "_RESTRICT_MINUTES", restrict_minutes)


def test_requests_within_threshold_are_allowed(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=3)
    for _ in range(3):
        allowed, reason = abuse_guard.check_and_record("u1")
        assert allowed is True
        assert reason is None


def test_exceeding_threshold_triggers_restriction(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=2)
    abuse_guard.check_and_record("u1")
    abuse_guard.check_and_record("u1")
    allowed, reason = abuse_guard.check_and_record("u1")   # 第 3 次，超過門檻 2

    assert allowed is False
    assert reason is not None
    assert abuse_guard.is_restricted("u1") is True


def test_restriction_blocks_subsequent_requests_without_recounting(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)
    abuse_guard.check_and_record("u1")
    allowed1, _ = abuse_guard.check_and_record("u1")   # 觸發限制
    assert allowed1 is False

    allowed2, reason2 = abuse_guard.check_and_record("u1")   # 限制中再次請求
    assert allowed2 is False
    assert "限制中" in reason2


def test_other_users_are_not_affected(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)
    abuse_guard.check_and_record("u1")
    abuse_guard.check_and_record("u1")   # u1 觸發限制

    allowed, reason = abuse_guard.check_and_record("u2")   # 不同使用者
    assert allowed is True
    assert reason is None


def test_clear_restriction_allows_requests_again(fresh_db, monkeypatch):
    _reset(monkeypatch, max_requests=1)
    abuse_guard.check_and_record("u1")
    abuse_guard.check_and_record("u1")   # 觸發限制
    assert abuse_guard.is_restricted("u1") is True

    abuse_guard.clear_restriction("u1")
    assert abuse_guard.is_restricted("u1") is False

    abuse_guard._request_log.clear()
    allowed, _ = abuse_guard.check_and_record("u1")
    assert allowed is True
