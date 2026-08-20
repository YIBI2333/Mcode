"""Memory engine wrapper — Mem0 + ChromaDB."""
import sys
import threading
import logging
from datetime import datetime
from mem0 import Memory
from config.settings import MEM0_CONFIG

import warnings
import logging as _logging_core

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
_logging_core.getLogger("mem0").setLevel(_logging_core.ERROR)

_log = logging.getLogger(__name__)

_memory = None


def get_memory():
    """Get memory engine (singleton)."""
    global _memory
    if _memory is None:
        print("Initializing mem0 config...")
        _memory = Memory.from_config(MEM0_CONFIG)
        print("mem0 config initialized.\n")
    return _memory


def retrieve_context(user_id: str, query: str, limit: int = 3) -> str:
    """Search long-term memory with timestamps."""
    query = (query or "").strip()
    if not query:
        return "No relevant memories."  # 空查询直接返回，避免 mem0 报错
    memory = get_memory()
    # mem0 的 search 用 top_k 控制返回条数（不是 limit）
    results = memory.search(query, filters={"user_id": user_id}, top_k=limit)
    items = results.get("results", [])
    if not items:
        return "No relevant memories."
    lines = []
    for m in items:
        created = m.get("created_at", "")[:10]
        date_str = f"({created})" if created else ""
        lines.append(f"- {date_str} {m['memory']}")
    return "\n".join(lines)


def store_memory_async(user_id: str, user_message: str, full_reply: str):
    """Store memory in background thread with date metadata."""

    def _store():
        try:
            memory = get_memory()
            today = datetime.now().strftime("%Y-%m-%d")
            memory.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": full_reply},
                ],
                user_id=user_id,
                metadata={"date": today, "weekday": datetime.now().strftime("%A")},
            )
        except Exception as e:
            msg = str(e)
            if "402" in msg:
                print("[memory] 余额不足，记忆未存储", file=sys.stderr, flush=True)
            else:
                _log.warning("memory store failed: %s", msg[:200])

    threading.Thread(target=_store, daemon=True).start()
