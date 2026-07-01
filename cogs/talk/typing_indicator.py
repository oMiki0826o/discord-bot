"""
cogs/talk/typing_indicator.py

職責：
- /typing：讓 Bot 在目前頻道持續顯示「正在輸入...」指示器
- /typing_stop：停止輸入指示器

Modification():

- 移植自 Bot-Firefly/cogs/talk/typing.py
- 類別命名改為 TypingIndicator（PEP 8）
- 加入 from __future__ import annotations
- 檔名改為 typing_indicator.py 避免與標準庫 typing 衝突

- 修正 /typing 與 /typing_stop 的權限檢查方式：由 @app_commands.checks.has_permissions
  改為 @app_commands.default_permissions（原因同 say.py）

"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.talk.typing")


class TypingIndicator(commands.Cog):
    """頻道持續輸入中指示器。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot   = bot
        self._tasks: dict[int, asyncio.Task] = {}   # channel_id → task

    async def _typing_loop(self, channel: discord.TextChannel) -> None:
        """每 9 秒觸發一次 typing，Discord 顯示時長約 10 秒。"""
        try:
            while True:
                async with channel.typing():
                    await asyncio.sleep(9)
        except asyncio.CancelledError:
            pass

    @app_commands.command(name="typing", description="讓 Bot 持續顯示正在輸入")
    @app_commands.default_permissions(manage_messages=True)
    async def cmd_typing_start(self, interaction: discord.Interaction) -> None:
        ch_id = interaction.channel_id
        if ch_id in self._tasks:
            await interaction.response.send_message("此頻道已在 typing。", ephemeral=True)
            return
        self._tasks[ch_id] = asyncio.create_task(
            self._typing_loop(interaction.channel)
        )
        logger.info("[typing] 開始 channel=%d", ch_id)
        await interaction.response.send_message("已開始 typing。", ephemeral=True)

    @app_commands.command(name="typing_stop", description="停止 Bot 的輸入指示器")
    @app_commands.default_permissions(manage_messages=True)
    async def cmd_typing_stop(self, interaction: discord.Interaction) -> None:
        ch_id = interaction.channel_id
        task  = self._tasks.pop(ch_id, None)
        if not task:
            await interaction.response.send_message("目前沒有在 typing。", ephemeral=True)
            return
        task.cancel()
        logger.info("[typing] 停止 channel=%d", ch_id)
        await interaction.response.send_message("已停止 typing。", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TypingIndicator(bot))
