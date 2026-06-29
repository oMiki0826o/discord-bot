"""
tests/test_budget.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

測試 core.ai.budget：
- _estimate() 字元數估算公式
- _extract_tokens() 對「有 / 無 usage_metadata」兩種情況的判斷
- record_usage() 寫入後 get_user_stats() / get_global_stats() 的彙總正確性
- get_top_users() 排行邏輯
- estimated_ratio 透明化欄位的計算

全部使用 fresh_db fixture（暫存 SQLite），不涉及任何真實 Gemini API 呼叫。
"""

from __future__ import annotations

from types import SimpleNamespace

import core.ai.budget as budget

# ── 估算公式 ──────────────────────

def test_estimate_uses_three_chars_per_token():
    assert budget._estimate("abc") == 1          # 3 字元 → 1 token
    assert budget._estimate("abcdef") == 2        # 6 字元 → 2 token
    assert budget._estimate("") == 1              # 至少回傳 1，避免 0


def test_extract_tokens_uses_real_usage_metadata_when_present():
    res = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=42, candidates_token_count=17,
        )
    )
    inp, out, estimated = budget._extract_tokens(res)
    assert (inp, out, estimated) == (42, 17, False)


def test_extract_tokens_falls_back_when_no_usage_metadata():
    res = SimpleNamespace(usage_metadata=None)
    inp, out, estimated = budget._extract_tokens(res)
    assert estimated is True
    assert inp == 0 and out == 0   # 由呼叫方（record_usage）補上估算值


def test_extract_tokens_falls_back_when_fields_missing():
    res = SimpleNamespace(usage_metadata=SimpleNamespace())  # 缺少欄位
    inp, out, estimated = budget._extract_tokens(res)
    assert estimated is True


# ── record_usage / 統計查詢 ──────────────────────

def test_record_usage_with_real_usage_metadata_is_not_estimated(fresh_db):
    res = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=50,
        )
    )
    budget.record_usage("u1", "gemini-2.5-flash", res=res)

    stats = budget.get_global_stats(hours=24)
    assert stats["total_requests"] == 1
    assert stats["total_tokens"] == 150
    assert stats["estimated_ratio"] == 0.0


def test_record_usage_without_response_is_estimated(fresh_db):
    budget.record_usage("u1", "gemma-4-31b-it", input_text="x" * 30, output_text="y" * 30)

    stats = budget.get_global_stats(hours=24)
    assert stats["total_requests"] == 1
    assert stats["estimated_ratio"] == 1.0


def test_estimated_ratio_reflects_mixed_requests(fresh_db):
    real_res = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=10,
        )
    )
    budget.record_usage("u1", "gemini-2.5-flash", res=real_res)        # 實際值
    budget.record_usage("u1", "gemma-4-31b-it", input_text="abc")      # 估算值
    budget.record_usage("u1", "gemma-4-31b-it", input_text="def")      # 估算值

    stats = budget.get_global_stats(hours=24)
    assert stats["total_requests"] == 3
    assert stats["estimated_ratio"] == 2 / 3


def test_get_user_stats_groups_by_model(fresh_db):
    budget.record_usage("u1", "model_a", input_text="aaa", output_text="bbb")
    budget.record_usage("u1", "model_a", input_text="ccc", output_text="ddd")
    budget.record_usage("u1", "model_b", input_text="eee", output_text="fff")

    stats = budget.get_user_stats("u1", days=30)
    assert stats["total_requests"] == 3
    assert set(stats["by_model"].keys()) == {"model_a", "model_b"}
    assert stats["by_model"]["model_a"]["requests"] == 2


def test_record_error_increments_error_count(fresh_db):
    budget.record_usage("u1", "model_a", input_text="ok")
    budget.record_error("timeout", user_id="u1", model="model_a")

    stats = budget.get_global_stats(hours=24)
    assert stats["error_count"] == 1
    assert stats["error_rate"] > 0


def test_get_top_users_orders_by_token_descending(fresh_db):
    budget.record_usage("heavy_user", "model_a", input_text="x" * 300, output_text="y" * 300)
    budget.record_usage("light_user", "model_a", input_text="x" * 3, output_text="y" * 3)

    top = budget.get_top_users(limit=10, days=30)
    assert len(top) == 2
    assert top[0]["user_id"] == "heavy_user"
    assert top[0]["tokens"] >= top[1]["tokens"]


def test_get_total_user_count_counts_distinct_users(fresh_db):
    budget.record_usage("u1", "model_a", input_text="x")
    budget.record_usage("u2", "model_a", input_text="x")
    budget.record_usage("u1", "model_a", input_text="y")  # 重複使用者

    assert budget.get_total_user_count() == 2
