"""Cog hot-reload compatibility checks."""

from discord.ext import commands

import core.music.queue as queue_module
import utils.confirmation as confirmation_module
from cogs.system.load import (
    _core_import_error,
    _music_core_restart_reasons,
    _reload_shared_dependencies,
)
from core.system.extension_loader import _collect_modules


def test_streaming_helper_is_not_discovered_as_cog_extension():
    modules = _collect_modules("cogs", frozenset({"__pycache__", "venv", ".venv"}))

    assert "cogs.ai.streaming_response" not in modules


def test_current_music_core_is_reload_compatible():
    assert _music_core_restart_reasons() == []


def test_stale_music_core_requires_restart(monkeypatch):
    monkeypatch.delattr(queue_module, "QueueFullError")

    reasons = _music_core_restart_reasons()

    assert "core.music.queue.QueueFullError 尚未載入" in reasons


def test_core_import_error_unwraps_extension_failure():
    root = ImportError("cannot import name 'QueueFullError' from 'core.music.queue'")
    wrapped = commands.ExtensionFailed("cogs.music.music", root)

    assert _core_import_error(wrapped) == str(root)


def test_shared_dependency_reload_restores_new_confirmation_api(monkeypatch):
    monkeypatch.delattr(confirmation_module, "guarded_action")

    reloaded = _reload_shared_dependencies()

    assert reloaded == ["utils.confirmation"]
    assert hasattr(confirmation_module, "guarded_action")
