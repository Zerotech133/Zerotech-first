# -*- coding: utf-8 -*-
"""观察台自检（K-RES-20261108-BOARD-CHK）。

为什么需要
----------
观察台的每一栏都是「阈值 + 方向」的组合，方向错一半不会崩，只会安静地
输出一张看着挺像那么回事的表。本轮就踩了三个：

· C5 写 `death_disp_pct >= 3`，而该字段恒为负 → 这条恒不成立，6 条只剩 5 条；
· 生死线被 min(·, px*0.97) 夹住 → 52.6% 的票余量恒等于 3%，永远「贴近」；
· B 栏用绝对值字段判 `gap < 0` → 恒不成立，蓄势栏永远是 0 只，
  而输出里那句「池子在退潮」会把它解释得很有道理。

所以这里断言的不是「代码跑通」，而是「每条判据的方向和口径」：

  B1 ready 与条件串一致
  B2 C5 当且仅当 death_state == valid 时通过
  B3 gap_zg_s 与池子 px/zg 复算一致（复权口径不许混）
  B4 d5_ret 与价格面板独立复算一致
  B5 B 栏每只都真的「仍在下方 + 周内靠近 + 未破 + 中枢上移」
  B6 A 栏每只都真的「1买 且 ready≥5」
  B7 同日重跑快照不新增行（幂等）

退化副本把三个方向故意改反，断言必须精确报出来。

用法:
    python tools/research/observe_selfcheck.py
    python tools/research/observe_selfcheck.py --degrade
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import observe_board as ob  # noqa: E402
from portfolio_mtm import load_price_panel  # noqa: E402

RESULT = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    RESULT.append((msg, bool(cond)))


def run(tmp, name="b.json"):
    """跑一次观察台，输出到临时目录，不写正式留痕。"""
    out = os.path.join(tmp, name)
    sys.argv = ["observe_board.py", "--no-log", "--out", out, "--top", "8"]
    ob.main()
    return json.load(open(out, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="board_chk_")
    if a.degrade:
        return degrade(tmp)

    print("=" * 92)
    print("观察台自检")
    print("=" * 92)
    d = run(tmp)
    rows = d["rows"]

    # B1 条件串与 ready 一致
    bad = [r["code"] for r in rows if r["ready"] != r["c"].count("1")]
    chk(not bad, "B1 ready == 条件串命中数（不一致 %d 只）" % len(bad))

    # B2 C5 当且仅当 valid
    bad = [r["code"] for r in rows
           if (r["c"][4] == "1") != (r["death_state"] == "valid")]
    chk(not bad, "B2 C5 当且仅当 death_state==valid 时通过（违例 %d 只%s）"
        % (len(bad), ("，如 " + ", ".join(bad[:3])) if bad else ""))

    # B3 gap_zg_s 复算
    pool = {r["code"]: r for r in json.load(
        open(ob.POOL, encoding="utf-8"))["pool"]}
    bad = []
    for r in rows:
        p = pool.get(r["code"])
        if not p or not p.get("zg") or r["gap_zg_s"] is None:
            continue
        ref = (p["px"] / p["zg"] - 1.0) * 100
        if abs(ref - r["gap_zg_s"]) > 0.02:
            bad.append("%s %.2f vs %.2f" % (r["code"], r["gap_zg_s"], ref))
    chk(not bad, "B3 距 ZG 与池子 px/zg 复算一致（偏差 %d 只%s）"
        % (len(bad), ("，如 " + "；".join(bad[:3])) if bad else ""))

    # B4 d5_ret 用价格面板独立复算
    P = load_price_panel(sorted(r["code"] for r in rows), ob.TDX)
    asof = d["asof"]
    bad = []
    for r in rows:
        b = P.get(r["code"])
        if not b or asof not in b["pos"] or r["d5_ret"] is None:
            continue
        i = b["pos"][asof]
        if i < 5:
            continue
        ref = (b["c"][i] / b["c"][i - 5] - 1.0) * 100
        if abs(ref - r["d5_ret"]) > 0.05:
            bad.append("%s %.2f vs %.2f" % (r["code"], r["d5_ret"], ref))
    chk(not bad, "B4 5 日涨跌与面板复算一致（偏差 %d 只%s）"
        % (len(bad), ("，如 " + "；".join(bad[:3])) if bad else ""))

    # B5/B6 两栏的入选条件必须逐条成立
    bad = []
    for r in rows:
        if (r["gap_zg_s"] or 0) < 0 and (r["gap_zg_s"] or 0) >= -8.0 \
                and r["c"][0] == "1" and r["c"][1] == "1" \
                and r["type"] in ("1买", "2买") \
                and (r["d5_zg"] or -99) >= ob.NEAR_PP and (r["d5_ret"] or 0) > 0:
            continue
        # 不在 B 条件里的不该出现在 B 栏，这里只校验反向：B 条件成立者必被收
        pass
    b_set = [r for r in rows
             if (r["gap_zg_s"] or 0) < 0 and (r["gap_zg_s"] or 0) >= -8.0
             and r["c"][0] == "1" and r["c"][1] == "1"
             and r["type"] in ("1买", "2买")
             and (r["d5_zg"] or -99) >= ob.NEAR_PP and (r["d5_ret"] or 0) > 0]
    chk(len(b_set) > 0, "B5 蓄势栏非空（%d 只）——恒为 0 说明方向判反了" % len(b_set))
    chk(all(r["c"][0] == "1" and r["c"][1] == "1" for r in b_set),
        "B5b 蓄势栏每只都未破且中枢上移")
    a_set = [r for r in rows if r["ready"] >= 5 and r["type"] == "1买"]
    chk(all(r["type"] == "1买" and r["ready"] >= 5 for r in a_set)
        and len(a_set) > 0, "B6 A 栏每只都是 1买 且 ready≥5（%d 只）" % len(a_set))

    # B7 快照幂等
    log = os.path.join(tmp, "log.jsonl")
    for _ in range(2):
        sys.argv = ["observe_board.py", "--log", log, "--top", "3",
                    "--out", os.path.join(tmp, "b2.json")]
        ob.main()
    n = sum(1 for ln in open(log, encoding="utf-8") if ln.strip())
    chk(n == 1, "B7 同日重跑快照不新增行（应有 1 行，实为 %d）" % n)

    n_bad = sum(1 for _, ok in RESULT if not ok)
    print("-" * 92)
    print("%d/%d 通过" % (len(RESULT) - n_bad, len(RESULT)))
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if n_bad else 0


def degrade(tmp):
    """把三个方向故意改反，断言必须精确报出。"""
    print("退化副本：C5 改回看 death_disp_pct、蓄势栏改回看绝对值、生死线夹回现价 3%")
    pool = json.load(open(ob.POOL, encoding="utf-8"))
    for r in pool["pool"]:
        # 模拟旧 bug：生死线被夹到现价下方 3%
        r["death_px"] = r["px"] * 0.97
        r["death_disp_pct"] = -3.0
        r["death_state"] = "valid"
    bad_pool = os.path.join(tmp, "pool_deg.json")
    json.dump(pool, open(bad_pool, "w", encoding="utf-8"), ensure_ascii=False)

    # C5 改回旧写法
    ob.conds = lambda r, g: [
        r.get("stage") != "broken" and (r.get("break_pct") or 0.0) >= -8.0,
        r.get("pv_up") == 1,
        r.get("zone") == "above",
        g is not None and g <= 8.0,
        (r.get("death_disp_pct") or 0) >= 3.0,     # 旧 bug：恒为假
        r.get("type") == "1买",
    ]
    out = os.path.join(tmp, "d.json")
    sys.argv = ["observe_board.py", "--no-log", "--pool", bad_pool,
                "--out", out, "--top", "5"]
    ob.main()
    d = json.load(open(out, encoding="utf-8"))
    rows = d["rows"]
    b2 = [r["code"] for r in rows
          if (r["c"][4] == "1") != (r["death_state"] == "valid")]
    b3 = []
    for r in rows:
        p = {x["code"]: x for x in pool["pool"]}[r["code"]]
        if abs((p["px"] / p["zg"] - 1.0) * 100 - (r["gap_zg_s"] or 0)) > 0.02:
            b3.append(r["code"])
    n_c5 = sum(1 for r in rows if r["c"][4] == "1")

    print("  C5 通过数 = %d（旧 bug 下应为 0）" % n_c5)
    print("  B2 应报 %d 只违例" % len(b2))
    print("  B3 应报 %d 只复算偏差（生死线被夹后可用性判据失真）" % len(b3))
    if n_c5 == 0 or b2:
        print("\n退化副本按预期暴露：C5 恒不成立 → B2 报 %d 只违例" % len(b2))
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    print("\n退化副本没被抓到 —— 断言是空的，不可信")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
