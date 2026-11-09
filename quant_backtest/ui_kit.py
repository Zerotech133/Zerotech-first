# -*- coding: utf-8 -*-
"""分析台 UI 单一事实源。

问题背景：平台 27 个页面此前各自定义配色，形成两套 token（浅色 A 套 5 页、
深色 B 套 20 页）、7 种内容宽度、6 种 h1 字号。翻页时内容左边缘会横跳、涨跌色
号不一致。本模块把配色、宽度、字号收敛为一份，所有页面从这里取。

用法：
    from ui_kit import root_css, BASE_CSS, W_READ, W_DATA
    <style>:root{...}</style>  →  <style>%s</style> % root_css()

约定（改动请改这里，不要在各页里写死色号）：
    · 涨 = 红、跌 = 绿（中式）
    · 深色主题唯一一套，不提供浅色分支
    · 内容宽度只有两档：W_READ 阅读型 / W_DATA 数据密集型
    · h1 一律 20px，不按页面重要性调整（重要性用域与徽标表达，不靠字号）
"""

# ── 深色主题（唯一一套）─────────────────────────────────────────────
TOKENS = {
    "bg": "#0d1117",    # 页面底色
    "pn": "#131a24",    # 面板
    "pn2": "#0f1620",   # 面板内嵌（比 pn 更暗，用于 code / 表格斑马）
    "bd": "#1f2937",    # 边框、分隔线
    "tx": "#e6edf3",    # 主文字
    "mu": "#7b8698",    # 次要文字、单位、注释
    "up": "#e3554f",    # 涨（红）
    "dn": "#3fa96a",    # 跌（绿）
    "ac": "#5b9bd5",    # 强调、链接、选中态
    "warn": "#e0b95c",  # 警示（黄）
    "bad": "#e8807f",   # 错误、失效（红，弱于涨色以便区分）
}

# ── 内容宽度（只两档）───────────────────────────────────────────────
W_READ = 1180   # 阅读型：工具、模拟、日报、清单
W_DATA = 1440   # 数据密集型：PC 工作台、全量全景表

# ── 字号（px）───────────────────────────────────────────────────────
FS = {
    "h1": 20,
    "h2": 15,
    "h3": 13,
    "body": 13,
    "small": 11.5,
    "micro": 11,   # 下限，再小在 1440 屏上读不清
}

# 字体栈统一：先系统拉丁，再中文。此前各页 PingFang SC 与 Microsoft YaHei 顺序不一。
FONT = ('-apple-system, "Segoe UI", system-ui, "PingFang SC", '
        '"Microsoft YaHei", "Hiragino Sans GB", sans-serif')
MONO = '"SF Mono", "Cascadia Mono", Consolas, "Liberation Mono", monospace'


def root_css():
    """返回 `:root{...}` 变量块 + 基础重置。各页塞进 <style> 最前面。"""
    v = ";".join("--%s:%s" % (k, val) for k, val in TOKENS.items())
    return (
        ":root{%s;--w-read:%dpx;--w-data:%dpx;"
        "--fh1:%dpx;--fh2:%dpx;--fbody:%dpx;--fsm:%spx}"
        "*{box-sizing:border-box}"
        "html,body{margin:0;padding:0}"
        "body{background:var(--bg);color:var(--tx);font-family:%s;"
        "font-size:var(--fbody);line-height:1.6;"
        "-webkit-font-smoothing:antialiased}"
        "a{color:var(--ac);text-decoration:none}"
        "a:hover{text-decoration:underline}"
        "h1{font-size:var(--fh1);font-weight:650;margin:0 0 6px;"
        "letter-spacing:.2px;line-height:1.35}"
        "h2{font-size:var(--fh2);font-weight:620;margin:28px 0 10px;"
        "display:flex;align-items:baseline;gap:8px}"
        "h3{font-size:var(--fh3);font-weight:600;color:#b9c4d2;margin:16px 0 8px}"
        "table{width:100%%;border-collapse:collapse;font-size:var(--fbody)}"
        "th{text-align:left;color:var(--mu);font-weight:500;padding:7px 8px;"
        "border-bottom:1px solid var(--bd);font-size:var(--fsm);white-space:nowrap}"
        "td{padding:7px 8px;border-bottom:1px solid var(--bd);"
        "vertical-align:middle}"
        "code,kbd{font-family:%s;font-size:var(--fsm);"
        "background:var(--pn2);padding:1px 5px;border-radius:4px}"
        "::selection{background:#1f3a56}"
        % (v, W_READ, W_DATA, FS["h1"], FS["h2"], FS["body"], FS["small"],
           FONT, MONO)
    )


# ── 通用组件（各页可选用，避免重复实现）──────────────────────────────
BASE_CSS = """
.wrap{max-width:var(--w-read);margin:0 auto;padding:18px 22px 56px}
.wrap-data{max-width:var(--w-data)}
.panel{background:var(--pn);border:1px solid var(--bd);border-radius:10px;
 padding:13px 14px}
.panel+.panel{margin-top:12px}
.lead{color:var(--mu);font-size:var(--fsm);line-height:1.8;margin:0 0 16px}
.lead b{color:var(--tx);font-weight:600}
.mu{color:var(--mu)}
.num{font-variant-numeric:tabular-nums}
.up{color:var(--up)}
.dn{color:var(--dn)}
.tag{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:9px;
 background:var(--pn2);color:var(--mu);border:1px solid var(--bd);
 white-space:nowrap;line-height:1.5}
.tag.ok{background:#14261b;color:#7fd18b;border-color:#22452e}
.tag.warn{background:#2a2312;color:var(--warn);border-color:#4a3d1c}
.tag.bad{background:#2b1618;color:var(--bad);border-color:#4d2528}
.grid{display:grid;gap:12px}
.grid2{grid-template-columns:repeat(2,minmax(0,1fr))}
.grid3{grid-template-columns:repeat(3,minmax(0,1fr))}
.grid4{grid-template-columns:repeat(4,minmax(0,1fr))}
.eq{font-variant-numeric:tabular-nums}
.scroll-x{overflow-x:auto;-webkit-overflow-scrolling:touch}
@media (max-width:900px){
 .grid2,.grid3,.grid4{grid-template-columns:minmax(0,1fr)}
 .wrap,.wrap-data{padding:14px 14px 40px}
 h1{font-size:18px}
}
"""


def page(title, body, extra_css="", wrap_cls="wrap", lang="zh-CN"):
    """整页壳。给新页面用；老页面各自有壳，只需取 root_css()。"""
    return (
        '<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>%s</title><style>%s%s%s</style></head><body>"
        '<div class="%s">%s</div></body></html>'
        % (lang, title, root_css(), BASE_CSS, extra_css, wrap_cls, body)
    )


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print("token 项数", len(TOKENS))
    print("宽度档位 W_READ=%d W_DATA=%d" % (W_READ, W_DATA))
    print("h1/h2/body/小字 = %d/%d/%d/%s"
          % (FS["h1"], FS["h2"], FS["body"], FS["small"]))
    print("root_css 长度", len(root_css()), "BASE_CSS 长度", len(BASE_CSS))
