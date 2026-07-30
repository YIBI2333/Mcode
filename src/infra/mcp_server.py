import os
import sys
import json
import re
import shlex
import platform
import shutil
import threading
import urllib.request
import urllib.error
import base64

# 将项目根目录添加到 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types
import subprocess
from datetime import datetime

try:
    from config.settings import MCP_WORKSPACE_DIR, ENABLE_WEB_LIVE2D
except ImportError as e:
    print(f"[MCP] 配置导入失败: {e}", file=sys.stderr, flush=True)
    MCP_WORKSPACE_DIR = None
    ENABLE_WEB_LIVE2D = False

# 可选依赖：剪贴板
try:
    import pyperclip
    _HAS_CLIPBOARD = True
except ImportError:
    _HAS_CLIPBOARD = False

# 可选依赖：YAML
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# 可选依赖：联网搜索
try:
    from ddgs import DDGS
    _HAS_SEARCH = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _HAS_SEARCH = True
    except ImportError:
        _HAS_SEARCH = False

# ---------- 工作目录配置 ----------
WORKSPACE_DIR = MCP_WORKSPACE_DIR
if WORKSPACE_DIR:
    os.makedirs(WORKSPACE_DIR, exist_ok=True)

# ---------- 路径白名单 ----------
# 除工作区外，允许访问用户的常用文件夹
# _PATH_WHITELIST = ["Desktop", "Documents", "Downloads", "桌面", "文档", "下载"]
_PATH_WHITELIST = []


def _is_path_allowed(full_path: str) -> bool:
    """检查路径是否在工作区或白名单内"""
    if WORKSPACE_DIR is None:
        return True  # 无限制，整个电脑都可访问

    path_norm = os.path.normcase(os.path.abspath(full_path))
    workspace_norm = os.path.normcase(os.path.abspath(WORKSPACE_DIR))

    # 工作区内
    if path_norm == workspace_norm or path_norm.startswith(workspace_norm + os.sep):
        return True

    # 白名单路径内
    home = os.path.expanduser("~")
    for wl in _PATH_WHITELIST:
        wl_norm = os.path.normcase(os.path.abspath(os.path.join(home, wl)))
        if path_norm == wl_norm or path_norm.startswith(wl_norm + os.sep):
            return True

    return False

def safe_path(relative_path: str) -> str:
    """路径安全校验 + 解析"""
    if WORKSPACE_DIR is None:
        return os.path.abspath(relative_path)

    # 相对路径拼到工作区下，绝对路径（如 C:\Users\..）保留原样
    full = os.path.abspath(os.path.join(WORKSPACE_DIR, relative_path))

    if _is_path_allowed(full):
        return full

    raise ValueError(f"无法访问: {relative_path}")


