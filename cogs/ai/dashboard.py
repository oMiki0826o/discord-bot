"""
cogs/ai/dashboard.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（重構）：
- 修正 import 路徑：from core.admin_service → from core.ai.admin_service
- 修正 from core.user_context → from core.ai.user_context
- Dashboard 不直接操作 SQLite，全部委派 admin_service
- 移除 _get_top_users()：原本直接 import database.ai.sqlite 查詢
  token_budget，違反本檔自身註明的分層原則；改呼叫
  admin_service.get_token_leaderboard()（邏輯已搬至 core.ai.budget）

新增：
- $dashboard 的「請求 / Token」欄位新增「估算佔比」，顯示過去
  24 小時內有多少比例的 Token 用量是估算值而非 API 實際回報
  （數值來自 core.ai.budget.get_global_stats() 的 estimated_ratio）

新增（Audit Log）：
- $dashboard clear / set / off / del 操作成功後呼叫
  admin_service.log_admin_action() 記錄到 audit_log 表
- 新增 $dashboard audit 子指令，顯示最近 20 筆管理指令操作紀錄

新增（內容審核規則）：
- 新增 $dashboard rules [reload] 子指令，顯示目前生效的
  database/ai/moderation_rules.txt 內容，或強制重新讀取
"""

from __future__ import annotations

import time

import discord
from discord.ext import commands

# ── import 路徑修正（原路徑 core.admin_service 為錯誤路徑） ──────────────────────
from core.ai.admin_service import (
    activate_template,
    deactivate_template,
    do_cache_cleanup,
    get_audit_log,
    get_dashboard_data,
    get_moderation_rules,
    get_templates,
    get_token_leaderboard,
    list_active_states,
    log_admin_action,
    reload_rules,
    remove_template,
)
from core.ai.user_context import clear_state

# ── 工具函式 ──────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "無紀錄"
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)} 秒前"
    if diff < 3_600:
        return f"{int(diff / 60)} 分鐘前"
    if diff < 86_400:
        return f"{int(diff / 3_600)} 小時前"
    return f"{int(diff / 86_400)} 天前"


def _error_color(rate: float) -> discord.Color:
    if rate < 0.05:
        return discord.Color.green()
    if rate < 0.15:
        return discord.Color.yellow()
    return discord.Color.red()


# ── Cog ──────────────────────

