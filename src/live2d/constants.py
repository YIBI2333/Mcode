"""
Live2D semantic constants — emotion definitions + vowel classification.
No render parameters (Cubism/VTS parameter names are not stored here).
"""
from typing import Dict, List

# ====================== Emotions ======================
EMOTION_NAMES = ["neutral", "happy", "sad", "angry", "surprised"]

INTENSITY_LEVELS = [0.0, 0.3, 0.6, 1.0]

EMOTION_CONFIG: Dict[str, Dict[str, dict]] = {
    "neutral": {
        "0.0": {"mouth": "closed", "eyes": "normal", "brows": "relaxed"},
        "0.3": {"mouth": "slight_smile", "eyes": "normal", "brows": "relaxed"},
        "0.6": {"mouth": "soft_smile", "eyes": "slightly_wide", "brows": "slightly_raised"},
        "1.0": {"mouth": "smile", "eyes": "wide", "brows": "raised"},
    },
    "happy": {
        "0.0": {"mouth": "neutral", "eyes": "normal", "brows": "relaxed"},
        "0.3": {"mouth": "soft_smile", "eyes": "normal", "brows": "slightly_raised"},
        "0.6": {"mouth": "wide_smile", "eyes": "squint", "brows": "raised"},
        "1.0": {"mouth": "big_smile", "eyes": "closed_squint", "brows": "high_raised"},
    },
    "sad": {
        "0.0": {"mouth": "neutral", "eyes": "normal", "brows": "relaxed"},
        "0.3": {"mouth": "slight_frown", "eyes": "slightly_narrow", "brows": "slightly_lowered"},
        "0.6": {"mouth": "frown", "eyes": "narrow", "brows": "lowered"},
        "1.0": {"mouth": "deep_frown", "eyes": "very_narrow", "brows": "deep_lowered"},
    },
    "angry": {
        "0.0": {"mouth": "neutral", "eyes": "normal", "brows": "relaxed"},
        "0.3": {"mouth": "tight", "eyes": "slightly_glare", "brows": "slightly_knit"},
        "0.6": {"mouth": "tight_frown", "eyes": "glare", "brows": "knit"},
        "1.0": {"mouth": "snarl", "eyes": "intense_glare", "brows": "deeply_knit"},
    },
    "surprised": {
        "0.0": {"mouth": "closed", "eyes": "normal", "brows": "relaxed"},
        "0.3": {"mouth": "slightly_open", "eyes": "slightly_wide", "brows": "slightly_raised"},
        "0.6": {"mouth": "open", "eyes": "wide", "brows": "raised"},
        "1.0": {"mouth": "wide_open", "eyes": "very_wide", "brows": "high_raised"},
    },
}

# ====================== Vowel classification ======================
_VOWEL_CLASS_MAP: Dict[str, str] = {}

_A = "啊阿大他拿哈拉扎擦萨马那卡压加假家下夏丫呀把怕妈发打塔呐啦嘎咖哈巴爬麻法搭塌那拉扎查沙撒鸦鸭罢霸骂八拔爸爬怕马发搭他拿拉嘎卡哈扎查杀杂擦撒压价假嫁架驾虾霞侠吓夏厦鸦哑亚牙芽雅崖轧讶"
_O = "噢哦喔播波破莫佛多托诺罗锅郭扩或左错所若弱说窝我握卧蜗涡"
_E = "呃鹅哥科和这车社热得特呢乐个可合者策色额恶饿厄遏扼鄂讹"
_I = "一以已意义衣医依移疑乙译亿益忆蚁椅宜仪遗倚翼役疫毅抑笔比鼻闭毕逼彼碧蔽壁避臂币弊"
_U = "五无物务武午舞吴误乌污屋恶雾悟吾勿巫呜诬不部步布补捕卜簿埠怖哺夫父服付府副富复附妇负福幅伏符辅覆腐"

for c in _A:
    _VOWEL_CLASS_MAP[c] = "a"
for c in _O:
    _VOWEL_CLASS_MAP[c] = "o"
for c in _E:
    _VOWEL_CLASS_MAP[c] = "e"
for c in _I:
    _VOWEL_CLASS_MAP[c] = "i"
for c in _U:
    _VOWEL_CLASS_MAP[c] = "u"


# Optional pypinyin support for better vowel coverage
try:
    from pypinyin import pinyin, Style

    def _pinyin_vowel_class(char: str) -> str | None:
        """Get vowel class via pypinyin (full final -> a/o/e/i/u)."""
        try:
            result = pinyin(char, style=Style.FINALS, heteronym=False)
            if result and result[0]:
                final = result[0][0]  # e.g. 'ang', 'i', 'ou'
                first = final[0] if final else ""
                if first in "aiou":
                    return first  # a, i, o, u (first letter of final)
                elif first == "e":
                    return "e"
                elif first == "v":
                    return "u"  # ü maps to u
        except Exception:
            pass
        return None

    _HAS_PYPINYIN = True
