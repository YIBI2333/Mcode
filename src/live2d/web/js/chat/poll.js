/* ============================================================
 * poll.js — 启动招呼 / 心跳 / 定时任务 轮询
 *
 * 前端定时向 /chat/greet-pending、/chat/heartbeat-pending、/chat/task-pending 轮询，
 * 一有待处理消息就交给上层 sender 作为隐藏消息发送。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};
  var _greetChecked = false;
  var _isBusy = function () { return false; };

  /**
   * @param {function} isBusy 返回 busy 状态
   * @param {function} sendHidden(message) 发送隐藏消息
   */
  _.init = function (isBusy, sendHidden) {
    _isBusy = isBusy;
    _._sendHidden = sendHidden;
  };

  _.check = async function () {
    try {
      // 打招呼（仅一次）
      if (!_greetChecked) {
        var gr = await fetch('/chat/greet-pending');
        if (gr.ok && (await gr.json()).greet) {
          _greetChecked = true;
          _._sendHidden('[系统] 启动完成，请主动打招呼');
          return;
        }
        _greetChecked = !!gr.ok; // 也标记成功响应避免反复
      }
      // 心跳 + 定时任务：busy 时都跳过（保留后端 pending，等当前生成结束再发，避免打断/丢失）
      if (!_isBusy()) {
        var hr = await fetch('/chat/heartbeat-pending');
        if (hr.ok) {
          var hd = await hr.json();
          if (hd.pending) { _._sendHidden(hd.message); return; }
        }
      }
      if (!_isBusy()) {
        var tr = await fetch('/chat/task-pending');
        if (tr.ok) {
          var td = await tr.json();
          if (td.pending) { _._sendHidden(td.message); return; }
        }
      }
    } catch (e) { /* 忽略瞬时网络错误 */ }
  };

  /** 启动 30s 轮询 */
  _.start = function () {
    setInterval(_.check, 30000);
  };

  CA.poll = _;
})(window.ChatApp);