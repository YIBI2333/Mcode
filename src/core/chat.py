"""对话生成主流程 — AI 自主决策 + Claude Code 风格 Agent

标记协议 (yield 给上层 web.py 的事件化流)：
  [[thinking]]文本          — Agent 思考停顿（显示但不读）
  [[plan]]文本              — 规划/计划展示
  [[progress]]文本          — 步骤间进度汇报
  [[reflect]]文本           — 最终反思总结
  [[step_desc:N]]描述文本   — 工具步骤的可朗读/显示描述（触发 TTS）
  [[tool:step_desc]]         — 工具调用详情（折叠区，不触发 TTS）
  [[tool_result:preview]]    — 工具返回结果（折叠区，不触发 TTS）
  [[reply_start]]            — AI 回复正文开始
  [[sentence:N]]文本          — 第 N 句正文（触发 TTS）
  [[end]]                    — 整轮对话结束

设计理念：
  - 每条 [[step_desc:N]] 和 [[sentence:N]] 都会在对话气泡中显示，并生成对应 TTS 语音
  - TTS 逐句生成、逐句播放：前一句播放时，后一句的 TTS 已在后台生成
  - [[tool:]] / [[tool_result:]] 仅用于折叠区显示技术细节，不触发语音
"""
import re
import json as _json
import sys
import logging
from src.infra.client import call_model, call_model_stream_str
from src.infra.messages import build_messages
from src.core.memory import store_memory_async
from src.tools.mcp_tools import get_tools_schema, execute_mcp_tool
from src.core.agent import run_agent_loop, reset_step_counter, next_step_num
from src.core.state import TaskContext
from src.core.sentence_splitter import split_full_text, SENTENCE_DELIMITERS, _OPEN_BRACKETS, _CLOSE_BRACKETS, _CN_CLOSE_BRACKETS
from src.core.tool_desc import step_desc
from config.settings import ENABLE_TOOLS, TOOL_CHOICE, ENABLE_AGENT_MODE, ENABLE_WEB_LIVE2D

_log = logging.getLogger(__name__)

_tools_schema_cache = None

# 全局 TTS 索引（跨 step_desc 和 sentence 统一编号，保证前端顺序播放）
_desc_idx = 0


# ── 向后兼容别名 ──
_step_desc = step_desc
_split_full_text = split_full_text


def _reset_desc_idx():
    """每次新对话重置描述索引"""
    global _desc_idx
    _desc_idx = 0


def _next_desc_idx() -> int:
    global _desc_idx
    idx = _desc_idx
    _desc_idx += 1
    return idx


def _get_tools_schema():
    """获取工具 schema（空结果不缓存，下次重试）"""
    global _tools_schema_cache
    if _tools_schema_cache:
        return _tools_schema_cache
    tools = get_tools_schema()
    if tools:
        _tools_schema_cache = tools
    return tools


def generate_reply(user_id: str, user_message: str, cancel_check=None, image_b64: str = None, image_mime: str = None):
    _reset_desc_idx()
    reset_step_counter()
    try:
        yield from _generate_reply_impl(user_id, user_message, cancel_check=cancel_check, image_b64=image_b64, image_mime=image_mime)
    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"[[error]]LLM 调用失败: {e}"


def _stream_yield_sentences(stream_str_func, messages, tools, tool_choice, reply_holder, cancel_check=None):
    """流式调用 LLM 并实时拆句 yield（括号内的分隔符不拆句）"""
    buf = ""
    for chunk in stream_str_func(messages, tools=tools, tool_choice=tool_choice):
        # 流式输出期间检查取消
        if cancel_check and cancel_check():
            yield "[[cancelled]]用户中断"
            return
        buf += chunk
        # 每当缓冲区有句子分隔符（且不在括号内），就切出完整句子
        while True:
            split_at = -1
            depth = 0
            for i, ch in enumerate(buf):
                if ch in _OPEN_BRACKETS:
                    depth += 1
                elif ch in _CLOSE_BRACKETS:
                    depth = max(0, depth - 1)
                elif ch in SENTENCE_DELIMITERS and depth <= 0:
                    split_at = i
                    break
            if split_at < 0:
                break
            # 句末中文全角闭合括号一并包含进来
            end = split_at + 1
            while end < len(buf) and buf[end] in _CN_CLOSE_BRACKETS:
                end += 1
            sent = buf[:end].strip()
            buf = buf[end:]
            if sent:
                yield f"[[sentence:{_next_desc_idx()}]]{sent}"
                reply_holder[0] += sent
    # 剩余文本
    remaining = buf.strip()
    if remaining:
        yield f"[[sentence:{_next_desc_idx()}]]{remaining}"
        reply_holder[0] += remaining


