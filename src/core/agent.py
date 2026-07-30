"""
Claude Code 风格 Agent 工作流引擎

三阶段工作流：
  1. PLAN     — 分析任务 → 制定执行计划 → 用自然语言展示
  2. EXECUTE  — 逐步执行 → 每步前思考 → 执行工具 → 观察 → 汇报进度
  3. REFLECT  — 检查结果 → 总结 → 情感化收尾

核心理念：
  - Agent 不是工具人，是有情感的数字伙伴
  - 像人类一样：先想再做，中间会停顿思考，完成后会表达感受
  - 保持猫娘人设，不丢失可爱和情感
"""
import json as _json
import re
import uuid
from collections import namedtuple
from typing import Generator, Callable
from src.infra.client import call_model, client
from src.tools.mcp_tools import execute_mcp_tool
from src.core.state import TaskContext, AgentPhase
from config.settings import MAX_AGENT_STEPS
import config.api_config as api_config


# ── 全局步骤计数器 ──
_step_counter = 0

def reset_step_counter():
    global _step_counter
    _step_counter = 0

def next_step_num() -> int:
    global _step_counter
    _step_counter += 1
    return _step_counter

# ── 情感化进度模板 ──
_THINKING_PREFIXES = [
    "嗯，让我想想这一步...",
    "我来捋一下思路~",
    "这个需要仔细看看呢...",
    "唔，让我分析一下...",
    "好的，我先理清楚...",
]

_PROGRESS_TEMPLATES = [
    "第{step}步完成啦 ✨ {detail}",
    "好，{step}步搞定了~ {detail}",
    "这一步做好了！{detail}",
    "完成！继续加油~ {detail}",
]

_TOOL_START_TEMPLATES = {
    "list_directory": "让我看看这里都有什么文件喵~",
    "read_text_file": "我来读一下这个文件...",
    "read_json_file": "读取配置中...",
    "write_file": "正在写入文件，稍等哦~",
    "delete_file": "删除文件... 确认一下不会删错！",
    "execute_command": "正在执行命令，让我看看结果...",
    "web_search": "让我在网上搜索一下相关信息~",
    "vision_analyze": "让我仔细看看这张图片...",
    "download_file": "下载中，请稍候...",
    "think": "让我认真思考一下这个问题...",
    "ask_user": "唔，我需要跟你确认一下...",
    "update_persona": "记下来记下来，这个很重要！",
}

_SUCCESS_REACTIONS = [
    "好耶！任务完成了~ ٩(◕‿◕)۶",
    "搞定啦！还有什么需要帮忙的吗？",
    "全部完成了！感觉收获满满呢~",
    "呼~ 顺利完成！我做得还不错吧？",
    "任务结束！希望帮到你啦~ ✨",
]

_PARTIAL_REACTIONS = [
    "嗯，做到这里了，让我想想接下来...",
    "好的，继续下一步！",
    "这一步没问题，我们接着来~",
    "看起来进展不错，继续推进！",
]


def _thinking_prefix() -> str:
    """随机返回一个思考前缀（保持新鲜感）"""
    import random
    return random.choice(_THINKING_PREFIXES)


def _tool_start(tool_name: str) -> str:
    """工具调用的情感化描述"""
    return _TOOL_START_TEMPLATES.get(tool_name, f"正在调用 {tool_name}...")


# ── 文本工具调用解析（DeepSeek 中文格式兼容）──

_FakeTextFunc = namedtuple("_FakeTextFunc", ["name", "arguments"])
_FakeTextTC = namedtuple("_FakeTextTC", ["id", "function"])


def _extract_json_block(content: str, start: int) -> str | None:
    """从 content[start] 开始提取 balanced {} JSON 块"""
    while start < len(content) and content[start] in (' ', '\n', '\r', '\t'):
        start += 1
    if start >= len(content) or content[start] != '{':
        return None
    depth = 0
    for i in range(start, len(content)):
        ch = content[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
    return None


def _parse_text_tool_calls(content: str) -> list | None:
    """解析 [调用 tool_name] {args} 中文文本格式的工具调用。

    返回 _FakeTextTC 列表（兼容 OpenAI ToolCall 接口），或 None。
    """
    if not content or "调用" not in content:
        return None
    call_pattern = re.finditer(r'\[调用\s+(\w+)\]', content)
    calls = []
    for match in call_pattern:
        tool_name = match.group(1)
        json_block = _extract_json_block(content, match.end())
        args = "{}"
        if json_block:
            try:
                parsed = _json.loads(json_block)
                args = _json.dumps(parsed, ensure_ascii=False)
            except (_json.JSONDecodeError, ValueError):
                args = "{}"
        calls.append(_FakeTextTC(
            id=f"txt_{uuid.uuid4().hex[:8]}",
            function=_FakeTextFunc(name=tool_name, arguments=args),
        ))
    return calls if calls else None


def _execute_single_tool(tc, step_num: int, messages: list, ctx: TaskContext):
    """执行单个工具调用并 yield 事件（兼容真实和 _FakeTextTC）"""
    n = next_step_num()
    tool_desc = _tool_start(tc.function.name)
    yield f"[[step_desc:{n}]]步骤{n}: {tool_desc}"
    yield f"[[tool:[{n}] {tc.function.name}({tc.function.arguments})]]"
    try:
        result = execute_mcp_tool(tc)
    except Exception as e:
        result = f"[工具执行失败] {e}"
    preview = result[:200].replace("\n", " ").strip()
    yield f"[[tool_result:{preview}]]"
    ctx.add_step(
        action=f"{tc.function.name}({tc.function.arguments})",
        result=result,
    )
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "content": result,
    })


