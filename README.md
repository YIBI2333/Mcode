# Mcode — 一个住在你电脑里的 AI 智能体

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-Protocol-orange.svg)](https://modelcontextprotocol.io)

**Mcode** 是一个轻量级、可扩展的 AI 智能体框架，它拥有长期记忆、能够操作你的电脑文件、执行命令，并通过 MCP（模型上下文协议）无缝集成外部工具。

> 它不是一个用完即走的工具，而是一个“住”在你电脑里的存在。

---

## ✨ 核心特性

| 特性         | 说明                                           |
| ------------ | ---------------------------------------------- |
| **长期记忆** | 基于 Mem0 + ChromaDB，跨会话记住用户偏好和事实 |
| **文件操作** | 读、写、列目录、删除、移动文件（可配置工作区） |
| **命令执行** | 支持白名单内的系统命令（如 `ls`、`python` 等） |
| **流式对话** | 实时逐字输出，响应迅速                         |
| **工具调用** | AI 自主判断并调用工具（Function Calling）      |
| **MCP 标准** | 基于 MCP 协议，可无缝接入任何 MCP 服务器       |
| **低成本**   | 单次对话约 1200 token，约 0.16 分钱            |
| **模块化**   | 各层独立，易于扩展和定制                       |



## 项目结构

```
project/
├── api_config.py      # 存放 DeepSeek API Key 和模型名称
├── config.py          # 系统配置：路径、Mem0 参数、环境变量
├── memory_bot.py      # 主程序，实现带记忆的对话
├── hf_cache/          # 本地嵌入模型（自动下载，约90MB）
├── mem0_db/           # 长期记忆数据库（ChromaDB，自动生成）
└── requirements.txt   # 依赖：openai, mem0ai, chromadb, sentence-transformers
```

## 用途

一个**带长期记忆的聊天助手**，能跨会话记住用户偏好、事实和互动历史，并基于记忆生成个性化回复。

## 实现方式

### 1. 配置分离
- **`api_config.py`**：只存 API Key 和模型名（`MEMORY_LLM_MODEL` / `CHAT_LLM_MODEL`）。
- **`config.py`**：设置本地路径、环境变量，构建 Mem0 的配置字典，引用 `api_config` 中的凭据。

### 2. 记忆机制（Mem0 框架）
- **嵌入模型**：`all-MiniLM-L6-v2`（本地 HuggingFace），将文字转成向量，用于语义搜索。
- **向量存储**：ChromaDB 本地持久化，保存所有记忆事实。
- **记忆提取**：每次对话后，Mem0 调用**轻量 LLM**（如 DeepSeek Chat）从对话中提取关键信息，向量化后存入 ChromaDB。
- **记忆检索**：每次新提问，用当前问题向量在库中搜索最相关的历史记忆，注入系统提示。

### 3. 双模型设计
- **记忆提取 LLM**：使用较轻量的模型（省钱），通过 `MEMORY_LLM_KEY` 配置。
- **对话生成 LLM**：使用最强模型（保证回复质量），通过 `CHAT_LLM_KEY` 配置。
- 两者可共用同一个 Key 但指定不同模型名，也可分开使用不同账号。

### 4. 主程序逻辑 (`memory_bot.py`)
1. **检索记忆**：用用户输入搜索长期记忆，获得上下文。
2. **构建提示**：系统提示（含记忆）+ 短期对话历史 + 当前问题。
3. **生成回复**：调用对话 LLM 得到回复。
4. **更新记忆**：将本轮对话存入长期记忆，Mem0 自动提取事实。











### 📁 项目目录结构与文件用途

| 文件/目录            | 用途                                                         |
| :------------------- | :----------------------------------------------------------- |
| **`core/`**          | 核心逻辑模块                                                 |
| `core/chat.py`       | 对话生成主逻辑：`generate_reply`（流式回复 + 工具调用）、`build_messages`（构建消息）、`update_history`、`store_memory_async`（异步存储记忆） |
| `core/memory.py`     | 记忆引擎封装：`get_memory`（单例 Mem0 实例）、`retrieve_context`（检索长期记忆） |
| **`hf_cache/`**      | HuggingFace 嵌入模型缓存（`all-MiniLM-L6-v2`），本地离线使用，约 90MB |
| **`mem0_db/`**       | ChromaDB 向量数据库，存储长期记忆事实（自动生成）            |
| **`tools/`**         | 工具相关模块                                                 |
| `tools/mcp_tools.py` | MCP 工具适配器：`get_tools_schema`（将 MCP 工具转为 OpenAI function calling 格式）、`execute_mcp_tool`（执行工具调用） |
| **`ui/`**            | 用户界面                                                     |
| `ui/cli.py`          | 命令行交互界面：`run_cli`（主循环，接收输入并调用 `generate_reply` 流式输出） |
| **`Workspace/`**     | MCP 服务器工作目录，AI 只能操作此目录下的文件（`test.txt`、`text.txt`） |
| **`api_config.py`**  | API 凭据与模型配置：`DEEPSEEK_BASE_URL`、`ZHIPU_BASE_URL`、`MEMORY_LLM_*`、`CHAT_LLM_*`（Key、Model、Base URL） |
| **`config.py`**      | 项目统一配置：Mem0 配置、对话参数（`SHORT_TERM_HISTORY_LENGTH`、`MEMORY_SEARCH_LIMIT`）、MCP 工作目录（`MCP_WORKSPACE_DIR`）、工具开关（`ENABLE_TOOLS`、`TOOL_CHOICE`） |
| **`main.py`**        | 程序入口：初始化记忆（预热）、有条件初始化 MCP、启动 CLI     |
| **`mcp_client.py`**  | MCP 客户端封装：`MCPClient` 类，通过 stdio 连接 MCP 服务器，提供 `connect`、`list_tools`、`call_tool` 等同步方法 |
| **`mcp_server.py`**  | MCP 服务器实现：基于 `mcp` 库，提供 `list_directory` 和 `read_text_file` 两个工具，路径限制在 `Workspace` 目录内 |

------

### 🔗 关键依赖关系

1. `main.py` → 初始化 `core/memory`（预热）和 `tools/mcp_tools`（如果启用工具）。
2. `ui/cli.py` → 调用 `core/chat.generate_reply`。
3. `core/chat.py` → 使用 `core/memory.retrieve_context` 检索记忆，使用 `tools/mcp_tools.get_tools_schema` 和 `execute_mcp_tool` 调用工具。
4. `tools/mcp_tools.py` → 依赖 `mcp_client.py` 连接 MCP 服务器。
5. `mcp_client.py` → 启动 `mcp_server.py` 子进程（通过 `python mcp_server.py`）。
6. `config.py` 被多个模块引用，提供全局配置。





---

## 🚀 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥

复制 `api_config.example.py` 为 `api_config.py`，填入你的 API Key：

python

```
# 记忆提取模型（可用免费模型，如智谱）
MEMORY_LLM_KEY = "your_key"
MEMORY_LLM_MODEL = "glm-4-flash"
MEMORY_LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"

# 对话生成模型（推荐 DeepSeek）
CHAT_LLM_KEY = "sk-your_deepseek_key"
CHAT_LLM_MODEL = "deepseek-v4-flash"
CHAT_LLM_BASE_URL = "https://api.deepseek.com"
```

### 3. 运行

```
python main.py
```





## 📦 依赖与致谢

本项目用到以下项目：

| 项目                                                         | 用途                                            |
| :----------------------------------------------------------- | :---------------------------------------------- |
| [Mem0](https://github.com/mem0ai/mem0)                       | 长期记忆引擎，管理跨会话的记忆提取与检索        |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MCP 协议官方 Python 实现，提供服务器/客户端基础 |
| [DeepSeek](https://deepseek.com/)                            | 对话生成模型，低成本高性能                      |
| [ChromaDB](https://www.trychroma.com/)                       | 向量数据库，存储记忆嵌入                        |
| [Agnes AI](https://agnes-ai.com/)                            | 免费记忆提取模型支持                            |
| [sentence-transformers](https://github.com/UKPLab/sentence-transformers) | 本地嵌入模型 `all-MiniLM-L6-v2`                 |

------



## 📄 许可证

本项目采用 [MIT License](https://license/) 开源协议。你可以自由使用、修改、分发，包括用于商业目的，只需保留原始版权声明。

------



## 🤝 贡献

欢迎提交 Issue 和 Pull Request！如果你有想法或改进建议，请随时提出。

------



## 📬 联系方式

- 项目地址：https://github.com/YIBI2333/Mcode
- 作者：夜白2333

