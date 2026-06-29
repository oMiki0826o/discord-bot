"""
core/ai/file_parser/encoding.py

修正（抽出共用編碼工具）：
- decode_bytes() 原為 text_parser.py 內部函式，因 code_parser.py 亦需要
  相同的編碼偵測邏輯而抽出為獨立模組，避免跨模組引用私有函式
- 多層編碼偵測策略：UTF-8 → chardet（可選依賴）→ latin-1 保底，
  確保任何位元組序列都能解碼成字串，不因編碼問題拋出例外
"""

from __future__ import annotations


def decode_bytes(raw: bytes) -> str:
    """多層編碼偵測，確保不因編碼失敗而丟棄檔案。"""
    # ── 1. UTF-8（最常見）────────────────────────────────
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # ── 2. chardet 自動偵測（可選依賴）─────────────────
    try:
        import chardet
        detected = chardet.detect(raw)
        enc = detected.get("encoding") or ""
        if enc and enc.lower() not in {"utf-8", "ascii"}:
            return raw.decode(enc, errors="replace")
    except ImportError:
        pass

    # ── 3. latin-1 保底（永遠不拋例外）──────────────────
    return raw.decode("latin-1", errors="replace")
