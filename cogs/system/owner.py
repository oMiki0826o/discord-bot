"""
cogs/system/owner.py

職責：
- Owner 專用前綴指令（$game、$slash、$slash_guild）
- Owner 專用 Slash 指令（/reply、/talk）
- $game [type] <文字>：即時更改 Bot 狀態，並持久化至 settings.json
- $slash：同步 Slash Commands 至全域
- $slash_guild：即時同步 Slash Commands 至當前伺服器（測試用）
- /reply：回覆最近私訊的使用者或指定 user ID
- /talk：讓機器人私訊指定使用者

Modification():

- 恢復 /reply 和 /talk slash 指令（重構時遺失）
- 新增 _is_owner() 用於 app_commands.check，與 commands.is_owner() 效果相同
  但適用於 Slash Commands（Interaction 物件而非 Context）
- 新增 _reply_error() 工具函式，統一 followup/response 發送錯誤訊息的邏輯
- 新增 _resolve_dm_target() 輔助方法，解析 /reply 的目標使用者 ID
  優先使用明確提供的 user_id，否則從 Messenger.last_dm_user_id 取得
- $game 維持原有設定持久化邏輯（write_value 至 settings.json）

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.logging.log     import LogManager
from core.system.settings import get, write_value

logger = LogManager().get_logger("cogs.system.owner")

# ── 所有可選的 status_type 供 $game 使用 ──────────────────────

_VALID_TYPES: frozenset[str] = frozenset({"playing", "listening", "watching", "competing"})

_ACTIVITY_TYPE_MAP: dict[str, discord.ActivityType] = {
    "playing":   discord.ActivityType.playing,
    "listening": discord.ActivityType.listening,
    "watching":  discord.ActivityType.watching,
    "competing": discord.ActivityType.competing,
}


# ── Slash 指令 Owner 驗證 ──────────────────────

def _is_owner() -> app_commands.check:
    """
    app_commands.check 版本的 is_owner 驗證。

    commands.is_owner() 只適用於前綴指令（Context），
    Slash 指令（Interaction）需要獨立實作。
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        return await interaction.client.is_owner(interaction.user)
    return app_commands.check(predicate)


# ── 私訊錯誤回覆工具 ──────────────────────

async def _reply_error(interaction: discord.Interaction, message: str) -> None:
    """
    統一發送錯誤訊息。

    已 defer 時使用 followup，否則直接 response，
    避免 InteractionResponded / NotResponded 例外。
    """
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ── Cog ──────────────────────