def _build_planning_prompt(user_message: str) -> str:
    """构建任务规划提示词"""
    return f"""请分析以下用户请求，判断这是一个简单对话还是复杂任务。

如果是简单对话（闲聊、问候、简单问答等），直接回复即可。
如果是复杂任务（需要多个步骤、使用工具等），请按以下格式输出：

[任务分析]
简要说明这个任务需要做什么（1-2句话）

[执行计划]
1. 第一步做什么 → 用什么工具
2. 第二步做什么 → 用什么工具
...
N. 最后一步 → 收尾总结

注意：
- 保持猫娘的语气，像在跟朋友聊天一样
- 计划要具体，每步都说明目标
- 如果任务很简单，不要过度规划"""


def run_agent_workflow(
    messages: list,
    tools: list,
    tool_choice: str,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator[str, None, str]:
    """
    Claude Code 风格的 Agent 工作流。

    三阶段：
      PLAN → EXECUTE → REFLECT

    yield 的事件格式：
      [[plan]]步骤描述            — 规划阶段输出
      [[thinking]]思考文本         — 执行前的停顿思考
      [[step_desc:N]]描述         — 工具调用描述（触发 TTS）
      [[tool:...]]               — 工具详情
      [[tool_result:...]]        — 工具结果
      [[progress]]进度汇报        — 步骤间的进度更新
      [[reflect]]反思文本         — 最终总结

    return → 最终回复文本
    """
    # ═══════════════════════════════════════════
    # Phase 1: PLAN — 分析任务、制定计划
    # ═══════════════════════════════════════════
    ctx.set_phase(AgentPhase.PLAN)

    yield f"[[thinking]]{_thinking_prefix()}"

    # 检查是否已有 plan（从第一轮工具调用推断）
    if ctx.plan_steps:
        # 已有计划步骤，跳过规划直接进入执行
        yield f"[[plan]]好的，我已经有计划了，总共 {len(ctx.plan_steps)} 步，开始执行吧~"
        yield from _execute_phase(messages, tools, tool_choice, ctx, cancel_check)
        return _finalize(messages, tools, ctx, cancel_check)

    # 第一次调用 LLM 获取规划
    planning_prompt = _build_planning_prompt(ctx.original_request)
    planning_msg = messages[-1]["content"]
    # 临时替换最后一条消息为规划提示
    messages[-1]["content"] = f"{planning_msg}\n\n[系统提示]\n{planning_prompt}"

    response = call_model(
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
    )
    msg = response.choices[0].message

    # 恢复原始消息
    messages[-1]["content"] = planning_msg

    # 分析 LLM 响应：是直接回复还是进入工作流
    content = msg.content or ""

    # ── 检测文本格式的工具调用 ──
    text_tc = _parse_text_tool_calls(content)

    if msg.tool_calls:
        # LLM 直接调了工具 → 进入执行阶段（跳过规划）
        ctx.set_phase(AgentPhase.EXECUTE)
        messages.append(msg)
        yield from _execute_tool_calls(messages, msg.tool_calls, ctx, cancel_check)
        yield from _execute_phase(messages, tools, tool_choice, ctx, cancel_check)
        return _finalize(messages, tools, ctx, cancel_check)

    if text_tc:
        # LLM 用了文本格式调工具 → 同样进入执行阶段
        ctx.set_phase(AgentPhase.EXECUTE)
        if content:
            messages.append({"role": "assistant", "content": content})
        yield from _execute_tool_calls(messages, text_tc, ctx, cancel_check)
        yield from _execute_phase(messages, tools, tool_choice, ctx, cancel_check)
        return _finalize(messages, tools, ctx, cancel_check)

    # 检查是否有计划格式
    has_plan = "[执行计划]" in content or "[任务分析]" in content

    if has_plan:
        # 解析并展示计划
        plan_lines = _parse_plan(content)
        ctx.plan_steps = plan_lines

        # 提取任务分析部分（在 [执行计划] 之前的内容）
        analysis_part = content.split("[执行计划]")[0].replace("[任务分析]", "").strip()
        if analysis_part:
            yield f"[[plan]]{analysis_part}"

        # 展示计划步骤
        yield f"[[plan]]我制定了 {len(plan_lines)} 步计划："
        for i, step in enumerate(plan_lines, 1):
            yield f"[[plan]]  {i}. {step}"

        yield f"[[progress]]计划就绪，开始执行！✨"
        ctx.set_phase(AgentPhase.EXECUTE)

        # 用计划指导后续执行（不需要再调用 LLM 做规划）
        yield from _execute_phase(messages, tools, tool_choice, ctx, cancel_check)
        return _finalize(messages, tools, ctx, cancel_check)

    # 没有工具调用也没有计划 → 直接回复（简单对话）
    ctx.set_phase(AgentPhase.DONE)
    return content


def _parse_plan(content: str) -> list[str]:
    """从 LLM 响应中解析执行计划步骤"""
    steps = []

    # 尝试匹配编号列表
    plan_section = content.split("[执行计划]")[-1] if "[执行计划]" in content else content

    for line in plan_section.split("\n"):
        line = line.strip()
        # 匹配 "1. xxx" 或 "1) xxx" 或 "- xxx" 或 "步骤1: xxx"
        m = re.match(r'^(\d+)[\.\)、]\s*(.+)', line)
        if m:
            steps.append(m.group(2).strip())
        elif re.match(r'^[-•*]\s+', line):
            steps.append(re.sub(r'^[-•*]\s+', '', line).strip())

    return steps if steps else [plan_section.strip()[:200]]


def _execute_phase(
    messages: list,
    tools: list,
    tool_choice: str,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator[str, None, None]:
    """
    Phase 2: EXECUTE — 自由循环执行

    每轮可以：思考 → 调工具 → 观察 → 回复用户 → 继续
    直到 LLM 判定任务完成（空回复或连续纯文本）。
    """
    ctx.set_phase(AgentPhase.EXECUTE)
    no_tool_rounds = 0

    for step_num in range(1, MAX_AGENT_STEPS + 1):
        if cancel_check and cancel_check():
            yield "[[cancelled]]用户中断"
            return

        # ── 思考停顿 ──
        if step_num > 1:
            import random
            if random.random() < 0.4:
                yield f"[[thinking]]{random.choice(_PARTIAL_REACTIONS)}"

        # ── 注入进度上下文 ──
        hint = _build_progress_hint(ctx)
        original = messages[-1]["content"] if messages else ""
        if isinstance(original, str) and "[当前进度]" not in original:
            messages[-1]["content"] = f"{original}\n\n[当前进度]\n{hint}"

        # ── 调用 LLM ──
        response = call_model(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        msg = response.choices[0].message
        content = (msg.content or "").strip()

        # ═══ PATH A: Native tool_calls ═══
        if msg.tool_calls:
            no_tool_rounds = 0
            messages.append(msg)
            for tc in msg.tool_calls:
                if cancel_check and cancel_check():
                    yield "[[cancelled]]用户中断"
                    return
                yield from _execute_single_tool(tc, step_num, messages, ctx)
            # 进度汇报（使用全局步骤计数）
            if ctx.plan_steps and ctx.steps:
                yield f"[[progress]]步骤 {_step_counter}/{len(ctx.plan_steps)} 完成"
            elif ctx.steps:
                yield f"[[progress]]步骤 {_step_counter} 完成"
            continue

        # ═══ PATH B: Text-encoded tool calls（[调用 xxx] {args}）═══
        text_tc = _parse_text_tool_calls(content)
        if text_tc:
            no_tool_rounds = 0
            # 保存原始消息到历史（让 LLM 看到上下文）
            if content:
                messages.append({"role": "assistant", "content": content})
            for tc in text_tc:
                if cancel_check and cancel_check():
                    yield "[[cancelled]]用户中断"
                    return
                yield from _execute_single_tool(tc, step_num, messages, ctx)
            if ctx.steps:
                yield f"[[progress]]步骤 {_step_counter} 完成"
            continue

        # ═══ PATH C: 纯文本回复（可能想说话） ═══
        if content:
            # 检查 [TASK_DONE] 标记 — 去掉标记后把前面文字也输出
            if "[TASK_DONE]" in content or "[任务完成]" in content:
                clean_done = re.sub(r'\[TASK_DONE\]|\[任务完成\]', '', content).strip()
                if clean_done:
                    messages.append({"role": "assistant", "content": clean_done})
                    ctx.has_spoken = True
                    yield f"[[speak]]{clean_done}"
                ctx.set_phase(AgentPhase.REFLECT)
                return

            no_tool_rounds += 1

            # 清理文本中残留的内部标记
            clean = re.sub(r'\[调用\s+\w+\]\s*\{[^}]*\}\s*', '', content).strip()
            clean = re.sub(r'\[调用\s+\w+\]', '', clean).strip()
            clean = re.sub(r'\[TASK_DONE\]|\[任务完成\]', '', clean).strip()

            if clean:
                messages.append({"role": "assistant", "content": clean})
                ctx.has_spoken = True
                yield f"[[speak]]{clean}"

            # 连续纯文本 → 给出更多轮次后才判定完成
            if no_tool_rounds >= 4:
                ctx.set_phase(AgentPhase.REFLECT)
                return

            # 每 2 轮纯文本提示 LLM 评估是否完成
            if no_tool_rounds % 2 == 0:
                messages.append({
                    "role": "user",
                    "content": (
                        "（系统检查点）请评估：任务是否已完成？\n"
                        "如果已完成请回复 [TASK_DONE]。\n"
                        "如果还需要执行操作或继续回复，请继续。"
                    ),
                })
            continue

        # ═══ PATH D: 空响应 → 完成 ═══
        ctx.set_phase(AgentPhase.REFLECT)
        return

    ctx.set_phase(AgentPhase.REFLECT)


def _finalize(
    messages: list,
    tools: list,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """根据执行情况决定是否需要 REFLECT 总结"""
    if ctx.has_spoken:
        # 执行过程中已回复过用户，简短收尾即可
        return _reflect_phase(messages, tools, ctx, cancel_check, had_interim_speech=True)
    else:
        # 纯工具执行，需要总结
        return _reflect_phase(messages, tools, ctx, cancel_check, had_interim_speech=False)


def _reflect_phase(
    messages: list,
    tools: list,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
    had_interim_speech: bool = False,
) -> Generator[str, None, str]:
    """
    Phase 3: REFLECT — 检查结果、总结、情感化收尾

    让 LLM 用自然语言总结完成的工作，保持情感和人格。
    """
    ctx.set_phase(AgentPhase.REFLECT)

    if cancel_check and cancel_check():
        return ""

    # 构建反思提示
    if had_interim_speech:
        reflect_prompt = (
            "任务已完成。请用1句简短的话收尾（保持猫娘语气），"
            "不需要总结已做过的内容，自然收尾即可。"
        )
    else:
        reflect_prompt = (
            "你已经完成了上述任务。请用自然、有情感的语气做一个简短总结："
            "告诉用户你做了什么、结果怎么样。"
            "保持猫娘语气，加入适当的情感表达（开心、成就感等）。"
            "不需要再调用工具，直接输出总结即可。"
            "控制在2-3句话以内。"
        )
    messages.append({"role": "user", "content": reflect_prompt})

    response = call_model(
        messages=messages,
        tools=None,
        tool_choice="none",
    )

    # 移除反思提示消息
    messages.pop()

    final_reply = response.choices[0].message.content or ""
    # 清理内部标记，避免泄露给用户
    final_reply = re.sub(r'\[TASK_DONE\]|\[任务完成\]', '', final_reply).strip()

    ctx.set_phase(AgentPhase.DONE)
    return final_reply


def _execute_tool_calls(
    messages: list,
    tool_calls: list,
    ctx: TaskContext,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator[str, None, None]:
    """执行一组工具调用（第一轮 tool_calls）"""
    for tc in tool_calls:
        if cancel_check and cancel_check():
            yield "[[cancelled]]用户中断"
            return

        n = next_step_num()
        tool_desc = _tool_start(tc.function.name)
        yield f"[[step_desc:{n}]]步骤{n}: {tool_desc}"
        yield f"[[tool:[{n}] {tc.function.name}({tc.function.arguments})]]"

        try:
            result = execute_mcp_tool(tc)
        except Exception as e:
            result = f"[工具执行失败] {e}"

        preview = result[:200].replace("\n", " ").strip()
        yield f"[[tool_result:{preview}]]"

        ctx.add_step(
            action=f"{tc.function.name}({tc.function.arguments})",
            result=result,
        )
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })


def _build_progress_hint(ctx: TaskContext) -> str:
    """构建进度提示（注入到 LLM 消息中）"""
    if ctx.is_empty():
        return "请开始执行任务。如果已完成，直接输出最终结果。"

    plan_info = ""
    if ctx.plan_steps:
        total = len(ctx.plan_steps)
        done = len(ctx.steps)
        plan_info = f"\n计划: {done}/{total} 步已完成"
        if done < total:
            plan_info += f"\n接下来: {ctx.plan_steps[done] if done < len(ctx.plan_steps) else '收尾总结'}"

    return (
        f"请根据已完成的操作继续推进。如果全部完成，直接输出最终结果。"
        f"{plan_info}"
        f"\n{ctx.progress_summary()}"
    )


# ── 向后兼容别名 ──
run_agent_loop = run_agent_workflow
