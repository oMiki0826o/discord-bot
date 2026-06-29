"""
core/system/startup_registry.py

集中管理核心模組的啟動預熱清單。
新增預熱項目只需在 REGISTRY 追加一行，不修改任何邏輯。

Modification():

- 整合 firefly-bot 新增模組（guild/ticket/vc/mod repository）
- 各 repository 的 init_tables() 在 import 時已自動呼叫
  此處 warmup 的目的是確保 import 發生在啟動階段，
  而非在第一次請求時才觸發（懶加載可能造成首次延遲）

"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from typing import Callable

from core.logging.log import LogManager

logger = LogManager().get_logger("startup_registry")


# ── 預熱項目定義 ──────────────────────

@dataclass(frozen=True)
class WarmupEntry:
    module:       str
    cleanup_attr: str = ""


# ── 預熱清單（新增只需在此追加） ──────────────────────

REGISTRY: tuple[WarmupEntry, ...] = (
    # 音樂服務
    WarmupEntry(module="core.music.service"),
    # 設定系統
    WarmupEntry(module="core.system.settings"),
    # AI 相關
    WarmupEntry(module="core.ai.search_manager", cleanup_attr="cleanup_expired"),
    # 資料庫 Repository（確保 init_tables 在啟動時執行）
    WarmupEntry(module="database.repository.user_repository"),
    WarmupEntry(module="database.repository.memory_repository"),
    WarmupEntry(module="database.repository.audit_repository"),
    WarmupEntry(module="database.repository.guild_repository"),
    WarmupEntry(module="database.repository.ticket_repository"),
    WarmupEntry(module="database.repository.mod_repository"),
    WarmupEntry(module="database.repository.vc_repository"),
    WarmupEntry(module="database.repository.favorites_repository"),
)


# ── 執行結果 ──────────────────────

@dataclass
class WarmupResult:
    module:  str
    success: bool
    elapsed: float
    error:   str = ""


# ── 單項執行 ──────────────────────

def _run_entry(entry: WarmupEntry) -> WarmupResult:
    start = time.perf_counter()
    try:
        mod = importlib.import_module(entry.module)

        if entry.cleanup_attr:
            fn: Callable | None = getattr(mod, entry.cleanup_attr, None)
            if callable(fn):
                fn()
            else:
                logger.warning(
                    "cleanup 屬性不存在或不可呼叫: %s.%s",
                    entry.module, entry.cleanup_attr,
                )

        elapsed = time.perf_counter() - start
        logger.info("預熱成功: %-50s %.3fs", entry.module, elapsed)
        return WarmupResult(module=entry.module, success=True, elapsed=elapsed)

    except Exception:
        elapsed = time.perf_counter() - start
        logger.exception("預熱失敗: %s", entry.module)
        return WarmupResult(module=entry.module, success=False, elapsed=elapsed, error="見 traceback")


# ── 批次執行 ──────────────────────

def run_warmup() -> list[WarmupResult]:
    """
    依序執行 REGISTRY 所有預熱項目。
    單一失敗不中斷整體，回傳完整結果清單。
    """
    results = [_run_entry(e) for e in REGISTRY]

    ok    = sum(r.success for r in results)
    fail  = len(results) - ok
    total = sum(r.elapsed for r in results)

    logger.info("預熱完成 | 成功=%d 失敗=%d 總耗時=%.3fs", ok, fail, total)
    return results
