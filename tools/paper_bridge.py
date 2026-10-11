# -*- coding: utf-8 -*-
"""paper_bridge：pick_verdicts → paper.db 薄桥（K-STOCK-095，10-16 实弹）。

职责最小化：选票（T1_resonance）+ 幂等（events trace_id + orders ref 双保险）
+ 调现成 paper CLI 落 pending 单。撮合（settle，含涨跌停拦截）、每日 mark、
止损检查全部复用 paper CLI 既有能力，本脚本不做交易逻辑。

口径（K-STOCK-097 必改①）：BUY 一律落 pending 单，plan_date=D+2（决策 asof
后第 2 个交易日），按开盘价撮合——与 pick_verdicts win 口径对齐，不再即时成交。
落 pick_bridge 专用子账本（--account/--strategy），不污染 main 绩效。

用法：
  python tools/paper_bridge.py ingest --date 20261009 [--db PATH] [--dry-run]
  python tools/paper_bridge.py smoke                # 离线 fixture 全链冒烟

幂等：trace_id = pick-{code}-{date}；events 表查重 + orders.ref 引擎级查重
（即使 events 后写窗口期进程崩溃，重复 ingest 也会被 ref 查重拦住）。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "portal"))
import portal_db as pdb  # noqa: E402

PY = sys.executable
CLI = os.path.join(ROOT, "k_stock", "paper", "cli.py")
PV = os.path.join(ROOT, "portal", "data", "pick_verdicts.json")
TIER_KEY = "T1"          # per_code.tier 取值（counts 里叫 T1_resonance）
BUDGET_PER_POS = 20000   # 每票名义仓位（元），10-16 实弹前老田可调
BRIDGE_ACCOUNT = "pick_bridge"    # 专用子账本（v0.3 多账本，不混 main）
BRIDGE_STRATEGY = "pick_bridge"   # orders.strategy 标签


def _paper(*args, db=None):
    cmd = [PY, CLI]
    if db:
        cmd += ["--db", db]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", timeout=60, cwd=ROOT)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _trace_done(trace_id):
    with sqlite3.connect(pdb.DB) as con:
        row = con.execute("SELECT 1 FROM events WHERE trace_id=? AND "
                          "kind='ingested' LIMIT 1", (trace_id,)).fetchone()
    return row is not None


def _mark_ingested(trace_id, payload):
    pdb.add_event(trace_id, "paper", "ingested", payload)


def _lookup_close(code):
    """从 todays_picks.stocks 找 close（找不到返回 None → 用最小 100 股）。"""
    try:
        tp = json.load(open(os.path.join(ROOT, "portal", "data",
                                         "todays_picks.json"),
                            encoding="utf-8"))
        for s in tp.get("stocks", []):
            if s.get("code") == code and s.get("close"):
                return float(s["close"])
    except Exception:
        pass
    return None


def ingest(date=None, db=None, dry_run=False):
    pv = json.load(open(PV, encoding="utf-8"))
    date = date or (pv.get("summary", {}) or {}).get("asof")
    tickets = [(c, r) for c, r in pv.get("per_code", {}).items()
               if r.get("tier") == TIER_KEY and r.get("date") == date]
    print("ingest asof=%s T1 票 %d" % (date, len(tickets)))
    opened, skipped = [], []
    for code, rec in tickets:
        trace_id = "pick-%s-%s" % (code, date)
        if _trace_done(trace_id):
            skipped.append(code)
            print("  ↷ %s 已入过（幂等跳过）" % code)
            continue
        if dry_run:
            print("  [dry] %s %s → BUY %s 开盘 pending，名义 ¥%d" % (
                code, rec.get("name", ""), "D+2", BUDGET_PER_POS))
            opened.append(code)
            continue
        price = _lookup_close(code)
        if price:
            qty = max(100, int(BUDGET_PER_POS / price // 100) * 100)
        else:
            qty = 100  # 无价参考时最小单（A 股一手口径，CLI 强制 100 整数倍）
        # pending 单：plan_date=d2（asof 后第 2 个交易日）开盘撮合，
        # ref=trace_id 引擎级幂等，落 pick_bridge 子账本。
        rc, out = _paper("order", "--side", "BUY", "--stock", code,
                         "--qty", str(qty), "--plan-date", "d2", "--asof", date,
                         "--ref", trace_id,
                         "--strategy", BRIDGE_STRATEGY,
                         "--account", BRIDGE_ACCOUNT, "--json", db=db)
        if rc != 0:
            print("  ❌ %s 落 pending 单失败 rc=%d：%s" % (code, rc, out[-120:]))
            continue
        _mark_ingested(trace_id, {"code": code, "name": rec.get("name"),
                                  "qty": qty, "plan_date": "d2",
                                  "strategies": rec.get("strategies")})
        opened.append(code)
        print("  ✅ %s pending 单已落（D+2 开盘）" % code)
    return {"date": date, "tickets": len(tickets), "opened": opened,
            "skipped": skipped}


def smoke():
    """离线 fixture 冒烟：临时 db + 假 events 表，验幂等、dry-run 与真单 pending 链。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="paper_bridge_smoke_")
    db = os.path.join(tmp, "paper.db")
    pdb.DB = os.path.join(tmp, "portal_events.db")
    with sqlite3.connect(pdb.DB) as con:
        con.executescript(pdb.SCHEMA)
    r1 = ingest(db=db, dry_run=True)
    assert r1["tickets"] >= 1, "fixture 里应有 T1 票"
    print("smoke: dry-run 票数 =", len(r1["opened"]))
    # 真单链（临时 db，不碰真库）：显式 plan-date 避免依赖行情湖，
    # 验 pending 落库 + ref 幂等（重复下单返回 skipped 不重复插入）。
    first = r1["opened"][0]
    rc, out = _paper("order", "--side", "BUY", "--stock", first, "--qty", "100",
                     "--plan-date", "20261019", "--ref", "smoke-ref-1",
                     "--strategy", BRIDGE_STRATEGY, "--account", BRIDGE_ACCOUNT,
                     "--json", db=db)
    assert rc == 0, "真单落 pending 失败: %s" % out[-200:]
    assert json.loads(out.splitlines()[-1])["order"]["status"] == "pending"
    rc, out = _paper("pending", "--account", BRIDGE_ACCOUNT, db=db)
    assert rc == 0 and "smoke-ref-1" not in out, out  # pending 展示不含 ref，仅验非空
    assert "待撮合 1 单" in out, "pending 列表应恰有 1 单: %s" % out
    rc, out = _paper("order", "--side", "BUY", "--stock", first, "--qty", "100",
                     "--plan-date", "20261019", "--ref", "smoke-ref-1",
                     "--strategy", BRIDGE_STRATEGY, "--account", BRIDGE_ACCOUNT,
                     "--json", db=db)
    assert rc == 0, "幂等重放不应报错: %s" % out[-200:]
    assert json.loads(out.splitlines()[-1])["order"]["status"] == "skipped", \
        "重复 ref 应幂等跳过"
    print("smoke: 真单 pending 落库 + ref 幂等 ✓")
    print("SMOKE PASS（离线；实弹需 10-16 开闸后人工窗口跑 ingest 真单）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("ingest")
    p1.add_argument("--date")
    p1.add_argument("--db")
    p1.add_argument("--dry-run", action="store_true")
    sub.add_parser("smoke")
    a = ap.parse_args()
    if a.cmd == "ingest":
        print(json.dumps(ingest(a.date, a.db, a.dry_run),
                         ensure_ascii=False))
    else:
        smoke()


if __name__ == "__main__":
    main()
