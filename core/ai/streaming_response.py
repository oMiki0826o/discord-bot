"""Discord AI 串流回覆管理。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import discord

from core.system.settings import get_float, get_int

MessageSender = Callable[[str], Awaitable[discord.Message]]


class StreamingResponse:
    """將模型的累積文字節流導向單一 Discord 訊息。"""

    def __init__(self, sender: MessageSender) -> None:
        self._sender = sender
        self._message: discord.Message | None = None
        self._last_content = ""
        self._last_update = 0.0
        # Discord 單則訊息硬上限為 2000；預留空間給 code fence 與提示。
        self._max_length = min(
            1_900,
            max(200, get_int("ai.max_reply_length", 1500)),
        )
        self._max_chunks = max(1, get_int("ai.long_reply_max_chunks", 4))
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

        可容納的長文會按自然邊界拆成數則訊息；超過安全訊息數時保留
        第一段預覽並回傳 False，讓呼叫端另外附上完整文字檔。
        """
        if not content or not content.strip():
            return False

        chunks = split_discord_text(content, self._max_length)
        if len(chunks) > self._max_chunks:
            notice = "\n\n（完整回覆請見下方附件）"
            preview = content[: self._max_length - len(notice)].rstrip() + notice
            if self._message is None:
                self._message = await self._sender(preview)
            elif preview != self._last_content:
                await self._message.edit(content=preview)
            self._last_content = preview
            return False

        if len(chunks) > 1:
            first, *remaining = chunks
            if self._message is None:
                self._message = await self._sender(first)
            elif first != self._last_content:
                await self._message.edit(content=first)
            for chunk in remaining:
                await self._sender(chunk)
            self._last_content = first
            return True

        if self._message is None:
            self._message = await self._sender(content)
        elif content != self._last_content:
            await self._message.edit(content=content)
        self._last_content = content
        return True


def split_discord_text(text: str, limit: int) -> list[str]:
    """優先在段落、換行或空白處拆分 Discord 長文。"""
    if not text:
        return []
    limit = max(1, limit)
    chunks: list[str] = []
    remaining = text.strip()

    while len(remaining) > limit:
        window = remaining[: limit + 1]
        minimum = max(1, limit // 2)
        cut = -1
        for boundary in ("\n\n", "\n", "。", "！", "？", ". ", " "):
            position = window.rfind(boundary, minimum)
            if position >= 0:
                cut = position + len(boundary)
                break
        if cut <= 0:
            cut = limit
        chunk = remaining[:cut].rstrip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks
