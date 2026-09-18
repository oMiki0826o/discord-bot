"""舊摘要與 Profile 相關性篩選回歸測試。"""

from core.ai.context_filter import select_profile, select_summary


SUMMARY = """
使用者希望 AI 表現得比較活潑。

**當前事實與狀態：**
- 台北明天多雲，晚間可能下雨。

**專案上下文：**
- GitHub 專案 raffel-yu/shuviV4 是 Discord.py Bot。

**待辦任務：**
- 繼續優化 shuviV4 Discord.py 機器人專案。
""".strip()


PROFILE = """
=== 使用者偏好 ===
- 常見話題：日常問候, GitHub Pages, 程式設計
- 溝通風格：幽默
- 備註：使用者熟悉開拓者相關語境
""".strip()


def test_daily_chat_does_not_receive_project_weather_or_todo_summary() -> None:
    selected = select_summary(SUMMARY, "在嗎")

    assert selected == ""


def test_project_question_receives_project_and_todo_blocks() -> None:
    selected = select_summary(SUMMARY, "shuviV4 專案現在還要改什麼")

    assert "專案上下文" in selected
    assert "待辦任務" in selected
    assert "台北明天" not in selected


def test_weather_question_only_receives_fact_state_block() -> None:
    selected = select_summary(SUMMARY, "明天天氣會下雨嗎")

    assert "台北明天" in selected
    assert "shuviV4" not in selected


def test_generic_discord_question_does_not_load_unrelated_project() -> None:
    selected = select_summary(SUMMARY, "Discord 要怎麼修改伺服器暱稱")

    assert "shuviV4" not in selected


def test_profile_keeps_style_but_hides_unrelated_topics_and_notes() -> None:
    selected = select_profile(PROFILE, "在嗎")

    assert "溝通風格：幽默" in selected
    assert "GitHub Pages" not in selected
    assert "開拓者" not in selected


def test_profile_topics_return_for_related_project_question() -> None:
    selected = select_profile(PROFILE, "GitHub Pages 要怎麼部署")

    assert "已確認相關話題" in selected
    assert "GitHub Pages" in selected
    assert "日常問候" not in selected
    assert "溝通風格：幽默" in selected
