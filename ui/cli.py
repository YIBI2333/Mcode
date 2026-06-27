"""命令行交互界面"""
import time
import sys
import api_config
from core.chat import generate_reply

def run_cli():
    print("=" * 50)
    print("启动成功 (CLI)")
    print(f"记忆提取模型：{api_config.MEMORY_LLM_MODEL}")
    print(f"对话生成模型：{api_config.CHAT_LLM_MODEL}")
    print("=" * 50 + "\n")

    while True:
        user_input = input("你: ").strip()
        if user_input.lower() in ("退出", "exit"):
            print("再见！")
            break
        if not user_input:
            continue
        print("助手: ", end="", flush=True)
        for chunk in generate_reply(user_id="user_001", user_message=user_input):
            print(chunk, end="", flush=True)
            time.sleep(0.05)
        print("\n")