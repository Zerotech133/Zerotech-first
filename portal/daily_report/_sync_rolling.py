# -*- coding: utf-8 -*-
"""把 status_latest.json 的 rolling 字段按最新 cohort_track.rolling_alpha() 重算同步。

用途：改了降级线口径文案后，不必重跑 4.5 分钟的 snap（snap 要拉全市场），
只刷新这一个字段。键集必须完全一致才写，防止结构被改坏。
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cohort_track as ct  # noqa: E402

FP = os.path.join(ct.COHORT_DIR, "status_latest.json")


def main() -> int:
    d = json.load(io.open(FP, encoding="utf-8"))
    old = d.get("rolling") or {}
    new = ct.rolling_alpha()
    if set(old) != set(new):
        print("[REFUSE] 键集不一致 old=%s new=%s" % (sorted(old), sorted(new)))
        return 2
    before = json.dumps(old, ensure_ascii=False, sort_keys=True)
    after = json.dumps(new, ensure_ascii=False, sort_keys=True)
    if before == after:
        print("[SKIP] 内容无变化")
        return 0
    d["rolling"] = new
    with io.open(FP, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    print("[OK] rolling 已更新")
    print("  _degrade:", json.dumps(new.get("_degrade"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
