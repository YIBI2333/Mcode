/**
 * 墨白 Live2D 智能体 — Electron 主进程
 *
 * 三窗口架构:
 *   Pet 窗口      — 透明悬浮窗 (Live2D 画布 + 对话气泡)，可拖动/可缩放
 *   Button 窗口   — 固定在屏幕右下角的圆形按钮栏 (聊天/设置/锁定)
 *   Chat 窗口     — 独立对话框 (拖拽自由定位)
 *   Settings 窗口 — 设置面板
 */
const { app, BrowserWindow, screen, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');

const SERVER_URL = process.env.PET_SERVER_URL || 'http://localhost:8866';
// MCP 模式: 设置环境变量 LIVE2D_MCP=true 使用 Prometheus Avatar MCP 渲染器
const MCP_MODE = process.env.LIVE2D_MCP === 'true';
const MCP_RENDERER_URL = process.env.MCP_RENDERER_URL || 'http://localhost:8850';

// ====================== 窗口引用 ======================
let petWindow = null;
let btnWindow = null;
let chatWindow = null;
let settingsWindow = null;

let _isQuitting = false;  // 真正退出中：绕过窗口 close 拦截，确保 app.quit() 不被 preventDefault 取消
let _parentDead = false;  // Python 父进程已死（看门狗检测到），退出时跳过 /api/shutdown 通知

let petLocked = false;
let petAlwaysOnTop = true;
let petMouseThrough = false;
let petShowBubble = true;
let petCustomExpression = false;  // Live2D custom expression (MO custom appearance)
let petAutoBlink = true;          // Auto blink enabled by default
let debugMode = false;           // 调试模式：DevTools + 额外日志
let petSize = { w: 400, h: 560 };

// Windows 下 'screen-saver' 层级高于任务栏 (普通 'alwaysOnTop' 会被任务栏覆盖)
const ON_TOP_LEVEL = 'screen-saver';
// 聊天和设置窗口用 floating 层级（在正常窗口之上，任务栏之下），hide/show 可靠
const SUB_TOP_LEVEL = 'floating';

// ====================== 配置持久化 ======================
const DEFAULT_PET_W = 500, DEFAULT_PET_H = 700;
// 0 = 前端自动计算合适的默认缩放
const DEFAULT_MODEL_SCALE = 0;

let _configPath = '';  // 在 app.whenReady 中初始化
let petConfig = {};

function loadConfig() {
  try {
    if (fs.existsSync(_configPath)) {
      const raw = JSON.parse(fs.readFileSync(_configPath, 'utf-8'));
      // 恢复 toggle 状态
      petAlwaysOnTop = raw.alwaysOnTop !== false;  // 默认 true
      petMouseThrough = raw.mouseThrough === true;
      petLocked = raw.locked === true;
      petShowBubble = raw.showBubble !== false;    // 默认 true
      petCustomExpression = raw.customExpression === true;
      petAutoBlink = raw.autoBlink !== false;       // 默认 true
      debugMode = raw.debugMode === true;           // 默认 false
      petConfig = {
        petX: raw.petX ?? null,
        petY: raw.petY ?? null,
        petW: raw.petW ?? DEFAULT_PET_W,
        petH: raw.petH ?? DEFAULT_PET_H,
        modelScale: raw.modelScale ?? DEFAULT_MODEL_SCALE,
        // 关键: 同步 toggle 状态到 petConfig，避免 resize/move 触发 saveConfig 时覆盖丢失
        alwaysOnTop: petAlwaysOnTop,
        mouseThrough: petMouseThrough,
        locked: petLocked,
        showBubble: petShowBubble,
        customExpression: petCustomExpression,
        autoBlink: petAutoBlink,
        debugMode: debugMode,
        customParams: raw.customParams || {},
      };
      return;
    }
  } catch (_) { /* ignore */ }
  petConfig = {
    petX: null, petY: null, petW: DEFAULT_PET_W, petH: DEFAULT_PET_H, modelScale: DEFAULT_MODEL_SCALE,
    alwaysOnTop: true, mouseThrough: false, locked: false, showBubble: true, customExpression: false, autoBlink: true,
    debugMode: false, customParams: {},
  };
}

let _saveTimer = null;
function saveConfig() {
  // 每次保存前将当前 toggle 状态同步到 petConfig，确保不被 resize/move 覆盖
  petConfig.alwaysOnTop = petAlwaysOnTop;
  petConfig.mouseThrough = petMouseThrough;
  petConfig.locked = petLocked;
  petConfig.showBubble = petShowBubble;
  petConfig.customExpression = petCustomExpression;
  petConfig.autoBlink = petAutoBlink;
  petConfig.debugMode = debugMode;
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    try { fs.writeFileSync(_configPath, JSON.stringify(petConfig, null, 2)); } catch (_) {}
  }, 600);
}

