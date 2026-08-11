"""
tests/test_ai_multimodal_flow.py

Modification():
- context_manager.build() 現在會 await get_user_info() /
  get_global_memories() / state_to_prompt() / profile_to_prompt() /
  extend_state()（因應 database/repository/user_repository.py 全面
  套用 utils.async_db.to_thread 的連鎖影響，core/ai/user_context.py
  這幾個函式都改為 async def）。原本用純同步 lambda 模擬這幾個
  monkeypatch 目標，await 一個非 coroutine 的回傳值會直接拋出
  TypeError。改用 async def 函式模擬。
- fake_memory_search 同樣改為 async def：context_manager.build()
  原本透過 loop.run_in_executor() 呼叫同步的 memory_search()，
  因此先前用一般同步函式模擬即可；core/ai/memory_manager.py 的
  search() 全面非同步化後，context_manager.py 移除了這層
  run_in_executor 包裝，改為直接
  asyncio.create_task(memory_search(...))，這要求 memory_search(...)
  呼叫後立即回傳 coroutine 物件，同步函式的模擬不再適用。
- 覆蓋 context_manager 參數順序、ranker 型別防護與 Gemini contents 組裝。
- 確認 audio / video / binary parser 已註冊進 file_parser registry。

職責：
- 防止 generate(files=..., image_parts=...) 與 channel_id 相關介面再次回歸。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types

import core.ai.context_manager as context_manager
from core.ai.agent_router import RouteDecision
from core.ai.core import _build_contents
from core.ai.file_parser import audio_parser, binary_parser, video_parser
from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.registry import get_parser
from core.ai.models import MODELS
from core.ai.ranker import rank_memories


# ── Context 參數順序 ──────────────────────

def test_context_build_passes_channel_id_before_query(monkeypatch):
    captured: dict[str, object] = {}
    global_mems = [("global", "全域記憶", 5)]
    parsed = ParsedFile(
        filename="note.txt",
        extension=".txt",
        category="text",
        size_bytes=4,
        content="測試附件",
    )

    async def fake_memory_search(user_id, channel_id, query, memories):
        captured["args"] = (user_id, channel_id, query, memories)
        return SimpleNamespace(
            memories=[("kw", "content", 1)],
            messages=[("user", "history")],
            recent=[("assistant", "recent")],
            summary="summary",
        )

    async def fake_get_user_info(user_id, username):
        return {
            "user_id": user_id,
            "username": username,
            "tier": 0,
            "tier_name": "測試",
            "interaction_count": 0,
        }

    async def fake_get_global_memories():
        return global_mems

    async def fake_state_to_prompt(user_id):
        return ""

    async def fake_profile_to_prompt(user_id):
        return ""

    async def fake_extend_state(user_id):
        return None

    monkeypatch.setattr(context_manager, "memory_search", fake_memory_search)
    monkeypatch.setattr(context_manager, "get_user_info", fake_get_user_info)
    monkeypatch.setattr(context_manager, "get_global_memories", fake_get_global_memories)
    monkeypatch.setattr(context_manager, "state_to_prompt", fake_state_to_prompt)
    monkeypatch.setattr(context_manager, "profile_to_prompt", fake_profile_to_prompt)
    monkeypatch.setattr(context_manager, "extend_state", fake_extend_state)

    bundle = asyncio.run(
        context_manager.build(
            user_id="user-1",
            username="Miki",
            channel_id="channel-1",
            clean="請分析附件",
            injection_detected=False,
            route=RouteDecision(model=MODELS["flash"], use_search=False),
            files=[parsed],
        )
    )

    assert captured["args"] == ("user-1", "channel-1", "請分析附件", global_mems)
    assert bundle.files == [parsed]
    assert bundle.messages == [("user", "history")]


# ── Ranker 型別防護 ──────────────────────

def test_ranker_accepts_non_string_query_without_crashing():
    result = rank_memories(
        ["python", "debug"],
        [("python", "debug tips", 2), ("music", "song", 5)],
    )

    assert result[0][0] == "python"


# ── Gemini Contents ──────────────────────

def test_build_contents_includes_image_parts_for_gemini():
    part = types.Part.from_bytes(data=b"fake-image", mime_type="image/png")

    contents = _build_contents(
        model=MODELS["flash"],
        system_prompt="system",
        final_prompt="請分析圖片",
        image_parts=[part],
    )

    assert isinstance(contents, list)
    assert contents[0] == "請分析圖片"
    assert contents[1] is part


def test_build_contents_keeps_gemma_text_only():
    part = types.Part.from_bytes(data=b"fake-image", mime_type="image/png")

    contents = _build_contents(
        model=MODELS["gemma"],
        system_prompt="system",
        final_prompt="hello",
        image_parts=[part],
    )

    assert contents == "system\n\nhello"


# ── Parser Registry ──────────────────────

def test_registry_enables_audio_video_and_binary_parsers():
    assert get_parser(".mp3") is audio_parser.parse
    assert get_parser(".mp4") is video_parser.parse
    assert get_parser(".exe") is binary_parser.parse
