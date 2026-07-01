"""
utils/discord_errors.py

職責：
- 將常見 Discord HTTPException 錯誤代碼轉換為人類可讀的繁體中文訊息
- 集中管理「錯誤代碼 → 訊息」對照表，避免各 Cog 重複撰寫相同判斷

Modification():

- 新增此檔案：原本 50007（無法私訊使用者）等常見錯誤，
  在 cogs/system/owner.py 等多處直接顯示 raw exception 字串
  （例如 `400 Bad Request (error code: 50007): Cannot send...`），
  使用者難以理解；且新增已知錯誤代碼的處理時需要修改多個檔案。
- 改為集中於此模組維護對照表，呼叫端只需呼叫 friendly_http_error()，
  未來新增已知錯誤代碼時只需在此檔案新增一筆，不影響任何呼叫端。

"""

from __future__ import annotations

import discord

# ── Discord 錯誤代碼對照表 ──────────────────────
# 完整代碼列表參見 Discord 官方文件「JSON Error Codes」。
# 新增已知錯誤代碼時，於此處新增一筆即可，呼叫端無需變動。

_ERROR_MESSAGES: dict[int, str] = {
    10003: "找不到指定的頻道",
    10013: "找不到指定的使用者",
    50001: "Bot 缺少存取該資源的權限",
    50007: "對方已關閉私訊，或與 Bot 沒有共同的伺服器，無法傳送訊息",
    50013: "Bot 缺少執行此操作所需的權限",
    50035: "傳送內容格式不符合 Discord 限制（欄位長度或數量超出上限）",
}


def friendly_http_error(exc: discord.HTTPException) -> str:
    """
    將 discord.HTTPException 轉換為人類可讀的繁體中文錯誤訊息。

    優先查表比對 exc.code（Discord 自訂錯誤代碼，與 HTTP 狀態碼無關，
    50007 可能包裝在 400 或 403 底下，查 code 比判斷狀態碼更可靠）；
    查無對照時退回顯示原始例外內容，確保任何錯誤都至少有可讀輸出。
    """
    code = getattr(exc, "code", None)
    return _ERROR_MESSAGES.get(code, str(exc))
