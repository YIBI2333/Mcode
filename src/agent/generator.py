"""对话生成主流程 — 首轮分流：任务路径(ReAct) / 聊天路径(流式)

分流逻辑（统一 ReAct 版本）:
  首轮非流式 call_model →
    msg.tool_calls 非空 → 任务路径 _tool_path
          (调用 run_react_loop，控制工具 finish 是唯一结束信号，
           speak 可中途说话但不结束)
    否则                 → 聊天路径 _stream_path
          (重新流式调用，逐句输出)

事件模型:
  Yield 全部为 PipelineEvent 子类:
    SentenceEvent  — 正文句子（上层异步填充 TTS）
    ToolEvent      — 工具调用/结果
    StatusEvent    — 状态消息（plan/step/reflect/done/error/cancelled）
"""

import sys
import logging
import re

from src.agent.llm_client import call_model, call_model_stream_str
from src.memory.messages import build_messages
from src.memory.engine import store_memory_async
from src.tools.adapter import get_tools_schema, execute_mcp_tool
from src.agent.workflow import run_react_loop, reset_step_counter, next_step_num
from src.agent.state import TaskContext
from src.agent.session import save_workflow, pop_workflow
from src.tools.desc import step_desc
from src.agent.pipeline import SentenceEvent, ToolEvent, StatusEvent, stream_and_split
from src.agent.splitter import split_full_text
from config.settings import ENABLE_TOOLS, TOOL_CHOICE, ENABLE_AGENT_MODE

_log = logging.getLogger(__name__)

_tools_schema_cache = None


def _get_tools_schema():
    """获取工具 schema（空结果不缓存，下次重试）"""
    global _tools_schema_cache
    if _tools_schema_cache:
        return _tools_schema_cache
    tools = get_tools_schema()
    if tools:
        _tools_schema_cache = tools
    return tools


def generate_reply(user_id: str, user_message: str, cancel_check=None,
                   image_b64: str = None, image_mime: str = None, hidden: bool = False,
                   system_note: str = None):
    """对话生成入口 — yield PipelineEvent 子类。

    若存在被打断的任务快照，则把 user_message 作为补充指令，恢复并继续任务。
    hidden（心跳/定时任务触发的系统消息）不参与快照续接。
    system_note 作为额外 system 指令注入（如心跳的自主行动提示），
    user_message 可留空，由 system_note 驱动 AI 主动回复。
    """
    reset_step_counter()
    try:
        wf = None if hidden else pop_workflow()
        if wf:
            yield from _resume_workflow(wf, user_message, cancel_check)
        else:
            yield from _generate_impl(user_id, user_message, cancel_check,
                                      image_b64, image_mime, hidden, system_note)
    except Exception as e:
        import traceback
        traceback.print_exc()
        yield StatusEvent(type="error", text=f"LLM 调用失败: {e}")


def _resume_workflow(wf: dict, user_message: str, cancel_check=None):
    """继续被打断的任务：恢复 messages + 计划/文件变更状态，注入补充消息，续跑 ReAct。"""
    tools = _get_tools_schema() if ENABLE_TOOLS else None
    tool_choice = TOOL_CHOICE if ENABLE_TOOLS else "none"

    messages = wf["messages"]
    ctx = wf["ctx"]
    user_id = wf["user_id"]

    # 用户补充的新消息 → 追加到上下文
    messages.append({"role": "user", "content": user_message})

    full_reply = yield from _drive_react(
        messages, tools, tool_choice, None, ctx, cancel_check, resume=True)

    # 继续过程中若再次被打断 → 重新保存快照，等待下次续接
    if cancel_check and cancel_check():
        save_workflow(messages, ctx, user_id)
        return

    if full_reply:
        try:
            print(f"\n[AI 原始回复]\n{full_reply}\n", flush=True)
        except UnicodeEncodeError:
            print(f"\n[AI reply] {len(full_reply)} chars\n", flush=True)
        store_memory_async(user_id, user_message, full_reply)