class Dashboard(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── $dashboard ──────────────────────

    @commands.group(name="dashboard", aliases=["db"], invoke_without_command=True)
    @commands.is_owner()
    async def cmd_dashboard(self, ctx: commands.Context) -> None:
        """$dashboard — 系統總覽 embed"""
        d = get_dashboard_data(self.bot)

        embed = discord.Embed(
            title     = "系統總覽 Dashboard",
            color     = _error_color(d["error_rate"]),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(
            name  = "請求 / Token（24h）",
            value = (
                f"請求次數：**{d['requests_24h']:,}**\n"
                f"Token 用量：**{_fmt_tokens(d['tokens_24h'])}**"
                f"（估算佔比 {d['estimated_ratio'] * 100:.0f}%）\n"
                f"活躍使用者：**{d['active_users']}** 人"
            ),
            inline=True,
        )
        embed.add_field(
            name  = "錯誤 / 快取（24h）",
            value = (
                f"錯誤率：**{d['error_rate'] * 100:.1f}%**\n"
                f"錯誤次數：**{d['error_count']}**\n"
                f"快取命中：**{d['cache_hits']}** 次"
            ),
            inline=True,
        )
        embed.add_field(
            name  = "記憶體系",
            value = (
                f"長期記憶：**{d['memory_count']:,}** 筆\n"
                f"向量記憶：**{d['vector_count']:,}** 筆\n"
                f"摘要使用者：**{d['summary_count']}** 人\n"
                f"快取有效：**{d['cache_valid']}** 筆"
            ),
            inline=True,
        )
        embed.add_field(
            name  = "使用者總覽",
            value = (
                f"累計使用者：**{d['user_count']}** 人\n"
                f"本 Bot 伺服器數：**{d['guild_count']}**\n"
                f"Bot 延遲：**{d['latency_ms']:.0f} ms**"
            ),
            inline=True,
        )

        if d["by_model"]:
            total_req = d["requests_24h"] or 1
            lines = [
                f"{model.split('-')[0].capitalize()}："
                f"{data['requests']} 次（{data['requests'] / total_req * 100:.0f}%）"
                f" / {_fmt_tokens(data['tokens'])}"
                for model, data in sorted(
                    d["by_model"].items(),
                    key=lambda x: x[1]["requests"],
                    reverse=True,
                )
            ]
            embed.add_field(name="模型分布", value="\n".join(lines), inline=False)

        embed.set_footer(text="$dashboard user / cache / state / prompt / audit / rules 查看子項目")
        await ctx.reply(embed=embed)

    # ── $dashboard user ──────────────────────

    @cmd_dashboard.command(name="user")
    @commands.is_owner()
    async def cmd_dash_user(self, ctx: commands.Context) -> None:
        """$dashboard user — Token 用量前 10 排行"""
        top = get_token_leaderboard(limit=10)

        embed = discord.Embed(
            title     = "Token 用量排行（30 天 Top 10）",
            color     = discord.Color.gold(),
            timestamp = discord.utils.utcnow(),
        )

        if not top:
            embed.description = "目前無資料"
            await ctx.reply(embed=embed)
            return

        lines  = [
            f"**{i}.** `{u['user_id']}`\n"
            f"    {u['requests']:,} 次請求 / {_fmt_tokens(u['tokens'])} tokens"
            for i, u in enumerate(top, start=1)
        ]
        embed.description = "\n".join(lines)
        await ctx.reply(embed=embed)

    # ── $dashboard cache ──────────────────────

    @cmd_dashboard.command(name="cache")
    @commands.is_owner()
    async def cmd_dash_cache(self, ctx: commands.Context) -> None:
        """$dashboard cache — 搜尋快取狀態，並執行一次清理"""
        before, removed, after = do_cache_cleanup()

        embed = discord.Embed(
            title     = "搜尋快取狀態",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )
        embed.add_field(
            name  = "清理前",
            value = f"總筆數：{before['total']}\n有效：{before['valid']}\n過期：{before['expired']}",
            inline=True,
        )
        embed.add_field(
            name  = "清理後",
            value = f"總筆數：{after['total']}\n有效：{after['valid']}\n已刪除：{removed} 筆",
            inline=True,
        )
        await ctx.reply(embed=embed)

    # ── $dashboard state ──────────────────────

    @cmd_dashboard.command(name="state")
    @commands.is_owner()
    async def cmd_dash_state(self, ctx: commands.Context) -> None:
        """$dashboard state — 列出所有非 normal 的對話狀態"""
        states = await list_active_states()

        embed = discord.Embed(
            title     = "對話狀態總覽",
            color     = discord.Color.orange(),
            timestamp = discord.utils.utcnow(),
        )

        if not states:
            embed.description = "目前所有使用者皆為一般對話狀態"
            await ctx.reply(embed=embed)
            return

        lines = [
            f"`{s['user_id']}` → **{s['label']}**"
            f"（{'永久' if not s['expires_at'] else _fmt_time(s['expires_at']) + ' 過期'}）"
            for s in states
        ]
        embed.description = "\n".join(lines)
        embed.set_footer(text="$dashboard clear @使用者 — 清除特定狀態")
        await ctx.reply(embed=embed)

    # ── $dashboard clear ──────────────────────

    @cmd_dashboard.command(name="clear")
    @commands.is_owner()
    async def cmd_dash_clear(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """$dashboard clear @使用者 — 清除指定使用者的對話狀態"""
        await clear_state(str(member.id))
        log_admin_action(
            actor_id=str(ctx.author.id), command="dashboard.clear",
            target_id=str(member.id),
        )
        await ctx.reply(f"已清除 **{member.display_name}** 的對話狀態")

    # ── $dashboard prompt ──────────────────────

    @cmd_dashboard.command(name="prompt")
    @commands.is_owner()
    async def cmd_dash_prompt(self, ctx: commands.Context) -> None:
        """$dashboard prompt — 列出所有 prompt 模板"""
        templates = get_templates()

        embed = discord.Embed(
            title     = "Prompt 模板清單",
            color     = discord.Color.teal(),
            timestamp = discord.utils.utcnow(),
        )

        if not templates:
            embed.description = (
                "目前無自訂模板，使用預設 SYSTEM_PROMPT\n\n"
                "新增方式：將模板檔放入 prompts/templates/<名稱>.txt"
            )
            await ctx.reply(embed=embed)
            return

        lines = [
            f"`{t['name']}`{'  **[啟用中]**' if t['is_active'] else ''}"
            f"{'  ' + t['description'] if t['description'] else ''}"
            for t in templates
        ]
        embed.description = "\n".join(lines)
        embed.set_footer(
            text=(
                "$dashboard set <名稱> — 啟用模板\n"
                "$dashboard off       — 停用所有模板\n"
                "$dashboard del <名稱> — 刪除模板"
            )
        )
        await ctx.reply(embed=embed)

    # ── $dashboard set ──────────────────────

    @cmd_dashboard.command(name="set")
    @commands.is_owner()
    async def cmd_dash_set(self, ctx: commands.Context, name: str) -> None:
        """$dashboard set <名稱> — 啟用指定 prompt 模板"""
        if activate_template(name):
            log_admin_action(
                actor_id=str(ctx.author.id), command="dashboard.set", target_id=name,
            )
            await ctx.reply(f"已啟用模板：`{name}`")
        else:
            await ctx.reply(f"找不到模板：`{name}`")

    # ── $dashboard off ──────────────────────

    @cmd_dashboard.command(name="off")
    @commands.is_owner()
    async def cmd_dash_off(self, ctx: commands.Context) -> None:
        """$dashboard off — 停用所有模板，恢復預設"""
        deactivate_template()
        log_admin_action(actor_id=str(ctx.author.id), command="dashboard.off")
        await ctx.reply("已停用所有模板，恢復使用預設 SYSTEM_PROMPT")

    # ── $dashboard del ──────────────────────

    @cmd_dashboard.command(name="del")
    @commands.is_owner()
    async def cmd_dash_del(self, ctx: commands.Context, name: str) -> None:
        """$dashboard del <名稱> — 刪除指定 prompt 模板"""
        if remove_template(name):
            log_admin_action(
                actor_id=str(ctx.author.id), command="dashboard.del", target_id=name,
            )
            await ctx.reply(f"已刪除模板：`{name}`")
        else:
            await ctx.reply(f"找不到模板：`{name}`")

    # ── $dashboard audit ──────────────────────

    @cmd_dashboard.command(name="audit")
    @commands.is_owner()
    async def cmd_dash_audit(self, ctx: commands.Context) -> None:
        """$dashboard audit — 顯示最近 20 筆管理指令操作紀錄"""
        logs = await get_audit_log(limit=20)

        embed = discord.Embed(
            title     = "管理指令操作紀錄（最近 20 筆）",
            color     = discord.Color.dark_grey(),
            timestamp = discord.utils.utcnow(),
        )

        if not logs:
            embed.description = "目前無任何操作紀錄"
            await ctx.reply(embed=embed)
            return

        lines = []
        for entry in logs:
            target = f" → `{entry['target_id']}`" if entry["target_id"] else ""
            detail = f"（{entry['detail']}）" if entry["detail"] else ""
            lines.append(
                f"`{_fmt_time(entry['created_at'])}` "
                f"**{entry['actor_id']}** 執行 `{entry['command']}`{target}{detail}"
            )
        embed.description = "\n".join(lines)
        await ctx.reply(embed=embed)

    # ── $dashboard rules ──────────────────────

    @cmd_dashboard.command(name="rules")
    @commands.is_owner()
    async def cmd_dash_rules(self, ctx: commands.Context, action: str = "show") -> None:
        """
        $dashboard rules        — 顯示目前生效的內容審核規則
        $dashboard rules reload — 強制重新讀取 moderation_rules.txt
        """
        if action == "reload":
            text = reload_rules()
            log_admin_action(actor_id=str(ctx.author.id), command="dashboard.rules.reload")
        else:
            text = get_moderation_rules()

        embed = discord.Embed(
            title     = "內容審核規則（moderation_rules.txt）",
            color     = discord.Color.dark_red(),
            timestamp = discord.utils.utcnow(),
        )

        if not text:
            embed.description = (
                "目前沒有任何啟用中的規則\n\n"
                "編輯 `database/ai/moderation_rules.txt` 即可新增，"
                "以 `#` 開頭的行會被視為註解忽略。\n"
                "編輯後下次請求即生效，或用 `$dashboard rules reload` 立即套用。"
            )
        else:
            embed.description = text[:4_000]

        await ctx.reply(embed=embed)

    # ── 統一錯誤處理 ──────────────────────

    @cmd_dashboard.error
    @cmd_dash_user.error
    @cmd_dash_cache.error
    @cmd_dash_state.error
    @cmd_dash_clear.error
    @cmd_dash_prompt.error
    @cmd_dash_set.error
    @cmd_dash_off.error
    @cmd_dash_del.error
    @cmd_dash_audit.error
    @cmd_dash_rules.error
    async def dashboard_error(
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
    await bot.add_cog(Dashboard(bot))
