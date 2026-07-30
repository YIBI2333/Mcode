"""POST /emotion — emotion detection endpoint."""
import asyncio
from fastapi import APIRouter, Request

from .shared import _executor, get_pet_state

router = APIRouter()


@router.post("/emotion")
async def detect_emotion(req: Request):
    """Analyze text emotion, return semantic emotion + intensity (LLM first, fallback to defaults)."""
    from src.utils.live2d_constants import EMOTION_NAMES

    body = await req.json()
    text = body.get("text", "")
    if not text:
        return {"emotion": "neutral", "intensity": 0.3}

    emotion, intensity = "neutral", 0.3
    method = "default"

    try:
        from src.infra.live2d_action import get_emotion_and_intensity
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
