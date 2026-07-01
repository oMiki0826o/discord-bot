"""
cogs/talk/webhook.py

職責：
- /webhook：以自訂名稱與頭像透過 Webhook 發送訊息
- 支援附件（最多 3 個）、圖片 URL、模擬回覆（prepend quote）
- Webhook 快取：同一頻道不重複建立

Modification():

- 移植自 Bot-Firefly/cogs/talk/webhook.py
- 類別命名改為 WebhookSender（PEP 8）
- 加入 from __future__ import annotations
- 使用 aiohttp 非同步確認 webhook 仍有效（取代 webhook.fetch()）

- 修正 /webhook 的權限檢查方式：由 @app_commands.checks.has_permissions
  改為 @app_commands.default_permissions（原因同 say.py）

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.talk.webhook")


class WebhookSender(commands.Cog):
    """Webhook 偽裝發話。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot   = bot
        self._cache: dict[int, discord.Webhook] = {}   # channel_id → webhook

    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        """取得（或建立）頻道的 Bot 專屬 Webhook，並快取。"""
        cached = self._cache.get(channel.id)
        if cached:
            try:
                # 嘗試發送空字串確認仍有效（若 NotFound 則重建）
                existing = [w for w in await channel.webhooks() if w.id == cached.id]
                if existing:
                    return cached
            except discord.HTTPException:
                pass
            self._cache.pop(channel.id, None)

        # 找或建立 Bot 的 Webhook
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.user and wh.user.id == self.bot.user.id:
                self._cache[channel.id] = wh
                return wh

        wh = await channel.create_webhook(name="Firefly Webhook")
        self._cache[channel.id] = wh
        return wh

    @app_commands.command(name="webhook", description="使用 Webhook 以自訂名稱發送訊息")
    @app_commands.describe(
        content    = "訊息內容",
        username   = "顯示名稱（預設使用你的名稱）",
        avatar_url = "頭像 URL（選填）",
        image_url  = "附加圖片 URL（選填）",
        message_id = "要引用的訊息 ID（選填）",
        image1     = "附件圖片 1",
        image2     = "附件圖片 2",
        image3     = "附件圖片 3",
    )
    @app_commands.default_permissions(manage_webhooks=True)
    async def cmd_webhook(
        self,
        interaction: discord.Interaction,
        content:     str,
        username:    str | None                = None,
        avatar_url:  str | None                = None,
        image_url:   str | None                = None,
        message_id:  str | None                = None,
        image1:      discord.Attachment | None = None,
        image2:      discord.Attachment | None = None,
        image3:      discord.Attachment | None = None,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("此指令僅限文字頻道使用。", ephemeral=True)
            return

        # 檢查 Bot 有 manage_webhooks 權限
        me = channel.guild.me
        if not channel.permissions_for(me).manage_webhooks:
            await interaction.response.send_message("Bot 缺少 Manage Webhooks 權限。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # 取得引用訊息
        reference_text = ""
        if message_id:
            try:
                ref_msg        = await channel.fetch_message(int(message_id))
                reference_text = f"> 回覆 {ref_msg.author.mention}\n"
            except (discord.NotFound, ValueError):
                pass

        files = [
            await img.to_file()
            for img in (image1, image2, image3)
            if img is not None
        ]

        final_content = reference_text + content
        send_name     = username   or interaction.user.display_name
        send_avatar   = avatar_url or str(interaction.user.display_avatar.url)

        try:
            wh = await self._get_webhook(channel)
            await wh.send(final_content, username=send_name, avatar_url=send_avatar, files=files)

            if image_url:
                em = discord.Embed()
                em.set_image(url=image_url)
                await wh.send(embed=em, username=send_name, avatar_url=send_avatar)

            await interaction.followup.send("已發送。", ephemeral=True)
            logger.info("[webhook] channel=%s by=%s", channel.name, interaction.user)

        except Exception as e:
            await interaction.followup.send(f"錯誤：```{e}```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WebhookSender(bot))
