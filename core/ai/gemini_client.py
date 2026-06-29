"""
core/ai/gemini_client.py

職責：
- 提供全專案共用的單一 google.genai.Client 實例

修正（新增此檔案，解決 Client 重複建立問題）：
- 原本 core.ai.core / core.ai.user_context / core.ai.memory_manager
  各自執行 genai.Client(api_key=GEMINI_API)，建立 3 個獨立實例
- 改為集中於此建立一次，三個模組改為 import 此 client
- 好處：
    1. 未來若需調整 Client 設定（timeout、http_options 等），
       只需修改此檔案一處
    2. 避免重複初始化造成的連線資源浪費
"""

from __future__ import annotations

from google import genai

from config import GEMINI_API

# ── 全域共用 Client ──────────────────────

client = genai.Client(api_key=GEMINI_API)
