"""AI Prompt 顯示開關與 Discord 紀錄的回歸測試。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import core.ai.prompt_logging as prompt_logging
import cogs.system.owner as owner_module
from cogs.system.owner import Owner


def test_prompt_log_command_enables_setting(monkeypatch) -> None:
    writes: list[tuple[str, object]] = []
    ctx = SimpleNamespace(send=AsyncMock(), author="owner")
    monkeypatch.setattr(
        owner_module,
        "write_value",
        lambda path, value: writes.append((path, value)),
    )
    monkeypatch.setattr(owner_module, "_prompt_logging_core_is_active", lambda: True)

    asyncio.run(
        Owner.ai_show_prompt.callback(Owner(SimpleNamespace()), ctx, "true")
    )

    assert writes == [("ai.show_prompt", True)]
    assert "`True`" in ctx.send.await_args.args[0]


def test_prompt_log_command_warns_when_running_core_is_stale(monkeypatch) -> None:
    ctx = SimpleNamespace(send=AsyncMock(), author="owner")
    monkeypatch.setattr(owner_module, "write_value", lambda *_args: None)
    monkeypatch.setattr(owner_module, "_prompt_logging_core_is_active", lambda: False)

    asyncio.run(
        Owner.ai_show_prompt.callback(Owner(SimpleNamespace()), ctx, "true")
    )

    message = ctx.send.await_args.args[0]
    assert "完整重啟 Bot" in message
    assert "$bot_reload" in message


def test_short_prompt_is_shown_inline_without_mentions(monkeypatch) -> None:
    channel = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        fetch_channel=AsyncMock(),
    )
    prompt_logging.set_prompt_log_client(bot)
    monkeypatch.setattr(prompt_logging, "get_int", lambda *_args: 1550078091949506622)

    asyncio.run(
        prompt_logging.send_prompt_to_discord(
            user_id="123",
            username="測試者",
            source_channel_id="456",
            model="gemini-test",
            system_prompt="system",
            final_prompt="hello",
        )
    )

    content = channel.send.await_args.args[0]
    assert "SYSTEM PROMPT" in content
    assert "FINAL PROMPT" in content
    assert "hello" in content
    assert channel.send.await_args.kwargs["allowed_mentions"].everyone is False
    bot.fetch_channel.assert_not_awaited()


def test_long_prompt_is_sent_as_complete_attachment(monkeypatch) -> None:
    channel = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        fetch_channel=AsyncMock(),
    )
    prompt_logging.set_prompt_log_client(bot)
    monkeypatch.setattr(prompt_logging, "get_int", lambda *_args: 1550078091949506622)
    long_prompt = "長" * 2_000

    asyncio.run(
        prompt_logging.send_prompt_to_discord(
            user_id="123",
            username="測試者",
            source_channel_id="456",
            model="gemini-test",
            system_prompt="system",
            final_prompt=long_prompt,
        )
    )

    sent_file = channel.send.await_args.kwargs["file"]
    sent_file.fp.seek(0)
    attachment_text = sent_file.fp.read().decode("utf-8")
    assert long_prompt in attachment_text
    assert sent_file.filename == "prompt-123.txt"


def test_prompt_logging_redacts_credentials() -> None:
    text = prompt_logging.redact_prompt(
        "Authorization: Bearer abc123\napi_key=secret-value"
    )

    assert "abc123" not in text
    assert "secret-value" not in text
    assert text.count("已遮罩") == 2


def test_prompt_logging_omits_attachment_and_private_memory_contents() -> None:
    text = prompt_logging.redact_prompt(
        "<attachment_content>private file</attachment_content>\n\n"
        "=== 使用者長期記憶參考 ===\nprivate memory\n\n"
        "=== 目前訊息 ===\nhello"
    )

    assert "private file" not in text
    assert "private memory" not in text
    assert "hello" in text


def test_prompt_logging_omits_channel_message_contents() -> None:
    text = prompt_logging.redact_prompt(
        "<channel_context>private channel text</channel_context>\n"
        "=== 目前訊息 ===\nhello"
    )

    assert "private channel text" not in text
    assert "頻道訊息原文未記錄" in text
    assert "hello" in text
