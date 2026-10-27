# -*- coding: utf-8 -*-
"""辩论回放 CLI（K-STOCK-007）：lineage 人工抽查工具，三判据 J3 的配套

用法：
    python replay.py <run_id>            # 各节点结论
    python replay.py <run_id> --trace    # 加 trace 时间线（每轮发言/相似度/延迟）
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).parent
DB = BASE / "data" / "checkpoints.db"


def main():
    ap = argparse.ArgumentParser(description="投研部辩论回放")
    ap.add_argument("run_id", help="账本里的 run_id（如 e1d80bc78cfb）")
    ap.add_argument("--trace", action="store_true", help="输出 trace 时间线")
    args = ap.parse_args()

    if not DB.exists():
        print("无 checkpoint 库：%s" % DB)
        sys.exit(1)
    conn = sqlite3.connect(str(DB), check_same_thread=False)

    run = conn.execute(
        "SELECT stock, status, started_at, finished_at FROM runs WHERE run_id=?",
        (args.run_id,),
    ).fetchone()
    if not run:
        print("run_id 不存在：%s" % args.run_id)
        sys.exit(1)
    stock, status, t0, t1 = run
    dur = ("%.1fs" % (t1 - t0)) if t1 else "未结束"
    print("=" * 62)
    print("run %s | 标的 %s | 状态 %s | 耗时 %s" % (args.run_id, stock, status, dur))
    print("=" * 62)

    for nk, aid, sj in conn.execute(
        "SELECT node_key, agent_id, state_json FROM checkpoints "
        "WHERE run_id=? ORDER BY node_key",
        (args.run_id,),
    ):
        st = json.loads(sj)
        body = st.get("merged") or st.get("output", "")
        meta = ""
        if "rounds" in st:
            meta = "（辩论 %s 轮，末轮相似度 %s）" % (st["rounds"], st.get("sim"))
        print("\n[%s] %s %s" % (aid, nk, meta))
        print("  " + body[:500].replace("\n", " "))

    if args.trace:
        print("\n" + "=" * 62)
        print("trace 时间线：")
        rows = conn.execute(
            "SELECT agent_id, phase, round, similarity, status, latency_ms, output "
            "FROM traces WHERE run_id=? ORDER BY ts",
            (args.run_id,),
        ).fetchall()
        for aid, ph, rd, sim, st, lat, out in rows:
            simtxt = (" sim=%.2f" % sim) if sim is not None else ""
            print(
                "[%s] %-16s r%s%s (%s, %.0fms): %s"
                % (
                    aid,
                    ph,
                    rd,
                    simtxt,
                    st,
                    lat or 0,
                    (out or "")[:110].replace("\n", " "),
                )
            )
        if not rows:
            print("  （该 run 无 trace 记录）")


if __name__ == "__main__":
    main()
