"""图片映射表 — key → 图片 URL 的键值对。

AI 可自行增删改查映射（image_set / image_list / image_delete 工具），
在对话/工作流中通过 `[图片:key]` 标记引用图片，后端会解析成可访问的 URL。

图片来源 URL 形式：
  - /workspace/xxx.png  工作区里的图片（下载/生成后）
  - /uploads/xxx.png    用户上传的图片
  - https://...         网络图片直链
"""
import os
import json
import re

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_MAPPING_FILE = os.path.join(_DATA_DIR, "images", "mapping.json")

# [图片:key] / [图片：key] 标记
_REF_PAT = re.compile(r"\[图片[:：]\s*([^\]\]]+)\]")


def load() -> dict:
    try:
        if os.path.exists(_MAPPING_FILE):
            with open(_MAPPING_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _save(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_MAPPING_FILE), exist_ok=True)
        with open(_MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_image(key: str, url: str) -> str:
    key = (key or "").strip()
    url = (url or "").strip()
    if not key or not url:
        return "错误：key 和 url 都不能为空"
    d = load()
    d[key] = url
    _save(d)
    return f"已保存图片映射：{key} → {url}"


def delete_image(key: str) -> str:
    key = (key or "").strip()
    d = load()
    if key in d:
        del d[key]
        _save(d)
        return f"已删除图片映射：{key}"
    return f"未找到图片映射：{key}"


def list_images() -> str:
    d = load()
    if not d:
        return "（暂无图片映射）"
    return "\n".join(f"- {k} → {v}" for k, v in d.items())


def resolve_image_refs(text: str) -> str:
    """把 [图片:key] 替换成 markdown 图片 ![](url)；无映射则原样保留。"""
    d = load()

    def repl(m):
        key = m.group(1).strip()
        url = d.get(key)
        if url:
            return f"![{key}]({url})"
        return m.group(0)

    return _REF_PAT.sub(repl, text)
