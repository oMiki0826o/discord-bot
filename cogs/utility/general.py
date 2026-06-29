"""
cogs/utility/general.py

職責：
- /ping：顯示 Bot 延遲
- /help：列出所有 Slash Commands 與說明
- /hi、/hyw：互動問候指令
- /botinfo：顯示 Bot 基本資訊（版本、延遲、伺服器數量、啟動時間）

Modification():

- 整合自 Bot-Firefly/cogs/other/test.py 與 cogs/other/info.py
- 新增 /botinfo（提供比 /ping 更完整的系統資訊）
- 類別命名改為 General（PEP 8）
- /help 改為 Embed 顯示，支援群組指令展開

"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.system.settings import get

# Bot 啟動時間（模組載入時記錄）
_START_TIME = time.time()


class General(commands.Cog):
    """一般工具指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /ping ──────────────────────

    @app_commands.command(name="ping", description="測試 Bot 是否在線並顯示延遲")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_ping(self, interaction: discord.Interaction) -> None:
        latency = round(self.bot.latency * 1000)
        embed   = discord.Embed(
            title       = "Pong!",
            description = f"WebSocket 延遲：`{latency} ms`",
            color       = (
                discord.Color.green()  if latency < 100 else
                discord.Color.yellow() if latency < 250 else
                discord.Color.red()
            ),
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await interaction.response.send_message(embed=embed)

    # ── /botinfo ──────────────────────

    @app_commands.command(name="botinfo", description="顯示 Bot 基本資訊")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_botinfo(self, interaction: discord.Interaction) -> None:
        uptime  = int(time.time() - _START_TIME)
        h, rem  = divmod(uptime, 3600)
        m, s    = divmod(rem, 60)
        latency = round(self.bot.latency * 1000)

        embed = discord.Embed(
            title     = self.bot.user.name,
            color     = discord.Color.blurple(),
            timestamp = datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="延遲",     value=f"`{latency} ms`",                    inline=True)
        embed.add_field(name="伺服器數", value=f"`{len(self.bot.guilds)}`",           inline=True)
        embed.add_field(name="上線時間", value=f"`{h}h {m}m {s}s`",                  inline=True)
        embed.add_field(
            name  = "AI 角色",
            value = f"`{get('ai.persona_name', 'Firefly')}`",
            inline=True,
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await interaction.response.send_message(embed=embed)

    # ── /help ──────────────────────

    @app_commands.command(name="help", description="顯示所有可用的 Slash Commands")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_help(self, interaction: discord.Interaction) -> None:
        tree_cmds = self.bot.tree.get_commands()

        embed = discord.Embed(
            title       = "指令清單",
            description = f"共 {len(tree_cmds)} 個頂層指令",
            color       = discord.Color.blurple(),
        )

        for cmd in sorted(tree_cmds, key=lambda c: c.name):
            if isinstance(cmd, app_commands.Group):
                sub_names = " · ".join(
                    f"`/{cmd.name} {s.name}`" for s in cmd.commands
                )
                embed.add_field(
                    name   = f"/{cmd.name}",
                    value  = sub_names or "（無子指令）",
                    inline = False,
                )
            else:
                desc = cmd.description or "無說明"
                embed.add_field(name=f"/{cmd.name}", value=desc, inline=True)

        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /hi ──────────────────────

    @app_commands.command(name="hi", description="向 Bot 打招呼")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_hi(self, interaction: discord.Interaction) -> None:
        name = get("ai.persona_name", "流螢")
        await interaction.response.send_message(
            f"早ㄤ，{interaction.user.mention}！我是 {name}。Ciallo (∠·ω )⌒ ☆"
        )

    # ── /hyw ──────────────────────

    @app_commands.command(name="hyw", description="何意味")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_hyw(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("何意味")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
