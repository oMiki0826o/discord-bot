"""
cogs/roles/role_management.py

職責：
- 提供身份組自助領取功能（Button Roles）
- /roles — 透過 action 選單建立、列出、修改或刪除身份組面板
- 所有會修改公開面板的操作都需要二次確認
- 使用 persistent Button View，Bot 重啟後仍可響應
- 所有面板資料存入 SQLite，重啟後自動重建 View

Modification():

- 全新建立，整合至 firefly-bot 架構
- 使用 Slash Commands
- 一個伺服器可建立多個面板，每個面板最多 25 個按鈕（Discord 限制）
- 按鈕點擊後若已有身份組則移除（切換邏輯），提供雙向功能

- 修正 _build_panel_embed：原本所有身份組擠在單一 field，
  累積到一定數量（或描述較長）即超過 Discord 1024 字元上限，
  觸發 400 error 50035。改為依實際字元數動態切分為多個 field，
  與 cogs/minecraft/mc_commands.py 的修正方式一致。
- 新增參數長度限制：label 對應 Discord 按鈕 label 本身就有 80
  字元硬上限，原本未限制，超長時會在送出訊息時才炸掉；description
  / title 也補上合理上限，降低觸發上述 1024 字元問題的機率。
- cmd_add / cmd_remove 的例外處理範圍過窄（只接 NotFound /
  Forbidden / AssertionError），未涵蓋的 HTTPException（例如表情符號
  格式不合法、內容超出長度限制）會直接成為未捕捉例外。改為統一
  捕捉 discord.HTTPException，並用 utils.discord_errors 轉換為
  可讀訊息。

"""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from database.ai.sqlite import get_connection
from utils.discord_errors import friendly_http_error
from utils.confirmation import guarded_action, missing_permissions, request_confirmation

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
        if role.is_default() or role.managed or role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "此身份組無法透過自助面板管理", ephemeral=True,
            )
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


# Discord embed 單一 field value 上限為 1024；留緩衝避免邊界誤差
_FIELD_VALUE_LIMIT: int = 1000


