"""
core/system/settings.py

Modification():

- 修正 link_preview.threads_proxy_hosts：www.fixthreads.net 對應的
  原始專案已被作者封存下線；www.vxthreads.net 誤加了官方沒有的 www
  字首（官方專案實際部署在不含 www 的 vxthreads.net），這正是 log
  中兩個候選網域同時失敗的原因。改為 ["vxthreads.net",
  "viewthreads.com"]，並新增 dead_host_cooldown_seconds 供
  core/link_preview/fallback.py 的短期冷卻機制使用。
- link_preview.instagram_proxy_hosts 新增 instagramez.com：與既有的
  ddinstagram 系列網域完全獨立（不同開發者維運），單純只是增加一個
  不會同時故障的候選來源，降低同一時間全部候選都失敗的機率。
- 補齊 music.favorites_per_page / favorites_load_all_limit 與
  voice_channel.jtc_channel_id / category_id 到 _DEFAULTS：這幾個
  設定原本只存在於呼叫端（favorites.py / voice_channel.py）
  get_int() 的行內預設值，能正常運作但沒有集中登記，$settings show
  完全看不到這幾個鍵，等於是「有效但不可見」的設定。
- 保留基於 mtime 的 settings.json 熱重載快取。
- 新增 get_int()、get_float()、get_bool()、get_str()，集中處理設定轉型與 fallback。
- 補齊 AI 訊息、附件上限與 DM 橋接相關預設值，減少 Cog 內硬編碼。
- reload() 仍提供管理員指令強制刷新設定。

Description():

- 本檔是非機密設定的唯一入口，支援點號路徑讀取 settings.json。
- TOKEN、API Key 等機密仍由 .env 與 config.py 管理，不放進 settings.json。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("bot.settings")

# ── 設定檔路徑 ──────────────────────

_ROOT     = Path(__file__).resolve().parents[2]   # 專案根目錄
_SETTINGS = _ROOT / "settings.json"

# ── 快取狀態 ──────────────────────

_cache:      dict[str, Any] = {}
_cache_mtime: float          = -1.0

# ── 預設值（settings.json 缺欄時的回退） ──────────────────────

_DEFAULTS: dict[str, Any] = {
    "bot.command_prefix":      "$",
    "bot.status_type":         "listening",
    "bot.status_text":         "/play",
    "bot.presence":            "online",
    "bot.startup_timeout":     60,

    "ai.default_model":        "gemma",
    "ai.cooldown_seconds":     3.0,
    "ai.max_reply_length":     1500,
    "ai.max_attachments":      5,
    "ai.persona_name":         "流螢",
    "ai.default_attachment_prompt": "請看一下這個附件並告訴我內容。",
    "ai.empty_prompt_message": "請輸入想問的內容",
    "ai.busy_message":         "正在處理上一個請求，請稍後",
    "ai.cooldown_message_template": "請稍等 {seconds:g} 秒再試",
    "ai.thinking_message":     "思考中...",
    "ai.long_reply_notice":    "回覆內容較長，請見附件",
    "ai.empty_reply_message":  "（回覆為空）",
    "ai.error_message_template": "錯誤：{error}",
    "ai.social_tier_names":    {"0":"陌生人","1":"路人","2":"朋友","3":"開拓者"},
    "ai.conversation_state_labels": {
        "normal":"一般對話","roleplay":"角色扮演",
        "creative":"創意寫作","task":"任務模式","debate":"辯論模式",
    },
    "ai.summary_trigger":      40,
    "ai.summary_keep":         10,
    "ai.summary_min_messages": 10,
    "ai.summary_line_max_chars": 200,
    "ai.memory_cache_ttl":     5.0,
    "ai.memory_extract_timeout_seconds": 15,
    "ai.memory_embed_timeout_seconds": 10,
    "ai.memory_summary_timeout_seconds": 20,
    "ai.memory_min_extract_chars": 20,
    "ai.memory_embedding_max_chars": 2000,
    "ai.memory_candidate_limit": 30,
    "ai.message_candidate_limit": 200,
    "ai.recent_message_limit": 12,
    "ai.vector_candidate_limit": 5,
    "ai.memory_vectorize_delay_seconds": 1.0,
    "ai.abuse_window_seconds": 60,
    "ai.abuse_max_requests":   15,
    "ai.abuse_restrict_minutes": 10,
    "ai.search_short_ttl_min": 30,
    "ai.search_long_ttl_min":  1440,
    "ai.search_fuzzy_threshold": 0.85,
    "ai.alert_error_rate_threshold": 0.15,
    "ai.alert_check_interval_seconds": 300,
    "ai.alert_min_sample_size": 5,

    "music.max_queue_size":        200,
    "music.idle_timeout_seconds":  180,
    "music.voice_connect_timeout": 30,
    "music.default_volume_percent": 50,
    "music.ffmpeg_path":           "ffmpeg",
    "music.search_prefix":         "ytsearch",
    "music.voice_health_check_interval_seconds": 15,
    "music.voice_reconnect_grace_seconds":       60,
    "music.favorites_per_page":      10,
    "music.favorites_load_all_limit": 50,

    "ticket.channel_prefix":   "ticket-",
    "ticket.category_name":    "工單",
    "ticket.archive_category": "",
    "ticket.cooldown_seconds": 300,
    "ticket.max_per_user":     1,

    "voice_channel.default_name_template": "{username} 的頻道",
    "voice_channel.default_limit":         0,
    "voice_channel.jtc_channel_id":        0,
    "voice_channel.category_id":           0,

    "guild.welcome_template": "歡迎 {user} 加入 **{guild}**！目前共有 {count} 名成員。",
    "guild.leave_template":   "**{username}** 離開了 **{guild}**",

    "moderation.default_mute_minutes": 10,
    "moderation.max_mute_minutes":     43200,
    "moderation.dm_target_on_warn":    True,
    "moderation.dm_target_on_mute":    False,

    "embed_footer.default": "Firefly Bot",
    "embed_footer.music":   "音樂系統",

    "dm.forward_map_limit":    200,
    "dm.recent_senders_limit": 200,
    "dm.owner_reply_prefix":   "**Bot 回覆：**\n",

    # ── 連結預覽 ──────────────────────
    "link_preview.enabled":                     True,
    "link_preview.max_embeds_per_message":      3,
    "link_preview.cache_size":                  200,
    "link_preview.request_timeout_seconds":     10,
    "link_preview.dead_host_cooldown_seconds":  300,
    "link_preview.embed_description_max_chars": 800,
    "link_preview.attach_video":                True,
    "link_preview.bilibili_fetch_video":        True,
    "link_preview.summary_trigger_min_chars":   60,
    "link_preview.summary_max_chars":           200,
    "link_preview.summary_input_max_chars":     4000,
    "link_preview.summary_keyword":             "摘要",
    "link_preview.summary_fetch_max_chars":     6000,
    "link_preview.instagram_proxy_hosts":       ["ddinstagram.com", "instagramez.com", "kkinstagram.com", "d.ddinstagram.com"],
    "link_preview.threads_proxy_hosts":         ["vxthreads.net", "viewthreads.com"],
    "link_preview.twitter_proxy_hosts":         ["fxtwitter.com", "vxtwitter.com"],
    "link_preview.tiktok_proxy_hosts":          ["tnktok.com", "vxtiktok.com"],
}


# ── 內部工具 ──────────────────────

def _reload_if_changed() -> dict[str, Any]:
    """
    檢查 settings.json 的 mtime，變動時重新讀取。
    若檔案不存在則回傳快取（或空字典），不拋出例外。
    """
    global _cache, _cache_mtime

    if not _SETTINGS.exists():
        if _cache_mtime != -1.0:
            logger.warning("[settings] settings.json 已不存在，使用舊快取")
        return _cache

    try:
        mtime = os.stat(_SETTINGS).st_mtime
    except OSError:
        return _cache

    if mtime == _cache_mtime:
        return _cache

    try:
        raw = json.loads(_SETTINGS.read_text(encoding="utf-8"))
        # ── 過濾註解鍵 ──────────────────────
        _cache       = {k: v for k, v in raw.items() if not k.startswith("_")}
        _cache_mtime = mtime
        logger.info("[settings] settings.json 已重新載入")
    except Exception as e:
        logger.warning("[settings] 讀取 settings.json 失敗: %s，使用舊快取", e)

    return _cache


def _deep_get(data: dict, keys: list[str]) -> Any:
    """
    遞迴取得巢狀 dict 中的值。
    任意層找不到時拋出 KeyError。
    """
    node = data
    for k in keys:
        if isinstance(node, dict):
            node = node[k]
        else:
            raise KeyError(k)
    return node


# ── 公開 API ──────────────────────

def get(path: str, default: Any = None) -> Any:
    """
    以點號路徑取得設定值。

    例：
        get("ai.cooldown_seconds")          → 3.0
        get("ai.social_tier_names")         → {"0": "陌生人", ...}
        get("music.idle_timeout_seconds")   → 180

    找不到時依序回退：
        1. settings.json 對應值
        2. _DEFAULTS 中的預設值
        3. default 參數值（預設 None）
    """
    data = _reload_if_changed()
    keys = path.split(".")

    try:
        return _deep_get(data, keys)
    except (KeyError, TypeError):
        pass

    # ── 預設值回退 ──────────────────────
    if path in _DEFAULTS:
        return _DEFAULTS[path]

    return default


def get_int(path: str, default: int = 0) -> int:
    """取得整數設定；值不存在或無法轉型時回傳 default。"""
    value = get(path, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("[settings] %s=%r 無法轉為 int，使用預設 %r", path, value, default)
        return default


def get_float(path: str, default: float = 0.0) -> float:
    """取得浮點數設定；值不存在或無法轉型時回傳 default。"""
    value = get(path, default)
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("[settings] %s=%r 無法轉為 float，使用預設 %r", path, value, default)
        return default


def get_bool(path: str, default: bool = False) -> bool:
    """取得布林設定；支援常見字串表示法。"""
    value = get(path, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    if isinstance(value, int):
        return bool(value)
    logger.warning("[settings] %s=%r 無法轉為 bool，使用預設 %r", path, value, default)
    return default


def get_str(path: str, default: str = "") -> str:
    """取得字串設定；None 會回退 default，其餘型別以 str() 轉換。"""
    value = get(path, default)
    if value is None:
        return default
    return str(value)


def get_list(path: str, default: list[Any] | None = None) -> list[Any]:
    """
    取得清單設定；值不是 list 時回退 default（避免呼叫端把字串
    誤當成字元清單疊代）。default 為 None 時視為空清單。
    """
    fallback = default if default is not None else []
    value = get(path, fallback)
    if isinstance(value, list):
        return value
    logger.warning("[settings] %s=%r 非清單格式，使用預設 %r", path, value, fallback)
    return fallback


def get_section(section: str) -> dict[str, Any]:
    """
    取得整個 section（如 "ai"、"music"）的 dict。
    回傳空 dict 而非 None。
    """
    data = _reload_if_changed()
    result = data.get(section, {})
    return result if isinstance(result, dict) else {}


def reload() -> dict[str, Any]:
    """
    強制清除快取並重新讀取 settings.json。
    供 $settings reload 指令使用。
    """
    global _cache_mtime
    _cache_mtime = -1.0
    result = _reload_if_changed()
    logger.info("[settings] 強制重新載入完成")
    return result


def all_settings() -> dict[str, Any]:
    """回傳目前所有設定的快照（供 $settings show 顯示）。"""
    return dict(_reload_if_changed())


def write_value(path: str, value) -> None:
    """
    將單一值寫入 settings.json。

    path 使用點號格式（如 "bot.status_text"）。
    寫入後使快取失效，下次 get() 會重新讀取。
    """
    global _cache_mtime

    if not _SETTINGS.exists():
        raise FileNotFoundError(f"settings.json 不存在：{_SETTINGS}")

    try:
        data = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"讀取 settings.json 失敗：{e}") from e

    keys = path.split(".")
    node = data
    for key in keys[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[keys[-1]] = value

    try:
        _SETTINGS.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        raise RuntimeError(f"寫入 settings.json 失敗：{e}") from e

    # ── 快取失效 ──────────────────────
    _cache_mtime = -1.0
    logger.info("[settings] write_value: %s = %r", path, value)
