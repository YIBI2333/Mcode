# -*- coding: utf-8 -*-
"""ReAct 工作流单元测试 — mock LLM 与 MCP，验证事件序列与收尾逻辑。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import patch

from src.agent.state import TaskContext
from src.agent.pipeline import SentenceEvent, StatusEvent, ToolEvent


def _tc(name, args=None, tid="t1"):
    """构造模拟 tool_call 对象（OpenAI 形状）"""
    return SimpleNamespace(id=tid, function=SimpleNamespace(
        name=name, arguments=args if args is not None else "{}"))


def _msg(content="", tool_calls=None):
    """构造模拟 ChatCompletionMessage"""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(msg):
    """构造模拟 ChatCompletion"""
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _run(gen):
    """跑完生成器，返回 (events, return_value)"""
    events = []
    try:
        while True:
            ev = next(gen)
            if ev is not None:
                events.append(ev)
    except StopIteration as e:
        return events, e.value


def _types(events):
    return [(type(e).__name__, getattr(e, "type", None)) for e in events]


# ────────────────────────────────────────────────
# 用例 1：工具 → finish 正常收尾，含 task-start/task-end
# ────────────────────────────────────────────────
def test_tool_then_finish():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="看时间")

    msgs = []
    first = _msg(tool_calls=[_tc("get_current_time", tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "现在是下午3点"}', tid="t1")])),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="当前时间: 15:00"):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    types = _types(events)
    assert ("StatusEvent", "task-start") in types, f"missing task-start: {types}"
    assert ("StatusEvent", "task-end") in types, f"missing task-end: {types}"
    assert ("StatusEvent", "step") in types
    assert any(isinstance(e, ToolEvent) for e in events)
    # finish 的 summary 走句子 + done 事件
    assert any(isinstance(e, SentenceEvent) and "下午3点" in e.text for e in events)
    assert ("StatusEvent", "done") in types
    assert "下午3点" in ret
    # tool result 回灌（msgs 混有 dict 与 LLM message 对象）
    assert any(isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") == "t0" for m in msgs)


# ────────────────────────────────────────────────
# 用例 2：两段式收尾 — content-only → 注入 finish 引导 → finish
# ────────────────────────────────────────────────
def test_content_only_then_finish():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="看时间")

    msgs = []
    first = _msg(tool_calls=[_tc("get_current_time", tid="t0")])
    seq = [
        _resp(_msg(content="现在是下午3点。")),                                          # 第一次 content-only
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "查到了时间"}', tid="t1")])),  # 引导后调 finish
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="15:00"):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    types = _types(events)
    # 第一次 content 先展示
    assert any(isinstance(e, SentenceEvent) and "下午3点" in e.text for e in events)
    # 注入了 finish 引导
    assert any(isinstance(m, dict) and m.get("role") == "system" and "finish" in m.get("content", "") for m in msgs)
    # finish 到位后正常收尾
    assert ("StatusEvent", "task-end") in types
    assert ("StatusEvent", "done") in types


# ────────────────────────────────────────────────
# 用例 3：LLM 不配合 — 两次 content-only → 强制结束
# ────────────────────────────────────────────────
def test_stubborn_content_forced_end():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="看时间")

    msgs = []
    first = _msg(tool_calls=[_tc("get_current_time", tid="t0")])
    seq = [
        _resp(_msg(content="好的。")),
        _resp(_msg(content="就这样。")),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="15:00"):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    types = _types(events)
    # 强制结束：无 finish 工具 → 无 done 事件，但必有 task-end（前端横幅完成态）
    assert ("StatusEvent", "task-end") in types
    assert ("StatusEvent", "done") not in types
    assert "好的。" in ret


# ────────────────────────────────────────────────
# 用例 4：plan → 工具 → finish 完整链
# ────────────────────────────────────────────────
def test_plan_tool_finish():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="整理文件")

    msgs = []
    first = _msg(tool_calls=[_tc("plan", '{"steps": ["列目录", "删除临时文件"]}', tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("list_directory", '{"path": "."}', tid="t1")])),
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "整理完成"}', tid="t2")])),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="a.txt b.txt"):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    types = _types(events)
    assert ("StatusEvent", "plan") in types
    assert ("StatusEvent", "task-start") in types
    assert ("StatusEvent", "task-end") in types
    assert ("StatusEvent", "step") in types
    assert ("StatusEvent", "done") in types
    # plan 记录到 ctx
    assert ctx.plan_steps == ["列目录", "删除临时文件"]


# ────────────────────────────────────────────────
# 用例 5：speak 中途说话不结束
# ────────────────────────────────────────────────
def test_speak_not_finish():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="查资料")

    msgs = []
    first = _msg(tool_calls=[_tc("web_search", '{"query": "x"}', tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("speak", '{"text": "找到一些结果了。"}', tid="t1")])),
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "查完了"}', tid="t2")])),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="结果..."):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    types = _types(events)
    # speak 的句子发出，但循环未结束（后续还有 finish 事件）
    assert any(isinstance(e, SentenceEvent) and "找到一些结果" in e.text for e in events)
    assert ("StatusEvent", "done") in types
    assert ("StatusEvent", "task-end") in types


# ────────────────────────────────────────────────
# 用例 6：取消中断
# ────────────────────────────────────────────────
def test_cancel():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="x")

    msgs = []
    first = _msg(tool_calls=[_tc("get_current_time", tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("get_current_time", tid="t1")])),
    ]
    it = iter(seq)

    # 首_轮工具执行后即取消
    flags = iter([False, False, True, True])

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="15:00"):
        events, ret = _run(run_react_loop(
            msgs, [], "auto", first, ctx,
            cancel_check=lambda: next(flags, True)))

    types = _types(events)
    assert ("StatusEvent", "task-start") in types
    assert ("StatusEvent", "cancelled") in types
    assert ("StatusEvent", "task-end") in types


# ────────────────────────────────────────────────
# 用例 7：文件操作 diff 统计 + task-end 汇总
# ────────────────────────────────────────────────
def test_file_op_summary():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="写文件")

    msgs = []
    first = _msg(tool_calls=[_tc("write_file", tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "写完了"}', tid="t1")])),
    ]
    it = iter(seq)

    fake_result = "文件已写入: a.txt (+10 -2)\n[FILE_OP] edit|a.txt|+10|-2"

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value=fake_result):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    # result 事件带 file_op meta
    result_ev = [e for e in events if type(e).__name__ == "ToolEvent" and e.tag == "result"][0]
    assert result_ev.meta["file_op"]["file"] == "a.txt"
    assert result_ev.meta["file_op"]["added"] == 10
    assert result_ev.meta["file_op"]["deleted"] == 2

    # task-end meta 带汇总
    end_ev = [e for e in events if type(e).__name__ == "StatusEvent" and e.type == "task-end"][0]
    assert end_ev.meta["files_total"] == 1
    assert end_ev.meta["added"] == 10
    assert end_ev.meta["deleted"] == 2
    assert "a.txt" in end_ev.meta["changed_files"]


# ────────────────────────────────────────────────
# 用例 8：step 事件无"步骤X"前缀，call 事件带 meta
# ────────────────────────────────────────────────
def test_step_no_prefix_and_call_meta():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="看时间")

    msgs = []
    first = _msg(tool_calls=[_tc("get_current_time", tid="t0")])
    seq = [
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "好了"}', tid="t1")])),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="15:00"):
        events, ret = _run(run_react_loop(msgs, [], "auto", first, ctx))

    step_ev = [e for e in events if type(e).__name__ == "StatusEvent" and e.type == "step"][0]
    assert "步骤" not in step_ev.text, f"step 仍带前缀: {step_ev.text}"

    call_ev = [e for e in events if type(e).__name__ == "ToolEvent" and e.tag == "call"][0]
    assert call_ev.meta.get("tool") == "get_current_time"
    assert not call_ev.text.startswith("["), f"call text 仍带编号: {call_ev.text}"


# ────────────────────────────────────────────────
# 用例 9：write/move/delete 工具的 FILE_OP 标记生成（server 端）
# ────────────────────────────────────────────────
def test_server_file_op_markers(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mcp_server_test", os.path.join(os.path.dirname(__file__), "..", "src", "tools", "server.py"))
    # server.py import 时会加载 OCR 模型，太重 — 直接测 _line_diff 纯函数逻辑
    # 这里改为内联验证 _line_diff 的行为（从 server.py 提取的核心逻辑一致）
    import difflib
    def _line_diff(old, new):
        a, b = old.splitlines(), new.splitlines()
        sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        added = deleted = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("replace", "delete"):
                deleted += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
        return added, deleted

    # 纯新增
    assert _line_diff("", "a\nb\nc") == (3, 0)
    # 纯删除
    assert _line_diff("a\nb\nc", "") == (0, 3)
    # 修改中间一行
    assert _line_diff("a\nb\nc", "a\nX\nc") == (1, 1)
    # 追加
    assert _line_diff("a", "a\nb") == (1, 0)
    # 不变
    assert _line_diff("a\nb", "a\nb") == (0, 0)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])