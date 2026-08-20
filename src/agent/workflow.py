"""
Agent ReAct 工作流 — 统一控制工具版

取代旧协议:
  旧: "AI 一旦在循环中输出文本就视为最终回复终止循环"（中间无说话）
  新: 用控制工具 plan/speak/reflect/finish 显式管理阶段；finish 是唯一结束信号，
      speak 可在执行中说话但不结束，工具后的 content-only 自然收尾

事件保证（无论 LLM 是否配合调用控制工具，前端都有任务感）:
  - 进入循环必发 StatusEvent(task-start)
  - 任何形式退出必发 StatusEvent(task-end)，meta 带文件变更汇总

工具事件（opencode 风格）:
  - ToolEvent(call)   meta={tool, args}   → 前端渲染工具条目（进行中）
  - ToolEvent(result) meta={file_op}      → 条目完成 + diff 徽章
  - StatusEvent(step) text=自然语言描述   → 仅用于 TTS 播报，前端不渲染文本
"""
import json as _json
import re

from typing import Generator, Callable

from src.agent.llm_client import call_model
from src.tools.adapter import execute_mcp_tool
from src.agent.pipeline import SentenceEvent, ToolEvent, StatusEvent
from src.agent.splitter import split_full_text
from src.agent.state import TaskContext
from src.agent.control_tools import is_control_tool, handle_control_tool
from src.tools.desc import step_desc
from config.settings import MAX_AGENT_STEPS

# ── 循环内注入的引导语（驱动 LLM 使用控制工具） ──
_GUIDE_AFTER_TOOL = (
    "[任务模式提醒] 你正处于任务执行循环中。规则：\n"
    "- 还有后续步骤 → 继续调用业务工具\n"
    "- 想向用户说话/汇报进展 → 调用 speak(text)，不要直接输出文本\n"
    "- 全部完成 → 调用 finish(summary) 收尾\n"
    "不要输出裸文本，必须通过工具表达。"
)
_GUIDE_ASK_FINISH = (
    "你刚才的回答已展示给用户。现在请调用 finish(summary) 正式收尾，"
    "summary 用一句话概括本次任务结果。只调这一个工具，不要输出文本。"
)
_GUIDE_PUSH_WORK = (
    "请通过调用业务工具推进任务（或先 plan 上报计划），"
    "不要只说话不行动；若认为无需任何工具，调用 finish 收尾。"
)
_GUIDE_CONTINUE = (
    "[任务继续] 用户刚才打断了任务并补充了新消息（见上一条 user 消息）。"
    "请结合新消息继续之前未完成的任务。规则不变：推进用业务工具，"
    "向用户说话用 speak，全部完成调 finish(summary) 收尾，不要输出裸文本。"
)

# 工具结果中的文件操作标记: [FILE_OP] edit|path|+12|-3
_FILE_OP_RE = re.compile(
    r"\[FILE_OP\]\s*(\w+)\|([^|]+)\|\+(\d+)\|-(\d+)"
)


def _parse_file_op(result: str) -> dict | None:
    """从工具结果文本解析文件操作标记，返回 file_op dict 或 None"""
    m = _FILE_OP_RE.search(str(result))
    if not m:
        return None
    return {
        "op": m.group(1),
        "file": m.group(2).strip(),
        "added": int(m.group(3)),
        "deleted": int(m.group(4)),
    }


