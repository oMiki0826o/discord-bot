"""
cogs/moderation/mod.py

職責：
- 提供 /ban /unban /kick /mute /unmute /warn /warnings /clear_warns /purge /modlog
- 所有動作記錄至 mod_repository，支援審計查詢
- DM 行為由 settings.json 控制（dm_target_on_warn / dm_target_on_mute）

Modification():

- 整合自上一版，dm 開關改由 settings.json 讀取
- _reason_str / _mod_embed / _send_log 抽為模組函式

"""

from __future__ import annotations

import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import database.repository.mod_repository as mod_repo
import database.repository.guild_repository as guild_repo
from core.system.settings import get

logger = logging.getLogger("bot.moderation")


def _reason_str(reason: str | None) -> str:
    return reason or "（未填寫原因）"


async def _send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        settings = guild_repo.get_settings(guild.id)
        ch_id    = settings.get("log_channel_id", 0)
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)
    except Exception:
        pass


def _mod_embed(
    action: str, target: discord.Member, moderator: discord.Member,
    reason: str, color: discord.Color = discord.Color.red(), extra: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title       = f"管理動作：{action}",
        description = f"目標：{target.mention}（{target}）\n原因：{reason}{extra}",
        color       = color,
        timestamp   = discord.utils.utcnow(),
    )
    embed.set_footer(text=f"執行者：{moderator}  |  {get('embed_footer.default','Firefly Bot')}")
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed


