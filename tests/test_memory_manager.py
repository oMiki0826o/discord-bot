"""
tests/test_memory_manager.py

測試範圍刻意限制在「不會呼叫 Gemini API」的部分：
- core.ai.memory_manager.save_message / search / get_recent
  （這幾個函式只包裝 repository 的純 SQL 查詢與 ranker 排序邏輯，
  不涉及任何 AI 呼叫，適合在無網路環境下做單元測試）
- database.repository.memory_repository.load_background()
  的格式解析（修正後的版本）

_extract() / _summarize_if_needed() / _vectorize_recent() 因為
直接呼叫 Gemini API，不在此檔測試範圍內；若要測試這幾個函式，
需要 mock core.ai.gemini_client.client，超出本檔的單元測試目標。
"""

from __future__ import annotations

import core.ai.memory_manager as memory_manager
import database.repository.memory_repository as mem_repo

# ── channel_id 隔離（核心新功能的回歸測試）──────────────────────────

def test_get_recent_filters_by_channel(fresh_db):
    memory_manager.save_message("u1", "user", "頻道A的話", "channelA")
    memory_manager.save_message("u1", "user", "頻道B的話", "channelB")

    recent_a = memory_manager.get_recent("u1", "channelA")
    recent_b = memory_manager.get_recent("u1", "channelB")

    assert any("頻道A" in c for _, c in recent_a)
    assert all("頻道B" not in c for _, c in recent_a)
    assert any("頻道B" in c for _, c in recent_b)
    assert all("頻道A" not in c for _, c in recent_b)


def test_search_does_not_leak_messages_across_channels(fresh_db):
    memory_manager.save_message("u1", "user", "在A群組討論的祕密話題", "channelA")
    memory_manager.save_message("u1", "user", "在B群組討論的另一個話題", "channelB")

    bundle_a = memory_manager.search("u1", "channelA", "祕密話題")
    bundle_b = memory_manager.search("u1", "channelB", "祕密話題")

    a_contents = [c for _, c in bundle_a.messages] + [c for _, c in bundle_a.recent]
    b_contents = [c for _, c in bundle_b.messages] + [c for _, c in bundle_b.recent]

    assert any("A群組" in c for c in a_contents)
    assert all("B群組" not in c for c in a_contents)
    assert all("A群組" not in c for c in b_contents)


def test_search_cache_key_distinguishes_channel(fresh_db):
    """
    同一使用者、同一查詢字串，在不同頻道呼叫 search() 時，
    快取 key 必須包含 channel_id，否則會誤用對方頻道的快取結果。
    """
    memory_manager._search_cache.clear()
    memory_manager.search("u1", "channelA", "測試查詢")
    memory_manager.search("u1", "channelB", "測試查詢")

    keys = list(memory_manager._search_cache.keys())
    assert any(k.startswith("u1:channelA:") for k in keys)
    assert any(k.startswith("u1:channelB:") for k in keys)


def test_count_messages_is_not_channel_filtered(fresh_db):
    """
    count_messages() 刻意不依 channel_id 過濾（_MSG_LIMIT 清理與
    摘要觸發判斷以使用者整體為單位），確認這個設計沒有被意外改動。
    """
    memory_manager.save_message("u1", "user", "頻道A訊息", "channelA")
    memory_manager.save_message("u1", "user", "頻道B訊息", "channelB")
    assert mem_repo.count_messages("u1") == 2


# ── load_background() 格式解析（修正 lstrip/rstrip 同類問題後的回歸測試）──

def test_load_background_parses_section_headers(tmp_path, monkeypatch):
    content = (
        "【基本設定】\n"
        "這是基本設定的內容\n"
        "\n"
        "【其他設定】\n"
        "這是其他設定的內容\n"
    )
    bg_file = tmp_path / "background.txt"
    bg_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(mem_repo, "_BG_FILE", bg_file)

    result = mem_repo.load_background()
    keywords = [kw for kw, _, _ in result]
    assert "基本設定" in keywords
    assert "其他設定" in keywords


def test_load_background_keeps_intro_text_before_first_header(tmp_path, monkeypatch):
    content = "這是開頭簡介文字\n【正式區塊】\n區塊內容\n"
    bg_file = tmp_path / "background.txt"
    bg_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(mem_repo, "_BG_FILE", bg_file)

    result = mem_repo.load_background()
    assert result[0][0] == "intro"
    assert "開頭簡介文字" in result[0][1]


def test_load_background_backward_compatible_key_value_format(tmp_path, monkeypatch):
    content = "name=測試名稱\nrole=測試角色\n"
    bg_file = tmp_path / "background.txt"
    bg_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(mem_repo, "_BG_FILE", bg_file)

    result = mem_repo.load_background()
    as_dict = {kw: content for kw, content, _ in result}
    assert as_dict.get("name") == "測試名稱"
    assert as_dict.get("role") == "測試角色"


def test_load_background_freeform_fallback_when_no_format_detected(tmp_path, monkeypatch):
    """
    回歸測試：原本的 bug 是「完全沒有 = 符號的自由格式文字」
    會讓 load_background() 永遠回傳空 list。
    修正後應 fallback 成單一筆，不再悄悄回傳空結果。
    """
    content = "這是一段完全沒有任何特殊格式的純文字說明。\n沒有等號也沒有區塊標題。"
    bg_file = tmp_path / "background.txt"
    bg_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(mem_repo, "_BG_FILE", bg_file)

    result = mem_repo.load_background()
    assert len(result) == 1
    assert result[0][0] == "background"
    assert "完全沒有任何特殊格式" in result[0][1]


def test_load_background_missing_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(mem_repo, "_BG_FILE", tmp_path / "does_not_exist.txt")
    assert mem_repo.load_background() == []


def test_load_background_empty_file_returns_empty_list(tmp_path, monkeypatch):
    bg_file = tmp_path / "background.txt"
    bg_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(mem_repo, "_BG_FILE", bg_file)
    assert mem_repo.load_background() == []
