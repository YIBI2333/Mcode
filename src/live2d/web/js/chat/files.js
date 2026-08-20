/* ============================================================
 * files.js — 文件上传 / 拖拽 / chips 显示
 *
 * 管理本地 _pendingFiles 列表 + chips 渲染 + 拖拽遮罩。
 * 不含发送逻辑（发送时由 send.js 取 pending() 快照）。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};

  // 本地状态
  var _pending = [];   // [{path, url, name, is_image}]
  var _els = {};       // fileInput, uploadBtn, filesBar, dragOverlay, msgList

  _.init = function (els) {
    _els = els;
    _els.uploadBtn.addEventListener('click', function () { _els.fileInput.click(); });
    _els.fileInput.addEventListener('change', function () {
      _.upload(_els.fileInput.files).then(function () {
        _els.fileInput.value = '';
      });
    });
    _bindDrag();
  };

  _.pending = function () {
    return _pending.slice();
  };

  _.clear = function () {
    _pending = [];
    _render();
  };

  /** 上传一组 File[]，依次 POST /api/upload，成功则加入 pending */
  _.upload = async function (files) {
    if (!files || !files.length) return;
    _els.uploadBtn.textContent = '⏳';
    _els.uploadBtn.disabled = true;
    for (var i = 0; i < files.length; i++) {
      try {
        var fd = new FormData();
        fd.append('file', files[i]);
        var resp = await fetch('/api/upload', { method: 'POST', body: fd });
        var data = await resp.json();
        if (data.ok) {
          _pending.push({ path: data.path, url: data.url, name: data.name, is_image: data.is_image });
        }
      } catch (e) { /* skip failed */ }
    }
    _render();
    _els.uploadBtn.textContent = '+';
    _els.uploadBtn.disabled = false;
  };

  function _render() {
    var bar = _els.filesBar;
    bar.innerHTML = '';
    _pending.forEach(function (f, i) {
      var chip = document.createElement('span');
      chip.className = 'file-chip';
      if (f.is_image) {
        var thumb = document.createElement('img');
        thumb.src = f.url;
        chip.appendChild(thumb);
      }
      var name = document.createElement('span');
      name.className = 'chip-name';
      name.textContent = f.is_image ? '' : '📄 ' + f.name.substring(0, 30);
      name.title = f.name;
      chip.appendChild(name);
      var rm = document.createElement('span');
      rm.className = 'chip-remove';
      rm.textContent = '✕';
      rm.addEventListener('click', function () { _pending.splice(i, 1); _render(); });
      chip.appendChild(rm);
      bar.appendChild(chip);
    });
    bar.classList.toggle('show', _pending.length > 0);
  }

  // ── 拖拽遮罩 ──
  function _bindDrag() {
    var overlay = _els.dragOverlay;
    var counter = 0;
    document.body.addEventListener('dragenter', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (++counter === 1) overlay.classList.add('show');
    });
    document.body.addEventListener('dragleave', function (e) {
      e.preventDefault(); e.stopPropagation();
      if (--counter <= 0) { counter = 0; overlay.classList.remove('show'); }
    });
    document.body.addEventListener('dragover', function (e) { e.preventDefault(); e.stopPropagation(); });
    document.body.addEventListener('drop', async function (e) {
      e.preventDefault(); e.stopPropagation();
      counter = 0; overlay.classList.remove('show');
      if (e.dataTransfer && e.dataTransfer.files) {
        await _.upload(e.dataTransfer.files);
        if (_els.inputEl) _els.inputEl.focus();
      }
    });
  }

  CA.files = _;
})(window.ChatApp);