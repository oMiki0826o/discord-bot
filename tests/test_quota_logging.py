"""Regression tests for provider cooldown parsing and visible AI error logs."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from google.genai import types
from google.genai.errors import ClientError, ServerError

import core.ai.core as ai_core
import core.ai.memory_manager as memory_manager
import core.ai.quota_manager as quota_manager


def test_retry_delay_parser_rounds_up_provider_delay():
    error = "429 RESOURCE_EXHAUSTED retryDelay': '21.288505456s'"
    assert quota_manager.retry_delay_seconds(error) == 22
    assert quota_manager.error_status_code(error) == 429


def test_quota_cooldown_uses_provider_retry_delay(monkeypatch):
    quota_manager.MODEL_QUOTA_UNTIL.clear()
    monkeypatch.setattr(quota_manager.time, "monotonic", lambda: 100.0)

    delay = quota_manager.mark_quota_exhausted_from_error(
        "model-a",
        "429 RESOURCE_EXHAUSTED. Please retry in 21.2s.",
    )

    assert delay == 22
    assert quota_manager.MODEL_QUOTA_UNTIL["model-a"] == 122.0


def test_only_one_background_request_per_model_runs_at_once():
    entered = asyncio.Event()
    release = asyncio.Event()
    allowed: list[bool] = []

    async def first():
        async with quota_manager.background_request("model-a") as can_run:
            allowed.append(can_run)
            entered.set()
            await release.wait()

    async def second():
        await entered.wait()
        async with quota_manager.background_request("model-a") as can_run:
            allowed.append(can_run)
        release.set()

    async def run():
        await asyncio.gather(first(), second())

    asyncio.run(run())
    assert allowed == [True, False]


def test_frontend_429_log_includes_status_and_retry_delay(monkeypatch, caplog):
    async def fail_call(*_args, **_kwargs):
        raise ClientError(
            429,
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Please retry in 3.2s.",
                }
            },
        )

    recorded: list[str] = []
    monkeypatch.setattr(ai_core, "_call", fail_call)
    monkeypatch.setattr(
        ai_core, "record_error", lambda error_type, *_args: recorded.append(error_type),
    )
    ai_core._MODEL_QUOTA_UNTIL.clear()

    with caplog.at_level(logging.WARNING, logger="bot.ai.core"):
        result = asyncio.run(
            ai_core._try_generate(
                "model-a",
                "prompt",
                types.GenerateContentConfig(),
                "user-1",
                "system",
            )
        )

    assert result is ai_core._QUOTA_EXHAUSTED
    assert "status=429" in caplog.text
    assert "retry_after=4s" in caplog.text
    assert recorded == ["quota_exceeded"]


def test_frontend_503_is_logged_and_recorded(monkeypatch, caplog):
    async def fail_call(*_args, **_kwargs):
        raise ServerError(
            503,
            {
                "error": {
                    "code": 503,
                    "status": "UNAVAILABLE",
                    "message": "high demand",
                }
            },
        )

    recorded: list[str] = []
    monkeypatch.setattr(ai_core, "_call", fail_call)
    monkeypatch.setattr(
        ai_core, "record_error", lambda error_type, *_args: recorded.append(error_type),
    )

    with caplog.at_level(logging.WARNING, logger="bot.ai.core"):
        result = asyncio.run(
            ai_core._try_generate(
                "model-a",
                "prompt",
                types.GenerateContentConfig(),
                "user-1",
                "system",
                max_retries=1,
            )
        )

    assert result is None
    assert "status=503" in caplog.text
    assert "exhausted" in caplog.text
    assert recorded == ["server_error_503"]


def test_frontend_503_retries_immediately(monkeypatch):
    calls = 0

    async def flaky_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ServerError(
                503,
                {"error": {"code": 503, "status": "UNAVAILABLE"}},
            )
        return "ok", SimpleNamespace(usage_metadata=None)

    async def forbidden_sleep(*_args, **_kwargs):
        raise AssertionError("immediate retry must not sleep")

    monkeypatch.setattr(ai_core, "_call", flaky_call)
    monkeypatch.setattr(ai_core, "record_usage", lambda **_kwargs: None)
    monkeypatch.setattr(ai_core.asyncio, "sleep", forbidden_sleep)
    reset_stream = AsyncMock()

    result = asyncio.run(
        ai_core._try_generate(
            "model-a",
            "prompt",
            types.GenerateContentConfig(),
            "user-1",
            "system",
            max_retries=2,
            on_retry=reset_stream,
        )
    )

    assert result == "ok"
    assert calls == 2
    reset_stream.assert_awaited_once_with()


def test_memory_errors_are_not_silent(monkeypatch, caplog):
    async def fail_embed(_text):
        raise RuntimeError("vector database response was invalid")

    monkeypatch.setattr(memory_manager, "_call_embed", fail_embed)

    with caplog.at_level(logging.ERROR, logger="bot.memory_manager"):
        result = asyncio.run(memory_manager._embed("hello"))

    assert result is None
    assert "operation=embed" in caplog.text
    assert "vector database response was invalid" in caplog.text


def test_memory_background_job_logs_repository_errors(monkeypatch, caplog):
    async def fail_extract(*_args):
        raise RuntimeError("memory repository unavailable")

    monkeypatch.setattr(memory_manager, "_extract", fail_extract)

    with caplog.at_level(logging.ERROR, logger="bot.memory_manager"):
        asyncio.run(memory_manager._on_message_generated("user-1", "hello", "hi"))

    assert "operation=background_job" in caplog.text
    assert "memory repository unavailable" in caplog.text
    assert "user-1:" not in memory_manager._memory_jobs_in_progress


def test_memory_busy_job_processes_latest_pending_message(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    extracted: list[str] = []

    async def extract(_user_id, user_msg, _ai_msg):
        extracted.append(user_msg)
        if user_msg == "first":
            started.set()
            await release.wait()

    async def no_op(*_args):
        return None

    monkeypatch.setattr(memory_manager, "_extract", extract)
    monkeypatch.setattr(memory_manager, "_summarize_if_needed", no_op)
    monkeypatch.setattr(memory_manager, "_vectorize_recent", no_op)
    memory_manager._memory_jobs_in_progress.clear()
    memory_manager._memory_pending_jobs.clear()

    async def run():
        first = asyncio.create_task(
            memory_manager._on_message_generated(
                "user-1", "first", "reply-1", channel_id="channel-1",
            )
        )
        await started.wait()
        await memory_manager._on_message_generated(
            "user-1", "latest", "reply-2", channel_id="channel-1",
        )
        release.set()
        await first

    asyncio.run(run())

    assert extracted == ["first", "latest"]
