"""
core/system/event_bus.py

Modification():
- on() 會略過同一事件的同一 handler，避免重複 import 或熱重載造成背景任務重複執行。
- Handler 仍以 create_task 方式執行，不阻塞發佈者。
- 背景 task 會被集合追蹤，完成後自動移除，避免執行中被回收。

Description():

- 本檔提供輕量 pub/sub 事件系統，取代 core.py 中散落的 asyncio.create_task()。
- 各模組在 import 時自行註冊 handler，發佈者只需呼叫 emit()。

設計說明：
- 全域 _handlers dict，無需實例化
- emit() 同時建立所有對應 handler 的 Task，錯誤各自獨立

修正：
- 移除文件中提及但實際未定義的 fire_and_forget()，避免誤導使用者；
  同步函式請改用 asyncio.create_task(event_bus.emit(...))
- logger 命名空間由 bot.ai.event_bus 改為 bot.system.event_bus，
  與本檔實際所在的 core/system/ 對應
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger("bot.system.event_bus")

# ── 全域 handler 表 ──────────────────────

_handlers: dict[str, list[Callable[..., Coroutine]]] = defaultdict(list)
_background_tasks: set[asyncio.Task] = set()

# ── 公開 API ──────────────────────

def on(event: str, handler: Callable[..., Coroutine]) -> None:
    """
    注冊事件 handler。
    同一事件可注冊多個 handler，發佈時全部並行執行。

    Example:
        event_bus.on("message_generated", my_async_handler)
    """
    handlers = _handlers[event]
    if handler in handlers:
        logger.debug("[event_bus] duplicate handler ignored event=%s handler=%s", event, handler.__name__)
        return
    handlers.append(handler)


async def emit(event: str, **kwargs: Any) -> None:
    """
    發佈事件，所有對應 handler 以 asyncio.create_task 背景執行。
    handler 的例外不影響其他 handler 或發佈者。
    """
    handlers = _handlers.get(event, [])
    if not handlers:
        return

    for handler in handlers:
        task = asyncio.create_task(
            _safe_call(handler, event, **kwargs),
            name=f"event_{event}_{handler.__name__}",
        )
        # 加入追蹤集合，避免 Task 在執行中被 GC 回收；完成後自動移除
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


async def _safe_call(
    handler: Callable[..., Coroutine],
    event:   str,
    **kwargs: Any,
) -> None:
    """包裝 handler 呼叫，例外靜默 log，不對外拋出。"""
    try:
        await handler(**kwargs)
    except Exception as e:
        logger.debug("[event_bus] handler=%s event=%s error=%s", handler.__name__, event, e)
