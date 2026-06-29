"""
core/system/settings.py

職責：
- 載入並快取 settings.json（專案根目錄）
- 基於 mtime 的熱重載：每次存取時用一次 os.stat() 判斷是否需重讀
  （成本極低，檔案不變動時等同記憶體存取）
- 提供型別安全的便捷存取函式：get() 支援點號路徑（如 "ai.cooldown_seconds"）
- 機密設定（TOKEN / API Key）不在此，仍由 .env + config.py 管理

Modification():

- 全新建立，作為 firefly-bot 統一設定系統的核心
- 設計參考 core/ai/content_guard.py 的 mtime 快取模式
- 所有模組統一從此讀設定，不再各自散落 hardcode 預設值
- reload() 供管理員指令（$settings reload）強制刷新

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

# ── 預設值（settings.json 缺欄時的回退）──────────────────────

_DEFAULTS: dict[str, Any] = {
    "bot.command_prefix":      "$",
    "bot.status_type":         "listening",
    "bot.status_text":         "/play",
    "bot.presence":            "online",
    "bot.startup_timeout":     60,

    "ai.default_model":        "gemma",
    "ai.cooldown_seconds":     3.0,
    "ai.max_reply_length":     1500,
    "ai.persona_name":         "流螢",
    "ai.social_tier_names":    {"0":"陌生人","1":"路人","2":"朋友","3":"開拓者"},
    "ai.conversation_state_labels": {
        "normal":"一般對話","roleplay":"角色扮演",
        "creative":"創意寫作","task":"任務模式","debate":"辯論模式",
    },
    "ai.summary_trigger":      40,
    "ai.summary_keep":         10,
    "ai.memory_cache_ttl":     5.0,
    "ai.abuse_window_seconds": 60,
    "ai.abuse_max_requests":   15,
    "ai.abuse_restrict_minutes": 10,
    "ai.search_short_ttl_min": 30,
    "ai.search_long_ttl_min":  1440,
    "ai.search_fuzzy_threshold": 0.85,
    "ai.alert_error_rate_threshold": 0.15,
    "ai.alert_check_interval_seconds": 300,

    "music.max_queue_size":        200,
    "music.idle_timeout_seconds":  180,
    "music.default_volume_percent": 50,
    "music.ffmpeg_path":           "ffmpeg",

    "ticket.channel_prefix":   "ticket-",
    "ticket.category_name":    "工單",
    "ticket.archive_category": "",
    "ticket.cooldown_seconds": 300,
    "ticket.max_per_user":     1,

    "voice_channel.default_name_template": "{username} 的頻道",
    "voice_channel.default_limit":         0,

    "guild.welcome_template": "歡迎 {user} 加入 **{guild}**！目前共有 {count} 名成員。",
    "guild.leave_template":   "**{username}** 離開了 **{guild}**",

    "moderation.default_mute_minutes": 10,
    "moderation.max_mute_minutes":     43200,
    "moderation.dm_target_on_warn":    True,
    "moderation.dm_target_on_mute":    False,

    "embed_footer.default": "Firefly Bot",
    "embed_footer.music":   "音樂系統",
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
        # 過濾 _comment 鍵
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

    # 回退到 _DEFAULTS
    if path in _DEFAULTS:
        return _DEFAULTS[path]

    return default


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

    # 使快取失效，下次 get() 重新載入
    _cache_mtime = -1.0
    logger.info("[settings] write_value: %s = %r", path, value)
