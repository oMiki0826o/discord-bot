"""
database/ai/sqlite.py

Modification():
- 修正 DB_PATH 設定被忽略的問題：原本路徑寫死為
  「向上兩層推算的專案根目錄 / database/ai/memory.db」，完全沒有
  讀取 config.py 已經解析好的 config.DB_PATH，導致使用者在 .env
  設定不同的 DB_PATH 也不會有任何效果，資料庫永遠寫在同一個位置。
  改為實際讀取 config.DB_PATH：相對路徑仍解析到專案根目錄下（沿用
  原本「不論從哪個工作目錄啟動都能找到 DB」的優點），絕對路徑則
  直接使用（pathlib 的 Path.__truediv__ 在右側已是絕對路徑時，
  會直接以右側為準，天生就同時處理好這兩種情況，不需要額外
  判斷分支）。

職責：
- 提供統一的 SQLite 連線入口
- 設定 row_factory = sqlite3.Row，使所有查詢結果可用欄位名稱存取
  （row["role"]、row["content"] 等），而非仰賴位置索引

修正：
- detect_types 啟用 PARSE_DECLTYPES，讓 TIMESTAMP 欄位自動轉換為 datetime 物件
- check_same_thread=False 允許多個 asyncio 協程在同一個執行緒使用（Discord bot 環境）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config

# ── 路徑 ──────────────────────

# 從此檔案向上兩層取得專案根目錄，供解析相對路徑時使用
_ROOT = Path(__file__).resolve().parents[2]

# 實際讀取 config.DB_PATH（對應 .env 的 DB_PATH）；相對路徑會解析到
# 專案根目錄下，絕對路徑則直接生效。
_DB = _ROOT / config.DB_PATH

# ── 公開函式 ──────────────────────

def get_connection() -> sqlite3.Connection:
    """
    回傳已設定好 row_factory 的 SQLite 連線。

    row_factory = sqlite3.Row：
        查詢結果可用欄位名稱存取，例如 row["role"]，
        比 row[0] 更安全，SELECT 欄位順序改變時不會靜默出錯。

    使用完畢需由呼叫方負責呼叫 conn.close()。
    每次呼叫都建立新連線，避免跨協程共享連線造成 thread-safety 問題。
    """
    _DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        _DB,
        detect_types   = sqlite3.PARSE_DECLTYPES,
        check_same_thread = False,
    )
    conn.row_factory = sqlite3.Row
    return conn
