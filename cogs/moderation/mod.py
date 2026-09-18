"""
cogs/moderation/mod.py

職責：
- 以單一 /mod 指令的 action 選單提供封禁、踢出、禁言、警告、清除與紀錄查詢
- 所有動作記錄至 mod_repository，支援審計查詢
- 所有會改變狀態的操作皆需要原操作者在 30 秒內二次確認
- DM 通知行為由 settings.json 控制（dm_target_on_warn / dm_target_on_mute）

Modification():

- 修正 /warn 對 Bot 帳號執行時觸發
  AttributeError: 'ClientUser' object has no attribute 'create_dm' 的問題
  （根本原因：Bot 的 Member._user 是 ClientUser 而非 User，
   ClientUser 沒有 create_dm()；直接呼叫 .send() 時觸發此錯誤）
- _can_moderate() 新增 Bot 帳號檢查：任何管理動作皆不可對 Bot 執行
- DM 通知區塊新增 member.bot 前置過濾，Bot 帳號不嘗試私訊
- DM 通知例外捕捉從 discord.HTTPException 擴展為 (discord.HTTPException, AttributeError)
  作為防禦性措施，避免未知的 ClientUser 邊界情況

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
from utils.confirmation import guarded_action, missing_permissions, request_confirmation

logger = logging.getLogger("bot.moderation")


# ── 模組工具函式 ──────────────────────

def _reason_str(reason: str | None) -> str:
    return reason or "（未填寫原因）"


async def _send_log(guild: discord.Guild, embed: discord.Embed) -> None:
    """將管理動作記錄發送至伺服器設定的 log 頻道。"""
    try:
        settings = await guild_repo.get_settings(guild.id)
        ch_id    = settings.get("log_channel_id", 0)
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)
    except Exception:
        pass


def _mod_embed(
    action:    str,
    target:    discord.Member,
    moderator: discord.Member,
    reason:    str,
    color:     discord.Color = discord.Color.red(),
    extra:     str           = "",
) -> discord.Embed:
    """建立標準管理動作 Embed。"""
    embed = discord.Embed(
        title       = f"管理動作：{action}",
        description = f"目標：{target.mention}（{target}）\n原因：{reason}{extra}",
        color       = color,
        timestamp   = discord.utils.utcnow(),
    )
    embed.set_footer(text=f"執行者：{moderator}  |  {get('embed_footer.default','Firefly Bot')}")
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed


# ── Cog ──────────────────────

class Moderation(commands.Cog):
    """伺服器管理指令群組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="mod", description="開啟伺服器管理面板")
    @app_commands.guild_only()
    async def cmd_mod(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="伺服器管理面板",
            description="請從下方選單選擇管理操作。\n需要成員的操作會再顯示成員選擇器。",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        await interaction.response.send_message(
            embed=embed, view=ModerationView(self, interaction.user.id), ephemeral=True,
        )

    async def dispatch_action(
        self,
        interaction: discord.Interaction,
        value: str,
        member: discord.Member | None = None,
        user_id: str | None = None,
        reason: str | None = None,
        minutes: int | None = None,
        delete_days: int = 0,
        amount: int = 10,
    ) -> None:
        user_permissions = {
            "ban": ("ban_members",), "unban": ("ban_members",),
            "kick": ("kick_members",),
            "mute": ("moderate_members",), "unmute": ("moderate_members",),
            "warn": ("moderate_members",), "warnings": ("moderate_members",),
            "clear_warns": ("administrator",),
            "purge": ("manage_messages",), "modlog": ("moderate_members",),
        }[value]
        bot_permissions = {
            "ban": ("ban_members",), "unban": ("ban_members",),
            "kick": ("kick_members",),
            "mute": ("moderate_members",), "unmute": ("moderate_members",),
            "purge": ("manage_messages", "read_message_history"),
        }.get(value, ())
        if error := missing_permissions(interaction, user=user_permissions, bot=bot_permissions):
            await interaction.response.send_message(error, ephemeral=True)
            return

        if value in {"warnings", "modlog"}:
            if value == "warnings":
                if member is None:
                    await interaction.response.send_message("查看警告時必須選擇目標成員。", ephemeral=True)
                    return
                await self.cmd_warnings(interaction, member)
            else:
                await self.cmd_modlog(interaction)
            return

        if value == "unban":
            if not user_id:
                await interaction.response.send_message("解除封禁時必須填寫使用者 ID。", ephemeral=True)
                return
            execute = lambda click: self.cmd_unban(click, user_id)
            target_text = f"使用者 ID `{user_id}`"
        elif value == "purge":
            execute = lambda click: self.cmd_purge(click, amount)
            target_text = f"目前頻道最近 **{amount}** 則訊息"
        else:
            if member is None:
                await interaction.response.send_message("此操作必須選擇目標成員。", ephemeral=True)
                return
            calls = {
                "ban": lambda click: self.cmd_ban(click, member, reason, delete_days),
                "kick": lambda click: self.cmd_kick(click, member, reason),
                "mute": lambda click: self.cmd_mute(click, member, minutes or 0, reason),
                "unmute": lambda click: self.cmd_unmute(click, member),
                "warn": lambda click: self.cmd_warn(click, member, reason),
                "clear_warns": lambda click: self.cmd_clear_warns(click, member),
            }
            execute = calls[value]
            target_text = member.mention

        labels = {
            "ban": "封禁", "unban": "解除封禁", "kick": "踢出",
            "mute": "禁言", "unmute": "解除禁言", "warn": "警告",
            "clear_warns": "清除全部警告", "purge": "批量刪除訊息",
        }
        await request_confirmation(
            interaction,
            title=f"確認{labels[value]}",
            description=f"目標：{target_text}\n原因：{_reason_str(reason)}\n此操作將被記錄。",
            action=guarded_action(execute, user=user_permissions, bot=bot_permissions),
        )

    def _can_moderate(
        self,
        interaction: discord.Interaction,
        target:      discord.Member,
    ) -> str | None:
        """
        檢查是否允許對目標執行管理動作。

        回傳 None 表示允許；回傳字串則為拒絕原因。

        修正：新增 target.bot 檢查，避免對 Bot 帳號執行管理動作，
        進而防止後續 .send() 呼叫觸發 AttributeError（ClientUser 無 create_dm）。
        """
        mod = interaction.user
        assert isinstance(mod, discord.Member)

        if target.bot:
            return "無法對 Bot 帳號執行此操作"
        if target == mod:
            return "無法對自己執行此操作"
        if target.top_role >= mod.top_role and mod.id != interaction.guild.owner_id:
            return "目標成員的身份組階層不低於您"
        if target.guild_permissions.administrator and mod.id != interaction.guild.owner_id:
            return "無法管理具有管理員權限的成員"
        return None

    # ── /ban ──────────────────────

    async def cmd_ban(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
        reason:      str | None = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return
        reason_str = _reason_str(reason)
        try:
            await member.ban(reason=reason_str, delete_message_days=delete_days)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少封禁權限", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"封禁失敗：{e}", ephemeral=True)
            return

        await mod_repo.log_action(
            interaction.guild.id, "ban",
            str(member.id), str(interaction.user.id), reason_str,
        )
        embed = _mod_embed("封禁", member, interaction.user, reason_str)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /unban ──────────────────────

    async def cmd_unban(self, interaction: discord.Interaction, user_id: str) -> None:
        try:
            uid  = int(user_id)
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user)
        except ValueError:
            await interaction.response.send_message("請輸入有效的使用者 ID", ephemeral=True)
            return
        except discord.NotFound:
            await interaction.response.send_message("找不到該使用者或未被封禁", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少解封權限", ephemeral=True)
            return

        await mod_repo.log_action(
            interaction.guild.id, "unban",
            user_id, str(interaction.user.id),
        )
        embed = discord.Embed(
            title       = "管理動作：解除封禁",
            description = f"已解封 `{user}` ({user_id})",
            color       = discord.Color.green(),
            timestamp   = discord.utils.utcnow(),
        )
        embed.set_footer(text=f"執行者：{interaction.user}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /kick ──────────────────────

    async def cmd_kick(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
        reason:      str | None = None,
    ) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return
        reason_str = _reason_str(reason)
        try:
            await member.kick(reason=reason_str)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少踢出權限", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"踢出失敗：{e}", ephemeral=True)
            return

        await mod_repo.log_action(
            interaction.guild.id, "kick",
            str(member.id), str(interaction.user.id), reason_str,
        )
        embed = _mod_embed("踢出", member, interaction.user, reason_str, discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /mute ──────────────────────

    async def cmd_mute(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
        minutes:     app_commands.Range[int, 1, 43200] = 0,
        reason:      str | None = None,
    ) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return

        default_min = int(get("moderation.default_mute_minutes", 10))
        max_min     = int(get("moderation.max_mute_minutes", 43200))
        duration    = min(minutes or default_min, max_min)
        reason_str  = _reason_str(reason)

        try:
            await member.timeout(timedelta(minutes=duration), reason=reason_str)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少禁言權限", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"禁言失敗：{e}", ephemeral=True)
            return

        await mod_repo.log_action(
            interaction.guild.id, "mute",
            str(member.id), str(interaction.user.id), reason_str, duration,
        )
        embed = _mod_embed(
            "禁言", member, interaction.user, reason_str,
            discord.Color.yellow(), extra=f"\n時長：{duration} 分鐘",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

        # ── DM 通知（Bot 帳號不嘗試私訊）──────────────────────
        if not member.bot and get("moderation.dm_target_on_mute", False):
            try:
                await member.send(embed=discord.Embed(
                    title       = f"你在 {interaction.guild.name} 被禁言",
                    description = f"原因：{reason_str}\n時長：{duration} 分鐘",
                    color       = discord.Color.yellow(),
                ))
            except (discord.HTTPException, AttributeError):
                pass

    # ── /unmute ──────────────────────

    async def cmd_unmute(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return
        try:
            await member.timeout(None)
        except discord.Forbidden:
            await interaction.response.send_message("Bot 缺少解除禁言權限", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"解除禁言失敗：{e}", ephemeral=True)
            return

        await mod_repo.log_action(
            interaction.guild.id, "unmute",
            str(member.id), str(interaction.user.id),
        )
        embed = _mod_embed("解除禁言", member, interaction.user, "手動解除", discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /warn ──────────────────────

    async def cmd_warn(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
        reason:      str | None = None,
    ) -> None:
        """
        對成員發出警告並記錄。

        修正：原版呼叫 member.send() 前未檢查 member.bot，
        當目標為 Bot 本身時，member._user 是 ClientUser，
        ClientUser 沒有 create_dm()，導致 AttributeError。
        現在透過 _can_moderate() 的 bot 檢查前置攔截，
        同時 DM 通知也增加 not member.bot 守衛及 AttributeError 捕捉。
        """
        if err := self._can_moderate(interaction, member):
            await interaction.response.send_message(err, ephemeral=True)
            return

        reason_str = _reason_str(reason)
        total      = await mod_repo.add_warn(
            interaction.guild.id, str(member.id),
            str(interaction.user.id), reason_str,
        )
        embed = _mod_embed(
            "警告", member, interaction.user, reason_str,
            discord.Color.yellow(), extra=f"\n累計警告：{total} 次",
        )

        # ── DM 通知（Bot 帳號不嘗試私訊）──────────────────────
        if not member.bot and get("moderation.dm_target_on_warn", True):
            try:
                await member.send(embed=discord.Embed(
                    title       = f"你在 {interaction.guild.name} 收到了警告",
                    description = f"原因：{reason_str}\n累計警告：{total} 次",
                    color       = discord.Color.yellow(),
                ))
            except (discord.HTTPException, AttributeError):
                # Forbidden / 使用者關閉 DM / ClientUser 邊界情況均靜默處理
                pass

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await _send_log(interaction.guild, embed)

    # ── /warnings ──────────────────────

    async def cmd_warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        warns = await mod_repo.get_warnings(interaction.guild.id, str(member.id))
        total = await mod_repo.count_warnings(interaction.guild.id, str(member.id))
        embed = discord.Embed(
            title     = f"{member.display_name} 的警告紀錄",
            color     = discord.Color.orange(),
            timestamp = discord.utils.utcnow(),
        )
        embed.set_footer(
            text=f"累計警告：{total} 次  |  {get('embed_footer.default','Firefly Bot')}"
        )
        if not warns:
            embed.description = "此成員目前無任何警告紀錄"
        else:
            lines = [
                f"**{i+1}.** <t:{int(w['created_at'])}:R> — {w['reason']}"
                for i, w in enumerate(warns)
            ]
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /clear_warns ──────────────────────

    async def cmd_clear_warns(self, interaction: discord.Interaction, member: discord.Member) -> None:
        deleted = await mod_repo.clear_warnings(interaction.guild.id, str(member.id))
        await interaction.response.send_message(
            f"已清除 **{member.display_name}** 的 {deleted} 筆警告",
            ephemeral=True,
        )

    # ── /purge ──────────────────────

    async def cmd_purge(
        self,
        interaction: discord.Interaction,
        amount:      app_commands.Range[int, 1, 100] = 10,
    ) -> None:
        assert isinstance(interaction.channel, discord.TextChannel)
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
        except discord.Forbidden:
            await interaction.followup.send("Bot 缺少刪除訊息權限", ephemeral=True)
            return
        await interaction.followup.send(f"已刪除 {len(deleted)} 則訊息", ephemeral=True)

    # ── /modlog ──────────────────────

    async def cmd_modlog(self, interaction: discord.Interaction) -> None:
        logs  = await mod_repo.get_mod_log(interaction.guild.id, limit=20)
        embed = discord.Embed(
            title     = "管理動作紀錄（最近 20 筆）",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        if not logs:
            embed.description = "目前無管理動作紀錄"
        else:
            lines = []
            for e in logs:
                ts     = int(e["created_at"])
                detail = f"（{e['duration_min']} 分）" if e.get("duration_min") else ""
                lines.append(
                    f"<t:{ts}:R> **{e['action']}** <@{e['user_id']}>{detail} — {e.get('reason','')}"
                )
            embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ModerationActionSelect(discord.ui.Select):
    def __init__(self, cog: Moderation) -> None:
        self.cog = cog
        super().__init__(
            placeholder="選擇管理操作",
            options=[
                discord.SelectOption(label="封禁成員", value="ban"),
                discord.SelectOption(label="解除封禁", value="unban"),
                discord.SelectOption(label="踢出成員", value="kick"),
                discord.SelectOption(label="禁言成員", value="mute"),
                discord.SelectOption(label="解除禁言", value="unmute"),
                discord.SelectOption(label="警告成員", value="warn"),
                discord.SelectOption(label="查看警告", value="warnings"),
                discord.SelectOption(label="清除警告", value="clear_warns"),
                discord.SelectOption(label="批量刪除訊息", value="purge"),
                discord.SelectOption(label="查看管理紀錄", value="modlog"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = self.values[0]
        if action == "modlog":
            await self.cog.dispatch_action(interaction, action)
        elif action in {"unban", "purge"}:
            await interaction.response.send_modal(ModerationInputModal(self.cog, action))
        else:
            await interaction.response.send_message(
                "請選擇目標成員：",
                view=ModerationMemberView(self.cog, action, interaction.user.id),
                ephemeral=True,
            )


class ModerationView(discord.ui.View):
    def __init__(self, cog: Moderation, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(ModerationActionSelect(cog))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的管理面板。", ephemeral=True)
        return False


class ModerationMemberSelect(discord.ui.UserSelect):
    def __init__(self, cog: Moderation, action: str) -> None:
        super().__init__(placeholder="選擇一位成員", min_values=1, max_values=1)
        self.cog, self.action = cog, action

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0]
        member = interaction.guild.get_member(selected.id)
        if member is None:
            await interaction.response.send_message("找不到這位伺服器成員。", ephemeral=True)
            return
        if self.action in {"warnings", "unmute", "clear_warns"}:
            await self.cog.dispatch_action(interaction, self.action, member=member)
            return
        await interaction.response.send_modal(ModerationInputModal(self.cog, self.action, member))


class ModerationMemberView(discord.ui.View):
    def __init__(self, cog: Moderation, action: str, user_id: int) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.add_item(ModerationMemberSelect(cog, action))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的管理面板。", ephemeral=True)
        return False


class ModerationInputModal(discord.ui.Modal):
    def __init__(self, cog: Moderation, action: str, member: discord.Member | None = None) -> None:
        labels = {
            "ban": "封禁成員", "unban": "解除封禁", "kick": "踢出成員",
            "mute": "禁言成員", "warn": "警告成員", "purge": "批量刪除訊息",
        }
        super().__init__(title=labels[action])
        self.cog, self.action, self.member = cog, action, member
        self.reason: discord.ui.TextInput | None = None
        self.number: discord.ui.TextInput | None = None
        if action == "unban":
            self.number = discord.ui.TextInput(label="使用者 ID", placeholder="123456789012345678", max_length=20)
            self.add_item(self.number)
        elif action == "purge":
            self.number = discord.ui.TextInput(label="刪除數量（1-100）", default="10", max_length=3)
            self.add_item(self.number)
        else:
            self.reason = discord.ui.TextInput(
                label="原因（選填）", required=False, max_length=500,
                style=discord.TextStyle.paragraph,
            )
            self.add_item(self.reason)
            if action == "mute":
                self.number = discord.ui.TextInput(label="禁言分鐘數", default="10", max_length=5)
                self.add_item(self.number)
            elif action == "ban":
                self.number = discord.ui.TextInput(label="刪除訊息天數（0-7）", default="0", max_length=1)
                self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_number = str(self.number.value).strip() if self.number else ""
        try:
            if self.action == "unban":
                if not raw_number.isdigit():
                    raise ValueError
                await self.cog.dispatch_action(interaction, self.action, user_id=raw_number)
                return
            if self.action == "purge":
                amount = int(raw_number)
                if not 1 <= amount <= 100:
                    raise ValueError
                await self.cog.dispatch_action(interaction, self.action, amount=amount)
                return
            number = int(raw_number) if raw_number else 0
            if self.action == "mute" and not 1 <= number <= 43200:
                raise ValueError
            if self.action == "ban" and not 0 <= number <= 7:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("輸入的數字超出允許範圍。", ephemeral=True)
            return

        await self.cog.dispatch_action(
            interaction,
            self.action,
            member=self.member,
            reason=(str(self.reason.value).strip() or None) if self.reason else None,
            minutes=number if self.action == "mute" else None,
            delete_days=number if self.action == "ban" else 0,
        )


# 保留舊版 callback 的程式化呼叫相容性，Slash 指令本身仍然是無參數面板。
_mod_panel_callback = Moderation.cmd_mod.callback


async def _mod_callback_compat(
    cog: Moderation,
    interaction: discord.Interaction,
    action: app_commands.Choice[str] | None = None,
    member: discord.Member | None = None,
    user_id: str | None = None,
    reason: str | None = None,
    minutes: int | None = None,
    delete_days: int = 0,
    amount: int = 10,
) -> None:
    if action is None:
        await _mod_panel_callback(cog, interaction)
        return
    await cog.dispatch_action(
        interaction, action.value, member, user_id, reason, minutes, delete_days, amount,
    )


Moderation.cmd_mod._callback = _mod_callback_compat


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
