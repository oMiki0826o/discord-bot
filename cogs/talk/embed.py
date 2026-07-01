"""
cogs/talk/embed.py

職責：
- /embed：全功能 Embed 建構器，支援標題、描述、顏色、作者、頁腳、縮圖、圖片、回覆

Modification():

- 移植自 Bot-Firefly/cogs/talk/embed.py
- 類別命名改為 EmbedBuilder（PEP 8）
- 加入 from __future__ import annotations
- 顏色解析失敗給出明確提示

- 修正 /embed 的權限檢查方式：由 @app_commands.checks.has_permissions
  改為 @app_commands.default_permissions（原因同 say.py）

"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


async def _fetch_reference(
    channel: discord.TextChannel,
    message_id: str | None,
) -> discord.Message | None:
    if not message_id:
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except (discord.NotFound, ValueError):
        return None


class EmbedBuilder(commands.Cog):
    """Embed 訊息建構器。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="embed", description="發送自訂 Embed 訊息")
    @app_commands.describe(
        title       = "標題",
        description = "內文",
        color       = "顏色（HEX #RRGGBB 或顏色名稱，如 red）",
        author      = "作者名稱",
        author_icon = "作者圖示 URL",
        footer      = "頁腳文字",
        footer_icon = "頁腳圖示 URL",
        thumbnail   = "縮圖 URL",
        image_url   = "主要圖片 URL",
        message_id  = "要回覆的訊息 ID",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def cmd_embed(
        self,
        interaction: discord.Interaction,
        title:       str | None = None,
        description: str | None = None,
        color:       str | None = None,
        author:      str | None = None,
        author_icon: str | None = None,
        footer:      str | None = None,
        footer_icon: str | None = None,
        thumbnail:   str | None = None,
        image_url:   str | None = None,
        message_id:  str | None = None,
    ) -> None:
        channel   = interaction.channel
        reference = await _fetch_reference(channel, message_id)

        # 顏色解析
        embed_color = discord.Color.blue()
        if color:
            try:
                embed_color = discord.Color.from_str(color)
            except ValueError:
                await interaction.response.send_message(
                    f"無效的顏色 `{color}`，使用預設藍色。（範例：`#FF5733` 或 `red`）",
                    ephemeral=True,
                )
                # 不 return，繼續用預設顏色發送

        embed = discord.Embed(
            title       = title,
            description = description,
            color       = embed_color,
        )
        if author:
            embed.set_author(name=author, icon_url=author_icon)
        if footer:
            embed.set_footer(text=footer, icon_url=footer_icon)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image_url:
            embed.set_image(url=image_url)

        try:
            await channel.send(embed=embed, reference=reference)
            # 若已透過顏色錯誤回應，改用 followup
            try:
                await interaction.response.send_message("已發送。", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send("已發送。", ephemeral=True)
        except Exception as e:
            try:
                await interaction.response.send_message(f"錯誤：```{e}```", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(f"錯誤：```{e}```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmbedBuilder(bot))
