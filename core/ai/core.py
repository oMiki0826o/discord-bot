"""
core/ai/core.py

職責：
- generate() 唯一公開入口
- 協調各服務層（context_manager / prompt_builder / agent_router / budget）
- 不直接操作 DB，透過服務模組存取資料

重構重點：
- 解耦：將上下文收集、Prompt 組裝、模型路由各自委派給專責模組
- event_bus：取代多個散落的 asyncio.create_task()，背景任務由模組自行注冊
- 簡化後 generate() 主流程可一眼看清：封鎖 → 清理 → 路由 → 快取 → 上下文 → 呼叫 → 儲存 → 事件

修正：
- FALLBACK_MODEL 改由 core.ai.models 統一提供，移除硬編碼字串
  （與 agent_router / memory_manager / user_context 共用同一份模型常數）
- is_gemini 改為直接從 core.ai.models 匯入，不再透過
  core.ai.agent_router 間接 re-export（agent_router 本身也是從
  core.ai.models 匯入，直接引用來源更清楚）
- client 改由 core.ai.gemini_client 統一提供，移除本檔的
  genai.Client(api_key=GEMINI_API) 重複建立，並連帶移除
  未使用的 genai / GEMINI_API import

新增（channel_id）：
- generate() 新增 channel_id 參數（呼叫端 cogs/ai/chat.py 傳入
  str(message.channel.id)），轉交 context_manager.build() 與
  memory_manager.save_message()，確保：
    1. 「相關歷史訊息」與「最近對話」依目前頻道過濾
    2. 新訊息存入 messages 表時帶上 channel_id
  達成「依賴 channel_id 避免不同伺服器間串台」的目標

新增（異常行為自動偵測）：
- 封鎖檢查之後新增 core.ai.abuse_guard.check_and_record() 呼叫，
  以滑動視窗偵測短時間內過量請求，超過門檻時自動施加暫時限制
  （與 is_banned() 的永久封鎖區分，效期過後自動解除，不需 Owner 介入）
"""

from __future__ import annotations

import asyncio
import logging
import re

from google.genai import types
from google.genai.errors import ClientError, ServerError

from core.ai.abuse_guard import check_and_record as check_abuse
from core.ai.agent_router import route as make_route
from core.ai.budget import record_error, record_usage
from core.ai.context_manager import build as build_context
from core.ai.gemini_client import client
from core.ai.memory_manager import save_message
from core.ai.models import FALLBACK_MODEL, is_gemini
from core.ai.prompt_builder import build as build_prompt
from core.ai.prompt_builder import get_system_prompt
from core.ai.search_manager import check_cache, save_result
from core.ai.user_context import (
    get_user_info,
    increment_interaction,
    is_banned,
)
from core.system import event_bus
from utils.ai.prompt_guard import sanitize_prompt

logger = logging.getLogger("bot.ai.core")

# ── 常數 ──────────────────────────────────────────────────────────────

TIMEOUT = 30
RETRY   = 3

_BLOCKED   = object()   # 安全過濾器擋住
_MALFORMED = object()   # MALFORMED_RESPONSE

# ── 內部工具 ──────────────────────────────────────────────────────────

def _parse_retry_after(error_str: str) -> float:
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", error_str)
    return min(float(m.group(1)), 90.0) if m else 60.0


def _get_block_reason(res) -> str | None:
    feedback = getattr(res, "prompt_feedback", None)
    reason   = getattr(feedback, "block_reason", None) if feedback else None
    return str(reason) if reason else None


def _get_finish_reason(res) -> str | None:
    candidates = getattr(res, "candidates", None)
    if not candidates:
        return None
    return str(getattr(candidates[0], "finish_reason", None) or "")


def _build_config(
    model:         str,
    use_search:    bool,
    system_prompt: str,
) -> types.GenerateContentConfig:
    """
    Gemini → system_instruction + 可選搜尋工具
    Gemma  → 空 config（system_prompt 拼入 contents）
    """
    if is_gemini(model):
        if use_search:
            return types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
        return types.GenerateContentConfig(system_instruction=system_prompt)
    return types.GenerateContentConfig()