// 确保按钮窗口始终在 pet 窗口上方
function keepBtnOnTop() {
  if (btnWindow && !btnWindow.isDestroyed()) {
    btnWindow.moveTop();
  }
}

// ====================== Pet 窗口 (Live2D + 对话气泡) ======================
function createPetWindow() {
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;

  const w = petConfig.petW || DEFAULT_PET_W;
  const h = petConfig.petH || DEFAULT_PET_H;
  const x = petConfig.petX != null ? petConfig.petX : Math.round(sw - w - 80);
  const y = petConfig.petY != null ? petConfig.petY : Math.round(sh - h - 120);
  petSize = { w, h };

  petWindow = new BrowserWindow({
    width: w,
    height: h,
    x, y,

    transparent: true,
    frame: false,
    skipTaskbar: true,
    resizable: true,
    hasShadow: false,
    show: false,
    backgroundColor: '#00000000',

    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  petWindow.setAlwaysOnTop(true, ON_TOP_LEVEL);

  if (process.platform === 'win32') {
    petWindow.setBackgroundColor('#00000000');
  }

  // MCP 模式: 使用 Prometheus Avatar 渲染器; 普通模式: 使用 FastAPI 页面
  if (MCP_MODE) {
    petWindow.loadURL(`${MCP_RENDERER_URL}?_t=${Date.now()}`);
  } else {
    petWindow.loadURL(`${SERVER_URL}/agent?_t=${Date.now()}`);
  }

  petWindow.once('ready-to-show', () => {
    // 同步注入锁定状态：executeJavaScript 在 show() 前完成，防止边框闪现
    if (petLocked) {
      petWindow.webContents.executeJavaScript(
        'if(typeof locked!=="undefined")locked=true;document.body.classList.add("locked")'
      );
      petWindow.setIgnoreMouseEvents(petLocked && petMouseThrough, { forward: true });
    }
    petWindow.show();
    // IPC 作为后备通知（后续锁定/解锁由 IPC 驱动）
    if (petLocked && btnWindow && !btnWindow.isDestroyed()) {
      btnWindow.webContents.send('pet-lock-changed', true);
    }
    setTimeout(function() { petWindow.webContents.send('pet-custom-expression-changed', petCustomExpression); }, 800);
    setTimeout(function() { petWindow.webContents.send('pet-auto-blink-changed', petAutoBlink); }, 900);
    // 调试模式：打开 DevTools
    if (debugMode) {
      setTimeout(() => petWindow.webContents.openDevTools({ mode: 'detach' }), 1500);
    }
  });

  // 大小变化时记录 + 通知 renderer + 保存配置 + 按钮跟随
  petWindow.on('resize', () => {
    const [w, h] = petWindow.getSize();
    petSize = { w, h };
    petConfig.petW = w;
    petConfig.petH = h;
    saveConfig();
    if (petWindow && !petWindow.isDestroyed()) {
      petWindow.webContents.send('pet-size-update', w, h);
    }
  });

  // 位置变化 → 通知 chat 窗口 + 保存配置 + 按钮跟随
  petWindow.on('move', () => {
    if (!petWindow || petWindow.isDestroyed()) return;
    const [x, y] = petWindow.getPosition();
    petConfig.petX = x;
    petConfig.petY = y;
    saveConfig();
    if (petWindow && chatWindow && !chatWindow.isDestroyed()) {
      chatWindow.webContents.send('pet-moved', petWindow.getBounds());
    }
  });

  // 点击 pet 窗口会抢走 z-order → 确保按钮窗口始终在上层
  petWindow.on('focus', () => keepBtnOnTop());

  petWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  return petWindow;
}

// ====================== Button 窗口 (屏幕右下角固定) ======================
function createButtonWindow() {
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  const bw = 54, bh = 350;

  btnWindow = new BrowserWindow({
    width: bw,
    height: bh,
    x: sw - bw - 20,
    y: sh - bh - 40,

    transparent: true,
    frame: false,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    hasShadow: false,
    show: false,
    backgroundColor: '#00000000',
    focusable: false,               // 不抢焦点，避免 z-order 冲突

    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // 始终置顶并高于任务栏
  btnWindow.setAlwaysOnTop(true, ON_TOP_LEVEL);

  if (process.platform === 'win32') {
    btnWindow.setBackgroundColor('#00000000');
  }

  btnWindow.loadURL(`${SERVER_URL}/actions?_t=${Date.now()}`);

  btnWindow.once('ready-to-show', () => {
    btnWindow.show();
    // broadcast initial customExpression state to action bar
    setTimeout(function() {
      btnWindow.webContents.send('pet-custom-expression-changed', petCustomExpression);
    }, 600);
  });

  // 保持按钮窗口始终在屏幕右下角
  const repositionBtn = () => {
    if (!btnWindow || btnWindow.isDestroyed()) return;
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    const [w, h] = btnWindow.getSize();
    btnWindow.setPosition(width - w - 20, height - h - 40);
  };

  screen.on('display-metrics-changed', repositionBtn);
  btnWindow.on('resize', repositionBtn);

  btnWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  return btnWindow;
}

// ====================== Chat 窗口 (独立对话框) ======================
function createChatWindow(focus = true, preload = false) {
  if (chatWindow && !chatWindow.isDestroyed()) {
    // 已存在（包括预加载的）→ 直接显示
    chatWindow.setOpacity(1);
    chatWindow.setIgnoreMouseEvents(false);
    chatWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);
    chatWindow.show();
    if (focus) chatWindow.focus();
    _chatVisible = true;
    broadcastChatVisible(true);
    return chatWindow;
  }

  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;

  chatWindow = new BrowserWindow({
    width: 380,
    height: 520,
    x: sw - petSize.w - 440,
    y: sh - 520 - 120,

    transparent: false,
    frame: false,
    skipTaskbar: false,
    resizable: true,
    hasShadow: true,
    show: false,
    backgroundColor: '#1a1a2e',

    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'chat_preload.js'),
    },
  });

  chatWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);

  // 加时间戳防止 Chromium 缓存旧版本 HTML/JS
  chatWindow.loadURL(`${SERVER_URL}/chat-panel?_t=${Date.now()}`);

  chatWindow.once('ready-to-show', () => {
    if (preload) {
      // 预加载模式：页面就绪后静默隐藏（不最小化，避免任务栏闪现）
      chatWindow.setOpacity(0);
      chatWindow.setIgnoreMouseEvents(true);
      chatWindow.hide();
      // _chatVisible 保持 false
    } else {
      chatWindow.show();
      if (focus) chatWindow.focus();
      _chatVisible = true;
      broadcastChatVisible(true);
    }
  });

  chatWindow.on('close', (e) => {
    // 拦截关闭 → 隐藏，保持 SSE 流和语音播放不中断
    // 但真正退出时（_isQuitting）不拦截，否则会取消 app.quit() 导致进程残留
    if (!_isQuitting) {
      e.preventDefault();
      if (chatWindow && !chatWindow.isDestroyed()) {
        chatWindow.hide();
      }
      _chatVisible = false;
      broadcastChatVisible(false);
      if (btnWindow && !btnWindow.isDestroyed()) {
        btnWindow.webContents.send('chat-window-visible', false);
      }
    }
  });

  chatWindow.on('closed', () => {
    chatWindow = null;
    _chatVisible = false;
    broadcastChatVisible(false);
    if (btnWindow && !btnWindow.isDestroyed()) {
      btnWindow.webContents.send('chat-window-visible', false);
    }
  });

  chatWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  return chatWindow;
}

