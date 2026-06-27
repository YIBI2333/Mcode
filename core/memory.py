"""记忆引擎封装"""
from mem0 import Memory
from config import MEM0_CONFIG

_memory = None
def get_memory():
    """获取记忆引擎"""
    global _memory
    if _memory is None:
        print("正在初始化mem0配置...")
        _memory = Memory.from_config(MEM0_CONFIG)
        print("mem0配置初始化完成。\n")
        
    return _memory

def retrieve_context(user_id: str, query: str, limit: int = 3) -> str:
    """检索相关记忆"""
    memory = get_memory()
    results = memory.search(query, filters={"user_id": user_id}, limit=limit)
    memories = [m["memory"] for m in results.get("results", [])]
    return "\n".join(memories) if memories else "暂无相关记忆。"









import warnings
import logging

# 忽略 DeprecationWarning 和 FutureWarning
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# 降低 Mem0 日志级别，屏蔽 spaCy 警告
logging.getLogger("mem0").setLevel(logging.ERROR)