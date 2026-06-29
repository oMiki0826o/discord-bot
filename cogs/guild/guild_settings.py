"""
cogs/guild/guild_settings.py

職責：
- 伺服器設定管理（/server 指令群組）
- 歡迎/離開訊息、日誌頻道、自動身份組設定
- 成員進出事件監聽（on_member_join / on_member_remove）

Modification():

- 歡迎/離開訊息預設範本改由 settings.json 讀取
- 日誌 Embed footer 從 settings 取得

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import database.repository.guild_repository as guild_repo
from core.system.settings import get

logger = logging.getLogger("bot.guild_settings")


def _format_msg(template: str, member: discord.Member) -> str:
    return (
        template
        .replace("{user}",     member.mention)
        .replace("{username}", str(member))
        .replace("{guild}",    member.guild.name)
        .replace("{count}",    str(member.guild.member_count or 0))
    )


class GuildSettings(commands.Cog):
    """伺服器設定與成員事件處理。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 成員加入 ──────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild    = member.guild
        settings = guild_repo.get_settings(guild.id)

        # 歡迎訊息
        ch_id = settings.get("welcome_channel_id", 0)
        if ch_id:
            ch = guild.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                template = (
                    settings.get("extra", {}).get("welcome_template")
                    or get("guild.welcome_template", "歡迎 {user} 加入 **{guild}**！")
                )
                try:
                    await ch.send(_format_msg(template, member))
                except discord.HTTPException as e:
                    logger.warning("[guild] 歡迎訊息失敗: %s", e)

        # 自動身份組
        role_id = settings.get("auto_role_id", 0)
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="自動身份組")
                except discord.HTTPException as e:
                    logger.warning("[guild] 自動身份組失敗: %s", e)

        # 日誌
        embed = discord.Embed(
            title       = "成員加入",
            description = f"{member.mention}（{member}）",
            color       = discord.Color.green(),
            timestamp   = discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID: {member.id}  |  {get('embed_footer.default','Firefly Bot')}")
        await self._log(guild, settings, embed)
        logger.info("[guild.join] guild=%s member=%s", guild.name, member)

    # ── 成員離開 ──────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild    = member.guild
        settings = guild_repo.get_settings(guild.id)

        ch_id = settings.get("leave_channel_id", 0)
        if ch_id:
            ch = guild.get_channel(ch_id)
            if isinstance(ch, discord.TextChannel):
                template = (
                    settings.get("extra", {}).get("leave_template")
                    or get("guild.leave_template", "**{username}** 離開了 **{guild}**")
                )
                try:
                    await ch.send(_format_msg(template, member))
                except discord.HTTPException as e:
                    logger.warning("[guild] 離開訊息失敗: %s", e)

        embed = discord.Embed(
            title       = "成員離開",
            description = f"{member}（{member.id}）",
            color       = discord.Color.red(),
            timestamp   = discord.utils.utcnow(),
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await self._log(guild, settings, embed)

    # ── 日誌工具 ──────────────────────

    async def _log(self, guild: discord.Guild, settings: dict, embed: discord.Embed) -> None:
        ch_id = settings.get("log_channel_id", 0)
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Slash Commands ──────────────────────

    server_group = app_commands.Group(name="server", description="伺服器設定管理")

    @server_group.command(name="welcome", description="設定歡迎頻道")
    @app_commands.describe(channel="歡迎頻道")
    @app_commands.default_permissions(administrator=True)
    async def cmd_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild_repo.set_setting(interaction.guild.id, "welcome_channel_id", channel.id)
        await interaction.response.send_message(f"歡迎頻道已設定為 {channel.mention}", ephemeral=True)

    @server_group.command(name="leave", description="設定離開訊息頻道")
    @app_commands.describe(channel="離開訊息頻道")
    @app_commands.default_permissions(administrator=True)
    async def cmd_leave(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild_repo.set_setting(interaction.guild.id, "leave_channel_id", channel.id)
        await interaction.response.send_message(f"離開訊息頻道已設定為 {channel.mention}", ephemeral=True)

    @server_group.command(name="log", description="設定日誌頻道")
    @app_commands.describe(channel="日誌頻道")
    @app_commands.default_permissions(administrator=True)
    async def cmd_log(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild_repo.set_setting(interaction.guild.id, "log_channel_id", channel.id)
        await interaction.response.send_message(f"日誌頻道已設定為 {channel.mention}", ephemeral=True)

    @server_group.command(name="autorole", description="設定新成員自動身份組（留空停用）")
    @app_commands.describe(role="自動身份組")
    @app_commands.default_permissions(administrator=True)
    async def cmd_autorole(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        guild_repo.set_setting(interaction.guild.id, "auto_role_id", role.id if role else 0)
        msg = f"自動身份組已設定為 {role.mention}" if role else "自動身份組已停用"
        await interaction.response.send_message(msg, ephemeral=True)

    @server_group.command(name="ticket_category", description="設定工單類別")
    @app_commands.default_permissions(administrator=True)
    async def cmd_ticket_category(self, interaction: discord.Interaction, category: discord.CategoryChannel) -> None:
        guild_repo.set_setting(interaction.guild.id, "ticket_category_id", category.id)
        await interaction.response.send_message(f"工單類別已設定為 **{category.name}**", ephemeral=True)

    @server_group.command(name="ticket_support", description="設定工單支援身份組（留空停用）")
    @app_commands.default_permissions(administrator=True)
    async def cmd_ticket_support(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        guild_repo.set_setting(interaction.guild.id, "ticket_support_role", role.id if role else 0)
        msg = f"工單支援身份組已設定為 {role.mention}" if role else "工單支援身份組已停用"
        await interaction.response.send_message(msg, ephemeral=True)

    @server_group.command(name="info", description="查看目前的伺服器設定")
    @app_commands.default_permissions(manage_guild=True)
    async def cmd_info(self, interaction: discord.Interaction) -> None:
        settings = guild_repo.get_settings(interaction.guild.id)
        guild    = interaction.guild

        def ch_m(ch_id: int) -> str:
            if not ch_id: return "未設定"
            ch = guild.get_channel(ch_id)
            return ch.mention if ch else f"不存在（{ch_id}）"

        def role_m(role_id: int) -> str:
            if not role_id: return "未設定"
            r = guild.get_role(role_id)
            return r.mention if r else f"不存在（{role_id}）"

        def cat_n(cat_id: int) -> str:
            if not cat_id: return "未設定"
            c = guild.get_channel(cat_id)
            return f"**{c.name}**" if c else f"不存在（{cat_id}）"

        embed = discord.Embed(
            title     = f"{guild.name} 伺服器設定",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(
            name  = "頻道設定",
            value = (
                f"歡迎：{ch_m(settings.get('welcome_channel_id',0))}\n"
                f"離開：{ch_m(settings.get('leave_channel_id',0))}\n"
                f"日誌：{ch_m(settings.get('log_channel_id',0))}"
            ),
            inline=False,
        )
        embed.add_field(
            name  = "身份組 / 工單",
            value = (
                f"自動身份組：{role_m(settings.get('auto_role_id',0))}\n"
                f"工單支援組：{role_m(settings.get('ticket_support_role',0))}\n"
                f"工單類別：{cat_n(settings.get('ticket_category_id',0))}\n"
                f"工單總數：{settings.get('ticket_count',0)} 張"
            ),
            inline=False,
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server_group.command(name="reset", description="重置所有伺服器設定為預設值")
    @app_commands.default_permissions(administrator=True)
    async def cmd_reset(self, interaction: discord.Interaction) -> None:
        from database.ai.sqlite import get_connection
        conn = get_connection()
        conn.execute("DELETE FROM guild_settings WHERE guild_id = ?", (interaction.guild.id,))
        conn.commit()
        conn.close()
        guild_repo.get_settings(interaction.guild.id)
        await interaction.response.send_message("伺服器設定已重置為預設值", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildSettings(bot))
