"""API 凭据配置 - 修改此文件切换 Key / 模型"""

# DeepSeek API 地址
DEEPSEEK_BASE_URL = "https://api.deepseek.com"



# 模型key和模型名称
#deepseek
DEEPSEEK_LLM_KEY = "填入你的模型api"
DEEPSEEK_LLM_MODEL = "deepseek-v4-flash"





# 记忆使用的模型key和模型名称
#由于记忆存储消耗token较大，可使用一些较小的模型
MEMORY_LLM_KEY = DEEPSEEK_LLM_KEY
MEMORY_LLM_MODEL = DEEPSEEK_LLM_MODEL

MEMORY_LLM_BASE_URL = DEEPSEEK_BASE_URL


# 对话和工具调用使用的模型key和模型名称
CHAT_LLM_KEY = DEEPSEEK_LLM_KEY
CHAT_LLM_MODEL = DEEPSEEK_LLM_MODEL

CHAT_LLM_BASE_URL = DEEPSEEK_BASE_URL