def _build_contents(
    model:         str,
    system_prompt: str,
    final_prompt:  str,
) -> str:
    """非 Gemini 模型將 system_prompt 拼入 contents 前段。"""
    if is_gemini(model):
        return final_prompt
    return f"{system_prompt}\n\n{final_prompt}"

# ── API 呼叫層 ────────────────────────────────────────────────────────

async def _call(model: str, contents: str, config: types.GenerateContentConfig):
    res = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=model, contents=contents, config=config,
        ),
        timeout=TIMEOUT,
    )
    return (res.text or "").strip(), res


async def _try_generate(
    model:         str,
    prompt:        str,
    config:        types.GenerateContentConfig,
    user_id:       str,
    system_prompt: str,
    max_retries:   int = RETRY,
) -> str | object | None:
    """
    回傳：str → 成功 | _BLOCKED → 安全過濾 | _MALFORMED → 格式錯誤 | None → 可重試
    """
    contents = _build_contents(model, system_prompt, prompt)

    for attempt in range(max_retries):
        try:
            text, res = await _call(model, contents, config)

            if not text:
                block  = _get_block_reason(res)
                finish = _get_finish_reason(res)
                logger.error(
                    "[empty_response] model=%s block=%s finish=%s",
                    model, block, finish,
                )
                logger.debug("[empty_response] head=\n%s", prompt[:600])
                record_error("empty_response", user_id, model)

                if block:
                    return _BLOCKED
                if finish and "MALFORMED" in finish.upper():
                    logger.error(
                        "[malformed] user=%s model=%s", user_id, model,
                    )
                    return _MALFORMED
                return None

            record_usage(
                user_id=user_id, model=model,
                input_text=prompt, output_text=text,
                res=res, request_type="chat",
            )
            return text

        except asyncio.TimeoutError:
            logger.warning(
                "[timeout] user=%s model=%s attempt=%d/%d",
                user_id, model, attempt + 1, max_retries,
            )
            record_error("timeout", user_id, model)
            return None

        except ServerError as e:
            logger.warning(
                "[server_error] user=%s model=%s attempt=%d/%d error=%s",
                user_id, model, attempt + 1, max_retries, e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2)

        except ClientError as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = _parse_retry_after(err)
                logger.warning(
                    "[quota] user=%s model=%s wait=%.0fs attempt=%d/%d",
                    user_id, model, wait, attempt + 1, max_retries,
                )
                record_error("quota_exceeded", user_id, model)
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                    continue
                return None
            logger.error(
                "[client_error] user=%s model=%s error=%s", user_id, model, e,
            )
            record_error("client_error", user_id, model)
            return None

    logger.error(
        "[failed] user=%s model=%s retries=%d exhausted",
        user_id, model, max_retries,
    )
    return None

# ── 主入口 ────────────────────────────────────────────────────────────

