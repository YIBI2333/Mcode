/* ============================================================
 * send.js — 发送消息主流程
 *
 *   - 忙时先打断再发新消息
 *   - 用户气泡渲染（含图片预览）
 *   - 打开 SSE 流，交给 sse.drain 解析，事件路由到 playQueue
 *   - user-prompt → 记住 agentSessionId，下次回 /chat/respond
 *   - 取消（AbortController + 后端 /chat/cancel）
 *   - busy 状态机 + 竞态保护（_genId）
 *
 * 依赖：CA.dom / CA.audio / CA.sse / CA.shell / CA.files / CA.markdown / queue / renderer
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};

  // ── 状态 ──
  var _busy = false;
  var _abortCtrl = null;
  var _genId = 0;
  var _agentSessionId = '';
  var _lastSendTime = 0;
  var _allSentences = [];
  var _resumeReady = false;   // 打断后是否可补充继续任务

  // ── 依赖注入 ──
  var _ctx = null;   // 见 bind()

  /**
   * @param {object} ctx
   *   - els: { inputEl, sendBtn, stopBtn, dot, msgList, fileInput, filesBar,
   *           uploadBtn, dragOverlay, lightbox, lightboxImg }
   *   - queue: PlayQueue 实例
   *   - renderer: 给 PlayQueue 用，承担 DOM 渲染
   *   - files: CA.files 模块
   *   - onBusyChange(busy)  // 外部监听 busy 变化
   *   - onDone()           // 整轮发送结束（playQueue 清空时）
   */
  _.bind = function (ctx) {
    _ctx = ctx;
  };

  _.isBusy = function () { return _busy; };
  /** 公开：根据当前输入框状态和 busy 刷新发送/停止按钮显示 */
  _.updateBtn = _updateBtn;

  function _setBusy(on) {
    _busy = on;
    _updateBtn();
    if (_ctx.onBusyChange) _ctx.onBusyChange(on);
  }

  function _updateBtn() {
    var els = _ctx.els;
    // 生成中：只显示停止按钮，禁止发送（避免快速点击堆积多个后端请求）
    if (_busy) {
      els.sendBtn.style.display = 'none';
      els.stopBtn.classList.add('show');
      return;
    }
    var hasText = els.inputEl.value.trim().length > 0;
    if (hasText) {
      els.sendBtn.style.display = '';
      els.stopBtn.classList.remove('show');
    } else {
      els.sendBtn.style.display = '';
      els.stopBtn.classList.remove('show');
    }
  }

  // ── 停止当前生成 ──
  // keepBusy=true 用于"系统消息打断"：保留 busy（新请求马上接管），避免按钮/状态闪跳；
  // 用户点停止（keepBusy 缺省/false）时立即释放 busy（可能还在播放积压内容）。
  async function _stopGeneration(keepBusy) {
    if (!_busy && !keepBusy) return;
    CA.audio.stopAll();
    _ctx.queue.clear();
    // 结束仍在运行的任务横幅，避免残留"任务执行中"
    if (_ctx && _ctx.renderer && _ctx.renderer.finalizeTaskBanner) {
      try { _ctx.renderer.finalizeTaskBanner('已中断'); } catch (e) {}
    }
    if (!keepBusy) {
      _abortCtrl = null;
      els.inputEl.disabled = false;
      _setBusy(false);
      els.inputEl.focus();
    }
    try { await fetch('/chat/cancel', { method: 'POST' }); } catch (e) {}
    if (_abortCtrl) { _abortCtrl.abort(); _abortCtrl = null; }
  }
  _.stopGeneration = _stopGeneration;

  // 打断后：提示可补充继续，输入框 placeholder 切换
  function _setResumeHint(on) {
    _resumeReady = on;
    if (_ctx && _ctx.els) {
      _ctx.els.inputEl.placeholder = on ? '补充指令，继续任务…' : '想说点什么...';
    }
  }
  _.resumeReady = function () { return _resumeReady; };

  // ── 发送 ──
  _.send = async function (text, hidden) {
    text = (text || '').trim();
    if (!text) return;
    // 生成中：
    //   - 用户消息：禁止发送（避免快速点击堆积多个后端请求）
    //   - 系统消息（定时任务/心跳/打招呼，hidden）：打断当前生成并处理，避免任务丢失
    if (_busy) {
      if (!hidden) return;
      await _stopGeneration(true);
      await CA.dom.sleep(150);
    }

    // 节流只针对用户消息（防止快速点击堆积）；系统消息（心跳/定时任务/打招呼）有后端
    // pending 机制控制频率，不节流，避免被 500ms 窗口丢弃导致"触发了但不显示"
    if (!hidden) {
      var now = Date.now();
      if (now - _lastSendTime < 500) return;
      _lastSendTime = now;
    }

    var myGenId = ++_genId;

    _setBusy(true);
    var els = _ctx.els;
    els.inputEl.value = '';
    els.inputEl.style.height = 'auto';
    _updateBtn();

    // 快照待发文件并清条
    var sendingFiles = _ctx.files.pending();
    _ctx.files.clear();

    // 用户气泡（含文件预览）
    if (!hidden) {
      var row = CA.dom.addMsg(els.msgFlow, 'user', text || '（文件）', els.msgList);
      sendingFiles.forEach(function (f) {
        if (f.is_image) {
          var img = document.createElement('img');
          img.src = f.url;
          img.style.cssText = 'max-width:200px;max-height:120px;border-radius:8px;margin:4px 4px 0 0;display:inline-block;cursor:pointer;';
          img.title = '点击预览大图';
          img.addEventListener('click', function () {
            els.lightboxImg.src = f.url;
            els.lightbox.classList.add('show');
          });
          row.bubble.appendChild(img);
        } else {
          var tag = document.createElement('span');
          tag.style.cssText = 'display:inline-block;background:rgba(255,255,255,0.06);padding:2px 8px;border-radius:6px;margin:4px 4px 0 0;font-size:12px;cursor:pointer;';
          tag.textContent = '📄 ' + f.name;
          tag.title = '点击打开文件';
          tag.addEventListener('click', function () {
            try { CA.shell.openPath(f.path); } catch (e) { window.open(f.url, '_blank'); }
          });
          row.bubble.appendChild(tag);
        }
      });
    }

    _ctx.renderer.addThinking('思考中...');
    _allSentences = [];

    _abortCtrl = new AbortController();
    var endpoint = _agentSessionId ? '/chat/respond' : '/chat';
    var body = _agentSessionId
      ? JSON.stringify({ session_id: _agentSessionId, message: text })
      : JSON.stringify({ message: text, hidden: !!hidden, images: sendingFiles.map(function (f) { return f.path; }) });
    _agentSessionId = '';

    // 流结束后延迟释放 busy：等 PlayQueue 把积压内容播完，
    // 避免心跳/定时任务在播放中途插队，导致"前一个任务运行中 + 又来新任务"。
    function _releaseBusy() {
      if (_genId !== myGenId) return;
      _abortCtrl = null;
      els.inputEl.disabled = false;
      _setBusy(false);
      els.inputEl.focus();
      setTimeout(function () {
        if (!_busy && els.dot.className !== 'connected') els.dot.className = '';
      }, 5000);
    }

    try {
      var resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
        signal: _abortCtrl.signal
      });
      if (!resp.ok) { _ctx.renderer.rmThink(); throw new Error('服务器错误 ' + resp.status); }

      await CA.sse.drain(resp.body.getReader(), {
        onStatus: function (st) { _ctx.queue.enqueue({ _type: 'status', data: st }); },
        onTool:   function (td) { _ctx.queue.enqueue({ _type: 'tool',   data: td }); },
        onSentence: function (sn) {
          _ctx.renderer.rmThink();
          _allSentences.push(sn.text);
          // 预取音频：入队即开始下载，轮到播放时命中缓存零等待
          if (sn.tts_url) CA.audio.prefetch(sn.tts_url);
          // 不在这里造气泡，交给队列播放时再创建
          _ctx.queue.enqueue({
            _type: 'sentence', text: sn.text,
            tts_url: sn.tts_url || '',
            duration_ms: sn.duration_ms || Math.min(sn.text.length * 80, 3000),
            phonemes: sn.phonemes || [],
            emotion: sn.emotion || 'neutral',
            intensity: sn.intensity || 0.5
          });
        },
        onUserPrompt: function (up) {
          _agentSessionId = up.session_id || '';
          CA.dom.addMsg(els.msgFlow, 'status', '🤔 ' + (up.question || '需要你的决定~'), els.msgList);
          els.dot.className = 'paused';
        },
        onDone: function () {
          _agentSessionId = '';
          els.dot.className = 'connected';
          _setResumeHint(false);
          _ctx.queue.onEmpty(function () {
            if (_ctx.onDone) _ctx.onDone(_allSentences.join(''));
          });
        }
      });
    } catch (e) {
      if (e.name === 'AbortError') {
        _ctx.renderer.rmThink();
        CA.dom.addMsg(els.msgFlow, 'status', '▸ 已暂停，输入补充指令可继续任务', els.msgList);
        els.dot.className = '';
        _setResumeHint(true);
      } else {
        if (window.console) console.error('[Chat]', e);
        _ctx.renderer.rmThink();
        CA.dom.addMsg(els.msgFlow, 'error', String(e), els.msgList);
        els.dot.className = 'error';
      }
    }

    // 无论成功/失败/取消，都等队列清空再释放 busy（防新任务插队）
    _ctx.queue.onEmpty(_releaseBusy);
  };

  CA.send = _;
})(window.ChatApp);