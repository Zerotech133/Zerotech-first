# -*- coding: utf-8 -*-
"""弱于板块跟踪状态流查询（K35-b §7 命令块⑦ 配套渲染器）。

把 reports/weak_track_<code6>.jsonl（weak_track_check.py 逐日追加）
渲染为可读表：时间 / 数据日·现价 / 超额250 / 趋势(个股/板块/上证) / 触发。

用法:
  python weak_track_view.py 301216            # 默认尾部 10 行
  python weak_track_view.py 301216 --tail 30
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(HERE), "reports")


def main():
    ap = argparse.ArgumentParser(description="跟踪状态流查询")
    ap.add_argument("code", nargs="?", default="301216")
    ap.add_argument("--tail", type=int, default=10, help="展示尾部 N 行")
    a = ap.parse_args()
    c6 = a.code.strip().upper().split(".")[0]
    fp = os.path.join(REP, "weak_track_%s.jsonl" % c6)
    if not os.path.exists(fp):
        print("状态流不存在：%s（weak_track_check.py 跑过才有）" % fp)
        return 1
    rows, bad = [], 0
    with open(fp, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                bad += 1
    total = len(rows)
    rows = rows[-a.tail :]
    print(
        "跟踪状态流 %s（尾部 %d / 共 %d 行%s）\n"
        % (fp, len(rows), total, "，坏行 %d" % bad if bad else "")
    )
    print(
        "%-19s %-15s %7s  %-12s %s"
        % ("时间", "数据日·现价", "超额250", "趋势 个/板/沪", "触发")
    )
    print("-" * 78)
    _t = lambda s: str(s).replace("（均线缠绕）", "·缠")[:8]  # noqa: E731
    for r in rows:
        ex = r.get("ex250")
        exs = "%+.1fpp" % ex if ex is not None else "—"
        trig = r.get("trig") or []
        t = "；".join(trig) + " ⚠复评" if trig else "—"
        print(
            "%-19s %-15s %7s  %-12s %s"
            % (
                str(r.get("ts", "?"))[:19],
                "%s·%s" % (str(r.get("px_d", "?"))[5:], r.get("px")),
                exs,
                "/".join(_t(x) for x in (r.get("trend") or [])),
                t,
            )
        )
    if rows and not any(r.get("trig") for r in rows):
        print("\n（区间内无 §6 触发，继续影子跟踪）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
