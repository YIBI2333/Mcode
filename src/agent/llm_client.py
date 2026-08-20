"""API 客户端封装"""
import time
import sys
from openai import OpenAI
import config.api_config as api_config

# 客户端缓存：key = (base_url, api_key)，避免每次请求都新建 OpenAI 对象
_client_cache = {}


def _get_client(base_url: str, api_key: str) -> OpenAI:
    """按当前配置动态获取 client（带缓存）。
    这样 UI 保存配置后，下一次调用会使用新的服务商/key，无需重启。"""
    key = (base_url, api_key)
    c = _client_cache.get(key)
    if c is None:
        # OpenCode 网关要求附加 x-opencode-session 头
        extra_headers = api_config.opencode_headers(base_url)
        c = OpenAI(api_key=api_key, base_url=base_url,
                   default_headers=extra_headers or None)
        _client_cache[key] = c
    return c


def _is_retryable(err: Exception) -> bool:
    """判断是否可重试的错误（429 限流/503/超时等）"""
    msg = str(err)
    return any(k in msg for k in ('429', '503', '502', '504', 'overloaded', 'unavailable', 'timeout', 'timed out', 'rate-limited', 'rate limit'))


def call_model(messages: list, tools: list = None, tool_choice: str = "auto",
               stream: bool = False, max_tokens: int = 4096):
    """调用 LLM，stream=False 返回完整响应，stream=True 返回流。
    max_tokens 默认 4096，避免 OpenRouter 等按模型最大值申请导致低额度账号 402。
    实时读取 config.api_config 当前值，支持 UI 保存配置后不重启即切换。"""
    last_err = None
    for attempt in range(3):
        try:
            client = _get_client(api_config.CHAT_LLM_BASE_URL, api_config.CHAT_LLM_KEY)
            # OpenRouter 等部分服务商拒绝 stream_options=null，故非流式时不传该字段
            kwargs = dict(
                model=api_config.CHAT_LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=stream,
                temperature=0.5,
                max_tokens=max_tokens,
                extra_body={"reasoning": None},
            )
            if stream:
                kwargs["stream_options"] = {"include_usage": True}
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            last_err = e
            if attempt < 2 and _is_retryable(e):
                wait = (attempt + 1) * 2
                print(f"[client] 调用失败，{wait}s 后重试 ({attempt+1}/3): {e}", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            raise
    raise last_err


def call_model_stream_str(messages: list, tools: list = None, tool_choice: str = "auto",
                          max_tokens: int = 4096):
    """流式调用 LLM，逐 chunk yield 文本（自动跳过 tool_calls 和空内容）。
    max_tokens 默认 4096，避免 OpenRouter 等按模型最大值申请导致低额度账号 402。
    实时读取 config.api_config 当前值，支持 UI 保存配置后不重启即切换。"""
    last_err = None
    for attempt in range(3):
        try:
            client = _get_client(api_config.CHAT_LLM_BASE_URL, api_config.CHAT_LLM_KEY)
            stream = client.chat.completions.create(
                model=api_config.CHAT_LLM_MODEL,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
                temperature=0.5,
                max_tokens=max_tokens,
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
