"""API 客户端封装"""
import time
import sys
from openai import OpenAI
import config.api_config as api_config

client = OpenAI(
    api_key=api_config.CHAT_LLM_KEY,
    base_url=api_config.CHAT_LLM_BASE_URL,
)


def _is_retryable(err: Exception) -> bool:
    """判断是否可重试的错误（503/超时等）"""
    msg = str(err)
    return any(k in msg for k in ('503', '502', '504', 'overloaded', 'unavailable', 'timeout', 'timed out'))


def call_model(messages: list, tools: list = None, tool_choice: str = "auto", stream: bool = False):
    """调用 LLM，stream=False 返回完整响应，stream=True 返回流"""
    last_err = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(
                model=api_config.CHAT_LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=stream,
                temperature=0.5,
                stream_options={"include_usage": True} if stream else None,
                extra_body={"reasoning": None},
            )
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_retryable(e):
                wait = (attempt + 1) * 2
                print(f"[client] 调用失败，{wait}s 后重试 ({attempt+1}/3): {e}", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise
    raise last_err


def call_model_stream_str(messages: list, tools: list = None, tool_choice: str = "auto"):
    """流式调用 LLM，逐 chunk yield 文本（自动跳过 tool_calls 和空内容）"""
    last_err = None
    for attempt in range(3):
        try:
            stream = client.chat.completions.create(
                model=api_config.CHAT_LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
                temperature=0.5,
                stream_options={"include_usage": True},
                extra_body={"reasoning": None},
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
            return
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_retryable(e):
                wait = (attempt + 1) * 2
                print(f"[client] 流式调用失败，{wait}s 后重试 ({attempt+1}/3): {e}", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise
    raise last_err