# ======================================================================
# 工具列表定义
# ======================================================================
TOOL_DEFINITIONS = [
    # --- 文件操作 ---
    types.Tool(name="list_directory", description="列出目录内容（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "绝对路径 或 工作区相对路径。桌面用 C:\\Users\\xxx\\Desktop"}
        }}),
    types.Tool(name="read_text_file", description="读取文本文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "文件的绝对路径或工作区相对路径"}
        }, "required": ["path"]}),
    types.Tool(name="read_json_file", description="读取 JSON 文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "JSON 文件的绝对路径或工作区相对路径"}
        }, "required": ["path"]}),
    types.Tool(name="read_yaml_file", description="读取 YAML 文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "YAML 文件的绝对路径或工作区相对路径"}
        }, "required": ["path"]}),
    types.Tool(name="write_file", description="创建/覆盖文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "文件的绝对路径 或 工作区相对路径"},
            "content": {"type": "string", "description": "要写入的内容"}
        }, "required": ["path", "content"]}),
    types.Tool(name="delete_file", description="删除文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "path": {"type": "string", "description": "文件的绝对路径 或 工作区相对路径"}
        }, "required": ["path"]}),
    types.Tool(name="move_file", description="移动/重命名文件（可访问工作区、桌面、文档、下载）",
        inputSchema={"type": "object", "properties": {
            "source": {"type": "string", "description": "源文件的绝对路径或相对路径"},
            "destination": {"type": "string", "description": "目标文件的绝对路径或相对路径"}
        }, "required": ["source", "destination"]}),

    # --- 命令执行 ---
    types.Tool(name="execute_command", description="在本地 shell 中执行命令（仅允许白名单命令）",
        inputSchema={"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的命令"}
        }, "required": ["command"]}),

    # --- 系统能力 ---
    types.Tool(name="get_current_time", description="获取当前日期和时间（精确到秒）",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="get_system_info", description="获取系统信息（操作系统、CPU、内存、磁盘）",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="get_env_var", description="读取环境变量值",
        inputSchema={"type": "object", "properties": {
            "name": {"type": "string", "description": "环境变量名称，如 PATH、HOME"}
        }, "required": ["name"]}),

    # --- 网络 ---
    types.Tool(name="web_search", description="联网搜索，返回 AI 总结后的搜索结果。优先用这个而不是 download_file",
        inputSchema={"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        }, "required": ["query"]}),
    types.Tool(name="vision_analyze", description="分析图片内容（识图）。传入图片路径和可选问题，返回 AI 对图片的理解描述",
        inputSchema={"type": "object", "properties": {
            "image_path": {"type": "string", "description": "图片文件的绝对路径"},
            "question": {"type": "string", "description": "你想了解图片的什么？可选，默认描述图片内容"}
        }, "required": ["image_path"]}),
    types.Tool(name="download_file", description="从 URL 下载文件到工作区",
        inputSchema={"type": "object", "properties": {
            "url": {"type": "string", "description": "下载地址"},
            "filename": {"type": "string", "description": "保存的文件名（可选，默认从URL提取）"}
        }, "required": ["url"]}),

    # --- 桌面交互 ---
    types.Tool(name="clipboard_read", description="读取剪贴板内容",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="clipboard_write", description="将文本写入剪贴板",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "要写入剪贴板的文本"}
        }, "required": ["content"]}),

    # --- Agent 元能力 ---
    types.Tool(name="think", description="记录 AI 的思考过程（不产生副作用，仅供透明化推理链）",
        inputSchema={"type": "object", "properties": {
            "thought": {"type": "string", "description": "思考内容或执行理由"}
        }, "required": ["thought"]}),
    types.Tool(name="ask_user", description="遇到歧义或需要确认时向用户提问",
        inputSchema={"type": "object", "properties": {
            "question": {"type": "string", "description": "向用户提出的问题"}
        }, "required": ["question"]}),

    # --- Live2D 操控 ---
    *([types.Tool(name="live2d_motion", description="触发 Live2D 动作或表情。表情(持久): neutral/happy/sad/angry/surprised。手势(一次性): sleep/wink/shake/tilt_head/cute_pose/nod/shake_head",
        inputSchema={"type": "object", "properties": {
            "motion": {"type": "string", "description": "动作名。表情: neutral/happy/sad/angry/surprised。手势: sleep/wink/shake/tilt_head/cute_pose/nod/shake_head"},
        }, "required": ["motion"]})] if ENABLE_WEB_LIVE2D else []),

    # --- 屏幕截图 ---
    types.Tool(name="take_screenshot", description="截取当前屏幕保存为图片，返回路径和尺寸信息",
        inputSchema={"type": "object", "properties": {}}),

    # --- 人设档案管理（4 个独立文档，各配读写工具） ---
    types.Tool(name="read_persona", description="读取基本人设（persona.md）：名字、性格、称呼等",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_persona", description="更新基本人设（persona.md 用户画像章节，自动追加时间戳）",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "要记录的内容（自动附带时间戳追加到用户画像）"}
        }, "required": ["content"]}),

    types.Tool(name="read_state", description="读取当前情感状态（state.md）：心情、好感度",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_state", description="更新情感状态（覆盖 state.md），如 '心情: 开心\\n好感度: 70/100'",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "新的情感状态，直接覆盖写入"}
        }, "required": ["content"]}),

    types.Tool(name="read_events", description="读取重要事件记录（events.md）：时间线形式的历史事件",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_events", description="记录重要事件（追加到 events.md，自动附带时间戳）",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "要记录的事件（自动附带时间戳追加）"}
        }, "required": ["content"]}),

    types.Tool(name="read_todos", description="读取待办与偏好（todos.md）：计划事项和用户偏好",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_todos", description="更新待办与偏好（追加到 todos.md，自动附带时间戳）",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "要记录的内容（自动附带时间戳追加）"}
        }, "required": ["content"]}),
]

# ---------- 人设文件路径 ----------
PERSONA_FILE = os.path.join(PROJECT_ROOT, "data", "persona", "persona.md")
STATE_FILE = os.path.join(PROJECT_ROOT, "data", "persona", "state.md")
EVENTS_FILE = os.path.join(PROJECT_ROOT, "data", "persona", "events.md")
TODOS_FILE = os.path.join(PROJECT_ROOT, "data", "persona", "todos.md")


