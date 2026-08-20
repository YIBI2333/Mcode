"""GET /pet/status, POST /pet/command, GET /pet/presets."""
from fastapi import APIRouter, Request

from src.server.routes.shared import get_pet_state, pet_notify_speak

router = APIRouter()


@router.get("/pet/presets")
async def pet_presets():
    """Return emotion configs, vowel classification, and motion presets for frontend."""
    from src.live2d.constants import (
        EMOTION_CONFIG, EMOTION_NAMES, INTENSITY_LEVELS, char_vowel_class,
        MOTION_PRESETS, VOWEL_MOUTH_OPEN, VOWEL_MOUTH_FORM,
    )
    emotions = {em: EMOTION_CONFIG.get(em, {}) for em in EMOTION_NAMES}
    vowel_sample = {}
    for ch in "hello world test char sample":
        vowel_sample[ch] = char_vowel_class(ch)
    return {
        "emotions": emotions,
        "emotion_names": EMOTION_NAMES,
        "intensity_levels": INTENSITY_LEVELS,
        "vowel_sample": vowel_sample,
        "vowel_mouth_open": VOWEL_MOUTH_OPEN,
        "vowel_mouth_form": VOWEL_MOUTH_FORM,
        "motion_presets": MOTION_PRESETS,
    }


@router.get("/pet/status")
async def pet_status():
    """Desktop polling endpoint — returns current emotion and lip-sync state."""
    return get_pet_state()


@router.post("/pet/command")
async def pet_command(req: Request):
    """Unified pet control endpoint.

    Body:
      { type: "emotion", emotion: "happy", intensity: 0.8 }
      { type: "speak", duration: 3000, audio_url: "...", phonemes: [...] }
      { type: "motion", emotion: "happy", intensity: 0.8 }
    """
    body = await req.json()
    cmd_type = body.get("type", "")
    ps = get_pet_state()

    if cmd_type == "emotion":
        em = body.get("emotion", "neutral")
        it = float(body.get("intensity", 0.5))
        ps["emotion"] = em
        ps["intensity"] = it
        ps["_pending_motion"] = em
        return {"ok": True, "emotion": em, "intensity": it, "motion": em}

    elif cmd_type == "speak":
        dur = int(body.get("duration", 0) or 0)
        url = body.get("audio_url", "")
        phonemes = body.get("phonemes") or []
        em = body.get("emotion", "")
        if em:
            ps["emotion"] = em
            ps["intensity"] = float(body.get("intensity", 0.5))
            ps["_pending_motion"] = em
        pet_notify_speak(dur, url, phonemes)
        return {"ok": True, "speak_duration": dur}

    elif cmd_type == "motion":
        em = body.get("emotion", body.get("name", "neutral"))
        it = float(body.get("intensity", 0.5))
        ps["emotion"] = em
        ps["intensity"] = it
        ps["_pending_motion"] = em
        return {"ok": True, "emotion": em, "intensity": it, "motion": em}

    return {"error": f"unknown command type: {cmd_type}"}
