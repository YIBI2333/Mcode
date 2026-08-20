/* ============================================================
 * sse.js — SSE 流解析 + 事件分发
 *
 * 封装 readable stream → 事件回调 的逻辑，供 send.js 用。
 * 不耦合 fetch（接收已有 reader），便于测试与替换。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};

  /**
   * 把已打开的 ReadableStream reader 读到底，逐事件回调。
   * @param {ReadableStreamDefaultReader} reader
   * @param {object} handlers
   *   - onStatus(st)
   *   - onTool(td)
   *   - onSentence(sn)
   *   - onUserPrompt(up)
   *   - onDone()
   *   - onError(err)
   * @returns {Promise<void>}
   */
  _.drain = async function (reader, handlers) {
    var decoder = new TextDecoder('utf-8');
    var buf = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });

      var parts = buf.split('\n\n');
      buf = parts.pop();

      for (var i = 0; i < parts.length; i++) {
        var block = parts[i].trim();
        if (!block) continue;

        var evtType = '', evtData = '';
        var lines = block.split('\n');
        for (var j = 0; j < lines.length; j++) {
          var ln = lines[j].trim();
          if (ln.indexOf('event:') === 0) evtType = ln.substring(6).trim();
          else if (ln.indexOf('data:') === 0) evtData = ln.substring(5).trim();
        }
        if (!evtType) continue;

        try {
          var data = evtData ? JSON.parse(evtData) : null;
        } catch (e) {
          if (window.console) console.warn('[SSE] parse err', evtData, e);
          continue;
        }

        switch (evtType) {
          case 'status':       handlers.onStatus && handlers.onStatus(data); break;
          case 'tool':         handlers.onTool && handlers.onTool(data); break;
          case 'sentence':     handlers.onSentence && handlers.onSentence(data); break;
          case 'user-prompt':  handlers.onUserPrompt && handlers.onUserPrompt(data); break;
          case 'done':         handlers.onDone && handlers.onDone(data); break;
          default: break;
        }
      }
    }
  };

  CA.sse = _;
})(window.ChatApp);