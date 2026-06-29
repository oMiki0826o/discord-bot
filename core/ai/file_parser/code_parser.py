"""
core/ai/file_parser/code_parser.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（程式碼解析）：
- 支援所有 CODE_EXTENSIONS 副檔名
- 提取 import / class / function 結構（Python 用 ast；其他用 regex）
- 結構資訊填入 ParsedFile 的 imports / classes / functions 欄位
- 原始碼超長時截斷，但結構摘要永遠保留
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.summary_builder import truncate
from core.ai.file_parser.encoding import decode_bytes

logger = logging.getLogger("bot.file_parser.code")

# ── 副檔名 → 語言名稱對應 ──────────────────────

_EXT_LANG: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "React JSX", ".tsx": "React TSX",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
    ".c": "C", ".cpp": "C++", ".cc": "C++", ".h": "C/C++ Header",
    ".hpp": "C++ Header", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".sh": "Shell",
    ".bash": "Bash", ".zsh": "Zsh", ".fish": "Fish",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".r": "R", ".m": "MATLAB/Objective-C",
    ".lua": "Lua", ".dart": "Dart",
}

# ── 主要入口 ──────────────────────

def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """解析程式碼檔案，回傳含結構資訊的 ParsedFile。"""
    ext  = path.suffix.lower()
    lang = _EXT_LANG.get(ext, ext.lstrip(".").upper())

    try:
        raw    = path.read_bytes()
        source = decode_bytes(raw)
    except Exception as e:
        return ParsedFile(
            filename=filename, extension=ext,
            category="code", size_bytes=size_bytes,
            language=lang, error=str(e),
        )

    # ── 結構提取（失敗不中斷） ──────────────────────
    imports, classes, functions = [], [], []
    try:
        if ext == ".py":
            imports, classes, functions = _extract_python(source)
        else:
            imports, classes, functions = _extract_generic(source, ext)
    except Exception as e:
        logger.debug("[code_parser] structure extract error file=%s: %s", filename, e)

    content, truncated = truncate(source)
    return ParsedFile(
        filename=filename, extension=ext,
        category="code", size_bytes=size_bytes,
        language=lang,
        imports=imports, classes=classes, functions=functions,
        content=content, truncated=truncated,
    )


# ── Python：使用 ast 精確提取 ──────────────────────

def _extract_python(
    source: str,
) -> tuple[list[str], list[str], list[str]]:
    """以 ast 解析 Python，失敗時拋出讓 caller 降級到 regex。"""
    import ast
    tree      = ast.parse(source)
    imports:   list[str] = []
    classes:   list[str] = []
    functions: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append(f"{mod}.{alias.name}" if mod else alias.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 只收頂層函式（parent 為 Module）
            functions.append(node.name)

    # 去重但保留順序
    seen: set[str] = set()
    def dedup(lst: list[str]) -> list[str]:
        result = []
        for item in lst:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    return dedup(imports), dedup(classes), dedup(functions)


# ── 其他語言：Regex 粗提取 ──────────────────────

# 各語言的 import 樣式
_IMPORT_PATTERNS: dict[str, str] = {
    ".js":   r"(?:import\s+.*?from\s+['\"](.+?)['\"]|require\(['\"](.+?)['\"]\))",
    ".ts":   r"(?:import\s+.*?from\s+['\"](.+?)['\"]|require\(['\"](.+?)['\"]\))",
    ".jsx":  r"import\s+.*?from\s+['\"](.+?)['\"]",
    ".tsx":  r"import\s+.*?from\s+['\"](.+?)['\"]",
    ".java": r"import\s+([\w.]+)\s*;",
    ".go":   r'import\s+"([\w./]+)"',
    ".rs":   r"use\s+([\w:]+)",
    ".rb":   r"require\s+['\"](.+?)['\"]",
    ".php":  r"(?:use|require|include)\s+['\"]?(.+?)['\"]?\s*[;)]",
}

# 類別與函式（共用正則）
_CLASS_RE    = re.compile(r"(?:^|\s)class\s+(\w+)", re.MULTILINE)
_FUNC_RE     = re.compile(
    r"(?:^|\s)(?:function|def|func|fn|sub|method)\s+(\w+)", re.MULTILINE,
)


def _extract_generic(
    source: str, ext: str,
) -> tuple[list[str], list[str], list[str]]:
    """Regex 提取，適用無 ast 可用的語言。"""
    imports:   list[str] = []
    pattern = _IMPORT_PATTERNS.get(ext)
    if pattern:
        for m in re.finditer(pattern, source):
            # 某些 pattern 有兩個 group（JS require）
            val = next((g for g in m.groups() if g), "")
            if val:
                imports.append(val)

    classes   = _CLASS_RE.findall(source)
    functions = _FUNC_RE.findall(source)

    # 去重
    return (
        list(dict.fromkeys(imports)),
        list(dict.fromkeys(classes)),
        list(dict.fromkeys(functions)),
    )