def _generate_impl(user_id: str, user_message: str, cancel_check=None,
                   image_b64: str = None, image_mime: str = None, hidden: bool = False,
                   system_note: str = None):
    """内部实现 — 首轮分流 + 任务/聊天两条路径"""

    tools = _get_tools_schema() if ENABLE_TOOLS else None
    tool_choice = TOOL_CHOICE if ENABLE_TOOLS else "none"

    messages = build_messages(user_id, user_message,
                              image_b64=image_b64, image_mime=image_mime,
                              system_note=system_note)

    # ═══ 首轮非流式：由 LLM 自主决定是否需要工具/规划 ═══
    response = call_model(messages=messages, tools=tools,
                          tool_choice=tool_choice, stream=False)
    msg = response.choices[0].message

    if msg.tool_calls:
        # ═══ 任务路径：ReAct 循环 ═══
        full_reply, ctx = yield from _tool_path(
            messages, msg, tools, tool_choice,
            _friendly_original(user_message, hidden), cancel_check)
        # 打断 → 保存工作流快照，等待用户补充消息继续（hidden 系统消息不续接）
        if cancel_check and cancel_check():
            if not hidden:
                save_workflow(messages, ctx, user_id)
            return
    else:
        # ═══ 聊天路径：重新走流式输出 ═══
        full_reply = yield from _stream_path(messages, cancel_check)

    if cancel_check and cancel_check():
        return

    if full_reply:
        try:
            print(f"\n[AI 原始回复]\n{full_reply}\n", flush=True)
        except UnicodeEncodeError:
            print(f"\n[AI reply] {len(full_reply)} chars\n", flush=True)
        if user_message:  # 心跳（空 user 输入）不存长期记忆
            store_memory_async(user_id, user_message, full_reply)


# ── ReAct 循环驱动器（新任务 / 继续共用） ──

def _drive_react(messages, tools, tool_choice, first_msg, ctx, cancel_check, resume=False):
    """驱动 ReAct 循环，收集句子文本。

    first_msg=None 表示继续模式（消息已含完整历史）。
    返回 full_reply（str）。
    """
    agent_gen = run_react_loop(
        messages, tools, tool_choice,
        first_msg, ctx, cancel_check=cancel_check)
    full_reply = ctx.accumulated_reply if resume else ""
    try:
        while True:
            event = next(agent_gen)
            if event is None:
                continue
            if isinstance(event, SentenceEvent):
                full_reply += event.text
            yield event
    except StopIteration as e:
        ret = (e.value or "").strip()
        if ret and not full_reply:
            full_reply = ret
            for s in split_full_text(ret):
                if s := s.strip():
                    yield SentenceEvent(index=-1, text=s)
    return full_reply


# ── 路径 A：任务（首轮已有 tool_calls → ReAct 循环） ──

def _friendly_original(user_message: str, hidden: bool) -> str:
    """任务横幅描述：隐藏消息（定时任务/心跳/打招呼）去掉系统提示词前缀。"""
    if not hidden:
        return user_message
    m = re.match(r'^\[定时任务[:：]\s*([^\]]+)\]', user_message or "")
    if m:
        return m.group(1).strip()
    if (user_message or "").startswith('[系统]'):
        return ""
    return user_message


def _tool_path(messages, first_msg, tools, tool_choice, user_message, cancel_check):
    """任务路径。

    返回 (full_reply, ctx)：full_reply 用于记忆存储，ctx 用于打断时保存快照。
    """
    ctx = TaskContext(original_request=user_message)

    if ENABLE_AGENT_MODE:
        full_reply = yield from _drive_react(
            messages, tools, tool_choice, first_msg, ctx, cancel_check, resume=False)
        return full_reply, ctx

    # ── 非 Agent 模式：执行首轮工具后只补一次最终回答 ──
    messages.append(first_msg)
    full_reply = ""
    for tc in first_msg.tool_calls:
        if cancel_check and cancel_check():
            yield StatusEvent(type="cancelled", text="用户中断")
            return full_reply, ctx
        n = next_step_num()
        desc = step_desc(tc.function.name, tc.function.arguments)
        yield StatusEvent(type="step", text=f"步骤{n}: {desc}")
        yield ToolEvent(tag="call", text=f"[{n}] {tc.function.name}({tc.function.arguments})")
        try:
            result = execute_mcp_tool(tc)
        except Exception as e:
            result = f"[工具执行失败] {e}"
        yield ToolEvent(tag="result", text=str(result)[:200].replace("\n", " ").strip())
        ctx.add_step(action=f"{tc.function.name}({tc.function.arguments})", result=str(result))
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    # 补一次最终回答（不带工具，纯文本）
    final = call_model(messages=messages, tools=None, tool_choice="none", stream=False)
    text = final.choices[0].message.content or ""
    full_reply = text
    if text:
        for sent in split_full_text(text):
            s = sent.strip()
            if not s:
                continue
            yield SentenceEvent(index=-1, text=s)
    return full_reply, ctx


# ── 路径 B：聊天（纯流式输出） ──

def _stream_path(messages, cancel_check):
    """流式 LLM + 实时拆句。返回 full_reply（str）。"""
    full_reply = ""
    for event in stream_and_split(
        call_model_stream_str, messages,
        tools=None, tool_choice="none",
        cancel_check=cancel_check,
    ):
        if isinstance(event, SentenceEvent):
            full_reply += event.text
        yield event
    return full_reply