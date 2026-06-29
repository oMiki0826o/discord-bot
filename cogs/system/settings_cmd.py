"""
cogs/system/settings_cmd.py

職責：
- 提供 $settings 系列指令（Owner 專用）
- $settings show [section]  — 以 Embed 顯示目前設定值
- $settings reload          — 強制重載 settings.json 並重新套用 Bot 狀態
- 直接在 Discord 中查閱設定，免開終端機

Modification():

- 全新建立
- 限 Owner 使用（commands.is_owner()）
- reload 後自動呼叫 bot.refresh_presence() 套用新狀態

"""

from __future__ import annotations

import json
import logging

import discord
from discord.ext import commands

from core.system.settings import reload, get_section, all_settings

logger = logging.getLogger("bot.settings_cmd")

# ── 可顯示的 section 清單 ──────────────────────

_SECTIONS = ("bot", "ai", "music", "ticket", "voice_channel", "guild", "moderation", "embed_footer")


class SettingsCmd(commands.Cog, name="Settings"):
    """settings.json 管理指令（Owner 專用）。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── $settings ──────────────────────

    @commands.group(name="settings", aliases=["cfg"], invoke_without_command=True)
    @commands.is_owner()
    async def cmd_settings(self, ctx: commands.Context) -> None:
        """顯示所有可用的子指令。"""
        embed = discord.Embed(
            title       = "Settings 指令",
            description = (
                "`$settings show [section]` — 顯示設定值\n"
                "`$settings reload` — 重載 settings.json"
            ),
            color = discord.Color.blurple(),
        )
        embed.add_field(
            name  = "可用 section",
            value = " / ".join(f"`{s}`" for s in _SECTIONS),
        )
        await ctx.send(embed=embed)

    # ── $settings show ──────────────────────

    @cmd_settings.command(name="show")
    @commands.is_owner()
    async def cmd_show(self, ctx: commands.Context, section: str = "") -> None:
        """
        顯示設定值。
        不指定 section 時顯示所有設定的 JSON 摘要（截斷至 3800 字元）。
        """
        if section and section not in _SECTIONS:
            await ctx.send(
                f"未知 section `{section}`，可用的有：{', '.join(_SECTIONS)}"
            )
            return

        if section:
            data = get_section(section)
            title = f"settings.json → [{section}]"
        else:
            data  = all_settings()
            title = "settings.json（全部）"

        # 過濾 _comment 鍵
        data = {k: v for k, v in data.items() if not str(k).startswith("_")}
        text = json.dumps(data, ensure_ascii=False, indent=2)

        embed = discord.Embed(title=title, color=discord.Color.blurple())

        if len(text) <= 3800:
            embed.description = f"```json\n{text}\n```"
        else:
            # 分段顯示
            chunks = [text[i:i+3800] for i in range(0, len(text), 3800)]
            embed.description = f"```json\n{chunks[0]}\n```"
            await ctx.send(embed=embed)
            for chunk in chunks[1:]:
                await ctx.send(f"```json\n{chunk}\n```")
            return

        await ctx.send(embed=embed)

    # ── $settings reload ──────────────────────

    @cmd_settings.command(name="reload")
    @commands.is_owner()
    async def cmd_reload(self, ctx: commands.Context) -> None:
        """
        強制重新讀取 settings.json，並即時套用 Bot 狀態設定。
        不需要重啟 Bot。
        """
        try:
            reload()
        except Exception as e:
            await ctx.send(f"重載失敗：{e}")
            logger.exception("[settings.reload] 失敗")
            return

        # 重新套用 Bot 狀態
        if hasattr(self.bot, "refresh_presence"):
            try:
                await self.bot.refresh_presence()
            except Exception as e:
                logger.warning("[settings.reload] refresh_presence 失敗: %s", e)

        embed = discord.Embed(
            title       = "settings.json 已重新載入",
            description = "所有設定已更新，Bot 狀態已套用。",
            color       = discord.Color.green(),
        )
        await ctx.send(embed=embed)
        logger.info("[settings.reload] 由 %s 觸發", ctx.author)


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCmd(bot))
