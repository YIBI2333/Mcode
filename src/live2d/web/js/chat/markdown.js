/* ============================================================
 * markdown.js — 极简 Markdown 渲染
 *
 * 支持: 代码块(```, 带语言标签+复制按钮) / 表格 / 行内代码 / 加粗
 * 不引入第三方库，只做最小够用转换，风格向 opencode 靠拢。
 * ============================================================ */
(function (CA) {
  'use strict';

  var _ = {};

  /** HTML 转义 */
  _.escapeHtml = function (s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  };

  /** 把 **xxx** 转成 <b>xxx</b>（输入须已转义） */
  _.renderBold = function (text) {
    return _.escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  };

  /**
   * Markdown → HTML
   * 处理顺序: 代码块占位 → 表格 → 行内代码 → 加粗 → 还原代码块
   * @param {string} text
   * @returns {string}
   */
  _.mdToHtml = function (text) {
    var codes = [];

    // 1) 代码块 → 占位符
    text = text.replace(/```(\w*)\n?([\s\S]*?)```/g, function (m, lang, code) {
      codes.push({ lang: (lang || '').trim(), code: code.trimEnd() });
      return '\u0000' + (codes.length - 1) + '\u0000';
    });

    // 2) 表格（逐行转义；首行表头，分隔行跳过，其余数据）
    var lines = text.split('\n');
    var html = '';
    var inTable = false;
    var headerDone = false;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^\s*\|.+\|\s*$/.test(line)) {
        if (!inTable) { html += '<div class="md-table"><table>'; inTable = true; headerDone = false; }
        // 分隔行（|---|:---|---:| 等）→ 跳过
        if (/^\s*\|(\s*[-:]+\s*\|)+\s*$/.test(line)) { headerDone = true; continue; }
        var cells = line.split('|').filter(function (c) { return c.trim(); });
        var tag = headerDone ? 'td' : 'th';
        html += '<tr>' + cells.map(function (c) {
          return '<' + tag + '>' + _.escapeHtml(c.trim()) + '</' + tag + '>';
        }).join('') + '</tr>';
      } else {
        if (inTable) { html += '</table></div>'; inTable = false; }
        html += _.escapeHtml(line) + '\n';
      }
    }
    if (inTable) html += '</table></div>';
    text = html;

    // 3) 行内代码 `x`
    text = text.replace(/`([^`\n]+)`/g, function (m, c) {
      return '<code class="inline-code">' + _.escapeHtml(c) + '</code>';
    });

    // 4) 加粗 **x**
    text = text.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

    // 4.5) 图片 ![alt](url) → <img>
    text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, function (m, alt, url) {
      return '<img class="msg-image" src="' + _.escapeHtml(url) + '" alt="' + _.escapeHtml(alt) + '" title="点击放大">';
    });

    // 5) 还原代码块
    text = text.replace(/\u0000(\d+)\u0000/g, function (m, idx) {
      var c = codes[parseInt(idx, 10)];
      var lang = c.lang ? '<span class="code-lang">' + _.escapeHtml(c.lang) + '</span>' : '';
      return '<div class="code-block">'
        + '<div class="code-block-head">' + lang
        + '<button class="code-copy" type="button">复制</button></div>'
        + '<pre><code>' + _.escapeHtml(c.code) + '</code></pre>'
        + '</div>';
    });

    return text;
  };

  /** 是否含代码块/表格（任意位置，用于决定是否跳过逐字打字直接渲染） */
  _.isMarkdownBlock = function (text) {
    return /```|~~~/.test(text) || /^\s*\|.+\|\s*$/m.test(text);
  };

  CA.markdown = _;
})(window.ChatApp);