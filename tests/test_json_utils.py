"""
tests/test_json_utils.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

測試 core.ai.json_utils.strip_json_fence()。

重點覆蓋原本的 bug：
舊版使用 raw.lstrip("```json").lstrip("```").rstrip("```")，
lstrip / rstrip 是「移除字元集合」而非「移除前綴字串」，
若 JSON 內容開頭恰好是 j / s / o / n 等字元就會被誤刪。
本測試確保改用正則表達式的新版不會重現這個問題。
"""

from __future__ import annotations

import json

from core.ai.json_utils import strip_json_fence


def test_no_fence_returns_unchanged():
    raw = '{"keyword": "test", "content": "hello"}'
    assert strip_json_fence(raw) == raw


def test_strips_json_fence_with_language_tag():
    raw = '```json\n{"a": 1}\n```'
    assert strip_json_fence(raw) == '{"a": 1}'


def test_strips_plain_fence_without_language_tag():
    raw = '```\n{"a": 1}\n```'
    assert strip_json_fence(raw) == '{"a": 1}'


def test_does_not_corrupt_content_starting_with_j():
    """
    回歸測試：舊版 bug 會把開頭的 'j' 誤判為 ```json 前綴的一部分而被刪除。
    """
    raw = '```json\n{"jobs": "data"}\n```'
    result = strip_json_fence(raw)
    assert result == '{"jobs": "data"}'
    assert json.loads(result) == {"jobs": "data"}


def test_does_not_corrupt_content_starting_with_s_o_n():
    """
    回歸測試：舊版 lstrip("```json") 會把字元集合 {`, j, s, o, n}
    中任何一個開頭字元都吃掉，這裡分別驗證 s / o / n 開頭的情況。
    """
    for word in ("summary", "object", "note"):
        raw = f'```json\n{{"{word}": "value"}}\n```'
        result = strip_json_fence(raw)
        parsed = json.loads(result)
        assert parsed == {word: "value"}, f"word={word!r} 解析結果不符: {parsed!r}"


def test_strips_leading_and_trailing_whitespace():
    raw = '   ```json\n{"a": 1}\n```   '
    assert strip_json_fence(raw) == '{"a": 1}'


def test_empty_string_returns_empty():
    assert strip_json_fence("") == ""


def test_multiline_json_content_preserved():
    raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
    result = strip_json_fence(raw)
    assert json.loads(result) == {"a": 1, "b": 2}
