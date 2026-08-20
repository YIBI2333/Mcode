"""定时任务调度器

存储: data/scheduled_tasks.json
触发: 后台线程检查 → 标记 pending → 前端轮询 → 作为隐藏消息发给 AI

MCP 工具 (由 mcp_server.py 注册):
  - add_scheduled_task:   创建定时任务
  - list_scheduled_tasks: 列出所有任务
  - delete_scheduled_task:删除任务
"""

import os
import sys
import json
import uuid
import time
import threading
import logging
from datetime import datetime
from typing import Optional

_log = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_TASKS_FILE = os.path.join(_PROJECT_ROOT, "data", "task", "scheduled_tasks.json")

# ── 任务数据结构 ──
# {
#   "id": "uuid",
#   "name": "提醒喝水",
#   "schedule": {"type": "interval", "minutes": 60} | {"type": "cron", "cron": "0 9 * * *"} | {"type": "once", "at": "2026-08-02 15:30"},
#   "prompt": "提醒主人喝水并休息一下眼睛",
#   "enabled": true,
#   "last_run": "2026-08-02 14:30:00" | null,
#   "next_run": "2026-08-02 15:30:00",
#   "created_at": "2026-08-02 12:00:00"
# }

# ── 待触发队列 ──
_pending_tasks: list[dict] = []
_lock = threading.Lock()
_running = False


