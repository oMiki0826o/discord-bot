"""
core/ai/core.py

Modification():
- generate() 是 AI 對話唯一公開入口，負責協調路由、上下文、Prompt 與模型呼叫。
- 新增 files / image_parts 參數，修正 Discord 附件傳入後 generate() 介面不一致的崩潰。
- 多模態圖片會以 Gemini Part 送入模型；若選到 Gemma 類別，會自動切到 Flash 池。
- channel_id 會一路傳給 context_manager 與 save_message，避免跨頻道串台。
- client、模型類別與輪替池皆使用集中模組，避免重複硬編碼。
- 新增 model_override 選填關鍵字參數，原樣轉呼叫 agent_router.route()：
  讓 cogs/ai/ai_command.py 的 /ai 指令下拉選單可覆寫規則路由的模型
  選擇。本檔不解析、不驗證其內容——合法性檢查、MODEL_CHOICES 對照、
  以及「指定模型不支援搜尋時自動升級」都由 agent_router 負責，這裡
  只單純轉傳一個可能為 None 的字串。此參數與既有的多模態路由保護
  （images 存在但類別為 Gemma → 切到 Flash 池）及搜尋雙重
  保險（use_search 但模型非 Gemini → 停用搜尋）完全相容：兩者都是
  在 decision 產生「之後」才生效的保護層，不論 decision.model 是自動
  判斷還是手動指定，都會一併套用，不需要另外處理。

職責：
- 驗證使用者狀態與 prompt 安全性。
- 組裝 context 與 prompt，呼叫 Gemini / Gemma，處理模型池輪替與結果儲存。
- 透過 event_bus 觸發背景記憶任務，不在本檔直接操作底層資料庫。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter

from google.genai import types
from google.genai.errors import ClientError, ServerError

from core.ai.abuse_guard import check_and_record as check_abuse
from core.ai.agent_router import route as make_route, strip_model_prefix
from core.ai.budget import record_error, record_usage
from core.ai.context_manager import build as build_context
from core.ai.file_parser.models import ParsedFile
from core.ai.gemini_client import client
from core.ai.memory_manager import save_message
from core.ai.models import (
    MULTIMODAL_CATEGORY,
    get_model_candidates,
    get_primary_model,
    is_gemini,
)
from core.ai.prompt_builder import build as build_prompt
from core.ai.prompt_builder import get_system_prompt
from core.ai.quota_manager import (
    MODEL_QUOTA_UNTIL as _MODEL_QUOTA_UNTIL,
    clear_quota_cooldown,
    foreground_request,
    mark_quota_exhausted,
    quota_remaining,
)
from core.ai.search_manager import check_cache, save_result
from core.ai.user_context import (
    get_user_info,
    increment_interaction,
    is_banned,
)
from core.system import event_bus
from core.system.settings import get_float, get_int
from utils.ai.prompt_guard import sanitize_prompt

logger = logging.getLogger("bot.ai.core")

# ── 常數 ──────────────────────

_BLOCKED   = object()   # 安全過濾器擋住
_MALFORMED = object()   # MALFORMED_RESPONSE
_QUOTA_EXHAUSTED = object()  # 當前模型配額用完，立即輪替下一個

ContentPayload = str | list[str | types.Part]
ChunkCallback = Callable[[str], Awaitable[None]]
RetryCallback = Callable[[], Awaitable[None]]

# ── 內部工具 ──────────────────────

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
    max_output_tokens = max(128, get_int("ai.max_output_tokens", 1200))
    if is_gemini(model):
        if use_search:
            return types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                max_output_tokens=max_output_tokens,
            )
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=max_output_tokens,
        )
    return types.GenerateContentConfig(max_output_tokens=max_output_tokens)


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
    on_chunk: ChunkCallback | None = None,
):
    # 整段串流都算前景請求；背景記憶／摘要在這段期間不會再發起新模型呼叫。
    async with foreground_request():
        return await _call_active(model, contents, config, on_chunk)


async def _call_active(
    model: str,
    contents: ContentPayload,
    config: types.GenerateContentConfig,
    on_chunk: ChunkCallback | None = None,
):
    timeout = max(1.0, get_float("ai.model_timeout_seconds", 15.0))
    if on_chunk is None:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model, contents=contents, config=config,
            ),
            timeout=timeout,
        )
        return (res.text or "").strip(), res

    parts: list[str] = []
    last_response = None
    callback = on_chunk
    stream_started = perf_counter()
    first_text_logged = False
    stream = await asyncio.wait_for(
        client.aio.models.generate_content_stream(
            model=model, contents=contents, config=config,
        ),
        timeout=timeout,
    )
    iterator = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(anext(iterator), timeout=timeout)
        except StopAsyncIteration:
            break
        last_response = chunk
        chunk_text = chunk.text or ""
        if not chunk_text:
            continue
        if not first_text_logged:
            logger.info(
                "[timing] model=%s ttft=%.3fs",
                model,
                perf_counter() - stream_started,
            )
            first_text_logged = True
        parts.append(chunk_text)
        if callback is not None:
            try:
                await callback("".join(parts))
            except Exception as e:
                logger.warning("[stream_callback] model=%s error=%s", model, e)
                callback = None

    return "".join(parts).strip(), last_response


async def _try_generate(
    model:         str,
    prompt:        str,
    config:        types.GenerateContentConfig,
    user_id:       str,
    system_prompt: str,
    image_parts:   Sequence[types.Part] | None = None,
    max_retries:   int | None = None,
    on_chunk:      ChunkCallback | None = None,
) -> str | object | None:
    """
    回傳：str → 成功 | _BLOCKED → 安全過濾 | _MALFORMED → 格式錯誤 | None → 可重試
    """
    contents = _build_contents(model, system_prompt, prompt, image_parts)
    if max_retries is None:
        max_retries = max(1, get_int("ai.model_server_retries", 1))

    for attempt in range(max_retries):
        try:
            text, res = await _call(model, contents, config, on_chunk=on_chunk)

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
                logger.warning(
                    "[quota] user=%s model=%s，切換模型池下一個",
                    user_id, model,
                )
                record_error("quota_exceeded", user_id, model)
                return _QUOTA_EXHAUSTED
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


async def _try_model_pool(
    category:      str,
    preferred:     str,
    prompt:        str,
    user_id:       str,
    system_prompt: str,
    use_search:    bool,
    image_parts:   Sequence[types.Part] | None = None,
    on_chunk:      ChunkCallback | None = None,
    on_retry:      RetryCallback | None = None,
) -> tuple[str | object | None, str]:
    """依 settings 的模型池順序呼叫，配額或可重試錯誤時切換下一個。"""
    candidates = get_model_candidates(category, preferred)
    last_model = preferred
    max_attempts = max(1, get_int("ai.max_model_attempts", 2))
    attempts = 0

    for index, model in enumerate(candidates):
        last_model = model
        remaining = quota_remaining(model)
        if remaining > 0:
            logger.info(
                "[model_pool] skip quota-cooldown model=%s remaining=%.0fs",
                model, remaining,
            )
            continue
        if (use_search or image_parts) and not is_gemini(model):
            logger.warning(
                "[model_pool] skip incompatible model=%s category=%s",
                model, category,
            )
            continue

        if attempts >= max_attempts:
            logger.warning(
                "[model_pool] max attempts reached category=%s attempts=%d",
                category, attempts,
            )
            break
        if attempts > 0 and on_retry is not None:
            try:
                await on_retry()
            except Exception as e:
                logger.warning("[stream_retry_reset] model=%s error=%s", model, e)
        attempts += 1

        logger.info(
            "[model_pool] category=%s model=%s position=%d/%d",
            category, model, index + 1, len(candidates),
        )
        result = await _try_generate(
            model,
            prompt,
            _build_config(model, use_search, system_prompt),
            user_id,
            system_prompt,
            image_parts=image_parts,
            on_chunk=on_chunk,
        )
        if result is _QUOTA_EXHAUSTED:
            mark_quota_exhausted(model)
        elif result is not None:
            clear_quota_cooldown(model)

        if result is _QUOTA_EXHAUSTED or result is None:
            if index < len(candidates) - 1:
                logger.warning(
                    "[model_pool] rotate category=%s failed=%s next=%s",
                    category, model, candidates[index + 1],
                )
            continue
        return result, model

    return None, last_model

# ── 主入口 ──────────────────────

async def generate(
    user,
    prompt: str,
    channel_id: str = "",
    *,
    files: Sequence[ParsedFile] | None = None,
    image_parts: Sequence[types.Part] | None = None,
    model_override: str | None = None,
    on_chunk: ChunkCallback | None = None,
    on_retry: RetryCallback | None = None,
) -> str:
    request_started = perf_counter()
    user_id  = str(user.id)
    # getattr(..., None) or getattr(...) 在型別上會被 mypy 推導為
    # Any | None（無法保證一定是 str），用 str() 包一層確保型別明確，
    # 同時也防呆：萬一 display_name/name 意外回傳非字串值，
    # 仍能安全轉為字串而不是讓後續呼叫炸掉。
    username = str(
        getattr(user, "display_name", None) or getattr(user, "name", None) or user_id
    )

    # ── 封鎖檢查 ──────────────────────
    if await is_banned(user_id):
        logger.info("[blocked] user=%s", user_id)
        return "抱歉，我沒辦法回應你的提問。"

    # ── 異常請求頻率檢查（自動暫時限制，非 Owner 手動封鎖） ──────────────────────
    allowed, restrict_reason = await check_abuse(user_id)
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
    user_info = await get_user_info(user_id, username)
    logger.info(
        "[request] user=%s(%s) tier=%s interactions=%d",
        user_id, username,
        user_info["tier_name"],
        user_info["interaction_count"],
    )

    parsed_files = list(files or [])
    images       = list(image_parts or [])

    # ── 規則路由（模型 + 工具，無 AI 呼叫） ──────────────────────
    decision = make_route(clean, model_override=model_override)

    # 開頭的「用flash」等語句只用於選擇模型，不送給模型，
    # 也不寫入對話記憶或搜尋快取。
    clean = strip_model_prefix(clean)
    if not clean:
        return "請輸入有效的內容"

    # ── 多模態路由保護 ──────────────────────
    if images and decision.category == "gemma":
        old_category = decision.category
        decision.category = MULTIMODAL_CATEGORY
        decision.model = get_primary_model(decision.category)
        logger.info(
            "[multimodal_route] user=%s category=%s -> %s model=%s images=%d",
            user_id, old_category, decision.category, decision.model, len(images),
        )

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
    context_started = perf_counter()
    bundle = await build_context(
        user_id            = user_id,
        username           = username,
        channel_id         = channel_id,
        clean              = clean,
        injection_detected = guard.injection_detected,
        route              = decision,
        cached_search      = cached_search,
        files              = parsed_files,
        user_info          = user_info,
    )
    context_elapsed = perf_counter() - context_started

    # ── Prompt 組裝 ──────────────────────
    system_prompt = get_system_prompt()
    final_prompt  = build_prompt(bundle)

    logger.info(
        "[call] user=%s category=%s primary=%s search=%s prompt_len=%d",
        user_id, decision.category, decision.model,
        decision.use_search, len(final_prompt),
    )

    # ── API 呼叫 ──────────────────────
    model_started = perf_counter()
    result, used_model = await _try_model_pool(
        decision.category,
        decision.model,
        final_prompt,
        user_id,
        system_prompt,
        decision.use_search,
        image_parts=images,
        on_chunk=on_chunk,
        on_retry=on_retry,
    )
    model_elapsed = perf_counter() - model_started

    # ── Sentinel 處理 ──────────────────────
    if result is _MALFORMED:
        return "模型回應格式異常，請改用 用flash 或 用gemini 再試"

    if result is _BLOCKED:
        return "這個請求被安全過濾器擋住了，請調整內容後再試"

    if not result:
        logger.error(
            "[give_up] user=%s category=%s last_model=%s",
            user_id, decision.category, used_model,
        )
        record_error("give_up", user_id, used_model)
        return "AI 服務暫時不可用，請稍後再試"

    # 經過上面三個 sentinel 分支後，result 必定是 str（_MALFORMED /
    # _BLOCKED 已 return，falsy 值也已 return），但其宣告型別仍是
    # str | object | None，mypy 無法從 sentinel 的 identity 比較
    # narrow 出 str，這裡用 str() 明確轉型，同時也是一層防呆。
    text: str = str(result)
    logger.info(
        "[response] user=%s model=%s chars=%d",
        user_id, used_model, len(text),
    )

    # ── Grounding 結果回填快取 ──────────────────────
    if decision.use_search and text:
        save_result(clean, text[:800])

    # ── 儲存對話歷史 ──────────────────────
    event_bus.create_background_task(
        _persist_and_emit(
            user_id=user_id,
            username=username,
            user_msg=clean,
            ai_msg=text,
            channel_id=channel_id,
        ),
        name=f"persist_ai_conversation_{user_id}",
    )

    # ── 觸發背景任務（透過 event_bus） ──────────────────────
    logger.info(
        "[timing] user=%s context=%.3fs model=%.3fs total=%.3fs",
        user_id, context_elapsed, model_elapsed, perf_counter() - request_started,
    )

    return text


async def _persist_and_emit(
    *,
    user_id: str,
    username: str,
    user_msg: str,
    ai_msg: str,
    channel_id: str,
) -> None:
    """背景儲存對話，完成後再觸發依賴新訊息的記憶與個人檔案任務。"""
    await asyncio.gather(
        save_message(user_id, "user", user_msg, channel_id),
        save_message(user_id, "assistant", ai_msg[:2_000], channel_id),
    )
    await increment_interaction(user_id)
    await event_bus.emit(
        "message_generated",
        user_id=user_id,
        username=username,
        user_msg=user_msg,
        ai_msg=ai_msg,
    )
