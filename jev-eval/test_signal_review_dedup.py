# -*- coding: utf-8 -*-
# K-STOCK-402 守卫测试：weekdays_after 矩阵 + dedup_triples 语义 + BUG-4 场景复现
#
# D4 修复（2026-10-18）：原为脚本式测试——主体在模块级直接执行并以
# sys.exit(1 if FAIL else 0) 收尾，全仓 pytest 收集期 import 即抛
# SystemExit → INTERNALERROR。现改为：主体包进 _run_script()
# （收集期零执行），pytest 经 test_signal_review_dedup() 驱动；
# 脚本单跑能力保留（python test_signal_review_dedup.py 仍以退出码报告结果）。
import json
import os
import sys
from datetime import date

# K-STOCK-406：路径锚相对测试文件定位（worktree 开发期/merge 后主仓均跑得动；
# 原 .worktrees/K-STOCK-402 硬编码在 worktree 清场后失效）
WT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_DIR = os.path.join(WT, "jev-eval")


def _run_script():
    """执行原脚本主体；返回失败项数量（0=全过）。"""
    sys.path.insert(0, JEV_DIR)
    old_cwd = os.getcwd()
    os.chdir(JEV_DIR)
    FAIL = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            FAIL.append(name)

    try:
        # 此前 signal_review_gate 顶部常量已可 import（不触发 DB 连接的纯函数安全）
        from signal_review_gate import weekdays_after, dedup_triples, N_OUTCOME_DAYS

        # ── weekdays_after 矩阵（2026-10 历法：10-09 周五, 10-12 周一, 10-14 周三）──
        W = weekdays_after
        check("周五→下周一 =1", W(date(2026, 10, 9), date(2026, 10, 12)) == 1)
        check("周五→下周三 =3", W(date(2026, 10, 9), date(2026, 10, 14)) == 3)
        check("周五→下周四 =4", W(date(2026, 10, 9), date(2026, 10, 15)) == 4)
        check("周五→下周五 =5（满窗即 due）", W(date(2026, 10, 9), date(2026, 10, 16)) == 5)
        check("周六→下周一 =1", W(date(2026, 10, 10), date(2026, 10, 12)) == 1)
        check("同日 =0", W(date(2026, 10, 14), date(2026, 10, 14)) == 0)
        check("整四周 =20", W(date(2026, 10, 12), date(2026, 11, 9)) == 20)
        check("周一→下周一 =5", W(date(2026, 10, 12), date(2026, 10, 19)) == 5)

        # ── BUG-4 场景复现：周五池 + 下周三 → 旧日历口径 due / 新交易日口径 not due ──
        pool_fri, today_wed = date(2026, 10, 9), date(2026, 10, 14)
        old_due = pool_fri <= today_wed - __import__("datetime").timedelta(days=N_OUTCOME_DAYS)
        new_due = W(pool_fri, today_wed) >= N_OUTCOME_DAYS
        check("BUG-4 场景：旧口径误判 due", old_due is True)
        check("BUG-4 场景：新口径正确 not-due", new_due is False)

        # ── dedup_triples ──────────────────────────────────────────
        lines = [
            json.dumps({"candidate_id": "c1", "decision": "PASS", "ret_nd": 0.01}),
            json.dumps({"candidate_id": "c2", "decision": "FOCUS", "ret_nd": 0.02}),
            json.dumps({"candidate_id": "c1", "decision": "PASS", "ret_nd": 0.015}),  # 崩溃窗口重复
            "",
            json.dumps({"candidate_id": "c2", "decision": "FOCUS", "ret_nd": 0.025}),  # 重复取末次
            "{bad json",  # 坏行保留
        ]
        out = dedup_triples(lines)
        recs = [json.loads(l) for l in out if not l.startswith("{bad")]
        check("去重后行数 =3（c1+c2+坏行；空行丢弃）", len(out) == 3)
        check("c1 取末次 ret=0.015", any(r["candidate_id"] == "c1" and r["ret_nd"] == 0.015 for r in recs if "candidate_id" in r))
        check("c2 取末次 ret=0.025", any(r["candidate_id"] == "c2" and r["ret_nd"] == 0.025 for r in recs if "candidate_id" in r))
        check("坏行保留", any(l == "{bad json" for l in out))
        check("报告计数不虚增（c2 只计 1 次）",
              sum(1 for r in recs if r.get("candidate_id") == "c2") == 1)
    finally:
        os.chdir(old_cwd)

    print("FAILURES: %d" % len(FAIL))
    return len(FAIL)


def test_signal_review_dedup():
    """pytest 入口：脚本主体零失败才通过（收集期零执行）。"""
    assert _run_script() == 0, "K-STOCK-402 守卫存在 FAIL 项，见上方 PASS/FAIL 明细"


if __name__ == "__main__":
    sys.exit(1 if _run_script() else 0)
