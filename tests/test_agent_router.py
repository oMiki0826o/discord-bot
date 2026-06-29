"""
tests/test_agent_router.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

測試 core.ai.agent_router 的「純規則」路由邏輯：
- 模型選擇（使用者指定 > 搜尋需求 > 內容判斷 > 預設）
- 不測試 execute_tools()，因為它會實際呼叫 memory_manager /
  user_context（牽涉 DB 與 Gemini Client），工具「決策」的部分
  已交由 tool_registry.select_tools() 負責，於
  test_tool_registry.py 單獨測試
"""

from __future__ import annotations

from core.ai.agent_router import route
from core.ai.models import DEFAULT_MODEL, GROUNDING_MIN_MODEL, MODELS


def test_default_greeting_uses_default_model_no_search():
    decision = route("你好")
    assert decision.model == DEFAULT_MODEL
    assert decision.use_search is False


def test_search_keyword_upgrades_to_grounding_model():
    decision = route("幫我查一下今天天氣")
    assert decision.use_search is True
    assert decision.model == GROUNDING_MIN_MODEL


def test_user_override_flash_without_search():
    decision = route("用flash 幫我寫一首詩")
    assert decision.model == MODELS["flash"]
    assert decision.use_search is False


def test_user_override_gemma_with_search_gets_upgraded():
    """
    gemma-4-31b-it 不是 Gemini 系列（is_gemini() 為 False），
    當本次請求同時需要搜尋時，即使使用者明確指定 gemma，
    仍應自動升級為支援 Grounding 的模型。
    """
    decision = route("用gemma 幫我查一下最新匯率")
    assert decision.use_search is True
    assert decision.model == GROUNDING_MIN_MODEL


def test_user_override_gemini_lite_with_search_not_upgraded():
    """
    gemini-3.1-flash-lite 本身就是 Gemini 系列（is_gemini() 為 True），
    依現行規則不會被再升級，維持使用者指定的模型。
    """
    decision = route("用gemini 幫我查一下最新股價")
    assert decision.use_search is True
    assert decision.model == MODELS["lite"]


def test_pro_keyword_routes_to_flash_without_search():
    decision = route("這段python程式可以幫我除錯嗎")
    assert decision.model == MODELS["flash"]
    assert decision.use_search is False


def test_short_prompt_has_no_tools():
    decision = route("嗨")
    assert decision.tools == []


def test_route_decision_needs_helper():
    decision = route("幫我記得我之前說過的事情並且詳細分析一下")
    assert decision.needs("memory") == ("memory" in decision.tools)
