"""
cogs/system/load.py

Modification():

- Extension 名稱正規化，支援 chat、ai.chat、cogs.ai.chat 與 cogs/ai/chat.py。
- logger 透過 LogManager 取得，與全域 log 設定一致。
- _split_names 共用解析逗號分隔的 extension 名稱。
- reload_all 失敗訊息使用 logger.exception 紀錄完整堆疊。

- 修正 bot_reload／_handle 的訊息可能超過 Discord 2000 字元訊息上限：
  原本將所有失敗模組的例外訊息直接 join 成單一字串送出，當多個模組
  同時失敗（例如重載時某個共用模組剛好有語法錯誤，連帶影響一票
  import 它的模組）很容易超過上限，導致 ctx.send() 本身又拋出例外。
  改為依長度切分為多則訊息發送；單一例外字串本身也加上長度截斷。

Description():

- 本檔提供 Owner 專用的 Cog 載入、卸載、重載與關閉指令。
"""

from __future__ import annotations

import importlib

from discord.ext import commands

import config
from core.logging.log import LogManager
from core.system.extension_loader import _collect_modules, _is_blacklisted

# ── logger ──────────────────────
logger = LogManager().get_logger("cogs.system.load")

# ── 動作名稱對應（中文顯示用） ──────────────────────
_ACTION_LABELS: dict[str, str] = {
    "load": "載入",
    "unload": "卸載",
    "reload": "重新載入",
}

# Discord 訊息內容上限為 2000；留緩衝避免邊界誤差
_MESSAGE_LIMIT: int = 1900

# Cog 會以 ``from ... import ...`` 取得這些無狀態共用工具；Cog 重載前必須
# 先刷新它們，否則 sys.modules 仍保留舊版 API，會造成連鎖 ImportError。
_SHARED_RELOAD_MODULES: tuple[str, ...] = (
    "utils.confirmation",
)


def _reload_shared_dependencies() -> list[str]:
    """依序匯入或重載可安全刷新、且不保存長期狀態的共用模組。"""
    reloaded: list[str] = []
    for module_name in _SHARED_RELOAD_MODULES:
        module = importlib.import_module(module_name)
        importlib.reload(module)
        reloaded.append(module_name)
    return reloaded


def _music_core_restart_reasons() -> list[str]:
    """檢查目前 process 中的音樂核心 API 是否與新 Cog 相容。

    Cog 可以透過 discord.py 熱重載，但已匯入的 core 模組仍會留在
    sys.modules。若 Cog 已改用新核心 API，重載後會形成新舊版本混用，
    此時必須完整重啟 Bot。
    """
    reasons: list[str] = []

    requirements = {
        "core.music.url": ("is_youtube_url",),
        "core.music.queue": (
            "QueueFullError",
            "MusicQueue",
        ),
        "core.music.views": ("require_player_control",),
        "core.music.song": ("Song",),
        "core.music.player": ("GuildPlayer",),
    }

    modules: dict[str, object] = {}
    for module_name, attributes in requirements.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            reasons.append(f"{module_name} 無法匯入：{exc}")
            continue

        modules[module_name] = module
        for attribute in attributes:
            if not hasattr(module, attribute):
                reasons.append(f"{module_name}.{attribute} 尚未載入")

    queue_module = modules.get("core.music.queue")
    queue_type = getattr(queue_module, "MusicQueue", None)
    for attribute in ("max_size", "index_of_id", "remove_by_id", "move_by_id"):
        if queue_type is not None and not hasattr(queue_type, attribute):
            reasons.append(f"core.music.queue.MusicQueue.{attribute} 尚未載入")

    song_module = modules.get("core.music.song")
    song_type = getattr(song_module, "Song", None)
    song_fields = getattr(song_type, "__dataclass_fields__", {})
    if song_type is not None and "queue_id" not in song_fields:
        reasons.append("core.music.song.Song.queue_id 尚未載入")

    player_module = modules.get("core.music.player")
    player_type = getattr(player_module, "GuildPlayer", None)
    add_playlist = getattr(player_type, "add_playlist", None)
    code_names = getattr(getattr(add_playlist, "__code__", None), "co_names", ())
    if player_type is not None and "QueueFullError" not in code_names:
        reasons.append("core.music.player.GuildPlayer.add_playlist 仍為舊版本")

    return reasons


def _core_import_error(exc: BaseException) -> str | None:
    """從 extension 包裝例外鏈中找出 core 模組的 ImportError。"""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ImportError) and "core." in str(current):
            return str(current)
        current = (
            current.__cause__
            or current.__context__
            or getattr(current, "original", None)
        )
    return None


async def _send_chunked(ctx: commands.Context, text: str) -> None:
    """依 _MESSAGE_LIMIT 將長文字切分為多則訊息依序發送，避免超過 Discord 2000 字元上限。"""
    for i in range(0, len(text), _MESSAGE_LIMIT):
        await ctx.send(text[i : i + _MESSAGE_LIMIT])


# ── extension 名稱正規化 ──────────────────────

def _normalize_extension_name(extension: str) -> str:
    """將使用者輸入轉為 discord.py load_extension() 需要的完整模組名。"""
    name = extension.strip().removesuffix(".py").replace("/", ".").strip(".")
    if name.startswith("cogs."):
        return name
    return f"cogs.{name}"


