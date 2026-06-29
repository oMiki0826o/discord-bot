"""
core/logging/discord_error_handler.py

修正：
- 補上完整型別註記與檔案標頭說明
- emit() 改用 run_coroutine_threadsafe，避免在非 event loop 執行緒呼叫
  self.bot.loop.create_task() 時拋出例外
- _get_owner、send_shutdown_report 加入 try/except，避免例外向外擴散
"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback

import discord

from .constants import LOG_FILE, LOG_MAX_BYTES
from .traceback_utils import split_traceback

# ── logger ──────────────────────
log = logging.getLogger(__name__)


# ── 即時錯誤通報 handler ──────────────────────
class DiscordErrorHandler(logging.Handler):
    """攔截 ERROR 以上等級的 log，並即時私訊通知 bot owner。"""

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(level=logging.ERROR)
        self.bot = bot

    # ── logging.Handler 介面：可能在非 event loop 執行緒被呼叫 ──────────────────────
    def emit(self, record: logging.LogRecord) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._send(record), self.bot.loop)
        except Exception:
            log.warning("無法排程錯誤通報任務", exc_info=True)

    # ── 實際發送通報訊息 ──────────────────────
    async def _send(self, record: logging.LogRecord) -> None:
        owner = await self._get_owner()
        if owner is None:
            return

        header = f"[{record.levelname}] {record.name}\n{record.getMessage()}"
        await owner.send(header[:2000])

        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info))
            for chunk in split_traceback(tb):
                await owner.send(chunk)

    # ── 取得 bot owner ──────────────────────
    async def _get_owner(self) -> discord.User | None:
        try:
            app = await self.bot.application_info()
            return app.owner
        except Exception:
            log.warning("無法取得 bot owner", exc_info=True)
            return None


# ── 關機報告 ──────────────────────
async def send_shutdown_report(bot: discord.Client, had_errors: bool) -> None:
    """於 bot 關閉時，私訊 owner 本次運行是否發生錯誤，並視情況附上 log 檔案。"""
    owner_id = getattr(bot, "owner_id", None)

    if owner_id is None:
        try:
            app = await bot.application_info()
            owner_id = app.owner.id
        except Exception:
            log.warning("無法取得 owner 以發送關機報告", exc_info=True)
            return

    try:
        owner = await bot.fetch_user(owner_id)
    except Exception:
        log.warning("無法取得 owner 使用者物件", exc_info=True)
        return

    if not had_errors:
        await owner.send("本次運行期間無任何錯誤")
        print("本次運行期間無任何錯誤")
        return

    log_size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0

    if log_size > LOG_MAX_BYTES:
        await owner.send(
            f"本次運行期間發生錯誤。log 檔案超過 50MB ({log_size / 1024 / 1024:.1f}MB)，不附加檔案。"
        )
    else:
        with open(LOG_FILE, "rb") as f:
            await owner.send(
                "本次運行期間發生錯誤，附上完整 log：",
                file=discord.File(f, filename=LOG_FILE),
            )
            print(f"本次運行期間發生錯誤，已附上完整 log ({log_size / 1024 / 1024:.1f}MB)")


# ── extension 進入點（此模組非真正 cog，僅供 load_extension 相容）──────────────────────
async def setup(bot: discord.Client) -> None:
    pass
