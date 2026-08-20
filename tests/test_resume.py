# -*- coding: utf-8 -*-
"""打断续接（工作流快照 + 继续模式）单元测试。"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import patch

from src.agent.state import TaskContext
from src.agent.pipeline import SentenceEvent, StatusEvent, ToolEvent
from src.agent import session as sess


def _tc(name, args=None, tid="t1"):
    return SimpleNamespace(id=tid, function=SimpleNamespace(
        name=name, arguments=args if args is not None else "{}"))


def _msg(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(msg):
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _run(gen):
    events = []
    try:
        while True:
            ev = next(gen)
            if ev is not None:
                events.append(ev)
    except StopIteration as e:
        return events, e.value


# ────────────────────────────────────────────────
# 序列化：ChatCompletionMessage 对象 → dict
# ────────────────────────────────────────────────
def test_msg_to_dict_preserves_tool_calls():
    m = SimpleNamespace(
        role="assistant", content=None,
        tool_calls=[SimpleNamespace(id="t9", type="function",
                                    function=SimpleNamespace(name="f", arguments='{"a":1}'))],
    )
    d = sess._msg_to_dict(m)
    assert d["role"] == "assistant"
    assert d["tool_calls"][0]["id"] == "t9"
    assert d["tool_calls"][0]["function"]["name"] == "f"


# ────────────────────────────────────────────────
# 序列化：打断中途补齐缺失的 tool 结果
# ────────────────────────────────────────────────
def test_serialize_fills_missing_tool_results():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [
             {"id": "a", "type": "function", "function": {"name": "f1", "arguments": "{}"}},
             {"id": "b", "type": "function", "function": {"name": "f2", "arguments": "{}"}},
         ]},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
        # b 缺结果（打断发生在执行 b 前）
    ]
    out = sess._serialize_messages(msgs)
    ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
    assert ids == ["a", "b"], f"应补齐 b 的结果: {ids}"
    # b 的占位结果
    b = [m for m in out if m.get("tool_call_id") == "b"][0]
    assert "[已中断" in b["content"]


def test_serialize_keeps_complete():
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "r"},
    ]
    out = sess._serialize_messages(msgs)
    assert len(out) == 2  # 不额外补


# ────────────────────────────────────────────────
# 快照生命周期：save / pop / TTL / clear
# ────────────────────────────────────────────────
def test_workflow_snapshot_lifecycle():
    sess.clear_workflow()
    ctx = TaskContext(original_request="x")
    sess.save_workflow([{"role": "user", "content": "hi"}], ctx, "web_user")
    wf = sess.pop_workflow()
    assert wf is not None
    assert wf["ctx"] is ctx
    assert wf["user_id"] == "web_user"
    # pop 后清空
    assert sess.pop_workflow() is None


def test_workflow_snapshot_expired():
    sess.clear_workflow()
    ctx = TaskContext(original_request="x")
    sess.save_workflow([{"role": "user", "content": "hi"}], ctx, "web_user")
    # 伪造过期
    with sess._workflow_lock:
        sess._active_workflow["ts"] = time.time() - sess.WORKFLOW_RESUME_TTL - 1
    assert sess.pop_workflow() is None


# ────────────────────────────────────────────────
# 继续模式：run_react_loop(first_msg=None) 不重复执行首轮工具
# ────────────────────────────────────────────────
def test_resume_mode_skips_first_tool():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="查时间")
    ctx.mark_real_tool_executed()
    ctx.set_phase_execute()

    # 历史：已执行过 get_current_time
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "现在几点"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t0", "type": "function",
                         "function": {"name": "get_current_time", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t0", "content": "15:00"},
    ]
    seq = [
        _resp(_msg(tool_calls=[_tc("finish", '{"summary": "3点了"}', tid="t1")])),
    ]
    it = iter(seq)

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)), \
         patch("src.agent.workflow.execute_mcp_tool", return_value="SHOULD_NOT_CALL") as exec_mock:
        events, ret = _run(run_react_loop(msgs, [], "auto", None, ctx))

    # 首轮工具 get_current_time 不应被再次执行
    exec_mock.assert_not_called()
    types = [(type(e).__name__, getattr(e, "type", None)) for e in events]
    assert ("StatusEvent", "task-start") in types
    assert ("StatusEvent", "done") in types
    assert ("StatusEvent", "task-end") in types
    # 注入了继续引导
    assert any(isinstance(m, dict) and "任务继续" in (m.get("content") or "") for m in msgs)
    assert "3点" in ret or "3点了" in ret


# ────────────────────────────────────────────────
# 继续模式：再次被打断时，事件含 cancelled + task-end
# ────────────────────────────────────────────────
def test_resume_mode_cancel():
    from src.agent.workflow import run_react_loop
    ctx = TaskContext(original_request="x")
    ctx.mark_real_tool_executed()

    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t0", "type": "function",
                         "function": {"name": "get_current_time", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t0", "content": "15:00"},
    ]
    seq = [_resp(_msg(tool_calls=[_tc("get_current_time", tid="t2")]))]
    it = iter(seq)
    flags = iter([True])  # 第一次 cancel 检查即命中

    with patch("src.agent.workflow.call_model", side_effect=lambda **kw: next(it)):
        events, ret = _run(run_react_loop(
            msgs, [], "auto", None, ctx, cancel_check=lambda: next(flags, True)))

    types = [(type(e).__name__, getattr(e, "type", None)) for e in events]
    assert ("StatusEvent", "task-start") in types
    assert ("StatusEvent", "cancelled") in types
    assert ("StatusEvent", "task-end") in types


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
