/* ============================================================
 * history.js — 历史分页加载（向上滚动加载更多）
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};
  var PAGE_SIZE = 10;
  var SCROLL_THRESHOLD = 50;

  var _state = {
    loading: false,
    loaded: 0,   // 已从 API 加载的历史条数（不含 SSE 实时消息）
    els: null
  };

  /**
   * @param {object} els { msgList, msgFlow }
   */
  _.init = function (els) {
    _state.els = els;
    els.msgList.addEventListener('scroll', function () {
      if (els.msgList.scrollTop <= SCROLL_THRESHOLD) _.loadMore(PAGE_SIZE);
    });
  };

  /** 首屏加载一页 */
  _.boot = function () {
    _.loadMore(PAGE_SIZE);
  };

  _.loadMore = async function (limit) {
    if (_state.loading) return;
    _state.loading = true;
    try {
      var r = await fetch('/api/chat/history?limit=' + limit + '&skip=' + _state.loaded);
      if (!r.ok) { _state.loading = false; return; }
      var d = await r.json();
      var items = d.history || [];
      if (!items.length) { _state.loading = false; return; }

      _state.loaded += items.length;
      var msgList = _state.els.msgList;
      var msgFlow = _state.els.msgFlow;
      var prevH = msgList.scrollHeight;
      var prevTop = msgList.scrollTop;

      // 倒序插入列表顶部（数据是旧到新）
      for (var i = items.length - 1; i >= 0; i--) {
        var it = items[i];
        if (it.role === 'tool') {
          _prependToolEntry(msgFlow, it);
        } else if (it.role === 'status') {
          _prependStatusEntry(msgFlow, it);
        } else {
          var role = it.role === 'assistant' ? 'ai' : it.role;
          CA.dom.prependMsg(msgFlow, role, CA.markdown.mdToHtml(it.content));
        }
      }
      // 保持用户视角不动，抵消新增高度
      msgList.scrollTop = prevTop + (msgList.scrollHeight - prevH);
    } catch (e) {
      if (window.console) console.error('[history]', e);
    }
    _state.loading = false;
  };

  /** 历史回放：工具消息渲染为完成态条目（opencode 风格简版） */
  function _prependToolEntry(msgList, it) {
    // content 形如 "[1] write_file test.py"（call）或结果文本（result）
    var row = document.createElement('div');
    row.className = 'tool-entry done';
    var head = document.createElement('div');
    head.className = 'tool-entry-head';
    var st = document.createElement('span');
    st.className = 'tool-status';
    st.textContent = '✓';
    head.appendChild(st);
    if (it.tag === 'call') {
      var parts = (it.content || '').replace(/^\[\d+\]\s*/, '').split(' ');
      var name = document.createElement('span');
      name.className = 'tool-name';
      name.textContent = parts[0] || 'tool';
      head.appendChild(name);
      if (parts.length > 1) {
        var argsEl = document.createElement('span');
        argsEl.className = 'tool-args';
        argsEl.textContent = parts.slice(1).join(' ');
        head.appendChild(argsEl);
      }
    } else {
      var res = document.createElement('span');
      res.className = 'tool-args';
      res.textContent = (it.content || '').substring(0, 80);
      head.appendChild(res);
    }
    row.appendChild(head);
    msgList.insertBefore(row, msgList.firstChild);
  }

  /** 历史回放：任务状态事件（task-start / plan / task-end）渲染为对应卡片 */
  function _prependStatusEntry(msgList, it) {
    var esc = CA.markdown.escapeHtml;
    var type = it.type;
    var content = it.content || '';
    var meta = it.meta || {};

    if (type === 'task-start') {
      var row = document.createElement('div');
      row.className = 'msg-row task-banner-row';
      var b = document.createElement('div');
      b.className = 'task-banner running';
      b.innerHTML = '<span class="task-banner-icon">⚡</span>'
        + '<span class="task-banner-label">任务执行中</span>'
        + (content ? '<span class="task-banner-desc"> — ' + esc(content) + '</span>' : '');
      row.appendChild(b);
      msgList.insertBefore(row, msgList.firstChild);
    } else if (type === 'plan') {
      var row2 = document.createElement('div');
      row2.className = 'msg-row plan';
      var card = document.createElement('div');
      card.className = 'plan-card';
      var head = document.createElement('div');
      head.className = 'plan-head';
      head.textContent = '计划';
      card.appendChild(head);
      var ol = document.createElement('ol');
      ol.className = 'plan-steps';
      (content || '').split(' → ').filter(Boolean).forEach(function (s) {
        var li = document.createElement('li');
        li.className = 'plan-step done';
        var check = document.createElement('span');
        check.className = 'plan-check';
        check.textContent = '✓';
        var span = document.createElement('span');
        span.textContent = s;
        li.appendChild(check);
        li.appendChild(span);
        ol.appendChild(li);
      });
      card.appendChild(ol);
      row2.appendChild(card);
      msgList.insertBefore(row2, msgList.firstChild);
    } else if (type === 'task-end') {
      var row3 = document.createElement('div');
      row3.className = 'msg-row task-banner-row';
      var b3 = document.createElement('div');
      b3.className = 'task-banner finished';
      var html = '<span class="task-banner-icon">✓</span><span class="task-banner-label">任务完成</span>';
      if (meta && meta.files_total > 0) {
        html += '<span class="task-files">' + meta.files_total + ' 个文件已更改'
          + (meta.added > 0 ? ' <span class="diff-add">+' + meta.added + '</span>' : '')
          + (meta.deleted > 0 ? ' <span class="diff-del">-' + meta.deleted + '</span>' : '')
          + '</span>';
      }
      b3.innerHTML = html;
      row3.appendChild(b3);
      msgList.insertBefore(row3, msgList.firstChild);
    }
  }

  CA.history = _;
})(window.ChatApp);