// 广播 chat 窗口可见性变化
function broadcastChatVisible(visible) {
  [petWindow, btnWindow].forEach(w => {
    if (w && !w.isDestroyed()) {
      w.webContents.send('chat-window-visible', visible);
    }
  });
}

// 广播 settings 窗口可见性变化
function broadcastSettingsVisible(visible) {
  [petWindow, btnWindow].forEach(w => {
    if (w && !w.isDestroyed()) {
      w.webContents.send('settings-window-visible', visible);
    }
  });
}

// ====================== Settings 窗口 ======================
function createSettingsWindow() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.show();
    settingsWindow.focus();
    _settingsVisible = true;
    broadcastSettingsVisible(true);
    return settingsWindow;
  }

  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;

  settingsWindow = new BrowserWindow({
    width: 480,
    height: 530,
    minWidth: 400,
    minHeight: 400,
    x: Math.round((sw - 480) / 2),
    y: Math.round((sh - 530) / 2),

    transparent: false,
    frame: false,
    skipTaskbar: false,
    resizable: true,
    hasShadow: true,
    show: false,
    backgroundColor: '#1a1a2e',

    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  settingsWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);

  settingsWindow.loadURL(`${SERVER_URL}/settings?_t=${Date.now()}`);

  settingsWindow.once('ready-to-show', () => {
    settingsWindow.show();
    settingsWindow.focus();
    _settingsVisible = true;
    broadcastSettingsVisible(true);
  });

  settingsWindow.on('close', (e) => { if (!_isQuitting) { e.preventDefault(); if (settingsWindow && !settingsWindow.isDestroyed()) { settingsWindow.hide(); } } });
  settingsWindow.on('closed', () => {
    settingsWindow = null;
    _settingsVisible = false;
    broadcastSettingsVisible(false);
    if (btnWindow && !btnWindow.isDestroyed()) {
      btnWindow.webContents.send('settings-window-visible', false);
    }
  });

  settingsWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  return settingsWindow;
}

