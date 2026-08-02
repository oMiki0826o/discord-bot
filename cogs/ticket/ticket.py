"""
cogs/ticket/ticket.py

Modification():

- 移除「關閉工單」「建立工單」按鈕上的 emoji（原本分別是鎖頭與
  便條紙），改為純文字標籤：專案在別處（例如 general.py 的 /hi
  問候語）已明確採用不使用 emoji 的規範，這裡的按鈕先前沒有跟上，
  是本次健檢一併統一的小地方。
- 全新建立，整合至 firefly-bot 架構
- 工單建立使用 /ticket open，顯示含「關閉工單」按鈕的 Embed
- 關閉按鈕（CloseView）以持久化 View 設計，Bot 重啟後仍可響應
- 冷卻機制與最大開票數限制均從 settings.json 讀取，可熱更新

職責：
- 工單（Ticket）系統，提供 /ticket open / close / add / remove / stats
- 每張工單建立一個私人文字頻道，僅工單建立者與支援身份組可見
- 工單關閉後：若設定封存類別則移入封存，否則刪除頻道
- 使用 Slash Commands + Button UI，提供直觀的操作體驗
"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from core.system.settings import get as _s_get
import database.repository.guild_repository as guild_repo
import database.repository.ticket_repository as ticket_repo

logger = logging.getLogger("bot.ticket")

# ── 冷卻記憶體（重啟後清空，設計意圖如此） ──────────────────────

_user_last_ticket: dict[str, float] = {}


# ── 關閉按鈕 View ──────────────────────

class CloseView(discord.ui.View):
    """
    工單頻道內顯示的「關閉工單」按鈕。
    使用 timeout=None 確保 Bot 重啟後按鈕仍可響應，
    但每次重啟後 bot 屬性需由 Ticket Cog 重新注入。
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label    = "關閉工單",
        style    = discord.ButtonStyle.red,
        custom_id= "ticket:close",
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        """按下關閉按鈕時觸發，與 /ticket close 邏輯共用。"""
        await _close_ticket(interaction)


# ── 工單建立面板 ──────────────────────

