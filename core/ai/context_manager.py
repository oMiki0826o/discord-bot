"""
core/ai/context_manager.py

修正（整合 file_parser 解析結果）：
- ContextBundle 新增 files 欄位，作為並行組裝的資料來源之一，
  與既有的記憶/搜尋/工具結果並列，呼應 file_parser 設計文件的整合點
- build() 新增 files 參數，由 cogs/ai/chat.py 在解析完附件後傳入
- 原有職責不變：統一 Context 組裝流程、回傳 ContextBundle 供
  prompt_builder 使用、並行執行各來源的資料抓取以減少串行延遲

Context 優先級（由 prompt_builder 決定排版）：
1. SECURITY_NOTICE（injection 時）
2. 使用者身份
3. 狀態
4. 個人偏好（profile）
5. Tool 結果（memory / summary / profile）
6. 快取搜尋結果
7. 靜態記憶
8. 附件解析內容（file_parser）
9. 相關歷史訊息
10. 最近對話
11. 使用者輸入
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.ai.agent_router import RouteDecision
from core.ai.memory_manager import search as memory_search
from core.ai.file_parser.models import ParsedFile
from core.ai.user_context import (
    get_user_info,
    get_global_memories,
    state_to_prompt,
    profile_to_prompt,
    extend_state,
)

logger = logging.getLogger("bot.context_manager")

# ── 資料結構 ──────────────────────────────────────────────────────────

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

# ── 主要入口 ──────────────────────────────────────────────────────────

async def build(
    user_id:            str,
    username:           str,
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
    - files：file_parser 已解析完成的附件結果，由 chat.py 傳入，
      本函式只負責原樣放入 ContextBundle，不重新觸發解析
    """
    user_info   = get_user_info(user_id, username)
    global_mems = get_global_memories()

    # ── 並行取得記憶與 tool 結果 ──────────────────────────────
    mem_task  = asyncio.create_task(_get_memory(user_id, clean, global_mems))
    tool_task = asyncio.create_task(_get_tools(route, user_id, clean))

    extend_state(user_id)   # 滑動 TTL

    state_sec   = state_to_prompt(user_id)
    profile_sec = profile_to_prompt(user_id)

    mem_bundle  = await mem_task
    tool_secs   = await tool_task

    # ── 快取搜尋結果注入為最優先 tool_section ─────────────────
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

# ── 內部工具 ──────────────────────────────────────────────────────────

async def _get_memory(user_id: str, query: str, global_mems: list):
    """非同步包裝同步記憶搜尋（避免阻塞 event loop）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, memory_search, user_id, query, global_mems,
    )


async def _get_tools(
    route:   RouteDecision,
    user_id: str,
    query:   str,
) -> list[str]:
    """執行 route 決定的工具，回傳 prompt 片段列表。"""
    from core.ai.agent_router import execute_tools
    return await execute_tools(route, user_id, query)
