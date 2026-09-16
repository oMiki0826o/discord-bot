"""
tests/test_ai_chat_mentions.py

職責：
- 驗證 AI mention 入口可用設定開關控制是否允許其他 Bot／應用呼叫。
- 驗證 AI mention 入口仍會忽略自己的訊息，避免回覆循環。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import cogs.ai.chat as chat_module
from cogs.ai.chat import Chat


class FakeBot:
    def __init__(self) -> None:
        self.user = SimpleNamespace(id=100)

    async def get_context(self, _message: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(valid=False)


def _message(*, author_id: int, author_is_bot: bool, bot_user: object) -> SimpleNamespace:
    return SimpleNamespace(
        author=SimpleNamespace(id=author_id, bot=author_is_bot),
        content="<@100> 請回應",
        attachments=[],
        mentions=[bot_user],
    )


def test_other_application_can_call_ai_by_mention_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_bool", lambda *_args: True)
    bot = FakeBot()
    cog = Chat(bot)
    calls: list[tuple[object, str]] = []

    async def fake_handle_ai(message: object, prompt: str) -> None:
        calls.append((message, prompt))

    cog.handle_ai = fake_handle_ai
    message = _message(author_id=200, author_is_bot=True, bot_user=bot.user)

    asyncio.run(cog.on_message(message))

    assert calls == [(message, "請回應")]


def test_other_application_is_ignored_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_bool", lambda *_args: False)
    bot = FakeBot()
    cog = Chat(bot)
    calls: list[tuple[object, str]] = []

    async def fake_handle_ai(message: object, prompt: str) -> None:
        calls.append((message, prompt))

    cog.handle_ai = fake_handle_ai
    message = _message(author_id=200, author_is_bot=True, bot_user=bot.user)

    asyncio.run(cog.on_message(message))

    assert calls == []


def test_regular_user_can_call_ai_when_other_applications_are_disabled(monkeypatch) -> None:
    monkeypatch.setattr(chat_module, "get_bool", lambda *_args: False)
    bot = FakeBot()
    cog = Chat(bot)
    calls: list[tuple[object, str]] = []

    async def fake_handle_ai(message: object, prompt: str) -> None:
        calls.append((message, prompt))

    cog.handle_ai = fake_handle_ai
    message = _message(author_id=200, author_is_bot=False, bot_user=bot.user)

    asyncio.run(cog.on_message(message))

    assert calls == [(message, "請回應")]


def test_ai_ignores_its_own_mention_message() -> None:
    bot = FakeBot()
    cog = Chat(bot)
    calls: list[tuple[object, str]] = []

    async def fake_handle_ai(message: object, prompt: str) -> None:
        calls.append((message, prompt))

    cog.handle_ai = fake_handle_ai
    message = _message(author_id=bot.user.id, author_is_bot=True, bot_user=bot.user)

    asyncio.run(cog.on_message(message))

    assert calls == []
