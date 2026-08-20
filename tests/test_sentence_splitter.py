"""Tests for sentence_splitter module."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.splitter import split_full_text


def test_single_sentence():
    result = split_full_text("Hello world！")
    assert result == ["Hello world！"], f"Got: {result}"


def test_multiple_sentences():
    result = split_full_text("First！Second？Third。")
    assert result == ["First！", "Second？", "Third。"], f"Got: {result}"


def test_no_terminator():
    result = split_full_text("No terminator")
    assert result == ["No terminator"], f"Got: {result}"


def test_empty_string():
    result = split_full_text("")
    assert result == [], f"Got: {result}"


def test_trailing_whitespace():
    result = split_full_text("Hello.  ")
    assert result == ["Hello."], f"Got: {result}"


def test_bracket_protection():
    result = split_full_text("Say (hello. world)!")
    assert len(result) == 1, f"Brackets should prevent splits: {result}"
    assert result[0].startswith("Say ("), f"Got: {result}"


def test_nested_brackets():
    result = split_full_text("A [B (C. D)] E! F.")
    assert len(result) == 2, f"Nested brackets: {result}"


def test_all_business_tools_have_descriptions():
    """业务工具必须有非空描述（用于步骤播报 + TTS）。"""
    from src.tools.desc import TOOL_DESC_MAP
    business_tools = [
        # 文件操作
        "list_directory", "read_text_file", "read_json_file", "read_yaml_file",
        "write_file", "delete_file", "move_file",
        # 系统
        "execute_command", "get_current_time", "get_system_info", "get_env_var",
        # 网络
        "web_search", "vision_analyze", "download_file",
        # 桌面交互
        "clipboard_read", "clipboard_write",
        # Live2D
        "live2d_motion",
        # 屏幕
        "take_screenshot",
        # Agent 元能力
        "think", "ask_user",
        # 人设档案
        "read_persona", "update_persona",
        "read_state", "update_state",
        "read_events", "update_events",
        "read_todos", "update_todos",
    ]
    for tool in business_tools:
        assert tool in TOOL_DESC_MAP, f"Missing business tool: {tool}"
        assert isinstance(TOOL_DESC_MAP[tool], str)
        assert len(TOOL_DESC_MAP[tool]) > 0, f"Empty desc for: {tool}"


def test_control_tools_present():
    """控制工具必须在 desc 表中有条目（描述允许为空，因为不走 TTS 播报）。"""
    from src.tools.desc import TOOL_DESC_MAP
    from src.agent.control_tools import CONTROL_TOOL_NAMES
    for tool in CONTROL_TOOL_NAMES:
        assert tool in TOOL_DESC_MAP, f"Missing control tool: {tool}"
        assert isinstance(TOOL_DESC_MAP[tool], str)


def test_step_desc_known():
    from src.tools.desc import step_desc
    assert "文件" in step_desc("list_directory")


def test_step_desc_unknown():
    from src.tools.desc import step_desc
    result = step_desc("nonexistent_tool")
    assert "nonexistent_tool" in result


def test_task_context():
    from src.agent.state import TaskContext
    ctx = TaskContext(original_request="test")
    assert ctx.is_empty()

    ctx.add_step("list_directory(.)", "file1.txt\nfile2.txt")
    assert not ctx.is_empty()

    summary = ctx.progress_summary()
    assert "list_directory" in summary
    assert "file1" in summary


def test_step_record():
    from src.agent.state import StepRecord
    step = StepRecord(step_num=1, action="test_action", result="test_result")
    assert step.step_num == 1
    assert step.action == "test_action"
    assert step.result == "test_result"
    assert step.timestamp  # non-empty timestamp


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
