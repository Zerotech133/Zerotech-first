# -*- coding: utf-8 -*-
"""前端入口统一治理（K-STOCK-100）· 第一件：导航单一源导出器。

用 AST 从 api_common.py 提取 DOMAIN_NAV_GROUPS 与 SECTIONS 两个字面量，
生成 portal/static/nav.js（统一顶栏渲染器，数据内嵌）。

为什么 AST 不 import：portal_api.py 是 2.3 万行巨石且 import 有副作用
（freshness 预热线程）；AST 只取字面量，零副作用、零依赖。

2026-10-27 UI/UX Week1：nav.js 不再内联硬编码 hex/style，改为 class 标记 +
portal/static/nav.css（tokens var + 安全 fallback）；视觉值与原实现等价。

用法：python tools/export_nav.py   （幂等，每次 pill_nav 数据变更后重跑）
"""
import ast
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "portal", "api_common.py")
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
        "/* 自动生成：tools/export_nav.py · 源=api_common.py DOMAIN_NAV_GROUPS\n"
        "   勿手改本文件——改 api_common.py 导航数据后重跑导出器。*/\n"
        "(function(){\n"
        "/* 幂等护栏（2026-10-19 审计加固）：页面双引入 <script src=nav.js> 时\n"
        "   （f7170225 实录缺陷：两个顶栏叠加），第二个 IIFE 直接返回。\n"
        "   标志落 documentElement 属性而非 var——跨 IIFE 实例必须可见。 */\n"
        "if(document.documentElement.getAttribute('data-wb-nav')==='1')return;\n"
        "document.documentElement.setAttribute('data-wb-nav','1');\n"
        "var NAV=" + json.dumps(data, ensure_ascii=False) + ";\n"
        "window.__WB_NAV=NAV;\n"
        "var CSS='/static/nav.css';\n"
        "if(!document.querySelector('link[data-wb-nav-css]')){\n"
        " var lk=document.createElement('link');lk.rel='stylesheet';"
        "lk.href=CSS;lk.setAttribute('data-wb-nav-css','1');"
        "document.head.appendChild(lk);\n"
        "}\n"
        "function grp(label,items,cur){\n"
        " var h='';\n"
        " if(label)h+='<div class=\"wb-nav-group-label\">'+label+'</div>';\n"
        " h+='<div class=\"wb-nav-group-links\">';\n"
        " items.forEach(function(it){\n"
        "  var on=cur&&it[2]===cur;\n"
        "  h+=on?'<span class=\"wb-nav-current\">'+it[0]+'</span>':"
        "(it[1].charAt(0)==='#'?'<span class=\"wb-nav-off\" title=\"未启用\">'+it[0]+'</span>':"
        "'<a class=\"wb-nav-link\" href=\"'+it[1]+'\">'+it[0]+'</a>');\n"
        " });\n"
        " return h+'</div>';\n"
        "}\n"
        "document.addEventListener('DOMContentLoaded',function(){\n"
        " var path=location.pathname.replace(/\\.html$/,'');\n"
        " var cur=null,sec=NAV.sections[0][0];\n"
        " NAV.sections.forEach(function(s){\n"
        "  (NAV.groups[s[0]]||[]).forEach(function(g){\n"
        "   g[1].forEach(function(it){\n"
        "    var p=it[1].replace('http://127.0.0.1','').replace(/\\.html$/,'');\n"
        "    if(p===path||p+'/'===path+'/'||(p!=='/'&&path.indexOf(p)===0"
        "&&it[1].charAt(0)==='/')){cur=it[2];sec=s[0];}\n"
        "   });\n"
        "  });\n"
        " });\n"
        " var bar=document.createElement('div');bar.className='wb-nav';\n"
        " var todayOn=(path==='/'||path==='/home_v3.html');\n"
        " bar.innerHTML='<a class=\"wb-nav-pill wb-nav-today'+(todayOn?' is-active':'')"
        "+'\" href=\"/\">🏠 今日</a>';\n"
        " NAV.sections.forEach(function(s){\n"
        "  var on=cur&&sec===s[0];\n"
        "  var dd='<details class=\"wb-nav-details\">"
        "<summary class=\"wb-nav-pill'+(on?' is-active':'')+'\">'+s[1]"
        "+'<span class=\"wb-nav-caret\"> ▾</span></summary>"
        "<div class=\"wb-nav-dropdown\">'+(NAV.groups[s[0]]||[]).map("
        "function(g){return grp(g[0],g[1],cur);}).join('')"
        "+'</div></details>';\n"
        "  bar.innerHTML+=dd;\n"
        " });\n"
        " if(path!=='/'&&path!=='/index.html'){\n"
        "  var backBtn=document.createElement('a');\n"
        "  backBtn.href='javascript:void(0)';\n"
        "  backBtn.title='返回上一页';\n"
        "  backBtn.className='wb-nav-back';\n"
        "  backBtn.textContent='← 返回';\n"
        "  backBtn.addEventListener('click',function(){\n"
        "   if(history.length>1){history.back();}else{location.href='/';}});\n"
        "  bar.insertBefore(backBtn,bar.firstChild);\n"
        "  var sep0=document.createElement('span');\n"
        "  sep0.className='wb-nav-sep';\n"
        "  sep0.textContent='|';\n"
        "  bar.insertBefore(sep0,bar.firstChild);\n"
        " }\n"
        " var skip=document.querySelector('.wb-skip');\n"
        " if(skip&&skip.parentNode===document.body){"
        "document.body.insertBefore(bar,skip.nextSibling);}\n"
        " else{document.body.insertBefore(bar,document.body.firstChild);}\n"
        " if(!document.querySelector('link[data-wb-palette-css]')){\n"
        "  var pl=document.createElement('link');pl.rel='stylesheet';"
        "pl.href='/static/ui_palette.css';pl.setAttribute('data-wb-palette-css','1');"
        "document.head.appendChild(pl);\n"
        " }\n"
        " if(!document.querySelector('script[data-wb-palette]')){\n"
        "  var ps=document.createElement('script');ps.src='/static/ui_palette.js';"
        "ps.defer=true;ps.setAttribute('data-wb-palette','1');"
        "document.head.appendChild(ps);\n"
        " }\n"
        " if(!document.querySelector('link[data-wb-shell-css]')){\n"
        "  var sl=document.createElement('link');sl.rel='stylesheet';"
        "sl.href='/static/page_shell.css';sl.setAttribute('data-wb-shell-css','1');"
        "document.head.appendChild(sl);\n"
        " }\n"
        " if(!document.querySelector('script[data-wb-shell]')){\n"
        "  var ss=document.createElement('script');ss.src='/static/page_shell.js';"
        "ss.defer=true;ss.setAttribute('data-wb-shell','1');"
        "document.head.appendChild(ss);\n"
        " }\n"
        "});\n"
        "})();\n")
    open(OUT, "w", encoding="utf-8").write(js)
    n_items = sum(len(g[1]) for v in data["groups"].values() for g in v)
    print("nav.js 导出 OK：%d 域 / %d 入口 → %s"
          % (len(data["sections"]), n_items, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
