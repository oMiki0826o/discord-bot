"""
cogs/utility/general.py

Modification():

- 將 /help 改為 Embed 類別選單，每個功能類別各自顯示指令頁面。
- 單一類別內容過多時自動拆頁，並由下拉選單直接選取類別與頁碼。
- Help 選單只允許原始呼叫者操作，逾時後會停用。
- 移除 /hi 問候語中的裝飾符號，維持專案不使用 emoji 的規範。
  修正：上一次的移除沒有清乾淨——訊息尾端仍留著顏文字
  「Ciallo (∠·ω )⌒」，跟檔頭這句「已移除裝飾符號」的說明對不上，
  本次一併清除，讓程式碼實際符合這裡宣告的規範。

職責：

- /ping：顯示 Bot WebSocket 延遲
- /help：依功能類別顯示所有 Slash Commands
- /hi、/hyw：互動問候指令
- /botinfo：顯示 Bot 基本資訊

"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.system.settings import get
from utils.help_menu import HelpEntry, HelpMenuView, HelpPage, build_help_pages

_CATEGORY_NAMES: dict[str, str] = {
    "ai": "AI",
    "guild": "伺服器設定",
    "minecraft": "Minecraft 工具",
    "moderation": "管理工具",
    "music": "音樂",
    "roles": "身份組",
    "system": "系統",
    "talk": "訊息工具",
    "ticket": "工單",
    "utility": "一般工具",
    "voice": "語音頻道",
}


# ── Help 輔助函式 ──────────────────────

def _command_category(command: app_commands.Command | app_commands.Group) -> str:
    """依綁定 Cog 的模組路徑取得穩定的功能類別。"""
    binding = getattr(command, "binding", None)
    if binding is None and isinstance(command, app_commands.Group):
        for child in command.commands:
            binding = getattr(child, "binding", None)
            if binding is not None:
                break

    module_parts = binding.__class__.__module__.split(".") if binding else []
    if len(module_parts) >= 2 and module_parts[0] == "cogs":
        return _CATEGORY_NAMES.get(module_parts[1], module_parts[1].title())
    return getattr(binding, "qualified_name", "未分類")


def _walk_slash_commands(
    command: app_commands.Command | app_commands.Group,
) -> list[app_commands.Command | app_commands.Group]:
    if not isinstance(command, app_commands.Group):
        return [command]

    children: list[app_commands.Command | app_commands.Group] = []
    for child in command.commands:
        children.extend(_walk_slash_commands(child))
    return children or [command]


def _build_slash_help_pages(
    tree_commands: list[app_commands.Command | app_commands.Group],
    footer: str,
) -> list[HelpPage]:
    groups: dict[str, list[HelpEntry]] = {}

    for top_level in tree_commands:
        category = _command_category(top_level)
        for command in _walk_slash_commands(top_level):
            groups.setdefault(category, []).append(
                HelpEntry(
                    name=f"/{command.qualified_name}",
                    description=command.description or "無說明",
                )
            )

    return build_help_pages(
        groups,
        title="Slash 指令說明",
        intro="請使用下方選單切換指令類別。",
        footer=footer,
    )


# ── Bot 啟動時間（模組載入時記錄） ──────────────────────

_START_TIME: float = time.time()


# ── Cog ──────────────────────

class General(commands.Cog):
    """一般工具指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /ping ──────────────────────

    @app_commands.command(name="ping", description="測試 Bot 是否在線並顯示延遲")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_ping(self, interaction: discord.Interaction) -> None:
        latency = round(self.bot.latency * 1000)
        color   = (
            discord.Color.green()  if latency < 100 else
            discord.Color.yellow() if latency < 250 else
            discord.Color.red()
        )
        embed = discord.Embed(
            title       = "Pong!",
            description = f"WebSocket 延遲：`{latency} ms`",
            color       = color,
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
        embed.add_field(name="延遲",     value=f"`{latency} ms`",           inline=True)
        embed.add_field(name="伺服器數", value=f"`{len(self.bot.guilds)}`",  inline=True)
        embed.add_field(name="上線時間", value=f"`{h}h {m}m {s}s`",         inline=True)
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
        """以 Embed 類別選單顯示所有 Slash Commands。"""
        footer    = get("embed_footer.default", "Firefly Bot")
        tree_cmds = self.bot.tree.get_commands()
        pages     = _build_slash_help_pages(tree_cmds, footer)
        view      = HelpMenuView(pages, interaction.user.id)

        await interaction.response.send_message(
            embed     = pages[0].embed,
            view      = view,
            ephemeral = True,
        )
        view.message = await interaction.original_response()

    # ── /hi ──────────────────────

    @app_commands.command(name="hi", description="向 Bot 打招呼")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_hi(self, interaction: discord.Interaction) -> None:
        name = get("ai.persona_name", "流螢")
        await interaction.response.send_message(
            f"早ㄤ，{interaction.user.mention}！我是 {name}。"
        )

    # ── /hyw ──────────────────────

    @app_commands.command(name="hyw", description="何意味")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_hyw(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("何意味")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
