/* ============================================================
 * dom.js — DOM 工具：消息行追加/前置、转义、滚动、打字效果、思考气泡
 *
 * 纯粹的 DOM 操作，不含业务逻辑，不依赖其它模块，便于单测。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};

  /**
   * 滚动消息列表到底部
   * @param {HTMLElement} msgList
   */
  _.scrollBot = function (msgList) {
    if (msgList) msgList.scrollTop = msgList.scrollHeight;
  };

  /**
   * HTML 转义（防注入）
   * @param {string} s
   * @returns {string}
   */
  _.escapeHtml = function (s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  };

  /**
   * 创建一条消息行并追加到底部
   * @param {HTMLElement} container 插入目标容器（如 #msg-flow）
   * @param {string} role - user / ai / thinking / status / tool / error / plan / speak / reflect
   * @param {string} text
   * @param {HTMLElement} [scroller] 滚动容器（默认 = container）
   * @returns {{row: HTMLElement, bubble: HTMLElement}}
   */
  _.addMsg = function (container, role, text, scroller) {
    var row = document.createElement('div');
    row.className = 'msg-row ' + role;

    var bub = document.createElement('div');
    bub.className = 'msg-bubble';
    bub.textContent = text;
    row.appendChild(bub);
    container.appendChild(row);
    _.scrollBot(scroller || container);
    return { row: row, bubble: bub };
  };

  /**
   * 在列表顶部插入消息（加载历史时用）
   * @param {HTMLElement} container 插入目标容器（如 #msg-flow）
   * @param {string} role
   * @param {string} html 已经过 mdToHtml 处理的 HTML
   * @returns {HTMLElement} row
   */
  _.prependMsg = function (container, role, html) {
    var row = document.createElement('div');
    row.className = 'msg-row ' + role;
    var bub = document.createElement('div');
    bub.className = 'msg-bubble';
    bub.innerHTML = html;
    row.appendChild(bub);
    container.insertBefore(row, container.firstChild);
    return row;
  };

  /**
   * 逐字打字效果（支持 emoji / 中英混排）
   * 返回 Promise，打字完成时 resolve。
   * @param {HTMLElement} bub
   * @param {string} text
   * @param {number} totalMs 这一句话的打字总时长（毫秒）
   */
  _.typeText = function (bub, text, totalMs) {
    return new Promise(function (resolve) {
      var chars = Array.from(text);
      var n = chars.length;
      if (!n) { resolve(); return; }
      var perChar = Math.max(totalMs / n, 20);
      var i = 0;
      (function tick() {
        if (i >= n) { resolve(); return; }
        bub.textContent += chars[i++];
        setTimeout(tick, perChar);
      })();
    });
  };

  /**
   * 便捷睡眠
   */
  _.sleep = function (ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  };

  CA.dom = _;
})(window.ChatApp);