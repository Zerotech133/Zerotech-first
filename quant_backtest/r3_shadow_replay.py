# -*- coding: utf-8 -*-
"""R3 绝对数闸 5→8 影子期只读对账（WO-QT-20261020-066）。

依据：deliverables/2026-10-20_R3绝对数闸5to8影子校准准备件_WO-QT-20261020-023.md
§三（影子期 14 天：2026-10-21 起算，第 7 日=10-28 中间复核，第 14 日=11-03
出影子期小结）。

定位：shadow（只记录不拦截）。生产 params.yaml 的 riskguard_bear_min_n
保持 5 不动；本脚本只**重放**账本已入账的 R3 判定在 min_n=8 下会怎样拦截，
输出与现行 5 档实录的对比，**零写入**（不写账本、不写 params.yaml、不触
kg_riskguard.enforce_allowed()）。

读账口径：
  · 账本：zerobound-kg/riskguard_ledger/ledger.jsonl（append-only），
    R3 行 reason 内嵌判定输入："bear事件{a}+矛盾边{b}={n} 密度{d}/日
    (阈{dt}中|未中) 绝对数闸(阈{m}中|未中)"；
  · 5 档实录 = 账本 action（shadow_block=拦 / pass=放）；
  · 8 档重放 = 密度闸（原样不动）or n_tot >= 8；
  · delta：both_block / both_pass / only_n5（收窄样本）/ only_n8（不应
    出现，出现即账本-重放不一致）；
  · 自洽检查：按 reason 重放 5 档与实录 action 不符 → replay_mismatch
    （每日读账须为 0）。

用法：
  python r3_shadow_replay.py                    # 人类可读小结（全量账本）
  python r3_shadow_replay.py --since 2026-10-21 --until 2026-10-28
  python r3_shadow_replay.py --json             # 机器可读
  环境变量 R3_SHADOW_LEDGER 可覆盖默认账本路径。
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_LEDGER = os.environ.get("R3_SHADOW_LEDGER") or os.path.join(
    HERE, os.pardir, os.pardir, "zerobound-kg", "riskguard_ledger", "ledger.jsonl"
)

R3_RULE_ID = "R3-event-risk"
SHADOW_MIN_N = 8  # 影子候选档（绝不写生产）
NARROWING_EXPECTED = 0.64  # WS-RG §3.3 回放预期收窄量级（~64%）

# reason 解析：bear事件{a}+矛盾边{b}={n} 密度{d}/日(阈{dt}中|未中) 绝对数闸(阈{m}中|未中)
RE_R3_REASON = re.compile(
    r"bear事件(\d+)\+矛盾边(\d+)=(\d+)\s+密度([\d.]+)/日"
    r"\(阈([\d.]+)(中|未中)\)\s+绝对数闸\(阈(\d+)(中|未中)\)"
)


def parse_r3_reason(reason):
    """R3 reason → 判定输入 dict；不可重放行（无数据/UNKNOWN/基数守卫）
    返回 None（如实归类 not_evaluated，不造数）。"""
    if not reason:
        return None
    m = RE_R3_REASON.search(reason)
    if not m:
        return None
    a, b, n, d, dt, d_hit, mn, n_hit = m.groups()
    return {
        "n_bear": int(a),
        "n_con": int(b),
        "n_tot": int(n),
        "density": float(d),
        "density_th": float(dt),
        "hit_d": d_hit == "中",
        "recorded_min_n": int(mn),
        "hit_n": n_hit == "中",
    }


def _recorded_block(action):
    return action in ("shadow_block", "enforce_block")


def replay_row(row, shadow_min_n=SHADOW_MIN_N):
    """单行重放：5 档实录 vs 8 档假想。非 R3 行返回 None。"""
    if row.get("rule_id") != R3_RULE_ID:
        return None
    base = {
        "ts": row.get("ts"),
        "security": row.get("security"),
        "action": row.get("action"),
    }
    parsed = parse_r3_reason(row.get("reason"))
    if parsed is None:
        return dict(
            base,
            status="not_evaluated",
            delta=None,
            block_n5=None,
            block_n8=None,
            replay_mismatch=False,
        )
    block_n5 = parsed["hit_d"] or (parsed["n_tot"] >= parsed["recorded_min_n"])
    block_n8 = parsed["hit_d"] or (parsed["n_tot"] >= shadow_min_n)
    if block_n5 and block_n8:
        delta = "both_block"
    elif not block_n5 and not block_n8:
        delta = "both_pass"
    elif block_n5:
        delta = "only_n5"  # 5→8 收窄样本
    else:
        delta = "only_n8"  # 8>5 不应收窄反扩 → 不一致
    return dict(
        base,
        status="evaluated",
        delta=delta,
        n_tot=parsed["n_tot"],
        density=parsed["density"],
        hit_d=parsed["hit_d"],
        block_n5=block_n5,
        block_n8=block_n8,
        replay_mismatch=(block_n5 != _recorded_block(row.get("action"))),
    )


def aggregate(replays, since=None, until=None):
    """重放结果汇总（观测项：行数/拦截率/收窄幅度/自洽）。"""
    rows = [r for r in replays if r is not None]
    if since:
        rows = [r for r in rows if (r["ts"] or "")[:10] >= since]
    if until:
        rows = [r for r in rows if (r["ts"] or "")[:10] <= until]
    ev = [r for r in rows if r["status"] == "evaluated"]
    s = {
        "total_r3_rows": len(rows),
        "evaluated": len(ev),
        "not_evaluated": len(rows) - len(ev),
        "block_n5": sum(1 for r in ev if r["block_n5"]),
        "block_n8": sum(1 for r in ev if r["block_n8"]),
        "only_n5": sum(1 for r in ev if r["delta"] == "only_n5"),
        "only_n8": sum(1 for r in ev if r["delta"] == "only_n8"),
        "mismatch": sum(1 for r in ev if r["replay_mismatch"]),
        "narrowing_expected": NARROWING_EXPECTED,
    }
    s["intercept_rate_n5"] = round(s["block_n5"] / len(ev), 4) if ev else None
    s["intercept_rate_n8"] = round(s["block_n8"] / len(ev), 4) if ev else None
    s["narrowing"] = (
        round(1 - s["block_n8"] / s["block_n5"], 4) if s["block_n5"] else None
    )
    by_day = {}
    for r in ev:
        d = (r["ts"] or "")[:10]
        day = by_day.setdefault(
            d, {"evaluated": 0, "block_n5": 0, "block_n8": 0, "only_n5": 0}
        )
        day["evaluated"] += 1
        day["block_n5"] += 1 if r["block_n5"] else 0
        day["block_n8"] += 1 if r["block_n8"] else 0
        day["only_n5"] += 1 if r["delta"] == "only_n5" else 0
    s["by_day"] = {k: by_day[k] for k in sorted(by_day)}
    return s


def _print_human(s, ledger):
    print("=== R3 绝对数闸 5→8 影子对账（只读）===")
    print("账本: %s" % os.path.normpath(ledger))
    print(
        "R3 行: %d（其中可重放 %d，不可重放/不评 %d）"
        % (s["total_r3_rows"], s["evaluated"], s["not_evaluated"])
    )
    if s["evaluated"] == 0:
        print("（窗口内无可重放 R3 行）")
        return
    print(
        "拦截(min_n=5 实录): %d（拦截率 %.2f%%）"
        % (s["block_n5"], (s["intercept_rate_n5"] or 0) * 100)
    )
    print(
        "假想(min_n=8 重放): %d（拦截率 %.2f%%）"
        % (s["block_n8"], (s["intercept_rate_n8"] or 0) * 100)
    )
    print(
        "收窄样本 only_n5: %d ｜ 反扩 only_n8: %d（应为 0）"
        % (s["only_n5"], s["only_n8"])
    )
    if s["narrowing"] is not None:
        print(
            "拦截面收窄: %.2f%%（回放预期 ~%.0f%%）"
            % (s["narrowing"] * 100, s["narrowing_expected"] * 100)
        )
    print("账本-重放自洽 mismatch: %d（每日读账应为 0）" % s["mismatch"])
    for d, day in s["by_day"].items():
        print(
            "  %s 评%d 5档拦%d 8档拦%d 收窄%d"
            % (d, day["evaluated"], day["block_n5"], day["block_n8"], day["only_n5"])
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="R3 绝对数闸 5→8 影子期只读对账（零写入）")
    ap.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER,
        help="ledger.jsonl 路径（默认 zerobound-kg 影子账本）",
    )
    ap.add_argument("--since", default=None, help="起始日 YYYY-MM-DD（含）")
    ap.add_argument("--until", default=None, help="截止日 YYYY-MM-DD（含）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    ledger = os.path.normpath(os.path.abspath(args.ledger))
    if not os.path.isfile(ledger):
        sys.stderr.write("[r3-shadow] 账本不存在: %s\n" % ledger)
        return 1
    replays = []
    with open(ledger, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 坏行跳过（只读对账不修复）
            rep = replay_row(row)
            if rep is not None:
                replays.append(rep)
    s = aggregate(replays, since=args.since, until=args.until)
    if args.json:
        s["ledger"] = ledger
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        _print_human(s, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
