"""OpenAI TTS 引擎 — 调用 /v1/audio/speech 端点"""
import os
import sys
import uuid
import asyncio

from openai import OpenAI

from src.agent.tts_engine import TTSEngine, TTSResult, PhonemeEvent

# 可用语音列表
_OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


class OpenAITTSEngine(TTSEngine):
    """基于 OpenAI /v1/audio/speech 的 TTS 引擎。

    也兼容所有 OpenAI 格式的 TTS 代理（如豆包等）。
    """

    def __init__(self, api_key: str, base_url: str, model: str = "tts-1",
                 voice: str = "nova", cache_dir: str = ""):
        self.model = model
        self.voice = voice if voice in _OPENAI_VOICES else "nova"
        self.cache_dir = cache_dir

        if base_url:
            base = base_url.rstrip("/") if not base_url.endswith("/v1") else base_url
            if not base.endswith("/v1"):
                base = base.rstrip("/")
            self._client = OpenAI(api_key=api_key, base_url=base, timeout=30.0, max_retries=1)
        else:
            self._client = OpenAI(api_key=api_key, timeout=30.0, max_retries=1)

    # ------------------------------------------------------------------
    def synthesize(self, text: str) -> TTSResult:
        return _run_async(self._synthesize(text))

    async def _synthesize(self, text: str) -> TTSResult:
        text = text.strip()
        if not text or len(text) < 1:
            return TTSResult(audio_path="", duration_ms=0, phonemes=[])

        save_dir = self.cache_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "tts_cache")
        os.makedirs(save_dir, exist_ok=True)
        mp3_path = os.path.join(save_dir, f"tts_{uuid.uuid4().hex[:8]}.mp3")

        try:
            response = self._client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
            )
            response.stream_to_file(mp3_path)
        except Exception as e:
            _log(f"OpenAI TTS 合成失败: {e.__class__.__name__} — \"{text[:50]}\"")
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            return TTSResult(audio_path="", duration_ms=0, phonemes=[])

        # OpenAI TTS 不返回字级时间戳 → 按字数均分时长估算
        duration_ms = max(500, len(text) * 280)  # ~3.5字/秒
        chars = [c for c in text if '一' <= c <= '鿿' or c.isalnum()]
        if not chars:
            phonemes = []
        else:
            char_dur = duration_ms / len(chars)
            phonemes = [
                PhonemeEvent(text=ch, start_ms=i * char_dur, end_ms=(i + 1) * char_dur)
                for i, ch in enumerate(chars)
            ]

        _log(f"OpenAI TTS 合成: {len(text)}字 → {duration_ms:.0f}ms, model={self.model}, voice={self.voice} → {mp3_path}")
        return TTSResult(
            audio_path=mp3_path,
            duration_ms=duration_ms,
            phonemes=phonemes,
        )


def _run_async(coro):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def _log(msg: str):
    print(f"[TTS] {msg}", file=sys.stderr, flush=True)
