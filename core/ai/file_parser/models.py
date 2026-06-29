"""
core/ai/file_parser/models.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（統一資料模型）：
- 所有 parser 不論處理何種格式，皆回傳 ParsedFile
- 禁止不同格式回傳不同資料結構
- error 欄位為 None 表示成功，字串表示失敗原因
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── 解析結果統一模型 ──────────────────────

@dataclass
class ParsedFile:
    """
    所有 parser 的統一回傳結構。
    呼叫端只需檢查 error 是否為 None 即可判斷成功。
    """

    # ── 基本資訊 ──────────────────────
    filename:   str            # 原始檔名（含副檔名）
    extension:  str            # 副檔名，小寫，如 ".py"
    category:   str            # 分類：text / code / document / unknown
    size_bytes: int            # 原始檔案大小（bytes）

    # ── 解析結果 ──────────────────────
    content:   str  = ""       # 提取出的純文字內容
    truncated: bool = False    # True 表示因過大而被截斷
    error:     str | None = None  # None = 成功；字串 = 失敗原因

    # ── 程式碼專用（code_parser 填充，其餘 parser 留空） ──────────────────────
    language:  str             = ""
    imports:   list[str]       = field(default_factory=list)
    classes:   list[str]       = field(default_factory=list)
    functions: list[str]       = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """快捷屬性：解析成功且有內容。"""
        return self.error is None and bool(self.content)

    def to_prompt_block(self) -> str:
        """
        組裝成可插入 prompt 的文字區塊。
        由 prompt_builder 呼叫；內容過長時應先經 summary_builder 截斷。
        """
        lines: list[str] = [f"=== 附件：{self.filename} ==="]

        if self.error:
            lines.append(f"[解析失敗：{self.error}]")
            return "\n".join(lines)

        if self.category == "code" and (
            self.imports or self.classes or self.functions
        ):
            if self.imports:
                lines.append(f"引用：{', '.join(self.imports[:10])}")
            if self.classes:
                lines.append(f"類別：{', '.join(self.classes[:10])}")
            if self.functions:
                lines.append(f"函式：{', '.join(self.functions[:10])}")
            lines.append("")

        lines.append(self.content)

        if self.truncated:
            lines.append("\n[... 內容過長，已截斷 ...]")

        return "\n".join(lines)