async def generate(user, prompt: str, channel_id: str) -> str:
    user_id  = str(user.id)
    # getattr(..., None) or getattr(...) 在型別上會被 mypy 推導為
    # Any | None（無法保證一定是 str），用 str() 包一層確保型別明確，
    # 同時也防呆：萬一 display_name/name 意外回傳非字串值，
    # 仍能安全轉為字串而不是讓後續呼叫炸掉。
    username = str(
        getattr(user, "display_name", None) or getattr(user, "name", None) or user_id
    )

    # ── 封鎖檢查 ───────────────────────────────────────────────
    if is_banned(user_id):
        logger.info("[blocked] user=%s", user_id)
        return "抱歉，我沒辦法回應你的提問。"

    # ── 異常請求頻率檢查（自動暫時限制，非 Owner 手動封鎖）─────
    allowed, restrict_reason = check_abuse(user_id)
    if not allowed:
        logger.info("[abuse_guard] user=%s restricted: %s", user_id, restrict_reason)
        return restrict_reason or "請求過於頻繁，請稍後再試"

    # ── Prompt 清理與注入偵測 ──────────────────────────────────
    guard = sanitize_prompt(prompt)
    clean = guard.cleaned

    logger.debug(
        "[prompt_guard] user=%s injection=%s pattern=%r input_len=%d",
        user_id, guard.injection_detected, guard.matched_pattern, len(clean),
    )

    if not clean:
        return "請輸入有效的內容"

    # ── 社交資訊（log 用）──────────────────────────────────────
    user_info = get_user_info(user_id, username)
    logger.info(
        "[request] user=%s(%s) tier=%s interactions=%d",
        user_id, username,
        user_info["tier_name"],
        user_info["interaction_count"],
    )

    # ── 規則路由（模型 + 工具，無 AI 呼叫）───────────────────
    decision = make_route(clean)

    # ── 搜尋快取（命中則跳過 Grounding）──────────────────────
    cached_search = None
    if decision.use_search:
        cached_search, still_need = check_cache(clean)
        if not still_need:
            decision.use_search = False
            logger.info("[search_cache] hit user=%s", user_id)

    # 雙重保險：非 Gemini 模型不能 Grounding
    if decision.use_search and not is_gemini(decision.model):
        logger.error(
            "[search_mismatch] model=%s 不支援 Grounding，停用", decision.model,
        )
        decision.use_search = False

    # ── Context 組裝 ───────────────────────────────────────────
    bundle = await build_context(
        user_id            = user_id,
        username           = username,
        channel_id         = channel_id,
        clean              = clean,
        injection_detected = guard.injection_detected,
        route              = decision,
        cached_search      = cached_search,
    )

    # ── Prompt 組裝 ───────────────────────────────────────────
    system_prompt = get_system_prompt()
    final_prompt  = build_prompt(bundle)

    config = _build_config(decision.model, decision.use_search, system_prompt)

    logger.info(
        "[call] user=%s model=%s search=%s prompt_len=%d",
        user_id, decision.model, decision.use_search, len(final_prompt),
    )

    # ── API 呼叫 ───────────────────────────────────────────────
    result = await _try_generate(
        decision.model, final_prompt, config, user_id, system_prompt,
    )

    # ── Fallback（max_retries=1 避免 quota 等待 × 3）──────────
    if result is None:
        logger.warning(
            "[fallback] user=%s %s → %s",
            user_id, decision.model, FALLBACK_MODEL,
        )
        fb_sys = get_system_prompt()
        result = await _try_generate(
            FALLBACK_MODEL,
            final_prompt,
            _build_config(FALLBACK_MODEL, False, fb_sys),
            user_id,
            fb_sys,
            max_retries=1,
        )

    # ── Sentinel 處理 ──────────────────────────────────────────
    if result is _MALFORMED:
        return "模型回應格式異常，請改用 用flash 或 用gemini 再試"

    if result is _BLOCKED:
        return "這個請求被安全過濾器擋住了，請調整內容後再試"

    if not result:
        logger.error(
            "[give_up] user=%s primary=%s fallback=%s",
            user_id, decision.model, FALLBACK_MODEL,
        )
        record_error("give_up", user_id, decision.model)
        return "AI 服務暫時不可用，請稍後再試"

    # 經過上面三個 sentinel 分支後，result 必定是 str（_MALFORMED /
    # _BLOCKED 已 return，falsy 值也已 return），但其宣告型別仍是
    # str | object | None，mypy 無法從 sentinel 的 identity 比較
    # narrow 出 str，這裡用 str() 明確轉型，同時也是一層防呆。
    text: str = str(result)
    logger.info(
        "[response] user=%s model=%s chars=%d",
        user_id, decision.model, len(text),
    )

    # ── Grounding 結果回填快取 ─────────────────────────────────
    if decision.use_search and text:
        save_result(clean, text[:800])

    # ── 儲存對話歷史 ──────────────────────────────────────────
    save_message(user_id, "user",      clean,         channel_id)
    save_message(user_id, "assistant", text[:2_000], channel_id)
    increment_interaction(user_id)

    # ── 觸發背景任務（透過 event_bus）────────────────────────
    await event_bus.emit(
        "message_generated",
        user_id  = user_id,
        username = username,
        user_msg = clean,
        ai_msg   = text,
    )

    return text
