# -*- coding: utf-8 -*-
"""数据仓驾驶舱快照生成器 v2：KPI 下钻 + 来源详情 + 今日动态 + 挖掘时间线 + 治理路线。

产出 portal/static/dw_snapshot.json（页面 fetch；支持前端下载 JSON/CSV）。
用法：python tools/dw_dashboard.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data"
PORTAL = REPO / "portal"
OUT = PORTAL / "static" / "dw_snapshot.json"
PY = sys.executable
TODAY = datetime.now().strftime("%Y-%m-%d")

CONTRACTS = {"daily", "daily_basic", "index_daily", "adj_factor", "margin_detail", "moneyflow"}
SOURCE_HYP = {
    "daily": ["H1", "H2", "H3", "H4", "H7", "H8", "H9", "H11", "H12", "H13", "H14"],
    "daily_basic": ["H7", "H10", "H14"], "moneyflow": ["H4", "H13"],
    "fina_indicator": ["H7", "H10", "H14"], "income": ["H7"], "balancesheet": ["H7"],
    "cashflow": ["H7"], "forecast": ["H2", "H11", "H12"], "express": ["H2"],
    "top_list": ["H3"], "top_inst": ["H3"], "index_global": ["H9"],
    "index_daily": ["H1", "H2", "H9"], "index_member_all": ["H8", "H14"],
    "anns_d": ["H1", "H11", "H12"], "us_daily": ["H9"], "hk_daily": ["H9"],
    "fx_daily": ["H9"],
}
LAYER_ORDER = ["原始层", "采集流", "生产库", "研究库", "治理库"]
STATUS_ORDER = ["done", "in_progress", "mining", "pending"]
SOURCE_META = {
    "daily": ("Tushare 日线", "原始层", "done", "全市场日线；catalog/registry 登记"),
    "daily_basic": ("Tushare 每日指标", "原始层", "done", "PE/PB/市值；契约已建"),
    "moneyflow": ("Tushare 资金流", "原始层", "done", "3 年 923 文件；契约已建；H13 无 alpha"),
    "fina_indicator": ("Tushare 财务指标", "原始层", "done", "5,569 文件；PIT 可用"),
    "income": ("Tushare 利润表", "原始层", "done", "PIT 财报"),
    "balancesheet": ("Tushare 资产负债表", "原始层", "done", "PIT 财报"),
    "cashflow": ("Tushare 现金流", "原始层", "done", "PIT 财报"),
    "forecast": ("Tushare 业绩预告", "原始层", "done", "3 年 21,196 行"),
    "express": ("Tushare 业绩快报", "原始层", "done", "3 年 5,947 行"),
    "top_list": ("Tushare 龙虎榜", "原始层", "done", "3 年 760 日"),
    "top_inst": ("Tushare 龙虎榜席位", "原始层", "done", "3 年 760 日"),
    "hk_daily": ("Tushare 港股日线", "原始层", "in_progress", "726 日/152.7 万行；A/H 研究待开"),
    "fx_daily": ("Tushare 汇率", "原始层", "in_progress", "FXCM 价格字段为空，需换源"),
    "index_global": ("全球指数", "原始层", "done", "20 指数；H9 已用"),
    "index_daily": ("A 股指数日线", "原始层", "done", "宽基指数"),
    "index_member_all": ("申万行业历史成分", "原始层", "done", "5,922 条 PIT 归属"),
    "anns_d": ("公告日数据", "原始层", "done", "按日公告标题"),
    "us_daily": ("美股日线", "原始层", "done", "24,211 文件"),
    "sw_classify_l1": ("申万一级分类字典", "原始层", "done", "31 行业"),
    "etf_mins": ("ETF 分钟线", "原始层", "mining", "存在，未挖掘"),
    "shibor": ("Shibor 利率", "原始层", "mining", "存在，未挖掘"),
    "shibor_lpr": ("LPR 利率", "原始层", "mining", "存在，未挖掘"),
}
GOV_ITEMS = [
    ("catalog/registry 一致性", "done", "v16 / 253 表 / sha 一致", "2026-10-29"),
    ("manifest 覆盖", "done", "closed 239 / not_applicable 12 / open 1 / missing 1", "2026-10-29"),
    ("数据契约（ODCS）", "in_progress", "6 份；待扩研究用表", "2026-10-28"),
    ("资格取证", "in_progress", "evidence_chain/registry；未全量覆盖", "2026-10-28"),
    ("display 展示登记", "in_progress", "92/253", "2026-10-28"),
    ("生命周期 lifecycle", "in_progress", "data_lifecycle.yaml 9 条", "2026-10-28"),
    ("新鲜度 freshness", "done", "DW-Daily 06:30 + run_all", "2026-10-28"),
    ("staging 生命周期", "done", "retention 30d / 锁 7d", "2026-10-27"),
    ("备份链 BackupDaily", "done", "7 产物 + 异地校验 + 新增 DB section7", "2026-10-29"),
    ("可重拉策略", "done", "_source_reclaim.json 11 类", "2026-10-29"),
    ("汇率数据源缺失", "in_progress", "Tushare FXCM 价格空 → 换源(P2)", "2026-10-29"),
    ("news 历史沉淀", "in_progress", "不可回填→长期采集+保留365天", "2026-10-29"),
    ("xq 近端抓取频率", "done", "单次 3 轮（240s）", "2026-10-29"),
    ("watchdog 单实例校验", "pending", "ws 双实例现象，建议互斥加固", "2026-10-30"),
    ("approval 回写 devtask/事件", "in_progress", "approvals.jsonl 已落；回写待接", "2026-10-30"),
]
ROADMAP = [
    ("D0 家底盘清", "done", "2026-10-28", "catalog/registry 基线、db health、备份链"),
    ("D1 元数据治理收尾", "done", "2026-10-28", "manifest/freshness/staging/lifecycle"),
    ("D2–D3 Supervisor + 签字卡", "done", "2026-10-30", "L1/L3/L4 dry-run；approval:* 接线"),
    ("D4–D5 M4/v1.1 S2 签字", "done", "2026-10-30", "卡片→点击→approvals 落账"),
    ("数据补强批（Tushare）", "done", "2026-10-28~29", "forecast/express/top_*/moneyflow/hk_daily/index_member_all"),
    ("09:30 节点检查", "pending", "2026-10-30", "T19/DEV7/交接 + 签字回写验证"),
    ("H14 质量陷阱决策 A/B/C", "pending", "2026-10-30 例会", "组合层回测→影子盘"),
    ("A/H 横截面比价研究", "in_progress", "2026-10-30+", "hk_daily 已入库；汇率缺源不阻塞"),
    ("fx 汇率换源", "pending", "P2", "东财/央行中间价"),
    ("备份覆盖补洞", "in_progress", "2026-10-30", "quant_meta+thunder_model 已接入；03:00 首跑验回执"),
]


def _fmt_size(mb: float) -> str:
    return ("%.1f GB" % (mb / 1000)) if mb >= 1000 else ("%.0f MB" % mb)


def _size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return round(total / 1e6, 1)


def _mtime_date(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return ""


def load_hypotheses() -> list[dict]:
    db = REPO / "research" / "hypotheses.db"
    if not db.exists():
        return []
    con = sqlite3.connect("file:%s?mode=ro" % str(db).replace("\\", "/"), uri=True)
    cols = [r[1] for r in con.execute("PRAGMA table_info(hypotheses)")]
    rows = con.execute("SELECT * FROM hypotheses ORDER BY id").fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        out.append({"id": d.get("id"), "title": d.get("title"), "status": d.get("status"),
                    "question": (d.get("question") or "")[:200],
                    "data_sources": d.get("data_sources") or "",
                    "method": d.get("method") or "",
                    "conclusion": (d.get("conclusion") or "")[:400],
                    "evidence": d.get("evidence_path") or "",
                    "created_at": (d.get("created_at") or "")[:19],
                    "updated_at": (d.get("updated_at") or "")[:19]})
    return out


def load_sources(cat: dict) -> list[dict]:
    rows = []
    tdir = DATA / "tushare"
    for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
        meta = SOURCE_META.get(d.name)
        if meta:
            label, layer, status, note = meta
        else:
            label, layer, status, note = d.name, "原始层", "mining", "自动发现（未分类）"
        files = sorted(d.glob("*.parquet"))
        if files:
            files_n = len(files)
            range_s = "%s..%s" % (files[0].stem.replace("part_", "p").replace("d_", ""),
                                  files[-1].stem.replace("part_", "p").replace("d_", ""))
            today_new = sum(1 for f in files if _mtime_date(f) == TODAY)
            last_update = max((_mtime_date(f) for f in files), default="")
        else:
            files_n = sum(1 for x in d.iterdir() if x.is_file()) if d.exists() else 0
            range_s = "按股票文件"
            today_new = 0
            last_update = max((_mtime_date(x) for x in d.iterdir()), default="") if d.exists() else ""
        table = cat["tables"].get(d.name) or {}
        mb = _size_mb(d)
        rows.append({"name": d.name, "label": label, "layer": layer, "status": status,
                     "note": note, "files": files_n, "size_mb": mb, "size_h": _fmt_size(mb),
                     "range": range_s, "last_update": last_update, "today_new": today_new,
                     "registered": bool(table), "manifest": table.get("manifest_status") or "—",
                     "date_status": table.get("date_status") or "—",
                     "contract": d.name in CONTRACTS,
                     "hypothesis_ids": SOURCE_HYP.get(d.name, []),
                     "next_action": "—" if status == "done" else note})
    return rows


def load_other_sources() -> list[dict]:
    specs = [
        ("news.db", "news.db（采集）", "采集流", "in_progress", "保留 365 天；历史不可回填",
         REPO / "news_collector" / "news.db", ["H5", "H6"]),
        ("xueqiu_info.db", "雪球舆情库", "采集流", "in_progress", "2012-2026，2021+ 稀疏",
         DATA / "xueqiu" / "xueqiu_info.db", ["H5"]),
        ("jev_audit.db", "JEV 判定账本", "研究库", "in_progress", "后验 6,658；companies 空 82%",
         REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db", ["H6"]),
        ("evidence_chain.db", "证据链", "治理库", "done", "资格/取证",
         DATA / "evidence_chain.db", []),
        ("ann_history.db", "公告历史库", "采集流", "done", "49 万条（2026-02 起）",
         REPO / "zerobound-kg" / "data" / "ann_history" / "ann_history.db", ["H1", "H11"]),
    ]
    rows = []
    for name, label, layer, status, note, path, hyps in specs:
        if not path.exists():
            continue
        mb = round(path.stat().st_size / 1e6, 2)
        rows.append({"name": name, "label": label, "layer": layer, "status": status,
                     "note": note, "files": 1, "size_mb": mb, "size_h": _fmt_size(mb),
                     "range": "—", "last_update": _mtime_date(path),
                     "today_new": 1 if _mtime_date(path) == TODAY else 0,
                     "registered": True, "manifest": "—", "date_status": "—",
                     "contract": False, "hypothesis_ids": hyps,
                     "next_action": note if status != "done" else "—"})
    return rows


def load_reports() -> list[dict]:
    rows = []
    for f in sorted((REPO / "research" / "reports").glob("*"), key=lambda x: -x.stat().st_mtime):
        if f.suffix not in (".md", ".json"):
            continue
        m = datetime.fromtimestamp(f.stat().st_mtime)
        prefix = f.name.split("_")[0]
        rows.append({"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
                     "date": m.strftime("%Y-%m-%d"), "time": m.strftime("%H:%M"),
                     "hid": prefix if prefix.startswith("H") else ""})
    return rows[:40]


def load_tasks() -> dict:
    f = REPO / ".worktrees" / "tasks.json"
    empty = {"doing": [], "today_pilot": [], "today_merged": [], "counts": {}}
    if not f.exists():
        return empty
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return empty
    tasks = d.get("tasks", d)
    doing, pilot_today, merged_today, counts = [], [], [], {}
    for tid, t in tasks.items():
        if not isinstance(t, dict):
            continue
        st = (t.get("status") or "").lower()
        counts[st] = counts.get(st, 0) + 1
        item = {"id": tid, "status": st, "owner": t.get("owner") or "",
                "desc": (t.get("desc") or "")[:120]}
        if st in ("doing", "review"):
            doing.append(item)
        merged = t.get("merged") or ""
        created = t.get("created") or ""
        if st in ("doing", "review") and (created.startswith(TODAY) or merged.startswith(TODAY)):
            pilot_today.append(item)
        if merged.startswith(TODAY):
            merged_today.append(item)
    return {"doing": doing[:30], "today_pilot": pilot_today, "today_merged": merged_today,
            "counts": counts}


def load_backup() -> dict:
    bp = REPO / "backups"
    out = {"latest": None, "products": [], "receipt": None, "age_hours": None}
    if not bp.exists():
        return out
    gens = sorted(d for d in bp.glob("2026-*") if d.is_dir())
    if not gens:
        return out
    last = gens[-1]
    out["latest"] = last.name
    out["receipt"] = next((f.name for f in last.glob("*receipt*.txt")), None)
    for f in sorted(last.glob("*.tar.gz")) + sorted(last.glob("*.gz*")):
        out["products"].append({"name": f.name, "mb": round(f.stat().st_size / 1e6, 1)})
    try:
        dt = datetime.strptime(last.name.split("_")[0], "%Y-%m-%d")
        out["age_hours"] = round((datetime.now() - dt).total_seconds() / 3600, 1)
    except ValueError:
        pass
    return out


def load_supervisor() -> dict:
    try:
        r = subprocess.run([PY, "-X", "utf8", "tools/automation_supervisor.py", "--json"],
                           cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        if r.returncode in (0, 2) and r.stdout.strip():
            d = json.loads(r.stdout)
            return {"red": d.get("red") or [], "generated_at": d.get("generated_at")}
    except Exception:  # noqa: BLE001
        pass
    return {"red": [], "generated_at": None}


def load_approvals() -> dict:
    f = REPO / "agent_platform" / "data" / "approvals.jsonl"
    rows = []
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return {"count": len(rows), "today": sum(1 for r in rows if str(r.get("ts") or "").startswith(TODAY)),
            "latest": rows[-5:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cat = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    reg = json.loads((DATA / "warehouse_registry.json").read_text(encoding="utf-8"))
    sources = load_sources(cat) + load_other_sources()
    hyps = load_hypotheses()
    reports = load_reports()
    tasks = load_tasks()
    backup = load_backup()
    sup = load_supervisor()
    approvals = load_approvals()
    status_cnt = {}
    for s in sources:
        status_cnt[s["status"]] = status_cnt.get(s["status"], 0) + 1
    hyp_cnt = {}
    for h in hyps:
        hyp_cnt[h["status"]] = hyp_cnt.get(h["status"], 0) + 1
    qual_unknown = sum(1 for t in cat["tables"].values() if isinstance(t, dict)
                       and (t.get("verification_status") or "unknown") in ("unknown", ""))
    contracts_n = len(list((REPO / "agent_platform" / "catalogs" / "contracts").glob("*.yaml")))
    total = len(sources)
    done = status_cnt.get("done", 0)
    snap = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": TODAY,
        "catalog": {"version": cat.get("version"), "tables": len(cat["tables"]),
                    "registry": {"physical": reg.get("physical_count"), "display": reg.get("display_count")},
                    "contracts": contracts_n, "qualification_unknown": qual_unknown,
                    "manifest": {k: sum(1 for t in cat["tables"].values()
                                        if isinstance(t, dict) and t.get("manifest_status") == k)
                                 for k in ("closed", "not_applicable", "open", "missing")}},
        "cockpit": {"sources_total": total, "sources_done": done,
                    "sources_in_progress": status_cnt.get("in_progress", 0),
                    "sources_mining": status_cnt.get("mining", 0),
                    "sources_pending": status_cnt.get("pending", 0),
                    "coverage_pct": round(done * 100.0 / total, 1) if total else 0,
                    "new_today": sum(1 for s in sources if s.get("today_new")),
                    "pilot_today": len(tasks["today_pilot"]),
                    "merged_today": len(tasks["today_merged"]),
                    "hypotheses": len(hyps), "hypotheses_by_status": hyp_cnt,
                    "reports_total": len(reports),
                    "reports_today": sum(1 for r in reports if r["date"] == TODAY),
                    "approvals_today": approvals["today"],
                    "risks": [{"item": g[0], "status": g[1], "note": g[2]} for g in GOV_ITEMS if g[1] != "done"],
                    "backup_latest": backup["latest"], "backup_age_hours": backup["age_hours"],
                    "supervisor_red": sup["red"]},
        "sources": sources,
        "new_today_sources": [s for s in sources if s.get("today_new")],
        "hypotheses": hyps,
        "reports": reports,
        "tasks": tasks,
        "governance": [{"item": a, "status": b, "note": c, "date": d} for a, b, c, d in GOV_ITEMS],
        "roadmap": [{"phase": a, "status": b, "date": c, "note": d} for a, b, c, d in ROADMAP],
        "ops": {"backup": backup, "supervisor": sup, "approvals": approvals},
        "layers": LAYER_ORDER, "status_order": STATUS_ORDER,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(snap["cockpit"], ensure_ascii=False, indent=1))
    else:
        print("snapshot:", OUT, "size=", OUT.stat().st_size)
        print("cockpit:", json.dumps(snap["cockpit"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
