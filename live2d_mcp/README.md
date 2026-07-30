# Live2D MCP — Prometheus Avatar 集成

独立的 Live2D 控制模块，使用 **Prometheus Avatar SDK** 驱动模型，通过 **MCP (Model Context Protocol)** 暴露工具给 LLM。

## 架构

```
LLM (Claude / Cursor)
  │ MCP stdio
  ▼
┌──────────────────────────┐
│  MCP Server (server.js)   │   ← Node.js 进程
│  ◄ stdin/stdout MCP       │
│  ► WebSocket :8850        │
└──────────┬───────────────┘
           │ WebSocket
  ┌────────▼──────────┐
  │  Renderer (Vite)  │      ← Electron BrowserWindow
  │  @prometheusavatar │
  │  PIXI.js + Live2D  │
  └───────────────────┘
```

## 快速开始

### 1. 安装依赖

```bash
cd live2d_mcp
npm install
```

### 2. 开发模式（MCP Server + Vite 同时启动）

```bash
npm run dev
```

启动后：
- **Vite 渲染器**: http://localhost:5188 （Electron 加载此地址）
- **MCP Server**: stdio transport（无需 HTTP，LLM 通过进程通信）
- **WebSocket**: ws://localhost:8850

终端会显示带颜色前缀的日志。

### 3. 生产模式

```bash
npm run build    # Vite 打包到 dist/
npm start        # MCP server 启动，同时服务 dist/ 静态文件
```

访问 http://localhost:8850 即为生产版本。

## MCP 工具列表

| 工具 | 说明 | 参数 |
|------|------|------|
| `avatar_speak` | 让角色说话（自动情感+口型同步） | `text: string` |
| `avatar_emotion` | 设置表情 | `emotion: string, intensity: number(0~1)` |
| `avatar_model_info` | 获取模型信息 | — |
| `avatar_status` | 获取连接状态 | — |

### 支持的表情

`neutral` | `happy` | `sad` | `angry` | `surprised` | `thinking`

## 配置 LLM 连接 MCP

将此项目配置到 MCP 客户端（Claude Desktop、Cursor 等）：

```json
{
  "mcpServers": {
    "live2d-avatar": {
      "command": "node",
      "args": ["server.js"],
      "cwd": "/path/to/Mcode/live2d_mcp"
    }
  }
}
```

## Electron 集成

开发模式：Electron 加载 `http://localhost:5188`（Vite dev server）

生产模式：Electron 加载 `http://localhost:8850`（MCP server 静态服务）

可在主项目的 `electron/main.js` 中设置 `MCP_MODE` 环境变量切换。

## 模型

默认加载主项目的 MO 模型（樱井檬）：
- 路径: `../live2d/model/resources/MO/MO.model3.json`
- 通过 Vite proxy 代理到主项目的 FastAPI `/live2d-resources` 路由

## 与主项目的关系

- **独立性**: 此文件夹可独立运行，不依赖主项目的 Python 后端
- **模型共享**: 引用主项目的 `live2d/model/resources/` 模型文件
- **Electron 共存**: 渲染器运行在 Electron BrowserWindow 中，MCP server 作为子进程
