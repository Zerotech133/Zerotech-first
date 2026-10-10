# -*- coding: utf-8 -*-
"""前端入口统一治理（K-STOCK-100）· 第一件：导航单一源导出器。

用 AST 从 portal_api.py 提取 DOMAIN_NAV_GROUPS 与 SECTIONS 两个字面量，
生成 portal/static/nav.js（统一顶栏渲染器，数据内嵌）。

为什么 AST 不 import：portal_api.py 是 2.3 万行巨石且 import 有副作用
（freshness 预热线程）；AST 只取字面量，零副作用、零依赖。

用法：python tools/export_nav.py   （幂等，每次 pill_nav 数据变更后重跑）
"""
import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "portal", "portal_api.py")
OUT = os.path.join(REPO, "portal", "static", "nav.js")


def _extract_literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise SystemExit("portal_api.py 顶层找不到 %s" % name)


def main():
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    groups = _extract_literal(tree, "DOMAIN_NAV_GROUPS")
    sections = _extract_literal(tree, "SECTIONS")
    # SECTIONS: ((key,label,href),...) → 顺序保留
    data = {
        "sections": [[k, lb, h] for k, lb, h in sections],
        "groups": {k: [[glb, [[lb, href, key]
                               for lb, href, key in its]]
                       for glb, its in its_groups]
                   for k, its_groups in groups.items()},
    }
    js = (
        "/* 自动生成：tools/export_nav.py · 源=portal_api.py DOMAIN_NAV_GROUPS\n"
        "   勿手改本文件——改 portal_api.py 导航数据后重跑导出器。*/\n"
        "(function(){\n"
        "var NAV=" + json.dumps(data, ensure_ascii=False) + ";\n"
        "var PILL='display:inline-block;padding:6px 13px;"
        "border:1px solid #30363d;border-radius:999px;color:#8b949e;"
        "font-size:12.5px;text-decoration:none;background:#0d1117';\n"
        "var PILL_ON=PILL.slice(0,-1)+';color:#e6edf3;"
        "border-color:#58a6ff;font-weight:600';\n"
        "function grp(label,items,cur){\n"
        " var h='';\n"
        " if(label)h+='<div style=\"color:#8b949e;font-size:10px;"
        "letter-spacing:1px;margin:5px 0 3px\">'+label+'</div>';\n"
        " h+='<div style=\"display:flex;flex-wrap:wrap;gap:4px 14px;"
        "font-size:12.5px;line-height:2\">';\n"
        " items.forEach(function(it){\n"
        "  var on=cur&&it[2]===cur;\n"
        "  h+=on?'<span style=\"color:#e6edf3;font-weight:600\">'+it[0]"
        "+'</span>':'<a href=\"'+it[1]+'\" style=\"color:#58a6ff;"
        "text-decoration:none\">'+it[0]+'</a>';\n"
        " });\n"
        " return h+'</div>';\n"
        "}\n"
        "document.addEventListener('DOMContentLoaded',function(){\n"
        " var path=location.pathname.replace(/\\.html$/,'');\n"
        " var cur=null,sec=NAV.sections[0][0];\n"
        " NAV.sections.forEach(function(s){\n"
        "  (NAV.groups[s[0]]||[]).forEach(function(g){\n"
        "   g[1].forEach(function(it){\n"
        "    var p=it[1].replace('http://127.0.0.1','');\n"
        "    if(p===path||p+'/'===path+'/'||(p!=='/'&&path.indexOf(p)===0"
        "&&it[1].charAt(0)==='/')){cur=it[2];sec=s[0];}\n"
        "   });\n"
        "  });\n"
        " });\n"
        " var bar=document.createElement('div');\n"
        " bar.style.cssText='position:sticky;top:0;z-index:99;"
        "display:flex;align-items:center;gap:7px;flex-wrap:wrap;"
        "padding:9px 14px;background:rgba(13,17,23,.92);"
        "border-bottom:1px solid #30363d;backdrop-filter:blur(6px)';\n"
        " var FLOW=[['🤖 晨报','/bt-brief'],['🌅 盘前','/premarket'],"
        "['🚦 信号','/bt-signals'],['📺 大屏','/screen'],"
        "['🌇 盘后','/afterhours'],['🧾 模拟仓','/paper']];\n"
        " var flow='<span style=\"color:#d29922;font-size:11px;"
        "letter-spacing:1px;font-weight:600\">⚡ 动线</span>';\n"
        " FLOW.forEach(function(f){\n"
        "  var on=path===f[1];\n"
        "  flow+='<a href=\"'+f[1]+'\" style=\"'+(on?"
        "PILL_ON.replace('#58a6ff','#d29922'):PILL)+'\">'+f[0]+'</a>';\n"
        " });\n"
        " bar.innerHTML=flow+'<span style=\"color:#30363d;"
        "margin:0 4px\">|</span><a href=\"/portal\" style=\"'+PILL+"
        "'\">🏛 门户</a>';\n"
        " NAV.sections.forEach(function(s){\n"
        "  var on=cur&&sec===s[0];\n"
        "  var dd='<details style=\"position:relative;"
        "display:inline-block\"><summary style=\"'+(on?PILL_ON:PILL)+"
        "'\";cursor:pointer;list-style:none\">'+s[1]+"
        "'<span style=\"font-size:9px\"> ▾</span></summary>"
        "<div style=\"position:absolute;left:0;top:calc(100% + 6px);"
        "z-index:100;background:#161b22;border:1px solid #30363d;"
        "border-radius:10px;padding:8px 14px 10px;min-width:320px;"
        "box-shadow:0 10px 28px rgba(0,0,0,.45)\">'+"
        "(NAV.groups[s[0]]||[]).map(function(g){"
        "return grp(g[0],g[1],cur);}).join('')+'</div></details>';\n"
        "  bar.innerHTML+=dd;\n"
        " });\n"
        " document.body.insertBefore(bar,document.body.firstChild);\n"
        "});\n"
        "})();\n")
    open(OUT, "w", encoding="utf-8").write(js)
    n_items = sum(len(g[1]) for v in data["groups"].values() for g in v)
    print("nav.js 导出 OK：%d 域 / %d 入口 → %s"
          % (len(data["sections"]), n_items, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
