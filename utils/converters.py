"""
utils/converters.py

職責：
- 自訂 discord.ext.commands Converter

Modification():

- 移植自 Bot-Firefly/utils/converters.py
- 新增 TimeConverter：解析 "30s" / "5m" / "1h" 等時間字串為秒數

"""

from __future__ import annotations

import re

import discord
from discord.ext import commands


class VoiceChannelConverter(commands.Converter):
    """依名稱（不分大小寫）尋找語音頻道。"""

    async def convert(self, ctx: commands.Context, argument: str) -> discord.VoiceChannel:
        for channel in ctx.guild.voice_channels:
            if channel.name.lower() == argument.lower():
                return channel
        raise commands.BadArgument(f"找不到語音頻道：{argument}")


class MemberConverter(commands.Converter):
    """依名稱（不分大小寫）尋找成員。"""

    async def convert(self, ctx: commands.Context, argument: str) -> discord.Member:
        member = discord.utils.find(
            lambda m: m.name.lower() == argument.lower() or m.display_name.lower() == argument.lower(),
            ctx.guild.members,
        )
        if member:
            return member
        raise commands.BadArgument(f"找不到成員：{argument}")


_TIME_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$", re.IGNORECASE)


class TimeConverter(commands.Converter):
    """
    將時間字串轉換為秒數。
    接受格式：30 / 30s / 5m / 1h / 1h30m / 1h30m15s
    """

    async def convert(self, ctx: commands.Context, argument: str) -> int:
        m = _TIME_RE.match(argument.strip())
        if not m or not any(m.groups()):
            raise commands.BadArgument(f"無效的時間格式：{argument}（範例：30s / 5m / 1h30m）")
        h = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        s    = int(m.group(3) or 0)
        total = h * 3600 + mins * 60 + s
        if total <= 0:
            raise commands.BadArgument("時間必須大於 0")
        return total
