"""Agent 会话状态 — 支持多任务规划、暂停/恢复、打断

同时维护"工作流快照"：ReAct 循环被用户打断时，把完整 messages（含工具结果）
+ TaskContext 存下来，下次用户补充消息时可恢复继续，实现真正的"打断续接"。
"""
import uuid, threading, time
from dataclasses import dataclass, field
from threading import Lock

@dataclass
class TaskPlan:
    name: str
    steps: list[str] = field(default_factory=list)
    status: str = "pending"  # pending / running / done

class AgentSession:
    def __init__(self, session_id: str = None):
        self.session_id = session_id or f"agt_{uuid.uuid4().hex[:8]}"
        self.tasks: list[TaskPlan] = []
        self.current_task: int = 0
        self.paused: bool = False
        self.user_response: str = ""
        self._response_event = threading.Event()
        self.messages: list = []   # 会话期间的对话消息

    def add_task(self, name: str, steps: list[str]):
        self.tasks.append(TaskPlan(name=name, steps=steps))

    def current_task_plan(self) -> TaskPlan | None:
        if self.current_task < len(self.tasks):
            return self.tasks[self.current_task]
        return None

    def advance_task(self):
        if self.current_task < len(self.tasks):
            self.tasks[self.current_task].status = "done"
        self.current_task += 1

    def wait_for_user(self, question: str, timeout: float = 180) -> str | None:
        """阻塞等待用户回复。在 ask_user 工具中调用。

        timeout 秒内未收到回复返回 None（超时，由调用方决定如何继续）；
        被 cancel() 解除阻塞时返回空串（用户已取消）。
        """
        self.paused = True
        self._response_event.clear()
        got = self._response_event.wait(timeout)
        self.paused = False
        return self.user_response if got else None

    def respond(self, text: str):
        self.user_response = text
        self._response_event.set()

    def cancel(self):
        self._response_event.set()  # 解除阻塞
        self.tasks.clear()

    @property
    def is_active(self) -> bool:
        return len(self.tasks) > 0 and self.current_task < len(self.tasks)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "tasks": [{"name": t.name, "steps": t.steps, "status": t.status} for t in self.tasks],
            "current_task": self.current_task,
            "paused": self.paused,
        }


# 全局会话存储
_sessions: dict[str, AgentSession] = {}

def get_or_create_session(session_id: str = None) -> AgentSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    s = AgentSession(session_id)
    _sessions[s.session_id] = s
    return s

def get_session(session_id: str) -> AgentSession | None:
    return _sessions.get(session_id)

def remove_session(session_id: str):
    _sessions.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════
# 工作流快照 — 打断后保存 / 继续时恢复
# ═══════════════════════════════════════════════════════════════
_workflow_lock = Lock()
_active_workflow = None  # {"messages": [...], "ctx": TaskContext, "user_id": str, "ts": float}
WORKFLOW_RESUME_TTL = 300  # 快照有效期（秒），超时视为放弃继续


def _msg_to_dict(msg) -> dict:
    """把 OpenAI ChatCompletionMessage 对象转成 dict（保留 tool_calls 和 reasoning_content）。"""
    if isinstance(msg, dict):
        return msg
    d = {"role": getattr(msg, "role", "assistant")}
    content = getattr(msg, "content", None)
    if content:
        d["content"] = content
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        d["reasoning_content"] = reasoning
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        d["tool_calls"] = []
        for tc in tool_calls:
            fn = tc.function
            d["tool_calls"].append({
                "id": getattr(tc, "id", ""),
                "type": getattr(tc, "type", "function"),
                "function": {"name": fn.name, "arguments": fn.arguments},
            })
    return d


def _serialize_messages(messages: list) -> list:
    """转 dict 并补齐缺失的 tool 结果（打断发生在工具执行中途时）。

    保证每个 assistant tool_call 都有对应 tool 结果，避免继续时 API 报错。
    """
    msgs = [_msg_to_dict(m) for m in messages]
    pending = {}
    for m in msgs:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                pending[tc["id"]] = True
        elif m.get("role") == "tool" and m.get("tool_call_id"):
            pending.pop(m.get("tool_call_id"), None)
    for tid in pending:
        msgs.append({"role": "tool", "tool_call_id": tid, "content": "[已中断，未执行]"})
    return msgs


def save_workflow(messages: list, ctx, user_id: str) -> None:
    """打断时保存工作流快照（messages 转成可序列化且配对完整的 dict）。"""
    global _active_workflow
    with _workflow_lock:
        _active_workflow = {
            "messages": _serialize_messages(messages),
            "ctx": ctx,
            "user_id": user_id,
            "ts": time.time(),
        }


def pop_workflow() -> dict | None:
    """取走未过期的工作流快照（继续时）。过期或不存在则返回 None。"""
    global _active_workflow
    with _workflow_lock:
        wf = _active_workflow
        _active_workflow = None
        if wf and (time.time() - wf["ts"]) < WORKFLOW_RESUME_TTL:
            return wf
        return None


def clear_workflow() -> None:
    """主动丢弃快照（如清空历史时）。"""
    global _active_workflow
    with _workflow_lock:
        _active_workflow = None


def has_workflow() -> bool:
    """是否有可继续的工作流快照。"""
    with _workflow_lock:
        return _active_workflow is not None and (time.time() - _active_workflow["ts"]) < WORKFLOW_RESUME_TTL