def _generate_reply_impl(user_id: str, user_message: str, cancel_check=None, image_b64: str = None, image_mime: str = None):
    tools = _get_tools_schema() if ENABLE_TOOLS else None
    tool_choice = TOOL_CHOICE if ENABLE_TOOLS else "none"

    messages = build_messages(user_id, user_message, image_b64=image_b64, image_mime=image_mime)
    full_reply = ""

    # 首次调用：非流式，检测是否需要工具
    response = call_model(messages=messages, tools=tools, tool_choice=tool_choice, stream=False)
    msg = response.choices[0].message

    if msg.tool_calls:
        # ===== 路径 A：有工具调用 → 工作流模式 =====
        messages.append(msg)
        first_results = []
        for tc in msg.tool_calls:
            if cancel_check and cancel_check():
                yield "[[cancelled]]用户中断"
                return
            n = next_step_num()
            yield f"[[step_desc:{n}]]步骤{n}: {_step_desc(tc.function.name, tc.function.arguments)}"
            yield f"[[tool:[{n}] {tc.function.name}({tc.function.arguments})]]"
            result = execute_mcp_tool(tc)
            preview = result[:200].replace("\n", " ").strip()
            yield f"[[tool_result:{preview}]]"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            first_results.append((tc, result))

        if ENABLE_AGENT_MODE:
            ctx = TaskContext(original_request=user_message)
            for tc, result in first_results:
                ctx.add_step(action=f"{tc.function.name}({tc.function.arguments})", result=result)
            agent_gen = run_agent_loop(messages, tools, tool_choice, ctx, cancel_check=cancel_check)
            try:
                while True:
                    progress = next(agent_gen)
                    if progress:
                        stripped = progress.strip()
                        if not stripped:
                            continue
                        # ── 中间回复（speak）→ 直接透传给 SSE route 即时处理 ──
                        if stripped.startswith("[[speak]]"):
                            yield stripped
                            full_reply += stripped[len("[[speak]]"):]
                            continue
                        # 新事件类型 → 直接透传
                        if stripped.startswith("[[thinking]]") or \
                           stripped.startswith("[[plan]]") or \
                           stripped.startswith("[[progress]]") or \
                           stripped.startswith("[[reflect]]") or \
                           stripped.startswith("[[cancelled]]"):
                            yield stripped
                            continue
                        # 工具调用 → 包装为 step_desc
                        if stripped.startswith("[") and "调用" in stripped:
                            m = re.search(r'调用\s+(\w+)', stripped)
                            tool_name = m.group(1) if m else ""
                            n = next_step_num()
                            yield f"[[step_desc:{n}]]步骤{n}: {_step_desc(tool_name)}"
                            yield f"[[tool:{stripped}]]"
                        elif stripped.startswith("→"):
                            yield f"[[tool_result:{stripped}]]"
                        else:
                            yield stripped + "\n"
            except StopIteration as e:
                full_reply = e.value or ""
            # Agent 模式拿到完整回复后拆句
            if full_reply and not full_reply.startswith("[["):
                yield "[[reply_start]]"
                sentences = _split_full_text(full_reply)
                for sent in sentences:
                    yield f"[[sentence:{_next_desc_idx()}]]{sent}"
        else:
            # 非 Agent 多轮：最多 3 轮，最后一轮流式实时拆句
            for turn in range(3):
                if cancel_check and cancel_check():
                    yield "[[cancelled]]用户中断"
                    return
                is_final = (turn == 2)
                if is_final:
                    yield "[[reply_start]]"
                    rp = [""]
                    for sent_chunk in _stream_yield_sentences(
                        call_model_stream_str, messages, tools=tools,
                        tool_choice=tool_choice, reply_holder=rp, cancel_check=cancel_check
                    ):
                        yield sent_chunk
                    full_reply = rp[0]
                    break
                else:
                    response = call_model(messages=messages, tools=tools, tool_choice=tool_choice, stream=False)
                    msg2 = response.choices[0].message
                    if msg2.tool_calls:
                        messages.append(msg2)
                        for tc in msg2.tool_calls:
                            if cancel_check and cancel_check():
                                yield "[[cancelled]]用户中断"
                                return
                            n = next_step_num()
                            yield f"[[step_desc:{n}]]步骤{n}: {_step_desc(tc.function.name, tc.function.arguments)}"
                            yield f"[[tool:[{n}] {tc.function.name}({tc.function.arguments})]]"
                            result = execute_mcp_tool(tc)
                            preview = result[:200].replace("\n", " ").strip()
                            yield f"[[tool_result:{preview}]]"
                            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                        continue
                    full_reply = msg2.content or ""
                    break
    else:
        # ===== 路径 B：无工具调用 → 流式实时拆句 =====
        yield "[[reply_start]]"
        rp = [""]
        for sent_chunk in _stream_yield_sentences(
            call_model_stream_str, messages, tools=None,
            tool_choice="none", reply_holder=rp, cancel_check=cancel_check
        ):
            yield sent_chunk
        full_reply = rp[0]

    # 取消后不保存历史
    if cancel_check and cancel_check():
        return

    # ===== 原始回复打印 =====
    if full_reply:
        print(f"\n[AI 原始回复]\n{full_reply}\n", flush=True)

    # ===== 统一出口：历史记录 =====
    if full_reply:
        # 历史写入由 web.py 在 SSE 流结束后按句完成
        store_memory_async(user_id, user_message, full_reply)

        # ── 自动情绪检测 + 应用到 Live2D ──
        try:
            emotion, intensity = "neutral", 0.3
            # 通知 Web Live2D
            if ENABLE_WEB_LIVE2D:
                import urllib.request
                body = _json.dumps({"type": "emotion", "emotion": emotion, "intensity": intensity}).encode()
                try:
                    urllib.request.urlopen(
                        urllib.request.Request("http://localhost:8866/pet/command", data=body,
                                               headers={"Content-Type": "application/json"}),
                        timeout=2
                    )
                except Exception:
                    _log.debug("Live2D emotion notify failed", exc_info=True)
        except Exception:
            _log.debug("情感检测或通知失败", exc_info=True)
