"""AI 前景／背景請求共用的配額與優先級狀態。"""

from __future__ import annotations

import asyncio
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from core.system.settings import get_int


# 保留公開 dict 讓診斷與測試可直接清除；值為 monotonic 到期時間。
MODEL_QUOTA_UNTIL: dict[str, float] = {}


@dataclass
class _LoopState:
    foreground_active: int = 0


_loop_states: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState] = (
    weakref.WeakKeyDictionary()
)


def _state() -> _LoopState:
    loop = asyncio.get_running_loop()
    state = _loop_states.get(loop)
    if state is None:
        state = _LoopState()
        _loop_states[loop] = state
    return state


def quota_remaining(model: str) -> float:
    """回傳模型配額冷卻剩餘秒數。"""
    return max(0.0, MODEL_QUOTA_UNTIL.get(model, 0.0) - time.monotonic())


def mark_quota_exhausted(model: str, cooldown: int | None = None) -> None:
    seconds = (
        max(0, get_int("ai.model_quota_cooldown_seconds", 300))
        if cooldown is None else max(0, cooldown)
    )
    MODEL_QUOTA_UNTIL[model] = time.monotonic() + seconds


def clear_quota_cooldown(model: str) -> None:
    MODEL_QUOTA_UNTIL.pop(model, None)


def is_quota_error(error: BaseException | str) -> bool:
    text = str(error).upper()
    return "429" in text or "RESOURCE_EXHAUSTED" in text


@asynccontextmanager
async def foreground_request() -> AsyncIterator[None]:
    """標記前景對話正在使用模型，讓新的背景工作主動讓路。"""
    state = _state()
    state.foreground_active += 1
    try:
        yield
    finally:
        state.foreground_active = max(0, state.foreground_active - 1)


@asynccontextmanager
async def background_request(model: str) -> AsyncIterator[bool]:
    """
    前景忙碌或模型冷卻時直接略過新的背景模型工作。

    背景記憶本來就是可延後的增強功能，因此略過比排隊阻塞、接著一次
    爆量呼叫更安全。下一輪對話仍會再次觸發更新。
    """
    state = _state()
    if state.foreground_active > 0 or quota_remaining(model) > 0:
        yield False
        return
    yield True
