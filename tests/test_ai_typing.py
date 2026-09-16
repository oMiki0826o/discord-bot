"""驗證 AI 的 mention 與 slash 兩個入口都會在產生回覆期間顯示 typing。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cogs.ai.ai_command as ai_command_module
import cogs.ai.chat as chat_module
from cogs.ai.ai_command import AICommand
from cogs.ai.chat import Chat


class TypingTracker:
    def __init__(self) -> None:
        self.active = False
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> None:
        self.active = True
        self.entered += 1

    async def __aexit__(self, *_args: object) -> None:
        self.active = False
        self.exited += 1


class FakeChannel:
    def __init__(self, tracker: TypingTracker) -> None:
        self.id = 456
        self.tracker = tracker

    def typing(self) -> TypingTracker:
        return self.tracker


def test_mention_ai_uses_typing_until_response_is_sent(monkeypatch) -> None:
    tracker = TypingTracker()
    channel = FakeChannel(tracker)
    message = SimpleNamespace(
        author=SimpleNamespace(id=123),
        attachments=[],
        channel=channel,
        reply=AsyncMock(),
    )

    async def fake_process(_attachments: list[object]) -> tuple[list[object], list[object]]:
        assert tracker.active
        return [], []

    async def fake_generate(**_kwargs: object) -> str:
        assert tracker.active
        return "AI reply"

    monkeypatch.setattr(chat_module, "check_cooldown", lambda _user_id: True)
    monkeypatch.setattr(chat_module, "lock_for", lambda _user_id: asyncio.Lock())
    monkeypatch.setattr(chat_module, "process_attachments", fake_process)
    monkeypatch.setattr(chat_module, "generate", fake_generate)

    asyncio.run(Chat(SimpleNamespace()).handle_ai(message, "hello"))

    assert (tracker.entered, tracker.exited, tracker.active) == (1, 1, False)
    message.reply.assert_awaited_once_with("AI reply")


def test_slash_ai_uses_typing_until_followup_is_sent(monkeypatch) -> None:
    tracker = TypingTracker()
    channel = FakeChannel(tracker)
    response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=789),
        channel=channel,
        channel_id=channel.id,
        response=response,
        followup=followup,
    )

    async def fake_process(_attachments: list[object]) -> tuple[list[object], list[object]]:
        assert tracker.active
        return [], []

    async def fake_generate(**_kwargs: object) -> str:
        assert tracker.active
        return "AI reply"

    monkeypatch.setattr(ai_command_module, "check_cooldown", lambda _user_id: True)
    monkeypatch.setattr(ai_command_module, "lock_for", lambda _user_id: asyncio.Lock())
    monkeypatch.setattr(ai_command_module, "process_attachments", fake_process)
    monkeypatch.setattr(ai_command_module, "generate", fake_generate)

    cog = AICommand(SimpleNamespace())
    asyncio.run(AICommand.ai_command.callback(cog, interaction, "hello"))

    assert (tracker.entered, tracker.exited, tracker.active) == (1, 1, False)
    response.defer.assert_awaited_once_with(thinking=True)
    followup.send.assert_awaited_once_with("AI reply", wait=True)
