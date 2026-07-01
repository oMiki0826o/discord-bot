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

from discord.ext import commands

from core.logging.log import LogManager

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
        success: list[str] = []
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

        msg = f"已重新載入 ```{len(success)} 個模組```"
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
