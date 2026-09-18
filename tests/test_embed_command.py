"""驗證 /embed 會先確認互動，避免處理期間超過 Discord 三秒期限。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.talk.embed import EmbedBuilder


def test_embed_defers_before_sending_and_reports_invalid_color() -> None:
    channel = SimpleNamespace(send=AsyncMock())
    interaction = SimpleNamespace(
        channel=channel,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    asyncio.run(
        EmbedBuilder.cmd_embed.callback(
            EmbedBuilder(SimpleNamespace()),
            interaction,
            title="測試",
            description="內容",
            color="不是顏色",
        )
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    channel.send.assert_awaited_once()
    status = interaction.followup.send.await_args.args[0]
    assert "已發送" in status
    assert "無效的顏色" in status
