"""
cogs/events/message.py

職責：
- 私訊轉發：將一般使用者發送的 DM 轉傳給 Bot Owner
- Owner 回覆橋接：Owner 在 DM 中回覆轉發訊息時，自動送回原私訊者
- 提供 last_dm_user_id 屬性，供 /reply 指令快速回覆最近一筆私訊

Modification():

- 新增 last_dm_user_id property（原本缺少此屬性）
  /reply 指令（cogs/system/owner.py）透過 Messenger cog 讀取此值
  來確定要回覆的對象，原版沒有此屬性導致 /reply 無法取得最近私訊者
- 其他邏輯維持不變：Owner 解析快取、DM 映射容量上限、訊息轉發

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
        self.bot          = bot
        # forward_message_id -> sender_user_id（OrderedDict 維持插入順序）
        self._dm_map:      OrderedDict[int, int] = OrderedDict()
        self._owner_id:    int | None            = getattr(bot, "owner_id", None)
        self._owner_user:  discord.User | None   = None

    # ── 公開屬性 ──────────────────────

    @property
    def last_dm_user_id(self) -> int | None:
        """
        回傳最近一筆私訊者的使用者 ID。

        新增：/reply 指令（cogs/system/owner.py）依賴此屬性
        來決定預設回覆對象；無紀錄時回傳 None。

        _dm_map 以 OrderedDict 儲存，move_to_end() 確保最新的在尾端，
        所以 reversed() 的第一個元素即為最近一筆。
        """
        if not self._dm_map:
            return None
        return next(reversed(self._dm_map.values()))

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

        owner            = app_info.owner
        self._owner_id   = owner.id
        self._owner_user = owner
        return owner

    async def _resolve_owner_id(self) -> int | None:
        """取得 Owner ID；必要時先解析 Owner 使用者。"""
        if self._owner_id:
            return self._owner_id
        owner = await self._resolve_owner()
        return owner.id if owner is not None else None

    # ── DM 映射維護 ──────────────────────

    def _remember_forward(self, forward_message_id: int, sender_user_id: int) -> None:
        """
        記住 Owner 端轉發訊息與原私訊者的對應關係。

        move_to_end() 確保最新的訊息排在尾端，
        last_dm_user_id 才能正確取到最近一筆。
        """
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

    # ── Owner 回覆橋接 ──────────────────────

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
            user   = await self.bot.fetch_user(sender_id)
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