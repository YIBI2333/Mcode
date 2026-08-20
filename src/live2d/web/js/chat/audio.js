/* ============================================================
 * audio.js — 音频播放与停止管理
 *
 * 维护一个活动音频列表，便于一次性停止所有正在播放的音频。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _active = [];

  var _ = {};

  /**
   * 入队时预热：创建 Audio 触发浏览器 HTTP 预取（不播放）。
   * 之后 playSync() 再 new Audio 同一 URL 会命中缓存，几乎零加载等待。
   */
  _.prefetch = function (url) {
    if (!url) return;
    try {
      var a = new Audio(url);
      a.preload = 'auto';
    } catch (e) { /* ignore */ }
  };

  /**
   * 同步播放：等 canplaythrough → play()。
   * started 在声音真正出来的那一刻 resolve，
   * 调用方应在 await started 之后才启动打字/口型，实现三路起点对齐。
   * @param {string} url
   * @returns {{started:Promise<void>, done:Promise<void>, el:Audio}}
   */
  _.playSync = function (url) {
    var a = new Audio(url);
    a.preload = 'auto';
    _active.push(a);

    var started = new Promise(function (resolve) {
      var fired = false;
      function begin() {
        if (fired) return;
        fired = true;
        var p = a.play();
        if (p && p.then) p.then(resolve, resolve);
        else resolve();
      }
      a.oncanplaythrough = begin;
      a.onerror = begin;
      setTimeout(begin, 2000);   // 加载慢就不再等，先起跑
    });

    var done = new Promise(function (resolve) {
      started.then(function () {
        a.onended = resolve;
        a.onerror = resolve;
        setTimeout(resolve, 30000);
      });
    });

    return { started: started, done: done, el: a };
  };

  /** 立即停止所有活动音频 */
  _.stopAll = function () {
    _seq = [];
    _seqPlaying = false;
    _active.forEach(function (a) {
      try { a.pause(); a.src = ''; a.load(); } catch (e) {}
    });
    _active = [];
  };

  /** 移除单个音频实例（播完时调用，便于 GC） */
  _.remove = function (a) {
    _active = _active.filter(function (x) { return x !== a; });
  };

  // ── 后台顺序播放队列 ──
  // 文本渲染与音频播放解耦：句子文字可立即回显，音频按入队顺序后台依次播放，
  // 避免"后端/TTS 都完成了，前端却因逐句等音频播完而长时间卡住"。
  var _seq = [];
  var _seqPlaying = false;

  function _teardown(a) {
    _active = _active.filter(function (x) { return x !== a; });
  }

  function _seqAdvance() {
    if (_seq.length === 0) { _seqPlaying = false; return; }
    _seqPlaying = true;
    var cur = _seq.shift();
    var a = new Audio(cur.url);
    a.preload = 'auto';
    _active.push(a);

    var fired = false;
    var settled = false;

    function _notify() {
      try {
        CA.shell.notifyPet({
          type: 'lip-sync',
          text: cur.text || '',
          phonemes: cur.phonemes || [],
          duration: (cur.duration || 0) * 0.85,
          emotion: cur.emotion || 'neutral',
          intensity: cur.intensity || 0.5,
          motion: cur.emotion || 'neutral'
        });
      } catch (e) { /* ignore */ }
    }

    function _begin() {
      if (fired) return;
      fired = true;
      var p = a.play();
      if (p && p.then) {
        p.then(_notify, function () { _finish(); });
      } else {
        _notify();
      }
    }

    function _finish() {
      if (settled) return;
      settled = true;
      _teardown(a);
      _seqAdvance();
    }

    a.oncanplaythrough = _begin;
    a.onerror = _finish;
    a.onended = _finish;
    setTimeout(_begin, 2000);   // 加载慢就先起跑
  }

  /** 入队后台顺序播放（不阻塞调用方）。按入队顺序依次播，每段启动时触发口型 */
  _.playSeq = function (opt) {
    if (!opt || !opt.url) return;
    _seq.push(opt);
    if (!_seqPlaying) _seqAdvance();
  };

  CA.audio = _;
})(window.ChatApp);