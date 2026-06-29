"""
core/logging/log.py

修正：
- Singleton 模式避免 LogManager 重複初始化
- root logger 加上旗標保護，避免 handler 重複疊加（log 重複輸出根因修正）
- attach_bot 避免 DiscordErrorHandler 重複掛載
- 新增 log_extension_loaded()，供 extension_loader 逐條輸出載入結果
- 壓制 discord 內部 logger 至 WARNING，避免 WebSocket 封包噴滿終端
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import DATE_FORMAT, LOG_DIR, LOG_FILE, LOG_FORMAT
from .discord_error_handler import DiscordErrorHandler, send_shutdown_report

if TYPE_CHECKING:
    from discord.ext import commands

# ── discord 內部噪音壓制層級 ──────────────────────
_DISCORD_LOG_LEVEL = logging.WARNING

# ── 受壓制的 discord 子 logger ──────────────────────
_DISCORD_LOGGERS = (
    "discord",
    "discord.http",
    "discord.gateway",
    "discord.client",
)


# ── 全域 Log 管理器（Singleton）──────────────────────
class LogManager:
    """
    全域 Log 管理器（Singleton）。

    保證整個 process 生命週期內只存在一份實例，
    root logger 的 handler 只會被新增一次。
    """

    _instance: LogManager | None = None
    _initialized: bool = False

    def __new__(cls) -> LogManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # ── 已初始化則直接返回，避免重複 setup ──────────────────────
        if self._initialized:
            return

        LogManager._initialized = True
        self._bot: commands.Bot | None = None
        self._had_errors: bool = False

        self._setup_logging()

    # ── logging 初始化（全域僅執行一次）──────────────────────
    def _setup_logging(self) -> None:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()

        # ── 防止重複掛載旗標（掛在 root logger 物件上）──────────────────────
        if getattr(root, "_logmanager_initialized", False):
            return
        root._logmanager_initialized = True  # type: ignore[attr-defined]

        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

        # ── stream handler（終端輸出）──────────────────────
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        # ── file handler（檔案持久化）──────────────────────
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)

        # ── error tracker（標記本次運行是否發生錯誤）──────────────────────
        tracker = _ErrorTracker(self)
        tracker.setLevel(logging.ERROR)

        root.setLevel(logging.DEBUG)
        root.addHandler(stream_handler)
        root.addHandler(file_handler)
        root.addHandler(tracker)

        # ── 壓制 discord 內部噪音 ──────────────────────
        for name in _DISCORD_LOGGERS:
            logging.getLogger(name).setLevel(_DISCORD_LOG_LEVEL)

    # ── 取得 logger ──────────────────────
    def get_logger(self, name: str = "bot") -> logging.Logger:
        return logging.getLogger(name)

    # ── 綁定 bot（避免 DiscordErrorHandler 重複掛載）──────────────────────
    def attach_bot(self, bot: commands.Bot) -> None:
        self._bot = bot
        root = logging.getLogger()

        if not any(isinstance(h, DiscordErrorHandler) for h in root.handlers):
            root.addHandler(DiscordErrorHandler(bot))

    # ── extension 載入結果逐條輸出 ──────────────────────
    def log_extension_loaded(self, module_name: str, *, success: bool) -> None:
        """
        供 extension_loader 逐條呼叫，於終端明確列印每個 cog 的載入結果。

        格式範例：
            [INFO]  extension_loader: 載入成功: cogs.ai.chat
            [ERROR] extension_loader: 載入失敗: cogs.ai.owner
        """
        logger = self.get_logger("extension_loader")
        if success:
            logger.info("載入成功: %s", module_name)
        else:
            logger.error("載入失敗: %s", module_name)

    # ── 發送關機報告 ──────────────────────
    async def send_shutdown_report(self) -> None:
        if not self._bot:
            return
        await send_shutdown_report(self._bot, self._had_errors)


# ── 內部 error 追蹤器 ──────────────────────
class _ErrorTracker(logging.Handler):
    """監聽 ERROR 以上等級的 log，標記 LogManager._had_errors 供關機報告使用。"""

    def __init__(self, manager: LogManager) -> None:
        super().__init__()
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self._manager._had_errors = True
