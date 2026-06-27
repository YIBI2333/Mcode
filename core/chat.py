"""对话生成核心逻辑"""
import sys
import threading
import tiktoken
from openai import OpenAI

import api_config
from config import SHORT_TERM_HISTORY_LENGTH, ENABLE_TOOLS, TOOL_CHOICE
from core.memory import retrieve_context, get_memory

from tools.mcp_tools import get_tools_schema, execute_mcp_tool



# ---------- 客户端配置 ----------
chat_client = OpenAI(
    api_key=api_config.CHAT_LLM_KEY,
    base_url=api_config.CHAT_LLM_BASE_URL,
)

# ---------- 短期对话历史（全局状态） ----------
conversation_history = []

# ---------- 辅助函数 ----------
def build_messages(user_id: str, user_message: str) -> list:
    """构建发送给 LLM 的 messages 列表"""
    long_term_context = retrieve_context(user_id, user_message)
    system_prompt = (
        "你是用户的私人助手。"
        "请根据历史记忆和对话回答。"
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history[-SHORT_TERM_HISTORY_LENGTH:])
    augmented_user_message = f"[相关记忆]\n{long_term_context}\n\n[用户输入]\n{user_message}"
    messages.append({"role": "user", "content": augmented_user_message})
    return messages

def print_message_stats(messages: list):
    """打印每条消息的 token 统计（调试用）"""
    print("\n========== 发送给 AI 的消息 ==========", file=sys.stderr)
    enc = tiktoken.encoding_for_model("gpt-4")
    total = 0
    for idx, msg in enumerate(messages):
        content = msg.get("content", "")
        token_count = len(enc.encode(content))
        total += token_count
        preview = content[:200].replace("\n", " ")
        print(f"[{idx}] {msg['role']} | tokens: {token_count} | preview: {preview}...", file=sys.stderr)
    print(f"输入总 token: {total}", file=sys.stderr)
    print("======================================\n", file=sys.stderr)

def stream_response(messages: list):
    """发起流式请求并逐块返回内容，同时返回 usage 和完整回复"""
    stream = chat_client.chat.completions.create(
        model=api_config.CHAT_LLM_MODEL,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
    )
    full_reply = ""
    usage = None
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content is not None:
            content = chunk.choices[0].delta.content
            full_reply += content
            yield content
        if hasattr(chunk, 'usage') and chunk.usage:
            usage = chunk.usage
    # 返回 usage 和完整回复（生成器无法直接返回，用 StopIteration 传递）
    # 这里我们通过生成器外的变量来处理，所以改为在外部收集
    # 但为了保持生成器简单，我们只 yield content，在外部收集 full_reply 和 usage
    # 这里我们不返回，由外部处理

def update_history(user_message: str, full_reply: str):
    """更新短期对话历史"""
    conversation_history.append({"role": "user", "content": user_message})
    conversation_history.append({"role": "assistant", "content": full_reply})

def estimate_memory_tokens(user_message: str, full_reply: str, user_id: str) -> dict:
    """估算 memory.add 的 token 消耗（仅供参考）"""
    memory = get_memory()
    results = memory.search(user_message, filters={"user_id": user_id}, limit=1)
    retrieved_text = ""
    if results.get("results"):
        for r in results["results"]:
            retrieved_text += r["memory"] + "\n"

    enc = tiktoken.encoding_for_model("gpt-4")
    dialogue_tokens = len(enc.encode(f"用户: {user_message}\n助手: {full_reply}"))
    retrieved_tokens = len(enc.encode(retrieved_text))
    system_instruction_estimate = 2500
    format_instruction_estimate = 800
    total_estimate = dialogue_tokens + retrieved_tokens + system_instruction_estimate + format_instruction_estimate
    return {
        "dialogue": dialogue_tokens,
        "retrieved": retrieved_tokens,
        "system_instruction": system_instruction_estimate,
        "format_instruction": format_instruction_estimate,
        "total": total_estimate,
    }

def store_memory_async(user_id: str, user_message: str, full_reply: str):
    """异步存储长期记忆（后台线程）"""
    def _store():
        try:
            # 估算并打印
            # est = estimate_memory_tokens(user_message, full_reply, user_id)
            # print(f"\n[记忆提取token估算] 对话: {est['dialogue']}, 已有记忆: {est['retrieved']}, 系统指令(预估): {est['system_instruction']}, 格式指令(预估): {est['format_instruction']}, 总计约: {est['total']} token", file=sys.stderr)

            memory = get_memory()
            memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_reply},
                ],
                user_id=user_id,
            )
        except Exception as e:
            print(f"\n[记忆存储失败] {e}", file=sys.stderr)

    threading.Thread(target=_store, daemon=True).start()

# ---------- 主入口 ----------
def generate_reply(user_id: str, user_message: str):
    global conversation_history
    
    messages = build_messages(user_id, user_message)

    # 如果启用工具，则进行工具调用判断
    if ENABLE_TOOLS:
        tools_schema = get_tools_schema()
        tool_response = chat_client.chat.completions.create(
            model=api_config.CHAT_LLM_MODEL,
            messages=messages,
            tools=tools_schema,
            tool_choice=TOOL_CHOICE,   # 从 config 读取
            stream=False,
        )
        assistant_message = tool_response.choices[0].message

        if assistant_message.tool_calls:
            messages.append(assistant_message)
            for tool_call in assistant_message.tool_calls:
                result = execute_mcp_tool(tool_call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })

    # 3. 流式请求（最终回复）
    stream = chat_client.chat.completions.create(
        model=api_config.CHAT_LLM_MODEL,
        messages=messages,
        stream=True,
        stream_options={"include_usage": True},
    )

    full_reply = ""
    usage = None
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content is not None:
            content = chunk.choices[0].delta.content
            full_reply += content
            yield content
        if hasattr(chunk, 'usage') and chunk.usage:
            usage = chunk.usage

    # 打印Token消耗情况
    # if usage:
    #     print(f"\n[Token消耗] 输入: {usage.prompt_tokens}, 输出: {usage.completion_tokens}, 总计: {usage.total_tokens}", file=sys.stderr)

    # 4. 更新历史
    conversation_history.append({"role": "user", "content": user_message})
    conversation_history.append({"role": "assistant", "content": full_reply})

    # 5. 异步存储记忆
    store_memory_async(user_id, user_message, full_reply)