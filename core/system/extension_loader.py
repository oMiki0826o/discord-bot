"""
core/system/extension_loader.py

職責：
- ExtensionLoader：Discord Cog 動態掃描與載入的封裝類別
- os.walk 掃描指定 package 目錄，找出全部 .py 檔案並轉換為模組名稱
- 支援多 package、黑名單（完整名或短名）、排除目錄
- load_all() 回傳 (loaded, errors) 供呼叫方記錄統計

Modification():

- 將原有 load_extensions() 獨立函式重構為 ExtensionLoader 類別
- 修正 bot.py 中 ExtensionLoader(bot).load_all() 的呼叫契約
- excluded 參數化（不再只讀全域 EXCLUDED_DIRS），可由呼叫方傳入
- load_all() 回傳 (list[str], list[str]) = (loaded, errors)

"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import config
from core.logging.log import LogManager

if TYPE_CHECKING:
    from discord.ext import commands

logger = LogManager().get_logger("extension_loader")

# 專案根目錄（config.py 所在位置）
_BASE_DIR = Path(config.__file__).resolve().parent


# ── 黑名單判斷 ──────────────────────

def _is_blacklisted(module: str, blacklist: frozenset[str]) -> bool:
    """支援完整模組名（cogs.ai.chat）或短名稱（chat）。"""
    return module in blacklist or module.rsplit(".", 1)[-1] in blacklist


# ── 模組掃描 ──────────────────────

def _collect_modules(package: str, excluded: frozenset[str]) -> list[str]:
    """
    os.walk 掃描 package 目錄，回傳排序後的模組名清單。
    不依賴 __init__.py 存在。
    """
    package_dir = _BASE_DIR / package
    if not package_dir.exists():
        logger.warning("找不到 package 目錄: %s", package_dir)
        return []

    modules: list[str] = []
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = [d for d in dirs if d not in excluded]
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("__"):
                continue
            module = (
                Path(root, filename)
                .relative_to(_BASE_DIR)
                .with_suffix("")
                .as_posix()
                .replace("/", ".")
            )
            modules.append(module)

    return sorted(modules)


# ── ExtensionLoader ──────────────────────

class ExtensionLoader:
    """Discord Cog 動態掃描與載入。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def load_all(
        self,
        packages:  list[str]          | None = None,
        blacklist: frozenset[str]      | None = None,
        excluded:  frozenset[str]      | None = None,
    ) -> tuple[list[str], list[str]]:
        """
        掃描所有 package 並載入 Cog。

        Returns
        -------
        (loaded, errors)
            loaded  - 成功載入的模組名清單
            errors  - 載入失敗的模組名清單
        """
        from discord.ext.commands import ExtensionAlreadyLoaded

        _packages  = packages  if packages  is not None else list(config.EXTENSION_PACKAGES)
        _blacklist = blacklist if blacklist is not None else config.EXTENSION_BLACKLIST
        _excluded  = excluded  if excluded  is not None else config.EXCLUDED_DIRS

        loaded:  list[str] = []
        errors:  list[str] = []
        skipped: list[str] = []

        for package in _packages:
            modules = _collect_modules(package, _excluded)
            logger.info("掃描 package=%s | 找到 %d 個模組", package, len(modules))

            for module in modules:
                if _is_blacklisted(module, _blacklist):
                    skipped.append(module)
                    logger.debug("黑名單跳過: %s", module)
                    continue

                try:
                    await self.bot.load_extension(module)
                    loaded.append(module)
                    logger.info("載入成功: %s", module)
                except ExtensionAlreadyLoaded:
                    skipped.append(module)
                    logger.debug("已載入，略過: %s", module)
                except Exception:
                    errors.append(module)
                    logger.exception("載入失敗: %s", module)

        logger.info(
            "Extension 載入完成 | 成功=%d 失敗=%d 跳過=%d",
            len(loaded), len(errors), len(skipped),
        )
        if errors:
            logger.warning("失敗清單: %s", errors)

        return loaded, errors

    async def reload_module(self, module: str) -> bool:
        """重新載入單一模組，回傳是否成功。"""
        try:
            await self.bot.reload_extension(module)
            logger.info("重新載入: %s", module)
            return True
        except Exception:
            logger.exception("重新載入失敗: %s", module)
            return False