# ── extension 載入 / 卸載 / 重載管理 ──────────────────────
class Load(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 共用處理函式 ──────────────────────
    async def _handle(self, ctx: commands.Context, action: str, extension: str) -> None:
        actions = {
            "load": self.bot.load_extension,
            "unload": self.bot.unload_extension,
            "reload": self.bot.reload_extension,
        }
        label = _ACTION_LABELS[action]
        module = _normalize_extension_name(extension)

        try:
            if action in {"load", "reload"}:
                _reload_shared_dependencies()
            await actions[action](module)
            await ctx.send(f"已{label} `{module}`")
            logger.info("%s：%s（操作者：%s）", label, module, ctx.author)

        except commands.ExtensionNotFound:
            await ctx.send(f"找不到模組：`{extension}`")
        except commands.ExtensionAlreadyLoaded:
            await ctx.send(f"`{extension}` 已經載入中")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"`{extension}` 尚未載入")
        except Exception as exc:
            core_error = _core_import_error(exc)
            if core_error:
                await ctx.send(
                    "操作失敗：Cog 需要新版 core API，無法安全熱重載。"
                    "請完整重啟 Bot 後再試。\n"
                    f"`{core_error[:300]}`"
                )
                logger.warning("核心模組版本不相容，需重啟：%s", module)
                return
            text = str(exc)
            if len(text) > 300:
                text = text[:300] + "..."
            await ctx.send(f"操作失敗：`{text}`")
            logger.exception("管理指令失敗：%s", module)

    # ── 解析逗號分隔的 extension 名稱清單 ──────────────────────
    @staticmethod
    def _split_names(extensions: str) -> list[str]:
        return [_normalize_extension_name(name) for name in extensions.split(",") if name.strip()]

    # ── 載入指令 ──────────────────────
    @commands.command(name="load", hidden=True)
    @commands.is_owner()
    async def load(self, ctx: commands.Context, *, extensions: str) -> None:
        for extension in self._split_names(extensions):
            await self._handle(ctx, "load", extension)

    # ── 卸載指令 ──────────────────────
    @commands.command(name="unload", hidden=True)
    @commands.is_owner()
    async def unload(self, ctx: commands.Context, *, extensions: str) -> None:
        for extension in self._split_names(extensions):
            await self._handle(ctx, "unload", extension)

    # ── 重載單一模組指令 ──────────────────────
    @commands.command(name="reload", hidden=True)
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, *, extensions: str) -> None:
        for extension in self._split_names(extensions):
            await self._handle(ctx, "reload", extension)

    # ── 重載全部模組 ──────────────────────
    @commands.command(name="bot_reload", hidden=True)
    @commands.is_owner()
    async def reload_all(self, ctx: commands.Context) -> None:
        restart_reasons = _music_core_restart_reasons()
        if restart_reasons:
            detail = "\n".join(f"- {reason}" for reason in restart_reasons)
            await _send_chunked(
                ctx,
                "偵測到執行中的音樂 core 與目前 Cog 版本不一致，"
                "已取消熱重載以避免新舊物件混用。\n"
                "請完整重啟 Bot。\n"
                f"{detail}",
            )
            logger.warning("bot_reload 取消：需重啟 | %s", restart_reasons)
            return

        try:
            shared_reloaded = _reload_shared_dependencies()
        except Exception as exc:
            logger.exception("bot_reload 取消：共用依賴重載失敗")
            await ctx.send(
                "共用依賴重載失敗，已取消 Cog 重載以避免大量連鎖錯誤："
                f"`{type(exc).__name__}: {str(exc)[:300]}`"
            )
            return

        success: list[str] = []
        loaded_new: list[str] = []
        failed: list[str] = []

        for ext in list(self.bot.extensions.keys()):
            try:
                await self.bot.reload_extension(ext)
                success.append(ext)
            except Exception as exc:
                detail = str(exc)
                if len(detail) > 200:
                    detail = detail[:200] + "..."
                failed.append(f"{ext}（{detail}）")
                logger.exception("bot_reload 失敗：%s", ext)

        known = set(self.bot.extensions.keys())
        for package in config.EXTENSION_PACKAGES:
            for ext in _collect_modules(package, config.EXCLUDED_DIRS):
                if ext in known or _is_blacklisted(ext, config.EXTENSION_BLACKLIST):
                    continue
                try:
                    await self.bot.load_extension(ext)
                    loaded_new.append(ext)
                    known.add(ext)
                    logger.info("bot_reload 補載新模組：%s", ext)
                except Exception as exc:
                    detail = str(exc)
                    if len(detail) > 200:
                        detail = detail[:200] + "..."
                    failed.append(f"{ext}（{detail}）")
                    logger.exception("bot_reload 補載失敗：%s", ext)

        msg = f"已重新載入 ```{len(success)} 個模組```"
        if shared_reloaded:
            msg += "\n已先重載共用依賴：" + ", ".join(f"`{name}`" for name in shared_reloaded)
        if loaded_new:
            msg += f"\n已補載新模組 ```{len(loaded_new)} 個```：\n" + "\n".join(loaded_new)
        if failed:
            msg += f"\n失敗 ```{len(failed)} 個```：\n" + "\n".join(failed)

        await _send_chunked(ctx, msg)

    # ── 關閉 Bot ──────────────────────
    @commands.command(name="bot_stop", hidden=True)
    @commands.is_owner()
    async def stop(self, ctx: commands.Context) -> None:
        await ctx.send("Bot 正在關閉...")
        logger.info("Bot 被 %s 手動關閉", ctx.author)
        await self.bot.close()


# ── extension 進入點 ──────────────────────
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Load(bot))
