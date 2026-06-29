"""
cogs/system/owner.py

職責：
- Owner 專用指令（$game、$slash）
- $game <文字>：即時更改 Bot 遊玩狀態，並寫入 settings.json 使設定持久化
- $slash：同步 Slash Commands 至全域或指定伺服器

Modification():

- $game 改為同時更新 settings.json（bot.status_type / bot.status_text），
  確保 $settings reload 後不會被覆蓋
- 移除冗餘的 import，改用 core.system.settings.write_value()

"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.logging.log    import LogManager
from core.system.settings import get, write_value

logger = LogManager().get_logger("cogs.system.owner")

# 所有可選的 status_type 供 $game 使用
_VALID_TYPES = frozenset({"playing", "listening", "watching", "competing"})


class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── $game ──────────────────────

    @commands.command(name="game", hidden=True)
    @commands.is_owner()
    async def set_game(
        self,
        ctx:  commands.Context,
        *,
        name: str,
    ) -> None:
        """
        $game <文字>          → 設定為「收聽」狀態（預設）
        $game playing <文字>  → 設定為「遊玩」狀態
        $game watching <文字> → 設定為「觀看」狀態
        $game competing <文字>→ 設定為「競賽」狀態

        設定同時持久化至 settings.json，$settings reload 後仍生效。
        """
        # ── 解析可選的 type 前綴 ──────────────────────
        parts      = name.split(None, 1)
        status_type = get("bot.status_type", "listening")
        status_text = name

        if len(parts) >= 2 and parts[0].lower() in _VALID_TYPES:
            status_type = parts[0].lower()
            status_text = parts[1]

        # ── 立即套用 ──────────────────────
        _type_map = {
            "playing":   discord.ActivityType.playing,
            "listening": discord.ActivityType.listening,
            "watching":  discord.ActivityType.watching,
            "competing": discord.ActivityType.competing,
        }
        activity = discord.Activity(
            type = _type_map.get(status_type, discord.ActivityType.listening),
            name = status_text,
        )
        await self.bot.change_presence(activity=activity)

        # ── 持久化至 settings.json ──────────────────────
        try:
            write_value("bot.status_type", status_type)
            write_value("bot.status_text", status_text)
        except Exception as e:
            logger.warning("[owner.$game] 寫入 settings.json 失敗: %s", e)
            await ctx.send(
                f"狀態已更改為：`{status_text}`（類型：{status_type}）\n"
                f"注意：settings.json 寫入失敗（{e}），重啟後可能不會保留"
            )
            return

        await ctx.send(
            f"狀態已更改為：`{status_text}`（類型：{status_type}）\n"
            "設定已寫入 settings.json，重啟後仍然生效"
        )
        logger.info("[owner.$game] type=%s text=%s by=%s", status_type, status_text, ctx.author)

    # ── $slash ──────────────────────

    @commands.command(name="slash", hidden=True)
    @commands.is_owner()
    async def slash(self, ctx: commands.Context) -> None:
        """$slash — 同步 Slash Commands 至全域（最多 1 小時生效）"""
        synced = await self.bot.tree.sync()
        cmd_names = "\n".join(f"  /{c.name}" for c in self.bot.tree.get_commands())
        await ctx.send(
            f"已同步 {len(synced)} 個 Slash Commands\n"
            f"```\n{cmd_names or '（無）'}\n```"
        )
        logger.info("[owner.$slash] 同步 %d 個指令 by %s", len(synced), ctx.author)

    # ── $slash_guild ──────────────────────

    @commands.command(name="slash_guild", hidden=True)
    @commands.is_owner()
    async def slash_guild(self, ctx: commands.Context) -> None:
        """$slash_guild — 即時同步 Slash Commands 至當前伺服器（測試用）"""
        guild  = ctx.guild
        if not guild:
            await ctx.send("此指令僅限在伺服器中使用")
            return
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await ctx.send(f"已即時同步 {len(synced)} 個 Slash Commands 至 **{guild.name}**")
        logger.info("[owner.$slash_guild] 同步 %d 個指令至 %s by %s", len(synced), guild.name, ctx.author)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Owner(bot))
