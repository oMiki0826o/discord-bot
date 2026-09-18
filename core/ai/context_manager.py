"""
core/ai/context_manager.py

Modification():
- 因應 core/ai/memory_manager.py 的 search() 全面非同步化（見該檔
  Modification 說明），移除本檔原本的 _get_memory() 包裝函式：
  該函式原本存在的唯一目的，是用 loop.run_in_executor() 把當時
  仍是同步函式的 search() 丟到執行緒池執行，避免阻塞事件迴圈。
  search() 現在本身就是 async def，會直接 await 每一次資料庫存取，
  不再需要這層額外的執行緒池包裝，build() 內直接
  asyncio.create_task(memory_search(...)) 排程即可。
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
from core.ai.context_filter import select_profile, select_summary
from core.ai.file_parser.models import ParsedFile
from core.ai.memory_manager import search as memory_search
from core.system.settings import get_int
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
    channel_id:      str                              = ""
    memories:        list[tuple[str, str, int]]      = field(default_factory=list)
    memory_details:  list[dict]                       = field(default_factory=list)
    messages:        list[tuple[str, str]]            = field(default_factory=list)
    recent:          list[tuple[str, str]]            = field(default_factory=list)
    summary:         str                              = ""
    tool_sections:   list[str]                        = field(default_factory=list)
    state_section:   str                              = ""
    profile_section: str                              = ""
    files:           list[ParsedFile]                 = field(default_factory=list)
    security_notice: bool                             = False
    injection_risk:  str                              = "none"
    reply_reference: dict | None                      = None
    channel_messages: list[dict]                      = field(default_factory=list)
    channel_context_max_tokens: int                   = 6_000
    max_tokens:      int                              = 32_768
    # 舊版測試／外部呼叫的字元預算相容欄位；新流程不設定它。
    max_length:      int | None                       = None

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
    user_info:          dict | None = None,
    injection_risk:     str = "none",
    reply_reference:    dict | None = None,
    channel_messages:   list[dict] | None = None,
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
    user_task = (
        asyncio.create_task(get_user_info(user_id, username))
        if user_info is None else None
    )
    global_task = asyncio.create_task(get_global_memories())
    tool_task = asyncio.create_task(_get_tools(route, user_id, channel_id, clean))
    extend_task = asyncio.create_task(extend_state(user_id))
    state_task = asyncio.create_task(state_to_prompt(user_id))
    profile_task = asyncio.create_task(profile_to_prompt(user_id))

    global_mems = await global_task

    # ── 並行取得記憶與 tool 結果 ──────────────────────
    mem_task = asyncio.create_task(memory_search(user_id, channel_id, clean, global_mems))
    mem_bundle, tool_secs, _, state_sec, profile_sec = await asyncio.gather(
        mem_task, tool_task, extend_task, state_task, profile_task,
    )
    if user_task is not None:
        user_info = await user_task

    # Profile 的風格／語言偏好可常駐，主題、備註與舊摘要則需要
    # 與目前問題相關，避免日常閒聊被舊專案、天氣或待辦污染。
    profile_sec = select_profile(profile_sec, clean)
    selected_summary = select_summary(mem_bundle.summary, clean)

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
        channel_id      = channel_id,
        memories        = mem_bundle.memories,
        memory_details  = getattr(mem_bundle, "memory_details", []),
        messages        = mem_bundle.messages,
        recent          = mem_bundle.recent,
        summary         = selected_summary,
        tool_sections   = tool_secs,
        state_section   = state_sec,
        profile_section = profile_sec,
        files           = files or [],
        security_notice = injection_detected,
        injection_risk  = injection_risk,
        reply_reference = reply_reference,
        channel_messages = channel_messages or [],
        channel_context_max_tokens = max(
            500, get_int("ai.channel_context_token_limit", 6_000),
        ),
        max_tokens      = max(4_096, get_int("ai.prompt_max_tokens", 32_768)),
    )

# ── 內部工具 ──────────────────────

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
    from core.ai.agent_router import RouteDecision, execute_tools

    # memory / summary / profile 已由本模組同一次並行查詢取得；再次透過
    # tool executor 查詢只會增加 DB I/O，並可能因完成時間不同注入兩份
    # 不一致的記憶。保留路由判斷，但只執行真正的外部工具。
    internal = {"memory", "summary", "profile"}
    external_tools = [name for name in route.tools if name not in internal]
    if not external_tools:
        return []
    external_route = RouteDecision(
        model=route.model,
        use_search=route.use_search,
        tools=external_tools,
        category=route.category,
    )
    return await execute_tools(external_route, user_id, channel_id, query)
