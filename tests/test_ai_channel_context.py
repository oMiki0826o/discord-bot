"""@Bot 頻道短期 Context 擷取與 Prompt 組裝測試。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import cogs.ai.chat as chat_module
from cogs.ai.chat import Chat
from core.ai.context_manager import ContextBundle
from core.ai.prompt_builder import build
from core.ai.token_budget import estimate_tokens


class FakeHistoryChannel:
    id = 456

    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.requested: dict[str, object] = {}

    def history(self, **kwargs):
        self.requested = kwargs

        async def iterator():
            for message in self.messages:
                yield message

        return iterator()


def _history_message(
    message_id: int,
    author_id: int,
    content: str,
    *,
    bot: bool = False,
) -> object:
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(
            id=author_id,
            bot=bot,
            display_name=f"user-{author_id}",
        ),
        content=content,
        attachments=[],
        reference=None,
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
    )


def test_mention_fetches_50_messages_including_other_bots_by_default(monkeypatch) -> None:
    bot_user = SimpleNamespace(id=999, bot=True, display_name="Bot")
    channel = FakeHistoryChannel([
        _history_message(3, 999, "Bot 之前的回覆", bot=True),
        _history_message(2, 777, "其他 Bot", bot=True),
        _history_message(1, 111, "你們覺得呢？"),
    ])
    trigger = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=channel,
    )
    monkeypatch.setattr(chat_module, "get_bool", lambda key, default=False: default)
    monkeypatch.setattr(chat_module, "get_int", lambda key, default=0: default)

    result = asyncio.run(Chat(SimpleNamespace(user=bot_user))._get_channel_context(trigger))

    assert channel.requested["limit"] == 50
    assert channel.requested["before"] is trigger
    assert [item["message_id"] for item in result] == ["1", "2", "3"]
    assert result[0]["author_id"] == "111"
    assert result[1]["role"] == "bot"
    assert result[1]["content"] == "其他 Bot"
    assert result[2]["role"] == "assistant"


def test_channel_context_can_exclude_other_bots(monkeypatch) -> None:
    bot_user = SimpleNamespace(id=999, bot=True, display_name="Bot")
    channel = FakeHistoryChannel([
        _history_message(2, 777, "其他 Bot", bot=True),
        _history_message(1, 111, "人類訊息"),
    ])
    trigger = SimpleNamespace(guild=SimpleNamespace(id=1), channel=channel)

    def fake_get_bool(key: str, default: bool = False) -> bool:
        if key == "ai.channel_context_include_bots":
            return False
        return default

    monkeypatch.setattr(chat_module, "get_bool", fake_get_bool)
    monkeypatch.setattr(chat_module, "get_int", lambda key, default=0: default)

    result = asyncio.run(Chat(SimpleNamespace(user=bot_user))._get_channel_context(trigger))

    assert [item["message_id"] for item in result] == ["1"]


def test_channel_context_is_not_read_for_dm(monkeypatch) -> None:
    channel = FakeHistoryChannel([])
    trigger = SimpleNamespace(guild=None, channel=channel)
    monkeypatch.setattr(chat_module, "get_bool", lambda *_args: True)

    result = asyncio.run(Chat(SimpleNamespace(user=None))._get_channel_context(trigger))

    assert result == []
    assert channel.requested == {}


def test_prompt_keeps_newest_channel_messages_within_token_budget() -> None:
    messages = [
        {
            "message_id": str(index),
            "author_id": str(index),
            "display_name": f"user-{index}",
            "role": "user",
            "created_at": "2026-09-17T00:00:00+00:00",
            "reply_to_message_id": "",
            "content": f"訊息 {index} " + "很長" * 80,
        }
        for index in range(1, 51)
    ]
    bundle = ContextBundle(
        user_input="總結剛剛的對話",
        user_info={
            "user_id": "1",
            "username": "tester",
            "tier_name": "朋友",
            "tier": 2,
            "interaction_count": 3,
        },
        channel_id="456",
        channel_messages=messages,
        channel_context_max_tokens=800,
        max_tokens=2_000,
    )

    prompt = build(bundle)

    assert estimate_tokens(prompt) <= 1_900
    assert "<channel_context>" in prompt
    assert "message_id=50" in prompt
    assert "message_id=1 " not in prompt
    assert prompt.endswith("請直接回覆目前訊息。")
