"""流螢角色相關的娛樂指令。"""

from __future__ import annotations

import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands


_IMAGE_DIR = Path(__file__).resolve().parent / "images" / "firefly"
_FIREFLY_IMAGES = (
    _IMAGE_DIR / "firefly_chan_1.jpg",
    _IMAGE_DIR / "firefly_chan_2.jpg",
)


def _choose_firefly_image() -> Path:
    """隨機選擇一張回覆圖片。"""
    return random.choice(_FIREFLY_IMAGES)


class Firefly(commands.Cog):
    """流螢娛樂指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="流螢醬", description="流螢…醬？")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_firefly_chan(self, interaction: discord.Interaction) -> None:
        image_path = _choose_firefly_image()
        await interaction.response.send_message(
            file=discord.File(image_path, filename=image_path.name)
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Firefly(bot))
