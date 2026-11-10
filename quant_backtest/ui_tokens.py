# -*- coding: utf-8 -*-
"""ui_tokens.py —— 全站统一设计 token（2026-11-10 UI 重调）

老田三项裁定：① 核心五页优先；② 全站统一暗色；③ 死链直接删除。
本文件是暗色主题的**单一事实源**：门面、选股工作台 v2、结构日报、
板块平台等生成器从这里取色，杜绝此前「每页各写各的 CSS」导致的
两套主题并存、红绿色值混用（#c0392b/#e8807f/#ff5252 混用，判例 UI-2）。

配色语义（A 股口径，2026-11-09 已确认长期有效）：
  涨/买入 → 红；跌/规避 → 绿。

九态档位色板（红=主买档优先级递减 → 蓝=中性 → 绿=规避）：
  三买（主档·红）> 三买待（橙）> 二买（琥珀）> 一买（黄）
  > 离开段（青）> 中枢（蓝灰）> 背驰（紫·预警）> 中枢下（灰）> 三卖（绿·规避）

用法（生成器内）：
  import ui_tokens as UT
  html = CSS_TMPL % dict(css=UT.BASE_CSS, tier_json=json.dumps(UT.TIER_COLORS))
"""

# ---------------------------------------------------------------- 基础色

BG = "#0c1117"        # 页面底
PANEL = "#121924"     # 卡片/面板
PANEL2 = "#182130"    # 悬停/次级面板
LINE = "#1f2835"      # 描边
LINE2 = "#2a3546"     # 强调描边
TX = "#e6edf3"        # 主文字
TX2 = "#9aa7b8"       # 次级文字
TX3 = "#6b7787"       # 弱文字（时间戳、来源）
ACC = "#4f8fe6"       # 主强调（链接、选中）
UP = "#e35d5d"        # 涨（红）
DN = "#3aa76d"        # 跌（绿）
FLAT = "#8a93a3"      # 平盘
WARN = "#e0b95c"      # 警示（陈旧度等）
OK = "#57b26a"        # 正常/最新

# ---------------------------------------------------------------- 九态档位色

TIER_COLORS = {
    "三买":   "#e35d5d",
    "三买待": "#e0874f",
    "二买":   "#d9a13b",
    "一买":   "#c9b23f",
    "离开段": "#4fa8a0",
    "中枢":   "#6f87a8",
    "背驰":   "#b07fd8",
    "中枢下": "#8090a4",
    "三卖":   "#3aa76d",
}
TIER_JSON = "__TIER_COLORS_JSON__"  # 生成器 replace 注入用占位


def tier_color(name, dflt=ACC):
    return TIER_COLORS.get(name, dflt)


# ---------------------------------------------------------------- 共享 CSS

FONT = ('"Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif')

CSS_VARS = (":root{--bg:%s;--panel:%s;--panel2:%s;--line:%s;--line2:%s;"
             "--tx:%s;--tx2:%s;--tx3:%s;--acc:%s;--up:%s;--dn:%s;--flat:%s;"
             "--warn:%s;--ok:%s}"
             % (BG, PANEL, PANEL2, LINE, LINE2, TX, TX2, TX3, ACC,
                UP, DN, FLAT, WARN, OK))

# 共享基础：变量 + 排版 + 卡片/表格/徽标/chip 组件。生成器把它嵌进 <head>，
# 页面自己的 CSS 只写布局，不再自带色值。
BASE_CSS = CSS_VARS + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
 font:14px/1.6 %(font)s;-webkit-font-smoothing:antialiased}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:20px;font-weight:600;letter-spacing:.2px;margin:0}
h2{font-size:16px;font-weight:600;margin:0}
.pf-card{background:var(--panel);border:1px solid var(--line);
 border-radius:10px;padding:14px 16px;transition:border-color .14s,
 background .14s,box-shadow .14s}
.pf-card:hover{background:var(--panel2);border-color:var(--line2);
 box-shadow:0 4px 14px rgba(0,0,0,.35)}
.pf-table{width:100%%;border-collapse:collapse;background:var(--panel);
 border:1px solid var(--line);border-radius:10px;overflow:hidden}
.pf-table th{background:var(--panel2);text-align:left;padding:8px 12px;
 font-size:12.5px;font-weight:600;color:var(--tx2);white-space:nowrap;
 border-bottom:1px solid var(--line)}
.pf-table td{padding:8px 12px;border-top:1px solid var(--line);
 font-size:13px;white-space:nowrap}
.pf-table tr.r{cursor:pointer}
.pf-table tr.r:hover{background:var(--panel2)}
.pf-up{color:var(--up)}.pf-dn{color:var(--dn)}.pf-flat{color:var(--flat)}
.pf-chip{display:inline-block;font-size:12px;border-radius:5px;
 padding:2px 9px;background:var(--panel2);color:var(--tx2);
 border:1px solid var(--line)}
.pf-badge{font-size:11.5px;padding:2px 8px;border-radius:10px;
 background:var(--panel2);color:var(--tx2);border:1px solid var(--line)}
.pf-badge.ok{background:rgba(87,178,106,.12);color:var(--ok);
 border-color:rgba(87,178,106,.3)}
.pf-badge.warn{background:rgba(224,185,92,.12);color:var(--warn);
 border-color:rgba(224,185,92,.3)}
.pf-badge.old{background:rgba(227,93,93,.12);color:var(--up);
 border-color:rgba(227,93,93,.3)}
.pf-dim{color:var(--tx3);font-size:12px}
""" % dict(font=FONT)

__all__ = ["CSS_VARS", "BASE_CSS", "TIER_COLORS", "TIER_JSON",
           "tier_color", "FONT"]
