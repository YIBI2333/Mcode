"""Agent 控制工具 — 无副作用的元工具，用于显式管理工作流阶段。

与业务工具（文件/搜索/Live2D 等）不同，控制工具不调用 MCP 服务端，
在 ReAct 循环里本地拦截处理，目的是替代"AI 一开口就结束"的隐式协议：

  plan     — 任务开始前上报计划，前端画进度
  speak    — 执行中向用户说话（不结束循环）
  reflect  — 一步结束前反思/调整
  finish   — 唯一显式结束信号，附摘要

设计原则:
  - 控制工具不进 MCP 工具服务器，纯 agent 层概念
  - schema 由 adapter.get_tools_schema() 合并到给 LLM 的工具列表
  - workflow.run_react_loop 在 execute_mcp_tool 之前拦截
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.agent.pipeline import SentenceEvent, StatusEvent
from src.agent.splitter import split_full_text


# 计划步骤里"结束性"措辞 → 过滤掉（finish 是独立动作，不该出现在 steps 里）
_FINISH_STEP_RE = re.compile(
    r"(收尾|收工|全部完成后|调用\s*finish|finish|结束任务|任务结束|完成\s*$|总结\s*$)"
)


# ═══════════════════════════════════════════════════════════════
# 名称注册表
# ═══════════════════════════════════════════════════════════════
CONTROL_TOOL_NAMES: frozenset = frozenset({"plan", "speak", "reflect", "finish"})


def is_control_tool(name: str) -> bool:
    return name in CONTROL_TOOL_NAMES


# ═══════════════════════════════════════════════════════════════
# Tool schema（OpenAI function-calling 格式）
# ═══════════════════════════════════════════════════════════════
CONTROL_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "复杂多步任务开始前上报执行计划。简单闲聊或单次工具调用不要用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "有序执行步骤列表，每步一句话",
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "任务执行中向用户说一句话（解释进展/安慰/中途回答），调用后循环不会结束，可继续执行后续工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要对用户说的话"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect",
            "description": "单步结束前反思：评估当前进展、决定继续/调整/收尾。",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "反思内容（一两句）"},
                    "action": {
                        "type": "string",
                        "enum": ["continue", "adjust", "finish"],
                        "description": "下一步行动：继续原计划/调整计划/准备收尾",
                    },
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "任务完成时调用此工具收尾，summary 会被读给用户听。纯闲聊不要调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "本次任务完成摘要，一两句话，会被语音读出"},
                },
                "required": ["summary"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# 分发处理
# ═══════════════════════════════════════════════════════════════
def _safe_args(raw) -> dict:
    """从 tool_call.function.arguments（str | dict）解析出 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def handle_control_tool(name: str, raw_args: Any, ctx):
    """处理一个控制工具调用。

    返回: (tool_result_str, events_list, should_finish)
      - tool_result_str: 回灌给 LLM 的工具结果文本（用户看不到）
      - events_list:    需要给上层 yield 的 PipelineEvent
      - should_finish:  True 表示循环应结束
    """
    args = _safe_args(raw_args)

    if name == "plan":
        steps = args.get("steps") or []
        if isinstance(steps, list):
            # 清洗：去空白、去空项、去"收尾/finish"类结束性步骤
            steps = [str(s).strip() for s in steps if s and str(s).strip()]
            steps = [s for s in steps if not _FINISH_STEP_RE.search(s)]
            ctx.plan_steps = steps
        ctx.set_phase_plan()
        ev = StatusEvent(type="plan", text=" → ".join(ctx.plan_steps))
        return f"已记录计划（{len(ctx.plan_steps)} 步）", [ev], False

    if name == "speak":
        text = str(args.get("text", "")).strip()
        if not text:
            return "已跳过空 speak", [], False
        ctx.set_phase_execute()
        ctx.add_reply(text)
        evs = [SentenceEvent(index=-1, text=s) for s in split_full_text(text) if s.strip()]
        return f"已向用户说: {text[:50]}", evs, False

    if name == "reflect":
        thought = str(args.get("thought", "")).strip()
        action = str(args.get("action", "continue")).strip()
        ev = StatusEvent(type="reflect", text=thought)
        if action == "finish":
            ctx.finished = True
            ctx.final_summary = thought
            return "反思完成，准备收尾", [ev], True
        if action == "adjust":
            return f"已记录调整想法: {thought[:60]}", [ev], False
        return f"已记录反思: {thought[:60]}", [ev], False

    if name == "finish":
        summary = str(args.get("summary", "")).strip()
        ctx.finished = True
        ctx.set_phase_done()
        evs = []
        if summary:
            ctx.add_reply(summary)
            evs = [SentenceEvent(index=-1, text=s) for s in split_full_text(summary) if s.strip()]
        evs.append(StatusEvent(type="done", text=summary or "任务完成"))
        return f"任务完成: {summary[:50]}", evs, True

    return f"未知控制工具: {name}", [], False