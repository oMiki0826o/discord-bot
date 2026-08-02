"""
core/ai/context_manager.py

Modification():
- _get_tools() 新增 channel_id 參數並往下傳給 execute_tools()：
  這是 tool_registry._exec_memory() 參數錯位 bug 修正鏈的最後一環——
  channel_id 從這裡開始才「存在」於 tool 執行的呼叫路徑上，
  execute_tools() 與 tool_registry 的 executor 都需要它才能正確呼叫
  memory_manager.search()。
- ContextBundle 新增 files 欄位，用於承接 file_parser 解析後的附件內容。
- build() 新增 channel_id 與 files 參數，修正記憶搜尋參數錯位問題。
- 記憶搜尋與工具執行維持並行，降低單次 AI 回應延遲。

職責：
- 統一收集 prompt_builder 所需的使用者資訊、記憶、工具結果與附件內容。
- 確保短期對話 context 依 channel_id 隔離，避免跨伺服器或跨頻道串台。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.ai.agent_router import RouteDecision
from core.ai.file_parser.models import ParsedFile
from core.ai.memory_manager import search as memory_search
from core.ai.user_context import (
    extend_state,
    get_global_memories,
    get_user_info,
    profile_to_prompt,
    state_to_prompt,
)

logger = logging.getLogger("bot.context_manager")

# ── 資料結構 ──────────────────────

@dataclass
class ContextBundle:
    """
    所有 prompt 組裝所需資料的統一容器。
    由 context_manager.build() 填充，由 prompt_builder.build() 消費。
    """
    user_input:      str
    user_info:       dict
    memories:        list[tuple[str, str, int]]      = field(default_factory=list)
    messages:        list[tuple[str, str]]            = field(default_factory=list)
    recent:          list[tuple[str, str]]            = field(default_factory=list)
    summary:         str                              = ""
    tool_sections:   list[str]                        = field(default_factory=list)
    state_section:   str                              = ""
    profile_section: str                              = ""
    files:           list[ParsedFile]                 = field(default_factory=list)
    security_notice: bool                             = False
    max_length:      int                              = 12_000

# ── 主要入口 ──────────────────────

async def build(
    user_id:            str,
    username:           str,
    channel_id:         str,
    clean:              str,
    injection_detected: bool,
    route:              RouteDecision,
    cached_search:      str | None = None,
    files:              list[ParsedFile] | None = None,
) -> ContextBundle:
    """
    組裝 ContextBundle：
    - 並行執行記憶搜尋 + tool 執行
    - 套用狀態滑動 TTL
    - 將快取搜尋結果作為額外 tool_section 注入
    - channel_id：用於短期訊息與最近對話過濾，避免不同頻道混入 context
    - files：file_parser 已解析完成的附件結果，由 chat.py 傳入，
      本函式只負責原樣放入 ContextBundle，不重新觸發解析
    """
    user_info   = get_user_info(user_id, username)
    global_mems = get_global_memories()

    # ── 並行取得記憶與 tool 結果 ──────────────────────
    mem_task  = asyncio.create_task(_get_memory(user_id, channel_id, clean, global_mems))
    tool_task = asyncio.create_task(_get_tools(route, user_id, channel_id, clean))

    extend_state(user_id)   # 滑動 TTL

    state_sec   = state_to_prompt(user_id)
    profile_sec = profile_to_prompt(user_id)

    mem_bundle  = await mem_task
    tool_secs   = await tool_task

    # ── 快取搜尋結果注入為最優先 tool_section ──────────────────────
    if cached_search:
        tool_secs.insert(0, f"=== 快取搜尋結果 ===\n{cached_search[:1_000]}")

    logger.info(
        "[context_manager] user=%s memories=%d messages=%d recent=%d tools=%d files=%d",
        user_id,
        len(mem_bundle.memories),
        len(mem_bundle.messages),
        len(mem_bundle.recent),
        len(tool_secs),
        len(files or []),
    )

    return ContextBundle(
        user_input      = clean,
        user_info       = user_info,
        memories        = mem_bundle.memories,
        messages        = mem_bundle.messages,
        recent          = mem_bundle.recent,
        summary         = mem_bundle.summary,
        tool_sections   = tool_secs,
        state_section   = state_sec,
        profile_section = profile_sec,
        files           = files or [],
        security_notice = injection_detected,
    )

# ── 內部工具 ──────────────────────

async def _get_memory(
    user_id: str,
    channel_id: str,
    query: str,
    global_mems: list[tuple[str, str, int]],
):
    """非同步包裝同步記憶搜尋（避免阻塞 event loop）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, memory_search, user_id, channel_id, query, global_mems,
    )


async def _get_tools(
    route:      RouteDecision,
    user_id:    str,
    channel_id: str,
    query:      str,
) -> list[str]:
    """
    執行 route 決定的工具，回傳 prompt 片段列表。

    channel_id 一路往下傳給 execute_tools() → tool_registry 的
    executor（例如 _exec_memory()），修正原本 memory_manager.search()
    呼叫時少一個參數、導致參數整個錯位的問題（詳見 tool_registry.py
    與 agent_router.py 的說明）。
    """
    from core.ai.agent_router import execute_tools
    return await execute_tools(route, user_id, channel_id, query)