def _tool_brief(name: str, raw_args: str) -> str:
    """工具调用的参数摘要（opencode 风格：显示第一个有意义的参数值）"""
    try:
        args = _json.loads(raw_args) if raw_args and raw_args != "{}" else {}
    except Exception:
        args = {}
    for key in ("path", "file", "query", "command", "url", "content", "source", "motion"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            v = v.replace("\n", " ")
            return (v[:40] + "…") if len(v) > 40 else v
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def _task_end(ctx, kind: str = "done") -> StatusEvent:
    """构造带文件变更汇总的 task-end 事件"""
    return StatusEvent(type="task-end", text=kind, meta=ctx.files_summary())


# ═══════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════
def run_react_loop(
    messages: list,
    tools: list,
    tool_choice: str,
    first_msg,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator:
    """ReAct 主循环。

    Args:
        first_msg: 首轮 call_model 返回的 ChatCompletionMessage（含 tool_calls）。
                   传 None 表示"继续模式"——messages 已含完整历史（被打断的任务），
                   直接进入循环，不重复执行首轮工具。
    yields: PipelineEvent 子类
    returns (StopIteration.value): 完整回复 str（用于记忆存储）
    """
    resume = first_msg is None

    if resume:
        # ── 继续模式：注入继续引导，直接进入循环 ──
        messages.append({"role": "system", "content": _GUIDE_CONTINUE})
        yield StatusEvent(type="task-start", text=(ctx.original_request or "")[:60])
        guide_sent = True     # 之前已注入过 after-tool 引导，不再重复
        no_work_count = 0
        loop_steps = max(1, MAX_AGENT_STEPS)
    else:
        # ── 新任务：执行首轮工具 ──
        messages.append(first_msg)
        yield StatusEvent(type="task-start", text=(ctx.original_request or "")[:60])
        finished = yield from _exec_tool_calls(messages, first_msg, ctx, cancel_check)
        if finished:
            yield _task_end(ctx)
            return ctx.accumulated_reply
        if cancel_check and cancel_check():
            yield StatusEvent(type="cancelled", text="已中断")
            yield _task_end(ctx, "cancelled")
            return ctx.accumulated_reply
        guide_sent = False
        no_work_count = 0
        loop_steps = max(1, MAX_AGENT_STEPS - 1)

    for _ in range(loop_steps):
        if ctx.finished:
            yield _task_end(ctx)
            return ctx.accumulated_reply
        if cancel_check and cancel_check():
            yield StatusEvent(type="cancelled", text="已中断")
            yield _task_end(ctx, "cancelled")
            return ctx.accumulated_reply

        try:
            resp = call_model(messages=messages, tools=tools, tool_choice=tool_choice)
        except Exception as e:
            yield StatusEvent(type="error", text=f"LLM 调用失败: {e}")
            yield _task_end(ctx, "error")
            return ctx.accumulated_reply
        msg = resp.choices[0].message
        messages.append(msg)

        tool_calls = msg.tool_calls or []
        content = (msg.content or "").strip()

        # ── 有工具调用 ──
        if tool_calls:
            no_work_count = 0
            # content 与 tool_calls 同帧：把 content 当中间说话
            if content:
                yield from _emit_sentences(content)
                ctx.add_reply(content)
            finished = yield from _exec_tool_calls(messages, msg, ctx, cancel_check)
            if finished:
                yield _task_end(ctx)
                return ctx.accumulated_reply
            # 工具执行完注入一次引导，驱动 LLM 下一步用 speak/finish
            if not guide_sent:
                guide_sent = True
                messages.append({"role": "system", "content": _GUIDE_AFTER_TOOL})
            continue

        # ── 无工具调用 ──
        if content:
            # 已要求过 finish 但模型仍输出 content（未调 finish）→ 不重复展示，直接收尾
            if ctx.finish_requested:
                yield _task_end(ctx)
                return ctx.accumulated_reply
            # 先把回答展示给用户（不阻塞）
            yield from _emit_sentences(content)
            ctx.add_reply(content)
            if ctx.has_executed_real_tool:
                # 两段式收尾：已要求过 finish 还是不调 → 强制结束
                ctx.finish_requested = True
                messages.append({"role": "system", "content": _GUIDE_ASK_FINISH})
                continue
            # 只规划/只说话没干活 → 推一下
            no_work_count += 1
            if no_work_count >= 3:
                yield _task_end(ctx)
                return ctx.accumulated_reply
            messages.append({"role": "system", "content": _GUIDE_PUSH_WORK})
            continue

        # ── 空 content + 无 tool_calls ──
        if ctx.accumulated_reply or ctx.has_executed_real_tool:
            yield _task_end(ctx)
            return ctx.accumulated_reply
        no_work_count += 1
        if no_work_count >= 3:
            yield _task_end(ctx)
            return ctx.accumulated_reply or ""
        messages.append({"role": "system", "content": _GUIDE_PUSH_WORK})

    # 超过 MAX_AGENT_STEPS
    yield StatusEvent(type="error", text=f"达到最大步数 {MAX_AGENT_STEPS}，强制收尾")
    yield _task_end(ctx, "max-steps")
    return ctx.accumulated_reply


# ═══════════════════════════════════════════════════════════════
# 工具执行
# ═══════════════════════════════════════════════════════════════
def _exec_tool_calls(messages, msg, ctx, cancel_check) -> bool:
    """执行 msg.tool_calls 里的所有工具。

    返回 (StopIteration.value): 是否触发结束信号（finish / reflect(action=finish)）。
    yielded: PipelineEvent
    """
    finished = False
    for tc in (msg.tool_calls or []):
        if cancel_check and cancel_check():
            yield StatusEvent(type="cancelled", text="已中断")
            finished = True
            return finished

        fn = tc.function.name
        fa = tc.function.arguments or "{}"

        # 控制工具 → 本地拦截
        if is_control_tool(fn):
            result, evs, should_finish = handle_control_tool(fn, fa, ctx)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            for ev in evs:
                yield ev
            if should_finish:
                finished = True
            continue

        # 业务工具 → 走 MCP
        ctx.mark_real_tool_executed()
        ctx.set_phase_execute()
        n = next_step_num()
        desc = step_desc(fn, fa)
        # step 事件只用于 TTS 播报（不带"步骤X"前缀），前端不渲染文本
        yield StatusEvent(type="step", text=desc)
        yield ToolEvent(
            tag="call", text=f"{fn} {_tool_brief(fn, fa)}".strip(),
            meta={"tool": fn, "args": _tool_brief(fn, fa)},
        )
        try:
            result = execute_mcp_tool(tc)
        except Exception as e:
            result = f"[工具执行失败] {e}"

        # 解析文件操作 → diff 统计 + 记录到 ctx
        file_op = _parse_file_op(result)
        if file_op:
            ctx.record_file_change(file_op["op"], file_op["file"],
                                   file_op["added"], file_op["deleted"])

        result_meta = {"file_op": file_op} if file_op else {}
        yield ToolEvent(
            tag="result", text=str(result)[:200].replace("\n", " ").strip(),
            meta=result_meta,
        )
        ctx.add_step(action=f"{fn}({fa})", result=str(result))
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    return finished


def _emit_sentences(text: str):
    """把一段文本拆成句子发出去（供循环中中间说话/最终回答用）."""
    for s in split_full_text(text):
        s = s.strip()
        if not s:
            continue
        yield SentenceEvent(index=-1, text=s)


# ═══════════════════════════════════════════════════════════════
# 步骤计数器（保持对外 API 兼容）
# ═══════════════════════════════════════════════════════════════
_step_counter = 0

def reset_step_counter() -> int:
    global _step_counter
    _step_counter = 0
    return _step_counter

def next_step_num() -> int:
    global _step_counter
    _step_counter += 1
    return _step_counter


# ═══════════════════════════════════════════════════════════════
# 旧 API 兼容（generator 改造后从新本文件导入 run_react_loop）
# ═══════════════════════════════════════════════════════════════
run_agent_workflow = run_react_loop