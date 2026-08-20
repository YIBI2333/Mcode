import os
import sys
import json
import re
import shlex
import platform
import shutil
import threading
import inspect
import functools
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

# 可选依赖：浏览器操控（Playwright，async API — 工具在 MCP 事件循环内运行，
# 不能用 sync API，它检测到正在运行的事件循环会直接报错）
try:
    from playwright.async_api import async_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

# 可选依赖：桌面控制（无显示器环境导入会失败）
try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except Exception:
    _HAS_PYAUTOGUI = False

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
    types.Tool(name="vision_analyze", description="分析图片内容（识图）。传入图片路径（工作区相对路径或绝对路径均可）和可选问题，返回 AI 对图片的理解描述。下载的图片在工作区内，直接用 download_file 返回的路径即可",
        inputSchema={"type": "object", "properties": {
            "image_path": {"type": "string", "description": "图片路径，支持工作区相对路径（如 文件名.png）或绝对路径"},
            "question": {"type": "string", "description": "你想了解图片的什么？可选，默认描述图片内容"}
        }, "required": ["image_path"]}),
    types.Tool(name="download_file", description="从 URL 下载文件到工作区，返回保存的完整绝对路径（可直接交给 vision_analyze 等工具）",
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
    types.Tool(name="take_screenshot", description="截取当前屏幕保存为图片，返回路径、尺寸、OCR 文字及每个文字的像素坐标 (x,y)。点击屏幕上的文字元素时用返回的文字坐标；图标/按钮等无文字元素先用 locate_on_screen 定位，不要凭空估计坐标",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="locate_on_screen", description="在屏幕上定位元素并返回可点击坐标（像素）。适合 OCR 识别不到文字的图标/按钮/颜色块等元素。先用 take_screenshot 得到截图路径，再传 image_path + target。返回直接可用于 mouse_click 的 (x, y)",
        inputSchema={"type": "object", "properties": {
            "image_path": {"type": "string", "description": "截图路径（take_screenshot 返回的图片路径）"},
            "target": {"type": "string", "description": "要定位的元素描述，如「QQ 登录图标」「输入框」「开始菜单按钮」"},
        }, "required": ["image_path", "target"]}),

    # --- 浏览器操控（Playwright，独立 Chromium 实例） ---
    *([
        types.Tool(name="browser_open", description="打开网页（独立浏览器）。返回页面标题、URL 和带编号的页面结构快照。visible=true 时显示浏览器窗口让用户看到操作过程，默认无窗口后台执行",
            inputSchema={"type": "object", "properties": {
                "url": {"type": "string", "description": "网址，如 https://www.bing.com（缺协议时自动补 https://）"},
                "visible": {"type": "boolean", "description": "是否显示浏览器窗口（默认 false 后台执行）"},
            }, "required": ["url"]}),
        types.Tool(name="browser_snapshot", description="获取当前网页的快照：页面可见文本节选 + 可交互元素列表（每个元素带 ref 编号）。不知道页面上有什么时先调这个",
            inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="browser_click", description="点击网页上的元素。ref 是 browser_snapshot 返回的元素编号（注意：每次操作后页面会变化，要用最新快照里的编号）",
            inputSchema={"type": "object", "properties": {
                "ref": {"type": "integer", "description": "browser_snapshot 返回的元素编号"},
            }, "required": ["ref"]}),
        types.Tool(name="browser_type", description="在网页输入框中输入文字，可选提交（按回车）。ref 是 browser_snapshot 返回的输入框编号",
            inputSchema={"type": "object", "properties": {
                "ref": {"type": "integer", "description": "browser_snapshot 返回的输入框编号"},
                "text": {"type": "string", "description": "要输入的文字"},
                "submit": {"type": "boolean", "description": "输入后按回车提交（默认 false）"},
            }, "required": ["ref", "text"]}),
        types.Tool(name="browser_press", description="在当前网页按键盘键，如 Enter、Escape、Tab、ArrowDown、ctrl+a（组合键用 + 连接）",
            inputSchema={"type": "object", "properties": {
                "key": {"type": "string", "description": "键名：enter/escape/tab/backspace/space/arrow键/f1-f12/home/end/pageup/pagedown，组合键如 ctrl+a"},
            }, "required": ["key"]}),
        types.Tool(name="browser_scroll", description="滚动当前网页页面",
            inputSchema={"type": "object", "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "滚动方向（默认 down）"},
                "amount": {"type": "integer", "description": "滚动量，单位约为半屏（默认 2）"},
            }}),
        types.Tool(name="browser_read", description="读取当前网页的正文文本（转成纯文本）。适合静态阅读类页面；需要交互的操作用 browser_snapshot",
            inputSchema={"type": "object", "properties": {
                "max_chars": {"type": "integer", "description": "最多返回字符数（默认 4000）"},
            }}),
        types.Tool(name="browser_screenshot", description="截取当前网页画面保存为图片，返回路径（可配合 vision_analyze 分析页面视觉内容，如验证码、图表）",
            inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="browser_close", description="关闭浏览器释放资源（任务完成后建议调用）",
            inputSchema={"type": "object", "properties": {}}),
    ] if _HAS_PLAYWRIGHT else []),

    # --- 桌面控制（pyautogui，操作真实鼠标键盘） ---
    *([
        types.Tool(name="list_windows", description="列出当前所有可见窗口的标题，用于定位要操作的程序",
            inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="activate_window", description="把指定标题的窗口切换到前台（支持模糊匹配标题）",
            inputSchema={"type": "object", "properties": {
                "title": {"type": "string", "description": "窗口标题（包含即可匹配）"},
            }, "required": ["title"]}),
    types.Tool(name="mouse_click", description="点击屏幕坐标（主屏幕）。坐标优先取自 take_screenshot 的 [屏幕文字及像素坐标]，或 locate_on_screen 返回的屏幕坐标；严禁凭空估计坐标",
        inputSchema={"type": "object", "properties": {
            "x": {"type": "integer", "description": "屏幕 X 坐标（来自 take_screenshot 的截图或 locate_on_screen）"},
            "y": {"type": "integer", "description": "屏幕 Y 坐标"},
            "clicks": {"type": "integer", "description": "点击次数（默认 1，2 为双击）"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "鼠标键（默认 left）"},
        }, "required": ["x", "y"]}),
        types.Tool(name="mouse_scroll", description="在当前位置滚动鼠标滚轮",
            inputSchema={"type": "object", "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "description": "滚动方向（默认 down）"},
                "amount": {"type": "integer", "description": "滚动量（默认 3，约 3 个滚轮格）"},
            }}),
        types.Tool(name="keyboard_type", description="向当前焦点窗口输入文字（自动处理中文：走剪贴板粘贴）",
            inputSchema={"type": "object", "properties": {
                "text": {"type": "string", "description": "要输入的文字"},
            }, "required": ["text"]}),
        types.Tool(name="keyboard_press", description="按键盘键或组合键，如 enter、esc、tab、alt+f4、ctrl+c、winleft（开始菜单）",
            inputSchema={"type": "object", "properties": {
                "key": {"type": "string", "description": "键名（组合键用 + 连接）"},
            }, "required": ["key"]}),
    ] if _HAS_PYAUTOGUI else []),

    # --- 人设档案管理（4 个独立文档，各配读写工具） ---
    types.Tool(name="read_persona", description="读取基本人设（persona.md）：名字、性格、称呼等",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_persona", description="更新基本人设（persona.md 用户画像章节，自动追加时间戳）",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "要记录的内容（自动附带时间戳追加到用户画像）"}
        }, "required": ["content"]}),

    types.Tool(name="read_state", description="读取当前情感状态（state.md）：心情、好感度",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="update_state", description="更新情感状态（覆盖 state.md）。content 必须严格为下面两行，只写这两行、不要写任何多余内容：\n心情: 安心又开心\n好感度: 100/100",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "严格两行：第一行『心情: xxx』，第二行『好感度: xxx/100』，不要有其它文字"}
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

    # --- 图片映射表（AI 可在对话中发图片） ---
    types.Tool(name="image_set", description="建立/修改图片映射：给一个名字(key)绑定一张图片(url)。之后在回复里写 [图片:key] 就能把这张图发给用户。url 可用 /workspace/xxx.png（工作区图片）、/uploads/xxx.png（用户上传）、https://...（网络直链）",
        inputSchema={"type": "object", "properties": {
            "key": {"type": "string", "description": "图片的名字/描述，如「猫猫照片」「截图」"},
            "url": {"type": "string", "description": "图片地址：/workspace/xxx.png、/uploads/xxx.png 或 https://..."},
        }, "required": ["key", "url"]}),
    types.Tool(name="image_list", description="列出当前所有图片映射（key → url）",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="image_delete", description="删除一个图片映射",
        inputSchema={"type": "object", "properties": {
            "key": {"type": "string", "description": "要删除的图片名字(key)"},
        }, "required": ["key"]}),
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
def _line_diff(old: str, new: str) -> tuple:
    """行级差异统计 (added, deleted) — 类似 git diff --stat 的简化版"""
    import difflib
    a, b = old.splitlines(), new.splitlines()
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    added = deleted = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            deleted += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, deleted


