"""Token 统计工具"""
import tiktoken

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """计算文本的 token 数量"""
    enc = tiktoken.encoding_for_model(model)
    return len(enc.encode(text))

def print_message_stats(messages: list):
    """打印每条消息的 token 统计）"""
    import sys
    print("\n========== 发送给 AI 的消息 ==========", file=sys.stderr)
    total = 0
    for idx, msg in enumerate(messages):
        content = msg.get("content", "")
        token_count = count_tokens(content)
        total += token_count
        preview = content[:200].replace("\n", " ")
        print(f"[{idx}] {msg['role']} | tokens: {token_count} | preview: {preview}...", file=sys.stderr)
    print(f"输入总 token: {total}", file=sys.stderr)
    print("======================================\n", file=sys.stderr)

def estimate_memory_tokens(user_message: str, full_reply: str, user_id: str = None) -> dict:
    """估算记忆存储的 token 消耗"""
    dialogue_tokens = count_tokens(f"用户: {user_message}\n助手: {full_reply}")
    retrieved_tokens = 0  
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