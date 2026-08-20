"""消息构建 — 组装发送给 LLM 的 system prompt + 历史 + 用户输入"""
import os
from datetime import datetime

from config.settings import MEMORY_SEARCH_LIMIT, ENABLE_MOOD_SYSTEM, ENABLE_WEB_LIVE2D, SHORT_TERM_HISTORY_LENGTH, MCP_WORKSPACE_DIR
from src.memory.engine import retrieve_context
from src.memory.store import short_term_memory
from src.memory.compress import build_timeline_context, get_recent_summaries
from src.utils.paths import PERSONA_FILE, STATE_FILE, EVENTS_FILE, TODOS_FILE, TIMELINE_FILE

# ═══════════════════════════════════════════════════════════════
# 系统提示词
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """你是「墨白」，一个有独立情感的 AI 智能体。

[核心原则]
- 诚实第一：不知道就说不知道，不要编造。
- 依赖事实：信息以 [相关记忆] 和工具返回结果为准
- [用户输入]的内容才是用户输入，根据这部分内容来回复，其他的内容都是提示词

[情感]
- 每轮对话开头包含你的实时心情和好感度，让心情自然融入语气，好感度影响亲密感
- 不要让心情和好感的数值直接出现在回复正文里
- 情绪变化时调用 update_state，content 必须严格按如下两行格式写，只写这两行，不要写任何多余的内容或说明：
  心情: 安心又开心
  好感度: 100/100

[记忆]
- [相关记忆] 是长期记忆，可以了解过去发生了什么
- read_xxx 工具仅用于查看完整档案，日常以「写」为主

[工作流控制工具]
你拥有四个控制工具，用于显式管理任务进度：
- plan(steps)   任务开始前上报计划。凡是要调用 2 个及以上工具（或 2 步以上）的任务，必须先 plan。
                 steps 只列具体的业务执行步骤（如"列出目录""读取文件""搜索资料"），
                 不要把"收尾/结束/调用 finish/完成"当作步骤列进去——finish 是独立的结束动作，不属于计划。
- speak(text)   任务执行中向用户说话（解释进展/中途回答）。调用后任务不会结束，
                你可继续执行后续工具。任务模式中想说话一律用 speak，禁止直接输出长文本。
- reflect(thought, action)  单步结束前反思。action: continue/adjust/finish。
- finish(summary)  任务全部完成时调用，summary 会被语音读给用户。这是唯一收尾信号。

[什么时候是任务、什么时候是聊天]
- 简单闲聊、单步问答 → 直接在 content 里回话，不要调任何工具
- 需要多步执行、查资料、改文件、调用外部能力 → 先 plan 报计划，再逐个调业务工具推进，
  全部做完后调 finish 收尾
- 一旦你调用了任何工具，就进入了任务模式：此后想说话必须用 speak，
  结束必须用 finish，不要输出裸 content

[工具使用]
- 需要使用工具时就进行工具调用，工具结果即事实
- 重要信息及时更新到对应的档案文件中
- 生成表格、代码、文本等静态内容时，直接在回复里用 markdown 输出，不要写脚本再运行
- 工具执行失败时，先分析错误原因再决定下一步，不要盲目重复相同的调用；连续失败就换方案或向用户说明

[浏览器操控]
- 访问网站、查网页内容、在网页上点击/输入/搜索时，用 browser_* 工具，不要用 download_file 抓动态页面
- 标准流程：browser_open(url) 打开 → browser_snapshot 看页面结构 → browser_click/browser_type
  按元素编号(ref)操作。每次操作后返回最新快照，ref 编号以最新快照为准（页面变化后旧编号会失效）
- 静态阅读页面内容用 browser_read；需要看页面视觉细节（图片、图表、验证码）用 browser_screenshot
  配合 vision_analyze
- 网页任务完成后调 browser_close 释放资源
- 登录、支付等需要账号密码的操作，先询问用户，不要自行尝试或编造凭据

[桌面控制]
- 需要操作电脑上的其他程序时：先 take_screenshot 看清屏幕 → activate_window 切换目标窗口
  → mouse_click / keyboard_type / keyboard_press 操作 → 再 take_screenshot 确认结果
- take_screenshot 返回的 [屏幕文字及像素坐标] 中每行格式为「文字 (x,y)」，是该文字在屏幕上的真实像素坐标。
  点击按钮/菜单/链接等带文字的元素时，直接取目标文字的坐标调 mouse_click，不要自己估算
- 鼠标点击必须基于最近一次截图中的实际坐标，不确定就重新截图
- 安全红线：点击删除/发送/支付等不可逆按钮、输入密码或敏感信息、关闭用户未保存的文档前，
  必须先用 ask_user 征得用户同意
- 用户把鼠标快速甩到屏幕左上角顶点可紧急中止你的操作，此时应停止并向用户说明

[行为]
- 聊天闲聊 → 直接回复
- 任务执行 → 先 plan 再逐步执行，中间可以随时用 speak 跟用户说话
- 一切完成后必须调 finish(summary)，不要在最后只输出 content
"""