# ======================================================================
# 工具实现（按功能分组的独立函数）
# ======================================================================

def _tool_list_directory(args: dict) -> str:
    path = args.get("path", ".")
    full = safe_path(path)
    if not os.path.isdir(full):
        return f"路径不是目录: {full}"
    items = os.listdir(full)
    return "\n".join(items) if items else "（空目录）"

def _tool_read_text_file(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return "错误: 未指定路径"
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"文件不存在: {full}"
    with open(full, "r", encoding="utf-8") as f:
        return f.read()

def _tool_read_json_file(args: dict) -> str:
    path = args.get("path", "")
    if not path:
        return "错误: 未指定路径"
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"文件不存在: {full}"
    with open(full, "r", encoding="utf-8") as f:
        data = json.load(f)
    return json.dumps(data, ensure_ascii=False, indent=2)

def _tool_read_yaml_file(args: dict) -> str:
    if not _HAS_YAML:
        return "错误: 未安装 PyYAML，请运行 pip install pyyaml"
    path = args.get("path", "")
    if not path:
        return "错误: 未指定路径"
    full = safe_path(path)
    if not os.path.isfile(full):
        return f"文件不存在: {full}"
    with open(full, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return json.dumps(data, ensure_ascii=False, indent=2)

def _tool_write_file(args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    full = safe_path(path)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"文件写入成功: {path}"

def _tool_delete_file(args: dict) -> str:
    path = args.get("path", "")
    full = safe_path(path)
    if os.path.isdir(full):
        os.rmdir(full) if not os.listdir(full) else shutil.rmtree(full)
        return f"已删除目录: {path}"
    os.remove(full)
    return f"已删除文件: {path}"

def _tool_move_file(args: dict) -> str:
    src = args.get("source", "")
    dst = args.get("destination", "")
    full_src = safe_path(src)
    full_dst = safe_path(dst)
    os.rename(full_src, full_dst)
    return f"移动成功: {src} → {dst}"

# cmd.exe 内建命令（需通过 cmd /c 执行）
_CMD_BUILTINS = frozenset({"dir", "del", "mkdir", "md", "rmdir", "echo", "type", "copy", "move", "ren", "rename"})

# 允许的命令白名单（可执行文件 + cmd 内建）
_ALLOWED_PROGRAMS = frozenset({
    "python", "python3", "pip", "node", "npm",
    "ls", "pwd", "cat", "grep",
}).union(_CMD_BUILTINS)

# cmd 内建命令需阻止的 shell 注入字符
_CMD_DANGEROUS = re.compile(r"[;&|<>^]")

def _tool_execute_command(args: dict) -> str:
    cmd = args.get("command", "").strip()
    if not cmd:
        return "错误: 空命令"

    # 阻止环境变量展开
    if re.search(r"%[A-Za-z_]+[A-Za-z0-9_]*%", cmd):
        return "拒绝执行：命令包含环境变量展开（%VAR%）"
    if re.search(r"\:", cmd, re.IGNORECASE):
        return "拒绝执行：命令包含 PowerShell 环境变量（:VAR）"

    # 用 shlex 安全解析命令（正确处理引号包裹的参数）
    try:
        tokens = shlex.split(cmd)
    except ValueError as e:
        return f"命令解析失败: {e}"

    if not tokens:
        return "错误: 空命令"

    program = tokens[0]
    program_lower = program.lower()

    # 检查命令是否在白名单（支持完整路径如 C:\\Python311\\python.exe）
    if program_lower not in _ALLOWED_PROGRAMS:
        basename = os.path.splitext(os.path.basename(program_lower))[0]
        if basename not in _ALLOWED_PROGRAMS:
            return f"拒绝执行: {program} 不在白名单内"

    # 验证所有参数
    for token in tokens[1:]:
        # 路径穿越检测
        if ".." in token and ("\\" in token or "/" in token):
            return "拒绝执行：参数包含路径穿越符号"
        # 绝对路径范围校验
        if re.match(r"^[A-Za-z]:\\", token) and not _is_path_allowed(os.path.abspath(token)):
            return f"拒绝执行：路径 {token} 超出允许范围"

    # cmd 内建命令：额外阻止 shell 注入字符（因为需要经过 cmd /c）
    if program_lower in _CMD_BUILTINS:
        for token in tokens[1:]:
            if _CMD_DANGEROUS.search(token):
                return "拒绝执行：参数包含危险字符"
        argv = ["cmd", "/c"] + tokens
    else:
        argv = tokens

    try:
        cwd = str(WORKSPACE_DIR) if WORKSPACE_DIR else None
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=30, stdin=subprocess.DEVNULL, cwd=cwd,
        )
        return (result.stdout + result.stderr) or "（无输出）"
    except subprocess.TimeoutExpired:
        return "执行超时（30秒），进程已被终止"
    except FileNotFoundError:
        return f"命令未找到: {program}（请确认已安装并加入 PATH）"

def _tool_get_current_time(_args: dict) -> str:
    now = datetime.now()
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    return f"当前时间: {now.strftime('%Y年%m月%d日 %H:%M:%S')} (周{weekdays[now.weekday()]})"

def _tool_get_system_info(_args: dict) -> str:
    total, used, free = shutil.disk_usage(WORKSPACE_DIR or ".")
    lines = [
        f"操作系统: {platform.system()} {platform.release()}",
        f"处理器: {platform.processor() or '未知'}",
        f"CPU核心: {os.cpu_count()}",
        f"磁盘总量: {total // (1024**3)} GB",
        f"磁盘已用: {used // (1024**3)} GB",
        f"磁盘可用: {free // (1024**3)} GB",
        f"工作目录: {WORKSPACE_DIR}",
    ]
    return "\n".join(lines)

def _tool_get_env_var(args: dict) -> str:
    name = args.get("name", "")
    value = os.environ.get(name)
    if value is None:
        return f"环境变量 {name} 未设置"
    return f"{name}={value}"

def _tool_web_search(args: dict) -> str:
    """联网搜索 — DeepSeek 内置搜索"""
    query = args.get("query", "")
    try:
        import config.api_config as _cfg
        from openai import OpenAI as _OpenAI
        sc = _OpenAI(api_key=_cfg.TOOL_LLM_KEY, base_url=_cfg.TOOL_LLM_BASE_URL)
        sr = sc.chat.completions.create(
            model=_cfg.TOOL_LLM_MODEL,
            messages=[{"role": "user", "content": f"请搜索：{query}"}],
            extra_body={"enable_search": True},
        )
        return (sr.choices[0].message.content or "").strip() or "未找到结果"
    except Exception as e:
        return f"搜索失败: {e}"

def _tool_vision_analyze(args: dict) -> str:
    """分析图片内容（使用 DeepSeek 视觉能力）"""
    image_path = args.get("image_path", "")
    question = args.get("question", "请详细描述这张图片的内容")

    if not os.path.isfile(image_path):
        return f"错误: 图片文件不存在: {image_path}"

    # 检查是否是图片格式
    ext = os.path.splitext(image_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
    mime = mime_map.get(ext)
    if not mime:
        return f"错误: 不支持的图片格式 {ext}，支持: {', '.join(mime_map.keys())}"

    try:
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"读取图片失败: {e}"

    try:
        from openai import OpenAI
        import config.api_config as cfg
        import re as _re
        vision_client = OpenAI(api_key=cfg.VISION_LLM_KEY, base_url=cfg.VISION_LLM_BASE_URL)
        resp = vision_client.chat.completions.create(
            model=cfg.VISION_LLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}},
                ],
            }],
            temperature=0.3,
        )
        content = resp.choices[0].message.content.strip()
        # MiniMax 会输出 <think>...</think> 思考过程，去掉
        content = _re.sub(r'<think>.*?</think>\s*', '', content, flags=_re.DOTALL).strip()
        return content
    except Exception as e:
        return f"图片分析失败: {e}"

