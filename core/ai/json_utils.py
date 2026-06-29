"""
core/ai/json_utils.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 提供 Gemini 回應中常見的 ```json ... ``` 包裝清理工具
- 供 memory_manager（記憶擷取 / 摘要）與 user_context（profile 分析）共用

修正（新增此檔案，解決 fence 清理重複與錯誤實作問題）：
- user_context.py 原使用：
    raw.lstrip("```json").lstrip("```").rstrip("```").strip()
  lstrip / rstrip 是「移除集合中出現的字元」而非「移除前綴字串」，
  例如字串開頭若恰好是 "j"、"s"、"o"、"n" 等字元，會被誤刪，
  導致 JSON 內容被破壞、json.loads 失敗或解析出錯誤資料。
- memory_manager.py 已自行定義 _FENCE_RE 並以 re.sub 正確處理，
  但與 user_context.py 的清理邏輯重複定義
- 統一改為本檔案的 strip_json_fence()，以正則表達式正確移除
  開頭的 ```json / ``` 與結尾的 ```，僅移除「邊界」而非任意字元

修正（單元測試發現：fence 前後有額外空白時無法正確移除）：
- _FENCE_RE 以 "^"（行首）錨定開頭 fence，若輸入字串在
  ```json 前還有空白字元（例如 "   ```json\\n..."），
  ```json 就不在行首，導致正則完全不匹配、fence 沒被移除。
- 修正為先對輸入做一次 strip()，確保 fence 真正落在字串（與行）
  的開頭/結尾後，再套用正則表達式
"""

from __future__ import annotations

import re

# ── 區塊樣式 ──────────────────────
# 開頭：```json 或 ``` ；結尾：```
# re.MULTILINE 讓 ^ / $ 對應每一行，避免內容中段被誤判
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# ── 公開函式 ──────────────────────

def strip_json_fence(text: str) -> str:
    """
    移除 Gemini 回應中常見的 Markdown code fence 包裝。

    例如：
        ```json
        {"a": 1}
        ```
    → '{"a": 1}'

    僅移除開頭 / 結尾的 fence 標記，不影響內容本身。
    """
    return _FENCE_RE.sub("", text.strip()).strip()
