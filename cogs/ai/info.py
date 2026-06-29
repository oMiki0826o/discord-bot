"""
cogs/ai/info.py

修正（重構）：
- 修正 import 路徑：from core.admin_service → from core.ai.admin_service
- 移除舊版 from core.ai.budget 直接 import
"""

from __future__ import annotations

import time

import discord
from discord.ext import commands

# ── import 路徑修正（原路徑 core.admin_service 為錯誤路徑）──────────────────────
from core.ai.admin_service import get_global_summary, get_user_data, run_force_summarize


class Info(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── $info ──────────────────────

    @commands.group(name="info", invoke_without_command=True)
    @commands.is_owner()
    async def cmd_info(
        self,
        ctx:    commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        """$info / $info @使用者"""
        if member:
            await self._show_user(ctx, member)
        else:
            await self._show_global(ctx)

    # ── 全系統統計 ──────────────────────

    async def _show_global(self, ctx: commands.Context) -> None:
        stats     = get_global_summary(hours=24)
        total_req = stats["total_requests"] or 1

        model_lines = [
            f"  {m}: {d['requests']} 次 ({d['requests'] / total_req * 100:.0f}%) / {d['tokens']:,} tokens"
            for m, d in stats["by_model"].items()
        ] or ["  無資料"]

        embed = discord.Embed(
            title     = "系統統計（過去 24 小時）",
            color     = discord.Color.blue(),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(
            name  = "請求 / Token",
            value = (
                f"總請求：{stats['total_requests']:,} 次\n"
                f"總 Token：{stats['total_tokens']:,}\n"
                f"活躍使用者：{stats['active_users']} 人"
            ),
            inline=False,
        )
        embed.add_field(
            name  = "錯誤 / 快取",
            value = (
                f"錯誤次數：{stats['error_count']}\n"
                f"錯誤率：{stats['error_rate'] * 100:.1f}%\n"
                f"快取命中：{stats['cache_hits']} 次"
            ),
            inline=False,
        )
        embed.add_field(
            name  = "模型分布",
            value = "\n".join(model_lines),
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ── 使用者統計 ──────────────────────

    async def _show_user(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        stats = get_user_data(str(member.id))

        last = "無紀錄"
        if stats["last_active"]:
            diff = time.time() - stats["last_active"]
            if diff < 3_600:
                last = f"{int(diff / 60)} 分鐘前"
            elif diff < 86_400:
                last = f"{int(diff / 3_600)} 小時前"
            else:
                last = f"{int(diff / 86_400)} 天前"

        model_lines = [
            f"  {m}: {d['requests']} 次 / {d['tokens']:,} tokens"
            for m, d in stats["by_model"].items()
        ] or ["  無資料"]

        embed = discord.Embed(
            title     = f"使用者統計：{member.display_name}（30 天）",
            color     = discord.Color.green(),
            timestamp = discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name  = "用量",
            value = (
                f"總請求：{stats['total_requests']:,} 次\n"
                f"輸入 Token：{stats['total_input']:,}\n"
                f"輸出 Token：{stats['total_output']:,}\n"
                f"合計 Token：{stats['total_tokens']:,}"
            ),
            inline=False,
        )
        embed.add_field(name="活躍", value=f"最後活躍：{last}", inline=False)
        embed.add_field(
            name  = "模型明細",
            value = "\n".join(model_lines),
            inline=False,
        )
        await ctx.reply(embed=embed)

    # ── $info summary ──────────────────────

    @cmd_info.command(name="summary")
    @commands.is_owner()
    async def cmd_summary(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """$info summary @使用者"""
        msg     = await ctx.reply("生成摘要中...")
        summary = await run_force_summarize(str(member.id))

        if not summary:
            await msg.edit(content="訊息數量不足或生成失敗")
            return

        await msg.edit(
            content=(
                f"**{member.display_name} 的對話摘要**\n"
                f"```\n{summary[:1_800]}\n```"
            )
        )

    # ── 統一錯誤處理 ──────────────────────

    @cmd_info.error
    @cmd_summary.error
    async def info_error(
        self,
        ctx:   commands.Context,
        error: Exception,
    ) -> None:
        if isinstance(error, commands.NotOwner):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("參數不足")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply("參數格式錯誤")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
