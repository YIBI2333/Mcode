const { app, BrowserWindow } = require('electron');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const PORT = 9191;
const MIME = {'.html':'text/html','.js':'application/javascript','.json':'application/json','.png':'image/png','.moc3':'application/octet-stream','.css':'text/css'};

const server = http.createServer((req, res) => {
  const p = path.join(ROOT, req.url.split('?')[0]);
  try {
    const data = fs.readFileSync(p);
    res.writeHead(200, {'Content-Type': MIME[path.extname(p)]||'text/plain','Access-Control-Allow-Origin':'*'});
    res.end(data);
  } catch(e) { res.writeHead(404); res.end('404: '+req.url); }
});

app.whenReady().then(() => {
  server.listen(PORT, () => {
    console.log('Server:', PORT);

    const win = new BrowserWindow({
      width: 960, height: 720,
      transparent: true, frame: false,
      skipTaskbar: true, resizable: true,
      hasShadow: false, backgroundColor: '#00000000',
      webPreferences: { nodeIntegration: false, contextIsolation: true }
    });

    win.webContents.on('did-fail-load', (e, code, desc, url) => {
      console.error('FAIL:', code, desc, url);
    });

    win.loadURL('http://localhost:' + PORT + '/live2d_mcp/test.html');
    win.setAlwaysOnTop(true, 'screen-saver');
    win.webContents.openDevTools({ mode: 'detach' });

    // 捕获渲染进程 console.error
    win.webContents.on('console-message', (e, lvl, msg) => {
      if (lvl >= 2) console.log('[page]', msg);
    });
  });
});

app.on('window-all-closed', () => { server.close(); app.quit(); });
