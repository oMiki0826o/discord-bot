"""
core/logging/log.py

Modification():
- 修正檔頭路徑：原本寫成 bot/core/logging/log.py，但專案根目錄下
  沒有 bot/ 這層資料夾（entry point 是根目錄的 bot.py，不是資料夾），
  實際路徑是 core/logging/log.py。

- 修正第三方套件 DEBUG 雜訊灌爆 log 檔案的問題：實測發現使用者上傳
  一份 PDF 讓 markitdown 解析時，底層 pdfminer.six 會記錄極其詳細的
  逐一 token DEBUG 訊息（例如「nexttoken: (1342485, 0)」這種等級），
  單一份 PDF 就能產生超過 20 萬行、佔整份 log 檔案 99.63% 的體積，
  把整份 16.7MB 的 log 灌到幾乎只剩雜訊，真正有意義的錯誤訊息被
  淹沒在裡面。根本原因：root logger 被設為 DEBUG，而 pdfminer 等
  第三方套件的 logger 沒有各自設定層級，於是全部繼承 root 的 DEBUG，
  毫無保留地把內部除錯訊息全部寫進我們的 log 檔案。
  原本的作法（_DISCORD_LOGGERS）只窄範圍地列出 discord.py 的幾個
  logger 名稱來壓制，這次改用更通用的做法：root logger 預設改為
  WARNING（涵蓋「所有」第三方套件，不限於 discord.py 或 pdfminer），
  我們自己的 logger 才明確依命名空間逐一設回 DEBUG。這樣未來不管
  再新增哪個第三方依賴（例如日後幫 markitdown 加裝
  audio-transcription extra、或任何其他函式庫），一律自動被壓制在
  WARNING，不需要每次發現新的雜訊來源就回來這裡加一行。
  _DISCORD_LOGGERS／_DISCORD_LOG_LEVEL 因此成為多餘的特例（root
  預設值已經涵蓋 discord.py），一併移除。

- 新增 log 檔案輪替：原本用 logging.FileHandler，單一 log 檔案沒有
  任何大小上限，若 Bot 長時間不重啟持續運作，理論上會無限成長
  （即使已經修正上面提到的第三方套件雜訊問題，長時間運作累積下來
  仍可能是個問題）。改用 logging.handlers.RotatingFileHandler，
  超過 LOG_ROTATE_MAX_BYTES（20MB）就自動切到新檔案，最多保留
  LOG_ROTATE_BACKUP_COUNT（5）份輪替後的舊檔，避免磁碟空間被無上限
  佔用。
- Singleton 保護、handler 重複掛載防護維持不變
- attach_bot() 保持冪等（同一 bot 不重複掛載 DiscordErrorHandler）

"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from .constants import (
    DATE_FORMAT,
    LOG_DIR,
    LOG_FILE,
    LOG_FORMAT,
    LOG_ROTATE_BACKUP_COUNT,
    LOG_ROTATE_MAX_BYTES,
)
from .discord_error_handler import DiscordErrorHandler, send_shutdown_report

if TYPE_CHECKING:
    from discord.ext import commands

# ── 我們自己的 logger 根命名空間 ──────────────────────
# 專案內所有自訂 logger 都落在下列其中一個命名空間之下（見專案內
# logging.getLogger(...) / LogManager().get_logger(...) 的實際呼叫）。
# root logger 預設為 WARNING 之後，只有明確列在這裡的命名空間會被
# 設回 DEBUG；新增「我們自己的」全新根命名空間時才需要在這裡補一行，
# 新增第三方依賴不需要修改此清單。
_OUR_LOG_ROOTS: tuple[str, ...] = (
    "bot",
    "startup",
    "startup_registry",
    "extension_loader",
    "cogs",
)


# ── 全域 Log 管理器（Singleton） ──────────────────────

class LogManager:
    """
    全域 Log 管理器（Singleton）。

    整個 process 生命週期內只存在一份實例，
    root logger 的 handler 只會被初始化一次。

    使用方式：
        log_manager = LogManager()
        logger = log_manager.get_logger("bot.music")
    """

    _instance:    LogManager | None = None
    _initialized: bool              = False

    def __new__(cls) -> LogManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        # ── 已初始化則直接返回，避免重複 setup ──────────────────────
        if self._initialized:
            return

        LogManager._initialized = True
        self._bot:        commands.Bot | None = None
        self._had_errors: bool                = False
        self._start_time: float               = time.monotonic()

        self._setup_logging()

    # ── logging 初始化（全域僅執行一次） ──────────────────────

    def _setup_logging(self) -> None:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

        root = logging.getLogger()

        # ── 防止重複掛載（掛在 root logger 物件上的旗標） ──────────────────────
        if getattr(root, "_logmanager_initialized", False):
            return
        root._logmanager_initialized = True  # type: ignore[attr-defined]

        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

        # ── 終端輸出 handler ──────────────────────
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        # ── 檔案持久化 handler（依大小輪替，避免長時間運作下無上限成長） ──────────────────────
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes    = LOG_ROTATE_MAX_BYTES,
            backupCount = LOG_ROTATE_BACKUP_COUNT,
            encoding    = "utf-8",
        )
        file_handler.setFormatter(formatter)

        # ── error 追蹤 handler（標記本次是否有 ERROR） ──────────────────────
        tracker = _ErrorTracker(self)
        tracker.setLevel(logging.ERROR)

        # root 預設為 WARNING：涵蓋所有第三方套件（見檔頭 Modification
        # 說明），我們自己的 logger 再依 _OUR_LOG_ROOTS 逐一設回 DEBUG。
        root.setLevel(logging.WARNING)
        root.addHandler(stream_handler)
        root.addHandler(file_handler)
        root.addHandler(tracker)

        for name in _OUR_LOG_ROOTS:
            logging.getLogger(name).setLevel(logging.DEBUG)

        # ── 寫入本次 session 的啟動標頭 ──────────────────────
        self._write_session_header()

    # ── session 啟動標頭 ──────────────────────

    def _write_session_header(self) -> None:
        """
        每次 Bot 啟動時寫入一段標頭到 log，方便日後快速定位每次啟動的邊界。

        格式範例：
            ================================================
            Bot Session 開始
            時間    : 2026-06-29 20:08:46
            Python  : 3.11.13
            discord : 2.7.1
            OS      : macOS-14.5
            PID     : 12345
            ================================================
        """
        sep     = "=" * 48
        now_str = datetime.now().strftime(DATE_FORMAT)
        header  = (
            f"\n{sep}\n"
            f"Bot Session 開始\n"
            f"  時間        : {now_str}\n"
            f"  Python      : {platform.python_version()}\n"
            f"  discord.py  : {discord.__version__}\n"
            f"  OS          : {platform.system()}-{platform.release()}\n"
            f"  PID         : {os.getpid()}\n"
            f"{sep}"
        )

        # ── 寫入檔案（不透過 logging，避免格式被污染） ──────────────────────
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(header + "\n")
        except OSError:
            pass

        # ── 同步輸出到終端 ──────────────────────
        print(header)

    # ── 取得 logger ──────────────────────

    def get_logger(self, name: str = "bot") -> logging.Logger:
        return logging.getLogger(name)

    # ── 綁定 bot（掛載 DiscordErrorHandler） ──────────────────────

    def attach_bot(self, bot: commands.Bot) -> None:
        """
        掛載 DiscordErrorHandler 到 root logger。
        冪等：同一 bot 實例不會重複掛載。
        必須在 setup_hook 中呼叫，確保 bot.loop 存在。
        """
        self._bot = bot
        root      = logging.getLogger()

        if not any(isinstance(h, DiscordErrorHandler) for h in root.handlers):
            root.addHandler(DiscordErrorHandler(bot))
            logging.getLogger("bot").info("DiscordErrorHandler 已掛載")

    # ── 發送關機報告 ──────────────────────

    async def send_shutdown_report(self) -> None:
        """由 FireflyBot.close() 呼叫，向 Owner 私訊本次運行摘要。"""
        if not self._bot:
            return
        await send_shutdown_report(self._bot, self._had_errors)


# ── 內部 error 追蹤器 ──────────────────────

class _ErrorTracker(logging.Handler):
    """
    監聽 ERROR 以上等級的 log，標記 LogManager._had_errors。
    供關機報告判斷本次運行是否有錯誤。
    """

    def __init__(self, manager: LogManager) -> None:
        super().__init__()
        self._manager = manager

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            self._manager._had_errors = True