// ====================== 锁定/解锁 (只禁用拖拽和缩放) ======================
function applyLockState(locked) {
  petLocked = locked;
  petConfig.locked = locked;
  saveConfig();
  if (!petWindow || petWindow.isDestroyed()) return;

  petWindow.webContents.send('pet-lock-changed', locked);

  // 锁定 → 应用已保存的鼠标穿透状态；解锁 → 关闭穿透
  petWindow.setIgnoreMouseEvents(locked && petMouseThrough, { forward: true });

  // 同步按钮窗口图标
  if (btnWindow && !btnWindow.isDestroyed()) {
    btnWindow.webContents.send('pet-lock-changed', locked);
  }

  // setResizable 会触发 Windows 重排 z-order → 按钮窗口被遮盖
  keepBtnOnTop();
}

// ====================== 鼠标穿透 (仅在锁定时生效) ======================
function applyMouseThrough(on) {
  petMouseThrough = on;
  if (!petWindow || petWindow.isDestroyed()) return;
  // 只有锁定状态下才允许鼠标穿透
  petWindow.setIgnoreMouseEvents(petLocked && on, { forward: true });

  // 广播状态
  [settingsWindow, btnWindow].forEach(w => {
    if (w && !w.isDestroyed()) {
      w.webContents.send('pet-mouse-through-changed', on);
    }
  });
}

// ====================== IPC: Pet 窗口 ======================
ipcMain.on('pet-close', () => {
  _isQuitting = true;   // 标记退出中，绕过各窗口的 close 拦截
  if (chatWindow && !chatWindow.isDestroyed()) { chatWindow.hide(); _chatVisible = false; broadcastChatVisible(false); }
  if (settingsWindow && !settingsWindow.isDestroyed()) { settingsWindow.close(); _settingsVisible = false; }
  if (btnWindow && !btnWindow.isDestroyed()) btnWindow.close();
  app.quit();
});

ipcMain.on('pet-hide', () => {
  if (petWindow) petWindow.hide();
  if (btnWindow) btnWindow.hide();
});

ipcMain.on('pet-show', () => {
  if (petWindow) { petWindow.show(); petWindow.focus(); }
  if (btnWindow) btnWindow.show();
});

ipcMain.on('pet-resize', (_event, width, height, anchorX, anchorY) => {
  if (!petWindow || petWindow.isDestroyed() || petLocked) return;
  if (anchorX !== undefined && anchorY !== undefined) {
    const [x, y] = petWindow.getPosition();
    const [cw, ch] = petWindow.getSize();
    const newX = Math.round(x + (cw - width) * (anchorX ?? 0));
    const newY = Math.round(y + (ch - height) * (anchorY ?? 0));
    petWindow.setBounds({ x: newX, y: newY, width, height });
  } else {
    petWindow.setSize(width, height);
  }
  keepBtnOnTop();
});

