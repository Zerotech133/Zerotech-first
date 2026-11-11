# -*- coding: utf-8 -*-
"""候风信号台账独立页构建器。

读 cohorts/status_latest.json + settlements.jsonl（cohort_track.py 产出），
把数据注入 houfeng_template.html → houfeng.html（自包含，nginx/file 双可用）。

规则（与 _gen_daily_h5.py 同一纪律）：
  - 数据缺失不是错误，页面显式说「没接上」，绝不静默沿用旧数字；
  - 模板里不写死任何会换日变化的数字。

用法： "/c/Program Files/Python311/python.exe" build_houfeng.py
"""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COHORT_DIR = os.path.join(HERE, "cohorts")
TEMPLATE = os.path.join(HERE, "houfeng_template.html")
OUT = os.path.join(HERE, "houfeng.html")

ANCHOR = "/*__DATA__*/ null"


def build_data() -> dict:
    """汇总台账数据。任何一段缺失记 None，页面自行显示「没接上」。"""
    data = {"gen_at": None, "run_at": None, "as_of_from": None,
            "today": None, "bench": None, "cohorts": [], "settlements": [],
            "rolling": {}}

    st_fp = os.path.join(COHORT_DIR, "status_latest.json")
    if os.path.exists(st_fp):
        st = json.load(io.open(st_fp, encoding="utf-8"))
        data["run_at"] = st.get("run_at")
        data["as_of_from"] = st.get("as_of_from")
        data["today"] = "-".join([st["today"][:4], st["today"][4:6], st["today"][6:]])
        data["bench"] = {"open_today": st.get("bench_open_today"),
                         "pool_n": st.get("bench_pool_n")}
        cs = []
        for c in st.get("cohorts") or []:
            cs.append({
                "asof": c.get("asof"), "pool": c.get("pool"),
                "entry_date": c.get("entry_date"),
                "day_index": c.get("day_index"),
                "n": c.get("n"), "n_used": c.get("n_used"),
                "n_miss": c.get("n_miss"),
                "cum": c.get("cum_today_mean"),
                "bench_cum": c.get("bench_cum"),
                "excess": c.get("excess"),
                "settled": c.get("settled") or {},
                "rows": c.get("rows") or [],
            })
        # 主选在前，批次按信号日倒序
        cs.sort(key=lambda c: (0 if c["pool"] == "main" else 1,
                               -(int(c["asof"] or 0))))
        data["cohorts"] = cs
        # 滚动超额与降级线：status_latest 里已有就直接用（cohort_track.snap 落盘，
        # 含 _degrade 判定）。2026-11-11 起口径唯一来源是 cohort_track.rolling_alpha()，
        # 本文件不再重算，避免两套实现各自漂移。
        if st.get("rolling"):
            data["rolling"] = st["rolling"]

    se_fp = os.path.join(COHORT_DIR, "settlements.jsonl")
    if os.path.exists(se_fp):
        rows = [json.loads(l) for l in io.open(se_fp, encoding="utf-8")
                if l.strip()]
        data["settlements"] = rows
        # 回退路径：仅当 status_latest.json 还没有 rolling 字段时（旧版快照）才本地自算。
        # 正常路径走上面 st["rolling"]，不执行这一段。
        if not data["rolling"]:
            agg = {}
            for r in rows:
                if r.get("pool") != "auto" or r.get("excess") is None:
                    continue
                a = agg.setdefault(str(r["n"]), {"list": []})
                a["list"].append(r["excess"])
            for n, a in agg.items():
                ex = a["list"]
                data["rolling"][n] = {
                    "n": len(ex),
                    "mean_pp": round(sum(ex) / len(ex) * 100, 2),
                    "win": round(sum(1 for x in ex if x > 0) / len(ex) * 100, 0),
                }
    return data


def main() -> int:
    data = build_data()
    tpl = io.open(TEMPLATE, encoding="utf-8").read()
    assert tpl.count(ANCHOR) == 1, "模板锚点 count=%d，应为 1" % tpl.count(ANCHOR)
    html = tpl.replace(ANCHOR, json.dumps(data, ensure_ascii=False))
    io.open(OUT, "w", encoding="utf-8", newline="").write(html)
    print("WROTE %s (%d bytes, %d cohorts, %d settlements)"
          % (OUT, os.path.getsize(OUT), len(data["cohorts"]),
             len(data["settlements"])))
    # 自检：页面必须含有数据里的关键数字（防注入失败静默出空页）
    n0 = data["cohorts"][0]["n"] if data["cohorts"] else None
    if n0 is not None:
        assert str(n0) in html, "自检失败：首批次只数 %s 不在页面里" % n0
    return 0


if __name__ == "__main__":
    sys.exit(main())
