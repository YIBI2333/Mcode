/* ============================================================
 * main.js — Chat 入口
 *
 * 粘合所有模块，构造 renderer（PlayQueue 的 DOM 实现），绑定交互事件。
 * 这是唯一可以引用全部子模块、且掌握全部 DOM 元素引用的入口文件。
 * ============================================================ */
(function (CA) {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var els;
  var queue;
  var renderer;

  // ── 第一步：取元素 ──
  function _cacheEls() {
    els = {
      msgList:    $('msg-list'),
      msgFlow:    $('msg-flow'),
      taskStatus: $('task-status'),
      inputEl:    $('chat-input'),
      sendBtn:    $('send-btn'),
      stopBtn:    $('stop-btn'),
      dot:        $('status-dot'),
      fileInput:  $('file-input'),
      uploadBtn:  $('upload-btn'),
      filesBar:   $('files-bar'),
      dragOverlay:$('drag-overlay'),
      lightbox:   $('lightbox'),
      lightboxImg:$('lightbox-img'),
      toolDetailOverlay:   $('tool-detail-overlay'),
      toolDetailContent:   $('tool-detail-content'),
      toolDetailClose:     $('tool-detail-close'),
      btnClose:    $('btn-close'),
      btnMinimize: $('btn-minimize'),
      btnMaximize: $('btn-maximize'),
      btnFold:     $('btn-fold')
    };
  }

  // ── 当前任务计划卡引用（plan 工具持有） ──
  var _planCard = null;   // {steps: [...], elements:[{li, check}]}
  var _stepCounter = 0;
  var _activeBanner = null;  // { banner, row } — 当前"任务执行中"横幅（与 task-end 配对）

  // 把任务横幅收尾成已完成/已中断
  function _markBannerDone(banner, interrupted) {
    if (banner.classList.contains('running')) banner.classList.remove('running');
    banner.classList.add('finished');
    var icon = banner.querySelector('.task-banner-icon');
    var label = banner.querySelector('.task-banner-label');
    if (icon) icon.textContent = '✓';
    if (label) label.textContent = interrupted ? '已中断' : '任务完成';
  }

  // ── renderer：把 PlayQueue 抽象动作落到真实 DOM/Pet ──
  function _buildRenderer() {
    return {
      setStatusDot: function (cls) { els.dot.className = cls; },
      rmThink: function () {
        var t = window.__chatThinkObj;
        if (t && t.row && t.row.parentNode) t.row.remove();
        window.__chatThinkObj = null;
      },
      addThinking: function (text) {
        var r = CA.dom.addMsg(els.msgFlow, 'thinking', text, els.msgList);
        window.__chatThinkObj = r;
        return r;
      },
      addStatus: function (text, type) {
        CA.dom.addMsg(els.msgFlow, type === 'error' ? 'error' : 'status', text, els.msgList);
      },
      addPlan: function (text) {
        // text 形如 "step1 → step2 → step3"
        _planCard && _planCard.row.remove();
        var row = document.createElement('div');
        row.className = 'msg-row plan';
        var card = document.createElement('div');
        card.className = 'plan-card';
        var head = document.createElement('div');
        head.className = 'plan-head';
        head.textContent = '计划';
        card.appendChild(head);
        var ol = document.createElement('ol');
        ol.className = 'plan-steps';
        var steps = String(text || '').split(' → ').filter(Boolean);
        _planCard = { row: row, steps: steps, elements: [] };
        steps.forEach(function (s) {
          var li = document.createElement('li');
          li.className = 'plan-step';
          var check = document.createElement('span');
          check.className = 'plan-check';
          check.textContent = '○';
          var span = document.createElement('span');
          span.textContent = s;
          li.appendChild(check);
          li.appendChild(span);
          ol.appendChild(li);
          _planCard.elements.push({ li: li, check: check });
        });
        card.appendChild(ol);
        row.appendChild(card);
        els.taskStatus.appendChild(row);
        CA.dom.scrollBot(els.msgList);
      },
      addReflect: function (text) {
        var row = document.createElement('div');
        row.className = 'msg-row reflect';
        var bub = document.createElement('div');
        bub.className = 'msg-bubble reflect-bubble';
        bub.textContent = '💭 ' + (text || '');
        row.appendChild(bub);
        els.msgFlow.appendChild(row);
        CA.dom.scrollBot(els.msgList);
      },
      addTaskStart: function (text) {
        // 上个任务横幅若仍在"运行中"→ 先收尾为已中断，避免残留在历史里转圈
        if (_activeBanner && _activeBanner.banner) {
          var ob = _activeBanner.banner;
          if (ob.parentNode && ob.classList.contains('running')) _markBannerDone(ob, true);
          _activeBanner = null;
        }
        // 清空上个任务可能残留的状态（异常未 end 时），固定到历史
        _flushTaskStatus();
        var row = document.createElement('div');
        row.className = 'msg-row task-banner-row';
        var banner = document.createElement('div');
        banner.className = 'task-banner running';
        var icon = document.createElement('span');
        icon.className = 'task-banner-icon';
        icon.textContent = '⚡';
        var label = document.createElement('span');
        label.className = 'task-banner-label';
        label.textContent = '任务执行中';
        banner.appendChild(icon);
        banner.appendChild(label);
        if (text) {
          var desc = document.createElement('span');
          desc.className = 'task-banner-desc';
          desc.textContent = ' — ' + text;
          banner.appendChild(desc);
        }
        row.appendChild(banner);
        els.taskStatus.appendChild(row);
        _activeBanner = { banner: banner, row: row };
        CA.dom.scrollBot(els.msgList);
      },
      addTaskEnd: function (_kind, meta) {
        var entry = _activeBanner;
        if (entry && entry.banner && entry.banner.parentNode) {
          var banner = entry.banner;
          _markBannerDone(banner, false);
          // 文件变更汇总（opencode 风格: N 个文件已更改 +45 -12）
          if (meta && meta.files_total > 0) {
            var stats = document.createElement('span');
            stats.className = 'task-files';
            var t1 = document.createElement('span');
            t1.textContent = meta.files_total + ' 个文件已更改';
            stats.appendChild(t1);
            if (meta.added > 0) {
              var a1 = document.createElement('span');
              a1.className = 'diff-add';
              a1.textContent = '+' + meta.added;
              stats.appendChild(a1);
            }
            if (meta.deleted > 0) {
              var d1 = document.createElement('span');
              d1.className = 'diff-del';
              d1.textContent = '-' + meta.deleted;
              stats.appendChild(d1);
            }
            banner.appendChild(stats);
          }
          _activeBanner = null;
        } else {
          // 兜底：没有横幅时补一个完成标记（含文件统计）
          var row = document.createElement('div');
          row.className = 'msg-row task-banner-row';
          var b = document.createElement('div');
          b.className = 'task-banner finished';
          var html = '<span class="task-banner-icon">✓</span><span class="task-banner-label">任务完成</span>';
          if (meta && meta.files_total > 0) {
            html += '<span class="task-files">' + meta.files_total + ' 个文件已更改'
              + (meta.added > 0 ? ' <span class="diff-add">+' + meta.added + '</span>' : '')
              + (meta.deleted > 0 ? ' <span class="diff-del">-' + meta.deleted + '</span>' : '')
              + '</span>';
          }
          b.innerHTML = html;
          row.appendChild(b);
          els.taskStatus.appendChild(row);
        }
        // 任务结束：把状态从"跟随区"搬到历史流，固定下来
        _flushTaskStatus();
        CA.dom.scrollBot(els.msgList);
      },
      // 主动收尾仍在运行的横幅（用户停止 / 系统消息打断时）
      finalizeTaskBanner: function (_msg) {
        if (!_activeBanner || !_activeBanner.banner) return;
        var banner = _activeBanner.banner;
        if (banner.parentNode) _markBannerDone(banner, true);
        _activeBanner = null;
        _flushTaskStatus();
        CA.dom.scrollBot(els.msgList);
      },
      // ── opencode 风格工具条目 ──
      addToolEntry: function (data) {
        // data: {tag:'call', text:'tool_name args', meta:{tool, args}}
        var meta = data.meta || {};
        var entry = document.createElement('div');
        entry.className = 'tool-entry running';

        var head = document.createElement('div');
        head.className = 'tool-entry-head';

        var st = document.createElement('span');
        st.className = 'tool-status';
        st.textContent = '◐';
        head.appendChild(st);

        var name = document.createElement('span');
        name.className = 'tool-name';
        name.textContent = meta.tool || data.text || 'tool';
        head.appendChild(name);

        if (meta.args) {
          var argsEl = document.createElement('span');
          argsEl.className = 'tool-args';
          argsEl.textContent = meta.args;
          head.appendChild(argsEl);
        }
        entry.appendChild(head);

        els.msgFlow.appendChild(entry);
        window.__chatToolEntry = entry;
        CA.dom.scrollBot(els.msgList);
        // 工具开跑 → 计划卡勾选下一项
        _advancePlan();
      },
      completeToolEntry: function (data) {
        // data: {tag:'result', text:'...', meta:{file_op?}}
        var entry = window.__chatToolEntry;
        var meta = data.meta || {};
        var fileOp = meta.file_op || null;

        if (entry && entry.parentNode) {
          entry.classList.remove('running');
          entry.classList.add('done');
          var st = entry.querySelector('.tool-status');
          if (st) st.textContent = '✓';

          // diff 徽章（opencode 风格: +20 -3）
          if (fileOp && (fileOp.added > 0 || fileOp.deleted > 0 || fileOp.op === 'create')) {
            var badge = document.createElement('span');
            badge.className = 'tool-diff';
            var shortFile = (fileOp.file || '').split(/[\\\/]/).pop();
            var opTag = document.createElement('span');
            opTag.className = 'tool-diff-op ' + fileOp.op;
            opTag.textContent = fileOp.op === 'create' ? '新建' : (fileOp.op === 'delete' ? '删除' : (fileOp.op === 'move' ? '移动' : '编辑'));
            badge.appendChild(opTag);
            var fname = document.createElement('span');
            fname.className = 'tool-diff-file';
            fname.textContent = shortFile;
            fname.title = fileOp.file;
            badge.appendChild(fname);
            var addEl = document.createElement('span');
            addEl.className = 'diff-add';
            addEl.textContent = '+' + (fileOp.added || 0);
            badge.appendChild(addEl);
            var delEl = document.createElement('span');
            delEl.className = 'diff-del';
            delEl.textContent = '-' + (fileOp.deleted || 0);
            badge.appendChild(delEl);
            entry.querySelector('.tool-entry-head').appendChild(badge);
          }

          // 结果区：智能折叠（短结果不折叠）+ 图片缩略图
          if (data.text) {
            var ext = _extractImages(data.text);
            var resultBox = document.createElement('div');
            resultBox.className = 'tool-result';

            // 图片缩略图（若有）— 点击放大
            if (ext.images.length) {
              var imgGrid = document.createElement('div');
              imgGrid.className = 'tool-images';
              ext.images.forEach(function (url) {
                var img = document.createElement('img');
                img.className = 'tool-image';
                img.src = url;
                img.title = '点击放大';
                img.addEventListener('click', function (e) {
                  e.stopPropagation();
                  els.lightboxImg.src = url;
                  els.lightbox.classList.add('show');
                });
                imgGrid.appendChild(img);
              });
              resultBox.appendChild(imgGrid);
            }

            // 文本：短文本直接完整显示，长文本才折叠展开
            var txt = ext.cleanText;
            if (txt) {
              var TOOL_RESULT_MAX = 120;
              if (txt.length <= TOOL_RESULT_MAX) {
                var plain = document.createElement('div');
                plain.className = 'tool-result-plain';
                plain.textContent = txt;
                resultBox.appendChild(plain);
              } else {
                var summary = document.createElement('div');
                summary.className = 'tool-result-summary';
                summary.textContent = txt.substring(0, TOOL_RESULT_MAX) + '…';
                var full = document.createElement('div');
                full.className = 'tool-result-full';
                full.textContent = txt;
                resultBox.appendChild(summary);
                resultBox.appendChild(full);
                resultBox.addEventListener('click', function (e) {
                  if (e.target.tagName === 'IMG') return;
                  e.stopPropagation();
                  var open = full.classList.toggle('open');
                  resultBox.classList.toggle('expanded', open);
                });
              }
            }
            entry.appendChild(resultBox);
          }
        } else {
          // 兜底：没有 pending 条目（如历史回放）→ 直接渲染完成态单行
          var row2 = document.createElement('div');
          row2.className = 'tool-entry done';
          row2.innerHTML = '<div class="tool-entry-head"><span class="tool-status">✓</span><span class="tool-name">结果</span></div>';
          els.msgFlow.appendChild(row2);
        }
        window.__chatToolEntry = null;
        CA.dom.scrollBot(els.msgList);
      },
      notifyPet: function (msg) { CA.shell.notifyPet(msg); },
      createSentence: function () {
        return CA.dom.addMsg(els.msgFlow, 'ai', '', els.msgList);
      },
      isMarkdown: function (text) { return CA.markdown.isMarkdownBlock(text); },
      renderMarkdown: function (bubble, text) { bubble.innerHTML = CA.markdown.mdToHtml(text); },
      typeAndSpeak: async function (item, bubble) {
        // 音频走后台顺序队列（不阻塞本句的文本回显），避免逐句等音频播完而整体卡住。
        if (item.tts_url) {
          CA.audio.playSeq({
            url: item.tts_url,
            text: item.text,
            phonemes: item.phonemes || [],
            duration: item.duration_ms || Math.min(item.text.length * 80, 3000),
            emotion: item.emotion || 'neutral',
            intensity: item.intensity || 0.5
          });
        }
        // 打字时长：短固定上限（不再等于音频时长，否则一句要打 8 秒，观感像卡死）
        var defaultDur = Math.min(item.text.length * 60, 1500);
        var dur = Math.max(400, Math.min(item.duration_ms || defaultDur, 1000));
        await CA.dom.typeText(bubble, item.text, dur);
        // 打字完成后做 Markdown 渲染（加粗/行内代码/代码块/表格）
        try { bubble.innerHTML = CA.markdown.mdToHtml(bubble.textContent); } catch (e) {}
      }
    };
  }

  // plan 卡片里把下一步的 ○ 改成 ✓
  function _advancePlan() {
    if (!_planCard) return;
    if (_stepCounter >= _planCard.elements.length) return;
    var e = _planCard.elements[_stepCounter];
    e.check.textContent = '✓';
    e.li.classList.add('done');
    _stepCounter++;
  }

  // 把"跟随区"（task-status）的内容搬到历史流，固定任务状态
  function _flushTaskStatus() {
    if (!els || !els.taskStatus || !els.msgFlow) return;
    while (els.taskStatus.firstChild) {
      els.msgFlow.appendChild(els.taskStatus.firstChild);
    }
    _planCard = null;
    _stepCounter = 0;
  }

  // 从工具结果文本提取图片 URL + 清理后的文本
  function _extractImages(text) {
    var images = [];
    var clean = text || '';
    // [IMAGE] 标记（后端显式标注）
    clean = clean.replace(/\[IMAGE\]\s*(\/[^\s\n]+)/g, function (m, url) {
      if (images.indexOf(url) === -1) images.push(url);
      return '';
    });
    // 直接出现的图片 URL 路径（/uploads /workspace /screenshots）
    var re = /(\/(?:uploads|workspace|screenshots)\/[^\s\)"'`]+\.(?:png|jpe?g|webp|gif|bmp))/gi;
    var m;
    while ((m = re.exec(clean)) !== null) {
      if (images.indexOf(m[1]) === -1) images.push(m[1]);
    }
    clean = clean.replace(/\n{2,}/g, '\n').trim();
    return { images: images, cleanText: clean };
  }

  // ── 事件绑定 ──
  function _bindEvents() {
    // 输入：自动调高 + 更新按钮
    els.inputEl.addEventListener('input', function () {
      CA.send.updateBtn();
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });
    // 回车发送（Shift+Enter 换行）
    els.inputEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        var val = els.inputEl.value;
        if (val.trim()) CA.send.send(val);
      }
    });
    els.sendBtn.addEventListener('click', function () { CA.send.send(els.inputEl.value); });
    // 停止/发送 切换
    els.stopBtn.addEventListener('click', function () {
      // 停止按钮 = 总是打断当前生成（不发送输入框内容）
      CA.send.stopGeneration();
    });
    // 窗口控制
    els.btnClose.addEventListener('click', function () { CA.shell.close(); });
    els.btnMinimize.addEventListener('click', function () { CA.shell.minimizeWindow(); });
    els.btnMaximize.addEventListener('click', function () { CA.shell.toggleMaximize(); });
    els.btnFold.addEventListener('click', function () {
      document.body.classList.toggle('folded');
      CA.shell.toggleFold();
    });
    // 灯箱
    els.lightbox.addEventListener('click', function () { this.classList.remove('show'); });
    // 代码块复制按钮 + 图片点击放大（事件委托）
    els.msgList.addEventListener('click', function (e) {
      var copyBtn = e.target.closest ? e.target.closest('.code-copy') : null;
      if (copyBtn) {
        e.stopPropagation();
        var block = copyBtn.closest('.code-block');
        var code = block ? block.querySelector('code') : null;
        if (code) {
          var text = code.textContent || '';
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () {
              copyBtn.textContent = '已复制';
              setTimeout(function () { copyBtn.textContent = '复制'; }, 1200);
            });
          }
        }
        return;
      }
      // 正文里的图片 → 灯箱放大
      var imgEl = e.target.closest ? e.target.closest('.msg-image') : null;
      if (imgEl && els.lightbox && els.lightboxImg) {
        e.stopPropagation();
        els.lightboxImg.src = imgEl.src;
        els.lightbox.classList.add('show');
      }
    });
    // 工具详情弹窗关闭
    if (els.toolDetailClose) {
      els.toolDetailClose.addEventListener('click', function () {
        els.toolDetailOverlay.classList.remove('show');
      });
      els.toolDetailOverlay.addEventListener('click', function (e) {
        if (e.target === els.toolDetailOverlay) els.toolDetailOverlay.classList.remove('show');
      });
    }
  }

  // ── 启动 ──
  function _boot() {
    _cacheEls();
    CA.files.init({
      fileInput: els.fileInput, uploadBtn: els.uploadBtn,
      filesBar: els.filesBar, dragOverlay: els.dragOverlay,
      inputEl: els.inputEl
    });
    renderer = _buildRenderer();
    queue = new CA.PlayQueue(renderer);

    CA.send.bind({
      els: els,
      queue: queue,
      renderer: renderer,
      files: CA.files,
      onDone: function (text) { CA.shell.notifyPet({ type: 'ai-done', text: text }); }
    });

    CA.history.init({ msgList: els.msgList, msgFlow: els.msgFlow });
    CA.history.boot();

    CA.poll.init(CA.send.isBusy, function (msg) { CA.send.send(msg, true); });
    // 启动后 600ms 检查一次，再 30s 轮询
    setTimeout(function () {
      CA.poll.check();
      els.inputEl.focus();
    }, 600);
    CA.poll.start();

    _bindEvents();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
  } else {
    _boot();
  }
})(window.ChatApp);