ipcMain.on('pet-set-position', (_event, x, y) => {
  if (!petWindow || petWindow.isDestroyed() || petLocked) return;
  petWindow.setPosition(x, y);
  keepBtnOnTop();
});

// 锁定切换
ipcMain.on('pet-toggle-lock', () => {
  applyLockState(!petLocked);
});

ipcMain.handle('pet-is-locked', () => petLocked);

// 鼠标穿透切换
ipcMain.on('pet-toggle-mouse-through', () => {
  petMouseThrough = !petMouseThrough;
  petConfig.mouseThrough = petMouseThrough;
  saveConfig();
  applyMouseThrough(petMouseThrough);
});

// 查询当前穿透状态
ipcMain.handle('pet-get-mouse-through', () => petMouseThrough);

// 聊天气泡显示切换
ipcMain.on('pet-toggle-bubble', () => {
  petShowBubble = !petShowBubble;
  petConfig.showBubble = petShowBubble;
  saveConfig();
  // 广播给 Pet 窗口
  [petWindow].forEach(w => {
    if (w && !w.isDestroyed()) w.webContents.send('pet-bubble-changed', petShowBubble);
  });
  // 同步设置窗口
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.webContents.send('pet-bubble-changed', petShowBubble);
  }
});
ipcMain.handle('pet-get-bubble', () => petShowBubble);

// custom expression toggle (Live2D MO custom appearance)
ipcMain.on('pet-toggle-custom-expression', () => {
  petCustomExpression = !petCustomExpression;
  petConfig.customExpression = petCustomExpression;
  saveConfig();
  [petWindow, btnWindow].forEach(function(w) {
    if (w && !w.isDestroyed()) w.webContents.send('pet-custom-expression-changed', petCustomExpression);
  });
});
ipcMain.handle('pet-get-custom-expression', () => petCustomExpression);

// 自动眨眼
ipcMain.on('pet-toggle-auto-blink', () => {
  petAutoBlink = !petAutoBlink;
  petConfig.autoBlink = petAutoBlink;
  saveConfig();
  // 广播给所有窗口（包括表情面板）
  [petWindow, btnWindow, exprWindow].forEach(function(w) {
    if (w && !w.isDestroyed()) {
      w.webContents.send('pet-auto-blink-changed', petAutoBlink);
      // 保险：直接在 pet 窗口执行 JS，确保 blink 状态切换生效
      w.webContents.executeJavaScript(
        'blinkEnabled=' + petAutoBlink + ';if(blinkEnabled&&blinkPhase==="open")blinkNext=performance.now()+600'
      ).catch(function(){});
    }
  });
});
ipcMain.handle('pet-get-auto-blink', () => petAutoBlink);

// 调试模式
ipcMain.on('pet-toggle-debug', () => {
  debugMode = !debugMode;
  petConfig.debugMode = debugMode;
  saveConfig();
  // 打开/关闭 pet 窗口的 DevTools
  if (petWindow && !petWindow.isDestroyed()) {
    if (debugMode) petWindow.webContents.openDevTools({ mode: 'detach' });
    else petWindow.webContents.closeDevTools();
  }
  [btnWindow].forEach(w => {
    if (w && !w.isDestroyed()) w.webContents.send('pet-debug-changed', debugMode);
  });
});
ipcMain.handle('pet-get-debug', () => debugMode);

// custom params persist: save all active slider values
ipcMain.on('pet-save-custom-params', (_event, params) => {
  petConfig.customParams = params || {};
  saveConfig();
});
ipcMain.handle('pet-get-custom-params', () => petConfig.customParams || {});

// ====================== Expression panel window ======================
let exprWindow = null;
let _exprVisible = false;

