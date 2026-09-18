"""Slash Command 高風險操作的共用二次確認元件。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

import discord

ConfirmAction = Callable[[discord.Interaction], Awaitable[None]]
logger = logging.getLogger("bot.security.confirmation")


class ConfirmationView(discord.ui.View):
    """只允許原操作者確認或取消，逾時後按鈕自動失效。"""

    def __init__(self, user_id: int, action: ConfirmAction, title: str, *, timeout: float = 30.0) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.action = action
        self.title = title
        self.completed = False
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        logger.warning(
            "[confirmation.denied] action=%s owner=%s attempted_by=%s guild=%s",
            self.title, self.user_id, interaction.user.id, interaction.guild_id,
        )
        await interaction.response.send_message("只有原指令執行者可以操作此確認。", ephemeral=True)
        return False

    async def _remove_buttons(self, interaction: discord.Interaction) -> None:
        self.stop()
        if interaction.message is None:
            return
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="確認執行", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if self.completed:
            await interaction.response.send_message("此操作已處理。", ephemeral=True)
            return
        self.completed = True
        logger.info(
            "[confirmation.confirmed] action=%s user=%s guild=%s channel=%s",
            self.title, interaction.user.id, interaction.guild_id, interaction.channel_id,
        )
        try:
            await self.action(interaction)
        except Exception as exc:
            logger.exception("[confirmation.failed] action=%s: %s", self.title, exc)
            message = f"操作失敗：{type(exc).__name__}"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        await self._remove_buttons(interaction)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        self.completed = True
        logger.info(
            "[confirmation.cancelled] action=%s user=%s guild=%s",
            self.title, interaction.user.id, interaction.guild_id,
        )
        await interaction.response.edit_message(content="操作已取消。", embed=None, view=None)
        self.stop()

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(content="確認已逾時，操作未執行。", embed=None, view=None)
            except discord.HTTPException:
                pass
        logger.info("[confirmation.timeout] action=%s user=%s", self.title, self.user_id)


async def request_confirmation(
    interaction: discord.Interaction,
    *,
    title: str,
    description: str,
    action: ConfirmAction,
) -> None:
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.orange(),
    )
    embed.set_footer(text="確認按鈕將在 30 秒後失效")
    view = ConfirmationView(interaction.user.id, action, title)
    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True,
    )
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        pass
    logger.info(
        "[confirmation.requested] action=%s user=%s guild=%s channel=%s",
        title, interaction.user.id, interaction.guild_id, interaction.channel_id,
    )


def missing_permissions(
    interaction: discord.Interaction,
    *,
    user: tuple[str, ...] = (),
    bot: tuple[str, ...] = (),
) -> str | None:
    """回傳缺少的使用者或 Bot 權限說明；None 表示通過。"""
    user_permissions = getattr(interaction, "permissions", None)
    if user_permissions is None:
        user_permissions = getattr(interaction.user, "guild_permissions", None)
    bot_permissions = getattr(interaction, "app_permissions", None)
    if bot_permissions is None:
        bot_member = getattr(interaction.guild, "me", None)
        bot_permissions = getattr(bot_member, "guild_permissions", None)

    user_is_admin = getattr(user_permissions, "administrator", False)
    bot_is_admin = getattr(bot_permissions, "administrator", False)
    missing_user = [name for name in user if not user_is_admin and not getattr(user_permissions, name, False)]
    if missing_user:
        return "你缺少必要權限：" + ", ".join(missing_user)

    missing_bot = [name for name in bot if not bot_is_admin and not getattr(bot_permissions, name, False)]
    if missing_bot:
        return "Bot 缺少必要權限：" + ", ".join(missing_bot)
    return None


def guarded_action(
    action: ConfirmAction,
    *,
    user: tuple[str, ...] = (),
    bot: tuple[str, ...] = (),
) -> ConfirmAction:
    """在按下確認的當下再次驗證權限，避免確認等待期間權限被撤銷。"""
    async def wrapped(interaction: discord.Interaction) -> None:
        if error := missing_permissions(interaction, user=user, bot=bot):
            await interaction.response.send_message(error, ephemeral=True)
            logger.warning(
                "[confirmation.permission_changed] user=%s guild=%s error=%s",
                interaction.user.id, interaction.guild_id, error,
            )
            return
        await action(interaction)

    return wrapped


__all__ = [
    "ConfirmationView", "guarded_action", "missing_permissions", "request_confirmation",
]
