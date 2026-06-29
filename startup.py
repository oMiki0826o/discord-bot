"""
startup.py

職責：
- 同步初始化流程（可選的獨立啟動步驟）
- 僅負責 extension package 預載，確保模組可被掃描
- 不重複執行 run_warmup()（由 bot.py 的 setup_hook 負責）

Modification():

- 移除 _warmup_core()（原先與 bot.py setup_hook 雙重執行）
- 只保留 _preload_packages()：確保 os.walk 掃描前 package 已初始化

"""

from __future__ import annotations

import importlib
import time

from config import EXTENSION_PACKAGES
from core.logging.log import LogManager

logger = LogManager().get_logger("startup")


def _preload_packages() -> None:
    """
    嘗試 import 每個 extension package 根目錄。
    無 __init__.py 時 ModuleNotFoundError 是正常情況（os.walk 不依賴它）。
    """
    start = time.perf_counter()
    for pkg in EXTENSION_PACKAGES:
        try:
            importlib.import_module(pkg)
            logger.debug("package 預載: %s", pkg)
        except ModuleNotFoundError:
            logger.debug("package 無 __init__.py（正常，os.walk 仍可掃描）: %s", pkg)
        except Exception:
            logger.exception("package 預載例外: %s", pkg)
    logger.info("package 預載完成 %.3fs", time.perf_counter() - start)


def initialize() -> None:
    """同步初始化入口，在 asyncio 前執行。"""
    logger.info("同步初始化開始")
    _preload_packages()
    logger.info("同步初始化完成")
