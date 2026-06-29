"""
utils/helpers.py

職責：
- 通用輔助函式（例外格式化、安全轉型等）

Modification():

- 移植自 Bot-Firefly/utils/helpers.py
- 新增 safe_int(), safe_float() 供各模組使用

"""

from __future__ import annotations

import traceback


def format_exception(error: Exception) -> str:
    """將例外格式化為完整 traceback 字串。"""
    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )


def safe_int(value: object, default: int = 0) -> int:
    """安全整數轉換，失敗回傳 default。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def safe_float(value: object, default: float = 0.0) -> float:
    """安全浮點數轉換，失敗回傳 default。"""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