except ImportError:
    _HAS_PYPINYIN = False

    def _pinyin_vowel_class(char: str) -> str | None:  # noqa: ARG001
        return None


def char_vowel_class(char: str) -> str:
    """Return vowel class for a Chinese character: 'a'/'o'/'e'/'i'/'u' or 'default'."""
    # Try pypinyin first for complete coverage
    if _HAS_PYPINYIN:
        result = _pinyin_vowel_class(char)
        if result:
            return result
    # Fall back to hardcoded map
    return _VOWEL_CLASS_MAP.get(char, "default")


# ====================== Emotion motion presets ======================
# Per-emotion: base_params, entry_anim, idle_anim, body_motion

MOTION_PRESETS: Dict[str, dict] = {
    "neutral": {
        "base_params": {
            "ParamMouthOpenY": 0.0, "ParamMouthForm": 0.0,
            "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0,
            "ParamEyeLSmile": 0.0, "ParamEyeRSmile": 0.0,
            "ParamBrowLY": 0.0, "ParamBrowRY": 0.0,
            "ParamBrowLForm": 0.0, "ParamBrowRForm": 0.0,
            "ParamAngleX": 0.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
            "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0, "ParamBodyAngleZ": 0.0,
        },
        "entry_anim": [
            {"t": 0,   "params": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}, "easing": "linear"},
            {"t": 400, "params": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}, "easing": "easeOut"},
        ],
        "idle_anim": {"params": {"ParamBodyAngleZ": 0.03, "ParamBreath": 0.3}, "period": 3000, "amplitude": 1.0},
        "body_motion": {"params": {}, "duration": 0},
    },
    "happy": {
        "base_params": {
            "ParamMouthOpenY": 0.0, "ParamMouthForm": 1.0,
            "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7,
            "ParamEyeLSmile": 1.0, "ParamEyeRSmile": 1.0,
            "ParamBrowLY": 0.6, "ParamBrowRY": 0.6,
            "ParamBrowLForm": -0.2, "ParamBrowRForm": -0.2,
            "ParamAngleX": 5.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
            "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0, "ParamBodyAngleZ": 0.0,
        },
        "entry_anim": [
            {"t": 0,   "params": {"ParamAngleX": 0.0, "ParamEyeLSmile": 0.0, "ParamEyeRSmile": 0.0, "ParamBrowLY": 0.0, "ParamBrowRY": 0.0}, "easing": "linear"},
            {"t": 200, "params": {"ParamAngleX": 8.0, "ParamEyeLSmile": 0.8, "ParamEyeRSmile": 0.8, "ParamBrowLY": 0.5, "ParamBrowRY": 0.5}, "easing": "easeOut"},
            {"t": 450, "params": {"ParamAngleX": 3.0, "ParamEyeLSmile": 1.0, "ParamEyeRSmile": 1.0, "ParamBrowLY": 0.6, "ParamBrowRY": 0.6}, "easing": "easeOut"},
            {"t": 600, "params": {"ParamAngleX": 5.0}, "easing": "easeOut"},
        ],
        "idle_anim": {"params": {"ParamAngleZ": 3.0, "ParamBodyAngleZ": 0.05}, "period": 1500, "amplitude": 1.0},
        "body_motion": {"params": {"ParamBodyAngleZ": 4.0}, "duration": 400},
    },
    "sad": {
        "base_params": {
            "ParamMouthOpenY": 0.0, "ParamMouthForm": -0.6,
            "ParamEyeLOpen": 0.5, "ParamEyeROpen": 0.5,
            "ParamEyeLSmile": 0.0, "ParamEyeRSmile": 0.0,
            "ParamBrowLY": -0.8, "ParamBrowRY": -0.8,
            "ParamBrowLForm": 0.6, "ParamBrowRForm": 0.6,
            "ParamAngleX": -5.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
            "ParamBodyAngleX": 3.0, "ParamBodyAngleY": 0.0, "ParamBodyAngleZ": 0.0,
        },
        "entry_anim": [
            {"t": 0,   "params": {"ParamAngleX": 0.0, "ParamBrowLY": 0.0, "ParamBrowRY": 0.0, "ParamBrowLForm": 0.0, "ParamBrowRForm": 0.0, "ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0}, "easing": "linear"},
            {"t": 350, "params": {"ParamAngleX": -8.0, "ParamBrowLY": -0.5, "ParamBrowRY": -0.5, "ParamBrowLForm": 0.4, "ParamBrowRForm": 0.4, "ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6}, "easing": "easeOut"},
            {"t": 700, "params": {"ParamAngleX": -5.0, "ParamBrowLY": -0.8, "ParamBrowRY": -0.8, "ParamBrowLForm": 0.6, "ParamBrowRForm": 0.6, "ParamEyeLOpen": 0.5, "ParamEyeROpen": 0.5}, "easing": "easeOut"},
        ],
        "idle_anim": {"params": {"ParamBodyAngleX": 2.0, "ParamAngleZ": -2.0}, "period": 4000, "amplitude": 0.6},
        "body_motion": {"params": {"ParamBodyAngleX": 5.0}, "duration": 600},
    },
    "angry": {
        "base_params": {
            "ParamMouthOpenY": 0.0, "ParamMouthForm": -0.5,
            "ParamEyeLOpen": 1.2, "ParamEyeROpen": 1.2,
            "ParamEyeLSmile": 0.0, "ParamEyeRSmile": 0.0,
            "ParamBrowLY": -1.0, "ParamBrowRY": -1.0,
            "ParamBrowLForm": 1.0, "ParamBrowRForm": 1.0,
            "ParamAngleX": -8.0, "ParamAngleY": 0.0, "ParamAngleZ": -3.0,
            "ParamBodyAngleX": 0.0, "ParamBodyAngleY": 0.0, "ParamBodyAngleZ": 0.0,
        },
        "entry_anim": [
            {"t": 0,   "params": {"ParamBrowLY": 0.0, "ParamBrowRY": 0.0, "ParamBrowLForm": 0.0, "ParamBrowRForm": 0.0, "ParamAngleZ": 0.0}, "easing": "linear"},
            {"t": 150, "params": {"ParamBrowLY": -0.8, "ParamBrowRY": -0.8, "ParamBrowLForm": 0.8, "ParamBrowRForm": 0.8, "ParamAngleZ": -6.0, "ParamAngleX": -6.0}, "easing": "easeOut"},
            {"t": 300, "params": {"ParamBrowLY": -1.0, "ParamBrowRY": -1.0, "ParamBrowLForm": 1.0, "ParamBrowRForm": 1.0, "ParamAngleZ": -3.0, "ParamAngleX": -8.0}, "easing": "easeOut"},
        ],
        "idle_anim": {"params": {"ParamAngleZ": -2.0, "ParamBodyAngleZ": 0.04}, "period": 2000, "amplitude": 0.7},
        "body_motion": {"params": {"ParamAngleZ": -8.0, "ParamBodyAngleZ": -5.0}, "duration": 250},
    },
    "surprised": {
        "base_params": {
            "ParamMouthOpenY": 0.6, "ParamMouthForm": 0.1,
            "ParamEyeLOpen": 1.8, "ParamEyeROpen": 1.8,
            "ParamEyeLSmile": 0.0, "ParamEyeRSmile": 0.0,
            "ParamBrowLY": 1.0, "ParamBrowRY": 1.0,
            "ParamBrowLForm": -0.3, "ParamBrowRForm": -0.3,
            "ParamAngleX": 2.0, "ParamAngleY": 0.0, "ParamAngleZ": 0.0,
            "ParamBodyAngleX": -5.0, "ParamBodyAngleY": 0.0, "ParamBodyAngleZ": 0.0,
        },
        "entry_anim": [
            {"t": 0,   "params": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 1.0, "ParamBrowLY": 0.0, "ParamBrowRY": 0.0, "ParamMouthOpenY": 0.0}, "easing": "linear"},
            {"t": 120, "params": {"ParamEyeLOpen": 1.9, "ParamEyeROpen": 1.9, "ParamBrowLY": 1.0, "ParamBrowRY": 1.0, "ParamMouthOpenY": 0.9}, "easing": "easeOut"},
            {"t": 500, "params": {"ParamEyeLOpen": 1.8, "ParamEyeROpen": 1.8, "ParamMouthOpenY": 0.6}, "easing": "easeOut"},
        ],
        "idle_anim": {"params": {"ParamBodyAngleX": -2.0, "ParamMouthOpenY": 0.1}, "period": 2500, "amplitude": 0.4},
        "body_motion": {"params": {"ParamBodyAngleX": -8.0, "ParamAngleX": 5.0}, "duration": 200},
    },
}

# ====================== Vowel mouth shape maps ======================
# Vowel class -> mouth openness (0.0-1.0)
VOWEL_MOUTH_OPEN: Dict[str, float] = {
    "a": 0.8,
    "o": 0.5,
    "e": 0.3,
    "i": 0.2,
    "u": 0.4,
    "default": 0.3,
}

# Vowel class -> mouth form (-1=rounded, 0=neutral, +1=spread)
VOWEL_MOUTH_FORM: Dict[str, float] = {
    "a": 0.0,
    "o": -0.4,
    "e": 0.3,
    "i": 0.5,
    "u": -0.5,
    "default": 0.0,
}
