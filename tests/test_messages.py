# -*- coding: utf-8 -*-
"""消息构建测试 — system 拆分 / 首轮注入 / 历史截断。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory import messages as M
from src.memory import store


def _reset_short_term():
    """清空短期记忆（用 clear 操作同一对象，避免 import 引用问题）。"""
    store.short_term_memory.clear()


def _system_messages():
    """构造一次 build_messages，返回所有 system 消息 + 历史消息。"""
    msgs = M.build_messages("web_user", "hi")
    return [m for m in msgs if m.get("role") == "system"], msgs


# ────────────────────────────────────────────────
# system 消息拆分：每个 part 独立成一条 system
# ────────────────────────────────────────────────
def test_system_split_into_multiple():
    _reset_short_term()
    systems, msgs = _system_messages()
    assert len(systems) >= 5, f"system 应拆成多条: {len(systems)}"
    # 首条是时间块，含系统提示词、工作区、记忆、人设
    heads = [s["content"].split("\n", 1)[0] for s in systems]
    assert heads[0].startswith("现在是"), f"首条应为时间块: {heads[0]}"
    assert any("墨白" in s["content"] for s in systems)          # 系统提示词
    assert any("[工作区" in s["content"] for s in systems)       # 工作区
    assert any("[基本人设]" in s["content"] for s in systems)    # 人设
    # 消息末尾是当前 user
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "hi"


# ────────────────────────────────────────────────
# 首轮（短期记忆空）注入 重要事件/待办/时间轴
# ────────────────────────────────────────────────
def test_first_turn_injects_events_todos_timeline():
    _reset_short_term()
    systems, _ = _system_messages()
    joined = "\n".join(s["content"] for s in systems)
    assert "[重要事件]" in joined
    assert "[待办与偏好]" in joined
    # 时间轴可能不在 system（合并进时间块）——检查时间块内容里是否有时间轴
    assert "[时间轴]" in joined or "距离上次对话" in joined


# ────────────────────────────────────────────────
# 非首轮（短期记忆非空）不注入 重要事件/待办/时间轴，但注入 基本人设
# ────────────────────────────────────────────────
def test_subsequent_turn_skips_events_todos():
    _reset_short_term()
    store.short_term_memory.append({"role": "user", "content": "q1"})
    store.short_term_memory.append({"role": "assistant", "content": "a1"})
    systems, msgs = _system_messages()
    joined = "\n".join(s["content"] for s in systems)
    assert "[重要事件]" not in joined, "非首轮不应注入重要事件"
    assert "[待办与偏好]" not in joined, "非首轮不应注入待办"
    assert "[基本人设]" in joined, "基本人设应始终注入"
    # 历史保留（user/assistant 交替）
    roles = [m["role"] for m in msgs if m["role"] in ("user", "assistant")]
    assert roles == ["user", "assistant", "user"], f"历史角色序列: {roles}"


# ────────────────────────────────────────────────
# 历史截断：总字符超过上限时从旧丢弃，至少保留最新一条
# ────────────────────────────────────────────────
def test_history_truncated_by_chars():
    _reset_short_term()
    # 多条超长历史，远超 MAX_HISTORY_CHARS
    for i in range(6):
        store.short_term_memory.append({"role": "user", "content": "问题%d" % i})
        store.short_term_memory.append({"role": "assistant", "content": "回答%d" % i * 3000})
    msgs = M.build_messages("web_user", "当前")
    hist = [m for m in msgs if m["role"] in ("user", "assistant")]
    total = sum(len(str(m.get("content", ""))) for m in hist)
    # 设计：从最新保留，超上限则丢弃旧的；至少保留最新一条
    # 每条回复 9000 字，远超市上限 → 6 轮应被截断到只剩最新 1~2 条
    assert len(hist) < 6, f"超长历史应被截断: {len(hist)} 条"
    # 最新一条 assistant 必须保留
    assert hist[0]["role"] == "assistant", "最新一条应保留"
    assert hist[0]["content"].startswith("回答5"), f"应保留最新回复: {hist[0]['content'][:10]}"


# ────────────────────────────────────────────────
# 历史保留（正常长度，不截断）
# ────────────────────────────────────────────────
def test_history_kept_when_short():
    _reset_short_term()
    for i in range(3):
        store.short_term_memory.append({"role": "user", "content": "问题%d" % i})
        store.short_term_memory.append({"role": "assistant", "content": "回答%d" % i})
    msgs = M.build_messages("web_user", "当前")
    hist = [m for m in msgs if m["role"] in ("user", "assistant")]
    # 3 轮 + 当前 = 7 条历史消息
    assert len(hist) == 7, f"正常历史应全部保留: {len(hist)}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
