"""
tests/test_help_menu.py

Modification():

- 新增 Help Embed 類別選單測試。
- 驗證單一類別超過每頁指令上限時會拆頁。
- 驗證沒有直接 binding 的 Slash Group 仍可由子指令判斷 Cog 類別。

"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utility.general import _build_slash_help_pages
from utils.help_menu import HelpEntry, HelpMenuView, build_help_pages


def test_build_help_pages_splits_large_category():
    entries = [
        HelpEntry(name=f"/command-{index}", description="測試指令")
        for index in range(12)
    ]

    pages = build_help_pages(
        {"一般工具": entries},
        title="指令說明",
        intro="選擇類別",
        footer="Firefly Bot",
    )

    assert [page.label for page in pages] == ["一般工具 (1/2)", "一般工具 (2/2)"]
    assert [len(page.embed.fields) for page in pages] == [10, 2]

    view = HelpMenuView(pages, user_id=123)
    select = view.children[0]
    assert isinstance(select, discord.ui.Select)
    assert [option.label for option in select.options] == [
        "一般工具 (1/2)",
        "一般工具 (2/2)",
    ]


def test_slash_group_uses_child_binding_for_category():
    class GuildTestCog(commands.Cog):
        server_group = app_commands.Group(name="server_test", description="測試群組")

        @server_group.command(name="show", description="查看設定")
        async def show(self, interaction: discord.Interaction) -> None:
            pass

    GuildTestCog.__module__ = "cogs.guild.test_help"
    cog = GuildTestCog()
    pages = _build_slash_help_pages(cog.get_app_commands(), "Firefly Bot")

    assert len(pages) == 1
    assert pages[0].label == "伺服器設定"
    assert pages[0].embed.fields[0].name == "/server_test show"
