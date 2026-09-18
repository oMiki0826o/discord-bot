"""AI 回覆期間的 Discord typing 降級處理。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

import discord

logger = logging.getLogger("bot.ai.typing")


@asynccontextmanager
async def optional_typing(channel: discord.abc.Messageable) -> AsyncIterator[bool]:
    """嘗試顯示 typing；Discord 拒絕存取時仍繼續執行 AI 請求。

    使用者安裝的 App 指令可能在 Bot 未加入、或 Bot 看不到的
    伺服器頻道裡執行。Interaction 本身仍可回覆，但頻道 typing
    REST API 會回傳 50001 Missing Access；typing 只是顯示效果，
    不應因此中止整個 AI 回覆。
    """
    indicator = channel.typing()
    try:
        await indicator.__aenter__()
    except discord.HTTPException as exc:
        logger.warning(
            "[typing.unavailable] channel=%s status=%s code=%s reason=%s",
            getattr(channel, "id", None),
            getattr(exc, "status", None),
            getattr(exc, "code", None),
            exc,
        )
        yield False
        return

    try:
        yield True
    finally:
        await indicator.__aexit__(None, None, None)


__all__ = ["optional_typing"]
