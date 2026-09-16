"""驗證 Owner 清除伺服器專用 Slash Commands，避免與全域指令重複。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.system.owner import Owner


def test_slash_guild_clears_guild_commands_instead_of_copying_globals() -> None:
    guild = SimpleNamespace(name="Test Guild")
    tree = SimpleNamespace(
        clear_commands=MagicMock(),
        copy_global_to=MagicMock(),
        sync=AsyncMock(return_value=[]),
    )
    bot = SimpleNamespace(tree=tree)
    ctx = SimpleNamespace(
        guild=guild,
        author=SimpleNamespace(id=1),
        send=AsyncMock(),
    )

    asyncio.run(Owner.slash_guild.callback(Owner(bot), ctx))

    tree.clear_commands.assert_called_once_with(guild=guild)
    tree.sync.assert_awaited_once_with(guild=guild)
    tree.copy_global_to.assert_not_called()
    ctx.send.assert_awaited_once()
