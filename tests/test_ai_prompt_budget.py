"""AI Prompt 預算、去重與記憶工具非同步呼叫回歸測試。"""

from __future__ import annotations

import asyncio

import core.ai.memory_manager as memory_manager
import core.ai.user_context as user_context
from core.ai.context_manager import ContextBundle
from core.ai.memory_manager import MemoryBundle
from core.ai.prompt_builder import build
from core.ai.tool_registry import _exec_memory


def _bundle(**overrides) -> ContextBundle:
    values = {
        "user_input": "這是最新問題，必須保留",
        "user_info": {
            "user_id": "1",
            "username": "tester",
            "tier_name": "朋友",
            "tier": 2,
            "interaction_count": 3,
        },
        "max_length": 1_000,
    }
    values.update(overrides)
    return ContextBundle(**values)


def test_prompt_budget_always_keeps_latest_user_input_at_end() -> None:
    prompt = build(_bundle(
        tool_sections=["=== 工具 ===\n" + "T" * 4_000],
        messages=[("user", "M" * 4_000)],
        recent=[("assistant", "R" * 4_000)],
    ))

    assert len(prompt) <= 1_000
    assert prompt.endswith("User: 這是最新問題，必須保留\nAI:")


def test_prompt_deduplicates_messages_that_are_also_recent() -> None:
    prompt = build(_bundle(
        messages=[("user", "同一句"), ("assistant", "較舊回答")],
        recent=[("user", "同一句"), ("user", "同一句")],
    ))

    assert prompt.count("user: 同一句") == 1


def test_overlong_latest_input_keeps_both_ends() -> None:
    prompt = build(_bundle(
        user_input="開頭" + "X" * 2_000 + "結尾",
        max_length=1_000,
    ))

    assert len(prompt) == 1_000
    assert "開頭" in prompt
    assert "結尾" in prompt
    assert "內容已截斷" in prompt


def test_memory_tool_awaits_async_search(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    async def fake_global_memories():
        return []

    async def fake_search(user_id, channel_id, query, _global):
        calls.append((user_id, channel_id, query))
        return MemoryBundle(
            memories=[("偏好", "喜歡安靜", 3)],
            messages=[], recent=[], summary="", background=[],
        )

    monkeypatch.setattr(user_context, "get_global_memories", fake_global_memories)
    monkeypatch.setattr(memory_manager, "search", fake_search)

    result = asyncio.run(_exec_memory("u1", "c1", "你記得嗎"))

    assert calls == [("u1", "c1", "你記得嗎")]
    assert "喜歡安靜" in result
