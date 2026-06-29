"""
config.py

職責：
- 僅管理機密性環境變數（TOKEN、API Key）與路徑設定
- 所有可客製化的非機密設定已移至 settings.json
- 啟動時驗證必要變數，型別轉換後即不再改變

Modification():

- 精簡版：移除所有已遷移至 settings.json 的 tunables
- 保留：TOKEN、GEMINI_API、OWNER_ID、DB 路徑、EXTENSION 設定

"""

from __future__ import annotations

import os
from typing import TypeVar

from dotenv import load_dotenv

load_dotenv()

_T = TypeVar("_T")


# ── Env 工具 ──────────────────────

def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise RuntimeError(f"必要環境變數未設定: {key}")
    return value


def _optional(key: str, default: _T) -> str | _T:
    value = os.getenv(key, "").strip()
    return value if value else default


def _tuple_from_env(key: str, default: str = "") -> tuple[str, ...]:
    raw = _optional(key, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _frozenset_from_env(key: str, default: str = "") -> frozenset[str]:
    raw = _optional(key, default)
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


# ── Discord ──────────────────────

TOKEN: str    = _require("DISCORD_TOKEN")
OWNER_ID: int = int(_optional("OWNER_ID", "0"))

# ── Google AI ──────────────────────

GEMINI_API: str = _optional("GEMINI_API", "")

# ── Extension Loader ──────────────────────

EXTENSION_PACKAGES: tuple[str, ...] = _tuple_from_env("EXTENSION_PACKAGES", "cogs")
EXTENSION_BLACKLIST: frozenset[str] = _frozenset_from_env("EXTENSION_BLACKLIST")
EXCLUDED_DIRS: frozenset[str]       = _frozenset_from_env(
    "EXCLUDED_DIRS", "__pycache__,venv,.venv",
)

# ── 資料庫路徑 ──────────────────────

DB_PATH: str = _optional("DB_PATH", "database/ai/memory.db")
