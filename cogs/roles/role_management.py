"""
cogs/roles/role_management.py

職責：
- 提供身份組自助領取功能（Button Roles）
- /roles panel — 管理員在指定頻道發送帶按鈕的身份組面板
- /roles add / remove — 動態新增/移除面板上的身份組按鈕
- 使用 persistent Button View，Bot 重啟後仍可響應
- 所有面板資料存入 SQLite，重啟後自動重建 View

Modification():

- 全新建立，整合至 firefly-bot 架構
- 使用 Slash Commands
- 一個伺服器可建立多個面板，每個面板最多 25 個按鈕（Discord 限制）
- 按鈕點擊後若已有身份組則移除（切換邏輯），提供雙向功能

"""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database.ai.sqlite import get_connection

logger = logging.getLogger("bot.roles")


# ── DB 初始化 ──────────────────────

def _init_db() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS role_panels (
            panel_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    INTEGER NOT NULL,
            channel_id  INTEGER NOT NULL,
            message_id  INTEGER NOT NULL UNIQUE,
            title       TEXT    NOT NULL DEFAULT '身份組領取',
            description TEXT    NOT NULL DEFAULT '點擊按鈕以領取或移除身份組',
            roles       TEXT    NOT NULL DEFAULT '[]',
            updated_at  REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_rp_guild
            ON role_panels(guild_id);
    """)
    conn.commit()
    conn.close()


_init_db()


# ── DB 存取 ──────────────────────

def _get_panels(guild_id: int) -> list[dict]:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT * FROM role_panels WHERE guild_id = ?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["roles"] = json.loads(d["roles"])
        except Exception:
            d["roles"] = []
        result.append(d)
    return result


def _get_panel_by_message(message_id: int) -> dict | None:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT * FROM role_panels WHERE message_id = ?", (message_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["roles"] = json.loads(d["roles"])
    except Exception:
        d["roles"] = []
    return d


def _upsert_panel(
    guild_id:   int,
    channel_id: int,
    message_id: int,
    title:      str,
    description: str,
    roles:      list[dict],
) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO role_panels (guild_id, channel_id, message_id, title, description, roles)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            title       = excluded.title,
            description = excluded.description,
            roles       = excluded.roles,
            updated_at  = unixepoch('now')
        """,
        (guild_id, channel_id, message_id, title, description, json.dumps(roles, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def _delete_panel(message_id: int) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM role_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


# ── 動態 View ──────────────────────

class RolePanelView(discord.ui.View):
    """
    身份組面板 View。
    每個按鈕對應一個身份組，點擊後切換（給予/移除）。
    使用 custom_id=f"role:{role_id}" 確保重啟後可持久化。
    """

    def __init__(self, roles: list[dict]) -> None:
        super().__init__(timeout=None)
        for entry in roles[:25]:   # Discord 限制每個 View 最多 25 個元件
            self.add_item(_RoleButton(entry))


class _RoleButton(discord.ui.Button):
    def __init__(self, entry: dict) -> None:
        super().__init__(
            label     = entry.get("label", "身份組"),
            style     = _parse_style(entry.get("style", "secondary")),
            emoji     = entry.get("emoji"),
            custom_id = f"role:{entry['role_id']}",
        )
        self.role_id: int = int(entry["role_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        role = interaction.guild.get_role(self.role_id)

        if role is None:
            await interaction.response.send_message("此身份組已不存在", ephemeral=True)
            return

        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="身份組面板自助移除")
                await interaction.response.send_message(
                    f"已移除身份組：**{role.name}**", ephemeral=True,
                )
            else:
                await interaction.user.add_roles(role, reason="身份組面板自助領取")
                await interaction.response.send_message(
                    f"已獲得身份組：**{role.name}**", ephemeral=True,
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Bot 缺少管理身份組的權限，或此身份組階層高於 Bot", ephemeral=True,
            )


def _parse_style(s: str) -> discord.ButtonStyle:
    return {
        "primary":   discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success":   discord.ButtonStyle.success,
        "danger":    discord.ButtonStyle.danger,
    }.get(s, discord.ButtonStyle.secondary)


def _build_panel_embed(title: str, description: str, roles: list[dict], guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title       = title,
        description = description,
        color       = discord.Color.blurple(),
    )
    if roles:
        lines = []
        for entry in roles:
            role = guild.get_role(int(entry["role_id"]))
            name = role.mention if role else f"（已刪除 {entry['role_id']}）"
            lines.append(f"• {name} — {entry.get('description', '')}")
        embed.add_field(name="可選身份組", value="\n".join(lines), inline=False)
    return embed


# ── Cog ──────────────────────

class RoleManagement(commands.Cog):
    """身份組管理指令群組。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        Bot 啟動時，從 DB 重建所有面板的持久化 View，
        確保重啟後按鈕仍可響應。
        """
        conn = get_connection()
        c    = conn.cursor()
        c.execute("SELECT message_id, roles FROM role_panels")
        rows = c.fetchall()
        conn.close()

        count = 0
        for row in rows:
            try:
                roles = json.loads(row["roles"])
                self.bot.add_view(RolePanelView(roles), message_id=row["message_id"])
                count += 1
            except Exception as e:
                logger.warning("[roles] 重建 View 失敗 message_id=%s: %s", row["message_id"], e)

        logger.info("[roles] 已重建 %d 個身份組面板 View", count)

    roles_group = app_commands.Group(name="roles", description="身份組面板管理")

    # ── /roles panel ──────────────────────

    @roles_group.command(name="panel", description="建立新的身份組面板")
    @app_commands.describe(
        title       = "面板標題",
        description = "面板說明",
    )
    @app_commands.default_permissions(manage_roles=True)
    async def cmd_panel(
        self,
        interaction: discord.Interaction,
        title:       str = "身份組領取",
        description: str = "點擊下方按鈕以領取或移除身份組",
    ) -> None:
        embed = _build_panel_embed(title, description, [], interaction.guild)
        msg   = await interaction.channel.send(embed=embed, view=RolePanelView([]))

        _upsert_panel(
            guild_id    = interaction.guild.id,
            channel_id  = interaction.channel.id,
            message_id  = msg.id,
            title       = title,
            description = description,
            roles       = [],
        )

        await interaction.response.send_message(
            f"面板已建立（訊息 ID：`{msg.id}`）\n使用 `/roles add {msg.id} @身份組` 新增按鈕",
            ephemeral=True,
        )

    # ── /roles add ──────────────────────

    @roles_group.command(name="add", description="新增身份組到指定面板")
    @app_commands.describe(
        message_id  = "面板訊息 ID",
        role        = "要新增的身份組",
        label       = "按鈕文字（預設使用身份組名稱）",
        emoji       = "按鈕表情符號（選填）",
        description = "身份組說明（顯示在 embed 列表）",
        style       = "按鈕樣式",
    )
    @app_commands.choices(style=[
        app_commands.Choice(name="藍色（primary）",   value="primary"),
        app_commands.Choice(name="灰色（secondary）", value="secondary"),
        app_commands.Choice(name="綠色（success）",   value="success"),
        app_commands.Choice(name="紅色（danger）",    value="danger"),
    ])
    @app_commands.default_permissions(manage_roles=True)
    async def cmd_add(
        self,
        interaction: discord.Interaction,
        message_id:  str,
        role:        discord.Role,
        label:       str | None = None,
        emoji:       str | None = None,
        description: str        = "",
        style:       str        = "secondary",
    ) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("訊息 ID 格式錯誤（請輸入純數字）", ephemeral=True)
            return

        panel = _get_panel_by_message(msg_id)
        if not panel:
            await interaction.response.send_message("找不到此面板（訊息 ID 錯誤，或非此 Bot 建立）", ephemeral=True)
            return

        roles = panel["roles"]
        if any(str(r["role_id"]) == str(role.id) for r in roles):
            await interaction.response.send_message("此身份組已在面板中", ephemeral=True)
            return

        if len(roles) >= 25:
            await interaction.response.send_message("每個面板最多 25 個身份組", ephemeral=True)
            return

        roles.append({
            "role_id":     role.id,
            "label":       label or role.name,
            "emoji":       emoji,
            "description": description,
            "style":       style,
        })

        # ── 更新面板訊息 ──────────────────────
        try:
            channel = interaction.guild.get_channel(panel["channel_id"])
            assert isinstance(channel, discord.TextChannel)
            msg = await channel.fetch_message(msg_id)
            embed = _build_panel_embed(panel["title"], panel["description"], roles, interaction.guild)
            await msg.edit(embed=embed, view=RolePanelView(roles))
        except (discord.NotFound, discord.Forbidden, AssertionError) as e:
            await interaction.response.send_message(f"更新面板失敗：{e}", ephemeral=True)
            return

        _upsert_panel(
            guild_id    = interaction.guild.id,
            channel_id  = panel["channel_id"],
            message_id  = msg_id,
            title       = panel["title"],
            description = panel["description"],
            roles       = roles,
        )

        # 重建持久化 View
        self.bot.add_view(RolePanelView(roles), message_id=msg_id)

        await interaction.response.send_message(
            f"已新增身份組 {role.mention} 至面板",
            ephemeral=True,
        )

    # ── /roles remove ──────────────────────

    @roles_group.command(name="remove", description="從面板移除指定身份組按鈕")
    @app_commands.describe(message_id="面板訊息 ID", role="要移除的身份組")
    @app_commands.default_permissions(manage_roles=True)
    async def cmd_remove(
        self,
        interaction: discord.Interaction,
        message_id:  str,
        role:        discord.Role,
    ) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("訊息 ID 格式錯誤", ephemeral=True)
            return

        panel = _get_panel_by_message(msg_id)
        if not panel:
            await interaction.response.send_message("找不到此面板", ephemeral=True)
            return

        original = panel["roles"]
        updated  = [r for r in original if str(r["role_id"]) != str(role.id)]

        if len(updated) == len(original):
            await interaction.response.send_message("此身份組不在面板中", ephemeral=True)
            return

        try:
            channel = interaction.guild.get_channel(panel["channel_id"])
            assert isinstance(channel, discord.TextChannel)
            msg   = await channel.fetch_message(msg_id)
            embed = _build_panel_embed(panel["title"], panel["description"], updated, interaction.guild)
            await msg.edit(embed=embed, view=RolePanelView(updated))
        except (discord.NotFound, discord.Forbidden, AssertionError) as e:
            await interaction.response.send_message(f"更新面板失敗：{e}", ephemeral=True)
            return

        _upsert_panel(
            guild_id    = interaction.guild.id,
            channel_id  = panel["channel_id"],
            message_id  = msg_id,
            title       = panel["title"],
            description = panel["description"],
            roles       = updated,
        )
        self.bot.add_view(RolePanelView(updated), message_id=msg_id)

        await interaction.response.send_message(
            f"已從面板移除身份組 {role.mention}",
            ephemeral=True,
        )

    # ── /roles delete ──────────────────────

    @roles_group.command(name="delete", description="刪除整個身份組面板（會刪除面板訊息）")
    @app_commands.describe(message_id="面板訊息 ID")
    @app_commands.default_permissions(administrator=True)
    async def cmd_delete(
        self,
        interaction: discord.Interaction,
        message_id:  str,
    ) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("訊息 ID 格式錯誤", ephemeral=True)
            return

        panel = _get_panel_by_message(msg_id)
        if not panel:
            await interaction.response.send_message("找不到此面板", ephemeral=True)
            return

        try:
            channel = interaction.guild.get_channel(panel["channel_id"])
            assert isinstance(channel, discord.TextChannel)
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except Exception:
            pass   # 訊息已刪除也無妨

        _delete_panel(msg_id)
        await interaction.response.send_message("面板已刪除", ephemeral=True)

    # ── /roles list ──────────────────────

    @roles_group.command(name="list", description="列出伺服器所有身份組面板")
    @app_commands.default_permissions(manage_roles=True)
    async def cmd_list(self, interaction: discord.Interaction) -> None:
        panels = _get_panels(interaction.guild.id)

        embed = discord.Embed(
            title     = "身份組面板清單",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )

        if not panels:
            embed.description = "目前無任何身份組面板\n使用 `/roles panel` 建立第一個"
        else:
            lines = [
                f"**{p['title']}** — {len(p['roles'])} 個身份組\n"
                f"  訊息 ID：`{p['message_id']}` | <#{p['channel_id']}>"
                for p in panels
            ]
            embed.description = "\n\n".join(lines)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleManagement(bot))
