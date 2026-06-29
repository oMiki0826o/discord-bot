"""
cogs/utility/general.py

職責：
- /ping：顯示 Bot WebSocket 延遲
- /help：分頁顯示所有 Slash Commands（自動分頁，支援 25+ 指令）
- /hi、/hyw：互動問候指令
- /botinfo：顯示 Bot 基本資訊

Modification():

- 修正 /help 因 Slash Commands 數量超過 25 個，
  導致 Discord 回傳 400 error 50035（fields 最多 25 個）的問題
- 新增 HelpView 分頁瀏覽器（discord.ui.View + Button）
- 指令數量超過 FIELDS_PER_PAGE 時自動分頁，並顯示翻頁按鈕
- 單頁時不顯示翻頁按鈕，維持原有簡潔 UI
- 分頁 View 逾時後自動停用按鈕，避免殭屍互動

"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.system.settings import get

# ── 分頁常數 ──────────────────────

# Discord embed field 上限為 25；留 5 個緩衝給群組展開用
_FIELDS_PER_PAGE: int = 20

# ── 分頁 View ──────────────────────

class HelpView(discord.ui.View):
    """
    /help 分頁瀏覽器。

    建立時傳入多個 Embed 頁面；按鈕在首末頁自動停用。
    逾時後所有按鈕停用，避免使用者操作無效互動。
    """

    def __init__(self, pages: list[discord.Embed]) -> None:
        super().__init__(timeout=120)
        self.pages   = pages
        self.current = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """根據目前頁碼更新按鈕可用狀態。"""
        self.btn_prev.disabled = self.current == 0
        self.btn_next.disabled = self.current >= len(self.pages) - 1

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def btn_prev(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.current -= 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current],
            view=self,
        )

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def btn_next(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.current += 1
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=self.pages[self.current],
            view=self,
        )

    async def on_timeout(self) -> None:
        """逾時停用所有按鈕（不主動編輯訊息，避免 interaction 過期錯誤）。"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


# ── 輔助函式 ──────────────────────

def _build_help_pages(
    tree_cmds: list[app_commands.Command | app_commands.Group],
    footer:    str,
) -> list[discord.Embed]:
    """
    將指令清單依 _FIELDS_PER_PAGE 切分為多頁 Embed。

    群組指令（app_commands.Group）展開子指令後以單一 field 呈現；
    一般指令以行內 field 呈現，維持整齊排版。
    """
    # ── 建立 field 清單 ──────────────────────
    fields: list[tuple[str, str, bool]] = []  # (name, value, inline)

    for cmd in sorted(tree_cmds, key=lambda c: c.name):
        if isinstance(cmd, app_commands.Group):
            sub_names = " · ".join(
                f"`/{cmd.name} {s.name}`" for s in cmd.commands
            )
            fields.append((f"/{cmd.name}", sub_names or "（無子指令）", False))
        else:
            fields.append((f"/{cmd.name}", cmd.description or "無說明", True))

    # ── 切分分頁 ──────────────────────
    total_pages = max(1, (len(fields) + _FIELDS_PER_PAGE - 1) // _FIELDS_PER_PAGE)
    pages: list[discord.Embed] = []

    for page_idx, start in enumerate(range(0, len(fields), _FIELDS_PER_PAGE)):
        chunk = fields[start : start + _FIELDS_PER_PAGE]
        embed = discord.Embed(
            title       = f"指令清單（{page_idx + 1}/{total_pages} 頁）",
            description = f"共 {len(fields)} 個頂層指令",
            color       = discord.Color.blurple(),
        )
        for name, value, inline in chunk:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text=footer)
        pages.append(embed)

    return pages


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
        """
        分頁顯示所有 Slash Commands。

        修正：原版每個指令都呼叫 add_field()，當指令總數超過 25 個時，
        Discord 會回傳 400 error 50035（embed fields 不能超過 25 個）。
        現在每頁最多顯示 _FIELDS_PER_PAGE 個指令，超出時顯示翻頁按鈕。
        """
        footer    = get("embed_footer.default", "Firefly Bot")
        tree_cmds = self.bot.tree.get_commands()
        pages     = _build_help_pages(tree_cmds, footer)

        # 單頁時不需要 View，避免顯示無用的翻頁按鈕
        view = HelpView(pages) if len(pages) > 1 else None
        await interaction.response.send_message(
            embed   = pages[0],
            view    = view,
            ephemeral = True,
        )

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