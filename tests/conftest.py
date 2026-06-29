"""
tests/conftest.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 在任何專案模組被 import 之前，設定必要的環境變數
  （config.py 在 import 時就會檢查 DISCORD_TOKEN / GEMINI_API，
  缺少任一個會直接 raise RuntimeError，導致整個測試集合失敗）
- 將專案根目錄加入 sys.path，讓測試可以用 `import core.xxx` /
  `import database.xxx`，不需要額外的 package 安裝步驟
- 在任何 repository 模組（user_repository / memory_repository /
  audit_repository / core.ai.budget）被 import 之前，搶先把
  database.ai.sqlite._DB 導向測試專用的暫存路徑。

  這一步必須在 conftest.py 的「模組層級」完成，不能放在 fixture
  函式內：這些 repository 模組的檔尾都會自動呼叫 init_tables() /
  _init()（專案既有慣例，建立資料表），一旦在 monkeypatch 之前
  就被任何測試檔 import，就會直接在開發環境的
  database/ai/memory.db 建立資料表，污染真實資料庫。
- 提供 fresh_db fixture：每個測試函式各自使用獨立的暫存 SQLite
  檔案，測試之間完全互不影響。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ── 1. 環境變數（必須在任何專案模組 import 之前設定） ──────────────────────
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API", "test-gemini-key")

# ── 2. 專案根目錄加入 sys.path ──────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── 3. 搶先把 DB 路徑導向測試專用暫存目錄 ──────────────────────
import database.ai.sqlite as sqlite_mod  # noqa: E402

_SESSION_TMP_DIR = Path(tempfile.mkdtemp(prefix="ai_bot_test_"))
sqlite_mod._DB = _SESSION_TMP_DIR / "session_memory.db"

import pytest  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """
    將 database.ai.sqlite 的連線目標導向每個測試各自的暫存檔案，
    並重新建立所有資料表（init_tables / _init 皆使用
    CREATE TABLE IF NOT EXISTS，重複呼叫安全無副作用）。

    回傳暫存資料庫路徑，多數測試不需要用到，僅供需要直接檢查
    檔案內容的測試使用。
    """
    db_path = tmp_path / "test_memory.db"
    monkeypatch.setattr(sqlite_mod, "_DB", db_path)

    import core.ai.budget as budget_mod
    import database.repository.audit_repository as audit_repo
    import database.repository.memory_repository as mem_repo
    import database.repository.user_repository as user_repo

    mem_repo.init_tables()
    user_repo.init_tables()
    audit_repo.init_tables()
    budget_mod._init()

    return db_path
