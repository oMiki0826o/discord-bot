"""
cogs/events/message.py

職責：
- 監聽所有訊息事件
- 私訊（DM）自動轉發給 Bot Owner，並記錄至日誌
- Owner 回覆轉發訊息時，可反向傳訊息給原私訊者（雙向 DM 橋接）
- 伺服器訊息：提供 on_message 後處理鉤子（目前傳給 process_commands）

Modification():

- 移植自 Bot-Firefly/cogs/events/message.py
- 新增：反向回覆橋接（Owner 回覆轉發訊息 → Bot 私訊回原使用者）
- 所有 f-string 日誌改為 % 格式

"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

logger = logging.getLogger("bot.events.message")


class Messenger(commands.Cog):
    """私訊轉發與訊息事件處理。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # user_id → message 轉發訊息的 ID（供 Owner 回覆對應）
        self._dm_map: dict[int, int] = {}   # forward_msg_id → sender_user_id

    # ── on_message ──────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # ── DM 轉發 ──────────────────────
        if message.guild is None:
            await self._handle_dm(message)
            return

        # ── 伺服器訊息：確保前綴指令仍可運作 ──────────────────────
        await self.bot.process_commands(message)

    # ── DM 核心邏輯 ──────────────────────

    async def _handle_dm(self, message: discord.Message) -> None:
        """將私訊轉發給 Bot Owner，並維護雙向映射。"""
        logger.info("[DM] from=%s content=%r", message.author, message.content[:80])

        try:
            app_info = await self.bot.application_info()
            owner    = app_info.owner
            if not owner:
                return
        except Exception as e:
            logger.error("[DM] 取得 Owner 失敗: %s", e)
            return

        # 組裝轉發訊息
        lines = [
            f"**收到私訊**",
            f"來自：**{message.author}**（ID: `{message.author.id}`）",
        ]
        if message.content:
            lines.append(f"內容：{message.content}")

        try:
            fwd_msg = await owner.send("\n".join(lines))
            # 建立映射：轉發訊息 → 原使用者 ID
            self._dm_map[fwd_msg.id] = message.author.id
            logger.info("[DM] 已轉發給 Owner %s", owner)
        except discord.HTTPException as e:
            logger.error("[DM] 轉發失敗: %s", e)

        # 轉發附件
        if message.attachments:
            for att in message.attachments:
                try:
                    await owner.send(f"附件：{att.url}")
                except discord.HTTPException:
                    pass

    # ── Owner 回覆橋接 ──────────────────────

    @commands.Cog.listener()
    async def on_message_reply(self, message: discord.Message) -> None:
        """
        若 Owner 在 DM 中回覆了轉發訊息，
        Bot 將把 Owner 的回覆私訊給原發送者。
        """
        if not message.guild and message.reference and not message.author.bot:
            app_info = await self.bot.application_info()
            if message.author.id != app_info.owner.id:
                return

            ref_id    = message.reference.message_id
            sender_id = self._dm_map.get(ref_id)
            if not sender_id:
                return

            try:
                user = await self.bot.fetch_user(sender_id)
                await user.send(f"**Bot 回覆：**\n{message.content}")
                logger.info("[DM回覆] 已轉發給使用者 %s", user)
            except discord.HTTPException as e:
                logger.error("[DM回覆] 失敗: %s", e)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Messenger(bot))
