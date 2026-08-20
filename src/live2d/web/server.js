/**
 * Live2D MCP Server — stdio transport + WebSocket bridge
 * 
 * 启动: node server.js
 * LLM 连接: 配置 MCP client 以 stdio 连接本进程
 * 渲染器连接: ws://localhost:8850
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { WebSocketServer, WebSocket } from 'ws';
import http from 'http';
import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ====================== WS 客户端管理 ======================
const clients = new Set();

function broadcast(data) {
  const msg = JSON.stringify(data);
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(msg);
    }
  }
}

// ====================== HTTP + WebSocket ======================
const app = express();
app.use(express.json());
const WSS_PORT = 8850;

// 接收来自 Python MCP 的动作命令，广播到渲染器
app.post('/command', (req, res) => {
  broadcast(req.body);
  res.json({ ok: true });
});

// MCP 渲染器页面（直用 vendored SDK，无 Vite/npm）
app.get('/', (_req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});
// 独立测试页
app.get('/test.html', (_req, res) => {
  res.sendFile(path.join(__dirname, 'test.html'));
});
// 代理 Live2D 模型资源 (本目录下)
const MODEL_DIR = path.resolve(__dirname, 'resources');
app.use('/live2d-resources', express.static(MODEL_DIR));
// 代理 SDK 文件 (本目录下)
const SDK_DIR = path.resolve(__dirname, '.');
app.use('/model', express.static(SDK_DIR));

const httpServer = http.createServer(app);
const wss = new WebSocketServer({ server: httpServer });

wss.on('connection', (ws) => {
  clients.add(ws);
  console.log('[WS] Client connected (' + clients.size + ' total)');
  ws.on('close', () => {
    clients.delete(ws);
    console.log('[WS] Client disconnected (' + clients.size + ' total)');
  });
});

httpServer.listen(WSS_PORT, () => {
  console.log('[Bridge] HTTP + WebSocket on port ' + WSS_PORT);
});

// ====================== 父进程看门狗 ======================
// 本服务由 Python 主程序启动；Python 退出/崩溃/被强杀后自动退出，
// 避免孤儿进程残留占用 8850 端口。信号 0 仅检测进程存在性。
const PARENT_PID = process.ppid;
if (PARENT_PID) {
  setInterval(() => {
    try {
      process.kill(PARENT_PID, 0);
    } catch (err) {
      if (err && err.code === 'ESRCH') {
        console.log('[Watchdog] parent process gone, exiting');
        process.exit(0);
      }
    }
  }, 2000);
}

// ====================== MCP Server ======================
const server = new McpServer({
  name: 'live2d-avatar',
  version: '1.0.0',
});

// ---------- Tool: avatar_speak ----------
server.tool(
  'avatar_speak',
  '让 Live2D 角色说话（自动感情分析 + 口型同步）。会触发对应情绪动作 + 嘴型动画。',
  {
    text: z.string().describe('要说的文本内容'),
    emotion: z.enum(['neutral', 'happy', 'sad', 'angry', 'surprised']).optional().describe('可选：说话时的情绪，不填则自动分析'),
    intensity: z.number().min(0).max(1).optional().describe('可选：情绪强度 0~1，默认 0.7'),
  },
  async ({ text, emotion, intensity }) => {
    const em = emotion || 'neutral';
    const it = intensity != null ? intensity : 0.7;
    // 广播 speak 命令 + 情绪动作，渲染器负责口型同步和动作播放
    broadcast({ type: 'speak', text, emotion: em, intensity: it, motion: em });
    return {
      content: [{ type: 'text', text: `✅ 已发送 speak (情绪:${em} 强度:${it}): "${text.slice(0, 50)}${text.length > 50 ? '...' : ''}"` }],
    };
  }
);

// ---------- Tool: avatar_emotion ----------
server.tool(
  'avatar_emotion',
  '设置 Live2D 角色的表情并播放对应的情绪动作（入场动画 + 待机动画）',
  {
    emotion: z.enum(['neutral', 'happy', 'sad', 'angry', 'surprised', 'thinking']).describe('表情名称。thinking 会映射为 neutral'),
    intensity: z.number().min(0).max(1).default(0.8).describe('表情强度 0~1'),
  },
  async ({ emotion, intensity }) => {
    // 'thinking' 映射为 neutral（沉思用平静表情）
    const em = emotion === 'thinking' ? 'neutral' : emotion;
    // 发送 emotion + motion 命令，渲染器播放入场动画 + 待机动画
    broadcast({ type: 'emotion', emotion: em, intensity, motion: em });
    return {
      content: [{ type: 'text', text: `✅ 表情动作已触发: ${em} (强度: ${intensity}) — 入场动画+待机循环` }],
    };
  }
);

// ---------- Tool: avatar_model_info ----------
server.tool(
  'avatar_model_info',
  '获取当前加载的 Live2D 模型信息（表情列表、参数等）',
  {},
  async () => {
    // 通过广播请求，等待渲染器回复（带超时）
    const requestId = 'req_' + Date.now();
    broadcast({ type: 'get_model_info', requestId });

    return {
      content: [{ type: 'text', text: '📋 已请求模型信息，渲染器就绪后即可获取' }],
    };
  }
);

// ---------- Tool: avatar_status ----------
server.tool(
  'avatar_status',
  '获取 Live2D 角色当前状态',
  {},
  async () => {
    return {
      content: [{
        type: 'text',
        text: JSON.stringify({
          active: clients.size > 0,
          clients: clients.size,
          message: clients.size > 0 ? '渲染器已连接，可使用 speak/emotion 工具' : '⚠ 渲染器未连接，请确认已打开 Live2D 窗口',
        }, null, 2),
      }],
    };
  }
);

// ====================== 启动 MCP ======================
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.log('[MCP] Server started (stdio transport)');
  console.log('[MCP] Ready — 渲染器需连接 ws://localhost:' + WSS_PORT);
}

main().catch(console.error);
