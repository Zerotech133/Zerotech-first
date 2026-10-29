/* ui_palette.js — Week3 命令面板原型（Ctrl/Cmd+K）
   约束：只做导航/滚动/视图切换/密度切换，不执行危险动作；不写 storage。 */
(function () {
  'use strict';
  if (window.__wbPalette) return;
  window.__wbPalette = 1;

  function click(sel) {
    var el = document.querySelector(sel);
    if (el && typeof el.click === 'function') el.click();
  }
  function go(href) { window.location.href = href; }
  function setDensity(mode) {
    document.documentElement.setAttribute('data-wb-density', mode);
  }
  function toggleDensity() {
    var cur = document.documentElement.getAttribute('data-wb-density') || 'comfortable';
    setDensity(cur === 'compact' ? 'comfortable' : 'compact');
  }

  var COMMANDS = [
    { label: '今日建议', hint: '视图', run: function () { click('[data-view="ai"]'); } },
    { label: '工作平台', hint: '跳转', run: function () { click('[data-jump="work"]'); } },
    { label: '今日结果', hint: '跳转', run: function () { click('[data-jump="results"]'); } },
    { label: '指挥大屏', hint: '视图', run: function () { click('[data-view="screen"]'); } },
    { label: '刷新来源', hint: '动作', run: function () { click('#refresh'); } },
    { label: '切换紧凑密度', hint: '显示', run: function () { setDensity('compact'); } },
    { label: '切换舒适密度', hint: '显示', run: function () { setDensity('comfortable'); } },
    { label: '打开个人 OS 入口', hint: '导航', run: function () { go('/os-entry'); } },
    { label: '打开指挥大屏', hint: '导航', run: function () { go('/os-board'); } },
    { label: '打开研发', hint: '导航', run: function () { go('/dev'); } },
    { label: '打开情报', hint: '导航', run: function () { go('/intel'); } },
    { label: '打开军团', hint: '导航', run: function () { go('/legion'); } }
  ];

  // W1：把 nav.js 暴露的 89 个入口全部加入命令面板（页面名/域分组）。
  (function () {
    try {
      var nav = window.__WB_NAV;
      if (!nav || !nav.sections || !nav.groups) return;
      nav.sections.forEach(function (sec) {
        (nav.groups[sec[0]] || []).forEach(function (g) {
          (g[1] || []).forEach(function (it) {
            COMMANDS.push({
              label: it[0],
              hint: '导航 · ' + (sec[1] || ''),
              run: function () { go(it[1]); }
            });
          });
        });
      });
    } catch (e) { /* 无 nav 数据时保持原命令 */ }
  })();

  function build() {
    var overlay = document.createElement('div');
    overlay.className = 'wb-palette';
    overlay.hidden = true;

    var box = document.createElement('div');
    box.className = 'wb-palette-box';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', '命令面板');

    var input = document.createElement('input');
    input.className = 'wb-palette-input';
    input.type = 'search';
    input.placeholder = '输入命令或页面名…';
    input.setAttribute('aria-label', '搜索命令');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');

    var list = document.createElement('div');
    list.className = 'wb-palette-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', '命令列表');

    var hint = document.createElement('div');
    hint.className = 'wb-palette-hint';
    hint.textContent = '↑↓ 选择 · Enter 执行 · Esc 关闭';

    box.appendChild(input);
    box.appendChild(list);
    box.appendChild(hint);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    var active = 0;
    var shown = [];
    var lastFocus = null;

    function render(q) {
      q = (q || '').toLowerCase().trim();
      shown = COMMANDS.filter(function (c) {
        return !q || c.label.toLowerCase().indexOf(q) >= 0 ||
          (c.hint || '').toLowerCase().indexOf(q) >= 0;
      });
      list.textContent = '';
      shown.forEach(function (c, i) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'wb-palette-item';
        b.setAttribute('role', 'option');
        b.setAttribute('aria-selected', i === active ? 'true' : 'false');
        var label = document.createElement('span');
        label.textContent = c.label;
        var h = document.createElement('small');
        h.textContent = c.hint || '';
        b.appendChild(label);
        b.appendChild(h);
        b.addEventListener('click', function () { run(i); });
        list.appendChild(b);
      });
      if (active >= shown.length) active = Math.max(0, shown.length - 1);
      input.setAttribute('aria-activedescendant', shown.length ? 'wb-palette-op-' + active : '');
      if (shown.length) {
        list.children[active].id = 'wb-palette-op-' + active;
        list.children[active].setAttribute('aria-selected', 'true');
      }
    }

    function run(i) {
      var c = shown[i];
      if (!c) return;
      close();
      c.run();
    }

    function open() {
      lastFocus = document.activeElement;
      overlay.hidden = false;
      active = 0;
      input.value = '';
      render('');
      input.focus();
    }
    function close() {
      overlay.hidden = true;
      if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    }

    input.addEventListener('input', function () { active = 0; render(input.value); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        active = Math.min(shown.length - 1, active + 1);
        render(input.value);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        active = Math.max(0, active - 1);
        render(input.value);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        run(active);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        close();
      }
    });
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close();
    });
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 'k') {
        e.preventDefault();
        if (overlay.hidden) open(); else close();
      } else if (e.key === 'Escape' && !overlay.hidden) {
        close();
      }
    });

    var trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'wb-palette-trigger';
    trigger.textContent = '⌘K 命令';
    trigger.setAttribute('aria-label', '打开命令面板');
    trigger.addEventListener('click', open);
    document.body.appendChild(trigger);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