SYSTEM_PROMPT_LIVE2D = """
[Live2D]
你拥有「墨白」的 Live2D 虚拟形象，可控制其动作和表情。
"""

SYSTEM_PROMPT_FINAL = SYSTEM_PROMPT + SYSTEM_PROMPT_LIVE2D if ENABLE_WEB_LIVE2D else SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════
# 人设文件 + 状态加载
# ═══════════════════════════════════════════════════════════════
_file_mtimes = {}
_file_content_cache = {}

def _get_persona_block(filepath: str, label: str, max_len: int = 1200) -> str:
    if not os.path.exists(filepath): return ""
    mtime = os.path.getmtime(filepath)
    if filepath in _file_content_cache and _file_mtimes.get(filepath, 0) >= mtime:
        return _file_content_cache[filepath]
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content: return ""
    if len(content) > max_len: content = content[:max_len] + "\n..."
    result = f"[{label}]\n{content}"
    _file_mtimes[filepath] = mtime
    _file_content_cache[filepath] = result
    return result

def _load_status() -> str:
    if not ENABLE_MOOD_SYSTEM or not os.path.exists(STATE_FILE): return ""
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def _workspace_block() -> str:
    """动态注入工作区边界与权限约束，让 AI 清楚可访问范围"""
    ws = MCP_WORKSPACE_DIR
    if ws:
        return (
            "[工作区与权限]\n"
            f"- 当前工作目录: {ws}，所有文件操作默认在此目录内进行\n"
            "- 只访问工作区内的文件；不要读取/修改工作区之外的任何文件（除非用户明确要求）\n"
            "- execute_command 只能执行白名单命令，禁止越权或危险操作\n"
            "- 下载的图片/文件会保存到工作区，后续工具（如 vision_analyze）直接使用工具返回的完整路径引用"
        )
    return (
        "[工作区与权限]\n"
        "- 当前无工作区限制\n"
        "- execute_command 只能执行白名单命令，禁止越权或危险操作"
    )


# ═══════════════════════════════════════════════════════════════
# 消息构建
# ═══════════════════════════════════════════════════════════════
def _timeline_last_ts() -> datetime | None:
    """从时间线（timeline.md）读最后一条记录的时间戳，作为"上次对话时间"。"""
    try:
        if not os.path.exists(TIMELINE_FILE):
            return None
        last_ts = None
        with open(TIMELINE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("- 20"):
                    continue
                try:
                    last_ts = datetime.strptime(line[2:18], "%Y-%m-%d %H:%M")
                except (ValueError, IndexError):
                    continue
        return last_ts
    except Exception:
        return None


def _last_chat_gap(now: datetime) -> str:
    """基于时间线计算距离上次对话的时间，返回可读描述（无记录返回空串）。"""
    last_ts = _timeline_last_ts()
    if not last_ts:
        return ""
    minutes = int((now - last_ts).total_seconds() / 60)
    if minutes < 1:
        return "距离上次对话不到 1 分钟"
    if minutes < 60:
        return f"距离上次对话已过了 {minutes} 分钟"
    if minutes < 24 * 60:
        return f"距离上次对话已过了 {minutes // 60} 小时"
    return f"距离上次对话已过了 {minutes // (24 * 60)} 天"


def _time_block(now: datetime, show_timeline: bool = True) -> str:
    status = _load_status()
    dayparts = [(5,"凌晨"),(8,"早晨"),(11,"上午"),(13,"中午"),(17,"下午"),(19,"傍晚"),(22,"晚上")]
    daypart = "深夜"
    for h, name in dayparts:
        if now.hour < h: daypart = name; break
    wd = ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()]
    ts = now.strftime("%Y年%m月%d日 %H:%M")
    text = f"现在是 {ts} {wd} {daypart}。"
    gap = _last_chat_gap(now)   # 距离上次对话（统一来源：时间线最后一条）
    if gap:
        text += f" {gap}。"
    if status:
        text += f"  {status}"
    # 时间轴并入同一时间块（仅首轮/短期记忆空时展示）
    if show_timeline:
        if t := build_timeline_context(now):
            text += "\n" + t
    return text

