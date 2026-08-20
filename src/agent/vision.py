"""图片/文件分析 — 从 routes/chat.py 提取，供 POST /chat 端点复用。

提供:
  - analyze_image(): OCR + 视觉 LLM 分析
  - analyze_text_file(): 文本文件内容读取
"""

import os
import sys
import base64
import re as _re


# ═══════════════════════════════════════════════════════════════
# OCR（进程内单例，复用 easyocr 模型）
# ═══════════════════════════════════════════════════════════════

_easyocr_reader = None

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_EXTS = {
    ".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".html", ".css",
    ".md", ".yaml", ".yml", ".xml", ".csv", ".log", ".cfg", ".ini", ".toml",
    ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".rb",
    ".lua", ".sql", ".r", ".m", ".swift", ".kt", ".scala", ".php", ".vue", ".svelte",
}


def _get_ocr():
    """获取 OCR reader 单例（懒加载 + 进程内复用）"""
    global _easyocr_reader
    if _easyocr_reader is not None:
        return _easyocr_reader
    try:
        import easyocr
        _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        model_dir = os.path.join(_proj_root, "easyocr_models")
        os.makedirs(model_dir, exist_ok=True)
        _easyocr_reader = easyocr.Reader(
            ["ch_sim"], gpu=False, verbose=False,
            model_storage_directory=model_dir, download_enabled=True,
        )
    except Exception as e:
        print(f"[vision] OCR 加载失败: {e}", file=sys.stderr, flush=True)
        _easyocr_reader = False
    return _easyocr_reader


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════

def analyze_image(image_path: str) -> str:
    """对图片做视觉分析 + OCR，返回拼接后的文字描述。

    视觉分析使用 VISION_LLM（MiniMax 等多模态模型），
    OCR 使用本地 easyocr 模型做文字提取补充。
    """
    ext = os.path.splitext(image_path)[1].lower()
    if ext not in IMG_EXTS:
        return ""

    fname = os.path.basename(image_path)
    parts = []

    # ── 视觉 LLM 分析 ──
    try:
        import config.api_config as _vcfg
        from openai import OpenAI as _vOpenAI

        mime_map = {
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
        }
        mime = mime_map.get(ext.lstrip('.'), "image/png")

        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        vc = _vOpenAI(
            api_key=_vcfg.VISION_LLM_KEY,
            base_url=_vcfg.VISION_LLM_BASE_URL,
            default_headers=_vcfg.opencode_headers(_vcfg.VISION_LLM_BASE_URL) or None,
        )
        vr = vc.chat.completions.create(
            model=_vcfg.VISION_LLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请详细描述这张图片的内容"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                ],
            }],
            temperature=0.3,
            max_tokens=2048,
        )
        vcontent = (vr.choices[0].message.content or "").strip()
        vcontent = _re.sub(r'<think>.*?</think>\s*', '', vcontent, flags=_re.DOTALL).strip()
        if vcontent:
            parts.append(f"[图片「{fname}」内容]\n{vcontent}")
    except Exception as e:
        print(f"[vision] 视觉分析失败: {e}", file=sys.stderr, flush=True)

    # ── OCR 文字补充 ──
    ocr_text = _ocr_image_text(image_path)
    if ocr_text:
        parts.append(f"[图中文字]\n{ocr_text}")

    return "\n\n".join(parts)


def analyze_text_file(file_path: str) -> str:
    """读取文本文件内容（最大 500KB，截断至 8000 字符）"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in TEXT_EXTS:
        return ""
    try:
        size = os.path.getsize(file_path)
        if size > 500 * 1024:
            return ""
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > 8000:
            content = content[:8000] + "\n...（文件过长，已截断）"
        return content
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
# 内部
# ═══════════════════════════════════════════════════════════════

def _ocr_image_text(path: str) -> str:
    """对图片文件做 OCR，返回识别文字"""
    reader = _get_ocr()
    if not reader:
        return ""
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(path)
        w, h = img.size
        if w > 1280:
            img = img.resize((1280, int(h * 1280 / w)))
        arr = np.array(img.convert("RGB"))
        items = reader.readtext(arr, detail=1)
        lines = [item[1] for item in items if item[2] > 0.4]
        return "\n".join(lines[:60]) if lines else ""
    except Exception:
        return ""


def preprocess_attachments(text: str, image_paths: list) -> str:
    """预处理附件：图片 OCR + 文本文件读取，结果拼接到消息末尾。
    返回拼接后的完整文本。
    """
    if not image_paths:
        return text
    parts_to_append = []
    for image_path in image_paths:
        if not image_path or not os.path.isfile(image_path):
            continue
        ext = os.path.splitext(image_path)[1].lower()
        if ext in IMG_EXTS:
            try:
                result = analyze_image(image_path)
                if result:
                    parts_to_append.append(result)
            except Exception as e:
                print(f"[chat] 图片分析失败: {e}", file=sys.stderr, flush=True)
        elif ext in TEXT_EXTS and os.path.getsize(image_path) < 500 * 1024:
            try:
                content = analyze_text_file(image_path)
                if content:
                    parts_to_append.append(content)
            except Exception:
                pass
    if parts_to_append:
        text = (text + "\n\n" + "\n\n".join(parts_to_append)) if text else "\n\n".join(parts_to_append)
    return text
