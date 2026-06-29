"""
cogs/system/load.py

修正：
- logger 改為透過 LogManager 取得，與全域 log 設定一致
- 新增 _split_names 共用函式，統一解析逗號分隔的 extension 名稱
- reload_all 失敗訊息改用 logger.exception 紀錄完整堆疊
- 所有指令補上完整型別註記
"""

from __future__ import annotations

from discord.ext import commands

from core.logging.log import LogManager

# ── logger ──────────────────────
logger = LogManager().get_logger("cogs.system.load")

# ── 動作名稱對應（中文顯示用）──────────────────────
_ACTION_LABELS: dict[str, str] = {
    "load": "載入",
    "unload": "卸載",
    "reload": "重新載入",
}


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
        module = f"cogs.{extension}"

        try:
            await actions[action](module)
            await ctx.send(f"已{label} `{extension}`")
            logger.info("%s：%s（操作者：%s）", label, module, ctx.author)

        except commands.ExtensionNotFound:
            await ctx.send(f"找不到模組：`{extension}`")
        except commands.ExtensionAlreadyLoaded:
            await ctx.send(f"`{extension}` 已經載入中")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"`{extension}` 尚未載入")
        except Exception as exc:
            await ctx.send(f"操作失敗：`{exc}`")
            logger.exception("管理指令失敗：%s", module)

    # ── 解析逗號分隔的 extension 名稱清單 ──────────────────────
    @staticmethod
    def _split_names(extensions: str) -> list[str]:
        return [name.strip() for name in extensions.split(",") if name.strip()]

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
                failed.append(f"{ext}（{exc}）")
                logger.exception("bot_reload 失敗：%s", ext)

        msg = f"已重新載入 ```{len(success)} 個模組```"
        if failed:
            msg += f"\n失敗 ```{len(failed)} 個```：\n" + "\n".join(failed)

        await ctx.send(msg)

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
