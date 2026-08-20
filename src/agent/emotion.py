"""情绪检测 — 对 AI 回复句子进行情绪分析，供 Live2D 表情动作使用。

设计:
  - 调用 live2d_action.get_emotion_and_intensity（基于 TOOL_LLM）
  - LRU 缓存：相同句子不重复调 LLM
  - 异常安全：任何错误回退到 ("neutral", 0.3)
"""

from functools import lru_cache


@lru_cache(maxsize=128)
def _detect_cached(text: str) -> tuple:
    """缓存版情绪检测，相同文本不重复调 LLM"""
    from src.live2d.action import get_emotion_and_intensity
    return get_emotion_and_intensity(text[:500])


def detect_emotion(text: str) -> tuple:
    """返回 (emotion: str, intensity: float)。异常安全，始终有返回值。

    emotion 取值: happy / sad / angry / surprised / neutral
    intensity: 0.0 ~ 1.0
    """
    if not text or not text.strip():
        return ("neutral", 0.3)
    try:
        return _detect_cached(text.strip())
    except Exception:
        return ("neutral", 0.3)