def _split_role_lines_to_fields(lines: list[str]) -> list[tuple[str, str]]:
    """
    將身份組清單字串依實際字元數動態切分為多個 (field_name, field_value)。

    修正：原版將所有身份組塞入單一 field，面板身份組數量增加
    （或描述較長）時會超過 Discord 1024 字元上限，觸發 400 error 50035。
    做法與 cogs/minecraft/mc_commands.py 的 _split_results_to_fields 一致。
    """
    fields: list[tuple[str, str]] = []
    current: list[str] = []

    for line in lines:
        candidate = "\n".join([*current, line])
        if len(candidate) > _FIELD_VALUE_LIMIT and current:
            label = "可選身份組" if not fields else "可選身份組（續）"
            fields.append((label, "\n".join(current)))
            current = [line]
        else:
            current.append(line)

    if current:
        label = "可選身份組" if not fields else "可選身份組（續）"
        fields.append((label, "\n".join(current)))

    return fields


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

        for field_name, field_value in _split_role_lines_to_fields(lines):
            embed.add_field(name=field_name, value=field_value, inline=False)
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

    @app_commands.command(name="roles", description="開啟身份組面板管理")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def cmd_roles(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="身份組面板管理",
            description="請從下方選單建立、修改或刪除身份組面板。",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        await interaction.response.send_message(
            embed=embed, view=RoleManagementView(self, interaction.user.id), ephemeral=True,
        )

    async def dispatch_action(
        self,
        interaction: discord.Interaction,
        value: str,
        message_id: str | None = None,
        role: discord.Role | None = None,
        title: app_commands.Range[str, 1, 100] = "身份組領取",
        description: app_commands.Range[str, 0, 300] | None = None,
        label: app_commands.Range[str, 1, 80] | None = None,
        emoji: str | None = None,
        style: str | None = None,
    ) -> None:
        user_perms = ("administrator",) if value == "delete" else ("manage_roles",)
        bot_perms = {
            "panel": ("send_messages", "embed_links", "manage_roles"),
            "add": ("manage_roles",),
            "remove": ("manage_roles",),
        }.get(value, ())
        if error := missing_permissions(interaction, user=user_perms, bot=bot_perms):
            await interaction.response.send_message(error, ephemeral=True)
            return

        if value == "list":
            await self.cmd_list(interaction)
            return
        if value == "panel":
            await request_confirmation(
                interaction,
                title="確認建立身份組面板",
                description=f"將在 {interaction.channel.mention} 建立公開面板「{title}」。",
                action=guarded_action(
                    lambda click: self.cmd_panel(
                        click, title, description or "點擊下方按鈕以領取或移除身份組",
                    ),
                    user=("manage_roles",), bot=bot_perms,
                ),
            )
            return
        if not message_id:
            await interaction.response.send_message("此操作必須填寫面板訊息 ID。", ephemeral=True)
            return
        if value in {"add", "remove"} and role is None:
            await interaction.response.send_message("新增或移除時必須選擇身份組。", ephemeral=True)
            return

        if value == "add":
            action_callback = lambda click: self.cmd_add(
                click, message_id, role, label, emoji, (description or "")[:100],
                style or "secondary",
            )
            summary = f"將 {role.mention} 加入面板 `{message_id}`。"
        elif value == "remove":
            action_callback = lambda click: self.cmd_remove(click, message_id, role)
            summary = f"將 {role.mention} 從面板 `{message_id}` 移除。"
        else:
            action_callback = lambda click: self.cmd_delete(click, message_id)
            summary = f"將刪除面板 `{message_id}` 及其訊息，此操作無法復原。"

        await request_confirmation(
            interaction,
            title={"add": "確認新增身份組", "remove": "確認移除身份組", "delete": "確認刪除面板"}[value],
            description=summary,
            action=guarded_action(action_callback, user=user_perms, bot=bot_perms),
        )

    # ── /roles panel ──────────────────────

    async def cmd_panel(
        self,
        interaction: discord.Interaction,
        title:       app_commands.Range[str, 1, 100] = "身份組領取",
        description: app_commands.Range[str, 1, 300] = "點擊下方按鈕以領取或移除身份組",
    ) -> None:
        embed = _build_panel_embed(title, description, [], interaction.guild)

        try:
            msg = await interaction.channel.send(embed=embed, view=RolePanelView([]))
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"建立面板失敗：{friendly_http_error(e)}", ephemeral=True,
            )
            return

        _upsert_panel(
            guild_id    = interaction.guild.id,
            channel_id  = interaction.channel.id,
            message_id  = msg.id,
            title       = title,
            description = description,
            roles       = [],
        )

        await interaction.response.send_message(
            f"面板已建立（訊息 ID：`{msg.id}`）\n再次使用 `/roles` 並選擇「新增身份組」即可加入按鈕。",
            ephemeral=True,
        )

    # ── /roles add ──────────────────────

    async def cmd_add(
        self,
        interaction: discord.Interaction,
        message_id:  str,
        role:        discord.Role,
        label:       app_commands.Range[str, 1, 80] | None = None,
        emoji:       str | None = None,
        description: app_commands.Range[str, 0, 100] = "",
        style:       str = "secondary",
    ) -> None:
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("訊息 ID 格式錯誤（請輸入純數字）", ephemeral=True)
            return

        panel = _get_panel_by_message(msg_id)
        if not panel or panel["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("找不到此面板（訊息 ID 錯誤，或非此 Bot 建立）", ephemeral=True)
            return

        member = interaction.user
        assert isinstance(member, discord.Member)
        if (
            role.is_default()
            or role.managed
            or role >= interaction.guild.me.top_role
            or (member.id != interaction.guild.owner_id and role >= member.top_role)
        ):
            await interaction.response.send_message(
                "無法將預設、整合管理、高於 Bot，或不低於您的身份組加入面板。",
                ephemeral=True,
            )
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
        except AssertionError:
            await interaction.response.send_message("面板所在頻道已不存在或類型錯誤", ephemeral=True)
            return
        except discord.HTTPException as e:
            # 涵蓋 NotFound（訊息已刪除）、Forbidden（權限不足）、
            # 以及表情符號格式錯誤、內容超出長度限制等其他 400 情況。
            await interaction.response.send_message(f"更新面板失敗：{friendly_http_error(e)}", ephemeral=True)
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
        if not panel or panel["guild_id"] != interaction.guild.id:
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
        except AssertionError:
            await interaction.response.send_message("面板所在頻道已不存在或類型錯誤", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"更新面板失敗：{friendly_http_error(e)}", ephemeral=True)
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
        if not panel or panel["guild_id"] != interaction.guild.id:
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

    async def cmd_list(self, interaction: discord.Interaction) -> None:
        panels = _get_panels(interaction.guild.id)

        embed = discord.Embed(
            title     = "身份組面板清單",
            color     = discord.Color.blurple(),
            timestamp = discord.utils.utcnow(),
        )

        if not panels:
            embed.description = "目前無任何身份組面板\n使用 `/roles` 並選擇「建立面板」建立第一個"
        else:
            lines = [
                f"**{p['title']}** — {len(p['roles'])} 個身份組\n"
                f"  訊息 ID：`{p['message_id']}` | <#{p['channel_id']}>"
                for p in panels
            ]
            embed.description = "\n\n".join(lines)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── extension 進入點 ──────────────────────

class RoleManagementSelect(discord.ui.Select):
    def __init__(self, cog: RoleManagement) -> None:
        self.cog = cog
        super().__init__(placeholder="選擇身份組面板操作", options=[
            discord.SelectOption(label="建立面板", value="panel"),
            discord.SelectOption(label="新增身份組", value="add"),
            discord.SelectOption(label="移除身份組", value="remove"),
            discord.SelectOption(label="刪除面板", value="delete"),
            discord.SelectOption(label="列出面板", value="list"),
        ])

    async def callback(self, interaction: discord.Interaction) -> None:
        action = self.values[0]
        if action == "list":
            await self.cog.dispatch_action(interaction, action)
        elif action in {"add", "remove"}:
            await interaction.response.send_message(
                "請選擇要操作的身份組：",
                view=RoleTargetView(self.cog, action, interaction.user.id), ephemeral=True,
            )
        else:
            await interaction.response.send_modal(RoleManagementModal(self.cog, action))


class RoleManagementView(discord.ui.View):
    def __init__(self, cog: RoleManagement, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(RoleManagementSelect(cog))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的身份組管理面板。", ephemeral=True)
        return False


class RoleTargetSelect(discord.ui.RoleSelect):
    def __init__(self, cog: RoleManagement, action: str) -> None:
        super().__init__(placeholder="選擇一個身份組", min_values=1, max_values=1)
        self.cog, self.action = cog, action

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RoleManagementModal(self.cog, self.action, self.values[0]))


class RoleTargetView(discord.ui.View):
    def __init__(self, cog: RoleManagement, action: str, user_id: int) -> None:
        super().__init__(timeout=180)
        self.user_id = user_id
        self.add_item(RoleTargetSelect(cog, action))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的身份組管理面板。", ephemeral=True)
        return False


class RoleManagementModal(discord.ui.Modal):
    def __init__(self, cog: RoleManagement, action: str, role: discord.Role | None = None) -> None:
        titles = {"panel": "建立身份組面板", "add": "新增身份組", "remove": "移除身份組", "delete": "刪除身份組面板"}
        super().__init__(title=titles[action])
        self.cog, self.action, self.role = cog, action, role
        self.inputs: dict[str, discord.ui.TextInput] = {}

        def add(key: str, label: str, **kwargs: object) -> None:
            item = discord.ui.TextInput(label=label, **kwargs)
            self.inputs[key] = item
            self.add_item(item)

        if action == "panel":
            add("title", "面板標題", default="身份組領取", max_length=100)
            add("description", "面板說明", default="點擊下方按鈕以領取或移除身份組", max_length=300, style=discord.TextStyle.paragraph)
        else:
            add("message_id", "面板訊息 ID", max_length=20)
            if action == "add":
                add("label", "按鈕文字（選填）", required=False, max_length=80)
                add("emoji", "表情符號（選填）", required=False, max_length=100)
                add("description", "身份組說明（選填）", required=False, max_length=100)
                add("style", "樣式：primary/secondary/success/danger", default="secondary", max_length=9)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        def value(key: str) -> str | None:
            return str(self.inputs[key].value).strip() if key in self.inputs else None

        style = value("style")
        if style and style not in {"primary", "secondary", "success", "danger"}:
            await interaction.response.send_message("按鈕樣式必須是 primary、secondary、success 或 danger。", ephemeral=True)
            return
        await self.cog.dispatch_action(
            interaction, self.action, message_id=value("message_id"), role=self.role,
            title=value("title") or "身份組領取", description=value("description"),
            label=value("label"), emoji=value("emoji"), style=style,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleManagement(bot))
