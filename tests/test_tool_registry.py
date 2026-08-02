"""
tests/test_tool_registry.py

Modification():
- 修正 test_memory_trigger_function_itself_reacts_to_keyword_regardless_of_length：
  原本用「你記得嗎」（4 字，未達 _trigger_memory 內建的 8 字長度守門，
  一律回傳 False）驗證「關鍵字能觸發」，斷言本身就與實際邏輯矛盾；
  且 import 了不存在的 _memory_trigger、只傳一個參數（實際需要
  prompt 與 ctx 兩個參數）。改用「你之前說的我記得」（剛好 8 字，
  含關鍵字，長度本身未超過 10 字）作為測試字串，並修正函式名稱與
  參數數量。
- 順手修正相鄰測試裡「你記得嗎」的字數描述（原寫 5 字，實際是 4 字）。

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
    _trigger_memory() 本身有一道長度守門：len(prompt) < 8 一律回傳
    False，不論內容是否含記憶關鍵字。"你記得嗎"（4 字）即使包含
    memory 關鍵字「記得」，仍會被這道守門擋下，select_tools() 因此
    回傳 []。這是既有設計（避免太短的訊息也觸發工具），非本次重構
    引入的行為。
    """
    assert select_tools("你記得嗎") == []


def test_memory_trigger_function_itself_reacts_to_keyword_regardless_of_length():
    """
    驗證 _trigger_memory() 的完整判斷邏輯：
        len < 8            → False（一律不觸發）
        8 <= len <= 10      → 只有含關鍵字才觸發
        len > 10            → 一律觸發（不需要關鍵字）

    "你之前說的我記得" 剛好 8 字（含關鍵字「之前」「記得」），落在
    「長度本身不足以觸發（未超過 10 字），但關鍵字仍能觸發」的區間，
    這正是「regardless_of_length」這個測試名稱想驗證的行為：只要有
    關鍵字，不需要靠長度 > 10 這個條件也能觸發。

    修正：原本這裡誤用「你記得嗎」（4 字，未達 8 字的長度守門，
    _trigger_memory 一律回傳 False）來驗證這個行為，等於斷言本身
    就是錯的；且 import 名稱寫成不存在的 _memory_trigger（實際是
    _trigger_memory），只傳一個參數（實際簽名需要 prompt 與 ctx
    兩個參數）。三個問題疊在一起，這個測試案例其實從未真正執行過。
    """
    from core.ai.tool_registry import _trigger_memory
    assert _trigger_memory("你之前說的我記得", {}) is True


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