class Owner(commands.Cog, name="Owner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── $game ──────────────────────

    @commands.command(name="game", hidden=True)
    @commands.is_owner()
    async def set_game(
        self,
        ctx:  commands.Context,
        *,
        name: str,
    ) -> None:
        """
        $game <文字>           → 套用目前 settings.json 的 status_type
        $game playing <文字>   → 遊玩
        $game listening <文字> → 收聽
        $game watching <文字>  → 觀看
        $game competing <文字> → 競賽

        設定同時持久化至 settings.json，$settings reload 後仍生效。
        """
        # ── 解析可選的 type 前綴 ──────────────────────
        parts       = name.split(None, 1)
        status_type = get("bot.status_type", "listening")
        status_text = name

        if len(parts) >= 2 and parts[0].lower() in _VALID_TYPES:
            status_type = parts[0].lower()
            status_text = parts[1]

        # ── 立即套用 ──────────────────────
        activity = discord.Activity(
            type = _ACTIVITY_TYPE_MAP.get(status_type, discord.ActivityType.listening),
            name = status_text,
        )
        await self.bot.change_presence(activity=activity)

        # ── 持久化至 settings.json ──────────────────────
        try:
            write_value("bot.status_type", status_type)
            write_value("bot.status_text", status_text)
        except Exception as e:
            logger.warning("[owner.$game] 寫入 settings.json 失敗: %s", e)
            await ctx.send(
                f"狀態已更改為：`{status_text}`（類型：{status_type}）\n"
                f"注意：settings.json 寫入失敗（{e}），重啟後可能不會保留"
            )
            return

        await ctx.send(
            f"狀態已更改為：`{status_text}`（類型：{status_type}）\n"
            "設定已寫入 settings.json，重啟後仍然生效"
        )
        logger.info("[owner.$game] type=%s text=%s by=%s", status_type, status_text, ctx.author)

    # ── $slash ──────────────────────

    @commands.command(name="slash", hidden=True)
    @commands.is_owner()
    async def slash(self, ctx: commands.Context) -> None:
        """$slash — 同步 Slash Commands 至全域（最多 1 小時生效）"""
        synced    = await self.bot.tree.sync()
        cmd_names = "\n".join(f"  /{c.name}" for c in self.bot.tree.get_commands())
        await ctx.send(
            f"已同步 {len(synced)} 個 Slash Commands\n"
            f"```\n{cmd_names or '（無）'}\n```"
        )
        logger.info("[owner.$slash] 同步 %d 個指令 by %s", len(synced), ctx.author)

    # ── $slash_guild ──────────────────────

    @commands.command(name="slash_guild", hidden=True)
    @commands.is_owner()
    async def slash_guild(self, ctx: commands.Context) -> None:
        """$slash_guild — 即時同步 Slash Commands 至當前伺服器（測試用）"""
        guild = ctx.guild
        if not guild:
            await ctx.send("此指令僅限在伺服器中使用")
            return
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await ctx.send(f"已即時同步 {len(synced)} 個 Slash Commands 至 **{guild.name}**")
        logger.info(
            "[owner.$slash_guild] 同步 %d 個指令至 %s by %s",
            len(synced), guild.name, ctx.author,
        )

    # ── /reply ──────────────────────

    @app_commands.command(name="reply", description="回覆最近私訊的使用者或指定 user ID")
    @app_commands.describe(
        content = "要回覆的內容",
        user_id = "指定使用者 ID（可選；省略時回覆最近一筆私訊者）",
    )
    @_is_owner()
    async def cmd_reply(
        self,
        interaction: discord.Interaction,
        content:     str,
        user_id:     str | None = None,
    ) -> None:
        """
        以 Bot 身份私訊指定使用者或最近私訊者。

        恢復：此 Slash 指令在重構 cogs/system/owner.py 時遺失，
        現在重新整合至此檔案。
        user_id 省略時，透過 Messenger.last_dm_user_id 取得最近私訊者。
        """
        await interaction.response.defer(ephemeral=True)

        target_id = await self._resolve_dm_target(interaction, user_id)
        if target_id is None:
            return

        # ── 取得使用者並傳送 ──────────────────────
        try:
            user = await self.bot.fetch_user(target_id)
        except discord.NotFound:
            await _reply_error(interaction, f"找不到使用者 `{target_id}`")
            return
        except discord.HTTPException as e:
            await _reply_error(interaction, f"取得使用者失敗：`{e}`")
            return

        try:
            await user.send(f"來自擁有者回覆：\n{content}")
        except discord.Forbidden:
            await _reply_error(interaction, "對方關閉私訊")
            return
        except discord.HTTPException as e:
            await _reply_error(interaction, f"傳送失敗：`{e}`")
            return

        await interaction.followup.send(
            f"已回覆給 {user}（ID: `{target_id}`）",
            ephemeral=True,
        )
        logger.info("[owner./reply] → %s (%s): %s", user, target_id, content[:80])

    async def _resolve_dm_target(
        self,
        interaction: discord.Interaction,
        user_id:     str | None,
    ) -> int | None:
        """
        解析 /reply 目標使用者 ID。

        優先使用明確提供的 user_id；省略時從 Messenger.last_dm_user_id 取得。
        任何解析失敗皆回傳 None 並已發送錯誤訊息。
        """
        if user_id is not None:
            try:
                return int(user_id)
            except ValueError:
                await _reply_error(interaction, "user ID 格式錯誤，請輸入純數字 ID")
                return None

        messenger = self.bot.get_cog("Messenger")
        last_id   = getattr(messenger, "last_dm_user_id", None)

        if messenger is None or last_id is None:
            await _reply_error(interaction, "沒有最近私訊使用者紀錄")
            return None

        return last_id

    # ── /talk ──────────────────────

    @app_commands.command(name="talk", description="讓機器人私訊指定使用者")
    @app_commands.describe(
        user    = "目標使用者",
        content = "文字內容（支援 Markdown）",
        image   = "圖片 URL（可選）",
    )
    @_is_owner()
    async def cmd_talk(
        self,
        interaction: discord.Interaction,
        user:        discord.User,
        content:     str,
        image:       str | None = None,
    ) -> None:
        """
        以 Bot 身份主動私訊指定使用者。

        恢復：此 Slash 指令在重構 cogs/system/owner.py 時遺失。
        image 提供時以 Embed 傳送（方便附加圖片預覽），否則以純文字傳送。
        """
        try:
            if image:
                embed = discord.Embed(description=content)
                embed.set_image(url=image)
                await user.send(embed=embed)
            else:
                await user.send(content)

            await interaction.response.send_message(
                f"已私訊 {user.mention}",
                ephemeral=True,
            )
            logger.info("[owner./talk] → %s: %s", user, content[:80])

        except discord.Forbidden:
            await interaction.response.send_message("對方關閉私訊", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"傳送失敗：`{e}`", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Owner(bot))