function createExpressionWindow() {
  if (exprWindow && !exprWindow.isDestroyed()) {
    exprWindow.show();
    exprWindow.focus();
    _exprVisible = true;
    [btnWindow].forEach(function(w) {
      if (w && !w.isDestroyed()) w.webContents.send('expression-window-visible', true);
    });
    return exprWindow;
  }
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  exprWindow = new BrowserWindow({
    width: 500,
    height: 600,
    x: Math.round((sw - 500) / 2),
    y: Math.round((sh - 560) / 2),
    transparent: false,
    frame: false,
    skipTaskbar: false,
    resizable: true,
    hasShadow: true,
    show: false,
    backgroundColor: '#1a1a2e',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  exprWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);
  exprWindow.loadURL(SERVER_URL + '/expression-panel?_t=' + Date.now());
  exprWindow.once('ready-to-show', () => {
    exprWindow.show(); exprWindow.focus(); _exprVisible = true;
    [btnWindow].forEach(function(w) {
      if (w && !w.isDestroyed()) w.webContents.send('expression-window-visible', true);
    });
  });
  exprWindow.on('closed', () => {
    exprWindow = null; _exprVisible = false;
    [btnWindow].forEach(function(w) {
      if (w && !w.isDestroyed()) w.webContents.send('expression-window-visible', false);
    });
  });
  exprWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  return exprWindow;
}

ipcMain.on('expr-open', () => createExpressionWindow());

ipcMain.on('expr-close', () => {
  if (exprWindow && !exprWindow.isDestroyed()) exprWindow.close();
  _exprVisible = false;
  [btnWindow].forEach(function(w) {
    if (w && !w.isDestroyed()) w.webContents.send('expression-window-visible', false);
  });
});

// ---- Test Panel window ----
ipcMain.on('test-panel-open', () => {
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  const tw = new BrowserWindow({
    width: 500, height: 650,
    x: Math.round((sw - 500) / 2) + 60,
    y: Math.round((sh - 650) / 2) + 60,
    transparent: false, frame: false, skipTaskbar: false,
    resizable: true, hasShadow: true, show: false,
    backgroundColor: '#1a1a2e',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  tw.setAlwaysOnTop(true, SUB_TOP_LEVEL);
  tw.loadURL(SERVER_URL + '/test-panel?_t=' + Date.now());
  tw.once('ready-to-show', () => { tw.show(); tw.focus(); });
  tw.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
});

// ---- Task Panel window ----
let taskWindow = null;
let _taskVisible = false;

function createTaskWindow() {
  if (taskWindow && !taskWindow.isDestroyed()) {
    taskWindow.show(); taskWindow.focus(); _taskVisible = true;
    [btnWindow].forEach(w => { if (w && !w.isDestroyed()) w.webContents.send('task-window-visible', true); });
    return taskWindow;
  }
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  taskWindow = new BrowserWindow({
    width: 640, height: 500,
    x: Math.round((sw - 640) / 2), y: Math.round((sh - 500) / 2),
    transparent: false, frame: false, skipTaskbar: false,
    resizable: true, hasShadow: true, show: false,
    backgroundColor: '#181825',
    webPreferences: {
      nodeIntegration: false, contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  taskWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);
  taskWindow.loadURL(SERVER_URL + '/tasks-panel?_t=' + Date.now());
  taskWindow.once('ready-to-show', () => { taskWindow.show(); taskWindow.focus(); _taskVisible = true; });
  taskWindow.on('close', (e) => { if (!_isQuitting) { e.preventDefault(); taskWindow.hide(); } });
  taskWindow.on('closed', () => { taskWindow = null; _taskVisible = false; });
  taskWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  return taskWindow;
}

ipcMain.on('task-panel-open', () => createTaskWindow());
ipcMain.on('task-panel-close', () => {
  if (taskWindow && !taskWindow.isDestroyed()) { taskWindow.hide(); _taskVisible = false; }
});

// parameter setting from expression panel -> relay to pet window
ipcMain.on('pet-set-param', (_event, paramId, value) => {
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.webContents.send('pet-set-param', paramId, value);
  }
});

ipcMain.on('pet-set-multi-params', (_event, params) => {
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.webContents.send('pet-set-multi-params', params);
  }
});

// request all current param values from pet
ipcMain.handle('pet-get-all-params', () => {
  return new Promise((resolve) => {
    if (!petWindow || petWindow.isDestroyed()) { resolve(null); return; }
    petWindow.webContents.send('pet-request-all-params');
    // Wait briefly for response, timeout after 300ms
    var timeout = setTimeout(() => resolve(null), 300);
    var handler = (_e, data) => { clearTimeout(timeout); ipcMain.removeListener('pet-all-params-response', handler); resolve(data); };
    ipcMain.on('pet-all-params-response', handler);
  });
});

// response from pet window with all params
ipcMain.on('pet-all-params-response-forward', (_event, data) => {
  if (exprWindow && !exprWindow.isDestroyed()) {
    exprWindow.webContents.send('pet-all-params-response', data);
  }
});


// 重置位置 → 恢复默认位置 + 大小 + 缩放
ipcMain.on('pet-reset-position', () => {
  if (!petWindow || petWindow.isDestroyed()) return;
  const { width: sw, height: sh } = screen.getPrimaryDisplay().workAreaSize;
  const dw = DEFAULT_PET_W, dh = DEFAULT_PET_H;
  const dx = Math.round(sw - dw - 80);
  const dy = Math.round(sh - dh - 120);
  petWindow.setBounds({ x: dx, y: dy, width: dw, height: dh });
  // 保存默认值
  petConfig.petX = dx; petConfig.petY = dy;
  petConfig.petW = dw; petConfig.petH = dh;
  petConfig.modelScale = DEFAULT_MODEL_SCALE;
  saveConfig();
  // 通知 renderer 重置模型缩放
  petWindow.webContents.send('pet-model-scale', DEFAULT_MODEL_SCALE);
  keepBtnOnTop();
});

// 模型缩放保存/读取
ipcMain.on('pet-save-model-scale', (_event, scale) => {
  petConfig.modelScale = scale;
  saveConfig();
});

ipcMain.handle('pet-get-model-scale', () => petConfig.modelScale ?? DEFAULT_MODEL_SCALE);

// ====================== IPC: Chat 窗口 ======================
ipcMain.on('chat-open', (_event, focus) => createChatWindow(focus));

// 隐藏 Chat 窗口（不销毁，AI 继续运行）
ipcMain.on('chat-hide', () => {
  if (chatWindow && !chatWindow.isDestroyed()) {
    chatWindow.minimize();
    chatWindow.setOpacity(0);
    chatWindow.setIgnoreMouseEvents(true);
    _chatVisible = false;
    broadcastChatVisible(false);
    if (btnWindow && !btnWindow.isDestroyed()) {
      btnWindow.webContents.send('chat-window-visible', false);
    }
  }
});

// 折叠聊天窗口（标题栏+输入框）
let _chatFolded = false;
let _chatUnfoldedSize = null;
ipcMain.on('chat-toggle-fold', () => {
  if (!chatWindow || chatWindow.isDestroyed()) return;
  _chatFolded = !_chatFolded;
  if (_chatFolded) {
    _chatUnfoldedSize = chatWindow.getSize();
    chatWindow.setSize(_chatUnfoldedSize[0], 100);  // 只够标题栏+输入框
  } else {
    if (_chatUnfoldedSize) {
      chatWindow.setSize(_chatUnfoldedSize[0], _chatUnfoldedSize[1]);
    }
  }
});

// 用系统默认程序打开文件
ipcMain.on('open-file', (_event, filePath) => {
  shell.openPath(filePath);
});

// Chat → Pet 转发 (对话气泡、表情、TTS)
ipcMain.on('chat-to-pet', (_event, msg) => {
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.webContents.send('pet-from-chat', msg);
  }
});

// ====================== IPC: Settings 窗口 ======================
ipcMain.on('settings-open', () => {
  _settingsVisible = true;
  createSettingsWindow();
  // 和锁按钮一样直接同步发（ready-to-show 里的广播是后备）
  if (btnWindow && !btnWindow.isDestroyed()) {
    btnWindow.webContents.send('settings-window-visible', true);
  }
});
ipcMain.on('settings-close', () => {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.hide();
    _settingsVisible = false;
    broadcastSettingsVisible(false);
    if (btnWindow && !btnWindow.isDestroyed()) {
      btnWindow.webContents.send('settings-window-visible', false);
    }
  }
});

// 始终置顶切换
ipcMain.on('pet-toggle-ontop', () => {
  petAlwaysOnTop = !petAlwaysOnTop;
  petConfig.alwaysOnTop = petAlwaysOnTop;
  saveConfig();
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.setAlwaysOnTop(petAlwaysOnTop, petAlwaysOnTop ? ON_TOP_LEVEL : 'normal');
  }
  if (btnWindow && !btnWindow.isDestroyed()) {
    btnWindow.setAlwaysOnTop(petAlwaysOnTop, petAlwaysOnTop ? ON_TOP_LEVEL : 'normal');
  }
  // 同步所有窗口
  if (petWindow && !petWindow.isDestroyed()) {
    petWindow.webContents.send('pet-ontop-changed', petAlwaysOnTop);
  }
  if (btnWindow && !btnWindow.isDestroyed()) {
    btnWindow.webContents.send('pet-ontop-changed', petAlwaysOnTop);
  }
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.webContents.send('pet-ontop-changed', petAlwaysOnTop);
  }
});

