"""Discord AI 串流回覆管理。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import discord

from core.system.settings import get_float

MessageSender = Callable[[str], Awaitable[discord.Message]]
DISCORD_SAFE_MESSAGE_LIMIT = 1_900


class StreamingResponse:
    """將模型的累積文字節流導向單一 Discord 訊息。"""

    def __init__(self, sender: MessageSender) -> None:
        self._sender = sender
        self._message: discord.Message | None = None
        self._last_content = ""
        self._last_update = 0.0
        # Discord 單則訊息硬上限為 2000；固定預留 100 字元安全空間。
        self._max_length = DISCORD_SAFE_MESSAGE_LIMIT
        self._interval = max(
            0.25,
            get_float("ai.stream_update_interval_seconds", 1.0),
        )

    async def push(self, content: str) -> None:
        """送出首段文字，後續以節流頻率編輯同一則訊息。"""
        if not content or len(content) > self._max_length:
            return

        now = asyncio.get_running_loop().time()
        if self._message is None:
            self._message = await self._sender(content)
            self._last_content = content
            self._last_update = now
            return

        if now - self._last_update < self._interval:
            return
        await self._message.edit(content=content)
        self._last_content = content
        self._last_update = now

    async def reset(self) -> None:
        """模型輪替前移除上一個 attempt 的不完整串流內容。"""
        if self._message is not None:
            try:
                await self._message.delete()
            except discord.HTTPException:
                pass
        self._message = None
        self._last_content = ""
        self._last_update = 0.0

    async def finish(self, content: str) -> bool:
        """
        確保最終文字已發送。

        超過單則安全長度時不再切割成多則 Discord 訊息，避免 Markdown
        code fence 或段落被切壞；改保留第一段預覽並回傳 False，讓呼叫端
        另外附上完整文字檔。
        """
        if not content or not content.strip():
            return False

        if len(content) > self._max_length:
            notice = "\n\n（完整回覆請見下方附件）"
            preview = content[: self._max_length - len(notice)].rstrip() + notice
            if self._message is None:
                self._message = await self._sender(preview)
            elif preview != self._last_content:
                await self._message.edit(content=preview)
            self._last_content = preview
            return False

        if self._message is None:
            self._message = await self._sender(content)
        elif content != self._last_content:
            await self._message.edit(content=content)
        self._last_content = content
        return True
