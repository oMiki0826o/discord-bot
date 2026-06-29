"""
utils/checks.py

職責：
- 常用的權限 check 工廠函式
- 提供 Prefix Command 與 Slash Command 的統一守衛

Modification():

- 移植自 Bot-Firefly/utils/checks.py
- 新增 slash_is_owner()：app_commands 版的擁有者檢查

"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


# ── Prefix Command ──────────────────────

async def _is_owner(ctx: commands.Context) -> bool:
    app = await ctx.bot.application_info()
    return ctx.author.id == app.owner.id


def owner_only() -> commands.check:
    """Prefix Command 擁有者守衛。"""
    return commands.check(_is_owner)


# ── Slash Command ──────────────────────

async def _slash_owner_check(interaction: discord.Interaction) -> bool:
    app = await interaction.client.application_info()
    return interaction.user.id == app.owner.id


def slash_owner_only() -> app_commands.check:
    """Slash Command 擁有者守衛。"""
    return app_commands.check(_slash_owner_check)
