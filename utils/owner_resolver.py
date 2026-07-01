"""
utils/owner_resolver.py

職責：
- 提供唯一、可靠的 Bot Owner 使用者 ID 解析入口
- 正確處理「應用程式由 Discord Team 擁有」的情境

Modification():

- 新增此檔案：修正私訊轉發（cogs/events/message.py）等功能
  各自呼叫 application_info().owner.id 取得 Owner 的問題。
  當應用程式由 Team 擁有時，.owner 不一定對應到實際可私訊的
  個別成員（可能是 Team 的代表帳號），導致 DM 轉發解析到
  錯誤對象、send() 失敗，且失敗過程只留下一行 log，
  外部看起來就像「完全沒有轉發」。
- 改為優先信任明確設定的 config.OWNER_ID；
  若未設定，借助 commands.Bot.is_owner() 內建邏輯
  （discord.py 已正確處理 Team／個人帳號兩種情況）觸發並快取
  bot.owner_id / bot.owner_ids，再從中取出一個穩定的 ID，
  不需要自行猜測 discord.Team 的內部屬性名稱。

"""

from __future__ import annotations

import discord
from discord.ext import commands

import config


async def resolve_owner_id(bot: commands.Bot) -> int | None:
    """
    解析 Bot Owner 的使用者 ID。

    優先順序：
        1. config.OWNER_ID（環境變數明確設定，最權威）
        2. bot.owner_id（discord.py 已快取的單一擁有者）
        3. bot.owner_ids（Team 應用程式，取最小 ID 確保結果穩定）
        4. 觸發 bot.is_owner() 強制解析後重試 2、3

    任一管道都取不到時回傳 None，呼叫端應自行決定如何處理
    （例如記錄警告、停用依賴此 ID 的功能）。
    """
    # ── 最高優先：明確設定的環境變數 ──────────────────────
    if config.OWNER_ID:
        return config.OWNER_ID

    # ── 已有快取結果，直接使用 ──────────────────────
    if bot.owner_id:
        return bot.owner_id
    if bot.owner_ids:
        return min(bot.owner_ids)

    # ── 尚未解析過：借助內建邏輯觸發一次（正確處理 Team） ──────────────────────
    if bot.user is not None:
        try:
            await bot.is_owner(bot.user)
        except discord.HTTPException:
            return None

    if bot.owner_id:
        return bot.owner_id
    if bot.owner_ids:
        return min(bot.owner_ids)

    return None
