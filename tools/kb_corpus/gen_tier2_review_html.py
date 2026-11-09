#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_tier2_review_html.py — 生成「全部文案 20261108」tier2 抽检人审清单 HTML。

输入:
    30_知识单元/视频号学习_全部文案_20261108_units_draft.jsonl   (claim/unit_class/status)
    30_知识单元/视频号学习_全部文案_20261108_unit_blogger_map.jsonl (博主归属)
    30_知识单元/tier2_sample_unit_ids.txt                        (抽检名单)
输出:
    deliverables/2026-11-08_全部文案85博主_tier2抽检580人审清单.html

交互复刻北斗 108 条清单：pass/revise/reject 三键、localStorage 进度、
导出 [{unit_id,action}] JSON 供 learning_status_flow review --from-map。
新增：博主筛选 + 类徽章 + 未归属标记。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

KB = Path(r"C:/项目/知识库")
UNIT_DIR = KB / "30_知识单元"
DELIV = Path(r"C:/项目/老田的AI助手/deliverables")
PID = "视频号学习_全部文案_20261108"


def main() -> int:
    ids = []
    for ln in open(UNIT_DIR / "tier2_sample_unit_ids.txt", encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            ids.append(ln)
    id_set = set(ids)

    bloggers = {}
    for ln in open(UNIT_DIR / f"{PID}_unit_blogger_map.jsonl", encoding="utf-8"):
        r = json.loads(ln)
        if r["unit_id"] in id_set:
            bloggers[r["unit_id"]] = (r["blogger_name"] or "（未归属）", r["match"])

    units = []
    for ln in open(UNIT_DIR / f"{PID}_units_draft.jsonl", encoding="utf-8"):
        r = json.loads(ln)
        if r["unit_id"] in id_set:
            b, m = bloggers.get(r["unit_id"], ("（未归属）", "no_match"))
            units.append({
                "unit_id": r["unit_id"],
                "blogger": b,
                "match": m,
                "cls": r.get("unit_class", ""),
                "claim": r.get("claim", ""),
                "video_key": r.get("video_key", ""),
            })
    # 名单序去重（同 id 孪生行只出一条，判定导出后两行同迁）
    seen = set()
    uniq = []
    for u in units:
        if u["unit_id"] in seen:
            continue
        seen.add(u["unit_id"])
        uniq.append(u)
    units = uniq

    n_blogger = len({u["blogger"] for u in units})
    cls_dist = Counter(u["cls"] for u in units)
    match_dist = Counter(u["match"] for u in units)
    print(f"清单单元: {len(units)}  博主: {n_blogger}  类: {dict(cls_dist)}  匹配: {dict(match_dist)}")

    data_json = json.dumps(units, ensure_ascii=False, separators=(",", ":"))
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"全部文案85博主 · tier2 抽检人审清单（{len(units)} 条）"

    html = HTML.replace("__TITLE__", title).replace("__DATA__", data_json) \
               .replace("__N__", str(len(units))).replace("__DATE__", today)
    out = DELIV / f"2026-11-08_全部文案85博主_tier2抽检{len(units)}人审清单.html"
    out.write_text(html, encoding="utf-8")
    print("已生成:", out)
    return 0


HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>__TITLE__</title>
<style>
:root{--bg:#fafafa;--card:#fff;--ink:#222;--mut:#777;--line:#e3e3e3;
--pass:#1a7f37;--rev:#b58105;--rej:#c62828;--u:#6a5acd;--t:#0b7285;--m:#c2255c}
*{box-sizing:border-box}
body{font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:20px;max-width:980px;margin-inline:auto}
h1{font-size:20px;margin:.2em 0}
.sub{color:var(--mut);margin-bottom:12px}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:14px;position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:5}
select,input[type=search]{padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:10px}
.meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:6px;color:var(--mut);font-size:12px}
.badge{padding:1px 8px;border-radius:10px;color:#fff;font-size:11px}
.b-U{background:var(--u)}.b-T{background:var(--t)}.b-M{background:var(--m)}
.claim{white-space:pre-wrap;word-break:break-word;margin:6px 0 10px}
.btns{display:flex;gap:8px}
.btns button{flex:1;padding:7px 0;border:1px solid var(--line);border-radius:8px;background:#fff;cursor:pointer;font-size:13px}
.btns button.pass.on{background:var(--pass);color:#fff;border-color:var(--pass)}
.btns button.revise.on{background:var(--rev);color:#fff;border-color:var(--rev)}
.btns button.reject.on{background:var(--rej);color:#fff;border-color:var(--rej)}
progress{width:160px;height:8px}
.hide{display:none}
.match-multi{color:var(--rev);font-size:11px}
</style></head><body>
<h1>__TITLE__</h1>
<div class="sub">判定三选一：通过 pass / 修改 revise / 拒绝 reject。进度自动存浏览器，完成后点「导出结论 JSON」，交回执行 review --from-map 落状态。__DATE__</div>
<div class="bar">
 进度：<b id="done">0</b>/__N__ <progress id="pg" max="__N__" value="0"></progress>
 <select id="fb"><option value="">全部博主</option></select>
 <select id="fc"><option value="">全部类</option><option>T</option><option>U</option><option>M</option></select>
 <select id="fs"><option value="">全部</option><option value="0">未判</option><option value="1">已判</option></select>
 <input type="search" id="q" placeholder="搜文案关键词">
 <button onclick="exportJson()">导出结论 JSON</button>
 <button onclick="if(confirm('清空本机判定记录？')){localStorage.removeItem(KEY);location.reload()}">重置</button>
</div>
<div id="list"></div>
<script>
const DATA=__DATA__;
const KEY='tier2_review_20261108';
const verdict=JSON.parse(localStorage.getItem(KEY)||'{}');
const bl=[...new Set(DATA.map(u=>u.blogger))].sort();
const fb=document.getElementById('fb');
bl.forEach(b=>{const o=document.createElement('option');o.value=b;o.textContent=b;fb.appendChild(o)});
function save(){localStorage.setItem(KEY,JSON.stringify(verdict))}
function paint(){
 const fbv=fb.value,fcv=fc.value,fsv=fs.value,q=document.getElementById('q').value.trim();
 const list=document.getElementById('list');list.innerHTML='';
 let shown=0;
 for(const u of DATA){
  const done=!!verdict[u.unit_id];
  if(fbv&&u.blogger!==fbv)continue;
  if(fcv&&u.cls!==fcv)continue;
  if(fsv==='0'&&done)continue;
  if(fsv==='1'&&!done)continue;
  if(q&&!(u.claim.includes(q)||u.blogger.includes(q)))continue;
  shown++;
  const d=document.createElement('div');d.className='card';
  const mm=u.match==='multi'?'<span class="match-multi">⧉同文多博主归属</span>':(u.match==='no_match'?'<span class="match-multi">?未归属</span>':'');
  d.innerHTML=`<div class="meta"><span class="badge b-${u.cls}">${u.cls}</span><b>${u.blogger}</b><span>${u.video_key}</span>${mm}<span style="margin-left:auto;color:#bbb">${u.unit_id.slice(-10)}</span></div>
  <div class="claim"></div>
  <div class="btns">
   <button class="pass" data-a="pass">通过</button>
   <button class="revise" data-a="revise">修改</button>
   <button class="reject" data-a="reject">拒绝</button>
  </div>`;
  d.querySelector('.claim').textContent=u.claim;
  d.querySelectorAll('button[data-a]').forEach(b=>{
   if(verdict[u.unit_id]===b.dataset.a)b.classList.add('on');
   b.onclick=()=>{verdict[u.unit_id]=b.dataset.a;save();paint()};
  });
  list.appendChild(d);
 }
 const n=Object.keys(verdict).length;
 document.getElementById('done').textContent=n;
 document.getElementById('pg').value=n;
 if(!shown){list.innerHTML='<div class="sub">（无匹配项）</div>'}
}
function exportJson(){
 const n=Object.keys(verdict).length;
 if(n<__N__ && !confirm(`还有 ${__N__-n} 条未判，仍导出？`))return;
 const out=Object.entries(verdict).map(([unit_id,action])=>({unit_id,action}));
 const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);
 a.download='tier2_review_verdicts.json';a.click();
}
['fb','fc','fs','q'].forEach(id=>document.getElementById(id).addEventListener('input',paint));
paint();
</script></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
