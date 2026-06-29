"""
tests/test_tool_registry.py

測試 core.ai.tool_registry.select_tools()：純規則判斷，
不呼叫任何 executor（executor 涉及 DB / Gemini Client，
此處只驗證「該不該啟用某個工具」的判斷邏輯）。
"""

from __future__ import annotations

from core.ai.tool_registry import TOOL_REGISTRY, select_tools


def test_short_prompt_triggers_nothing():
    assert select_tools("嗨") == []
    assert select_tools("hi") == []


def test_long_prompt_triggers_memory():
    tools = select_tools("這是一段超過十個字的長句子用來測試記憶工具")
    assert "memory" in tools


def test_memory_keyword_alone_does_not_bypass_global_short_prompt_guard():
    """
    select_tools() 有一個全域規則：prompt 長度 < 8 時直接回傳 []，
    不論內容是否包含任何工具關鍵字。"你記得嗎"（5 字）即使包含
    memory 關鍵字「記得」，仍會被這個全域規則擋下，回傳 []。
    這是既有設計（避免太短的訊息也觸發工具），非本次重構引入的行為。
    """
    assert select_tools("你記得嗎") == []


def test_memory_trigger_function_itself_reacts_to_keyword_regardless_of_length():
    """
    與上一個測試對照：_memory_trigger() 本身（不經過 select_tools()
    的全域長度守門）確實會因關鍵字而觸發，證明「全域守門擋下」與
    「trigger 規則本身」是兩個獨立的判斷層級，重構後仍維持原樣。
    """
    from core.ai.tool_registry import _memory_trigger
    assert _memory_trigger("你記得嗎") is True


def test_memory_keyword_triggers_when_prompt_is_long_enough():
    """關鍵字 + 長度 >= 8，兩個條件都成立時的正常情況。"""
    tools = select_tools("你還記得嗎？我很好奇")
    assert "memory" in tools


def test_summary_keyword_triggers_summary_tool():
    tools = select_tools("我們剛才聊到什麼話題了")
    assert "summary" in tools


def test_profile_keyword_triggers_profile_tool():
    tools = select_tools("我喜歡安靜的環境，幫我推薦一下")
    assert "profile" in tools


def test_multiple_tools_can_trigger_together():
    tools = select_tools("我們剛才聊的內容，你記得嗎，我喜歡那種風格")
    assert "memory" in tools
    assert "summary" in tools
    assert "profile" in tools


def test_registry_entries_have_unique_names():
    names = [entry.name for entry in TOOL_REGISTRY]
    assert len(names) == len(set(names)), "TOOL_REGISTRY 內有重複的工具名稱"


def test_registry_entries_are_callable():
    for entry in TOOL_REGISTRY:
        assert callable(entry.trigger)
        assert callable(entry.executor)
