"""
core/ai/core.py

Modification():
- generate() 是 AI 對話唯一公開入口，負責協調路由、上下文、Prompt 與模型呼叫。
- 新增 files / image_parts 參數，修正 Discord 附件傳入後 generate() 介面不一致的崩潰。
- 多模態圖片會以 Gemini Part 送入模型；若路由選到非 Gemini，會自動切到 MULTIMODAL_MODEL。
- channel_id 會一路傳給 context_manager 與 save_message，避免跨頻道串台。
- client、模型名稱與 fallback 皆使用集中模組，避免重複硬編碼。

職責：
- 驗證使用者狀態與 prompt 安全性。
- 組裝 context 與 prompt，呼叫 Gemini / Gemma，處理 fallback 與結果儲存。
- 透過 event_bus 觸發背景記憶任務，不在本檔直接操作底層資料庫。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence

from google.genai import types
from google.genai.errors import ClientError, ServerError

from core.ai.abuse_guard import check_and_record as check_abuse
from core.ai.agent_router import route as make_route
from core.ai.budget import record_error, record_usage
from core.ai.context_manager import build as build_context
from core.ai.file_parser.models import ParsedFile
from core.ai.gemini_client import client
from core.ai.memory_manager import save_message
from core.ai.models import FALLBACK_MODEL, MULTIMODAL_MODEL, is_gemini
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

# ── 常數 ──────────────────────

TIMEOUT = 30
RETRY   = 3

_BLOCKED   = object()   # 安全過濾器擋住
_MALFORMED = object()   # MALFORMED_RESPONSE

ContentPayload = str | list[str | types.Part]

# ── 內部工具 ──────────────────────

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
    image_parts:   Sequence[types.Part] | None = None,
) -> ContentPayload:
    """
    建立 generate_content 的 contents。

    Gemini 可直接接收文字與 Part 列表；Gemma 不支援 system_instruction，
    因此仍將 system_prompt 拼入文字內容。圖片 Part 只會在 Gemini 路徑使用。
    """
    if is_gemini(model):
        if image_parts:
            return [final_prompt, *image_parts]
        return final_prompt
    return f"{system_prompt}\n\n{final_prompt}"

# ── API 呼叫層 ──────────────────────

async def _call(
    model: str,
    contents: ContentPayload,
    config: types.GenerateContentConfig,
):
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
    image_parts:   Sequence[types.Part] | None = None,
    max_retries:   int = RETRY,
) -> str | object | None:
    """
    回傳：str → 成功 | _BLOCKED → 安全過濾 | _MALFORMED → 格式錯誤 | None → 可重試
    """
    contents = _build_contents(model, system_prompt, prompt, image_parts)

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

# ── 主入口 ──────────────────────

async def generate(
    user,
    prompt: str,
    channel_id: str = "",
    *,
    files: Sequence[ParsedFile] | None = None,
    image_parts: Sequence[types.Part] | None = None,
) -> str:
    user_id  = str(user.id)
    # getattr(..., None) or getattr(...) 在型別上會被 mypy 推導為
    # Any | None（無法保證一定是 str），用 str() 包一層確保型別明確，
    # 同時也防呆：萬一 display_name/name 意外回傳非字串值，
    # 仍能安全轉為字串而不是讓後續呼叫炸掉。
    username = str(
        getattr(user, "display_name", None) or getattr(user, "name", None) or user_id
    )

    # ── 封鎖檢查 ──────────────────────
    if is_banned(user_id):
        logger.info("[blocked] user=%s", user_id)
        return "抱歉，我沒辦法回應你的提問。"

    # ── 異常請求頻率檢查（自動暫時限制，非 Owner 手動封鎖） ──────────────────────
    allowed, restrict_reason = check_abuse(user_id)
    if not allowed:
        logger.info("[abuse_guard] user=%s restricted: %s", user_id, restrict_reason)
        return restrict_reason or "請求過於頻繁，請稍後再試"

    # ── Prompt 清理與注入偵測 ──────────────────────
    guard = sanitize_prompt(prompt)
    clean = guard.cleaned

    logger.debug(
        "[prompt_guard] user=%s injection=%s pattern=%r input_len=%d",
        user_id, guard.injection_detected, guard.matched_pattern, len(clean),
    )

    if not clean:
        return "請輸入有效的內容"

    # ── 社交資訊（log 用） ──────────────────────
    user_info = get_user_info(user_id, username)
    logger.info(
        "[request] user=%s(%s) tier=%s interactions=%d",
        user_id, username,
        user_info["tier_name"],
        user_info["interaction_count"],
    )

    parsed_files = list(files or [])
    images       = list(image_parts or [])

    # ── 規則路由（模型 + 工具，無 AI 呼叫） ──────────────────────
    decision = make_route(clean)

    # ── 多模態路由保護 ──────────────────────
    if images and not is_gemini(decision.model):
        logger.info(
            "[multimodal_route] user=%s model=%s -> %s images=%d",
            user_id, decision.model, MULTIMODAL_MODEL, len(images),
        )
        decision.model = MULTIMODAL_MODEL

    # ── 搜尋快取（命中則跳過 Grounding） ──────────────────────
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

    # ── Context 組裝 ──────────────────────
    bundle = await build_context(
        user_id            = user_id,
        username           = username,
        channel_id         = channel_id,
        clean              = clean,
        injection_detected = guard.injection_detected,
        route              = decision,
        cached_search      = cached_search,
        files              = parsed_files,
    )

    # ── Prompt 組裝 ──────────────────────
    system_prompt = get_system_prompt()
    final_prompt  = build_prompt(bundle)

    config = _build_config(decision.model, decision.use_search, system_prompt)

    logger.info(
        "[call] user=%s model=%s search=%s prompt_len=%d",
        user_id, decision.model, decision.use_search, len(final_prompt),
    )

    # ── API 呼叫 ──────────────────────
    result = await _try_generate(
        decision.model, final_prompt, config, user_id, system_prompt,
        image_parts=images,
    )

    # ── Fallback（max_retries=1 避免 quota 等待 × 3） ──────────────────────
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
            image_parts=images if is_gemini(FALLBACK_MODEL) else None,
            max_retries=1,
        )

    # ── Sentinel 處理 ──────────────────────
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

    # ── Grounding 結果回填快取 ──────────────────────
    if decision.use_search and text:
        save_result(clean, text[:800])

    # ── 儲存對話歷史 ──────────────────────
    save_message(user_id, "user",      clean,         channel_id)
    save_message(user_id, "assistant", text[:2_000], channel_id)
    increment_interaction(user_id)

    # ── 觸發背景任務（透過 event_bus） ──────────────────────
    await event_bus.emit(
        "message_generated",
        user_id  = user_id,
        username = username,
        user_msg = clean,
        ai_msg   = text,
    )

    return text
