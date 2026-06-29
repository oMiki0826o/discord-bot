"""
core/ai/file_parser/metadata_builder.py

修正（AI 分析前置資訊）：
- AI 分析前必須建立 Metadata，避免 AI 缺乏檔案背景資訊
- 接收 ParsedFile 列表，彙整成可插入 prompt 的背景文字區塊
- 本模組已可使用，無需額外套件；設計為 prompt_builder 的可選補充輸入
- 不做任何 AI 呼叫，不操作 Discord，只負責彙整現有解析結果的統計資訊

呼叫位置（未來整合）：
- prompt_builder.build() 可在附件區塊前呼叫 build_metadata() 加入總覽
- context_manager.build() 在有 files 時自動加入 metadata block
"""

from __future__ import annotations

from collections import Counter
from core.ai.file_parser.models import ParsedFile


def build_metadata(files: list[ParsedFile]) -> str:
    """
    從 ParsedFile 列表彙整出 AI 分析的背景資訊區塊。
    若清單為空回傳空字串，prompt_builder 直接跳過。

    輸出範例：
        === 附件概覽（共 3 個）===
        成功解析：2   失敗：1
        類型分布：code×2, document×1
        語言分布：Python×1, TypeScript×1
        總文字量：約 1,200 字
    """
    if not files:
        return ""

    total   = len(files)
    ok_cnt  = sum(1 for f in files if f.error is None)
    err_cnt = total - ok_cnt

    # ── 類型分布 ──────────────────────────────────────────
    cat_counter: Counter[str] = Counter(f.category for f in files)
    cat_str = "  ".join(f"{c}×{n}" for c, n in cat_counter.most_common())

    # ── 語言分布（code 類才有 language）───────────────────
    lang_counter: Counter[str] = Counter(
        f.language for f in files if f.category == "code" and f.language
    )
    lang_str = (
        "  ".join(f"{l}×{n}" for l, n in lang_counter.most_common())
        if lang_counter else ""
    )

    # ── 總文字量 ──────────────────────────────────────────
    total_chars = sum(len(f.content) for f in files if f.error is None)

    # ── 失敗清單 ──────────────────────────────────────────
    errors = [f"  - {f.filename}：{f.error}" for f in files if f.error]

    lines = [f"=== 附件概覽（共 {total} 個）==="]

    if err_cnt:
        lines.append(f"成功解析：{ok_cnt}   失敗：{err_cnt}")
    else:
        lines.append(f"成功解析：{ok_cnt}")

    lines.append(f"類型分布：{cat_str}")

    if lang_str:
        lines.append(f"語言分布：{lang_str}")

    if total_chars:
        lines.append(f"總文字量：約 {total_chars:,} 字")

    if errors:
        lines.append("解析失敗項目：")
        lines.extend(errors)

    return "\n".join(lines)
