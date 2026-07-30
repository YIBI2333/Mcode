/**
 * Chat 窗口预加载 — 独立对话框
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('chatShell', {
  close: () => ipcRenderer.send('chat-hide'),
  minimizeWindow: () => ipcRenderer.send('win-minimize'),
  toggleMaximize: () => ipcRenderer.send('win-toggle-maximize'),
  toggleFold: () => ipcRenderer.send('chat-toggle-fold'),

  // 发送消息给 Pet 窗口（对话气泡 / 表情 / TTS 音素）
  sendToPet: (msg) => ipcRenderer.send('chat-to-pet', msg),

  // 用系统默认程序打开文件
  openPath: (path) => ipcRenderer.send('open-file', path),
});
