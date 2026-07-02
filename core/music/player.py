"""
core/music/player.py

Modification():

- 移植自 music_bot/core/player.py
- idle_timeout 與 default_volume 從 settings.json 讀取
- _play_next() 中通知 embed import 路徑調整為 core.music.embeds
- connect() 捕捉 TimeoutError 並轉換為 ConnectionError 附上可讀說明；
  原本 str(TimeoutError()) 為空字串，使用者只會看到空白錯誤訊息
- connect() 新增幽靈連接清理：self._vc 存在但 is_connected() 為 False
  時，先強制 disconnect() 再重新連線，避免 ClientException
- add_playlist() 對應 Song.from_playlist() 的 tuple 回傳值，
  同時傳遞跳過數量給上層
- voice_connect_timeout 可由 settings.json 的
  music.voice_connect_timeout 調整
- 新增語音連線健康監控（watchdog），修正語音 WebSocket 反覆斷線
  重連失敗時，殘留任務被垃圾回收而觸發
  「Task was destroyed but it is pending!」的問題；詳見下方說明

職責：

- GuildPlayer 管理單一伺服器的完整播放生命週期
- connect() 支援移動至新頻道而不重新連線
- add_song() / add_playlist() 加入佇列並在需要時啟動播放
- _play_next() 串流網址在播放前即時提取，避免逾期
- 閒置計時器：佇列空時啟動，達 idle_timeout 後自動斷線
- 語音健康監控：語音 WebSocket 持續無法恢復時自動清理連線
- set_volume() 即時更新正在播放的音量

語音健康監控的背景說明：

- discord.py 的語音資料傳輸使用獨立於 Gateway 的 WebSocket 連線，
  這條連線因網路問題斷開時（log 中常見的 close code 1006），
  discord.py 會在同一頻道內自動嘗試重新連線，這與 Discord 伺服器
  端主動通知「你已離開頻道」的 on_voice_state_update 事件是兩套
  不同機制：後者我們已在 cogs/music/music.py 監聽並清理，
  前者卻沒有任何機制可以感知。
- 若底層網路持續不穩，discord.py 內部的重連迴圈可能長時間停留在
  pending 狀態，我們的 GuildPlayer 卻毫無感知，繼續持有一個
  「看似連接、實際上已死」的 VoiceClient；當這個殘留的重連任務
  最終被垃圾回收時，asyncio 會噴出
  「Task was destroyed but it is pending!」的警告。
- 修正方式：_voice_watchdog() 週期性檢查 is_connected()；連續斷線
  超過寬限時間（music.voice_reconnect_grace_seconds）仍未恢復時，
  視為連線已死，主動強制斷線並清理狀態，而非放任 discord.py 的
  內部重連迴圈無限期卡住。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.music.queue import MusicQueue, LoopMode
from core.music.song  import Song
from core.system.settings import get, get_int

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

        # ── 語音健康監控狀態 ──────────────────────
        self._watchdog_task:      asyncio.Task | None = None
        self._disconnected_since: float        | None = None

    # ── 連線管理 ──────────────────────

    async def connect(self, channel: discord.VoiceChannel) -> discord.VoiceClient:
        """
        連線至語音頻道；已連線則移動至新頻道。

        幽靈連接清理：self._vc 存在但 is_connected() 為 False 時，
        先強制 disconnect() 再重新連線，避免 ClientException。

        TimeoutError 轉換：channel.connect() 逾時時拋出 TimeoutError，
        其 str() 為空字串，直接顯示給使用者毫無意義，改為包裝成
        ConnectionError 並附上可讀說明。

        連接逾時秒數由 settings.json music.voice_connect_timeout 控制。
        """
        timeout = get_int("music.voice_connect_timeout", 30)

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

        self._start_watchdog()
        return self._vc

    async def disconnect(self) -> None:
        """中斷連線並完整清理狀態。"""
        self._cancel_idle_timer()
        self._cancel_watchdog()
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

        was_active     = self.is_active
        songs, skipped = await Song.from_playlist(url, requester)

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
        """設定音量（0.0 至 2.0），若正在播放則即時套用。"""
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
        timeout = get_int("music.idle_timeout_seconds", 180)
        await asyncio.sleep(timeout)
        log.info("[%s] 閒置 %ds，自動斷線", self.guild.name, timeout)
        await self.disconnect()

    # ── 語音健康監控 ──────────────────────

    def _start_watchdog(self) -> None:
        """連線成功後啟動健康監控迴圈。"""
        self._cancel_watchdog()
        self._disconnected_since = None
        self._watchdog_task = asyncio.create_task(self._voice_watchdog())

    def _cancel_watchdog(self) -> None:
        """停止健康監控迴圈（disconnect() 時呼叫）。"""
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task      = None
        self._disconnected_since = None

    async def _voice_watchdog(self) -> None:
        """
        週期性檢查語音連線健康度。

        每隔 music.voice_health_check_interval_seconds 秒檢查一次
        is_connected()；一旦連續斷線超過
        music.voice_reconnect_grace_seconds 秒仍未恢復，視為
        discord.py 內部的自動重連已經失敗，主動強制清理連線，
        而非放任其無限期卡在 pending 狀態。
        """
        interval = get_int("music.voice_health_check_interval_seconds", 15)
        grace    = get_int("music.voice_reconnect_grace_seconds", 60)
        loop     = asyncio.get_event_loop()

        while True:
            await asyncio.sleep(interval)

            if self._vc is None:
                return  # 已被 disconnect() 清理，監控迴圈自然結束

            if self._vc.is_connected():
                self._disconnected_since = None
                continue

            now = loop.time()
            if self._disconnected_since is None:
                self._disconnected_since = now
                continue

            if now - self._disconnected_since < grace:
                continue

            # ── 超過寬限時間仍未恢復，視為連線已死 ──────────────────────
            log.warning(
                "[%s] 語音連線已斷開超過 %ds 仍未恢復，強制清理",
                self.guild.name, grace,
            )
            await self._notify_connection_lost()
            await self.disconnect()
            return

    async def _notify_connection_lost(self) -> None:
        """語音連線判定為已死時，於文字頻道通知使用者（有設定時才發送）。"""
        if not self.text_channel:
            return
        try:
            await self.text_channel.send("語音連線不穩定，已自動離開頻道，請重新使用 /play")
        except discord.HTTPException as exc:
            log.warning("[%s] 無法發送連線中斷通知：%s", self.guild.name, exc)
