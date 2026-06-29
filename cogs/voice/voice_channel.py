"""
cogs/voice/voice_channel.py

職責：
- 「加入即建立」（Join to Create, JTC）臨時語音頻道系統
- 成員加入觸發頻道時，自動建立專屬語音頻道並移入
- 頻道清空後自動刪除（on_voice_state_update）
- 頻道擁有者可用 /vc 指令客製化自己的頻道

功能指令（Slash Command）：
  /vc setup   — 管理員設定 JTC 觸發頻道
  /vc name    — 更改頻道名稱
  /vc limit   — 設定人數上限
  /vc lock    — 鎖定頻道（阻止其他人進入）
  /vc unlock  — 解鎖頻道
  /vc permit  — 允許指定成員進入已鎖定的頻道
  /vc reject  — 禁止指定成員進入此頻道
  /vc kick    — 將成員踢出此頻道
  /vc transfer — 將頻道所有權轉移給其他成員
  /vc info    — 查看目前頻道設定

設計說明：
- 觸發頻道（create_channel）本身永遠不刪除，其他動態頻道空了才刪
- Bot 重啟後：on_ready 掃描 DB 裡的臨時頻道，已空的直接刪除
- 頻道名稱範本中的 {username} / {guild} 在建立時替換
- 同一 guild 的 JTC 設定存 DB，可熱更新無需重啟

Modification():

- 全新建立，參考 Reddit r/discordapp JTC Bot 概念
- 完整實作擁有者控制系統（非 admin 也可管理自己的頻道）
- 重啟恢復：on_ready 清理已空的殭屍臨時頻道
- 權限管理使用 channel.set_permissions() 而非身份組，
  確保效果精確且不污染全域身份組

"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.system.settings import get as _s_get
import database.repository.vc_repository as vc_repo
import database.repository.guild_repository as guild_repo

logger = logging.getLogger("bot.voice")


# ── 工具函式 ──────────────────────

def _render_name(template: str, member: discord.Member) -> str:
    """將名稱範本的佔位符替換為實際數值。"""
    return (
        template
        .replace("{username}", member.display_name)
        .replace("{name}",     member.display_name)
        .replace("{guild}",    member.guild.name)
    )


def _get_channel_owner(channel: discord.VoiceChannel) -> str | None:
    """從 DB 取得此頻道的擁有者 ID。"""
    data = vc_repo.get_channel(channel.id)
    return data["owner_id"] if data else None


# ── Cog ──────────────────────

class VoiceChannel(commands.Cog):
    """臨時語音頻道（JTC）系統。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 重啟後清理殭屍頻道 ──────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        Bot 啟動後掃描所有 guild，
        清理 DB 中記錄但實際上已空或不存在的臨時頻道。
        """
        cleaned = 0
        for guild in self.bot.guilds:
            for entry in vc_repo.get_all_channels(guild.id):
                channel = guild.get_channel(entry["channel_id"])
                if channel is None:
                    # 頻道已不存在（可能 Bot 離線期間被手動刪除）
                    vc_repo.delete_channel(entry["channel_id"])
                    cleaned += 1
                elif isinstance(channel, discord.VoiceChannel) and len(channel.members) == 0:
                    # 頻道存在但已空
                    try:
                        await channel.delete(reason="Bot 重啟後清理空的臨時頻道")
                    except discord.HTTPException:
                        pass
                    vc_repo.delete_channel(channel.id)
                    cleaned += 1

        if cleaned:
            logger.info("[voice] 重啟後清理了 %d 個殭屍臨時頻道", cleaned)

    # ── 語音狀態變更事件 ──────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ) -> None:
        """
        監聽語音頻道進出事件：
        1. 進入 JTC 觸發頻道 → 建立並移入臨時頻道
        2. 離開臨時頻道且頻道已空 → 刪除頻道
        """
        guild = member.guild

        # ── 進入觸發頻道 ──────────────────────
        if after.channel is not None:
            await self._handle_join(member, after.channel, guild)

        # ── 離開某個頻道 ──────────────────────
        if before.channel is not None and before.channel != after.channel:
            await self._handle_leave(before.channel, guild)

    async def _handle_join(
        self,
        member:  discord.Member,
        channel: discord.VoiceChannel,
        guild:   discord.Guild,
    ) -> None:
        """
        判斷加入的頻道是否為 JTC 觸發頻道。
        若是，建立臨時頻道並移入成員。
        """
        settings = vc_repo.get_vc_settings(guild.id)
        create_id = settings.get("create_channel", 0) or int(_s_get('voice_channel.jtc_channel_id', 0))

        if not create_id or channel.id != create_id:
            return   # 不是觸發頻道，忽略

        # ── 取得目標類別 ──────────────────────
        category_id = settings.get("category_id", 0) or int(_s_get('voice_channel.category_id', 0))
        category    = guild.get_channel(category_id) if category_id else channel.category

        # ── 組裝名稱與人數上限 ──────────────────────
        template    = settings.get("name_template") or _s_get('voice_channel.default_name_template', '{username} 的頻道')
        ch_name     = _render_name(template, member)
        user_limit  = settings.get("default_limit", 0) or int(_s_get('voice_channel.default_limit', 0))

        # ── 建立頻道（與觸發頻道相同排序位置） ──────────────────────
        try:
            new_channel = await guild.create_voice_channel(
                name       = ch_name,
                category   = category,          # type: ignore[arg-type]
                user_limit = user_limit,
                position   = channel.position + 1,
                reason     = f"JTC：{member} 建立的臨時頻道",
            )
        except discord.Forbidden:
            logger.warning("[voice] 建立語音頻道失敗：缺少權限 guild=%s", guild.name)
            return
        except discord.HTTPException as e:
            logger.warning("[voice] 建立語音頻道失敗: %s", e)
            return

        # ── 給予擁有者管理權限 ──────────────────────
        await new_channel.set_permissions(
            member,
            connect              = True,
            speak                = True,
            manage_channels      = False,   # 透過指令控制，不直接給管理頻道
            move_members         = True,    # 可踢人（移至 AFK 或其他頻道）
        )

        # ── 寫入 DB ──────────────────────
        vc_repo.create_channel(
            channel_id = new_channel.id,
            guild_id   = guild.id,
            owner_id   = str(member.id),
            name       = ch_name,
            user_limit = user_limit,
        )

        # ── 將成員移入新頻道 ──────────────────────
        try:
            await member.move_to(new_channel, reason="JTC：移入臨時頻道")
        except discord.HTTPException as e:
            logger.warning("[voice] 移動成員失敗，嘗試刪除空頻道: %s", e)
            await asyncio.sleep(0.5)
            if len(new_channel.members) == 0:
                await new_channel.delete()
                vc_repo.delete_channel(new_channel.id)
            return

        logger.info("[voice.create] guild=%s channel=%s owner=%s", guild.name, ch_name, member)

    async def _handle_leave(
        self,
        channel: discord.VoiceChannel,
        guild:   discord.Guild,
    ) -> None:
        """
        若離開的頻道是臨時頻道且已空，刪除並清理 DB。
        """
        if not vc_repo.is_temp_channel(channel.id):
            return
        if len(channel.members) > 0:
            return   # 還有人在

        try:
            await channel.delete(reason="臨時語音頻道已空，自動刪除")
        except discord.NotFound:
            pass   # 頻道已不存在，忽略
        except discord.HTTPException as e:
            logger.warning("[voice] 刪除臨時頻道失敗: %s", e)
            return

        vc_repo.delete_channel(channel.id)
        logger.info("[voice.delete] guild=%s channel=%s（已空）", guild.name, channel.name)

    # ── Slash Command 群組 ──────────────────────

    vc_group = app_commands.Group(name="vc", description="語音頻道管理（限頻道擁有者）")

    # ── 權限驗證工具 ──────────────────────

    async def _get_owner_channel(
        self,
        interaction: discord.Interaction,
    ) -> discord.VoiceChannel | None:
        """
        驗證互動者是否為臨時頻道的擁有者。
        回傳 VoiceChannel 表示驗證通過；回傳 None 表示已回應錯誤。
        """
        member = interaction.user
        assert isinstance(member, discord.Member)

        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "請先加入您的臨時語音頻道", ephemeral=True,
            )
            return None

        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "此功能僅適用於語音頻道", ephemeral=True,
            )
            return None

        owner_id = _get_channel_owner(channel)
        if owner_id is None:
            await interaction.response.send_message(
                "您目前所在的頻道不是臨時頻道", ephemeral=True,
            )
            return None

        if owner_id != str(member.id):
            await interaction.response.send_message(
                "您不是此頻道的擁有者", ephemeral=True,
            )
            return None

        return channel

    # ── /vc setup ──────────────────────

    @vc_group.command(name="setup", description="設定「加入即建立」語音頻道")
    @app_commands.describe(
        channel  = "作為觸發器的語音頻道（成員加入此頻道即自動建立臨時頻道）",
        category = "臨時頻道所屬的類別（預設和觸發頻道同一類別）",
        template = "頻道名稱範本，{username} 會替換為成員名稱",
        limit    = "預設人數上限（0 = 無上限）",
    )
    @app_commands.default_permissions(administrator=True)
    async def cmd_setup(
        self,
        interaction: discord.Interaction,
        channel:     discord.VoiceChannel,
        category:    discord.CategoryChannel | None = None,
        template:    str = "{username} 的頻道",
        limit:       app_commands.Range[int, 0, 99] = 0,
    ) -> None:
        guild_id = interaction.guild.id
        vc_repo.set_vc_setting(guild_id, "create_channel",  channel.id)
        vc_repo.set_vc_setting(guild_id, "category_id",     category.id if category else 0)
        vc_repo.set_vc_setting(guild_id, "name_template",   template)
        vc_repo.set_vc_setting(guild_id, "default_limit",   limit)

        desc_lines = [
            f"觸發頻道：**{channel.name}**",
            f"類別：{category.name if category else '（與觸發頻道相同）'}",
            f"名稱範本：`{template}`",
            f"預設人數上限：{'無上限' if limit == 0 else str(limit)}",
        ]

        embed = discord.Embed(
            title       = "JTC 語音頻道已設定",
            description = "\n".join(desc_lines),
            color       = discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(
            "[voice.setup] guild=%s channel=%s template=%r limit=%d",
            interaction.guild.name, channel.name, template, limit,
        )

    # ── /vc name ──────────────────────

    @vc_group.command(name="name", description="更改您的臨時頻道名稱")
    @app_commands.describe(name="新的頻道名稱（最長 100 字元）")
    async def cmd_name(
        self,
        interaction: discord.Interaction,
        name:        app_commands.Range[str, 1, 100],
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        try:
            await channel.edit(name=name, reason=f"{interaction.user} 更名臨時頻道")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"更名失敗：{e}", ephemeral=True)
            return

        vc_repo.update_channel(channel.id, "name", name)
        await interaction.response.send_message(f"頻道已更名為 **{name}**", ephemeral=True)

    # ── /vc limit ──────────────────────

    @vc_group.command(name="limit", description="設定頻道人數上限（0 = 無上限）")
    @app_commands.describe(limit="人數上限（0-99，0 為無上限）")
    async def cmd_limit(
        self,
        interaction: discord.Interaction,
        limit:       app_commands.Range[int, 0, 99],
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        try:
            await channel.edit(user_limit=limit)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"設定失敗：{e}", ephemeral=True)
            return

        vc_repo.update_channel(channel.id, "user_limit", limit)
        msg = f"人數上限已設為 **{limit}**" if limit else "人數上限已取消（無上限）"
        await interaction.response.send_message(msg, ephemeral=True)

    # ── /vc lock ──────────────────────

    @vc_group.command(name="lock", description="鎖定頻道，阻止新成員進入")
    async def cmd_lock(self, interaction: discord.Interaction) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=False,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"鎖定失敗：{e}", ephemeral=True)
            return

        vc_repo.update_channel(channel.id, "is_locked", 1)
        await interaction.response.send_message("頻道已鎖定，新成員無法進入", ephemeral=True)

    # ── /vc unlock ──────────────────────

    @vc_group.command(name="unlock", description="解鎖頻道，恢復正常進入")
    async def cmd_unlock(self, interaction: discord.Interaction) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                connect=None,   # 移除覆蓋，恢復預設
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"解鎖失敗：{e}", ephemeral=True)
            return

        vc_repo.update_channel(channel.id, "is_locked", 0)
        await interaction.response.send_message("頻道已解鎖", ephemeral=True)

    # ── /vc permit ──────────────────────

    @vc_group.command(name="permit", description="允許指定成員進入已鎖定的頻道")
    @app_commands.describe(member="要允許進入的成員")
    async def cmd_permit(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        try:
            await channel.set_permissions(member, connect=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"設定失敗：{e}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"已允許 {member.mention} 進入此頻道", ephemeral=True,
        )

    # ── /vc reject ──────────────────────

    @vc_group.command(name="reject", description="禁止指定成員進入此頻道")
    @app_commands.describe(member="要禁止進入的成員")
    async def cmd_reject(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        if member == interaction.user:
            await interaction.response.send_message("不能禁止自己進入", ephemeral=True)
            return

        try:
            await channel.set_permissions(member, connect=False)
            # 若該成員正在頻道內，踢出
            if member in channel.members:
                await member.move_to(None, reason=f"{interaction.user} 從臨時頻道移除")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"設定失敗：{e}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"已禁止 {member.mention} 進入此頻道", ephemeral=True,
        )

    # ── /vc kick ──────────────────────

    @vc_group.command(name="kick", description="將成員踢出您的頻道")
    @app_commands.describe(member="要踢出的成員")
    async def cmd_kick(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        if member == interaction.user:
            await interaction.response.send_message("不能踢出自己", ephemeral=True)
            return

        if member not in channel.members:
            await interaction.response.send_message(
                f"{member.display_name} 不在此頻道中", ephemeral=True,
            )
            return

        try:
            await member.move_to(None, reason=f"{interaction.user} 踢出臨時頻道成員")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"踢出失敗：{e}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"已將 {member.display_name} 踢出頻道", ephemeral=True,
        )

    # ── /vc transfer ──────────────────────

    @vc_group.command(name="transfer", description="將頻道所有權轉移給其他成員")
    @app_commands.describe(member="要轉移所有權的對象（必須在頻道內）")
    async def cmd_transfer(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
    ) -> None:
        channel = await self._get_owner_channel(interaction)
        if channel is None:
            return

        if member == interaction.user:
            await interaction.response.send_message("不能轉移給自己", ephemeral=True)
            return

        if member not in channel.members:
            await interaction.response.send_message(
                f"{member.display_name} 不在此頻道中，無法轉移", ephemeral=True,
            )
            return

        vc_repo.update_channel(channel.id, "owner_id", str(member.id))

        # 移除舊擁有者的 move_members 權限，給予新擁有者
        try:
            await channel.set_permissions(interaction.user, overwrite=None)
            await channel.set_permissions(member, connect=True, move_members=True)
        except discord.HTTPException:
            pass   # 權限設定失敗不影響所有權轉移

        await interaction.response.send_message(
            f"已將頻道所有權轉移給 {member.mention}", ephemeral=True,
        )
        logger.info(
            "[voice.transfer] channel=%s from=%s to=%s",
            channel.name, interaction.user, member,
        )

    # ── /vc info ──────────────────────

    @vc_group.command(name="info", description="查看目前臨時頻道的設定")
    async def cmd_info(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        assert isinstance(member, discord.Member)

        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message("請先加入語音頻道", ephemeral=True)
            return

        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message("此功能僅適用於語音頻道", ephemeral=True)
            return

        data = vc_repo.get_channel(channel.id)
        if not data:
            await interaction.response.send_message("此頻道不是臨時頻道", ephemeral=True)
            return

        owner = interaction.guild.get_member(int(data["owner_id"]))
        owner_mention = owner.mention if owner else f"ID: {data['owner_id']}"

        embed = discord.Embed(
            title     = f"語音頻道：{channel.name}",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(name="擁有者",   value=owner_mention,                                     inline=True)
        embed.add_field(name="人數上限", value=str(data["user_limit"]) if data["user_limit"] else "無上限", inline=True)
        embed.add_field(name="鎖定狀態", value="已鎖定" if data["is_locked"] else "開放",           inline=True)
        embed.add_field(name="目前人數", value=f"{len(channel.members)} 人",                       inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /vc forcedelete（管理員強制刪除） ──────────────────────

    @vc_group.command(name="forcedelete", description="管理員強制刪除指定臨時頻道")
    @app_commands.describe(channel="要強制刪除的語音頻道")
    @app_commands.default_permissions(administrator=True)
    async def cmd_forcedelete(
        self,
        interaction: discord.Interaction,
        channel:     discord.VoiceChannel,
    ) -> None:
        if not vc_repo.is_temp_channel(channel.id):
            await interaction.response.send_message("此頻道不是臨時頻道", ephemeral=True)
            return

        try:
            await channel.delete(reason=f"管理員 {interaction.user} 強制刪除")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"刪除失敗：{e}", ephemeral=True)
            return

        vc_repo.delete_channel(channel.id)
        await interaction.response.send_message(
            f"已強制刪除臨時頻道 **{channel.name}**", ephemeral=True,
        )


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceChannel(bot))
