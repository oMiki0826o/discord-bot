"""Slash Commands 合併與高風險操作確認的回歸測試。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord import app_commands

import cogs.moderation.mod as moderation_module
from cogs.guild.guild_settings import GuildSettings, ServerSettingsView
from cogs.music.music import Music, MusicPanelView
from cogs.utility.favorites import Favorites
from cogs.moderation.mod import Moderation
from cogs.roles.role_management import RoleManagement
from cogs.talk.typing_indicator import TypingIndicator
from cogs.ticket.ticket import Ticket
from cogs.voice.voice_channel import VoiceChannel


def _command_names(cog_type: type) -> list[str]:
    return [command.name for command in cog_type.__cog_app_commands__]


def test_related_features_expose_one_top_level_command_each() -> None:
    assert _command_names(GuildSettings) == ["server"]
    assert _command_names(Moderation) == ["mod"]
    assert _command_names(VoiceChannel) == ["vc"]
    assert _command_names(Ticket) == ["ticket"]
    assert _command_names(RoleManagement) == ["roles"]
    assert _command_names(TypingIndicator) == ["typing"]


def test_music_exposes_only_play_and_music_commands() -> None:
    assert _command_names(Music) == ["play", "music"]
    assert _command_names(Favorites) == []
    mode = Music.cmd_play.get_parameter("mode")
    assert mode.required is False
    assert mode.default == "song"


def test_music_panel_contains_all_music_features() -> None:
    view = MusicPanelView(SimpleNamespace(), user_id=123)
    select = view.children[0]
    assert {option.value for option in select.options} == {
        "now_playing", "queue", "history", "favorites",
        "favorite_add", "clear", "leave", "refresh",
    }


def test_server_panel_contains_all_previous_settings() -> None:
    view = ServerSettingsView(SimpleNamespace(), user_id=123)
    select = view.children[0]
    values = {option.value for option in select.options}

    assert values == {
        "welcome", "leave", "log", "autorole",
        "ticket_category", "ticket_support", "info", "reset",
    }


def test_typing_command_has_start_and_stop_choices() -> None:
    command = TypingIndicator.cmd_typing
    parameter = command.get_parameter("action")

    assert [choice.value for choice in parameter.choices] == ["start", "stop"]


def test_destructive_mod_action_requests_confirmation_before_execution(monkeypatch) -> None:
    confirmation = AsyncMock()
    monkeypatch.setattr(moderation_module, "request_confirmation", confirmation)
    permissions = SimpleNamespace(
        administrator=False,
        ban_members=True,
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1, guild_permissions=permissions),
        permissions=permissions,
        app_permissions=permissions,
        guild=SimpleNamespace(me=SimpleNamespace(guild_permissions=permissions)),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(mention="<@2>")

    asyncio.run(
        Moderation.cmd_mod.callback(
            Moderation(SimpleNamespace()),
            interaction,
            app_commands.Choice(name="封禁成員", value="ban"),
            member,
            None,
            "測試原因",
            None,
            0,
            10,
        )
    )

    confirmation.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
