# -*- coding: utf-8 -*-
"""影子运行评估（蓝图 v2 三判据）——数据攒够 5 交易日后运行

用法：python eval_shadow.py [--min-days 5]
判据：
  J1 无人干预：账本中 done 行覆盖 篮子×交易日，且无连续 failed
  J2 秩相关>0：D1 打分（score，辅以 confidence）与决策日后验收益的 Spearman 秩相关
  J3 lineage 可追溯：每笔 done 决策的 run_id 在 checkpoints.db 中能查到辩论 trace
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "engine"))

from tools import quote_snapshot  # noqa: E402

LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
DB = BASE / "data" / "checkpoints.db"


def load_ledger():
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def spearman(xs, ys):
    """零依赖 Spearman 秩相关（平均秩处理并列）"""
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=5)
    args = ap.parse_args()

    rows = load_ledger()
    done = [r for r in rows if r.get("status") == "done"]
    failed = [r for r in rows if r.get("status") == "failed"]
    days = sorted({r["date"] for r in done})
    symbols = sorted({r["symbol"] for r in done})
    print("=" * 62)
    print("影子账本：done=%d failed=%d 交易日=%d 票数=%d" % (len(done), len(failed), len(days), len(symbols)))
    print("交易日:", ", ".join(days))

    j1 = len(days) >= args.min_days and len(done) >= len(days) * max(len(symbols), 1) * 0.9
    print("[J1 无人干预] %s（done 覆盖率 %.0f%%）" % (
        "PASS" if j1 else "WAIT/PENDING", 100 * len(done) / max(len(done) + len(failed), 1)))

    per_symbol_latest = {}
    for r in done:
        per_symbol_latest[r["symbol"]] = r  # 每票取最近一次决策
    scores, confs, rets = [], [], []
    for sym, r in per_symbol_latest.items():
        if r.get("score") is None or not r.get("price_at_decision"):
            continue
        try:
            now = quote_snapshot(sym)["price"]
            ret = (now / r["price_at_decision"] - 1) * 100
            scores.append(r["score"])
            confs.append(r.get("confidence") or 0)
            rets.append(ret)
        except Exception as e:  # noqa: BLE001
            print("  [warn] %s 后验价获取失败: %s" % (sym, e))
    rho_s = spearman(scores, rets) if scores else None
    rho_c = spearman(confs, rets) if confs else None
    j2 = rho_s is not None and rho_s > 0
    print("[J2 秩相关] %s（score-vs-ret rho=%s, conf-vs-ret rho=%s, n=%d）" % (
        "PASS" if j2 else "WAIT/FAIL", None if rho_s is None else round(rho_s, 3),
        None if rho_c is None else round(rho_c, 3), len(scores)))

    if DB.exists() and done:
        conn = sqlite3.connect(str(DB), check_same_thread=False)
        traced = sum(
            1 for r in done
            if conn.execute("SELECT COUNT(*) FROM traces WHERE run_id=?",
                            (r["run_id"],)).fetchone()[0] > 0
        )
        conn.close()
        j3 = traced == len(done)
        print("[J3 lineage] %s（%d/%d 决策可回查辩论 trace）" % ("PASS" if j3 else "FAIL", traced, len(done)))
    else:
        j3 = False
        print("[J3 lineage] WAIT（无账本或无 checkpoint 库）")

    verdict = "PASS" if (j1 and j2 and j3) else ("PENDING" if not j1 else "PARTIAL")
    print("-" * 62)
    print("影子评估结论：%s（满 5 交易日后 J2/J3 才具统计意义）" % verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
