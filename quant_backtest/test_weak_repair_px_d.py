# -*- coding: utf-8 -*-
"""px_d 休市日修复工具验证（R1-R6 · 可重复跑）。

锁住三件事：
  * dry-run 不写盘、不改文件 mtime 语义（本次用内容比对）；
  * --apply 逐行改 px_d → 真实最后交易日，原值进 px_d_fix 留痕；
  * 其它字段（ts/px/ex250/trig）一律不动，坏行不丢。

全部 PASS 退出码 0。
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cn_trading_days as ctd  # noqa: E402
import weak_track_repair_px_d as rp  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % ("PASS" if cond else "FAIL", name, "｜" + detail if detail and not cond else "")
    )
    if not cond:
        fails.append(name)


def main():
    tmp = tempfile.mkdtemp(prefix="weak_repair_")
    fp = os.path.join(tmp, "weak_track_999991.closed_20261020.jsonl")
    rows = [
        {"code": "999991.SZ", "ts": "2026-09-30T15:00:00", "px_d": "2026-09-30", "px": 10.0},
        {"code": "999991.SZ", "ts": "2026-10-01T15:00:00", "px_d": "2026-10-02", "px": 10.0},
        {"code": "999991.SZ", "ts": "2026-10-05T15:00:00", "px_d": "2026-10-02", "px": 10.0},
        {"code": "999991.SZ", "ts": "2026-10-08T15:00:00", "px_d": "2026-10-08", "px": 10.4},
    ]
    with open(fp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write("{坏行不是 json\n")

    before = open(fp, encoding="utf-8").read()

    # --- R1 dry-run 记数但不写盘 ---
    res = rp._fix_one(fp, False)
    check("R1 dry-run 计 2 行", res["fixed"] == 2, str(res["fixed"]))
    check("R1 dry-run 不改文件", open(fp, encoding="utf-8").read() == before)
    check(
        "R1 映射 10-02→09-30",
        res["mapping"] == {"2026-10-02→2026-09-30": 2},
        str(res["mapping"]),
    )

    # --- R2 apply 写盘 + 备份 ---
    res = rp._fix_one(fp, True)
    after = open(fp, encoding="utf-8").read()
    check("R2 apply 后内容变化", after != before)
    baks = [x for x in os.listdir(tmp) if ".bak-" in x]
    check("R2 已留备份", len(baks) == 1, str(baks))
    check("R2 备份是改前内容", open(os.path.join(tmp, baks[0]), encoding="utf-8").read() == before)

    # --- R3 px_d 已改、其它字段未动、坏行仍在 ---
    lines = [l for l in after.splitlines() if l.strip()]
    got = [json.loads(l) for l in lines[:4]]
    check("R3 休市行改 09-30", [r["px_d"] for r in got[1:3]] == ["2026-09-30"] * 2, str([r["px_d"] for r in got]))
    check("R3 ts 未动", got[1]["ts"] == "2026-10-01T15:00:00", got[1]["ts"])
    check("R3 px 未动", [r["px"] for r in got] == [10.0, 10.0, 10.0, 10.4], str([r["px"] for r in got]))
    check(
        "R3 留痕 px_d_fix",
        got[1].get("px_d_fix") == {"from": "2026-10-02", "to": "2026-09-30", "rule": "prev_trading_day"},
        str(got[1].get("px_d_fix")),
    )
    check("R3 坏行保留", "{坏行不是 json" in after.splitlines()[4])

    # --- R4 改后自检归零 ---
    check("R4 非交易日清零", rp._rescan(fp) == 0, str(rp._rescan(fp)))

    # --- R5 幂等：再跑一次不再改 ---
    check("R5 幂等无改动", rp._fix_one(fp, True)["fixed"] == 0)

    # --- R6 日历未覆盖年份不出手（防误杀）---
    # 快照已覆盖 2027，故关掉快照模拟真正的降级态（快照缺失 + 该年未手写）。
    fp2 = os.path.join(tmp, "weak_track_999990.jsonl")
    with open(fp2, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2027-10-04T15:00:00", "px_d": "2027-10-04"}) + "\n")
    with ctd.snapshot_off():
        check(
            "R6 未覆盖年份不误杀",
            rp._fix_one(fp2, False)["fixed"] == 0 and ctd.covered(2027) is False,
        )

    for p in os.listdir(tmp):
        os.remove(os.path.join(tmp, p))
    os.rmdir(tmp)

    print("\n%s（%d 项失败）" % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
