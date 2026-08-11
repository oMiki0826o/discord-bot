"""
utils/async_db.py

Modification():

- 新增本檔案：提供 to_thread() 裝飾器，讓 database/repository 底下
  原本同步（sqlite3 為同步 API）的函式，可以在 async 函式內用
  await 呼叫，實際執行則透過 asyncio.to_thread() 丟到背景執行緒池，
  不佔用事件迴圈。

  背景：稽核發現 database/repository 底下約 60 個函式（init_tables
  除外，見下方說明）全部是同步函式，卻被 cogs／core 底下的 async
  函式直接呼叫（例如 `def on_message(self, message): ...
  repo.insert_message(...)`），完全沒有透過 asyncio.to_thread 或
  任何方式離開事件迴圈。SQLite 對小型本地檔案的單次查詢通常很快
  （微秒到低毫秒等級），但只要 Discord 事件量夠大、或剛好遇到
  SQLite 需要等待檔案鎖定的情況，這些呼叫都會直接卡住整個 Bot
  的事件迴圈，讓所有其他訊息／互動處理跟著延遲。

  修正做法選擇說明：與其在 cogs／core 這 70 幾個呼叫端各自包一層
  asyncio.to_thread(...)（容易漏改、日後新增呼叫端也容易忘記），
  改為在 repository 函式「定義」的地方套用這個裝飾器一次，呼叫端
  只需要維持原本呼叫方式再加上 await 即可，往後新增的 repository
  函式只要套用同一個裝飾器，呼叫端不需要另外记得要包 asyncio.to_thread。

  init_tables() 例外：這幾個函式只在 Bot 啟動時呼叫一次
  （startup.py 的 initialize()），而 initialize() 本身已經整個由
  bot.py 的 setup_hook() 透過 `await asyncio.to_thread(initialize)`
  離開事件迴圈執行，因此 init_tables() 不需要重複包裝，維持同步
  函式即可，也不需要呼叫端加 await（呼叫端仍是 startup.py 內的
  同步流程）。

職責：
- to_thread：包裝同步函式，回傳同名但可 await 的非同步版本，
  內部委派給 asyncio.to_thread() 執行，不阻塞事件迴圈。
"""

from __future__ import annotations

import asyncio
import functools
from typing import Awaitable, Callable, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


def to_thread(func: Callable[_P, _R]) -> Callable[_P, Awaitable[_R]]:
    """
    將同步函式包裝成可用 await 呼叫的版本，實際執行委派給
    asyncio.to_thread()，於背景執行緒池中執行，不阻塞事件迴圈。

    使用範例：
        @to_thread
        def get_recent_messages(user_id: str, channel_id: str) -> list:
            ...

        # 呼叫端：
        rows = await get_recent_messages(user_id, channel_id)
    """
    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return await asyncio.to_thread(func, *args, **kwargs)
    return wrapper
