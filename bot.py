"""
bot.py

職責：
- 定義 FireflyBot（commands.Bot 子類別）
- setup_hook：動態載入所有 Cog、執行啟動預熱
- on_ready：從 settings.json 設定 Discord 狀態
- sync_slash / refresh_presence 供管理指令呼叫

Modification():

- 整合自 ai-bot-optimized/bot.py
- 狀態設定改由 settings.json 控制（status_type/status_text/presence）
- 新增 refresh_presence() 供 $settings reload 即時套用

"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

import config
from core.logging.log             import LogManager
from core.system.extension_loader import ExtensionLoader
from core.system.startup_registry import run_warmup
from core.system.settings         import get

logger = LogManager().get_logger("bot")

_ACTIVITY_TYPE: dict[str, discord.ActivityType] = {
    "playing":   discord.ActivityType.playing,
    "listening": discord.ActivityType.listening,
    "watching":  discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}

_STATUS_MAP: dict[str, discord.Status] = {
    "online":    discord.Status.online,
    "idle":      discord.Status.idle,
    "dnd":       discord.Status.dnd,
    "invisible": discord.Status.invisible,
}


def _build_presence() -> tuple[discord.Activity | None, discord.Status]:
    atype_str = get("bot.status_type", "listening")
    atext     = get("bot.status_text", "/play | @我")
    presence  = get("bot.presence",    "online")
    atype     = _ACTIVITY_TYPE.get(atype_str, discord.ActivityType.listening)
    status    = _STATUS_MAP.get(presence, discord.Status.online)
    activity  = discord.Activity(type=atype, name=atext) if atext else None
    return activity, status


class FireflyBot(commands.Bot):
    """Firefly Discord Bot 主類別。"""

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
        self._loader = ExtensionLoader(self)

    async def setup_hook(self) -> None:
        logger.info("Bot 啟動中...")
        results = run_warmup()
        failed  = [r for r in results if not r.success]
        if failed:
            logger.warning("預熱失敗：%s", [r.module for r in failed])

        loaded, errors = await self._loader.load_all(
            packages  = list(config.EXTENSION_PACKAGES),
            blacklist = config.EXTENSION_BLACKLIST,
            excluded  = config.EXCLUDED_DIRS,
        )
        logger.info("已載入 %d 個 Cog，失敗 %d 個", len(loaded), len(errors))

    async def on_ready(self) -> None:
        logger.info("已登入：%s（ID: %s）", self.user, self.user.id)
        # 狀態由 cogs/events/status.py 的 on_ready 套用

    async def sync_slash(self, guild: discord.Guild | None = None) -> list:
        if guild:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        logger.info("Slash Commands 已同步（共 %d 個）", len(synced))
        return synced

    async def refresh_presence(self) -> None:
        """從 settings.json 重新套用 Discord 狀態，供 reload 後呼叫。"""
        activity, status = _build_presence()
        await self.change_presence(activity=activity, status=status)
        logger.info("[bot] 狀態已重新套用")


async def main() -> None:
    async with FireflyBot() as bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
