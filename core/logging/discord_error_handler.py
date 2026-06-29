"""
bot/core/logging/discord_error_handler.py

Modification():

- 新增 _is_noise()：過濾 ffmpeg、yt-dlp、asyncio、discord 已知例外，避免 Owner 收到無意義私訊
- emit() 新增 bot.is_ready() 防護：bot 尚未就緒時不嘗試發送 DM
- send_shutdown_report() 改用 logger 取代 print()，輸出統一
- _get_owner() 改為快取結果，避免每次 error 都呼叫 application_info()

"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback

import discord

from .constants import LOG_FILE, LOG_MAX_BYTES
from .traceback_utils import split_traceback

# ── 模組 logger ──────────────────────
log = logging.getLogger(__name__)

# ── 已知噪音：logger 名稱前綴 ──────────────────────
# 這些 logger 的 ERROR 通常是 discord.py 內部或網路問題，不需要通知 Owner
_NOISE_LOGGER_PREFIXES: tuple[str, ...] = (
    "discord.gateway",
    "discord.http",
    "discord.client",
    "discord.voice_client",
)

# ── 已知噪音：訊息關鍵字（小寫比對）──────────────────────
# 包含這些關鍵字的 error 通常是 ffmpeg / yt-dlp 一時性問題
_NOISE_MESSAGE_KEYWORDS: tuple[str, ...] = (
    "ffmpeg",
    "yt-dlp",
    "ytdl",
    "youtube",
    "video unavailable",
    "sign in to confirm",
)

# ── 已知噪音：例外類別 ──────────────────────
_NOISE_EXC_TYPES: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    discord.HTTPException,
    discord.ConnectionClosed,
    discord.GatewayNotFound,
)


# ── 噪音判斷 ──────────────────────

def _is_noise(record: logging.LogRecord) -> bool:
    """
    判斷此 log record 是否屬於已知噪音，不需要發送 DM 給 Owner。

    噪音定義：
    1. logger 名稱屬於 discord 內部模組
    2. 訊息包含 ffmpeg / yt-dlp 相關關鍵字
    3. 例外類別屬於已知可忽略類型
    """
    # ── 檢查 logger 名稱前綴 ──────────────────────
    if any(record.name.startswith(prefix) for prefix in _NOISE_LOGGER_PREFIXES):
        return True

    # ── 檢查訊息關鍵字 ──────────────────────
    msg_lower = record.getMessage().lower()
    if any(kw in msg_lower for kw in _NOISE_MESSAGE_KEYWORDS):
        return True

    # ── 檢查例外類別 ──────────────────────
    if record.exc_info and record.exc_info[1] is not None:
        if isinstance(record.exc_info[1], _NOISE_EXC_TYPES):
            return True

    return False


# ── 即時錯誤通報 handler ──────────────────────

class DiscordErrorHandler(logging.Handler):
    """
    攔截 ERROR 以上等級的 log，過濾噪音後私訊通知 Bot Owner。

    attach_bot() 必須在 setup_hook 中呼叫，
    確保 bot.loop 存在且 bot 已開始運行。
    """

    def __init__(self, bot: discord.Client) -> None:
        super().__init__(level=logging.ERROR)
        self.bot = bot
        # ── 快取 owner，避免每次 error 都查詢 application_info ──────────────────────
        self._owner: discord.User | None = None

    # ── logging.Handler 介面 ──────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        # ── bot 尚未就緒時不發送（setup_hook 期間的錯誤先放行至檔案）──────────────────────
        if not self.bot.is_ready():
            return

        # ── 過濾已知噪音 ──────────────────────
        if _is_noise(record):
            return

        try:
            asyncio.run_coroutine_threadsafe(self._send(record), self.bot.loop)
        except Exception:
            log.warning("無法排程錯誤通報任務", exc_info=True)

    # ── 實際發送私訊 ──────────────────────

    async def _send(self, record: logging.LogRecord) -> None:
        owner = await self._get_owner()
        if owner is None:
            return

        header = f"[{record.levelname}] {record.name}\n{record.getMessage()}"
        await owner.send(header[:2000])

        # ── 附上 traceback（分段發送）──────────────────────
        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info))
            for chunk in split_traceback(tb):
                await owner.send(chunk)

    # ── 取得 bot owner（快取）──────────────────────

    async def _get_owner(self) -> discord.User | None:
        if self._owner is not None:
            return self._owner
        try:
            app          = await self.bot.application_info()
            self._owner  = app.owner
            return self._owner
        except Exception:
            log.warning("無法取得 bot owner", exc_info=True)
            return None


# ── 關機報告 ──────────────────────

async def send_shutdown_report(bot: discord.Client, had_errors: bool) -> None:
    """
    Bot 關閉時私訊 Owner，報告本次運行是否有錯誤。
    若有錯誤且 log 檔案未超過大小限制，附上完整 log。
    """
    # ── 取得 owner_id ──────────────────────
    owner_id: int | None = getattr(bot, "owner_id", None)
    if owner_id is None:
        try:
            app      = await bot.application_info()
            owner_id = app.owner.id
        except Exception:
            log.warning("無法取得 owner 以發送關機報告", exc_info=True)
            return

    # ── 取得 owner 使用者物件 ──────────────────────
    try:
        owner = await bot.fetch_user(owner_id)
    except Exception:
        log.warning("無法取得 owner 使用者物件", exc_info=True)
        return

    # ── 無錯誤：簡短通知 ──────────────────────
    if not had_errors:
        await owner.send("本次運行期間無任何錯誤")
        log.info("關機報告：本次運行無錯誤")
        return

    # ── 有錯誤：附上 log 檔案 ──────────────────────
    log_size = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0

    if log_size > LOG_MAX_BYTES:
        await owner.send(
            f"本次運行期間發生錯誤。"
            f"log 檔案過大（{log_size / 1024 / 1024:.1f} MB），不附加檔案。"
        )
        log.info("關機報告：發生錯誤，log 檔案過大（%.1f MB）", log_size / 1024 / 1024)
    else:
        with open(LOG_FILE, "rb") as f:
            await owner.send(
                "本次運行期間發生錯誤，附上完整 log：",
                file=discord.File(f, filename=os.path.basename(LOG_FILE)),
            )
        log.info("關機報告：發生錯誤，已附上 log（%.1f MB）", log_size / 1024 / 1024)


# ── setup（此模組非 cog，僅供 load_extension 相容）──────────────────────

async def setup(bot: discord.Client) -> None:
    pass