"""Edge TTS 引擎 — 免费的微软神经网络语音"""
import asyncio
import os
import sys
import uuid

import edge_tts

from src.agent.tts_engine import TTSEngine, TTSResult, PhonemeEvent

# 中文语音列表 (edge-tts --list-voices | grep zh-CN)
_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",        # 活泼女声 — 新闻/小说
    "xiaoyi":   "zh-CN-XiaoyiNeural",           # 俏皮女声 — 动漫/小说
    "yunxi":    "zh-CN-YunxiNeural",            # 阳光男声 — 小说
    "yunjian":  "zh-CN-YunjianNeural",          # 激情男声 — 体育/小说
    "yunxia":   "zh-CN-YunxiaNeural",           # 可爱男声 — 动漫/小说
    "yunyang":  "zh-CN-YunyangNeural",          # 专业男声 — 新闻播报
    "xiaobei":  "zh-CN-liaoning-XiaobeiNeural", # 辽宁方言 — 幽默
    "xiaoni":   "zh-CN-shaanxi-XiaoniNeural",   # 陕西方言 — 爽朗
}


class EdgeTTSEngine(TTSEngine):
    """基于 Edge TTS 的免费中文语音合成"""

    def __init__(self, voice: str = "xiaoxiao", rate: str = "-10%", cache_dir: str = ""):
        """
        Args:
            voice: 语音名称 (xiaoxiao/yunxi/xiaoyi/yunyang/xiaochen)
            rate: 语速调整 ("+10%" 加快, "-10%" 减慢)
            cache_dir: 音频缓存目录（为空则用系统临时目录）
        """
        self.voice = _VOICES.get(voice, voice)
        self.rate = rate
        self.cache_dir = cache_dir

    # ------------------------------------------------------------------
    def synthesize(self, text: str) -> TTSResult:
        return _run_async(self._synthesize(text))

    async def _synthesize(self, text: str) -> TTSResult:
        # 文本预处理：去空白，过滤纯标点/无意义短文本
        text = text.strip()
        if not text or len(text) < 1:
            _log(f"跳过空文本 TTS 合成")
            return TTSResult(audio_path="", duration_ms=0, phonemes=[])

        save_dir = self.cache_dir or os.path.join(os.path.dirname(__file__), "..", "..", "data", "tts_cache")
        os.makedirs(save_dir, exist_ok=True)
        mp3_path = os.path.join(save_dir, f"tts_{uuid.uuid4().hex[:8]}.mp3")

        try:
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate)
            await comm.save(mp3_path)
        except Exception as e:
            _log(f"TTS 合成失败: {e.__class__.__name__} — \"{text[:50]}\"")
            # 清理可能创建的半截文件
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            return TTSResult(audio_path="", duration_ms=0, phonemes=[])

        # 第二步：流式获取字/句边界时间戳
        comm2 = edge_tts.Communicate(text, self.voice, rate=self.rate)
        word_events: list[PhonemeEvent] = []
        sentence_events: list[PhonemeEvent] = []

        try:
            async for chunk in comm2.stream():
                t = chunk["type"]
                if t == "WordBoundary":
                    offset_100ns = chunk["offset"]
                    duration_100ns = chunk["duration"]
                    word_events.append(PhonemeEvent(
                        text=chunk.get("text", ""),
                        start_ms=offset_100ns / 10_000,
                        end_ms=(offset_100ns + duration_100ns) / 10_000,
                    ))
                elif t == "SentenceBoundary":
                    offset_100ns = chunk["offset"]
                    duration_100ns = chunk["duration"]
                    sentence_events.append(PhonemeEvent(
                        text=chunk.get("text", ""),
                        start_ms=offset_100ns / 10_000,
                        end_ms=(offset_100ns + duration_100ns) / 10_000,
                    ))
        except Exception:
            # stream 异常不影响已保存的音频，走回退音素生成
            pass

        # 有词边界就直接用，否则从句子边界按字数均分生成
        if word_events:
            phonemes = word_events
        elif sentence_events:
            phonemes = _split_sentence_to_chars(sentence_events)
        else:
            # 最终回退：按字数均分（假设语速 4字/秒）
            duration_ms = max(500, len(text) * 250)
            phonemes = _make_uniform_phonemes(text, 0, duration_ms)

        duration_ms = phonemes[-1].end_ms if phonemes else 0

        _log(f"TTS 合成: {len(text)}字 → {duration_ms:.0f}ms, {len(phonemes)}音节 → {mp3_path}")
        return TTSResult(
            audio_path=mp3_path,
            duration_ms=duration_ms,
            phonemes=phonemes,
        )


# ------------------------------------------------------------------
def _split_sentence_to_chars(sentences: list[PhonemeEvent]) -> list[PhonemeEvent]:
    """将句子级边界按字数均分为字级音节"""
    result: list[PhonemeEvent] = []
    for sent in sentences:
        text = sent.text.strip()
        if not text:
            continue
        # 只保留中文和非标点字符
        chars = [c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum()]
        if not chars:
            continue
        dur = sent.end_ms - sent.start_ms
        char_dur = dur / len(chars)
        for i, ch in enumerate(chars):
            result.append(PhonemeEvent(
                text=ch,
                start_ms=sent.start_ms + i * char_dur,
                end_ms=sent.start_ms + (i + 1) * char_dur,
            ))
    return result


def _make_uniform_phonemes(text: str, start_ms: float, duration_ms: float) -> list[PhonemeEvent]:
    """按字数均分时间生成音节列表（无边界数据时的回退）"""
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff' or c.isalnum()]
    if not chars:
        return []
    char_dur = duration_ms / len(chars)
    return [
        PhonemeEvent(text=ch, start_ms=start_ms + i * char_dur, end_ms=start_ms + (i + 1) * char_dur)
        for i, ch in enumerate(chars)
    ]


def _run_async(coro):
    """在独立事件循环中运行协程"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def _log(msg: str):
    print(f"[TTS] {msg}", file=sys.stderr, flush=True)
