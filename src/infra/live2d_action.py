"""动作模型 — AI 只输出情绪+强度，本地预设库负责参数"""
import json
from openai import OpenAI
import config.api_config as cfg

_client = OpenAI(api_key=cfg.TOOL_LLM_KEY, base_url=cfg.TOOL_LLM_BASE_URL)

PROMPT = """分析AI回复的情绪。只输出JSON，不要解释：
{"emotion":"happy/sad/angry/surprised/neutral","intensity":0.0~1.0}
- intensity: 0=微弱 0.5=正常 1.0=强烈
- 开心/夸奖/欢迎→happy, 难过/道歉→sad, 生气/拒绝→angry, 惊讶→surprised, 普通→neutral"""


def get_emotion_and_intensity(ai_reply: str) -> tuple:
    """返回 (emotion_str, intensity_float)"""
    resp = _client.chat.completions.create(
        model=cfg.TOOL_LLM_MODEL,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": ai_reply[:500]},
        ],
        temperature=0.2,
        max_tokens=80,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("\n", 1)[0]
    data = json.loads(raw)
    return data.get("emotion", "neutral"), float(data.get("intensity", 0.5))