class TicketPanel(discord.ui.View):
    """工單建立面板按鈕，放置在指定頻道供使用者點擊開票。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label    = "建立工單",
        style    = discord.ButtonStyle.green,
        custom_id= "ticket:open_panel",
    )
    async def open_panel(
        self,
        interaction: discord.Interaction,
        button:      discord.ui.Button,
    ) -> None:
        """點擊面板按鈕時，引導使用者輸入主題並建立工單。"""
        modal = TicketModal()
        await interaction.response.send_modal(modal)


class TicketModal(discord.ui.Modal, title="建立工單"):
    """彈出視窗，讓使用者填入工單主題。"""

    topic = discord.ui.TextInput(
        label       = "工單主題",
        placeholder = "請簡單描述您的問題或需求（選填）",
        required    = False,
        max_length  = 100,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _open_ticket(interaction, topic=self.topic.value or "")


# ── 工單核心邏輯 ──────────────────────

async def _open_ticket(
    interaction: discord.Interaction,
    topic:       str = "",
) -> None:
    """
    建立工單頻道的核心邏輯。
    由 /ticket open 或 TicketModal.on_submit 呼叫。
    """
    guild   = interaction.guild
    user    = interaction.user
    user_id = str(user.id)

    # ── 冷卻檢查 ──────────────────────
    last = _user_last_ticket.get(user_id, 0.0)
    if time.monotonic() - last < int(_s_get('ticket.cooldown_seconds', 300)):
        remaining = int(int(_s_get('ticket.cooldown_seconds', 300)) - (time.monotonic() - last))
        await interaction.response.send_message(
            f"請等待 {remaining} 秒後再建立工單",
            ephemeral=True,
        )
        return

    # ── 最大開票數檢查 ──────────────────────
    open_tickets = ticket_repo.get_open_tickets_by_user(guild.id, user_id)
    if len(open_tickets) >= int(_s_get('ticket.max_per_user', 1)):
        await interaction.response.send_message(
            f"您已有 {len(open_tickets)} 張開啟中的工單（上限 {int(_s_get('ticket.max_per_user', 1))} 張）",
            ephemeral=True,
        )
        return

    # ── 取得工單序號與類別 ──────────────────────
    ticket_num   = guild_repo.increment_ticket_count(guild.id)
    channel_name = f"{_s_get('ticket.channel_prefix', 'ticket-')}{ticket_num:04d}"
    settings     = guild_repo.get_settings(guild.id)
    category_id  = settings.get("ticket_category_id", 0)
    support_id   = settings.get("ticket_support_role", 0)

    # ── 組裝頻道權限覆蓋 ──────────────────────
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user:               discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
        guild.me:           discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            read_message_history=True,
        ),
    }
    if support_id:
        support_role = guild.get_role(support_id)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    # ── 取得/建立類別 ──────────────────────
    category: discord.CategoryChannel | None = None
    if category_id:
        category = guild.get_channel(category_id)    # type: ignore[assignment]
    if category is None:
        for cat in guild.categories:
            if cat.name == _s_get('ticket.category_name', '工單'):
                category = cat
                break

    # ── 建立頻道 ──────────────────────
    await interaction.response.defer(ephemeral=True)
    try:
        channel = await guild.create_text_channel(
            name       = channel_name,
            overwrites = overwrites,
            category   = category,
            topic      = f"工單由 {user} 建立｜{topic}" if topic else f"工單由 {user} 建立",
        )
    except discord.Forbidden:
        await interaction.followup.send("Bot 缺少建立頻道的權限", ephemeral=True)
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"建立工單失敗：{e}", ephemeral=True)
        return

    # ── 寫入 DB ──────────────────────
    ticket_id = ticket_repo.create_ticket(
        guild_id   = guild.id,
        channel_id = channel.id,
        user_id    = user_id,
        topic      = topic,
    )
    _user_last_ticket[user_id] = time.monotonic()

    # ── 在工單頻道發送歡迎訊息 ──────────────────────
    embed = discord.Embed(
        title       = f"工單 #{ticket_num:04d}",
        description = (
            f"您好 {user.mention}，感謝您建立工單！\n\n"
            f"{'**主題**：' + topic + chr(10) if topic else ''}"
            "支援人員將會盡快協助您。\n\n"
            "完成後請點擊下方「關閉工單」按鈕。"
        ),
        color       = discord.Color.green(),
        timestamp   = discord.utils.utcnow(),
    )
    embed.set_footer(text=f"工單 ID：{ticket_id}")

    await channel.send(
        content = user.mention,
        embed   = embed,
        view    = CloseView(),
    )

    await interaction.followup.send(
        f"工單已建立：{channel.mention}",
        ephemeral=True,
    )
    logger.info(
        "[ticket.open] guild=%d channel=%s user=%s topic=%r",
        guild.id, channel.name, user, topic,
    )


async def _close_ticket(interaction: discord.Interaction) -> None:
    """
    關閉工單的核心邏輯。
    由按鈕回呼或 /ticket close 呼叫。
    """
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("此指令僅限文字頻道使用", ephemeral=True)
        return

    ticket = ticket_repo.get_ticket_by_channel(channel.id)
    if not ticket:
        await interaction.response.send_message("此頻道不是工單頻道", ephemeral=True)
        return
    if ticket["status"] == "closed":
        await interaction.response.send_message("此工單已關閉", ephemeral=True)
        return

    closed_by = str(interaction.user.id)
    ticket_repo.close_ticket(channel.id, closed_by)

    await interaction.response.send_message(
        f"工單已由 {interaction.user.mention} 關閉，頻道將在 5 秒後封存或刪除",
        ephemeral=False,
    )
    logger.info("[ticket.close] guild=%d channel=%s by=%s", interaction.guild.id, channel.name, interaction.user)

    await asyncio.sleep(5)

    # ── 封存或刪除 ──────────────────────
    settings     = guild_repo.get_settings(interaction.guild.id)
    archive_name = _s_get('ticket.archive_category', '')

    if archive_name:
        archive_cat: discord.CategoryChannel | None = None
        for cat in interaction.guild.categories:
            if cat.name == archive_name:
                archive_cat = cat
                break
        if archive_cat is None:
            try:
                archive_cat = await interaction.guild.create_category(archive_name)
            except discord.HTTPException:
                archive_cat = None

        if archive_cat:
            try:
                await channel.edit(
                    category = archive_cat,
                    overwrites = {
                        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        interaction.guild.me:           discord.PermissionOverwrite(view_channel=True),
                    },
                )
                return
            except discord.HTTPException:
                pass

    # 無封存設定或封存失敗時直接刪除
    try:
        await channel.delete()
    except discord.HTTPException:
        pass


# ── Cog ──────────────────────

class Ticket(commands.Cog):
    """工單系統指令群組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # 重啟後持久化 View 需重新加入
        bot.add_view(CloseView())
        bot.add_view(TicketPanel())

    ticket_group = app_commands.Group(name="ticket", description="工單系統")

    # ── /ticket open ──────────────────────

    @ticket_group.command(name="open", description="建立新工單")
    @app_commands.describe(topic="工單主題（選填）")
    async def cmd_open(
        self,
        interaction: discord.Interaction,
        topic:       str = "",
    ) -> None:
        await _open_ticket(interaction, topic=topic)

    # ── /ticket close ──────────────────────

    @ticket_group.command(name="close", description="關閉目前頻道的工單")
    async def cmd_close(self, interaction: discord.Interaction) -> None:
        await _close_ticket(interaction)

    # ── /ticket add ──────────────────────

    @ticket_group.command(name="add", description="將成員加入工單頻道")
    @app_commands.describe(member="要加入的成員")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_add(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("此指令僅限文字頻道使用", ephemeral=True)
            return

        if not ticket_repo.get_ticket_by_channel(channel.id):
            await interaction.response.send_message("此頻道不是工單頻道", ephemeral=True)
            return

        try:
            await channel.set_permissions(
                member,
                view_channel         = True,
                send_messages        = True,
                read_message_history = True,
            )
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少設定權限的能力", ephemeral=True)
            return

        await interaction.response.send_message(
            f"已將 {member.mention} 加入工單",
            ephemeral=False,
        )

    # ── /ticket remove ──────────────────────

    @ticket_group.command(name="remove", description="從工單頻道移除成員")
    @app_commands.describe(member="要移除的成員")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_remove(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("此指令僅限文字頻道使用", ephemeral=True)
            return

        if not ticket_repo.get_ticket_by_channel(channel.id):
            await interaction.response.send_message("此頻道不是工單頻道", ephemeral=True)
            return

        try:
            await channel.set_permissions(member, overwrite=None)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少設定權限的能力", ephemeral=True)
            return

        await interaction.response.send_message(
            f"已將 {member.mention} 從工單移除",
            ephemeral=False,
        )

    # ── /ticket stats ──────────────────────

    @ticket_group.command(name="stats", description="查看伺服器工單統計")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_stats(self, interaction: discord.Interaction) -> None:
        stats = ticket_repo.get_guild_stats(interaction.guild.id)

        embed = discord.Embed(
            title     = "工單統計",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(name="總工單數",   value=str(stats.get("total", 0)),       inline=True)
        embed.add_field(name="開啟中",     value=str(stats.get("open_count", 0)),  inline=True)
        embed.add_field(name="已關閉",     value=str(stats.get("closed_count", 0)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /ticket panel ──────────────────────

    @ticket_group.command(name="panel", description="在目前頻道發送工單建立面板")
    @app_commands.default_permissions(administrator=True)
    async def cmd_panel(self, interaction: discord.Interaction) -> None:
        """
        在當前頻道發送一個帶有「建立工單」按鈕的嵌入訊息，
        讓使用者可以直接點擊建立工單，而不需要輸入 /ticket open。
        """
        embed = discord.Embed(
            title       = "需要幫助嗎？",
            description = "點擊下方按鈕建立工單，我們的支援團隊將盡快協助您。",
            color       = discord.Color.green(),
        )
        await interaction.channel.send(embed=embed, view=TicketPanel())
        await interaction.response.send_message("工單面板已發送", ephemeral=True)


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ticket(bot))
