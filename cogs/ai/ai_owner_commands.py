"""
cogs/ai/ai_owner_commands.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（重構）：
- 修正 import 路徑：from core.user_context → from core.ai.user_context
- 全指令保留 @commands.is_owner() 保護
- 統一錯誤處理格式

新增（Audit Log）：
- $tier / $ban / $unban / $記憶 / $刪記憶 操作成功後呼叫
  admin_service.log_admin_action()，記錄到 audit_log 表，
  方便事後追溯誰在何時做了什麼變更

新增（暫時限制手動解除）：
- 新增 $unrestrict @使用者，供 Owner 解除
  core.ai.abuse_guard 自動施加的暫時限制（誤判時可手動排除），
  與 $unban（解除永久封鎖）區分為不同指令
"""

from __future__ import annotations

import discord
from discord.ext import commands

from core.ai.abuse_guard import clear_restriction, is_restricted
from core.ai.admin_service import log_admin_action

# ── import 路徑修正（原路徑 core.user_context 為錯誤路徑） ──────────────────────
from core.ai.user_context import (
    ban_user,
    dump_social,
    get_tier_name,
    is_banned,
    remove_global_memory,
    set_global_memory,
    set_tier,
    unban_user,
)


class AiOwnerCommands(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 等級管理 ──────────────────────

    @commands.command(name="tier")
    @commands.is_owner()
    async def cmd_tier(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        tier:   int,
    ) -> None:
        """$tier @使用者 0~3"""
        if not 0 <= tier <= 3:
            await ctx.reply("等級必須介於 0~3")
            return
        set_tier(str(member.id), tier)
        log_admin_action(
            actor_id=str(ctx.author.id), command="tier",
            target_id=str(member.id), detail=f"tier={tier}",
        )
        await ctx.reply(
            f"{member.display_name} 的等級設為 "
            f"**{get_tier_name(tier)}**（等級 {tier}）"
        )

    # ── 封鎖管理 ──────────────────────

    @commands.command(name="ban")
    @commands.is_owner()
    async def cmd_ban(
        self,
        ctx:    commands.Context,
        member: discord.Member,
        *,
        reason: str = "",
    ) -> None:
        """$ban @使用者 [原因]"""
        uid = str(member.id)
        ban_user(uid, reason)
        log_admin_action(
            actor_id=str(ctx.author.id), command="ban",
            target_id=uid, detail=reason,
        )
        await ctx.reply(
            f"已封鎖 **{member.display_name}**（ID: `{uid}`）\n"
            f"原因：{reason or '未說明'}"
        )

    @commands.command(name="unban")
    @commands.is_owner()
    async def cmd_unban(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """$unban @使用者"""
        uid = str(member.id)
        if not is_banned(uid):
            await ctx.reply(f"{member.display_name} 並未在封鎖名單中")
            return
        unban_user(uid)
        log_admin_action(
            actor_id=str(ctx.author.id), command="unban", target_id=uid,
        )
        await ctx.reply(f"已解除 **{member.display_name}** 的封鎖")

    # ── 暫時限制解除（系統自動偵測誤判時手動解除） ──────────────────────

    @commands.command(name="unrestrict")
    @commands.is_owner()
    async def cmd_unrestrict(
        self,
        ctx:    commands.Context,
        member: discord.Member,
    ) -> None:
        """$unrestrict @使用者 — 解除系統自動施加的暫時限制（非永久封鎖）"""
        uid = str(member.id)
        if not is_restricted(uid):
            await ctx.reply(f"{member.display_name} 目前沒有暫時限制")
            return
        clear_restriction(uid)
        log_admin_action(
            actor_id=str(ctx.author.id), command="unrestrict", target_id=uid,
        )
        await ctx.reply(f"已解除 **{member.display_name}** 的暫時限制")

    # ── 全域記憶 ──────────────────────

    @commands.command(name="記憶", aliases=["memory"])
    @commands.is_owner()
    async def cmd_memory(
        self,
        ctx:        commands.Context,
        keyword:    str,
        importance: int,
        *,
        content:    str,
    ) -> None:
        """
        $記憶 關鍵字 重要度 '內容'

        範例：
        $記憶 規則 5 '不要討論政治'
        $記憶 Bot人格 5 'AI自稱流螢'
        """
        if not 1 <= importance <= 5:
            await ctx.reply("重要度必須介於 1~5")
            return

        content = content.strip()
        if len(content) < 2 or not content.startswith("'") or not content.endswith("'"):
            await ctx.reply("格式錯誤\n用法：$記憶 關鍵字 重要度 '內容'")
            return

        inner = content[1:-1].strip()
        if not keyword:
            await ctx.reply("關鍵字不可為空")
            return
        if not inner:
            await ctx.reply("內容不可為空")
            return

        set_global_memory(keyword, inner, importance)
        log_admin_action(
            actor_id=str(ctx.author.id), command="memory.set",
            target_id=keyword, detail=f"importance={importance} content={inner[:60]}",
        )
        await ctx.reply(
            f"已注入全域記憶\n"
            f"關鍵字：`{keyword}`\n"
            f"內容：'{inner}'\n"
            f"重要度：{importance}"
        )

    @commands.command(name="刪記憶", aliases=["delmemory", "memorydel"])
    @commands.is_owner()
    async def cmd_remove_memory(
        self,
        ctx:     commands.Context,
        *,
        keyword: str,
    ) -> None:
        """$刪記憶 關鍵字"""
        keyword = keyword.strip()
        if not keyword:
            await ctx.reply("請提供關鍵字")
            return
        if remove_global_memory(keyword):
            log_admin_action(
                actor_id=str(ctx.author.id), command="memory.del", target_id=keyword,
            )
            await ctx.reply(f"已刪除全域記憶：`{keyword}`")
        else:
            await ctx.reply(f"找不到關鍵字：`{keyword}`")

    # ── 社交狀態查看 ──────────────────────

    @commands.command(name="社交", aliases=["social"])
    @commands.is_owner()
    async def cmd_social(self, ctx: commands.Context) -> None:
        """$社交 — 顯示所有等級設定、封鎖名單、全域記憶、互動計數"""
        data  = dump_social()
        lines: list[str] = []

        tiers = data.get("tiers", {})
        if tiers:
            lines.append("**等級設定**")
            for uid, tier in tiers.items():
                lines.append(f"  `{uid}` → {get_tier_name(tier)}（{tier}）")

        bans = data.get("bans", {})
        if bans:
            lines.extend(["", "**封鎖名單**"])
            for uid, reason in bans.items():
                lines.append(f"  `{uid}` — {reason or '無說明'}")

        mems = data.get("global_memories", [])
        if mems:
            lines.extend(["", "**全域記憶**"])
            for m in mems:
                kw  = m.get("keyword", "")
                cnt = m.get("content", "")[:40]
                imp = m.get("importance", 5)
                lines.append(f"  [{kw}] '{cnt}'（重要度 {imp}）")

        interactions = data.get("interactions", {})
        if interactions:
            lines.extend(["", "**互動計數**"])
            for uid, count in interactions.items():
                lines.append(f"  `{uid}` — {count} 次")

        text = "\n".join(lines) if lines else "目前無任何設定"

        if len(text) <= 1_900:
            await ctx.reply(text)
            return
        for i in range(0, len(text), 1_900):
            await ctx.send(text[i : i + 1_900])

    # ── 統一錯誤處理 ──────────────────────

    @cmd_tier.error
    @cmd_ban.error
    @cmd_unban.error
    @cmd_unrestrict.error
    @cmd_memory.error
    @cmd_remove_memory.error
    @cmd_social.error
    async def owner_error(
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
    await bot.add_cog(AiOwnerCommands(bot))