def _tool_download_file(args: dict) -> str:
    url = args.get("url", "")
    filename = args.get("filename") or url.split("/")[-1].split("?")[0] or "download"
    dest = safe_path(filename)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
            f.write(resp.read())
        size = os.path.getsize(dest)
        return f"下载成功: {filename} ({size} 字节)"
    except urllib.error.HTTPError as e:
        return f"下载失败: HTTP {e.code}"
    except Exception as e:
        return f"下载失败: {e}"

def _tool_clipboard_read(_args: dict) -> str:
    if not _HAS_CLIPBOARD:
        return "错误: 未安装 pyperclip，请运行 pip install pyperclip"
    content = pyperclip.paste()
    return content if content else "（剪贴板为空）"

def _tool_clipboard_write(args: dict) -> str:
    if not _HAS_CLIPBOARD:
        return "错误: 未安装 pyperclip，请运行 pip install pyperclip"
    content = args.get("content", "")
    pyperclip.copy(content)
    return "已写入剪贴板"

def _tool_live2d_motion(args: dict) -> str:
    """触发 Live2D 动作（通过 WebSocket bridge 直达渲染器）"""
    motion = args.get("motion", "idle")
    try:
        import urllib.request
        body = json.dumps({"type": "emotion", "emotion": motion, "intensity": 0.8}).encode()
        req = urllib.request.Request("http://localhost:8850/command", data=body,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
        return f"动作已触发: {motion}"
    except Exception as e:
        return f"动作触发失败: {e}"

def _tool_take_screenshot(args: dict) -> str:
    """截屏 + OCR + 窗口信息"""
    print("[screenshot] 开始截屏...", file=sys.stderr, flush=True)
    from PIL import ImageGrab
    import numpy as np
    from datetime import datetime as _datetime
    import glob as _glob

    # 截图保存到 data/screenshots/，每次清理旧图
    save_dir = os.path.join(PROJECT_ROOT, "data", "screenshots")
    os.makedirs(save_dir, exist_ok=True)
    for old in _glob.glob(os.path.join(save_dir, "screenshot_*.png")):
        try: os.remove(old)
        except Exception: pass

    ts = _datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"screenshot_{ts}.png")

    img = ImageGrab.grab(all_screens=True)
    img.save(path, "PNG")
    window_info = _get_active_window_info()

    ocr_text = _ocr_image(img)
    print(f"[screenshot] 完成 OCR={len(ocr_text)}字", file=sys.stderr, flush=True)

    return (
        f"截图: {path} ({img.size[0]}x{img.size[1]})\n"
        f"{window_info}"
        + (f"\n\n[屏幕文字]\n{ocr_text}" if ocr_text else "")
    )