def _tool_write_file(args: dict) -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    full = safe_path(path)
    # 旧内容 → 行级 diff（新文件 = 全部新增）
    old = None
    if os.path.isfile(full):
        try:
            with open(full, "r", encoding="utf-8") as f:
                old = f.read()
        except Exception:
            old = None
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    if old is None:
        added, deleted = len(content.splitlines()), 0
        op = "create"
    else:
        try:
            added, deleted = _line_diff(old, content)
        except Exception:
            added, deleted = len(content.splitlines()), 0
        op = "edit"
    return (f"文件已写入: {path} (+{added} -{deleted})\n"
            f"[FILE_OP] {op}|{path}|+{added}|-{deleted}")


def _tool_delete_file(args: dict) -> str:
    path = args.get("path", "")
    full = safe_path(path)
    if os.path.isdir(full):
        os.rmdir(full) if not os.listdir(full) else shutil.rmtree(full)
        return f"已删除目录: {path}\n[FILE_OP] delete|{path}|+0|-0"
    os.remove(full)
    return f"已删除文件: {path}\n[FILE_OP] delete|{path}|+0|-0"


def _tool_move_file(args: dict) -> str:
    src = args.get("source", "")
    dst = args.get("destination", "")
    full_src = safe_path(src)
    full_dst = safe_path(dst)
    os.rename(full_src, full_dst)
    return f"移动成功: {src} → {dst}\n[FILE_OP] move|{src} → {dst}|+0|-0"
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
    # 阻止 PowerShell 环境变量（$env:VAR / $VAR:xxx），但允许 Windows 盘符（C:\）
    if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*:", cmd):
        return "拒绝执行：命令包含 PowerShell 环境变量（$VAR:）"

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
        # 让 Python/Node 子进程强制 UTF-8 输出，父进程按对应编码解码，避免 GBK 乱码
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # cmd 内建命令（dir/type 等）输出是系统代码页（GBK），其余按 UTF-8 解码
        encoding = "gbk" if program_lower in _CMD_BUILTINS else "utf-8"
        result = subprocess.run(
            argv, capture_output=True, text=True,
            encoding=encoding, errors="replace",
            timeout=30, stdin=subprocess.DEVNULL, cwd=cwd, env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out.strip() or "（无输出）"
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

def _search_bing(query: str, max_results: int = 5) -> list[dict]:
    """Bing 网页搜索 — 抓取搜索结果（中文搜索效果好，国内可访问）。

    必须带 session cookie(SRCHHPGUSR) + mkt 参数，否则 Bing 对无头请求返回
    降级的固定结果集（不同查询返回同一批内容）。带 cookie 才能按查询返回真实结果。
    """
    import requests as _requests
    from bs4 import BeautifulSoup as _BeautifulSoup
    try:
        _s = _requests.Session()
        _s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        _s.cookies.set("SRCHHPGUSR", "SRCHLANG=zh-Hans")
        resp = _s.get(
            "https://www.bing.com/search",
            params={"q": query, "mkt": "zh-CN", "setlang": "zh-CN"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        soup = _BeautifulSoup(resp.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            title_el = li.select_one("h2 a") or li.select_one("a")
            snippet_el = li.select_one(".b_caption p") or li.select_one("p")
            if title_el:
                results.append({
                    "title": title_el.get_text(strip=True),
                    "href": title_el.get("href", ""),
                    "body": snippet_el.get_text(strip=True) if snippet_el else "",
                })
            if len(results) >= max_results:
                break
        return results
    except Exception:
        return []


def _fetch_page_text(href: str, timeout: int = 8) -> str:
    """抓取网页文本内容"""
    if not href or not href.startswith("http"):
        return ""
    import requests as _requests
    from bs4 import BeautifulSoup as _BeautifulSoup
    try:
        resp = _requests.get(href, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=timeout, allow_redirects=True)
        if resp.status_code != 200 or not resp.text:
            return ""
        soup = _BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        return "\n".join(lines)[:2000]
    except Exception:
        return ""


def _tool_web_search(args: dict) -> str:
    """联网搜索 — Bing 直接搜索，返回原始结果 + 网页内容（无 AI 中间处理）"""
    query = args.get("query", "").strip()
    if not query:
        return "错误: 请提供搜索关键词"

    results = _search_bing(query)
    if not results:
        return f"未找到关于「{query}」的搜索结果。"

    lines = [f"=== Bing 搜索结果: {query} ==="]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        href = r.get("href", "")
        body = r.get("body", "")
        lines.append(f"\n[{i}] {title}")
        lines.append(f"    URL: {href}")
        if body:
            lines.append(f"    摘要: {body}")
        # 抓取前 3 条结果的页面内容
        if i <= 3:
            content = _fetch_page_text(href)
            if content:
                lines.append(f"    页面内容:\n{content}")

    return "\n".join(lines)

# 系统图片目录（截图/上传）——vision_analyze 只读访问
_SCREENSHOT_DIR = os.path.join(PROJECT_ROOT, "data", "screenshots")
_UPLOAD_DIR = os.path.join(PROJECT_ROOT, "data", "uploads")


def _resolve_vision_image(image_path: str) -> str:
    """vision_analyze 专用路径解析：工作区相对路径 + 截图/上传目录。

    截图（data/screenshots）和上传（data/uploads）是系统产生的图片位置，
    不在工作区 data/Workspace 内，需单独放行。返回绝对路径，非法返回空串。
    """
    image_path = (image_path or "").strip()
    if not image_path:
        return ""
    # /screenshots/xxx、/uploads/xxx → 映射到系统目录（须先于 isabs 判断，因 / 在 Windows 也算绝对路径分隔符）
    for prefix, base in (("/screenshots/", _SCREENSHOT_DIR), ("/uploads/", _UPLOAD_DIR)):
        if image_path.startswith(prefix):
            return os.path.join(base, image_path[len(prefix):])
    # 盘符开头的绝对路径（如 E:\...\screenshots\s.png）直接放行——vision 只读，风险可控
    if os.path.isabs(image_path):
        return image_path
    # 工作区相对路径
    try:
        return safe_path(image_path)
    except ValueError:
        return ""


def _tool_vision_analyze(args: dict) -> str:
    """分析图片内容（使用视觉模型）。image_path 支持工作区相对路径 / 截图 / 上传目录 / 绝对路径"""
    image_path = args.get("image_path", "")
    question = args.get("question", "请详细描述这张图片的内容")

    if not image_path:
        return "错误: 未指定图片路径"

    full = _resolve_vision_image(image_path)
    if not full:
        return f"错误: 无法访问: {image_path}"

    if not os.path.isfile(full):
        return f"错误: 图片文件不存在: {full}"

    # 检查是否是图片格式
    ext = os.path.splitext(full)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
    mime = mime_map.get(ext)
    if not mime:
        return f"错误: 不支持的图片格式 {ext}，支持: {', '.join(mime_map.keys())}"

    try:
        with open(full, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"读取图片失败: {e}"

    try:
        from openai import OpenAI
        import config.api_config as cfg
        import re as _re
        vision_client = OpenAI(
            api_key=cfg.VISION_LLM_KEY,
            base_url=cfg.VISION_LLM_BASE_URL,
            default_headers=cfg.opencode_headers(cfg.VISION_LLM_BASE_URL) or None,
        )
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


def _tool_locate_on_screen(args: dict) -> str:
    """在屏幕上定位元素：视觉模型识别目标中心，返回可直接用于 mouse_click 的屏幕坐标。

    对 OCR 识别不到文字的图标/按钮（如图标、颜色块、非文本元素）尤其有用。
    """
    image_path = (args.get("image_path") or "").strip()
    target = (args.get("target") or "").strip()
    if not target:
        return "错误: 未指定 target（要定位的元素描述）"

    full = _resolve_vision_image(image_path)
    if not full or not os.path.isfile(full):
        return f"错误: 无效或不存在图片路径: {image_path}"

    try:
        from PIL import Image, ImageDraw
        img = Image.open(full).convert("RGB")
        img_w, img_h = img.size
    except Exception as e:
        return f"读取图片失败: {e}"

    # 叠加网格线，帮助视觉模型稳定估算位置（仅用于识别，不影响原图）
    try:
        grid_img = img.copy()
        draw = ImageDraw.Draw(grid_img)
        cols, rows = 6, 6
        for i in range(1, cols):
            x = int(img_w * i / cols)
            draw.line([(x, 0), (x, img_h)], fill=(255, 90, 90), width=1)
        for j in range(1, rows):
            y = int(img_h * j / rows)
            draw.line([(0, y), (img_w, y)], fill=(255, 90, 90), width=1)
        draw.rectangle([0, 0, img_w - 1, img_h - 1], outline=(90, 220, 120), width=2)
    except Exception:
        grid_img = img.copy()

    import base64 as _b64
    import io as _io
    buf = _io.BytesIO()
    grid_img.save(buf, format="PNG")
    b64 = _b64.b64encode(buf.getvalue()).decode()

    try:
        from openai import OpenAI
        import config.api_config as cfg
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
        ext = os.path.splitext(full)[1].lower()
        mime = mime_map.get(ext, "image/png")
        prompt = (
            f"图片宽 {img_w} 像素，高 {img_h} 像素，已覆盖红色网格线（6x6）便于估算位置。\n"
            f"请找出「{target}」，返回它的中心点坐标。\n"
            f"要求：只输出 JSON，格式 {{\"x\": <int>, \"y\": <int>}}；"
            f"x 范围 0~{img_w-1}，y 范围 0~{img_h-1}。不要输出其它文字。"
        )
        vc = OpenAI(
            api_key=cfg.VISION_LLM_KEY,
            base_url=cfg.VISION_LLM_BASE_URL,
            default_headers=cfg.opencode_headers(cfg.VISION_LLM_BASE_URL) or None,
        )
        vr = vc.chat.completions.create(
            model=cfg.VISION_LLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            temperature=0.2,
            max_tokens=200,
        )
        content = (vr.choices[0].message.content or "").strip()
        import re as _re
        m = _re.search(r'\{[^{}]*\}', content)
        if not m:
            return f"[locate] 无法解析定位结果: {content[:200]}"
        pair = json.loads(m.group(0))
        imx = int(pair.get("x", 0))
        imy = int(pair.get("y", 0))
        imx = max(0, min(imx, img_w - 1))
        imy = max(0, min(imy, img_h - 1))
    except Exception as e:
        return f"[locate] 定位失败: {e}"

    # 图片像素 → 屏幕坐标（与截图同一套换算）
    sx = sy = 1.0
    try:
        import pyautogui as _pag
        _pw, _ph = _pag.size()
        sx = _pw / img_w if img_w else 1.0
        sy = _ph / img_h if img_h else 1.0
    except Exception:
        sx = sy = 1.0
    scr_x = int(round(imx * sx))
    scr_y = int(round(imy * sy))

    return (
        f"[定位] {target}: 图片中心 ({imx},{imy}) → 屏幕坐标 ({scr_x},{scr_y})。\n"
        f"可直接 mouse_click(x={scr_x}, y={scr_y})。"
    )


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
        # 图片 → 附带可访问 URL，前端展开可显示缩略图
        ext = os.path.splitext(dest)[1].lower()
        img_line = ""
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"} and WORKSPACE_DIR:
            from urllib.parse import quote
            rel = os.path.relpath(dest, WORKSPACE_DIR).replace("\\", "/")
            img_line = f"\n[IMAGE] /workspace/{quote(rel)}"
        # 返回完整绝对路径，方便后续工具（如 vision_analyze）直接引用
        return f"下载成功: {dest} ({size} 字节){img_line}"
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

    # 截图坐标 → pyautogui 屏幕坐标的换算系数（兼容 DPI 缩放 / 虚拟屏幕分辨率差异）。
    # 截图返回的是"捕获分辨率"像素，而 mouse_click 走 pyautogui 的坐标空间；两者因
    # DPI 缩放可能不一致。按"pyautogui 主屏尺寸 / 捕获尺寸"校准，保证 OCR 坐标可直接点击。
    sx = sy = 1.0
    try:
        import pyautogui as _pag
        _pw, _ph = _pag.size()
        if img.size[0] > 0:
            sx = _pw / img.size[0]
        if img.size[1] > 0:
            sy = _ph / img.size[1]
    except Exception:
        sx = sy = 1.0

    ocr_text = _ocr_image(img, sx, sy)
    print(f"[screenshot] 完成 OCR={len(ocr_text)}字, scale=({sx:.3f},{sy:.3f})",
          file=sys.stderr, flush=True)

    return (
        f"截图: {path} ({img.size[0]}x{img.size[1]})\n"
        f"{window_info}"
        + (f"\n\n[屏幕文字及像素坐标]（格式: 文字 (x,y)，坐标为屏幕像素，可直接用于 mouse_click 点击该文字所在元素）\n{ocr_text}" if ocr_text else "")
        + (f"\n[提示] 图标/按钮等无文字元素，OCR 无法给坐标；请用 locate_on_screen(image_path=上面路径, target=元素描述) 定位后再 mouse_click，不要凭空估计坐标。")
        + f"\n[IMAGE] /screenshots/screenshot_{ts}.png"
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

def _ocr_image(img, sx: float = 1.0, sy: float = 1.0) -> str:
    """OCR 识别图片文字，每行附带文字中心的屏幕像素坐标 (x,y)。

    坐标先换算回原始捕获分辨率，再按 (sx,sy) 校准到 pyautogui 屏幕坐标，
    保证可直接用于 mouse_click。未就绪则返回提示。"""
    reader = _get_ocr_reader()
    if reader is None:
        return "(OCR 模型加载中，请稍后再试)"

    try:
        import numpy as np
        orig_w = img.size[0]
        if orig_w > 1280:
            img = img.resize((1280, int(img.size[1] * 1280 / orig_w)))
        scale = orig_w / img.size[0]  # 缩放图坐标 → 原始分辨率坐标的换算系数
        arr = np.array(img)
        items = reader.readtext(arr, detail=1)

        results = []
        for bbox, text, conf in items:
            text = str(text).strip()
            if conf <= 0.4 or not text:
                continue
            # bbox 为四点坐标 [(x,y)x4]，取中心点，先还原到原始分辨率，再校准到屏幕坐标
            cx = int(sum(p[0] for p in bbox) / 4 * scale * sx)
            cy = int(sum(p[1] for p in bbox) / 4 * scale * sy)
            results.append((cx, cy, text))

        # 按阅读顺序排序：y 行容差 24px（同视为一行），行内按 x
        results.sort(key=lambda r: (r[1] // 24, r[0]))
        lines = [f"{t} ({x},{y})" for x, y, t in results]
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

def _normalize_state(content: str) -> str:
    """把 AI 写入的内容归一化为固定两行：心情 + 好感度，剔除多余文字。

    兼容 `心情: 开心` / `心情：开心`，字段可出现在任意位置；完全解析不到时保留原文。
    """
    import re as _re
    mood = fav = None
    m = _re.search(r'心情\s*[:：]\s*(.+?)(?=\s*好感度|，|。|；|,|;|\n|$)', content)
    if m:
        mood = m.group(1).strip()
    # 好感度优先匹配数字（如 95/100、100），避免把后面的口癖词也带进去
    m = _re.search(r'好感度\s*[:：]\s*([0-9０-９]{1,3}(?:\s*[／/：:]\s*[0-9０-９]{1,3})?)', content)
    if not m:
        m = _re.search(r'好感度\s*[:：]\s*(.+?)(?=，|。|；|,|;|\n|$)', content)
    if m:
        fav = m.group(1).strip()
    if mood is None and fav is None:
        return content
    # 缺失字段回退到当前 state.md 已有值
    if mood is None or fav is None:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if mood is None:
                        mm = _re.search(r'心情\s*[:：]\s*(.+)$', line)
                        if mm: mood = mm.group(1).strip()
                    if fav is None:
                        ff = _re.search(r'好感度\s*[:：]\s*(.+)$', line)
                        if ff: fav = ff.group(1).strip()
        except Exception:
            pass
    mood = mood or "平静"
    fav = fav or "50/100"
    return f"心情: {mood}\n好感度: {fav}"

def _tool_update_state(args: dict) -> str:
    content = args.get("content", "").strip()
    if not content: return "错误: content 为空"
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    norm = _normalize_state(content)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(norm + "\n")
    return f"状态已更新:\n{norm}"

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

# ── 图片映射表 ──
def _tool_image_set(args: dict) -> str:
    from src.memory.images import set_image
    return set_image(args.get("key", ""), args.get("url", ""))

def _tool_image_list(_args: dict) -> str:
    from src.memory.images import list_images
    return list_images()

def _tool_image_delete(args: dict) -> str:
    from src.memory.images import delete_image
    return delete_image(args.get("key", ""))


# ======================================================================
# 浏览器操控（Playwright async API）
#
# 设计：snapshot + ref 模式 —— AI 每步拿到带编号的元素列表，
# 按 ref 编号点击/输入（服务端把编号映射回 CSS 选择器），
# 比让视觉模型输出像素坐标稳定得多。每次操作后自动返回最新快照。
#
# 线程模型：MCP 服务器主循环是 WindowsSelectorEventLoopPolicy（stdio 需要），
# 而 Selector 循环不支持 asyncio 子进程，Playwright 驱动起不来。
# 故 Playwright 跑在独立线程的 Proactor 循环里，工具调用线程安全桥接过去。
# ======================================================================
_pw = None             # Playwright 实例（仅 Playwright 线程访问）
_pw_browser = None
_pw_page = None
_pw_headless = True
_pw_refs = {}          # ref 编号 → CSS 选择器（browser_snapshot 生成，操作后刷新）
_pw_loop = None        # Playwright 专用事件循环（Proactor）
_pw_thread = None
_pw_ready = threading.Event()

# 页面结构采集脚本：遍历可见的可交互元素，生成唯一 CSS 选择器 + 标签文本
_SNAPSHOT_JS = """() => {
  const out = [];
  const sel = 'a[href], button, input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="checkbox"], [role="menuitem"], [role="option"], [role="textbox"], [onclick], [contenteditable="true"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    // 构造唯一 CSS 选择器（有 id 直接用，否则沿 DOM 树拼 nth-of-type 路径）
    let path = '';
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      let s = node.tagName.toLowerCase();
      if (node.id) { path = '#' + CSS.escape(node.id) + (path ? ' > ' + path : ''); break; }
      const parent = node.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sibs.length > 1) s += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      path = s + (path ? ' > ' + path : '');
      node = parent;
    }
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 60);
    out.push({
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || el.getAttribute('role') || '').toLowerCase(),
      label: label,
      selector: path,
    });
  }
  return out;
}"""


async def _pw_err() -> str:
    return "错误: 未安装 playwright。请运行: pip install playwright && python -m playwright install chromium"


def _pw_thread_main():
    """Playwright 专用线程：跑 Proactor 事件循环（Windows 上 Selector 循环
    不支持 asyncio 子进程，Playwright 驱动进程无法启动）。
    注意不能用 asyncio.new_event_loop()：它按全局 policy 创建，
    而主入口已把 policy 设为 SelectorEventLoop。"""
    global _pw_loop
    if sys.platform == "win32":
        _pw_loop = asyncio.ProactorEventLoop()
    else:
        _pw_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_pw_loop)
    _pw_ready.set()
    try:
        _pw_loop.run_forever()
    finally:
        _pw_loop.close()


def _pw_start_thread():
    global _pw_thread
    if _pw_thread is None or not _pw_thread.is_alive():
        _pw_ready.clear()
        _pw_thread = threading.Thread(target=_pw_thread_main, daemon=True, name="playwright")
        _pw_thread.start()
        _pw_ready.wait(timeout=5)


def _pw_reset_browser():
    """超时后清理浏览器/Playwright，使下一次调用重新启动，避免卡死的操作残留。"""
    global _pw, _pw_browser, _pw_page
    if _pw_loop is None or _pw_loop.is_closed():
        return
    async def _close():
        if _pw_browser is not None:
            try:
                await _pw_browser.close()
            except Exception:
                pass
        if _pw is not None:
            try:
                await _pw.stop()
            except Exception:
                pass
        _pw = _pw_browser = _pw_page = None
    try:
        fut = asyncio.run_coroutine_threadsafe(_close(), _pw_loop)
        fut.result(timeout=3)
    except Exception:
        pass


async def _pw_call(coro, timeout: float = 50):
    """把协程提交到 Playwright 线程执行并等待结果（非阻塞桥接）"""
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    _pw_start_thread()
    if _pw_loop is None or _pw_loop.is_closed():
        return "错误: Playwright 线程启动失败"
    fut = asyncio.run_coroutine_threadsafe(coro, _pw_loop)
    loop = asyncio.get_running_loop()
    aio_fut = loop.create_future()

    def _done():
        if aio_fut.done():
            return
        try:
            aio_fut.set_result(fut.result())
        except Exception as e:
            aio_fut.set_exception(e)

    fut.add_done_callback(lambda _f: loop.call_soon_threadsafe(_done))
    try:
        return await asyncio.wait_for(aio_fut, timeout=timeout)
    except asyncio.TimeoutError:
        fut.cancel()
        _pw_reset_browser()
        return f"错误: 浏览器操作超时（{timeout:.0f}s）"


async def _ensure_browser(headless: bool = True):
    """懒初始化并复用浏览器实例。headless 模式变化时重启浏览器。"""
    global _pw, _pw_browser, _pw_page, _pw_headless
    if _pw is None:
        _pw = await async_playwright().start()
    if _pw_browser is not None and _pw_headless != headless:
        try:
            await _pw_browser.close()
        except Exception:
            pass
        _pw_browser = None
        _pw_page = None
    _pw_headless = headless
    if _pw_browser is None or not _pw_browser.is_connected():
        _pw_browser = await _pw.chromium.launch(headless=headless)
    if _pw_page is None or _pw_page.is_closed():
        ctx = await _pw_browser.new_context(
            viewport={"width": 1280, "height": 800}, locale="zh-CN")
        _pw_page = await ctx.new_page()
    return _pw_page


async def _pw_settle(page):
    """操作后等待页面稳定。先等最终文档的 load（点击/提交可能触发重定向链，
    networkidle 会在中间页提前触发），再等网络空闲（3s 上限，防长轮询页面卡死）。"""
    try:
        await page.wait_for_load_state("load", timeout=10000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def _browser_snapshot_text(page, max_elems: int = 80, max_text: int = 2500) -> str:
    """采集当前页面结构，刷新 ref 映射表，返回给 AI 的文本快照"""
    global _pw_refs
    elems = await page.evaluate(_SNAPSHOT_JS)
    # JS 渲染型页面元素可能分批出现，轮询直到数量稳定（最多再等 3 轮×1s）
    for _ in range(3):
        await page.wait_for_timeout(1000)
        cur = await page.evaluate(_SNAPSHOT_JS)
        if len(cur) <= len(elems):
            break
        elems = cur
    _pw_refs = {}
    lines = []
    for i, el in enumerate(elems[:max_elems], 1):
        _pw_refs[i] = el["selector"]
        kind = el.get("type") or ""
        label = el.get("label") or ""
        if el["tag"] in ("input", "textarea", "select"):
            desc = f"{el['tag']}[{kind}]" + (f' "{label}"' if label else "")
        else:
            desc = el["tag"] + (f' "{label}"' if label else "")
        lines.append(f"[{i}] {desc}")
    title = await page.title()
    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    body = re.sub(r"\n{3,}", "\n\n", body or "").strip()
    out = [f"页面: {title}", f"URL: {page.url}"]
    if body:
        out.append(f"\n[可见文本节选]\n{body[:max_text]}" +
                   (f"\n...(截断，共 {len(body)} 字符，完整内容用 browser_read)" if len(body) > max_text else ""))
    out.append(f"\n[可交互元素]（ref 编号用于 browser_click / browser_type）")
    out.extend(lines if lines else ["(未发现可交互元素)"])
    return "\n".join(out)


async def _do_browser_open(args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    url = (args.get("url") or "").strip()
    if not url:
        return "错误: 未指定 url"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    page = await _ensure_browser(headless=not bool(args.get("visible", False)))
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        # 超时但页面可能已部分加载，继续尝试取快照
        if "Timeout" not in type(e).__name__ and "timeout" not in str(e).lower():
            return f"打开页面失败: {e}"
    await _pw_settle(page)
    snap = await _browser_snapshot_text(page)
    return f"已打开: {page.url}\n\n{snap}"


async def _do_browser_snapshot(_args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    return await _browser_snapshot_text(_pw_page)


def _pw_ref_selector(ref) -> str:
    try:
        ref = int(ref)
    except (TypeError, ValueError):
        raise ValueError(f"ref 必须是数字，收到: {ref!r}")
    selector = _pw_refs.get(ref)
    if selector is None:
        raise ValueError(f"ref={ref} 不存在或已过期（页面变化后编号失效），请先 browser_snapshot 获取最新编号")
    return selector


async def _do_browser_click(args: dict) -> str:
    global _pw_page
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    try:
        selector = _pw_ref_selector(args.get("ref"))
    except ValueError as e:
        return f"错误: {e}"
    pages_before = list(_pw_page.context.pages)
    try:
        await _pw_page.locator(selector).first.click(timeout=8000)
    except Exception as e:
        return f"点击失败: {e}\n（元素可能已被页面更新移除，试试重新 browser_snapshot）"
    await _pw_settle(_pw_page)
    # target=_blank 的链接会开新标签页，此时切换到新页面继续操作
    new_pages = [p for p in _pw_page.context.pages if p not in pages_before]
    if new_pages:
        _pw_page = new_pages[-1]
        await _pw_settle(_pw_page)
    snap = await _browser_snapshot_text(_pw_page)
    return f"已点击 [ref={args.get('ref')}]。\n\n{snap}"


async def _do_browser_type(args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    text = args.get("text", "")
    if not text:
        return "错误: text 为空"
    try:
        selector = _pw_ref_selector(args.get("ref"))
    except ValueError as e:
        return f"错误: {e}"
    try:
        loc = _pw_page.locator(selector).first
        await loc.click(timeout=8000)
        await loc.fill(text, timeout=8000)
    except Exception as e:
        return f"输入失败: {e}\n（元素可能已被页面更新移除，试试重新 browser_snapshot）"
    result = f"已在 [ref={args.get('ref')}] 输入: {text}"
    if args.get("submit"):
        await _pw_page.keyboard.press("Enter")
        result += " 并按回车提交"
        await _pw_settle(_pw_page)
    snap = await _browser_snapshot_text(_pw_page)
    return f"{result}。\n\n{snap}"


async def _do_browser_press(args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    key = (args.get("key") or "").strip()
    if not key:
        return "错误: 未指定 key"
    try:
        await _pw_page.keyboard.press(key)
    except Exception as e:
        return f"按键失败: {e}（示例: enter、escape、tab、ctrl+a）"
    return f"已按键: {key}"


async def _do_browser_scroll(args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    direction = args.get("direction", "down")
    amount = max(1, min(10, int(args.get("amount", 2))))
    delta = amount * 400 * (1 if direction == "down" else -1)
    try:
        await _pw_page.evaluate(f"() => window.scrollBy(0, {delta})")
    except Exception as e:
        return f"滚动失败: {e}"
    snap = await _browser_snapshot_text(_pw_page)
    return f"已向 {direction} 滚动。\n\n{snap}"


async def _do_browser_read(args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    max_chars = max(500, min(20000, int(args.get("max_chars", 4000))))
    try:
        body = await _pw_page.evaluate("() => document.body ? document.body.innerText : ''")
        if len(body.strip()) < 200:
            # JS 渲染型页面内容可能比 networkidle 更晚就绪，等待后重读一次
            await _pw_page.wait_for_timeout(1200)
            body = await _pw_page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception as e:
        return f"读取页面失败: {e}"
    body = re.sub(r"\n{3,}", "\n\n", (body or "").strip())
    if not body:
        return f"页面无正文文本（标题: {await _pw_page.title()}）。可能是动态渲染页，试试 browser_screenshot + vision_analyze"
    title = await _pw_page.title()
    return f"页面: {title}\nURL: {_pw_page.url}\n\n{body[:max_chars]}" + \
        (f"\n...(已截断，共 {len(body)} 字符)" if len(body) > max_chars else "")


async def _do_browser_screenshot(_args: dict) -> str:
    if not _HAS_PLAYWRIGHT:
        return await _pw_err()
    if _pw_page is None or _pw_page.is_closed():
        return "错误: 浏览器未打开任何页面，请先 browser_open"
    os.makedirs(_SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_SCREENSHOT_DIR, f"browser_{ts}.png")
    try:
        await _pw_page.screenshot(path=path)
    except Exception as e:
        return f"截图失败: {e}"
    return f"网页截图: {path}\n[IMAGE] /screenshots/browser_{ts}.png"


async def _do_browser_close(_args: dict) -> str:
    global _pw_browser, _pw_page, _pw_refs
    if _pw_browser is not None:
        try:
            await _pw_browser.close()
        except Exception:
            pass
    _pw_browser = None
    _pw_page = None
    _pw_refs = {}
    return "浏览器已关闭"


# ── MCP 工具入口：桥接到 Playwright 线程（超时需小于客户端 call_tool 的 60s）──
def _tool_browser_open(args: dict):
    return _pw_call(_do_browser_open(args), timeout=55)

def _tool_browser_snapshot(args: dict):
    return _pw_call(_do_browser_snapshot(args), timeout=35)

def _tool_browser_click(args: dict):
    return _pw_call(_do_browser_click(args), timeout=35)

def _tool_browser_type(args: dict):
    return _pw_call(_do_browser_type(args), timeout=45)

def _tool_browser_press(args: dict):
    return _pw_call(_do_browser_press(args), timeout=20)

def _tool_browser_scroll(args: dict):
    return _pw_call(_do_browser_scroll(args), timeout=35)

def _tool_browser_read(args: dict):
    return _pw_call(_do_browser_read(args), timeout=30)

def _tool_browser_screenshot(args: dict):
    return _pw_call(_do_browser_screenshot(args), timeout=25)

def _tool_browser_close(args: dict):
    return _pw_call(_do_browser_close(args), timeout=20)


# ======================================================================
# 桌面控制（pyautogui）— 操作真实屏幕的鼠标键盘
#
# 安全设计：
# 1. 总开关 desktop_control_enabled（config/app_config.json，设置面板可热切换，
#    每次调用实时读取，无需重启工具子进程）
# 2. pyautogui 自带 FAILSAFE：鼠标移到屏幕左上角会抛异常中止（不关闭）
# 3. 破坏性操作前的确认由 system prompt 约束 AI 走 ask_user
# ======================================================================
_DESKTOP_CONTROL_DEFAULT = True


def _desktop_control_enabled() -> bool:
    try:
        cfg_path = os.path.join(PROJECT_ROOT, "config", "app_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("desktop_control_enabled", _DESKTOP_CONTROL_DEFAULT))
    except Exception:
        return _DESKTOP_CONTROL_DEFAULT


def _desktop_check() -> str:
    if not _HAS_PYAUTOGUI:
        return "错误: 未安装 pyautogui，请运行 pip install pyautogui"
    if not _desktop_control_enabled():
        return "桌面控制已被用户禁用（设置面板 → 对话设置 → 桌面控制）"
    return ""


def _tool_list_windows(_args: dict) -> str:
    import pygetwindow as gw
    titles = [t for t in gw.getAllTitles() if isinstance(t, str) and t.strip()]
    if not titles:
        return "未发现可见窗口"
    return "当前窗口列表:\n" + "\n".join(f"- {t}" for t in titles[:50])


def _tool_activate_window(args: dict) -> str:
    err = _desktop_check()
    if err:
        return err
    title = (args.get("title") or "").strip()
    if not title:
        return "错误: 未指定 title"
    import pygetwindow as gw
    wins = gw.getWindowsWithTitle(title)
    if not wins:
        return f"未找到标题包含「{title}」的窗口。可先用 list_windows 查看所有窗口标题"
    w = wins[0]
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        import time as _time
        _time.sleep(0.3)
        return f"已激活窗口: {w.title}"
    except Exception as e:
        return f"激活窗口失败: {e}（窗口: {w.title}）"


def _tool_mouse_click(args: dict) -> str:
    err = _desktop_check()
    if err:
        return err
    try:
        x, y = int(args.get("x")), int(args.get("y"))
    except (TypeError, ValueError):
        return "错误: x/y 必须是整数坐标"
    button = args.get("button", "left")
    if button not in ("left", "right", "middle"):
        return f"错误: button 只能是 left/right/middle，收到 {button}"
    clicks = max(1, min(3, int(args.get("clicks", 1))))
    try:
        pyautogui.moveTo(x, y, duration=0.25)
        pyautogui.click(button=button, clicks=clicks, interval=0.15)
    except pyautogui.FailSafeException:
        return "已触发安全中止（鼠标位于屏幕角落）。如非用户手动中止，请检查坐标是否位于屏幕边缘"
    except Exception as e:
        return f"点击失败: {e}"
    return f"已点击 ({x}, {y}) button={button} clicks={clicks}。建议 take_screenshot 确认操作效果"


def _tool_mouse_scroll(args: dict) -> str:
    err = _desktop_check()
    if err:
        return err
    direction = args.get("direction", "down")
    amount = max(1, min(20, int(args.get("amount", 3))))
    delta = amount if direction == "down" else -amount
    try:
        pyautogui.scroll(delta)
    except Exception as e:
        return f"滚动失败: {e}"
    return f"已向 {direction} 滚动 {amount} 格"


def _tool_keyboard_type(args: dict) -> str:
    err = _desktop_check()
    if err:
        return err
    text = args.get("text", "")
    if not text:
        return "错误: text 为空"
    try:
        try:
            text.encode("ascii")
            pyautogui.typewrite(text, interval=0.03)
        except UnicodeEncodeError:
            # 非 ASCII（中文等）typewrite 不支持，走剪贴板粘贴
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
    except pyautogui.FailSafeException:
        return "已触发安全中止（鼠标位于屏幕角落）"
    except Exception as e:
        return f"输入失败: {e}"
    return f"已输入 {len(text)} 个字符到当前焦点窗口"


# 常用键名归一化（pyautogui 的命名和直觉略有差异）
_KEY_ALIASES = {
    "esc": "escape", "del": "delete", "ctrl": "ctrl", "alt": "alt",
    "win": "winleft", "meta": "winleft", "super": "winleft", "cmd": "winleft",
    "return": "enter", "space": "space", "pgup": "pageup", "pgdn": "pagedown",
}


def _tool_keyboard_press(args: dict) -> str:
    err = _desktop_check()
    if err:
        return err
    key = (args.get("key") or "").strip().lower()
    if not key:
        return "错误: 未指定 key"
    parts = [_KEY_ALIASES.get(p, p) for p in key.split("+") if p]
    if not parts:
        return "错误: key 解析为空"
    try:
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
    except pyautogui.FailSafeException:
        return "已触发安全中止（鼠标位于屏幕角落）"
    except Exception as e:
        return f"按键失败: {e}（示例: enter、esc、tab、alt+f4、ctrl+c）"
    return f"已按键: {'+'.join(parts)}"


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
    "image_set":          _tool_image_set,
    "image_list":         _tool_image_list,
    "image_delete":       _tool_image_delete,
    # --- 浏览器操控 ---
    "browser_open":       _tool_browser_open,
    "browser_snapshot":   _tool_browser_snapshot,
    "browser_click":      _tool_browser_click,
    "browser_type":       _tool_browser_type,
    "browser_press":      _tool_browser_press,
    "browser_scroll":     _tool_browser_scroll,
    "browser_read":       _tool_browser_read,
    "browser_screenshot": _tool_browser_screenshot,
    "browser_close":      _tool_browser_close,
    # --- 桌面控制 ---
    "list_windows":       _tool_list_windows,
    "activate_window":    _tool_activate_window,
    "mouse_click":        _tool_mouse_click,
    "mouse_scroll":       _tool_mouse_scroll,
    "keyboard_type":      _tool_keyboard_type,
    "keyboard_press":     _tool_keyboard_press,
    "locate_on_screen":   _tool_locate_on_screen,
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
        # 同步工具可能在内部阻塞（截图/OCR/命令/视觉/文件等），放线程池执行，
        # 避免卡死 MCP 单线程事件循环，导致其它工具调用全部等待。
        if inspect.iscoroutinefunction(handler):
            result = await handler(arguments)
        else:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, functools.partial(handler, arguments))
        return [types.TextContent(type="text", text=str(result))]
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