def _load() -> list[dict]:
    try:
        if os.path.exists(_TASKS_FILE):
            with open(_TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        _log.warning("读取定时任务文件失败", exc_info=True)
    return []


def _save(tasks: list[dict]):
    try:
        os.makedirs(os.path.dirname(_TASKS_FILE), exist_ok=True)
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception:
        _log.warning("保存定时任务文件失败", exc_info=True)


def _compute_next_run(task: dict, from_time: datetime | None = None) -> str | None:
    """计算下次执行时间，返回 ISO 格式字符串，或 None（一次性任务已过期）"""
    schedule = task.get("schedule", {})
    stype = schedule.get("type", "once")
    now = from_time or datetime.now()

    if stype == "once":
        at_str = schedule.get("at", "")
        try:
            at_dt = datetime.strptime(at_str, "%Y-%m-%d %H:%M")
            return at_dt.strftime("%Y-%m-%d %H:%M:%S") if at_dt > now else None
        except ValueError:
            return None

    if stype == "interval":
        minutes = int(schedule.get("minutes", 60))
        last = task.get("last_run")
        if last:
            try:
                last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                next_dt = datetime.fromtimestamp(last_dt.timestamp() + minutes * 60)
                if next_dt <= now:
                    next_dt = datetime.fromtimestamp(now.timestamp() + minutes * 60)
                return next_dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        # 首次：从现在开始计算
        next_dt = datetime.fromtimestamp(now.timestamp() + minutes * 60)
        return next_dt.strftime("%Y-%m-%d %H:%M:%S")

    if stype == "cron":
        # 简化 cron 支持: "minute hour day month weekday"
        # 这里用简单实现：每分钟检查 cron 是否匹配
        minutes = 1  # cron 任务每分钟检查一次
        return now.strftime("%Y-%m-%d %H:%M:%S")

    return None


def _cron_match(cron_expr: str, dt: datetime) -> bool:
    """检查 cron 表达式是否匹配当前时间（简化版，仅支持数字和 *）"""
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        minute, hour, day, month, weekday = parts

        def _match(expr: str, value: int) -> bool:
            if expr == "*":
                return True
            for item in expr.split(","):
                if "-" in item:
                    lo, hi = item.split("-")
                    if int(lo) <= value <= int(hi):
                        return True
                elif item == str(value):
                    return True
            return False

        return (
            _match(minute, dt.minute)
            and _match(hour, dt.hour)
            and _match(day, dt.day)
            and _match(month, dt.month)
            and _match(weekday, dt.weekday())
        )
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# 公开 API（MCP 工具调用）
# ═══════════════════════════════════════════════════════════════

def add_task(name: str, schedule: dict, prompt: str) -> dict:
    """添加定时任务。schedule: {type: "once"|"interval"|"cron", ...}"""
    tasks = _load()
    task = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "schedule": schedule,
        "prompt": prompt,
        "enabled": True,
        "last_run": None,
        "next_run": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    task["next_run"] = _compute_next_run(task)
    tasks.append(task)
    _save(tasks)
    return task


def list_tasks() -> list[dict]:
    return _load()


def delete_task(task_id: str) -> bool:
    tasks = _load()
    filtered = [t for t in tasks if t["id"] != task_id]
    if len(filtered) < len(tasks):
        _save(filtered)
        return True
    return False


def update_task(task_id: str, updates: dict) -> dict | None:
    """更新任务字段。返回更新后的任务，或 None（任务不存在）。"""
    tasks = _load()
    for t in tasks:
        if t["id"] == task_id:
            for key in ("name", "schedule", "prompt", "enabled"):
                if key in updates:
                    t[key] = updates[key]
            # schedule 变更时重新计算 next_run
            if "schedule" in updates:
                t["next_run"] = _compute_next_run(t)
            _save(tasks)
            return t
    return None


def check_and_pop_task() -> dict | None:
    """获取下一个待触发的任务（供前端轮询）"""
    with _lock:
        if _pending_tasks:
            return _pending_tasks.pop(0)
    return None


def _norm_time(t: str) -> str:
    """标准化时间格式 → HH:MM"""
    t = (t or "").strip()
    try:
        parts = t.split(":")
        h, m = int(parts[0]), int(parts[1])
        return f"{h:02d}:{m:02d}"
    except (ValueError, IndexError):
        return "00:00"

def _norm_datetime(dt_str: str) -> str:
    """标准化日期时间格式 → YYYY-MM-DD HH:MM"""
    dt_str = (dt_str or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(dt_str, fmt)
            return d.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    # 回退：直接取前16字符
    return dt_str[:16] if len(dt_str) >= 16 else dt_str

# ═══════════════════════════════════════════════════════════════
# 后台调度线程
# ═══════════════════════════════════════════════════════════════

def _scheduler_loop():
    """后台线程：每 30 秒检查一次到期任务"""
    global _running
    _running = True
    time.sleep(5)  # 启动后等 5 秒

    print('[scheduler] 调度器线程已启动', file=sys.stderr, flush=True)
    while _running:
        try:
            now = datetime.now()
            tasks = _load()
            if tasks:
                print(f'[scheduler] 检查 {len(tasks)} 个任务 at {now.strftime("%H:%M:%S")}', file=sys.stderr, flush=True)
            modified = False

            for task in tasks:
                if not task.get("enabled"):
                    continue

                schedule = task.get("schedule", {})
                stype = schedule.get("type", "once")

                # 检查是否到期（精确到分钟，忽略秒）
                now_minute = now.strftime("%Y-%m-%d %H:%M")
                now_time = now.strftime("%H:%M")
                now_date = now_minute[:10]
                last_run = task.get("last_run") or ""
                last_minute = last_run[:16] if last_run else ""
                last_date = last_minute[:10] if last_minute else ""
                should_run = False

                if stype == "once":
                    at_str = schedule.get("at", "")
                    # 标准化: "YYYY-MM-DD HH:MM"
                    at_norm = _norm_datetime(at_str)
                    should_run = (at_norm == now_minute) and not last_run

                elif stype == "daily":
                    at_time = schedule.get("at", "09:00")
                    # 标准化: "HH:MM"
                    at_norm = _norm_time(at_time)
                    should_run = (at_norm == now_time) and (last_date != now_date)

                elif stype == "interval":
                    minutes = int(schedule.get("minutes", 60))
                    if last_run:
                        try:
                            last_dt = datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S")
                            should_run = (now - last_dt).total_seconds() >= minutes * 60
                        except ValueError:
                            should_run = False  # 时间格式异常时不触发，避免误触发
                    else:
                        created = task.get("created_at", "")
                        try:
                            created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                            should_run = (now - created_dt).total_seconds() >= minutes * 60
                        except ValueError:
                            should_run = False

                elif stype == "cron":
                    should_run = _cron_match(schedule.get("cron", ""), now) and (last_minute != now_minute)

                if should_run:
                    print(f'[scheduler] 触发: {task["name"]} type={stype}', file=sys.stderr, flush=True)
                    task["last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")
                    task["next_run"] = _compute_next_run(task, now)
                    modified = True

                    # 放入待触发队列
                    msg = f"[定时任务: {task['name']}]\n{task['prompt']}"
                    with _lock:
                        _pending_tasks.append({
                            "task_id": task["id"],
                            "task_name": task["name"],
                            "message": msg,
                        })

                    # 一次性任务：执行后禁用
                    if stype == "once":
                        task["enabled"] = False

                    print(f"[scheduler] 触发任务: {task['name']}", file=sys.stderr, flush=True)

            if modified:
                # 清理已完成的一次性任务，重新计算下次执行时间
                for task in tasks:
                    if task.get("schedule", {}).get("type") == "once" and not task.get("enabled"):
                        continue
                    task["next_run"] = _compute_next_run(task, now)
                _save(tasks)

        except Exception:
            _log.warning("调度器异常", exc_info=True)

        time.sleep(30)


def _reset_interval_baseline():
    """启动时重置 interval 任务的计时基准，避免重启后立即补触发。

    循环任务（interval）在应用重启后，应从"重启时刻"重新计时，
    而不是把关闭期间"欠下"的触发一次性补发。
    """
    try:
        tasks = _load()
        modified = False
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        for task in tasks:
            if task.get("schedule", {}).get("type") == "interval":
                task["last_run"] = now_str
                task["next_run"] = _compute_next_run(task, now)
                modified = True
        if modified:
            _save(tasks)
            print("[scheduler] 已重置循环任务计时基准（重启后重新计时）", file=sys.stderr, flush=True)
    except Exception:
        _log.warning("重置 interval 任务基准失败", exc_info=True)


def start_scheduler():
    """启动后台调度线程"""
    _reset_interval_baseline()
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()
    print("[scheduler] 定时任务调度器已启动", file=sys.stderr, flush=True)
