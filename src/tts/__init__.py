"""TTS 模块 — 文本转语音 + 字级时间戳"""
from .engine import TTSEngine, TTSResult, PhonemeEvent
from .edge_tts import EdgeTTSEngine

__all__ = ["TTSEngine", "TTSResult", "PhonemeEvent", "EdgeTTSEngine"]
