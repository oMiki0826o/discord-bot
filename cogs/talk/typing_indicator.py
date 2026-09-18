"""
cogs/talk/typing_indicator.py

職責：
- /typing：以下拉選項開啟或關閉目前頻道的「正在輸入...」指示器

Modification():

- 移植自 Bot-Firefly/cogs/talk/typing.py
- 類別命名改為 TypingIndicator（PEP 8）
- 加入 from __future__ import annotations
- 檔名改為 typing_indicator.py 避免與標準庫 typing 衝突

- /typing 使用 action 下拉選單整合開啟與關閉，並同時使用
  default_permissions 與執行期 has_permissions，限制於伺服器頻道。

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

    def cog_unload(self) -> None:
        """重載或卸載 Cog 時停止所有輸入任務，避免背景任務殘留。"""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    async def _typing_loop(self, channel: discord.TextChannel) -> None:
        """每 9 秒觸發一次 typing，Discord 顯示時長約 10 秒。"""
        try:
            while True:
                async with channel.typing():
                    await asyncio.sleep(9)
        except asyncio.CancelledError:
            pass

    @app_commands.command(name="typing", description="開啟或關閉 Bot 的輸入指示器")
    @app_commands.describe(action="選擇開啟或關閉")
    @app_commands.choices(action=[
        app_commands.Choice(name="開啟", value="start"),
        app_commands.Choice(name="關閉", value="stop"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(send_messages=True)
    async def cmd_typing(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        ch_id = interaction.channel_id
        if action.value == "start":
            if ch_id in self._tasks:
                await interaction.response.send_message("此頻道已在 typing。", ephemeral=True)
                return
            self._tasks[ch_id] = asyncio.create_task(
                self._typing_loop(interaction.channel)
            )
            logger.info("[typing] 開始 channel=%d", ch_id)
            await interaction.response.send_message("已開始 typing。", ephemeral=True)
            return

        task  = self._tasks.pop(ch_id, None)
        if not task:
            await interaction.response.send_message("目前沒有在 typing。", ephemeral=True)
            return
        task.cancel()
        logger.info("[typing] 停止 channel=%d", ch_id)
        await interaction.response.send_message("已停止 typing。", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TypingIndicator(bot))
