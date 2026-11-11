# -*- coding: utf-8 -*-
"""把 arena/latest.json 注入 arena_template.html → paper_arena.html

规则同 build_houfeng.py：
  - 数据缺失不静默沿用旧数字，页面显式说「没接上」；
  - 模板不写死任何会随日期变化的数字；
  - 写完自检：关键数字必须能在页面里找到，防注入失败出空页。

用法： python build_arena.py
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARENA = os.path.join(HERE, "arena", "latest.json")
TEMPLATE = os.path.join(HERE, "arena_template.html")
OUT = os.path.join(HERE, "paper_arena.html")

ANCHOR = "/*__DATA__*/ null"


def build_data() -> dict:
    if not os.path.exists(ARENA):
        return None
    r = json.load(io.open(ARENA, encoding="utf-8"))
    matrix = {}
    for sid, tracks in (r.get("tracks") or {}).items():
        matrix[sid] = {t["sig"]: t for t in tracks}
    return {
        "generated_at": r.get("generated_at"),
        "cal_first": r.get("cal_first"),
        "cal_last": r.get("cal_last"),
        "n_days": len(r.get("cal") or []),
        "n_signals": r.get("n_signals"),
        "capital": r.get("capital"),
        "cost_one_side": r.get("cost_one_side"),
        "cost_round": r.get("cost_round"),
        "min_obs": r.get("min_obs"),
        "signals": r.get("signals") or [],
        "strategies": r.get("strategies") or [],
        "matrix": matrix,
        "gate": r.get("gate") or {},
    }


def main() -> int:
    data = build_data()
    tpl = io.open(TEMPLATE, encoding="utf-8").read()
    assert tpl.count(ANCHOR) == 1, "模板锚点 count=%d，应为 1" % tpl.count(ANCHOR)
    if data is None:
        print("arena/latest.json 不存在 → 页面会显示「数据没接上」")
        data = None
    html = tpl.replace(ANCHOR, json.dumps(data, ensure_ascii=False))
    io.open(OUT, "w", encoding="utf-8", newline="").write(html)
    print("WROTE %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    if data:
        # 自检：防止注入成功但内容为空
        n = data["n_signals"]
        assert str(n) in html, "自检失败：信号数 %s 不在页面里" % n
        ids = [s["id"] for s in data["strategies"]]
        miss = [x for x in ids if x not in html]
        assert not miss, "自检失败：策略 %s 未出现在页面" % miss
        print("自检通过：%d 条信号 / %d 个策略 / %d 个交易日"
              % (n, len(ids), data["n_days"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