class Moderation(commands.Cog):
    """伺服器管理指令群組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _can_moderate(self, interaction: discord.Interaction, target: discord.Member) -> str | None:
        mod = interaction.user
        assert isinstance(mod, discord.Member)
        if target == mod:
            return "無法對自己執行此操作"
        if target.top_role >= mod.top_role and mod.id != interaction.guild.owner_id:
            return "目標成員的身份組階層不低於您"
        if target.guild_permissions.administrator and mod.id != interaction.guild.owner_id:
            return "無法管理具有管理員權限的成員"
        return None

    # ── /ban ──────────────────────

    @app_commands.command(name="ban", description="封禁成員")
    @app_commands.describe(member="要封禁的成員", reason="原因", delete_days="刪除幾天內的訊息（0-7）")
    @app_commands.default_permissions(ban_members=True)
    async def cmd_ban(
        self, interaction: discord.Interaction,
        member: discord.Member,
        reason: str | None = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return
        reason_str = _reason_str(reason)
        try:
            await member.ban(reason=reason_str, delete_message_days=delete_days)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少封禁權限", ephemeral=True); return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"封禁失敗：{e}", ephemeral=True); return

        mod_repo.log_action(interaction.guild.id, "ban", str(member.id), str(interaction.user.id), reason_str)
        embed = _mod_embed("封禁", member, interaction.user, reason_str)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /unban ──────────────────────

    @app_commands.command(name="unban", description="解除成員封禁")
    @app_commands.describe(user_id="要解封的使用者 ID")
    @app_commands.default_permissions(ban_members=True)
    async def cmd_unban(self, interaction: discord.Interaction, user_id: str) -> None:
        try:
            uid  = int(user_id)
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user)
        except ValueError:
            await interaction.response.send_message("請輸入有效的使用者 ID", ephemeral=True); return
        except discord.NotFound:
            await interaction.response.send_message("找不到該使用者或未被封禁", ephemeral=True); return
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少解封權限", ephemeral=True); return

        mod_repo.log_action(interaction.guild.id, "unban", user_id, str(interaction.user.id))
        embed = discord.Embed(
            title=f"管理動作：解除封禁",
            description=f"已解封 `{user}` ({user_id})",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"執行者：{interaction.user}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /kick ──────────────────────

    @app_commands.command(name="kick", description="踢出成員（可重新加入）")
    @app_commands.describe(member="要踢出的成員", reason="原因")
    @app_commands.default_permissions(kick_members=True)
    async def cmd_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True); return
        reason_str = _reason_str(reason)
        try:
            await member.kick(reason=reason_str)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少踢出權限", ephemeral=True); return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"踢出失敗：{e}", ephemeral=True); return

        mod_repo.log_action(interaction.guild.id, "kick", str(member.id), str(interaction.user.id), reason_str)
        embed = _mod_embed("踢出", member, interaction.user, reason_str, discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /mute ──────────────────────

    @app_commands.command(name="mute", description="禁言成員（Discord timeout）")
    @app_commands.describe(member="要禁言的成員", minutes="時長（分鐘）", reason="原因")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_mute(
        self, interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 43200] = 0,
        reason: str | None = None,
    ) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True); return

        default_min = int(get("moderation.default_mute_minutes", 10))
        max_min     = int(get("moderation.max_mute_minutes", 43200))
        duration    = min(minutes or default_min, max_min)
        reason_str  = _reason_str(reason)

        try:
            await member.timeout(timedelta(minutes=duration), reason=reason_str)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少禁言權限", ephemeral=True); return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"禁言失敗：{e}", ephemeral=True); return

        mod_repo.log_action(interaction.guild.id, "mute", str(member.id), str(interaction.user.id), reason_str, duration)
        embed = _mod_embed("禁言", member, interaction.user, reason_str, discord.Color.yellow(), extra=f"\n時長：{duration} 分鐘")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

        if get("moderation.dm_target_on_mute", False):
            try:
                await member.send(embed=discord.Embed(
                    title=f"你在 {interaction.guild.name} 被禁言",
                    description=f"原因：{reason_str}\n時長：{duration} 分鐘",
                    color=discord.Color.yellow(),
                ))
            except discord.HTTPException:
                pass

    # ── /unmute ──────────────────────

    @app_commands.command(name="unmute", description="解除成員禁言")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_unmute(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少解除禁言權限", ephemeral=True); return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"解除禁言失敗：{e}", ephemeral=True); return

        mod_repo.log_action(interaction.guild.id, "unmute", str(member.id), str(interaction.user.id))
        embed = _mod_embed("解除禁言", member, interaction.user, "手動解除", discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /warn ──────────────────────

    @app_commands.command(name="warn", description="對成員發出警告")
    @app_commands.describe(member="要警告的成員", reason="原因")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True); return
        reason_str = _reason_str(reason)
        total = mod_repo.add_warn(interaction.guild.id, str(member.id), str(interaction.user.id), reason_str)
        embed = _mod_embed("警告", member, interaction.user, reason_str, discord.Color.yellow(), extra=f"\n累計警告：{total} 次")

        if get("moderation.dm_target_on_warn", True):
            try:
                await member.send(embed=discord.Embed(
                    title=f"你在 {interaction.guild.name} 收到了警告",
                    description=f"原因：{reason_str}\n累計警告：{total} 次",
                    color=discord.Color.yellow(),
                ))
            except discord.HTTPException:
                pass

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /warnings ──────────────────────

    @app_commands.command(name="warnings", description="查看成員的警告紀錄")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        warns = mod_repo.get_warnings(interaction.guild.id, str(member.id))
        total = mod_repo.count_warnings(interaction.guild.id, str(member.id))
        embed = discord.Embed(title=f"{member.display_name} 的警告紀錄", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"累計警告：{total} 次  |  {get('embed_footer.default','Firefly Bot')}")
        if not warns:
            embed.description = "此成員目前無任何警告紀錄"
        else:
            lines = [f"**{i+1}.** <t:{int(w['created_at'])}:R> — {w['reason']}" for i, w in enumerate(warns)]
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /clear_warns ──────────────────────

    @app_commands.command(name="clear_warns", description="清除成員所有警告紀錄")
    @app_commands.default_permissions(administrator=True)
    async def cmd_clear_warns(self, interaction: discord.Interaction, member: discord.Member) -> None:
        deleted = mod_repo.clear_warnings(interaction.guild.id, str(member.id))
        await interaction.response.send_message(f"已清除 **{member.display_name}** 的 {deleted} 筆警告", ephemeral=True)

    # ── /purge ──────────────────────

    @app_commands.command(name="purge", description="批量刪除頻道訊息（最多 100 則）")
    @app_commands.describe(amount="要刪除的數量（1-100）")
    @app_commands.default_permissions(manage_messages=True)
    async def cmd_purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100] = 10) -> None:
        assert isinstance(interaction.channel, discord.TextChannel)
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send("Bot 缺少刪除訊息權限", ephemeral=True); return
        await interaction.followup.send(f"已刪除 {len(deleted)} 則訊息", ephemeral=True)

    # ── /modlog ──────────────────────

    @app_commands.command(name="modlog", description="查看最近 20 筆管理動作紀錄")
    @app_commands.default_permissions(moderate_members=True)
    async def cmd_modlog(self, interaction: discord.Interaction) -> None:
        logs  = mod_repo.get_mod_log(interaction.guild.id, limit=20)
        embed = discord.Embed(title="管理動作紀錄（最近 20 筆）", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        if not logs:
            embed.description = "目前無管理動作紀錄"
        else:
            lines = []
            for e in logs:
                ts     = int(e["created_at"])
                detail = f"（{e['duration_min']} 分）" if e.get("duration_min") else ""
                lines.append(f"<t:{ts}:R> **{e['action']}** <@{e['user_id']}>{detail} — {e.get('reason','')}")
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