_easyocr_reader = None
_easyocr_loading = False
_easyocr_lock = threading.Lock()

def _get_ocr_reader():
    """获取 easyocr reader（非阻塞，未就绪返回 None）"""
    global _easyocr_reader
    if _easyocr_reader is not None:
        return _easyocr_reader
    return None  # 还没加载好，返回 None

def _load_ocr_reader():
    """后台加载 easyocr（模型存到项目 easyocr_models/）"""
    global _easyocr_reader, _easyocr_loading
    with _easyocr_lock:
        if _easyocr_reader is not None or _easyocr_loading:
            return
        _easyocr_loading = True
    try:
        import easyocr
        model_dir = os.path.join(PROJECT_ROOT, "easyocr_models")
        os.makedirs(model_dir, exist_ok=True)
        # 只加载中文（英文模型大且对中文屏幕帮助不大）
        reader = easyocr.Reader(
            ["ch_sim"], gpu=False, verbose=False,
            model_storage_directory=model_dir, download_enabled=True
        )
        with _easyocr_lock:
            _easyocr_reader = reader
    except Exception as e:
        print(f"[OCR] 加载失败: {e}", file=sys.stderr, flush=True)

def _ocr_image(img) -> str:
    """OCR 识别图片文字，未就绪则返回提示"""
    reader = _get_ocr_reader()
    if reader is None:
        return "(OCR 模型加载中，请稍后再试)"

    try:
        import numpy as np
        w, h = img.size
        if w > 1280:
            img = img.resize((1280, int(h * 1280 / w)))
        arr = np.array(img)
        items = reader.readtext(arr, detail=1)
        lines = [item[1] for item in items if item[2] > 0.4]
        return "\n".join(lines[:80]) if lines else ""
    except Exception:
        return ""


def _get_active_window_info() -> str:
    """获取活跃窗口标题和应用名（2s 超时防止卡死）"""
    try:
        import win32gui, win32process, psutil
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app, exe = "?", "?"
        try:
            proc = psutil.Process(pid)
            app = str(proc.name() or "?")
            exe = str(proc.exe() or "?")
        except Exception:
            pass
        return f"活跃窗口: {title}\n应用: {app}"
    except Exception:
        return "(窗口信息不可用)"


def _tool_think(args: dict) -> str:
    return f"[思考记录] {args.get('thought', '')}"

def _tool_ask_user(args: dict) -> str:
    return f"[需要用户确认] {args.get('question', '')}"

