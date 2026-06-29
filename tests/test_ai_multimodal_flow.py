"""
tests/test_ai_multimodal_flow.py

Modification():
- 測試 AI 多模態與附件資料流，不呼叫 Gemini API。
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

    def fake_memory_search(user_id, channel_id, query, memories):
        captured["args"] = (user_id, channel_id, query, memories)
        return SimpleNamespace(
            memories=[("kw", "content", 1)],
            messages=[("user", "history")],
            recent=[("assistant", "recent")],
            summary="summary",
        )

    monkeypatch.setattr(context_manager, "memory_search", fake_memory_search)
    monkeypatch.setattr(
        context_manager,
        "get_user_info",
        lambda user_id, username: {
            "user_id": user_id,
            "username": username,
            "tier": 0,
            "tier_name": "測試",
            "interaction_count": 0,
        },
    )
    monkeypatch.setattr(context_manager, "get_global_memories", lambda: global_mems)
    monkeypatch.setattr(context_manager, "state_to_prompt", lambda user_id: "")
    monkeypatch.setattr(context_manager, "profile_to_prompt", lambda user_id: "")
    monkeypatch.setattr(context_manager, "extend_state", lambda user_id: None)

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
