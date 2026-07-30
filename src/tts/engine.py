"""TTS 引擎抽象接口"""
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from typing import List


@dataclass
class PhonemeEvent:
    """单个字/音素的时间戳"""
    text: str          # 文字
    start_ms: float    # 开始时间 (ms)
    end_ms: float      # 结束时间 (ms)


@dataclass
class TTSResult:
    """TTS synthesis result."""
    audio_path: str                     # MP3 file path
    duration_ms: float                  # total duration in ms
    phonemes: List[PhonemeEvent] = field(default_factory=list)  # word timestamps


class TTSEngine(ABC):
    """TTS 引擎抽象基类"""

    @abstractmethod
    def synthesize(self, text: str) -> TTSResult:
        """将文本合成为语音，返回带时间戳的结果"""
        ...
