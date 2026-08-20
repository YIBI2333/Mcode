"""POST /tts — TTS generation endpoint."""
import os, sys, asyncio
from fastapi import APIRouter, Request

from src.server.routes.shared import _tts_dir

router = APIRouter()


@router.post("/tts")
async def tts(req: Request):
    """Generate TTS audio (saved to data/tts_cache)."""
    body = await req.json()
    text = body.get("text", "")
    if not text:
        return {"error": "empty text"}

    try:
        from src.agent.tts_edge import EdgeTTSEngine
        engine = EdgeTTSEngine(voice="xiaoyi", rate="-10%", cache_dir=_tts_dir)
        result = await engine._synthesize(text)
        filename = os.path.basename(result.audio_path)

        phonemes = [
            {"t": p.text, "s": round(p.start_ms), "e": round(p.end_ms)}
            for p in result.phonemes
        ]
        try:
            from src.live2d.constants import char_vowel_class
            for ph in phonemes:
                ph["class"] = char_vowel_class(ph["t"])
        except Exception:
            pass

        return {
            "url": f"/tts-audio/{filename}",
            "duration": round(result.duration_ms),
            "phonemes": phonemes,
        }
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"error": str(e)}
"""POST /emotion — emotion detection endpoint."""
import asyncio
from fastapi import APIRouter, Request

from src.server.routes.shared import _executor, get_pet_state

router = APIRouter()


@router.post("/emotion")
async def detect_emotion(req: Request):
    """Analyze text emotion, return semantic emotion + intensity (LLM first, fallback to defaults)."""
    from src.live2d.constants import EMOTION_NAMES

    body = await req.json()
    text = body.get("text", "")
    if not text:
        return {"emotion": "neutral", "intensity": 0.3}

    emotion, intensity = "neutral", 0.3
    method = "default"

    try:
        from src.live2d.action import get_emotion_and_intensity
        loop = asyncio.get_event_loop()
        emotion, intensity = await loop.run_in_executor(
            _executor, get_emotion_and_intensity, text,
        )
        method = "llm"
    except Exception:
        pass

    if emotion not in EMOTION_NAMES:
        emotion = "neutral"

    ps = get_pet_state()
    ps["emotion"] = emotion
    ps["intensity"] = intensity

    return {"emotion": emotion, "intensity": intensity, "method": method}
