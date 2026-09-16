"""音樂佇列、網址限制與背景任務生命週期的回歸測試。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.music.queue as queue_module
import core.music.views as views_module
from core.music.player import GuildPlayer
from core.music.queue import MusicQueue, QueueFullError
from core.music.song import Song
from core.music.url import is_youtube_url


def _song(title: str) -> Song:
    return Song(
        title=title,
        webpage_url=f"https://www.youtube.com/watch?v={title}",
        uploader="tester",
        duration=60,
        thumbnail=None,
        requester=SimpleNamespace(mention="@tester"),
    )


def test_queue_enforces_configured_max_size(monkeypatch):
    monkeypatch.setattr(queue_module, "get_int", lambda *_: 2)
    queue = MusicQueue()
    queue.add(_song("one"))
    queue.add(_song("two"))

    with pytest.raises(QueueFullError, match="最多 2 首"):
        queue.add(_song("three"))

    with pytest.raises(QueueFullError):
        queue.insert_at(1, _song("inserted"))


def test_queue_mutation_uses_stable_song_id_after_advance():
    queue = MusicQueue()
    first = _song("first")
    selected = _song("selected")
    last = _song("last")
    for song in (first, selected, last):
        queue.add(song)

    assert queue.advance() is first
    assert queue.remove_by_id(selected.queue_id) is selected
    assert queue.songs == [last]


def test_youtube_url_allowlist():
    assert is_youtube_url("https://youtu.be/abc")
    assert is_youtube_url("https://www.youtube.com/watch?v=abc")
    assert is_youtube_url("https://music.youtube.com/watch?v=abc")
    assert not is_youtube_url("https://youtube.com.evil.example/watch?v=abc")
    assert not is_youtube_url("http://127.0.0.1:8080/audio")
    assert not is_youtube_url("file:///tmp/audio.mp3")


def test_player_disconnect_does_not_cancel_calling_background_task():
    class FakeVoiceClient:
        def __init__(self) -> None:
            self.disconnected = False

        def stop(self) -> None:
            pass

        async def disconnect(self, *, force: bool) -> None:
            await asyncio.sleep(0)
            self.disconnected = force

    async def run(slot: str) -> None:
        player = GuildPlayer(SimpleNamespace(), SimpleNamespace())
        voice = FakeVoiceClient()
        player._vc = voice
        setattr(player, slot, asyncio.current_task())

        await player.disconnect()

        assert voice.disconnected
        assert player._vc is None
        assert getattr(player, slot) is None

    asyncio.run(run("_idle_task"))
    asyncio.run(run("_watchdog_task"))


def test_playlist_only_fills_remaining_queue_capacity(monkeypatch):
    monkeypatch.setattr(queue_module, "get_int", lambda *_: 3)
    player = GuildPlayer(SimpleNamespace(), SimpleNamespace())
    player.queue.add(_song("existing-one"))
    player.queue.add(_song("existing-two"))
    playlist = [_song("new-one"), _song("new-two"), _song("new-three")]

    async def fake_playlist(cls, url, requester):
        return playlist, 0

    monkeypatch.setattr(Song, "from_playlist", classmethod(fake_playlist))

    async def run():
        return await player.add_playlist("https://youtube.com/playlist?list=test", SimpleNamespace())

    added, skipped = asyncio.run(run())

    assert added == [playlist[0]]
    assert skipped == 2
    assert player.queue.size == 3


def test_player_control_requires_same_channel_or_admin(monkeypatch):
    class FakeMember:
        def __init__(self, channel, *, administrator: bool = False) -> None:
            self.voice = SimpleNamespace(channel=channel) if channel else None
            self.guild_permissions = SimpleNamespace(administrator=administrator)

    monkeypatch.setattr(views_module.discord, "Member", FakeMember)
    bot_channel = SimpleNamespace(id=10)
    player = SimpleNamespace(voice_channel=bot_channel)

    same_channel = SimpleNamespace(user=FakeMember(SimpleNamespace(id=10)))
    other_channel = SimpleNamespace(user=FakeMember(SimpleNamespace(id=20)))
    administrator = SimpleNamespace(user=FakeMember(None, administrator=True))

    assert views_module.can_control_player(same_channel, player)
    assert not views_module.can_control_player(other_channel, player)
    assert views_module.can_control_player(administrator, player)