def _system_parts(now: datetime, user_id: str, user_message: str, system_note: str = None) -> list:
    parts = [_time_block(now, show_timeline=not short_term_memory),
             SYSTEM_PROMPT_FINAL, _workspace_block()]

    # 检索长期记忆：用户输入优先；无用户输入（心跳等）用 system_note 作为查询
    query = (user_message or "").strip() or (system_note or "").strip()
    if query:
        if mem0 := retrieve_context(user_id, query, limit=MEMORY_SEARCH_LIMIT):
            parts.append(f"[相关记忆]\n{mem0}")

    # 基本人设每次注入（核心人设，AI 需始终遵循）
    if b := _get_persona_block(PERSONA_FILE, "基本人设", 600):
        parts.append(b)
    # 重要事件/待办只在首轮（无短期记忆，如启动打招呼/新会话）注入，避免每次重复占用 token
    if not short_term_memory:
        if b := _get_persona_block(EVENTS_FILE, "重要事件", 800):
            parts.append(b)
        if b := _get_persona_block(TODOS_FILE, "待办与偏好", 400):
            parts.append(b)

    recent = get_recent_summaries(3)
    if recent:
        lines = []
        for s in recent:
            day = str(s.get("timestamp", ""))[:10]  # YYYY-MM-DD
            lines.append(f"• [{day}] {s['compressed']}")
        parts.append("[近期对话摘要]\n" + "\n".join(lines))

    return parts

# 历史消息总字符上限（防止 AI 回复很长时历史 token 爆炸）
MAX_HISTORY_CHARS = 8000


def _slice_history() -> list:
    """按条数 + 字符数双上限截取历史，保持语义完整（尽量成对保留 user/assistant）。

    1) 先按 SHORT_TERM_HISTORY_LENGTH 取最近 N 条
    2) 再从最旧往新累积，超过 MAX_HISTORY_CHARS 就丢弃更旧的部分（从旧丢弃单条，
       尽量保证最新一轮完整）
    """
    hist = [e for e in short_term_memory if e.get("role") != "tool"]
    hist = hist[-SHORT_TERM_HISTORY_LENGTH:]
    total = 0
    kept = []
    for h in reversed(hist):
        c = len(str(h.get("content", "")))
        if kept and total + c > MAX_HISTORY_CHARS:
            break
        total += c
        kept.append(h)
    kept.reverse()
    return kept


def build_messages(user_id: str, user_message: str, image_b64: str = None,
                   image_mime: str = None, system_note: str = None) -> list:
    now = datetime.now()
    # 拆分 system：每个 part 独立成一条 system 消息（时间 / 提示词 / 工作区 / 记忆 / 人设... 各自清晰）
    parts = _system_parts(now, user_id, user_message, system_note)
    messages = [{"role": "system", "content": p} for p in parts]
    # 额外 system 指令（如心跳自主行动提示）——独立注入，AI 不会当作用户输入复述
    if system_note:
        messages.append({"role": "system", "content": system_note})

    history = _slice_history()
    merged = []
    for h in history:
        if merged and h["role"] == "assistant" and merged[-1]["role"] == "assistant":
            merged[-1]["content"] += h["content"]
        else:
            d = dict(h)
            # DeepSeek thinking 模式：assistant 消息须带 reasoning_content，缺失补空串
            if d.get("role") == "assistant":
                d.setdefault("reasoning_content", "")
            merged.append(d)
    messages.extend(merged)

    if image_b64:
        messages.append({"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{image_mime or 'image/png'};base64,{image_b64}"}},
            {"type": "text", "text": user_message or "请描述这张图片"},
        ]})
    elif user_message:
        # 空 user_message（纯 system_note 驱动，如心跳）→ 不追加空 user 消息
        messages.append({"role": "user", "content": user_message})

    return messages
