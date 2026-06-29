"""
core/music/service.py

職責：
- PlayerManager 管理所有 Guild 的 GuildPlayer 實例
- 保證每個 Guild 只存在一個播放器（避免重複連線）
- 提供模組層級的 get_player() / remove_player() 便捷函式

Modification():

- 移植自 music_bot/services/music_service.py
- 路徑調整，其餘邏輯不變

"""

from __future__ import annotations

import discord

from core.music.player import GuildPlayer


class PlayerManager:
    """全域播放器管理員，維護 guild_id -> GuildPlayer 的對應表。"""

    def __init__(self) -> None:
        self._players: dict[int, GuildPlayer] = {}

    def get(self, bot: discord.Client, guild: discord.Guild) -> GuildPlayer:
        """取得播放器，不存在時建立新實例。"""
        if guild.id not in self._players:
            self._players[guild.id] = GuildPlayer(bot, guild)
        return self._players[guild.id]

    def remove(self, guild_id: int) -> None:
        """移除播放器實例（Bot 離開伺服器時呼叫）。"""
        self._players.pop(guild_id, None)

    def all_players(self) -> dict[int, GuildPlayer]:
        """回傳所有播放器快照（僅供除錯）。"""
        return dict(self._players)

    def active_count(self) -> int:
        """回傳目前有在播放的伺服器數量。"""
        return sum(1 for p in self._players.values() if p.is_active)


# ── 全域單例 ──────────────────────

_manager = PlayerManager()


def get_player(bot: discord.Client, guild: discord.Guild) -> GuildPlayer:
    return _manager.get(bot, guild)


def remove_player(guild_id: int) -> None:
    _manager.remove(guild_id)


def get_manager() -> PlayerManager:
    """取得 PlayerManager 單例（供 /status 指令使用）。"""
    return _manager
