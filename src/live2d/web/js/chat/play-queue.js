/* ============================================================
 * play-queue.js — 顺序播放队列
 *
 * 负责按入队顺序依次"渲染并播放"事件项：句子（含 TTS+口型） / 工具气泡 / 状态气泡。
 * 一边播一边接新项；空时通过 onEmpty 回调通知上层（用于 ai-done 收尾）。
 *
 * 依赖注入（构造参数）：renderer 提供具体渲染/通知动作，
 *   使队列本身不直接依赖 DOM/Audio/Pet，便于单测。
 *
 * item 约定:
 *   { _type:'status', data:{ type, text, tts_url?, phonemes?, duration_ms? } }
 *   { _type:'tool',   data:{ tag, text } }
 *   { _type:'sentence', text, tts_url?, duration_ms?, phonemes?, emotion?, intensity?, bubble? }
 * ============================================================ */
(function (CA) {
  'use strict';

  /**
   * @param {object} renderer
   *   - setStatusDot(cls)
   *   - rmThink()
   *   - addToolMsg(tag, text)
   *   - addStatus(text, type)            // 用于 plan/progress/reflect/cancelled/error...
   *   - addThinking(text)
   *   - notifyPet(msg)
   *   - createSentence()                 // 返回 {bubble}
   *   - typeAndSpeak(item, bubble)       // 返回 Promise，完成打字+音频
   *   - renderMarkdown(bubble, text)     // 表格/代码块直接渲染
   *   - isMarkdown(text)                 // 判断是否表格/代码块
   *   - relayoutIfFiles()                // 有文件条时调整高度
   */
  function PlayQueue(renderer) {
    this._q = [];
    this._running = false;
    this._onEmptyCbs = [];
    this._renderer = renderer;
  }

  PlayQueue.prototype.enqueue = function (item) {
    this._q.push(item);
    if (!this._running) this._playNext();
  };

  PlayQueue.prototype.onEmpty = function (cb) {
    if (this._q.length === 0 && !this._running) { try { cb(); } catch (e) {} return; }
    this._onEmptyCbs.push(cb);
  };

  PlayQueue.prototype.clear = function () {
    this._q.length = 0;
    this._onEmptyCbs = [];
  };

  PlayQueue.prototype._playNext = function () {
    if (this._running) return;
    this._running = true;
    this._loop();
  };

  PlayQueue.prototype._loop = async function () {
    var R = this._renderer;
    while (this._q.length > 0) {
      var item = this._q.shift();

      // ── 状态项 ──
      if (item._type === 'status') {
        try {
          R.setStatusDot('streaming');
          R.rmThink();
          var s = item.data;
          if (s.type === 'thinking') {
            R.notifyPet({ type: 'ai-thinking' });
            R.addThinking('思考中...');
          } else if (s.type === 'step') {
            // step 仅用于 TTS 播报（语音"正在写入文件..."），不渲染文本气泡。
            // 走后台顺序队列，不阻塞队列推进，避免多个 step 叠加导致卡顿。
            if (s.tts_url) {
              CA.audio.playSeq({
                url: s.tts_url,
                text: '',
                phonemes: s.phonemes || [],
                duration: s.duration_ms || 0,
                emotion: 'neutral',
                intensity: 0.5
              });
            }
          } else if (s.type === 'plan') {
            // 计划卡片
            R.addPlan(s.text);
          } else if (s.type === 'reflect') {
            R.addReflect(s.text);
          } else if (s.type === 'done') {
            // 任务完成标记
            if (s.text) R.addStatus(s.text, 'done');
          } else if (s.type === 'task-start') {
            // 任务模式横幅（无论 LLM 是否调 plan，都显示任务容器）
            R.addTaskStart(s.text);
          } else if (s.type === 'task-end') {
            R.addTaskEnd(s.text, s.meta);
          } else {
            var pf = { progress: '▸ ', cancelled: '▸ ', error: '' }[s.type] || '';
            R.addStatus(pf + (s.text || ''), s.type === 'error' ? 'error' : 'status');
          }
          await CA.dom.sleep(80);
        } catch (e) { /* 忽略单帧异常，继续队列 */ }
        continue;
      }

      // ── 工具项：opencode 风格条目（call 创建 → result 完成） ──
      if (item._type === 'tool') {
        try {
          R.setStatusDot('streaming');
          R.rmThink();
          var td = item.data;
          if (td.tag === 'call') {
            R.addToolEntry(td);        // 创建条目（spinner）
          } else {
            R.completeToolEntry(td);   // 条目完成（✓ + 结果折叠 + diff 徽章）
          }
          await CA.dom.sleep(80);
        } catch (e) {}
        continue;
      }

      // ── 句子项 ──
      if (!item.text) continue;
      if (!item.bubble) {
        var row = R.createSentence();
        item.bubble = row ? row.bubble : null;
        if (!item.bubble) continue;
      }
      try {
        R.setStatusDot('streaming');
        if (R.isMarkdown(item.text)) {
          R.renderMarkdown(item.bubble, item.text);
          await CA.dom.sleep(400);
          continue;
        }
        await R.typeAndSpeak(item, item.bubble);
        await CA.dom.sleep(200);
      } catch (e) {
        if (window.console) console.warn('[PlayQueue] item err:', e);
      }
    }

    this._running = false;
    R.setStatusDot('connected');
    // 防竞态：期间若有新项入队则继续
    if (this._q.length > 0) { this._playNext(); return; }
    if (this._onEmptyCbs.length > 0) {
      var cbs = this._onEmptyCbs;
      this._onEmptyCbs = [];
      cbs.forEach(function (cb) { try { cb(); } catch (e) { if (window.console) console.warn('[PlayQueue] onEmpty err:', e); } });
    }
  };

  window.ChatApp.PlayQueue = PlayQueue;
})(window.ChatApp);