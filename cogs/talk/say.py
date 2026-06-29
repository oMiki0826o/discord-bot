"""
cogs/talk/say.py

職責：
- /say：以 Bot 身份在目前頻道發送訊息（支援附件、回覆、圖片 URL）
- 使用者需有 Manage Messages 權限

Modification():

- 移植自 Bot-Firefly/cogs/talk/say.py
- 加入 from __future__ import annotations
- 類別命名改為 Say（PEP 8）
- 錯誤處理更細緻

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


class Say(commands.Cog):
    """Bot 代發訊息指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="say", description="使用 Bot 發送訊息")
    @app_commands.describe(
        content    = "要發送的文字內容",
        image_url  = "圖片網址（選填）",
        message_id = "要回覆的訊息 ID（選填）",
        image1     = "附件圖片 1（選填）",
        image2     = "附件圖片 2（選填）",
        image3     = "附件圖片 3（選填）",
    )
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def cmd_say(
        self,
        interaction: discord.Interaction,
        content:     str,
        image_url:   str | None                  = None,
        message_id:  str | None                  = None,
        image1:      discord.Attachment | None   = None,
        image2:      discord.Attachment | None   = None,
        image3:      discord.Attachment | None   = None,
    ) -> None:
        channel   = interaction.channel
        reference = await _fetch_reference(channel, message_id)

        files = [
            await img.to_file()
            for img in (image1, image2, image3)
            if img is not None
        ]

        try:
            await channel.send(content, files=files, reference=reference)

            if image_url:
                embed = discord.Embed()
                embed.set_image(url=image_url)
                await channel.send(embed=embed, reference=reference)

            await interaction.response.send_message("已發送。", ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message("Bot 沒有發送訊息的權限。", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("找不到指定的訊息 ID。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"錯誤：```{e}```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Say(bot))
