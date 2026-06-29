"""
core/logging/constants.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正：
- 集中管理 log 路徑、格式、檔案大小限制與 traceback 分段大小
- log 檔名依啟動時間自動產生，避免覆蓋舊紀錄
"""

from __future__ import annotations

from datetime import datetime

# ── 啟動時間戳記（用於 log 目錄與檔名） ──────────────────────
_now = datetime.now()
_date_str = _now.strftime("%Y-%m-%d")
_time_str = _now.strftime("%H-%M-%S")

# ── log 路徑設定 ──────────────────────
LOG_BASE_DIR = "database/logs"
LOG_DIR = f"{LOG_BASE_DIR}/{_date_str}"
LOG_FILE = f"{LOG_DIR}/logs_{_date_str}_{_time_str}.log"

# ── log 行為設定 ──────────────────────
LOG_MAX_BYTES = 50 * 1024 * 1024  # 50MB
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
TRACEBACK_CHUNK_SIZE = 1900
