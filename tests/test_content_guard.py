"""
tests/test_content_guard.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

測試 core.ai.content_guard：
- 檔案不存在 / 為空時回傳空字串（功能預設不影響任何行為）
- "#" 開頭的註解行會被過濾
- 檔案內容變動後（mtime 改變）會自動重新讀取，不需重啟
"""

from __future__ import annotations

import time

import core.ai.content_guard as content_guard


def _point_to(monkeypatch, path):
    monkeypatch.setattr(content_guard, "_RULES_FILE", path)
    monkeypatch.setattr(content_guard, "_cached_content", "")
    monkeypatch.setattr(content_guard, "_cached_mtime", -1.0)


def test_missing_file_returns_empty_prompt(tmp_path, monkeypatch):
    _point_to(monkeypatch, tmp_path / "does_not_exist.txt")
    assert content_guard.moderation_to_prompt() == ""


def test_empty_file_returns_empty_prompt(tmp_path, monkeypatch):
    rules_file = tmp_path / "moderation_rules.txt"
    rules_file.write_text("", encoding="utf-8")
    _point_to(monkeypatch, rules_file)
    assert content_guard.moderation_to_prompt() == ""


def test_comment_lines_are_filtered_out(tmp_path, monkeypatch):
    rules_file = tmp_path / "moderation_rules.txt"
    rules_file.write_text(
        "# 這是註解，不應出現在結果中\n避免討論政治\n# 另一行註解\n避免提供醫療診斷\n",
        encoding="utf-8",
    )
    _point_to(monkeypatch, rules_file)

    prompt = content_guard.moderation_to_prompt()
    assert "這是註解" not in prompt
    assert "另一行註解" not in prompt
    assert "避免討論政治" in prompt
    assert "避免提供醫療診斷" in prompt


def test_only_comments_results_in_empty_prompt(tmp_path, monkeypatch):
    rules_file = tmp_path / "moderation_rules.txt"
    rules_file.write_text("# 只有註解\n# 沒有實際規則\n", encoding="utf-8")
    _point_to(monkeypatch, rules_file)
    assert content_guard.moderation_to_prompt() == ""


def test_file_change_triggers_reload(tmp_path, monkeypatch):
    rules_file = tmp_path / "moderation_rules.txt"
    rules_file.write_text("規則一\n", encoding="utf-8")
    _point_to(monkeypatch, rules_file)

    first = content_guard.moderation_to_prompt()
    assert "規則一" in first

    time.sleep(0.02)   # 確保 mtime 有變化
    rules_file.write_text("規則二\n", encoding="utf-8")

    second = content_guard.moderation_to_prompt()
    assert "規則二" in second
    assert "規則一" not in second


def test_reload_rules_forces_refresh(tmp_path, monkeypatch):
    rules_file = tmp_path / "moderation_rules.txt"
    rules_file.write_text("初始規則\n", encoding="utf-8")
    _point_to(monkeypatch, rules_file)
    content_guard.moderation_to_prompt()

    rules_file.write_text("強制重載後的規則\n", encoding="utf-8")
    text = content_guard.reload_rules()
    assert "強制重載後的規則" in text
