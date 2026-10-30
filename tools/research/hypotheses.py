# -*- coding: utf-8 -*-
"""假设台账 CLI：research/hypotheses.db 的初始化/播种/查询/更新。

用法：
  python tools/research/hypotheses.py init [--seed]
  python tools/research/hypotheses.py list [--status X] [--json]
  python tools/research/hypotheses.py add --id H11 --title ... --question ... --data ... --method ...
  python tools/research/hypotheses.py update H1 --status backtested --conclusion ... [--evidence PATH]
退出码：0=ok / 1=参数/DB 错误。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "research" / "hypotheses.db"
GATES_TEMPLATE = {"pit": "", "oos": "", "cost": "", "capacity": ""}

SEED = [
    ("H1", "公告类型的事件超额（CAR）", "哪类公告在披露后 1/3/5 交易日有显著超额收益？", "ann_history + tushare daily", "事件研究：市场调整 AR，CAR[0,1]/[0,5]，按类型/市场分层"),
    ("H2", "业绩预告/快报的 PEAD", "盈余公告后是否存在持续漂移？", "tushare forecast/express + daily", "分位数组合 + 持有 5/10/20 日，样本外验证"),
    ("H3", "龙虎榜机构席位跟单", "机构席位净买入是否预示次日/5 日超额？", "tushare top_list/top_inst + daily", "席位分类事件研究 + 成本后"),
    ("H4", "资金流与价格背离", "量价/资金流背离是否预示反转？", "tushare moneyflow + daily", "条件分组 + 反转收益检验"),
    ("H5", "舆情情绪领先价格", "新闻/雪球情绪是否领先价格 1–2 小时？", "news.db + xueqiu_info.db + zhice minute", "lead-lag 互相关 + 事件窗口"),
    ("H6", "JEV 决策归因", "哪些 bias/playbook/regime 在系统性赚钱或亏钱？", "jev_audit.db + zhice aw_decision", "分组统计（收益/胜率/样本量）+ 决策规则改进"),
    ("H7", "财务因子分层有效性", "9 维财务因子在哪些市值/行业分层有效？", "tushare nine_dim/财报 + daily", "IC / 分层 / 正交化"),
    ("H8", "行业资金轮动", "行业资金流是否存在 3–5 日动量？", "tushare sector + moneyflow", "行业截面动量回测"),
    ("H9", "跨市场映射", "美股/港股事件是否领先 A 股映射标的？", "us/hk 行情 + A 股 daily", "跨市场 lead-lag"),
    ("H10", "数据质量对回测结论的影响", "剔除低可信度表后回测结论是否改变？", "catalog/registry + 回测", "前后对比实验"),
]


def connect(db: Path | None = None) -> sqlite3.Connection:
    p = Path(db) if db is not None else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute(
        """CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT, data_sources TEXT,
            method TEXT, gates TEXT, status TEXT NOT NULL DEFAULT 'idea',
            owner TEXT, conclusion TEXT, evidence_path TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    con.commit()
    return con


def init(seed: bool = True, db: Path | None = None) -> dict:
    con = connect(db)
    added = 0
    if seed:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for hid, title, question, data, method in SEED:
            cur = con.execute(
                "INSERT OR IGNORE INTO hypotheses (id,title,question,data_sources,method,gates,status,owner,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (hid, title, question, data, method, json.dumps(GATES_TEMPLATE, ensure_ascii=False),
                 "idea", "", now, now))
            added += cur.rowcount
        con.commit()
    n = con.execute("SELECT count(*) FROM hypotheses").fetchone()[0]
    con.close()
    return {"ok": True, "seeded": added, "total": n}


def get(hid: str, db: Path | None = None) -> dict | None:
    con = connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM hypotheses WHERE id=?", (hid,)).fetchone()
    con.close()
    return dict(row) if row else None


def rows(status: str = "", db: Path | None = None) -> list[dict]:
    con = connect(db)
    con.row_factory = sqlite3.Row
    if status:
        rs = con.execute("SELECT * FROM hypotheses WHERE status=? ORDER BY id", (status,)).fetchall()
    else:
        rs = con.execute("SELECT * FROM hypotheses ORDER BY id").fetchall()
    con.close()
    return [dict(r) for r in rs]


def add(hid: str, title: str, question: str = "", data: str = "", method: str = "",
        db: Path | None = None) -> dict:
    con = connect(db)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        con.execute(
            "INSERT INTO hypotheses (id,title,question,data_sources,method,gates,status,owner,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (hid, title, question, data, method, json.dumps(GATES_TEMPLATE, ensure_ascii=False),
             "idea", "", now, now))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return {"ok": False, "error": "duplicate id: " + hid}
    con.close()
    return {"ok": True, "id": hid}


def update(hid: str, db: Path | None = None, **fields) -> dict:
    allowed = {"status", "conclusion", "evidence_path", "gates", "owner", "method", "title", "question", "data_sources"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "gates" and isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        sets.append("%s=?" % k)
        vals.append(v)
    if not sets:
        return {"ok": False, "error": "no fields"}
    sets.append("updated_at=?")
    vals.append(datetime.now().astimezone().isoformat(timespec="seconds"))
    vals.append(hid)
    con = connect(db)
    cur = con.execute("UPDATE hypotheses SET %s WHERE id=?" % ", ".join(sets), vals)  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
    con.commit()
    con.close()
    return {"ok": cur.rowcount > 0, "id": hid, "updated": cur.rowcount}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="假设台账")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--seed", action="store_true")
    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default="")
    p_list.add_argument("--json", action="store_true")
    p_add = sub.add_parser("add")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--question", default="")
    p_add.add_argument("--data", default="")
    p_add.add_argument("--method", default="")
    p_up = sub.add_parser("update")
    p_up.add_argument("id")
    p_up.add_argument("--status", default=None)
    p_up.add_argument("--conclusion", default=None)
    p_up.add_argument("--evidence", default=None)
    p_up.add_argument("--owner", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "init":
        print(json.dumps(init(seed=args.seed), ensure_ascii=False))
    elif args.cmd == "list":
        rs = rows(args.status)
        if args.json:
            print(json.dumps(rs, ensure_ascii=False, indent=1))
        else:
            print("%-4s %-8s %-28s %s" % ("id", "status", "title", "evidence"))
            for r in rs:
                print("%-4s %-8s %-28s %s" % (r["id"], r["status"], r["title"][:26],
                                              r.get("evidence_path") or "-"))
    elif args.cmd == "add":
        print(json.dumps(add(args.id, args.title, args.question, args.data, args.method), ensure_ascii=False))
    elif args.cmd == "update":
        print(json.dumps(update(args.id, status=args.status, conclusion=args.conclusion,
                                evidence_path=args.evidence, owner=args.owner), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
