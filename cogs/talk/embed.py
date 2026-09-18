"""
cogs/talk/embed.py

職責：
- /embed：全功能 Embed 建構器，支援標題、描述、顏色、作者、頁腳、縮圖、圖片、回覆

Modification():

- 移植自 Bot-Firefly/cogs/talk/embed.py
- 類別命名改為 EmbedBuilder（PEP 8）
- 加入 from __future__ import annotations
- 顏色解析失敗給出明確提示

- /embed 同時使用 default_permissions 與執行期 has_permissions，
  並限制於伺服器頻道。

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
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(send_messages=True, embed_links=True)
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
        # 讀取引用訊息或送出 Embed 可能超過 Discord 的 3 秒互動期限，
        # 一開始先確認互動，後續一律使用 followup 回覆狀態。
        await interaction.response.defer(ephemeral=True)
        channel   = interaction.channel
        reference = await _fetch_reference(channel, message_id)

        # 顏色解析
        embed_color = discord.Color.blue()
        color_warning = ""
        if color:
            try:
                embed_color = discord.Color.from_str(color)
            except ValueError:
                color_warning = (
                    f"\n無效的顏色 `{color}`，已使用預設藍色。"
                    "（範例：`#FF5733` 或 `red`）"
                )

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
            await interaction.followup.send(f"已發送。{color_warning}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"錯誤：```{e}```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmbedBuilder(bot))


# /embed 已整合進 /say 面板；保留 Command 物件作為共用發送實作，
# 但不再將它註冊為獨立的 Slash Command。
EmbedBuilder.__cog_app_commands__ = []
