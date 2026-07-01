"""
core/music/player.py

職責：
- GuildPlayer 管理單一伺服器的完整播放生命週期
- connect() 支援移動至新頻道而不重新連線
- add_song() / add_playlist() 加入佇列並在需要時啟動播放
- _play_next() 串流 URL 在播放前即時提取，避免逾期
- 閒置計時器：佇列空時啟動，達 idle_timeout 後自動斷線
- set_volume() 即時更新正在播放的音量

Modification():

- 移植自 music_bot/core/player.py
- idle_timeout 與 default_volume 從 settings.json 讀取
- _play_next() 中通知 embed import 路徑調整為 core.music.embeds

- 修正 connect() 語音頻道連接逾時（TimeoutError）導致使用者
  看到空白錯誤訊息的問題：
  原本 channel.connect() 的逾時例外（asyncio.TimeoutError /
  Python 3.11 的 TimeoutError）不在 cmd_play 的 except 分支
  中被識別，str(TimeoutError()) 回傳空字串，讓使用者看到
  「 」這樣的空白錯誤 embed。
  改為在 connect() 內捕捉 TimeoutError 並轉換為 ConnectionError
  附上可讀說明，cmd_play / cmd_playlist 再捕捉 ConnectionError
  顯示具體錯誤訊息給使用者。

- 修正幽靈連接（ghost connection）問題：
  原本若 self._vc 存在但 is_connected() 為 False（Bot 被踢出
  頻道、網路中斷後殘留的失效 VoiceClient），直接嘗試 connect()
  可能導致 discord.ClientException: Already connected，改為
  先強制 disconnect() 清除殘留狀態再重新連線。

- add_playlist() 對應 Song.from_playlist() 的 tuple 回傳值，
  同時傳遞跳過數量給上層，讓 music.py 可以通知使用者。

- voice_connect_timeout 可由 settings.json 的
  music.voice_connect_timeout 調整（預設 30 秒）。

"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.music.queue import MusicQueue, LoopMode
from core.music.song  import Song
from core.system.settings import get

log = logging.getLogger("bot.music.player")


class GuildPlayer:
    """
    單一 Discord 伺服器的音樂播放器。
    由 PlayerManager 建立，每個 Guild 只存在一個實例。
    """

    def __init__(self, bot: discord.Client, guild: discord.Guild) -> None:
        self.bot   = bot
        self.guild = guild
        self.queue = MusicQueue()

        # ── 可調整狀態 ──────────────────────
        self.volume: float = get("music.default_volume_percent", 50) / 100.0
        self.text_channel: discord.TextChannel | None = None

        # ── 內部狀態 ──────────────────────
        self._vc:          discord.VoiceClient | None = None
        self._idle_task:   asyncio.Task        | None = None
        self._play_lock    = asyncio.Lock()
        # True = _play_next 自行發送「正在播放」通知
        self._auto_notify: bool = False

    # ── 連線管理 ──────────────────────

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """
        連線至語音頻道；已連線則移動至新頻道。

        修正：
        1. 幽靈連接清理：self._vc 存在但 is_connected() 為 False 時，
           先強制 disconnect() 再重新連線，避免 ClientException。
        2. TimeoutError → ConnectionError：channel.connect() 逾時時
           拋出 TimeoutError（Python 3.11+），其 str() 為空字串，
           直接顯示給使用者毫無意義。改為包裝成 ConnectionError
           並附上可讀說明。
        3. 連接逾時秒數由 settings.json music.voice_connect_timeout 控制。
        """
        timeout = int(get("music.voice_connect_timeout", 30))

        if self._vc and self._vc.is_connected():
            if self._vc.channel.id != channel.id:
                try:
                    await self._vc.move_to(channel)
                except TimeoutError as exc:
                    raise ConnectionError(
                        f"移動語音頻道逾時（{timeout}s），請確認 Bot 網路連線"
                    ) from exc
                except discord.HTTPException as exc:
                    raise ConnectionError(f"移動語音頻道失敗：{exc}") from exc
            return self._vc

        # ── 清除幽靈連接 ──────────────────────
        if self._vc is not None:
            log.warning("[%s] 清除失效的語音連接", self.guild.name)
            try:
                await self._vc.disconnect(force=True)
            except Exception:
                pass
            self._vc = None

        # ── 建立新連接 ──────────────────────
        try:
            self._vc = await channel.connect(self_deaf=True, timeout=timeout)
        except TimeoutError as exc:
            raise ConnectionError(
                f"連接語音頻道逾時（{timeout}s），請確認網路連線或稍後再試"
            ) from exc
        except discord.ClientException as exc:
            raise ConnectionError(f"語音頻道連接失敗：{exc}") from exc

        return self._vc

    async def disconnect(self) -> None:
        """中斷連線並完整清理狀態。"""
        self._cancel_idle_timer()
        self.queue.clear()
        self.queue.current = None
        if self._vc:
            self._vc.stop()
            await self._vc.disconnect(force=True)
            self._vc = None

    # ── 歌曲加入 ──────────────────────

    async def add_song(
        self,
        query:     str,
        requester: discord.Member,
        channel:   discord.TextChannel | None = None,
    ) -> Song:
        """
        解析單曲並加入佇列。
        若目前未播放，立即開始。
        """
        if channel:
            self.text_channel = channel

        was_active = self.is_active
        song       = await Song.from_query(query, requester)
        self.queue.add(song)

        if not was_active:
            async with self._play_lock:
                if not self.is_active:
                    self._auto_notify = False
                    await self._play_next()

        return song

    async def add_playlist(
        self,
        url:       str,
        requester: discord.Member,
        channel:   discord.TextChannel | None = None,
    ) -> tuple[list[Song], int]:
        """
        解析播放清單並批次加入佇列。
        回傳 (成功加入的歌曲清單, 跳過數量)。
        """
        if channel:
            self.text_channel = channel

        was_active       = self.is_active
        songs, skipped   = await Song.from_playlist(url, requester)

        for song in songs:
            self.queue.add(song)

        if not was_active and songs:
            async with self._play_lock:
                if not self.is_active:
                    self._auto_notify = False
                    await self._play_next()

        return songs, skipped

    # ── 播放核心 ──────────────────────

    async def _play_next(self) -> None:
        """
        從佇列取出下一首並開始播放。
        佇列空時啟動閒置計時器；串流提取失敗時跳過並遞迴嘗試下一首。
        """
        if not self._vc or not self._vc.is_connected():
            return

        song = self.queue.advance()

        if not song:
            self._start_idle_timer()
            return

        try:
            raw_source = await song.create_source()
        except Exception as exc:
            log.error("[%s] 無法載入音訊：%s，跳過此曲", self.guild.name, exc)
            await asyncio.sleep(0.3)
            await self._play_next()
            return

        source = discord.PCMVolumeTransformer(raw_source, volume=self.volume)

        def _after(error: Exception | None) -> None:
            if error:
                log.error("[%s] 播放錯誤：%s", self.guild.name, error)
            self._auto_notify = True
            asyncio.run_coroutine_threadsafe(self._play_next(), self.bot.loop)

        self._vc.play(source, after=_after)
        self._cancel_idle_timer()

        if self._auto_notify and self.text_channel:
            from core.music.embeds import now_playing_embed
            from core.music.views  import MusicControls
            try:
                await self.text_channel.send(
                    embed=now_playing_embed(song, self.queue),
                    view=MusicControls(self),
                )
            except discord.HTTPException as exc:
                log.warning("[%s] 無法發送自動通知：%s", self.guild.name, exc)

    # ── 播放控制 ──────────────────────

    def skip(self) -> None:
        if self._vc and (self._vc.is_playing() or self._vc.is_paused()):
            self._vc.stop()

    def pause(self) -> bool:
        if self._vc and self._vc.is_playing():
            self._vc.pause()
            return True
        return False

    def resume(self) -> bool:
        if self._vc and self._vc.is_paused():
            self._vc.resume()
            return True
        return False

    async def stop(self) -> None:
        """停止播放並清空佇列，保持語音連線。"""
        self.queue.clear()
        self.queue.current = None
        if self._vc:
            self._vc.stop()

    def set_volume(self, volume: float) -> None:
        """設定音量（0.0–2.0），若正在播放則即時套用。"""
        self.volume = max(0.0, min(2.0, volume))
        if (
            self._vc
            and self._vc.source
            and isinstance(self._vc.source, discord.PCMVolumeTransformer)
        ):
            self._vc.source.volume = self.volume

    def set_loop(self, mode: LoopMode) -> None:
        self.queue.loop_mode = mode

    # ── 狀態屬性 ──────────────────────

    @property
    def is_playing(self) -> bool:
        return bool(self._vc and self._vc.is_playing())

    @property
    def is_paused(self) -> bool:
        return bool(self._vc and self._vc.is_paused())

    @property
    def is_connected(self) -> bool:
        return bool(self._vc and self._vc.is_connected())

    @property
    def is_active(self) -> bool:
        return self.is_playing or self.is_paused

    @property
    def current_song(self) -> Song | None:
        return self.queue.current

    @property
    def voice_channel(self) -> discord.VoiceChannel | None:
        return self._vc.channel if self._vc else None  # type: ignore[return-value]

    # ── 閒置計時器 ──────────────────────

    def _start_idle_timer(self) -> None:
        self._cancel_idle_timer()
        self._idle_task = asyncio.create_task(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_disconnect(self) -> None:
        timeout = int(get("music.idle_timeout_seconds", 180))
        await asyncio.sleep(timeout)
        log.info("[%s] 閒置 %ds，自動斷線", self.guild.name, timeout)
        await self.disconnect()
