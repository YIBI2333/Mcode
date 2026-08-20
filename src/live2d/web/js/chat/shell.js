/* ============================================================
 * shell.js — Electron 桥接（chatShell）安全包装
 *
 * 在非 Electron 环境（直接浏览器）提供 noop 替身，避免下游 try/catch 散布。
 * ============================================================ */
(function (CA) {
  'use strict';

  var raw = window.chatShell || {};
  var noop = function () {};

  var _ = {
    close:           raw.close           || noop,
    minimizeWindow:  raw.minimizeWindow  || noop,
    toggleMaximize:   raw.toggleMaximize  || noop,
    toggleFold:       raw.toggleFold       || noop,
    openPath:         raw.openPath         || noop,
    sendToPet:        raw.sendToPet        || noop
  };

  /** 给桌面宠物发消息（吞错误） */
  _.notifyPet = function (msg) {
    try { _.sendToPet(msg); } catch (e) { /* ignore */ }
  };

  CA.shell = _;
})(window.ChatApp);