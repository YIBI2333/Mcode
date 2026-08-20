## 一、记忆模块

### 1. mem0 长期记忆
`src/core/memory.py` — mem0 + ChromaDB 向量记忆，`retrieve_context()` 检索 / `store_memory_async()` 存储

### 2. 短期记忆 + 对话压缩
`src/infra/messages.py`
- `short_term_memory` — 最近 N 轮对话（JSON 持久化到 `data/chat/`）
- `build_messages()` — 组装 system prompt + 历史 + 用户输入
- `_maybe_compress()` — 超长时后台压缩为一句摘要
- `_build_timeline_context()` — 时间轴注入（仅首轮）
- 人设文件缓存：`_get_persona_block()`

### 3. 人设文件
`data/persona/`
| 文件 | 用途 |
|------|------|
| `persona.md` | 基本人设（名字、性格、称呼） |
| `state.md` | 实时状态（心情、好感度） |
| `events.md` | 重要事件记录 |
| `todos.md` | 待办与偏好 |
| `timeline.md` | 时间轴（压缩时自动记录） |


## 二、对话模块

### 1. 消息构建
`src/infra/messages.py` — `build_messages()` 按顺序拼装：
```
[时间块] → [系统提示] → [时间轴] → [记忆] → [人设] → [摘要] → [历史] → [用户输入]
```

### 2. 对话流
| 文件 | 职责 |
|------|------|
| `src/core/chat.py` | 对话入口：首轮非流式检测 tool_calls → 分流（工具/纯聊天） |
| `src/core/agent.py` | Agent 工作流：Plan → Execute → Reflect |
| `src/core/pipeline.py` | 流式拆句 + `SentenceEvent`/`ToolEvent`/`StatusEvent` 事件模型 |
| `src/ui/routes/chat.py` | `POST /chat` SSE 端点，线程桥接 + TTS 调度 |
| `src/infra/client.py` | LLM API 客户端（OpenAI 兼容，3 次重试） |

### 3. TTS 语音合成
| 文件 | 职责 |
|------|------|
| `src/tts/engine.py` | `TTSEngine` 抽象基类 |
| `src/tts/edge_tts.py` | Edge TTS（免费微软语音，默认） |
| `src/tts/openai_tts.py` | OpenAI 兼容 TTS（`/v1/audio/speech`） |
| `src/ui/routes/shared.py` | `do_tts_sentence()` — 引擎选择 + 并发控制 + 音频缓存 |

### 4. 定时任务
| 文件 | 职责 |
|------|------|
| `src/core/scheduler.py` | 后台调度线程：once/daily/interval/cron 触发 |
| `live2d/model/task_panel.html` | 任务管理面板 UI |


## 三、MCP 工具模块

### 1. MCP 客户端
`src/infra/mcp_client.py` + `src/tools/mcp_tools.py` — 连接工具服务器，把 LLM tool_call 转为 MCP 调用

### 2. MCP 服务端
`src/infra/mcp_server.py` — 注册 28 个工具（文件操作、搜索、截图、Live2D、人设、剪贴板等）


## 四、前端

桌面显示：Electron 透明悬浮窗

### 1. 按钮区域
`live2d/model/action_bar.html` + `electron/main.js` — 右下角浮动按钮栏（Chat/Expr/Settings/Tasks/Lock/Exit）

### 2. Live2D 区域
| 文件 | 职责 |
|------|------|
| `live2d_mcp/index.html` | Live2D 渲染 + 调整边框 + 对话气泡 |
| `electron/main.js` | 透明窗口创建、拖拽缩放、锁定/穿透 |
| `src/infra/live2d_action.py` | Live2D 动作映射（情绪 → motion/expression） |
| `src/core/emotion.py` | 情绪检测（每句话分析情绪+强度） |
| `src/utils/live2d_constants.py` | 口型参数映射（韵母 → Cubism 参数） |

### 3. 对话窗口
`live2d/model/chat_panel.html` — 聊天面板：消息列表 + 打字动画 + PlayQueue 顺序播放

### 4. 设置面板
`live2d/model/settings.html` — 统一设置入口

#### 4.1 显示设置
置顶、鼠标穿透、气泡开关、TTS 开关、位置重置

#### 4.2 对话设置
历史长度、记忆条数、最大步数、工具调用模式、启动打招呼、心跳间隔

#### 4.3 服务商 + 模型设置
`config/model_config.json` + `config/api_config.py` — 提供商管理（API Key/Base URL）+ 角色模型分配（chat/memory/tool/vision/tts）

#### 4.4 调试设置
DevTools 开关


## 五、启动入口

| 文件 | 职责 |
|------|------|
| `main.py` | 总入口：启动 Web 服务 → MCP → Live2D 渲染 → Electron |
| `src/ui/web.py` | FastAPI 应用工厂 + 路由注册 |
| `src/ui/agent_launcher.py` | 启动 Electron + Live2D MCP 子进程 |
| `config/settings.py` | 全局配置加载 + `reload_from_dict()` 热重载 |
| `config/app_config.json` | 运行时配置持久化 |
