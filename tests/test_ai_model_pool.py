"""測試 AI 三類模型池、settings 可調整性與失敗輪替。"""

from __future__ import annotations

import asyncio

import core.ai.core as ai_core
import core.ai.models as ai_models


def test_builtin_model_pools_include_all_requested_models():
    assert ai_models.DEFAULT_MODEL_POOLS == {
        "gemini": (
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
        ),
        "flash": (
            "gemini-2.5-flash",
            "gemini-3-flash-preview",
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3.7-flash",
            "gemini-3.8-flash",
        ),
        "gemma": ("gemma-4-31b-it",),
    }


def test_default_category_comes_from_settings(monkeypatch):
    monkeypatch.setattr(ai_models, "get_str", lambda *_args: "flash")
    assert ai_models.get_default_category() == "flash"


def test_model_pool_comes_from_settings_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        ai_models,
        "get_list",
        lambda *_args: ["model-a", "model-a", "", "model-b"],
    )
    assert ai_models.get_model_pool("gemini") == ("model-a", "model-b")


def test_quota_exhaustion_rotates_to_next_model(monkeypatch):
    calls: list[str] = []

    async def fake_try_generate(model, *_args, **_kwargs):
        calls.append(model)
        if model == "model-a":
            return ai_core._QUOTA_EXHAUSTED
        return "ok"

    ai_core._MODEL_QUOTA_UNTIL.clear()
    monkeypatch.setattr(
        ai_core, "get_model_candidates", lambda *_args: ("model-a", "model-b"),
    )
    monkeypatch.setattr(ai_core, "_try_generate", fake_try_generate)
    monkeypatch.setattr(ai_core, "get_int", lambda *_args: 300)

    result, used_model = asyncio.run(
        ai_core._try_model_pool(
            category="gemini",
            preferred="model-a",
            prompt="hello",
            user_id="user-1",
            system_prompt="system",
            use_search=False,
        )
    )

    assert result == "ok"
    assert used_model == "model-b"
    assert calls == ["model-a", "model-b"]
    assert "model-a" in ai_core._MODEL_QUOTA_UNTIL

