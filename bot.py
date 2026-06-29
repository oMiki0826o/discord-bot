"""
bot/bot.py

Modification():

- setup_hook 新增 attach_bot()，確保 DiscordErrorHandler 正確掛載
- setup_hook 新增 sync_slash()，啟動時自動同步 Slash Commands 並輸出數量
- setup_hook 新增 ready_event，供 Cog 內背景任務等待初始化完成
- on_ready 新增啟動摘要（伺服器數、使用者數、啟動耗時）
- close() 新增優雅關閉：輸出「Bot 關閉中...」、送出關機報告
- __main__ 攔截 KeyboardInterrupt，改為輸出「Bot 關閉中...」取代 traceback
- 移除 help_command=None 硬編碼，改由 settings.json 控制前綴

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

# ── 全域 LogManager（Singleton，整個 process 只建立一次）──────────────────────
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


# ── 從 settings.json 組合 presence ──────────────────────

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
            command_prefix = get("bot.command_prefix", "$"),
            intents        = intents,
            owner_id       = config.OWNER_ID or None,
        )

        self.log_manager                 = log_manager
        self.ready_event: asyncio.Event  = asyncio.Event()
        self._startup_time: float        = time.monotonic()
        self._loader                     = ExtensionLoader(self)

    # ── setup_hook ──────────────────────

    async def setup_hook(self) -> None:
        logger.info("setup_hook 開始")

        # ── 同步預載（在執行緒中執行，不阻塞 event loop）──────────────────────
        await asyncio.to_thread(initialize)

        # ── 預熱核心模組 ──────────────────────
        results = run_warmup()
        failed  = [r for r in results if not r.success]
        if failed:
            logger.warning("預熱失敗模組: %s", [r.module for r in failed])

        # ── 掛載 Discord 錯誤通報 handler（必須在此處呼叫）──────────────────────
        # 若放到外部呼叫，可能因時機不對而使 DiscordErrorHandler 無法收到訊息
        self.log_manager.attach_bot(self)

        # ── 載入所有 Cog extension ──────────────────────
        loaded, errors = await self._loader.load_all(
            packages  = list(config.EXTENSION_PACKAGES),
            blacklist = config.EXTENSION_BLACKLIST,
            excluded  = config.EXCLUDED_DIRS,
        )
        logger.info("Cog 載入完成 | 成功=%d 失敗=%d", len(loaded), len(errors))

        # ── 自動同步 Slash Commands ──────────────────────
        # 在 setup_hook 同步可確保指令在 on_ready 時已可使用
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

    # ── sync_slash（供管理指令手動呼叫）──────────────────────

    async def sync_slash(self, guild: discord.Guild | None = None) -> list:
        """手動同步 Slash Commands，供 $slash / $slash_guild 指令呼叫。"""
        if guild:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        logger.info("Slash Commands 已同步 | %d 個指令", len(synced))
        return synced

    # ── refresh_presence（供 $settings reload 呼叫）──────────────────────

    async def refresh_presence(self) -> None:
        """從 settings.json 重新套用 Discord 狀態。"""
        activity, status = _build_presence()
        await self.change_presence(activity=activity, status=status)
        logger.info("Bot 狀態已重新套用")

    # ── close（優雅關閉）──────────────────────

    async def close(self) -> None:
        logger.info("Bot 關閉中...")

        # ── 發送關機報告（限時 10 秒，避免卡死）──────────────────────
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
        # 攔截 Ctrl+C，避免 Python 印出大量 traceback
        # 實際關閉流程已在 FireflyBot.close() 中處理
        print("Bot 關閉中...")
    except Exception:
        logger.exception("Bot 發生未處理例外")