"""
流式输出管线 — 事件模型 + 增量拆句 + 流式 LLM → 句子 → TTS 并行管线

核心组件:
  PipelineEvent / SentenceEvent / ToolEvent / StatusEvent  — 统一事件模型
  StreamingSentenceSplitter — 增量拆句器（复用 sentence_splitter 括号保护逻辑）
  stream_and_split         — 流式 LLM + 实时拆句的生成器

设计原则:
  - 替代 [[tag]]text 字符串协议，用类型化 dataclass
  - 拆句逻辑只此一处，所有路径复用
  - TTS 由上层（SSE route）异步并行处理，本模块只负责文本拆句
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Generator, Callable

from src.agent.splitter import (
    SENTENCE_DELIMITERS,
    _OPEN_BRACKETS,
    _CLOSE_BRACKETS,
    _CN_CLOSE_BRACKETS,
)


# ═══════════════════════════════════════════════════════════════
# 事件模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class PipelineEvent:
    """事件基类"""
    pass


@dataclass
class SentenceEvent(PipelineEvent):
    """正文句子 — 上层负责填充 TTS 数据后发送 SSE"""
    index: int
    text: str
    # TTS 字段由上层异步填充
    tts_url: str = ""
    duration_ms: int = 0
    phonemes: list = field(default_factory=list)
    emotion: str = "neutral"
    intensity: float = 0.3

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "tts_url": self.tts_url,
            "duration_ms": self.duration_ms,
            "phonemes": self.phonemes,
            "emotion": self.emotion,
            "intensity": self.intensity,
        }


@dataclass
class ToolEvent(PipelineEvent):
    """工具调用/结果

    meta 可选结构:
      call:   {"tool": name, "args": 参数摘要}
      result: {"file_op": {"op": "edit|create|delete|move", "file": path,
                           "added": n, "deleted": n}}
    """
    tag: str   # "call" | "result"
    text: str
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"tag": self.tag, "text": self.text}
        if self.meta:
            d["meta"] = self.meta
        return d


@dataclass
class StatusEvent(PipelineEvent):
    """状态消息（thinking/plan/progress/reflect/error/cancelled/task-start/task-end）

    task-end meta: {"files_total": n, "added": a, "deleted": d,
                    "changed_files": {path: {op, added, deleted}}}
    """
    type: str
    text: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"type": self.type, "text": self.text}
        if self.meta:
            d["meta"] = self.meta
        return d

@dataclass
class UserPromptEvent(PipelineEvent):
    """Agent 需要用户输入 → 暂停执行，等待用户回复"""
    question: str
    session_id: str = ""

    def to_dict(self) -> dict:
        return {"type": "user-prompt", "question": self.question, "session_id": self.session_id}

class AgentPause(Exception):
    """Agent 工具执行中需要暂停等待用户"""
    def __init__(self, question: str):
        self.question = question


# ═══════════════════════════════════════════════════════════════
# 增量拆句器
# ═══════════════════════════════════════════════════════════════

class StreamingSentenceSplitter:
    """增量拆句器 — 接收流式 chunk，积累 buffer，按句子边界 yield 完整句子。

    与 split_full_text() 使用相同的括号保护逻辑：
      - 括号内的分隔符不拆句
      - 句末中文全角闭合括号跟随前句
      - 代码块 (```)、表格 (|...|) 保持完整

    用法:
        splitter = StreamingSentenceSplitter()
        for chunk in llm_stream:
            for sentence in splitter.feed(chunk):
                print(sentence)
        for sentence in splitter.flush():
            print(sentence)
    """

    # 用于检测代码块开始的模式（``` 或 ~~~）
    _FENCE_START = re.compile(r'```|~~~')

    def __init__(self):
        self._buf = ""
        self._fence_delim = ""     # 当前代码块分隔符（空=不在代码块内）
        self._fence_buf = ""       # 代码块内容缓冲

    def feed(self, chunk: str) -> Generator[str, None, None]:
        """喂入一个文本 chunk，yield 所有完整句子。"""
        self._buf += chunk
        yield from self._drain()

    def flush(self) -> Generator[str, None, None]:
        """流结束后调用，yield 所有剩余内容。"""
        # 关闭未完成的代码块
        if self._fence_delim:
            self._buf = self._fence_delim + self._fence_buf + self._buf
            self._fence_delim = ""
            self._fence_buf = ""
        yield from self._drain()
        # 剩余文本
        remaining = self._buf.strip()
        if remaining:
            # 末尾是表格块 → 加 [MD] 前缀（跳过 TTS，前端整体渲染）
            if re.match(r'^\s*\|.+\|\s*$', remaining.split('\n')[0]):
                remaining = f"[MD]{remaining}"
            yield remaining
        self._buf = ""

    # ── 内部 ──

    def _drain(self) -> Generator[str, None, None]:
        """从 buffer 中尽可能多地切出完整句子。"""
        while True:
            # 如果在代码块内，等待结束
            if self._fence_delim:
                end_idx = self._buf.find(self._fence_delim)
                if end_idx == -1:
                    self._fence_buf += self._buf
                    self._buf = ""
                    return
                # 找到代码块结束
                code_block = self._fence_delim + self._fence_buf + self._buf[:end_idx + len(self._fence_delim)]
                self._fence_delim = ""
                self._fence_buf = ""
                self._buf = self._buf[end_idx + len(self._fence_delim):]
                yield f"[MD]{code_block}"
                continue

            # 检测代码块开始
            fm = self._FENCE_START.search(self._buf)
            if fm:
                # 检查：分隔符是否在行首或前面是换行
                pos = fm.start()
                if pos == 0 or self._buf[pos - 1] == '\n':
                    # 在代码块之前的内容先拆句
                    before = self._buf[:pos]
                    self._buf = self._buf[pos:]
                    if before.strip():
                        # 临时替换 buffer，递归处理前半部分
                        saved = self._buf
                        self._buf = before
                        yield from self._drain()
                        self._buf = saved + self._buf
                        continue
                    # 开始代码块
                    self._fence_delim = fm.group()
                    self._fence_buf = self._buf[len(self._fence_delim):]
                    self._buf = ""
                    return

            # 检测表格块（buffer 开头是表格行时）
            table_end = self._detect_table()
            if table_end is None:
                return  # 表格还没结束，等更多 chunk
            if table_end > 0:
                table_block = self._buf[:table_end].strip()
                self._buf = self._buf[table_end:]
                yield f"[MD]{table_block}"
                continue

            # 正常拆句：扫描分隔符
            split_at = -1
            depth = 0
            for i, ch in enumerate(self._buf):
                if ch in _OPEN_BRACKETS:
                    depth += 1
                elif ch in _CLOSE_BRACKETS:
                    depth = max(0, depth - 1)
                elif ch in SENTENCE_DELIMITERS and depth <= 0:
                    split_at = i
                    break

            if split_at < 0:
                return  # 没有完整句子，等更多 chunk

            # 切出句子（包含句末闭合括号）
            end = split_at + 1
            while end < len(self._buf) and self._buf[end] in _CN_CLOSE_BRACKETS:
                end += 1
            sentence = self._buf[:end].rstrip()
            self._buf = self._buf[end:]
            if sentence:
                yield sentence

    def _detect_table(self) -> int | None:
        """检测 buffer 开头是否为表格块。

        返回:
          -1  — buffer 开头不是表格行（走正常拆句）
          None — 表格尚未结束（buffer 全是表格行，等待更多 chunk）
          >0  — 完整表格块的结束位置
        """
        if not self._buf:
            return -1
        lines = self._buf.split('\n')
        # 去掉末尾空串（split('\n') 在末尾换行时产生）
        while lines and lines[-1] == '':
            lines.pop()
        if not lines or not re.match(r'^\s*\|.+\|\s*$', lines[0]):
            return -1  # 开头不是表格行
        # 找连续表格行结束
        end = 0
        while end < len(lines):
            line = lines[end]
            if re.match(r'^\s*\|.+\|\s*$', line):
                end += 1
                continue
            # 以 | 开头但未以 | 结尾 → 这一行还没接收完整，等待更多 chunk
            if re.match(r'^\s*\|', line):
                return None
            break
        if end >= len(lines):
            return None  # 全是完整表格行，表格可能还没结束
        table_text = '\n'.join(lines[:end])
        return len(table_text)


# ═══════════════════════════════════════════════════════════════
# 流式 LLM + 拆句 生成器
# ═══════════════════════════════════════════════════════════════

def stream_and_split(
    stream_func: Callable,
    messages: list,
    tools: list = None,
    tool_choice: str = "auto",
    cancel_check: Callable[[], bool] | None = None,
    start_index: int = 0,
) -> Generator[PipelineEvent, None, int]:
    """流式调用 LLM，实时拆句，yield SentenceEvent。

    返回最终 index（供后续句子编号连续使用）。

    Args:
        stream_func: call_model_stream_str 或兼容的流式函数
        messages: LLM 消息列表
        tools: 工具 schema
        tool_choice: 工具选择策略
        cancel_check: 取消检查回调
        start_index: 起始句子编号

    Yields:
        SentenceEvent — 每个完整句子（无 TTS 数据，由上层填充）
    """
    splitter = StreamingSentenceSplitter()
    idx = start_index

    for chunk in stream_func(messages, tools=tools, tool_choice=tool_choice):
        if cancel_check and cancel_check():
            yield StatusEvent(type="cancelled", text="用户中断")
            return idx

        for sentence_text in splitter.feed(chunk):
            text = sentence_text.strip()
            if not text:
                continue
            # 跳过纯空白/分隔符
            if text in ('。', '！', '？', '!', '?', '；', ';', '~', '…'):
                continue
            yield SentenceEvent(index=idx, text=text)
            idx += 1

    # flush 剩余
    for sentence_text in splitter.flush():
        text = sentence_text.strip()
        if not text or text in ('。', '！', '？', '!', '?', '；', ';', '~', '…'):
            continue
        yield SentenceEvent(index=idx, text=text)
        idx += 1

    return idx
