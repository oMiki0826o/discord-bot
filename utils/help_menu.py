"""
utils/help_menu.py

職責：
- 提供 /help 與 $help 共用的 Embed 類別分頁產生器
- 提供下拉選單 View，依類別切換 Help 頁面
- 限制只有原始呼叫者能操作選單，並在逾時後停用互動元件

Modification():

- 新增 HelpEntry、HelpPage 與 HelpMenuView。
- 單一類別超過 Embed 容量時自動拆成多頁，並在選單標示頁碼。

"""

from __future__ import annotations

from dataclasses import dataclass

import discord


_MAX_ENTRIES_PER_PAGE: int = 10
_MAX_PAGE_CONTENT_LENGTH: int = 4800
_MAX_SELECT_OPTIONS: int = 25


@dataclass(frozen=True, slots=True)
class HelpEntry:
    """Help 頁面中的單一指令。"""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class HelpPage:
    """下拉選單中的一頁 Help 內容。"""

    label: str
    description: str
    embed: discord.Embed


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _split_entries(entries: list[HelpEntry]) -> list[list[HelpEntry]]:
    """依 Embed field 數量與總字數限制切分同一類別。"""
    chunks: list[list[HelpEntry]] = []
    chunk: list[HelpEntry] = []
    content_length = 0

    for entry in entries:
        entry_length = len(entry.name) + min(len(entry.description), 1024)
        if chunk and (
            len(chunk) >= _MAX_ENTRIES_PER_PAGE
            or content_length + entry_length > _MAX_PAGE_CONTENT_LENGTH
        ):
            chunks.append(chunk)
            chunk = []
            content_length = 0

        chunk.append(entry)
        content_length += entry_length

    if chunk:
        chunks.append(chunk)

    return chunks or [[]]


def build_help_pages(
    groups: dict[str, list[HelpEntry]],
    *,
    title: str,
    intro: str,
    footer: str,
) -> list[HelpPage]:
    """把已分類的指令轉換成可供下拉選單切換的 Embed 頁面。"""
    pages: list[HelpPage] = []
    total_commands = sum(len(entries) for entries in groups.values())

    for category, entries in sorted(groups.items()):
        sorted_entries = sorted(entries, key=lambda entry: entry.name)
        chunks = _split_entries(sorted_entries)

        for page_index, chunk in enumerate(chunks, start=1):
            page_count = len(chunks)
            page_suffix = f" ({page_index}/{page_count})" if page_count > 1 else ""
            page_label = _truncate(f"{category}{page_suffix}", 100)
            embed = discord.Embed(
                title=f"{title} | {category}{page_suffix}",
                description=intro,
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )

            if chunk:
                for entry in chunk:
                    embed.add_field(
                        name=_truncate(entry.name, 256),
                        value=_truncate(entry.description or "無說明", 1024),
                        inline=False,
                    )
            else:
                embed.description = f"{intro}\n\n此類別目前沒有可用指令。"

            embed.set_footer(text=f"{footer} | 共 {total_commands} 個指令")
            pages.append(
                HelpPage(
                    label=page_label,
                    description=_truncate(f"{len(chunk)} 個指令", 100),
                    embed=embed,
                )
            )

    if not pages:
        embed = discord.Embed(
            title=title,
            description=f"{intro}\n\n目前沒有可用指令。",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=footer)
        pages.append(HelpPage(label="未分類", description="0 個指令", embed=embed))

    if len(pages) > _MAX_SELECT_OPTIONS:
        raise ValueError(f"Help 分頁超過 Discord 下拉選單上限：{len(pages)} 頁")

    return pages


class _HelpCategorySelect(discord.ui.Select):
    """Help 類別選單。"""

    def __init__(self, pages: list[HelpPage]) -> None:
        self.pages = pages
        options = [
            discord.SelectOption(
                label=page.label,
                value=str(index),
                description=page.description,
                default=index == 0,
            )
            for index, page in enumerate(pages)
        ]
        super().__init__(placeholder="選擇指令類別", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        page_index = int(self.values[0])
        for index, option in enumerate(self.options):
            option.default = index == page_index

        await interaction.response.edit_message(
            embed=self.pages[page_index].embed,
            view=self.view,
        )


class HelpMenuView(discord.ui.View):
    """由類別下拉選單切換 Embed 的共用 Help View。"""

    def __init__(self, pages: list[HelpPage], user_id: int, timeout: int = 120) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.message: discord.Message | None = None
        self.add_item(_HelpCategorySelect(pages))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True

        await interaction.response.send_message(
            "只有原本開啟 Help 的使用者可以操作此選單。",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

        if self.message is None:
            return

        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
