"""
core/music/queue.py

職責：
- 定義 LoopMode 列舉（OFF / SINGLE / QUEUE）
- MusicQueue 管理播放佇列，支援循環、隨機、移動、歷史記錄
- advance() 依 LoopMode 決定推進邏輯，不含任何 Discord 操作

Modification():

- 移植自 music_bot/core/queue.py，無需修改（邏輯純粹）
- 新增 total_duration 屬性供 embeds 顯示總時長

"""

from __future__ import annotations

import random
from collections import deque
from enum import Enum, auto

from core.music.song import Song


# ── 循環模式 ──────────────────────

class LoopMode(Enum):
    OFF    = auto()   # 不循環
    SINGLE = auto()   # 單首重複
    QUEUE  = auto()   # 全佇列循環


# ── 佇列 ──────────────────────

class MusicQueue:
    """
    音樂播放佇列。
    所有對外索引均為 1-based，與使用者介面一致。
    """

    def __init__(self) -> None:
        self._queue:   deque[Song] = deque()
        self._history: deque[Song] = deque(maxlen=50)
        self.current:  Song | None  = None
        self.loop_mode: LoopMode   = LoopMode.OFF

    # ── 基本操作 ──────────────────────

    def add(self, song: Song) -> None:
        self._queue.append(song)

    def insert_at(self, index: int, song: Song) -> None:
        songs = list(self._queue)
        songs.insert(max(0, index - 1), song)
        self._queue = deque(songs)

    def remove(self, index: int) -> Song | None:
        if not 1 <= index <= len(self._queue):
            return None
        songs   = list(self._queue)
        removed = songs.pop(index - 1)
        self._queue = deque(songs)
        return removed

    def move(self, from_idx: int, to_idx: int) -> bool:
        n = len(self._queue)
        if not (1 <= from_idx <= n and 1 <= to_idx <= n):
            return False
        songs = list(self._queue)
        song  = songs.pop(from_idx - 1)
        songs.insert(to_idx - 1, song)
        self._queue = deque(songs)
        return True

    def shuffle(self) -> None:
        songs = list(self._queue)
        random.shuffle(songs)
        self._queue = deque(songs)

    def clear(self) -> None:
        self._queue.clear()

    # ── 播放推進 ──────────────────────

    def advance(self) -> Song | None:
        """
        推進至下一首並回傳：
        - SINGLE：重播 current
        - QUEUE ：播完的曲目放回尾端再推進
        - OFF   ：直接推進，空則回傳 None
        """
        if self.loop_mode == LoopMode.SINGLE and self.current:
            return self.current

        if self.current:
            self._history.append(self.current)
            if self.loop_mode == LoopMode.QUEUE:
                self._queue.append(self.current)

        self.current = self._queue.popleft() if self._queue else None
        return self.current

    # ── 屬性 ──────────────────────

    @property
    def is_empty(self) -> bool:
        return not self._queue

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def songs(self) -> list[Song]:
        return list(self._queue)

    @property
    def history(self) -> list[Song]:
        return list(reversed(self._history))

    @property
    def total_duration(self) -> int:
        return sum(s.duration for s in self._queue)
