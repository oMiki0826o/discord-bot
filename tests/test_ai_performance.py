"""AI 回覆速度優化的回歸測試。"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from google.genai import types

import core.ai.attachment_utils as attachment_utils
import core.ai.core as ai_core
import core.ai.streaming_response as streaming_module
from core.ai.streaming_response import StreamingResponse
from core.system import event_bus


def test_event_bus_handler_error_is_visible(caplog) -> None:
    async def broken_handler(**_kwargs):
        raise RuntimeError("event handler exploded")

    with caplog.at_level(logging.ERROR, logger="bot.system.event_bus"):
        asyncio.run(event_bus._safe_call(broken_handler, "test_event"))

    assert "handler=broken_handler" in caplog.text
    assert "event handler exploded" in caplog.text


def test_streaming_call_emits_accumulated_text(monkeypatch) -> None:
    class Models:
        async def generate_content_stream(self, **_kwargs):
            async def chunks():
                yield SimpleNamespace(text="hello ")
                yield SimpleNamespace(text="world")
            return chunks()

    seen: list[str] = []

    async def on_chunk(text: str) -> None:
        seen.append(text)

    monkeypatch.setattr(ai_core, "client", SimpleNamespace(aio=SimpleNamespace(models=Models())))
    monkeypatch.setattr(ai_core, "get_float", lambda *_args: 5.0)

    text, _response = asyncio.run(
        ai_core._call(
            "model-a",
            "prompt",
            types.GenerateContentConfig(),
            on_chunk=on_chunk,
        )
    )

    assert text == "hello world"
    assert seen == ["hello ", "hello world"]


def test_model_pool_stops_at_configured_attempt_limit(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_try_generate(model, *_args, **_kwargs):
        calls.append(model)
        return None

    monkeypatch.setattr(
        ai_core, "get_model_candidates", lambda *_args: ("a", "b", "c", "d"),
    )
    monkeypatch.setattr(ai_core, "_try_generate", fake_try_generate)
    monkeypatch.setattr(
        ai_core,
        "get_int",
        lambda path, default=0: 2 if path == "ai.max_model_attempts" else default,
    )

    result, _model = asyncio.run(
        ai_core._try_model_pool(
            category="gemini",
            preferred="a",
            prompt="hello",
            user_id="u1",
            system_prompt="system",
            use_search=False,
        )
    )

    assert result is None
    assert calls == ["a", "b"]


def test_discord_stream_updates_one_message_and_flushes_final_text(monkeypatch) -> None:
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    sender = AsyncMock(return_value=message)
    monkeypatch.setattr(streaming_module, "get_float", lambda *_args: 60.0)

    async def run() -> None:
        response = StreamingResponse(sender)
        await response.push("A")
        await response.push("AB")
        assert await response.finish("ABC") is True

    asyncio.run(run())

    sender.assert_awaited_once_with("A")
    message.edit.assert_awaited_once_with(content="ABC")


def test_response_within_discord_safe_limit_stays_in_one_message(monkeypatch) -> None:
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    sender = AsyncMock(return_value=message)
    monkeypatch.setattr(streaming_module, "get_float", lambda *_args: 60.0)

    async def run() -> None:
        response = StreamingResponse(sender)
        await response.push("開頭")
        assert await response.finish("段落。" * 300) is True

    asyncio.run(run())

    sender.assert_awaited_once_with("開頭")
    message.edit.assert_awaited_once()


def test_very_long_response_keeps_preview_for_attachment_fallback(monkeypatch) -> None:
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    sender = AsyncMock(return_value=message)
    monkeypatch.setattr(streaming_module, "get_float", lambda *_args: 60.0)

    async def run() -> None:
        response = StreamingResponse(sender)
        await response.push("預覽")
        assert await response.finish("很長。" * 700) is False

    asyncio.run(run())

    message.delete.assert_not_awaited()
    preview = message.edit.await_args.kwargs["content"]
    assert len(preview) <= streaming_module.DISCORD_SAFE_MESSAGE_LIMIT
    assert "完整回覆請見下方附件" in preview


def test_model_retry_resets_previous_stream(monkeypatch) -> None:
    calls: list[str] = []
    reset = AsyncMock()

    async def fake_try_generate(model, *_args, **_kwargs):
        calls.append(model)
        return None if model == "a" else "ok"

    monkeypatch.setattr(ai_core, "get_model_candidates", lambda *_args: ("a", "b"))
    monkeypatch.setattr(ai_core, "_try_generate", fake_try_generate)
    monkeypatch.setattr(
        ai_core,
        "get_int",
        lambda path, default=0: 2 if path == "ai.max_model_attempts" else default,
    )

    result, _model = asyncio.run(ai_core._try_model_pool(
        category="gemini",
        preferred="a",
        prompt="hello",
        user_id="u1",
        system_prompt="system",
        use_search=False,
        on_retry=reset,
    ))

    assert result == "ok"
    assert calls == ["a", "b"]
    reset.assert_awaited_once_with()


def test_attachments_are_processed_with_bounded_parallelism(monkeypatch) -> None:
    active = 0
    peak = 0

    async def fake_read(attachment, _ext):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return attachment.filename

    monkeypatch.setattr(attachment_utils, "read_image_part", fake_read)
    monkeypatch.setattr(
        attachment_utils,
        "get_int",
        lambda path, default=0: 2 if path == "ai.attachment_concurrency" else 5,
    )
    attachments = [
        SimpleNamespace(filename=f"{index}.png")
        for index in range(4)
    ]

    files, images = asyncio.run(attachment_utils.process_attachments(attachments))

    assert files == []
    assert images == ["0.png", "1.png", "2.png", "3.png"]
    assert peak == 2


def test_background_tasks_can_be_drained_before_shutdown() -> None:
    completed: list[bool] = []

    async def run() -> None:
        async def work() -> None:
            await asyncio.sleep(0)
            completed.append(True)

        event_bus.create_background_task(work(), name="test_background_work")
        await event_bus.drain(timeout=1.0)

    asyncio.run(run())

    assert completed == [True]
