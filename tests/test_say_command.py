import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from cogs.talk.say import Say


def test_say_is_guild_only_and_has_runtime_checks() -> None:
    command = Say.cmd_say

    assert command.guild_only is True
    assert command.default_permissions == discord.Permissions(manage_messages=True)
    assert len(command.checks) == 2


def test_say_labels_message_with_user_id_and_limits_mass_mentions() -> None:
    bot = MagicMock()
    cog = Say(bot)
    user = SimpleNamespace(id=123456789)
    bot_member = object()
    permissions = SimpleNamespace(
        attach_files=True,
        embed_links=True,
        mention_everyone=False,
    )
    channel = MagicMock()
    channel.permissions_for.return_value = permissions
    channel.send = AsyncMock()
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(
        channel=channel,
        guild=SimpleNamespace(me=bot_member),
        user=user,
        response=response,
    )

    asyncio.run(Say.cmd_say.callback(cog, interaction, "hello @everyone"))

    channel.send.assert_awaited_once()
    args, kwargs = channel.send.await_args
    assert args[0] == "「123456789」説：hello @everyone"
    assert kwargs["allowed_mentions"].to_dict() == {
        "replied_user": True,
        "parse": ["users"],
    }
    response.send_message.assert_awaited_once_with("已發送。", ephemeral=True)
