"""
core/logging/constants.py

Modification():
- 修正 LOG_MAX_BYTES：原本設定 50MB，實測發現這個門檻遠高於 Discord
  實際對 Bot DM 上傳的限制。關機報告會把整份 log 私訊給 Owner
  （見 discord_error_handler.py 的 send_shutdown_report()），先用
  LOG_MAX_BYTES 判斷「log 是否小到可以附加」；當時的 log 檔案是
  16.7MB，通過了 50MB 這道自訂門檻，實際呼叫 Discord API 卻收到
  413 Payload Too Large（error code 40005），代表這道門檻形同虛設，
  完全沒有真正防到它原本該防的狀況。查證 Discord 目前對 Bot 上傳
  的實際限制是 8MB（一般使用者的免費額度雖然是 10MB，但 Bot 走的
  API 端點限制更低），改為 7MB，留一點安全餘裕，讓這道自訂門檻
  真正能在送出前擋下太大的檔案，而不是等 Discord 拒絕才知道。

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
LOG_MAX_BYTES = 7 * 1024 * 1024  # 7MB（Discord Bot 上傳實際限制約 8MB，留安全餘裕）
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
TRACEBACK_CHUNK_SIZE = 1900

# ── log 檔案輪替設定 ──────────────────────
# 單一 log 檔案雖然已依啟動時間分檔（見上方 LOG_FILE），但若 Bot
# 長時間不重啟持續運作，單一 session 的 log 檔案仍可能無限成長。
# 加上以大小為準的輪替：超過 LOG_ROTATE_MAX_BYTES 就切到新檔案，
# 最多保留 LOG_ROTATE_BACKUP_COUNT 份輪替後的舊檔，避免長時間運作
# 下 log 檔案無上限地佔用磁碟空間。
LOG_ROTATE_MAX_BYTES = 20 * 1024 * 1024  # 20MB
LOG_ROTATE_BACKUP_COUNT = 5
