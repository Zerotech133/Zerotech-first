# -*- coding: utf-8 -*-
"""对账摘要自动再生（K180）：读 col_audit.json + source_col_audit 底册
→ 生成 col_audit_summary_YYYYMMDD.html（/col-audit 路由 glob 最新直出），
并滚动清理旧摘要（保留最近 4 份）。AIColAudit 周批任务跑完自动调用，
防线报告永不过时。

用法：python gen_col_audit_summary.py
"""

import glob
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
KEEP = 4
_esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_report():
    p = os.path.join(REPORTS, "col_audit.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def table_rows(report):
    """底册 18 表状态行：FAIL 红缺列 / SKIP 灰原因 / 其余绿 OK。"""
    import source_col_audit as sca

    fails = {}
    for _, store, rel, msg in report.get("fails") or []:
        fails.setdefault((store, rel), []).append(msg)
    skips = {(s, r): m for _, s, r, m in report.get("skips") or []}
    rows = []
    for (store, rel), consumers in sorted(sca.CONSUMERS.items()):
        if (store, rel) in fails:
            det = "；".join(_esc(m) for m in fails[(store, rel)])
            cell = "<td class='r'>FAIL：{} </td>".format(det)
        elif (store, rel) in skips:
            cell = "<td class='y'>SKIP：{}</td>".format(_esc(skips[(store, rel)]))
        else:
            cell = "<td class='g'>OK</td>"
        descs = "；".join(_esc(d) for d, _ in consumers)
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td>{}</tr>".format(
                _esc(store), _esc(rel), descs, cell
            )
        )
    return "\n".join(rows)


def gen():
    rep = load_report()
    n_fail = len(rep.get("fails") or [])
    n_skip = len(rep.get("skips") or [])
    n_tbl = rep.get("n_consumers") or 0
    graph = bool(rep.get("graph_reachable"))
    verdict = "全绿" if not n_fail else "{} 项断源嫌疑".format(n_fail)
    vcolor = "g" if not n_fail else "r"
    today = datetime.now().strftime("%Y%m%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = table_rows(rep)
    gstate = "可达" if graph else "<span class='y'>不可达（SKIP）</span>"

    html = """<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8">
<title>数据源列对账报告摘要 · __NOW__</title>
<style>
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
     background:#0d1117;color:#e6edf3;margin:0;padding:24px;line-height:1.6}
h1{font-size:20px;border-bottom:1px solid #30363d;padding-bottom:10px}
h2{font-size:16px;color:#58a6ff;margin-top:28px}
.dim{color:#8b949e;font-size:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;
      padding:14px 16px;margin:10px 0}
.g{color:#3fb950;font-weight:600}.r{color:#f85149;font-weight:600}
.y{color:#d29922;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{border:1px solid #30363d;padding:5px 9px;text-align:left}
th{background:#21262d;color:#8b949e}
code{background:#0d1117;border:1px solid #30363d;border-radius:4px;
     padding:1px 6px;font-size:12px;color:#a5d6ff}
.kpi{display:flex;gap:12px;flex-wrap:wrap}
.kpi .card{flex:1 1 160px;text-align:center}
.kpi b{font-size:26px;display:block}
</style></head>
<body>
<h1>数据源列对账报告摘要 <span class="dim">· 生成于 __NOW__ · 系列 K165~K180</span></h1>
<div class="kpi">
<div class="card"><b class="__VC__">__VERDICT__</b>对账结论</div>
<div class="card"><b>__NTBL__</b>底册表</div>
<div class="card"><b class="g">__NFAIL__</b>断源嫌疑</div>
<div class="card"><b>__NSKIP__</b>Skip</div>
<div class="card"><b>__GRAPH__</b>图内链</div>
</div>

<h2>一、说明</h2>
<div class="card">
<p>本页由 <b>AIColAudit</b> 周批任务（每周日 06:20）自动再生，数据来自
<code>reports/col_audit.json</code>；对账工具 <code>source_col_audit.py</code>
（K175）三大模块：①源列快照（arrow footer）②消费端底册交叉（本页表格）
③图内 presence（MarketEvent/Seat/边，图内链 __GSTATE__）。</p>
<p>断源嫌疑处置：先判定「tushare 改名 vs 消费端写错」，再改对应头；
任务 FAIL 已自动经 alert_fail_wrap.py 推飞书。</p>
</div>

<h2>二、底册状态（__NTBL__ 表）</h2>
<div class="card">
<table>
<tr><th>仓</th><th>表</th><th>消费端</th><th>状态</th></tr>
__ROWS__
</table>
<p class="dim">消费列全集见 source_col_audit.py CONSUMERS 底册（K165~K173
实读背书）；部分覆盖率语义（尾盘量/封单额等稀疏特征）非断源。</p>
</div>

<h2>三、历史断源账本（均已修复）</h2>
<div class="card">
<table>
<tr><th>断源</th><th>实伤</th><th>修复</th></tr>
<tr><td>tushare <code>limit_type</code> → <code>limit</code> 改名</td>
<td>1413 行涨跌停静默错标（K161）</td>
<td>源列对齐 + 消费端三态化（涨停/跌停/炸板）</td></tr>
<tr><td>图内 first/last_seal_time 缺失</td>
<td>Neo4j property warning 每查询触发</td>
<td>K164 映射 + K165 重刷 3470/3470</td></tr>
</table>
</div>

<h2>四、防线范式（四级）</h2>
<div class="card">
<ol>
<li><b>date_col_of 权威映射器</b>（tdx_read）——列名单点定义</li>
<li><b>issubset/keepc 显式检查</b>（chanlun_scan / nine_dim_backtest）</li>
<li><b>_warn_missing 读入点 WARN</b>（kg_bclass，K164）</li>
<li><b>try 兜底降级</b>——自产仓可，外部源仓弱</li>
</ol>
<p class="dim">遗留挂池：tushare 仓 TABLE_DEFS 共享映射器（中量级）。
历史摘要滚动保留 __KEEP__ 份。</p>
</div>
</body></html>
"""
    html = (
        html.replace("__NOW__", now)
        .replace("__VERDICT__", verdict)
        .replace("__VC__", vcolor)
        .replace("__NTBL__", str(n_tbl))
        .replace("__NFAIL__", str(n_fail))
        .replace("__NSKIP__", str(n_skip))
        .replace("__GRAPH__", "可达" if graph else "SKIP")
        .replace("__GSTATE__", gstate)
        .replace("__ROWS__", rows)
        .replace("__KEEP__", str(KEEP))
    )
    out = os.path.join(REPORTS, "col_audit_summary_%s.html" % today)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    olds = sorted(glob.glob(os.path.join(REPORTS, "col_audit_summary_*.html")))
    for old in olds[:-KEEP]:
        try:
            os.remove(old)
            print("pruned:", os.path.basename(old))
        except OSError:
            pass
    print("summary:", os.path.basename(out), "fails=%d skips=%d" % (n_fail, n_skip))


if __name__ == "__main__":
    gen()
