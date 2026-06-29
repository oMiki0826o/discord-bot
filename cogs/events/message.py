"""
cogs/events/message.py

Modification():

- 移除伺服器訊息中的 bot.process_commands()，避免前綴指令被 Discord.py 與 Cog 各處理一次。
- 將 Owner 回覆橋接整合進單一 on_message 入口，避免同一則 DM 被多個 listener 重複處理。
- Owner 查詢加入快取，減少每則 DM 都呼叫 application_info() 的成本。
- DM 轉發映射加入容量上限，避免長時間運行後無限制累積。

Description():

- 本檔只負責私訊轉發與 Owner 回覆橋接。
- 伺服器前綴指令由 commands.Bot 內建 on_message 處理，本 Cog 不再手動觸發。
"""

from __future__ import annotations

import logging
from collections import OrderedDict

import discord
from discord.ext import commands

from core.system.settings import get_int, get_str

logger = logging.getLogger("bot.events.message")


# ── 私訊橋接 Cog ──────────────────────

class Messenger(commands.Cog):
    """私訊轉發與 Owner 回覆橋接。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._dm_map: OrderedDict[int, int] = OrderedDict()
        self._owner_id: int | None = getattr(bot, "owner_id", None)
        self._owner_user: discord.User | None = None

    # ── 訊息事件入口 ──────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只處理 DM；伺服器訊息交給 commands.Bot 預設流程。"""
        if message.author.bot or message.guild is not None:
            return

        if await self._handle_owner_reply(message):
            return

        await self._forward_dm_to_owner(message)

    # ── Owner 解析 ──────────────────────

    async def _resolve_owner(self) -> discord.User | None:
        """取得 Bot Owner，優先使用 config.OWNER_ID 注入到 bot.owner_id 的值。"""
        if self._owner_user is not None:
            return self._owner_user

        if self._owner_id:
            user = self.bot.get_user(self._owner_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(self._owner_id)
                except discord.HTTPException as exc:
                    logger.error("[DM] 取得 Owner 失敗 owner_id=%s: %s", self._owner_id, exc)
                    return None
            self._owner_user = user
            return user

        try:
            app_info = await self.bot.application_info()
        except discord.HTTPException as exc:
            logger.error("[DM] 取得 application_info 失敗: %s", exc)
            return None

        owner = app_info.owner
        self._owner_id = owner.id
        self._owner_user = owner
        return owner

    async def _resolve_owner_id(self) -> int | None:
        """取得 Owner ID；必要時會先解析 Owner 使用者。"""
        if self._owner_id:
            return self._owner_id
        owner = await self._resolve_owner()
        return owner.id if owner is not None else None

    # ── DM 映射維護 ──────────────────────

    def _remember_forward(self, forward_message_id: int, sender_user_id: int) -> None:
        """記住 Owner 端轉發訊息與原私訊者的對應關係。"""
        self._dm_map[forward_message_id] = sender_user_id
        self._dm_map.move_to_end(forward_message_id)

        limit = max(1, get_int("dm.forward_map_limit", 200))
        while len(self._dm_map) > limit:
            self._dm_map.popitem(last=False)

    # ── 私訊轉發 ──────────────────────

    async def _forward_dm_to_owner(self, message: discord.Message) -> None:
        """將一般使用者私訊轉發給 Owner。"""
        owner = await self._resolve_owner()
        if owner is None:
            return
        if message.author.id == owner.id:
            return

        logger.info("[DM] from=%s content=%r", message.author, message.content[:80])

        lines = [
            "**收到私訊**",
            f"來自：**{message.author}**（ID: `{message.author.id}`）",
        ]
        if message.content:
            lines.append(f"內容：{message.content}")

        try:
            forward_message = await owner.send("\n".join(lines))
            self._remember_forward(forward_message.id, message.author.id)
            logger.info("[DM] 已轉發給 Owner %s", owner)
        except discord.HTTPException as exc:
            logger.error("[DM] 轉發失敗: %s", exc)
            return

        for attachment in message.attachments:
            try:
                await owner.send(f"附件：{attachment.url}")
            except discord.HTTPException as exc:
                logger.warning("[DM] 附件轉發失敗 filename=%s: %s", attachment.filename, exc)

    async def _handle_owner_reply(self, message: discord.Message) -> bool:
        """Owner 在 DM 回覆轉發訊息時，將內容送回原私訊者。"""
        if message.reference is None or message.reference.message_id is None:
            return False

        owner_id = await self._resolve_owner_id()
        if owner_id is None or message.author.id != owner_id:
            return False

        sender_id = self._dm_map.get(message.reference.message_id)
        if sender_id is None:
            return False

        if not message.content and not message.attachments:
            return True

        try:
            user = await self.bot.fetch_user(sender_id)
            prefix = get_str("dm.owner_reply_prefix", "**Bot 回覆：**\n")
            if message.content:
                await user.send(f"{prefix}{message.content}")
            for attachment in message.attachments:
                await user.send(f"{prefix}附件：{attachment.url}")
            logger.info("[DM回覆] 已轉發給使用者 %s", user)
        except discord.HTTPException as exc:
            logger.error("[DM回覆] 失敗 sender_id=%s: %s", sender_id, exc)

        return True


# ── Extension 入口 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Messenger(bot))