// 查询当前置顶状态
ipcMain.handle('pet-get-ontop', () => petAlwaysOnTop);

// ====================== IPC: Button 窗口 ======================
let _chatVisible = false;
let _settingsVisible = false;

ipcMain.on('btn-chat-toggle', () => {
  if (chatWindow && !chatWindow.isDestroyed()) {
    if (_chatVisible) {
      chatWindow.setOpacity(0);
      chatWindow.setIgnoreMouseEvents(true);
      chatWindow.hide();
      _chatVisible = false;
      broadcastChatVisible(false);
      if (btnWindow && !btnWindow.isDestroyed()) {
        btnWindow.webContents.send('chat-window-visible', false);
      }
    } else {
      chatWindow.show();
      chatWindow.setOpacity(1);
      chatWindow.setIgnoreMouseEvents(false);
      chatWindow.setAlwaysOnTop(true, SUB_TOP_LEVEL);
      chatWindow.focus();
      _chatVisible = true;
      broadcastChatVisible(true);
      if (btnWindow && !btnWindow.isDestroyed()) {
        btnWindow.webContents.send('chat-window-visible', true);
      }
    }
  } else {
    createChatWindow(true);
    _chatVisible = true;
  }
});

// ====================== 窗口最小化/最大化 ======================
ipcMain.on('win-minimize', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) win.minimize();
});

