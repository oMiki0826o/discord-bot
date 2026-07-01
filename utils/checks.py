"""
utils/checks.py

職責：
- 常用的權限 check 工廠函式
- 提供 Prefix Command 與 Slash Command 的統一守衛

Modification():

- 修正 owner_only() / slash_owner_only()：原本直接比對
  application_info().owner.id，當 Bot 應用程式由 Discord Team 擁有時，
  .owner 不一定對應到實際操作的 Team 成員，導致誤判「非擁有者」。
- 改為委派 bot.is_owner()（discord.py 內建，已正確處理 Team／
  個人帳號兩種情況，並自動快取 owner_id / owner_ids）。

"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


# ── Prefix Command ──────────────────────

async def _is_owner(ctx: commands.Context) -> bool:
    return await ctx.bot.is_owner(ctx.author)


def owner_only() -> commands.check:
    """Prefix Command 擁有者守衛。"""
    return commands.check(_is_owner)


# ── Slash Command ──────────────────────

async def _slash_owner_check(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)


def slash_owner_only() -> app_commands.check:
    """Slash Command 擁有者守衛。"""
    return app_commands.check(_slash_owner_check)
