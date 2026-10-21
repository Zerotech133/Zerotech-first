#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 registry.json 渲染成可离线浏览的静态档案页。

输出：档案总览.html（单文件，数据内嵌，无外部依赖、无网络请求）
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
OUT = BASE / "档案总览.html"

PRIORITY_LABEL = {"P0": "P0 优先接入", "P1": "P1 核心", "P2": "P2 因子候选", "P3": "P3 背景"}
ACCESS_LABEL = {"open": "免费 OPEN", "trial": "高校试用", "direct_sale": "需采购", "api": "需 API Key", "unknown": "未知"}

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CnOpenData 数据源档案 · 股票与金融</title>
<style>
  :root{
    --bg:#f6f7f9; --panel:#ffffff; --line:#e3e6eb; --text:#1b2430; --muted:#67707d;
    --accent:#0f6b6b; --accent-soft:#e6f2f2; --p0:#b3261e; --p1:#0f6b6b; --p2:#6b5b0f; --p3:#67707d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{background:var(--panel);border-bottom:1px solid var(--line);padding:22px 28px}
  h1{margin:0 0 6px;font-size:20px;letter-spacing:.3px}
  .sub{color:var(--muted);font-size:13px}
  .wrap{padding:20px 28px 60px;max-width:1500px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .card b{display:block;font-size:22px;line-height:1.2}
  .card span{color:var(--muted);font-size:12px}
  .bar{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;
    display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:16px;position:sticky;top:0;z-index:5}
  input[type=search],select{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--text)}
  input[type=search]{min-width:280px;flex:1}
  .count{color:var(--muted);font-size:12px;margin-left:auto}
  table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
  th{background:#f0f2f5;font-size:12px;color:var(--muted);font-weight:600;white-space:nowrap}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#fafbfc}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .tag{display:inline-block;padding:1px 7px;border-radius:20px;font-size:11px;border:1px solid var(--line);color:var(--muted);margin:1px 3px 1px 0;background:#fafbfc;white-space:nowrap}
  .p{font-weight:700;font-size:12px;white-space:nowrap}
  .p-P0{color:var(--p0)} .p-P1{color:var(--p1)} .p-P2{color:var(--p2)} .p-P3{color:var(--p3)}
  .acc-open{color:#1f6b33;font-weight:600}
  .dim{color:var(--muted);font-size:12px}
  .note{background:var(--accent-soft);border:1px solid #cfe4e4;border-radius:10px;padding:12px 16px;margin-bottom:18px;font-size:13px}
  .note b{color:var(--accent)}
  footer{color:var(--muted);font-size:12px;padding:18px 28px;border-top:1px solid var(--line);background:var(--panel)}
</style>
</head>
<body>
<header>
  <h1>CnOpenData 数据源档案 · 股票与金融</h1>
  <div class="sub" id="sub"></div>
</header>
<div class="wrap">
  <div class="note">
    <b>档案边界：</b>只登记公开目录页的元信息（名称、覆盖、量级、更新频率、获取方式、链接），
    <b>不含任何数据内容</b>；未采购、未登录、未调用接口。「需采购 / 需 API Key」不等于已获授权，
    接入前必须完成合同与来源权利核验。
  </div>
  <div class="cards" id="cards"></div>
  <div class="bar">
    <input type="search" id="q" placeholder="搜索数据集名 / 简介 / 标签">
    <select id="fp"><option value="">全部优先级</option></select>
    <select id="fg"><option value="">全部分类组</option></select>
    <select id="ff"><option value="">全部更新频率</option></select>
    <select id="fa"><option value="">全部获取方式</option></select>
    <span class="count" id="count"></span>
  </div>
  <table>
    <thead><tr>
      <th style="width:26%">数据集</th><th style="width:13%">分类</th><th style="width:10%">更新</th>
      <th style="width:12%">覆盖</th><th style="width:9%">获取</th><th style="width:6%">优先级</th>
      <th style="width:10%">映射板块</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
</div>
<footer id="foot"></footer>
<script>
const DATA = __DATA__;
const PRI = {"P0":"P0 优先接入","P1":"P1 核心","P2":"P2 因子候选","P3":"P3 背景"};
const ACC = {"open":"免费 OPEN","trial":"高校试用","direct_sale":"需采购","api":"需 API Key","unknown":"未知"};

const el = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function uniq(arr){ return Array.from(new Set(arr)); }

function init(){
  const m = DATA.meta, list = DATA.datasets;
  el("sub").textContent = "生成时间 " + m.generated_at + "　·　唯一数据集 " + m.unique_datasets +
    " 个（条目 " + m.total_records + " 条，其中别名 " + (m.alias_records || 0) +
    " 条）　·　新鲜度已核 " + (m.freshness_checked || 0) + " 条　·　运营主体 " + m.source_operator;
  el("foot").textContent = "档案来源：CnOpenData 公开目录页（" + m.generated_at + "）｜本页为静态快照，不含数据内容，不联网。";

  const cards = [
    ["唯一数据集", m.unique_datasets],
    ["重复挂载别名", m.alias_records || 0],
    ["至今存疑(数据集)", (m.by_freshness_canonical || {}).stale || 0],
    ["频率口径待确认", (m.by_freq_conflict || {})["频率低频但区间标实时"] || 0],
    ["P0 优先接入", m.by_priority.P0 || 0],
    ["P1 核心", m.by_priority.P1 || 0],
    ["实时更新", m.by_frequency["实时"] || 0],
    ["免费 OPEN", m.by_access_mode.open || 0],
    ["需采购", m.by_access_mode.direct_sale || 0],
    ["API 接口", m.by_access_mode.api || 0],
  ];
  el("cards").innerHTML = cards.map(c =>
    '<div class="card"><b>' + c[1] + '</b><span>' + c[0] + '</span></div>').join("");

  const fill = (id, vals, labels) => {
    el(id).innerHTML += vals.map(v =>
      '<option value="' + esc(v) + '">' + esc(labels ? labels[v] || v : v) + '</option>').join("");
  };
  fill("fp", uniq(list.map(d => d.priority)).sort(), PRI);
  fill("fg", uniq(list.map(d => d.category_group)).sort());
  fill("ff", uniq(list.map(d => d.frequency)).sort());
  fill("fa", uniq(list.map(d => d.access_mode)).sort(), ACC);

  render();
  ["q","fp","fg","ff","fa"].forEach(id => {
    el(id).addEventListener(id === "q" ? "input" : "change", render);
  });
}

function render(){
  const q = el("q").value.trim().toLowerCase();
  const fp = el("fp").value, fg = el("fg").value, ff = el("ff").value, fa = el("fa").value;
  const list = DATA.datasets.filter(d => {
    if (fp && d.priority !== fp) return false;
    if (fg && d.category_group !== fg) return false;
    if (ff && d.frequency !== ff) return false;
    if (fa && d.access_mode !== fa) return false;
    if (q){
      const hay = [d.name, d.desc, (d.tags||[]).join(" "), d.category, d.category_group].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  el("count").textContent = "显示 " + list.length + " / " + DATA.datasets.length + " 条";
  el("rows").innerHTML = list.map(d => {
    const tags = (d.tags || []).slice(0, 4).map(t => '<span class="tag">' + esc(t) + '</span>').join("");
    const maps = (d.maps_to || []).map(t => '<span class="tag">' + esc(t) + '</span>').join("");
    const alias = d.alias_of
      ? '<span class="tag" title="同一数据集的另一挂载点，正本为 ' + esc(d.alias_of) + '">别名 → ' + esc(d.alias_of) + '</span>'
      : '';
    // 频率口径待确认：页面「更新频率」写低频（多为年度），同页「时间区间」却标实时。
    // 定性是"待确认"而非"矛盾"：取证显示多为同一系列的不同交付版本（批量库按年度、
    // 实时走 API），不等于档案抄错。
    // 只标这一档；「可补齐」96 条是机会不是故障，标出来会把 6 条真问题淹了。
    const freq = d.freq_conflict === "频率低频但区间标实时"
      ? '<span class="tag" title="' + esc(d.freq_note || '') +
        '（页面年度与实时多为不同交付版本：批量库按年度、实时走 API，不等于抄错）">频率口径待确认</span>'
      : '';
    // 新鲜度证据：只标 stale（止期距今>18个月），lag 属于正常发布延迟，不刷屏
    const fresh = d.freshness_flag === "stale"
      ? '<span class="tag" title="页面原文：' + esc(d.coverage_page || '') + '">至今存疑 · 止期距今 ' +
        esc(d.freshness_lag_months || '?') + ' 个月</span>'
      : '';
    return '<tr>' +
      '<td><a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.name) + '</a>' +
        '<div class="dim">' + esc((d.desc || "").slice(0, 78)) + (d.desc && d.desc.length > 78 ? "…" : "") + '</div>' +
        (freq ? '<div style="margin-top:3px">' + freq + '</div>' : '') +
        (fresh ? '<div style="margin-top:3px">' + fresh + '</div>' : '') +
        (alias ? '<div style="margin-top:3px">' + alias + '</div>' : '') +
        (tags ? '<div style="margin-top:3px">' + tags + '</div>' : '') + '</td>' +
      '<td class="dim">' + esc(d.category) + '</td>' +
      '<td>' + esc(d.frequency) + '</td>' +
      '<td class="dim">' + esc(d.coverage) + '</td>' +
      '<td class="' + (d.access_mode === "open" ? "acc-open" : "") + '">' + esc(ACC[d.access_mode] || d.access_mode) + '</td>' +
      '<td class="p p-' + esc(d.priority) + '">' + esc(d.priority) + '</td>' +
      '<td>' + maps + '</td>' +
    '</tr>';
  }).join("") || '<tr><td colspan="7" class="dim">无匹配条目</td></tr>';
}

init();
</script>
</body>
</html>
"""


def main() -> int:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    payload["meta"]["rendered_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    data_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.replace("__DATA__", data_js), encoding="utf-8")
    print(f"OK {OUT}  ({len(payload['datasets'])} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
