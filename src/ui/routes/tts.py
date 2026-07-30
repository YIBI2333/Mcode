"""POST /tts — TTS generation endpoint."""
import os, sys, asyncio
from fastapi import APIRouter, Request

from .shared import _tts_dir

router = APIRouter()


@router.post("/tts")
async def tts(req: Request):
    """Generate TTS audio (saved to data/tts_cache)."""
    body = await req.json()
    text = body.get("text", "")
    if not text:
        return {"error": "empty text"}

    try:
        from src.tts.edge_tts import EdgeTTSEngine
        engine = EdgeTTSEngine(voice="xiaoyi", rate="-10%", cache_dir=_tts_dir)
        result = await engine._synthesize(text)
        filename = os.path.basename(result.audio_path)

        phonemes = [
            {"t": p.text, "s": round(p.start_ms), "e": round(p.end_ms)}
            for p in result.phonemes
        ]
        try:
            from src.utils.live2d_constants import char_vowel_class
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
