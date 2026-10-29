# -*- coding: utf-8 -*-
"""数据仓可视化快照生成器：source 治理 + 挖掘台账 + 运维健康。

产出：
  - portal/dw_snapshot.json（页面 fetch 用）
  - 控制台摘要
数据源：catalog.json / warehouse_registry.json / research/hypotheses.db / reports /
        backups / approvals.jsonl / supervisor dry-run / data 目录扫描。
用法：python tools/dw_dashboard.py [--json]
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data"
PORTAL = REPO / "portal"
OUT = PORTAL / "dw_snapshot.json"
PY = sys.executable

# 数据源治理状态分类（人工维护：只列语义，不重复目录扫描）
SOURCE_META = {
    "daily": ("Tushare 日线", "原始层", "done", "全市场日线，catalog/registry 已登记"),
    "daily_basic": ("Tushare 每日指标", "原始层", "done", "PE/PB/市值，契约已建"),
    "moneyflow": ("Tushare 资金流", "原始层", "done", "3 年 923 文件；契约已建；H13 复核 no alpha"),
    "fina_indicator": ("Tushare 财务指标", "原始层", "done", "5,569 股票文件；PIT 可用；H7/H10/H14 已用"),
    "income": ("Tushare 利润表", "原始层", "done", "PIT 财报"),
    "balancesheet": ("Tushare 资产负债表", "原始层", "done", "PIT 财报"),
    "cashflow": ("Tushare 现金流", "原始层", "done", "PIT 财报"),
    "forecast": ("Tushare 业绩预告", "原始层", "done", "3 年 21,196 行（type+p_change）；H2v2 已用"),
    "express": ("Tushare 业绩快报", "原始层", "done", "3 年 5,947 行"),
    "top_list": ("Tushare 龙虎榜", "原始层", "done", "3 年 760 日；H3 已用"),
    "top_inst": ("Tushare 龙虎榜席位", "原始层", "done", "3 年 760 日；H3 已用"),
    "hk_daily": ("Tushare 港股日线", "原始层", "in_progress", "726 日/152.7 万行；A/H 横截面研究待开"),
    "fx_daily": ("Tushare 汇率", "原始层", "in_progress", "FXCM 价格字段为空，需换源（P2）"),
    "index_global": ("全球指数", "原始层", "done", "20 指数 2018+；H9 pilot + 可交易性已用"),
    "index_daily": ("A 股指数日线", "原始层", "done", "宽基指数；研究常用"),
    "index_member_all": ("申万行业历史成分", "原始层", "done", "5,922 条 PIT 行业归属；H8/H14 已用"),
    "anns_d": ("公告日数据", "原始层", "done", "按日公告标题；H1/H11/H12 事件源"),
    "us_daily": ("美股日线", "原始层", "done", "24,211 股票文件；H9 未用（pilot 用指数）"),
    "sw_classify_l1": ("申万一级分类字典", "原始层", "done", "31 个行业"),
    "etf_mins": ("ETF 分钟线", "原始层", "mining", "数据存在，尚未挖掘"),
    "shibor": ("Shibor 利率", "原始层", "mining", "数据存在，尚未挖"),
    "shibor_lpr": ("LPR 利率", "原始层", "mining", "数据存在，尚未挖"),
    "announcement": ("公告类新闻", "衍生/采集", "done", "news.db 来源之一"),
    "news_collector": ("news.db（采集）", "采集流", "in_progress", "保留 365 天；历史无法回填（API 深度限制）"),
    "xueqiu_info": ("雪球舆情库", "采集流", "in_progress", "2012-2026；2021+ 稀疏，3 轮抓取已加"),
    "ann_history": ("公告历史库(ann_history.db)", "采集流", "done", "49 万条（2026-02 起）"),
    "zhice_pg": ("智策 OS v2（PG）", "生产库", "done", "8.4GB；B 批优化完成（-648MB/死元组 129）"),
    "quant_pg": ("quant-pg（PG）", "生产库", "done", "quant_meta 415MB；清理 613MB"),
    "cainiao_mysql": ("thunder_model（MySQL）", "生产库", "done", "181MB gz 已入备份"),
    "jev_audit": ("JEV 判定账本（SQLite）", "研究库", "in_progress", "6,658 后验；companies 空 82% 是上游限制"),
}

GOV_ITEMS = [
    ("catalog/registry 一致性", "done", "v16 / 253 表 / sha 一致"),
    ("manifest 覆盖", "done", "closed 239 / not_applicable 12 / open 1 / missing 1"),
    ("数据契约（ODCS）", "in_progress", "6 份（daily/daily_basic/index_daily/adj_factor/margin_detail/moneyflow）；待扩到研究用表"),
    ("资格取证（qualification）", "in_progress", "以 evidence_chain/registry 为准；未全量覆盖"),
    ("display 展示登记", "in_progress", "92/253 展示名（门户/台账可读性）"),
    ("生命周期（lifecycle）", "in_progress", "data_lifecycle.yaml 9 条（xq_* planned + 1 deprecated）"),
    ("新鲜度（freshness）", "done", "daily 调度 + run_all 报告"),
    ("staging 生命周期", "done", "周检（retention 30d / 失效锁 7d）"),
    ("备份链（BackupDaily）", "done", "7 产物 + 异地 byte-identical + 新增 quant_meta/thunder_model（section 7）"),
    ("可重拉策略", "done", "_source_reclaim.json（11 类来源）"),
    ("汇率数据源缺失", "in_progress", "Tushare FXCM 价格全空 → 需换源（P2，不阻塞横截面）"),
    ("news 历史沉淀", "in_progress", "无法回填（API 深度 ≈1 周）→ 改长期采集+保留 365 天"),
    ("xq 近端抓取频率", "done", "单次 3 轮（240s 间隔）已上线；待 18:00 后看密度"),
    ("watchdog 单实例校验", "pending", "ws 重启后仍双实例（历史现象），建议互斥加固"),
    ("approval 回写 devtask/事件", "in_progress", "S2 台账已落 approvals.jsonl；devtask/事件回写待接"),
]


def _size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return round(total / 1e6, 1)


def load_catalog() -> tuple[dict, dict]:
    cat = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    reg = json.loads((DATA / "warehouse_registry.json").read_text(encoding="utf-8"))
    return cat, reg


def tushare_sources(cat: dict) -> list[dict]:
    rows = []
    tdir = DATA / "tushare"
    for d in sorted(p for p in tdir.iterdir() if p.is_dir()):
        name = d.name
        files = list(d.glob("*.parquet"))
        table = cat["tables"].get(name) or {}
        meta = SOURCE_META.get(name)
        if meta:
            label, layer, status, note = meta
        else:
            label, layer, status, note = name, "原始层", "mining", "未分类（自动发现）"
        rows.append({
            "name": name, "label": label, "layer": layer, "status": status, "note": note,
            "files": len(files) if files else _count_dirs(d),
            "size_mb": _size_mb(d),
            "manifest": (table.get("manifest_status") or "—"),
            "date_status": (table.get("date_status") or "—"),
            "registered": bool(table),
        })
    return rows


def _count_dirs(d: Path) -> int:
    try:
        return sum(1 for x in d.iterdir() if x.is_file())
    except OSError:
        return 0


def other_sources() -> list[dict]:
    rows = []
    for key, label, layer, status, note, path, files in [
        ("news.db", "news.db（采集）", "采集流", "in_progress", "保留 365 天；无法回填",
         REPO / "news_collector" / "news.db", None),
        ("xueqiu_info.db", "雪球舆情库", "采集流", "in_progress", "2012-2026，2021+ 稀疏",
         DATA / "xueqiu" / "xueqiu_info.db", None),
        ("jev_audit.db", "JEV 判定账本", "研究库", "in_progress", "后验 6,658；companies 空 82%",
         REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db", None),
        ("evidence_chain.db", "证据链", "治理库", "done", "资格/取证",
         DATA / "evidence_chain.db", None),
    ]:
        if path.exists():
            rows.append({"name": key, "label": label, "layer": layer, "status": status,
                         "note": note, "files": files, "size_mb": round(path.stat().st_size / 1e6, 2),
                         "manifest": "—", "date_status": "—", "registered": True})
    return rows


def hypotheses() -> list[dict]:
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
                    "question": (d.get("question") or "")[:160],
                    "conclusion": (d.get("conclusion") or "")[:220],
                    "evidence": d.get("evidence_path") or ""})
    return out


def reports() -> list[dict]:
    rows = []
    for f in sorted((REPO / "research" / "reports").glob("*"), key=lambda x: -x.stat().st_mtime):
        if f.suffix not in (".md", ".json"):
            continue
        rows.append({"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
                     "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")})
    return rows[:20]


def backup_info() -> dict:
    bp = REPO / "backups"
    out = {"latest": None, "products": [], "receipt": None}
    if not bp.exists():
        return out
    gens = sorted(d for d in bp.glob("2026-*") if d.is_dir())
    if not gens:
        return out
    last = gens[-1]
    out["latest"] = last.name
    for f in sorted(last.glob("*.tar.gz")) + sorted(last.glob("*.gz*")):
        out["products"].append({"name": f.name, "mb": round(f.stat().st_size / 1e6, 1)})
    for f in last.glob("*receipt*.txt"):
        out["receipt"] = f.name
    return out


def supervisor() -> dict:
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


def approvals() -> dict:
    f = REPO / "agent_platform" / "data" / "approvals.jsonl"
    rows = []
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return {"count": len(rows), "latest": rows[-3:]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cat, reg = load_catalog()
    qual = sum(1 for t in cat["tables"].values() if isinstance(t, dict)
               and (t.get("verification_status") or "unknown") in ("unknown", ""))
    contracts = len(list((REPO / "agent_platform" / "catalogs").glob("contracts/*.yaml")))
    snap = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog": {"version": cat.get("version"), "tables": len(cat["tables"]),
                    "manifest": {"closed": sum(1 for t in cat["tables"].values()
                                               if isinstance(t, dict) and t.get("manifest_status") == "closed"),
                                 "missing": sum(1 for t in cat["tables"].values()
                                                if isinstance(t, dict) and t.get("manifest_status") == "missing"),
                                 "open": sum(1 for t in cat["tables"].values()
                                             if isinstance(t, dict) and t.get("manifest_status") == "open")},
                    "registry": {"physical": reg.get("physical_count"), "display": reg.get("display_count")},
                    "contracts": contracts, "qualification_unknown": qual},
        "sources": tushare_sources(cat) + other_sources(),
        "governance": [{"item": a, "status": b, "note": c} for a, b, c in GOV_ITEMS],
        "mining": {"hypotheses": hypotheses(), "reports": reports()},
        "ops": {"backup": backup_info(), "supervisor": supervisor(), "approvals": approvals()},
    }
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1)[:2000])
    else:
        print("snapshot:", OUT, "size=", OUT.stat().st_size)
        print("sources:", len(snap["sources"]), "hypotheses:", len(snap["mining"]["hypotheses"]),
              "tables:", snap["catalog"]["tables"], "backup:", snap["ops"]["backup"]["latest"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
