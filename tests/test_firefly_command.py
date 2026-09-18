from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.utility.firefly import Firefly, _FIREFLY_IMAGES, _choose_firefly_image


def test_firefly_images_exist() -> None:
    assert len(_FIREFLY_IMAGES) == 2
    assert all(image.is_file() for image in _FIREFLY_IMAGES)


def test_firefly_image_is_chosen_from_configured_images() -> None:
    with patch("cogs.utility.firefly.random.choice", return_value=_FIREFLY_IMAGES[1]):
        assert _choose_firefly_image() == _FIREFLY_IMAGES[1]


def test_firefly_command_metadata_and_response() -> None:
    command = Firefly.cmd_firefly_chan
    assert command.name == "流螢醬"
    assert command.description == "流螢…醬？"

    cog = Firefly(MagicMock())
    response = SimpleNamespace(send_message=AsyncMock())
    interaction = SimpleNamespace(response=response)
    selected_image = _FIREFLY_IMAGES[0]
    discord_file = object()

    with (
        patch(
            "cogs.utility.firefly._choose_firefly_image",
            return_value=selected_image,
        ),
        patch("cogs.utility.firefly.discord.File", return_value=discord_file) as file_cls,
    ):
        asyncio.run(command.callback(cog, interaction))

    file_cls.assert_called_once_with(selected_image, filename=selected_image.name)
    response.send_message.assert_awaited_once_with(file=discord_file)