ipcMain.on('win-toggle-maximize', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win && !win.isDestroyed()) {
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  }
});

// ====================== 父进程看门狗 ======================
// Electron 由 Python 主程序启动。若 Python 被强杀/崩溃（不会走 /api/shutdown 路径），
// 通过轮询父进程 PID 检测并自动退出，避免桌面窗口残留。
// 仅在由 launcher 启动（带 PET_SERVER_URL 环境变量）时启用，手动调试不受影响。
if (process.env.PET_SERVER_URL && process.ppid) {
  const _parentPid = process.ppid;
  setInterval(() => {
    let alive = true;
    try {
      process.kill(_parentPid, 0);  // 信号 0 仅检测进程存在性，不真正发送信号
    } catch (err) {
      // EPERM = 进程存在但无权限（视为存活）；ESRCH = 进程已不存在
      alive = err && err.code === 'EPERM';
    }
    if (!alive) {
      _parentDead = true;
      _isQuitting = true;
      app.quit();
      setTimeout(() => process.exit(0), 2000);  // 兜底：万一 quit 流程被卡住也强制退出
    }
  }, 2000);
}

// ====================== 应用生命周期 ======================
app.whenReady().then(() => {
  _configPath = path.join(app.getPath('userData'), 'pet-config.json');
  loadConfig();
  createPetWindow();
  createButtonWindow();

  // 启动时后台预加载对话窗口（提前拉取历史记录，打开时无需等待）
  createChatWindow(false, true);

  app.on('activate', () => {
    if (petWindow === null || petWindow.isDestroyed()) createPetWindow();
    else petWindow.show();
    if (btnWindow === null || btnWindow.isDestroyed()) createButtonWindow();
    else btnWindow.show();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', () => {
  _isQuitting = true;   // 确保从任何退出路径都能绕过窗口 close 拦截
  // 退出前强制保存配置 (清除 debounce 定时器)
  clearTimeout(_saveTimer);
  try { fs.writeFileSync(_configPath, JSON.stringify(petConfig, null, 2)); } catch (_) {}
  // 父进程（Python）已死时无法也无需通知后端，直接跳过（避免 fetch 白等 3s）
  if (_parentDead) {
    petWindow = null;
    btnWindow = null;
    chatWindow = null;
    settingsWindow = null;
    return;
  }
  // 同步通知后端退出，让 Python 主线程结束（execSync 阻塞确保请求发出）
  try {
    execSync(`node -e "fetch('${SERVER_URL}/api/shutdown',{method:'POST'}).catch(()=>{})"`, { timeout: 3000 });
  } catch (_) {}
  petWindow = null;
  btnWindow = null;
  chatWindow = null;
  settingsWindow = null;
});
