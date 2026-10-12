# -*- coding: utf-8 -*-
# K-STOCK-245: 常用工具区 JS——loadToday 判空加固 + 常用增删/localStorage
import re

P = 'portal/index.html'
t = open(P, encoding='utf-8').read()

# ① 裸 getElementById().textContent = X → 判空 set('id', X)
pat = re.compile(r"document\.getElementById\('([^']+)'\)\.textContent\s*=\s*")
n = len(pat.findall(t))
assert n == 23, f"裸调用计数 {n} != 期望 23"  # 实测23处=loadToday+paper/strong/overview等全部加载器，全替换=全站判空加固
t = pat.sub(lambda m: f"set('{m.group(1)}', ", t)

# ② 注入 set 辅助函数（loadToday 定义之前）
anchor = "  // ── 今日要务四域态势（K-STOCK-112）──"
assert t.count(anchor) == 1
helper = ("  // 判空赋值：常用工具卡可被移除，元素可能不存在（K-STOCK-245）\n"
          "  function set(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }\n\n")
t = t.replace(anchor, helper + anchor, 1)

# ③ 追加常用区管理 JS（setInterval(loadToday) 之后）
tail_anchor = "  loadToday();\n  setInterval(loadToday, 120000);"
assert t.count(tail_anchor) == 1
fav_js = """
  // ── 常用工具区：增删 + localStorage 持久化（K-STOCK-245）──
  const FAV_KEY = 'os_favs_v1';
  const FAV_POOL = [
    { href: '/quant', ic: '📈', t: '量化' },
    { href: '/intel', ic: '🕵️', t: '情报' },
    { href: '/creator', ic: '🎬', t: '创作' },
    { href: '/dev.html', ic: '💻', t: '开发' },
    { href: '/screen.html', ic: '🖥️', t: '数据大屏', s: '盘面总览' },
    { href: '/strong.html', ic: '📊', t: '板块强弱', s: '领涨识别' },
    { href: '/paper', ic: '🧾', t: '模拟仓', s: '交易台' },
    { href: '/academy.html', ic: '🎓', t: '智能体学院', s: 'AI 教 AI' },
    { href: '/ops', ic: '⚙️', t: '系统运维', s: 'OS 控制台' },
    { href: '/market.html', ic: '💰', t: '金融驾驶舱', s: '绩效账本' },
    { href: '/picks.html', ic: '🎯', t: '选股中心', s: '每日精选' },
    { href: '/kg/ask', ic: '🕸️', t: '图谱问答', s: '图谱查询' },
    { href: '/audit.html', ic: '🛡️', t: '安全审计', s: '合规检查' },
    { href: '/market_strategy.html', ic: '🧮', t: '策略市场', s: '策略投影' },
  ];
  const FAV_DEFAULT = ['/quant', '/intel', '/creator', '/dev.html'];
  const favBoard = document.getElementById('favBoard');
  const favPanel = document.getElementById('favPanel');
  function favList() {
    try { const v = JSON.parse(localStorage.getItem(FAV_KEY)); if (Array.isArray(v) && v.length) return v; } catch {}
    return FAV_DEFAULT.slice();
  }
  function favSave(l) { try { localStorage.setItem(FAV_KEY, JSON.stringify(l)); } catch {} }
  function favBindX(el, href) {
    const x = document.createElement('div');
    x.className = 'fav-x'; x.title = '移除'; x.textContent = '×';
    x.onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); favSave(favList().filter(v => v !== href)); renderFavs(); };
    el.appendChild(x);
  }
  function favCard(p) {
    const a = document.createElement('a');
    a.className = 'dm fav-dyn'; a.href = p.href;
    a.innerHTML = '<div class="head"><div class="ic">' + p.ic + '</div><div><div class="t">' + p.t + '</div></div></div>'
      + '<div class="foot"><span class="st"><span class="dot unknown"></span><span>' + (p.s || '常用直达') + '</span></span><span class="enter">进入 →</span></div>';
    favBindX(a, p.href);
    return a;
  }
  function renderFavs() {
    const list = favList();
    favBoard.querySelectorAll('.fav-dyn').forEach(el => el.remove());
    FAV_DEFAULT.forEach(h => {
      const el = favBoard.querySelector('a.dm[href="' + h + '"]');
      if (el) {
        el.style.display = list.includes(h) ? '' : 'none';
        const old = el.querySelector('.fav-x'); if (old) old.remove();
        if (list.includes(h)) favBindX(el, h);
      }
    });
    const addCard = document.getElementById('favAdd');
    list.filter(h => !FAV_DEFAULT.includes(h)).forEach(h => {
      const p = FAV_POOL.find(x => x.href === h);
      if (p && addCard) favBoard.insertBefore(favCard(p), addCard);
    });
    renderPanel();
  }
  function renderPanel() {
    const list = favList();
    favPanel.innerHTML = '';
    FAV_POOL.forEach(p => {
      const inFav = list.includes(p.href);
      const c = document.createElement('span');
      c.className = 'fav-chip' + (inFav ? ' in' : '');
      c.innerHTML = '<span class="ic-s">' + p.ic + '</span>' + p.t + (inFav ? ' · 已添加' : '');
      if (!inFav) c.onclick = () => { favSave(favList().concat([p.href])); renderFavs(); };
      favPanel.appendChild(c);
    });
    const tip = document.createElement('span');
    tip.style.cssText = 'font-size:11px;color:var(--gray-6);margin-left:auto;';
    tip.textContent = '点选加入常用区 · 配置存在本机浏览器';
    favPanel.appendChild(tip);
  }
  document.getElementById('favAdd').addEventListener('click', (e) => { e.preventDefault(); favPanel.hidden = !favPanel.hidden; });
  renderFavs();"""
t = t.replace(tail_anchor, tail_anchor + "\n" + fav_js, 1)

open(P, 'w', encoding='utf-8', newline='').write(t)
print(f"OK: set() 替换 {n} 处, helper+fav JS 已注入")
