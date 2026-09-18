"""
cogs/guild/guild_settings.py

職責：
- 伺服器設定管理（/server 指令群組）
- 歡迎/離開訊息、日誌頻道、自動身份組設定
- 成員進出事件監聽（on_member_join / on_member_remove）

Modification():

- 歡迎/離開訊息預設範本改由 settings.json 讀取
- 日誌 Embed footer 從 settings 取得
- $server reset 改呼叫 guild_repo.reset_settings()，不再於 Cog 內
  直接執行原始 SQL，與其餘指令一致委派給 Repository 層處理。

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import database.repository.guild_repository as guild_repo
from core.system.settings import get
from utils.confirmation import guarded_action, missing_permissions, request_confirmation

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
        settings = await guild_repo.get_settings(guild.id)

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
        settings = await guild_repo.get_settings(guild.id)

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

    async def _settings_embed(self, guild: discord.Guild) -> discord.Embed:
        settings = await guild_repo.get_settings(guild.id)

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
        embed.description = "使用下方選單修改設定；所有操作只對目前伺服器生效。"
        return embed

    async def _set_channel(
        self,
        interaction: discord.Interaction,
        key: str,
        channel: discord.abc.GuildChannel | None,
        label: str,
    ) -> None:
        if error := missing_permissions(interaction, user=("administrator",)):
            await interaction.response.send_message(error, ephemeral=True)
            return
        await guild_repo.set_setting(interaction.guild.id, key, channel.id if channel else 0)
        if channel is None:
            message = f"{label}已停用"
        else:
            shown = channel.mention if hasattr(channel, "mention") else f"**{channel.name}**"
            message = f"{label}已設定為 {shown}"
        await interaction.response.send_message(message, ephemeral=True)

    async def _set_role(
        self,
        interaction: discord.Interaction,
        key: str,
        role: discord.Role | None,
        label: str,
    ) -> None:
        if error := missing_permissions(interaction, user=("administrator",)):
            await interaction.response.send_message(error, ephemeral=True)
            return
        if role is not None:
            invalid = role.is_default()
            if key == "auto_role_id":
                invalid = invalid or role.managed or role >= interaction.guild.me.top_role
            if invalid:
                await interaction.response.send_message("此身份組無法用於這項設定。", ephemeral=True)
                return
        await guild_repo.set_setting(interaction.guild.id, key, role.id if role else 0)
        message = f"{label}已設定為 {role.mention}" if role else f"{label}已停用"
        await interaction.response.send_message(message, ephemeral=True)

    async def _reset_settings(self, interaction: discord.Interaction) -> None:
        await guild_repo.reset_settings(interaction.guild.id)
        await guild_repo.get_settings(interaction.guild.id)
        await interaction.response.send_message("伺服器設定已重置為預設值", ephemeral=True)

    @app_commands.command(name="server", description="開啟伺服器設定面板")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def cmd_server(self, interaction: discord.Interaction) -> None:
        embed = await self._settings_embed(interaction.guild)
        await interaction.response.send_message(
            embed=embed,
            view=ServerSettingsView(self, interaction.user.id),
            ephemeral=True,
        )


class ServerSettingsSelect(discord.ui.Select):
    def __init__(self, cog: GuildSettings) -> None:
        self.cog = cog
        super().__init__(
            placeholder="選擇要調整的伺服器設定",
            options=[
                discord.SelectOption(label="歡迎訊息頻道", value="welcome"),
                discord.SelectOption(label="離開訊息頻道", value="leave"),
                discord.SelectOption(label="管理日誌頻道", value="log"),
                discord.SelectOption(label="新成員自動身份組", value="autorole"),
                discord.SelectOption(label="工單類別", value="ticket_category"),
                discord.SelectOption(label="工單支援身份組", value="ticket_support"),
                discord.SelectOption(label="重新整理設定資訊", value="info"),
                discord.SelectOption(label="重置全部設定", value="reset"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if error := missing_permissions(interaction, user=("administrator",)):
            await interaction.response.send_message(error, ephemeral=True)
            return
        action = self.values[0]
        if action == "info":
            await interaction.response.edit_message(
                embed=await self.cog._settings_embed(interaction.guild),
                view=self.view,
            )
            return
        if action == "reset":
            await request_confirmation(
                interaction,
                title="確認重置伺服器設定",
                description="歡迎、離開、日誌、自動身份組與工單設定都會被清除。",
                action=guarded_action(self.cog._reset_settings, user=("administrator",)),
            )
            return

        channel_options = {
            "welcome": ("welcome_channel_id", "歡迎訊息頻道", [discord.ChannelType.text]),
            "leave": ("leave_channel_id", "離開訊息頻道", [discord.ChannelType.text]),
            "log": ("log_channel_id", "管理日誌頻道", [discord.ChannelType.text]),
            "ticket_category": ("ticket_category_id", "工單類別", [discord.ChannelType.category]),
        }
        if action in channel_options:
            key, label, channel_types = channel_options[action]
            await interaction.response.send_message(
                f"請選擇{label}：",
                view=ServerChannelView(self.cog, key, label, channel_types),
                ephemeral=True,
            )
            return

        key, label = (
            ("auto_role_id", "新成員自動身份組")
            if action == "autorole"
            else ("ticket_support_role", "工單支援身份組")
        )
        await interaction.response.send_message(
            f"請選擇{label}，或按下停用：",
            view=ServerRoleView(self.cog, key, label),
            ephemeral=True,
        )


class ServerSettingsView(discord.ui.View):
    def __init__(self, cog: GuildSettings, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(ServerSettingsSelect(cog))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的設定面板。", ephemeral=True)
        return False


class ServerChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog: GuildSettings, key: str, label: str, channel_types: list[discord.ChannelType]) -> None:
        super().__init__(channel_types=channel_types, min_values=1, max_values=1)
        self.cog, self.key, self.label = cog, key, label

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog._set_channel(interaction, self.key, self.values[0], self.label)


class ServerChannelView(discord.ui.View):
    def __init__(self, cog: GuildSettings, key: str, label: str, channel_types: list[discord.ChannelType]) -> None:
        super().__init__(timeout=120)
        self.cog, self.key, self.label = cog, key, label
        self.add_item(ServerChannelSelect(cog, key, label, channel_types))

    @discord.ui.button(label="停用此設定", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog._set_channel(interaction, self.key, None, self.label)


class ServerRoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog: GuildSettings, key: str, label: str) -> None:
        super().__init__(min_values=1, max_values=1)
        self.cog, self.key, self.label = cog, key, label

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog._set_role(interaction, self.key, self.values[0], self.label)


class ServerRoleView(discord.ui.View):
    def __init__(self, cog: GuildSettings, key: str, label: str) -> None:
        super().__init__(timeout=120)
        self.cog, self.key, self.label = cog, key, label
        self.add_item(ServerRoleSelect(cog, key, label))

    @discord.ui.button(label="停用此設定", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.cog._set_role(interaction, self.key, None, self.label)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildSettings(bot))