# ── 通用：追加一行到文件指定标题下方 ──
def _append_under_heading(filepath: str, heading: str, line: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    inserted = False
    result = []
    for ln in lines:
        result.append(ln)
        if ln.strip() == heading:
            result.append(line)
            inserted = True
    if not inserted:
        result.append(f"\n{heading}\n")
        result.append(line)
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(result)
    return "ok"

# ── Persona ──
def _tool_read_persona(_args: dict) -> str:
    if not os.path.exists(PERSONA_FILE): return "人设文件不存在。"
    with open(PERSONA_FILE, "r", encoding="utf-8") as f: return f.read()

def _tool_update_persona(args: dict) -> str:
    content = args.get("content", "").strip()
    if not content: return "错误: content 为空"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- {now}: {content}\n"
    _append_under_heading(PERSONA_FILE, "## 用户画像", line)
    return f"人设已更新: {content}"

# ── State ──
def _tool_read_state(_args: dict) -> str:
    if not os.path.exists(STATE_FILE): return "状态文件不存在。"
    with open(STATE_FILE, "r", encoding="utf-8") as f: return f.read()

def _tool_update_state(args: dict) -> str:
    content = args.get("content", "").strip()
    if not content: return "错误: content 为空"
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(content + "\n")
    return f"状态已更新: {content}"

# ── Events ──
def _tool_read_events(_args: dict) -> str:
    if not os.path.exists(EVENTS_FILE): return "事件记录不存在。"
    with open(EVENTS_FILE, "r", encoding="utf-8") as f: return f.read()

def _tool_update_events(args: dict) -> str:
    content = args.get("content", "").strip()
    if not content: return "错误: content 为空"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- {now}: {content}\n"
    _append_under_heading(EVENTS_FILE, "# 重要事件", line)
    return f"事件已记录: {content}"

# ── Todos ──
def _tool_read_todos(_args: dict) -> str:
    if not os.path.exists(TODOS_FILE): return "待办文件不存在。"
    with open(TODOS_FILE, "r", encoding="utf-8") as f: return f.read()

def _tool_update_todos(args: dict) -> str:
    content = args.get("content", "").strip()
    if not content: return "错误: content 为空"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- {now}: {content}\n"
    _append_under_heading(TODOS_FILE, "# 待办与偏好", line)
    return f"待办已更新: {content}"


# ======================================================================
# 工具分发字典
# ======================================================================
TOOL_HANDLERS = {
    "list_directory":     _tool_list_directory,
    "read_text_file":     _tool_read_text_file,
    "read_json_file":     _tool_read_json_file,
    "read_yaml_file":     _tool_read_yaml_file,
    "write_file":         _tool_write_file,
    "delete_file":        _tool_delete_file,
    "move_file":          _tool_move_file,
    "execute_command":    _tool_execute_command,
    "get_current_time":   _tool_get_current_time,
    "get_system_info":    _tool_get_system_info,
    "get_env_var":        _tool_get_env_var,
    "web_search":         _tool_web_search,
    "vision_analyze":     _tool_vision_analyze,
    "download_file":      _tool_download_file,
    "clipboard_read":     _tool_clipboard_read,
    "clipboard_write":    _tool_clipboard_write,
    "live2d_motion": _tool_live2d_motion,
    "think":          _tool_think,
    "take_screenshot":    _tool_take_screenshot,
    "ask_user":           _tool_ask_user,
    "read_persona":       _tool_read_persona,
    "update_persona":     _tool_update_persona,
    "read_state":         _tool_read_state,
    "update_state":       _tool_update_state,
    "read_events":        _tool_read_events,
    "update_events":      _tool_update_events,
    "read_todos":         _tool_read_todos,
    "update_todos":       _tool_update_todos,
}


# ======================================================================
# MCP 服务器注册
# ======================================================================
server = Server("local-file-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return TOOL_DEFINITIONS

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return [types.TextContent(type="text", text=f"未知工具: {name}")]
    try:
        result = handler(arguments)
        # 支持 async handler
        if asyncio.iscoroutine(result):
            result = await result
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"工具执行失败: {e}")]


# ======================================================================
# 启动入口
# ======================================================================

# 启动时同步加载 OCR 模型（在本进程内，确保工具可直接使用）
print("[OCR] 正在加载模型...", file=sys.stderr, flush=True)
_load_ocr_reader()
print("[OCR] 模型就绪", file=sys.stderr, flush=True)


async def main():
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream,
                InitializationOptions(
                    server_name="local-file-server",
                    server_version="1.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    except Exception as e:
        print(f"[MCP FATAL] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
