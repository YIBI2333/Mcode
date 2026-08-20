/**
 * Pet 窗口预加载 — Live2D 桌面
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agentShell', {
  // 窗口控制
  close: () => ipcRenderer.send('pet-close'),
  hide: () => ipcRenderer.send('pet-hide'),
  show: () => ipcRenderer.send('pet-show'),
  resize: (w, h, anchorX, anchorY) => ipcRenderer.send('pet-resize', w, h, anchorX, anchorY),
  setSize: (w, h) => ipcRenderer.send('pet-set-size', w, h),
  setPosition: (x, y) => ipcRenderer.send('pet-set-position', x, y),
  moveBy: (dx, dy) => ipcRenderer.send('pet-move-by', dx, dy),

  // 锁定
  toggleLock: () => ipcRenderer.send('pet-toggle-lock'),
  onLockChanged: (cb) => {
    ipcRenderer.on('pet-lock-changed', (_e, locked) => cb(locked));
  },

  // 始终置顶
  toggleAlwaysOnTop: () => ipcRenderer.send('pet-toggle-ontop'),
  getAlwaysOnTop: () => ipcRenderer.invoke('pet-get-ontop'),
  onAlwaysOnTopChanged: (cb) => {
    ipcRenderer.on('pet-ontop-changed', (_e, on) => cb(on));
  },

  // 鼠标穿透
  toggleMouseThrough: () => ipcRenderer.send('pet-toggle-mouse-through'),
  getMouseThrough: () => ipcRenderer.invoke('pet-get-mouse-through'),
  onMouseThroughChanged: (cb) => {
    ipcRenderer.on('pet-mouse-through-changed', (_e, on) => cb(on));
  },

  // 聊天气泡
  toggleShowBubble: () => ipcRenderer.send('pet-toggle-bubble'),
  getShowBubble: () => ipcRenderer.invoke('pet-get-bubble'),
  onShowBubbleChanged: (cb) => {
    ipcRenderer.on('pet-bubble-changed', (_e, on) => cb(on));
  },

  // 重置位置
  resetPosition: () => ipcRenderer.send('pet-reset-position'),

  // 模型缩放持久化
  getModelScale: () => ipcRenderer.invoke('pet-get-model-scale'),
  saveModelScale: (scale) => ipcRenderer.send('pet-save-model-scale', scale),
  onModelScaleReset: (cb) => {
    ipcRenderer.on('pet-model-scale', (_e, scale) => cb(scale));
  },

  // 获取窗口尺寸
  requestSize: () => ipcRenderer.send('pet-get-size'),
  onSizeUpdate: (cb) => {
    ipcRenderer.on('pet-size-update', (_e, w, h) => cb(w, h));
  },

  // 操作按钮 → 打开/关闭子窗口
  openChat: (focus) => ipcRenderer.send('chat-open', focus !== false),
  hideChat: () => ipcRenderer.send('chat-hide'),
  toggleChat: () => ipcRenderer.send('btn-chat-toggle'),
  openSettings: () => ipcRenderer.send('settings-open'),
  closeSettings: () => ipcRenderer.send('settings-close'),

  // 接收 Chat 窗口发来的消息 (对话气泡 + 表情 + TTS)
  onChatMessage: (cb) => {
    ipcRenderer.on('pet-from-chat', (_e, msg) => cb(msg));
  },
  onChatVisible: (cb) => {
    ipcRenderer.on('chat-window-visible', (_e, visible) => cb(visible));
  },
  onSettingsVisible: (cb) => {
    ipcRenderer.on('settings-window-visible', (_e, visible) => cb(visible));
  },
  onExpressionVisible: (cb) => {
    ipcRenderer.on('expression-window-visible', (_e, visible) => cb(visible));
  },

  // custom expression toggle (Live2D MO custom appearance)
  toggleCustomExpression: () => ipcRenderer.send('pet-toggle-custom-expression'),
  getCustomExpression: () => ipcRenderer.invoke('pet-get-custom-expression'),
  onCustomExpressionChanged: (cb) => {
    ipcRenderer.on('pet-custom-expression-changed', (_e, on) => cb(on));
  },

  // expression panel: open/close
  openExpressionPanel: () => ipcRenderer.send('expr-open'),
  closeExpressionPanel: () => ipcRenderer.send('expr-close'),
  openTaskPanel: () => ipcRenderer.send('task-panel-open'),
  closeTaskPanel: () => ipcRenderer.send('task-panel-close'),

  // debug mode
  toggleDebug: () => ipcRenderer.send('pet-toggle-debug'),
  getDebug: () => ipcRenderer.invoke('pet-get-debug'),

  // custom params persist (save/load all slider values)
  saveCustomParams: (params) => ipcRenderer.send('pet-save-custom-params', params),
  getCustomParams: () => ipcRenderer.invoke('pet-get-custom-params'),

  // send parameter to pet window
  setPetParam: (paramId, value) => ipcRenderer.send('pet-set-param', paramId, value),
  setPetMultiParams: (params) => ipcRenderer.send('pet-set-multi-params', params),
  getPetAllParams: () => ipcRenderer.invoke('pet-get-all-params'),
  onPetAllParamsResponse: (cb) => {
    ipcRenderer.on('pet-all-params-response', (_e, data) => cb(data));
  },

  // pet window: receive param changes
  onSetParam: (cb) => {
    ipcRenderer.on('pet-set-param', (_e, paramId, value) => cb(paramId, value));
  },
  onSetMultiParams: (cb) => {
    ipcRenderer.on('pet-set-multi-params', (_e, params) => cb(params));
  },
  // pet window: respond with all params
  sendAllParamsResponse: (data) => ipcRenderer.send('pet-all-params-response-forward', data),
  // minimize / maximize
  minimizeWindow: () => ipcRenderer.send('win-minimize'),
  toggleMaximize: () => ipcRenderer.send('win-toggle-maximize'),

  // test panel
  openTestPanel: () => ipcRenderer.send('test-panel-open'),

  onRequestAllParams: (cb) => {
    ipcRenderer.on('pet-request-all-params', () => cb());
  },

  // 自动眨眼
  toggleAutoBlink: () => ipcRenderer.send('pet-toggle-auto-blink'),
  getAutoBlink: () => ipcRenderer.invoke('pet-get-auto-blink'),
  onAutoBlinkChanged: (cb) => {
    ipcRenderer.on('pet-auto-blink-changed', (_e, on) => cb(on));
  },

});
