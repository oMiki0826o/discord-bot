"""
tests/test_message_events.py

Modification():

- 新增 `$` 前綴指令重複執行的回歸測試。
- 驗證 Messenger 不會再對伺服器訊息呼叫 bot.process_commands()。
- 驗證 Owner 回覆橋接會依轉發映射送回原私訊者。

Description():

- 本檔測試 cogs.events.message 的純事件分流邏輯，不連線 Discord。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cogs.events.message import Messenger


# ── 測試替身 ──────────────────────

class FakeUser:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.sent: list[str] = []

    async def send(self, content: str) -> SimpleNamespace:
        self.sent.append(content)
        return SimpleNamespace(id=len(self.sent))


class FakeBot:
    def __init__(self) -> None:
        self.owner_id = 99
        self.process_calls = 0
        self.owner = FakeUser(99)
        self.recipient = FakeUser(1)

    async def process_commands(self, message: SimpleNamespace) -> None:
        self.process_calls += 1

    def get_user(self, user_id: int) -> FakeUser | None:
        if user_id == self.owner.id:
            return self.owner
        if user_id == self.recipient.id:
            return self.recipient
        return None

    async def fetch_user(self, user_id: int) -> FakeUser:
        user = self.get_user(user_id)
        if user is None:
            raise AssertionError(f"unexpected user_id={user_id}")
        return user


# ── 前綴指令不重複處理 ──────────────────────

def test_guild_message_does_not_call_process_commands() -> None:
    bot = FakeBot()
    cog = Messenger(bot)
    message = SimpleNamespace(
        author=FakeUser(1),
        guild=object(),
        content="$ping",
        attachments=[],
        reference=None,
    )

    asyncio.run(cog.on_message(message))

    assert bot.process_calls == 0


# ── Owner 回覆橋接 ──────────────────────

def test_owner_reply_bridge_forwards_to_original_sender() -> None:
    bot = FakeBot()
    cog = Messenger(bot)
    cog._remember_forward(forward_message_id=123, sender_user_id=bot.recipient.id)

    message = SimpleNamespace(
        author=bot.owner,
        guild=None,
        content="收到，我來處理",
        attachments=[],
        reference=SimpleNamespace(message_id=123),
    )

    handled = asyncio.run(cog._handle_owner_reply(message))

    assert handled is True
    assert bot.recipient.sent == ["**Bot 回覆：**\n收到，我來處理"]
