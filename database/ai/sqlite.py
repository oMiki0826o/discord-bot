"""
database/ai/sqlite.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 提供統一的 SQLite 連線入口
- 設定 row_factory = sqlite3.Row，使所有查詢結果可用欄位名稱存取
  （row["role"]、row["content"] 等），而非仰賴位置索引

修正：
- 路徑由 __file__ 向上推算至專案根目錄，確保在任何工作目錄下都能正確找到 DB
- detect_types 啟用 PARSE_DECLTYPES，讓 TIMESTAMP 欄位自動轉換為 datetime 物件
- check_same_thread=False 允許多個 asyncio 協程在同一個執行緒使用（Discord bot 環境）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# ── 路徑 ──────────────────────

# 從此檔案向上兩層取得專案根目錄
# database/ai/sqlite.py → database/ai → database → 根目錄
_ROOT = Path(__file__).resolve().parents[2]
_DB   = _ROOT / "database" / "ai" / "memory.db"

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
