"""对话压缩 + 时间轴 + 启动初始化"""
import os, sys, threading
from datetime import datetime, timedelta

from config.settings import SHORT_TERM_HISTORY_LENGTH
from src.utils.paths import TIMELINE_FILE

# ═══════════════════ 状态 ═══════════════════
_SUMMARY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "chat", "session_summary.json")
_GREET_FLAG   = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "chat", "greet_pending")

_summary_history = []
_compression_done = False
_compress_lock = threading.Lock()
_startup_ran = False


# ═══════════════════ 时间轴 ═══════════════════
def _time_rel(ts: datetime, now: datetime) -> str:
    minutes = (now - ts).total_seconds() / 60
    if minutes < 1:       return "刚刚"
    if minutes < 60:      return f"{int(minutes)}分钟前"
    if minutes < 120:     return "1小时前"
    if minutes < 360:     return f"{int(minutes // 60)}小时前"
    if ts.date() == now.date():           return f"今天 {ts.strftime('%H:%M')}"
    if ts.date() == now.date() - timedelta(days=1): return f"昨天 {ts.strftime('%H:%M')}"
    if minutes < 2880:    return f"前天 {ts.strftime('%H:%M')}"
    return ts.strftime("%m月%d日 %H:%M")

def build_timeline_context(now: datetime) -> str:
    if not os.path.exists(TIMELINE_FILE):
        return ""
    try:
        entries = []
        with open(TIMELINE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("- 20"): continue
                try:
                    ts = datetime.strptime(line[2:18], "%Y-%m-%d %H:%M")
                    entries.append((ts, line[19:].lstrip("| ")))
                except (ValueError, IndexError): continue
        if not entries: return ""
        if len(entries) > 50:
            entries = entries[-50:]
            _save_timeline(entries)
        return "[时间轴]\n" + "\n".join(f"• {_time_rel(ts, now)} | {rest}" for ts, rest in entries)
    except Exception: return ""

def _save_timeline(entries):
    try:
        with open(TIMELINE_FILE, "w", encoding="utf-8") as f:
            f.write("# 时间轴\n")
            for ts, rest in entries:
                f.write(f"- {ts.strftime('%Y-%m-%d %H:%M')} | {rest}\n")
    except Exception: pass

def _append_timeline(event_type: str, description: str):
    try:
        line = f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} | {event_type} | {description}\n"
        with open(TIMELINE_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception: pass


# ═══════════════════ 压缩 ═══════════════════
def _call_compress(dialogue_text: str) -> str:
    """把对话压缩成一句简短中文摘要"""
    try:
        from src.agent.llm_client import client
        import config.api_config as api_config
        resp = client.chat.completions.create(
            model=api_config.CHAT_LLM_MODEL,
            messages=[
                {"role": "system", "content": "用一句简短中文（不超过80字）概括以下对话的关键话题和情绪，只输出摘要。"},
                {"role": "user", "content": dialogue_text},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[memory] 压缩失败: {e}", file=sys.stderr, flush=True)
        return ""

def _schedule_compress():
    from src.memory.store import short_term_memory
    meaningful = [e for e in short_term_memory if e.get("role") in ("user", "assistant")]
    if len(meaningful) <= max(SHORT_TERM_HISTORY_LENGTH * 2, 10): return
    if _compress_lock.locked(): return
    threading.Thread(target=_maybe_compress, daemon=True).start()

def _maybe_compress():
    global _compression_done, _summary_history
    from src.memory.store import short_term_memory, _save_memory

    with _compress_lock:
        meaningful = [e for e in short_term_memory if e.get("role") in ("user", "assistant")]
        if len(meaningful) <= 2: _compression_done = True; return
        if _compression_done and len(meaningful) <= max(SHORT_TERM_HISTORY_LENGTH * 2, 10): return
        _compression_done = True

        if len(meaningful) > SHORT_TERM_HISTORY_LENGTH * 2:
            split = len(meaningful) - SHORT_TERM_HISTORY_LENGTH
            to_compress = meaningful[:split]
            keep_ids = {id(e) for e in meaningful[split:]}
            short_term_memory[:] = [e for e in short_term_memory if id(e) in keep_ids]
        else:
            to_compress = meaningful
            short_term_memory.clear()

    lines = [f"{'用户' if e['role']=='user' else '墨白'}: {str(e.get('content',''))[:300]}" for e in to_compress[-20:]]
    compressed = _call_compress("\n".join(lines))
    # 防止 LLM 忽略字数限制返回全文 —— 强制截断
    if len(compressed) > 200:
        compressed = compressed[:200] + "..."
    print(f"[memory] 压缩完成: {compressed}", file=sys.stderr, flush=True)

    _summary_history.insert(0, {"compressed": compressed, "count": len(to_compress)})
    _summary_history = _summary_history[:MAX_SUMMARIES]
    _save_summary(datetime.now().isoformat(), compressed, len(to_compress))
    _append_timeline("对话", compressed)
    # 压缩摘要写回短期记忆，下次对话也能看到
    short_term_memory.append({"role": "assistant", "content": f"[对话摘要] {compressed}"})
    _save_memory()
    print(f"[memory] 压缩 {len(to_compress)}→摘要, 保留{len(short_term_memory)}条", file=sys.stderr, flush=True)

def get_summary_history():
    return _summary_history


# ═══════════════════ 摘要 I/O ═══════════════════
import json as _json

# 压缩摘要保留条数（内存 + 文件统一）
MAX_SUMMARIES = 5


def _load_summaries() -> list:
    """读取压缩摘要列表（兼容旧版单条 dict 格式）。"""
    try:
        if os.path.exists(_SUMMARY_FILE):
            with open(_SUMMARY_FILE, "r", encoding="utf-8") as f:
                d = _json.load(f)
            if isinstance(d, list):
                return d
            if isinstance(d, dict):   # 旧格式：单条 → 转列表
                return [d]
    except Exception:
        pass
    return []


def _load_summary():
    """返回最新一条摘要 dict（无则空 dict）。"""
    items = _load_summaries()
    return items[0] if items else {}


def _save_summary(timestamp: str, compressed: str, count: int):
    """保存压缩摘要（列表，保留最近 MAX_SUMMARIES 条，新的在前）。"""
    try:
        items = _load_summaries()
        items.insert(0, {"timestamp": timestamp, "compressed": compressed, "message_count": count})
        items = items[:MAX_SUMMARIES]
        os.makedirs(os.path.dirname(_SUMMARY_FILE), exist_ok=True)
        with open(_SUMMARY_FILE, "w", encoding="utf-8") as f:
            _json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _init_summary_history():
    """启动时把持久化摘要加载回内存（重启后不丢失）。"""
    global _summary_history
    if not _summary_history:
        _summary_history = _load_summaries()


def get_summary_history():
    """返回内存中的摘要历史（最新在前）。"""
    return _summary_history


def get_recent_summaries(n: int = 3) -> list:
    """按条数返回最近 n 条压缩摘要（内存优先，未初始化时从文件回填）。"""
    if not _summary_history:
        _init_summary_history()
    return _summary_history[:n]


def clear_summaries():
    """清空压缩摘要（内存 + 文件）。"""
    global _summary_history
    _summary_history = []
    try:
        if os.path.exists(_SUMMARY_FILE):
            os.remove(_SUMMARY_FILE)
    except Exception:
        pass


# ═══════════════════ 启动 ═══════════════════
def startup_init():
    global _startup_ran
    if _startup_ran: return
    _startup_ran = True
    # 启动不做强制压缩——摘要已有持久化存储，直接加载到内存；
    # 压缩按短期对话条数超阈值时由 _schedule_compress 自然触发
    _init_summary_history()

    from config.settings import GREET_ON_STARTUP
    if GREET_ON_STARTUP:
        try:
            os.makedirs(os.path.dirname(_GREET_FLAG), exist_ok=True)
            with open(_GREET_FLAG, "w") as f: f.write("1")
            print("[startup] 已标记待打招呼", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[startup] 标记失败: {e}", file=sys.stderr, flush=True)

def check_and_clear_greet_flag() -> bool:
    if os.path.exists(_GREET_FLAG):
        try: os.remove(_GREET_FLAG)
        except Exception: pass
        return True
    return False
