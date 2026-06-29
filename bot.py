"""
bot/bot.py

Modification():

- 將前綴改為動態讀取 settings.json，避免改設定後仍需重啟。
- 保留 setup_hook 的初始化、Cog 載入、Slash 同步與 ready_event 通知流程。
- close() 維持優雅關閉，關機報告逾時或失敗時不阻塞 Discord 連線關閉。
- KeyboardInterrupt 僅輸出簡短關閉訊息，避免終端出現不必要 traceback。

Description():

- FireflyBot 是專案的 Discord Bot 入口，負責建立 intents、載入擴充模組、
  同步 Slash Commands、套用 presence，並管理啟動與關閉生命週期。
"""

from __future__ import annotations

import asyncio
import platform
import time
import logging

import discord
from discord.ext import commands

import config
from core.logging.log             import LogManager
from core.system.extension_loader import ExtensionLoader
from core.system.startup_registry import run_warmup
from core.system.settings         import get
from startup                      import initialize

# ── 全域 LogManager（Singleton，整個 process 只建立一次） ──────────────────────
log_manager = LogManager()
logger      = log_manager.get_logger("bot")

# ── 活動類型對照表 ──────────────────────
_ACTIVITY_TYPE: dict[str, discord.ActivityType] = {
    "playing":   discord.ActivityType.playing,
    "listening": discord.ActivityType.listening,
    "watching":  discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}

# ── 狀態對照表 ──────────────────────
_STATUS_MAP: dict[str, discord.Status] = {
    "online":    discord.Status.online,
    "idle":      discord.Status.idle,
    "dnd":       discord.Status.dnd,
    "invisible": discord.Status.invisible,
}


# ── 指令前綴 ──────────────────────

def _dynamic_command_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """每次解析指令時讀取 settings.json，讓前綴設定可以熱更新。"""
    prefix = str(get("bot.command_prefix", "$")).strip() or "$"
    return commands.when_mentioned_or(prefix)(bot, message)


# ── Presence 組合 ──────────────────────

def _build_presence() -> tuple[discord.Activity | None, discord.Status]:
    atype_str = get("bot.status_type", "listening")
    atext     = get("bot.status_text", "/play | @我")
    presence  = get("bot.presence",    "online")
    atype     = _ACTIVITY_TYPE.get(atype_str, discord.ActivityType.listening)
    status    = _STATUS_MAP.get(presence, discord.Status.online)
    activity  = discord.Activity(type=atype, name=atext) if atext else None
    return activity, status


# ── Bot 主體 ──────────────────────

class FireflyBot(commands.Bot):
    """
    Firefly Discord Bot 主類別。

    setup_hook 在登入完成後、on_ready 之前執行，
    是掛載 DiscordErrorHandler、載入 Cog、同步 Slash 的標準時機。

    需要等待初始化完成的背景任務：
        await self.bot.ready_event.wait()
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states    = True
        intents.members         = True
        intents.guilds          = True

        super().__init__(
            command_prefix     = _dynamic_command_prefix,
            intents            = intents,
            owner_id           = config.OWNER_ID or None,
            strip_after_prefix = True,
        )

        self.log_manager                 = log_manager
        self.ready_event: asyncio.Event  = asyncio.Event()
        self._startup_time: float        = time.monotonic()
        self._loader                     = ExtensionLoader(self)

    # ── setup_hook ──────────────────────

    async def setup_hook(self) -> None:
        logger.info("setup_hook 開始")

        # ── 同步預載（在執行緒中執行，不阻塞 event loop） ──────────────────────
        await asyncio.to_thread(initialize)

        # ── 預熱核心模組 ──────────────────────
        results = run_warmup()
        failed  = [r for r in results if not r.success]
        if failed:
            logger.warning("預熱失敗模組: %s", [r.module for r in failed])

        # ── 掛載 Discord 錯誤通報 handler ──────────────────────
        self.log_manager.attach_bot(self)

        # ── 載入所有 Cog extension ──────────────────────
        loaded, errors = await self._loader.load_all(
            packages  = list(config.EXTENSION_PACKAGES),
            blacklist = config.EXTENSION_BLACKLIST,
            excluded  = config.EXCLUDED_DIRS,
        )
        logger.info("Cog 載入完成 | 成功=%d 失敗=%d", len(loaded), len(errors))

        # ── 自動同步 Slash Commands ──────────────────────
        try:
            synced = await self.tree.sync()
            logger.info("Slash Commands 同步完成 | 全域指令 %d 個", len(synced))
        except Exception:
            logger.exception("Slash Commands 同步失敗")

        # ── 通知所有等待初始化的背景任務 ──────────────────────
        self.ready_event.set()
        logger.info("setup_hook 完成")

    # ── on_ready ──────────────────────

    async def on_ready(self) -> None:
        assert self.user is not None

        startup_elapsed = time.monotonic() - self._startup_time
        user_count      = sum(g.member_count or 0 for g in self.guilds)

        # ── 啟動摘要 ──────────────────────
        separator = "=" * 48
        logger.info(separator)
        logger.info("Bot 啟動完成")
        logger.info("  帳號          : %s (%s)", self.user, self.user.id)
        logger.info("  Python        : %s", platform.python_version())
        logger.info("  discord.py    : %s", discord.__version__)
        logger.info("  伺服器數      : %d", len(self.guilds))
        logger.info("  使用者數      : %d", user_count)
        logger.info("  啟動耗時      : %.2f 秒", startup_elapsed)
        logger.info(separator)

    # ── sync_slash（供管理指令手動呼叫） ──────────────────────

    async def sync_slash(self, guild: discord.Guild | None = None) -> list:
        """手動同步 Slash Commands，供 $slash / $slash_guild 指令呼叫。"""
        if guild:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        logger.info("Slash Commands 已同步 | %d 個指令", len(synced))
        return synced

    # ── refresh_presence（供 $settings reload 呼叫） ──────────────────────

    async def refresh_presence(self) -> None:
        """從 settings.json 重新套用 Discord 狀態。"""
        activity, status = _build_presence()
        await self.change_presence(activity=activity, status=status)
        logger.info("Bot 狀態已重新套用")

    # ── close（優雅關閉） ──────────────────────

    async def close(self) -> None:
        logger.info("Bot 關閉中...")

        # ── 發送關機報告（限時 10 秒，避免卡死） ──────────────────────
        log_mgr = getattr(self, "log_manager", None)
        if log_mgr is not None:
            try:
                await asyncio.wait_for(log_mgr.send_shutdown_report(), timeout=10.0)
            except TimeoutError:
                logger.warning("關機報告發送逾時，繼續關閉")
            except Exception:
                logger.exception("關機報告發送失敗")

        await super().close()
        logger.info("Bot 已關閉")


# ── 主要進入點 ──────────────────────

async def main() -> None:
    async with FireflyBot() as bot:
        await bot.start(config.TOKEN)


# ── 執行入口 ──────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot 關閉中...")
    except Exception:
        logger.exception("Bot 發生未處理例外")
