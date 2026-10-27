#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宽度口径并行对照采集（W8 议题一前置，20261015）。

背景：审计发现宽度口径分裂——qpzm 自口径（close>MA20，20261014 实测
23.3%）vs 湖口径（qd_breadth_series，同日 32.64%），差 9.3pp。W8 定档
"观察带 45~50%" 前需一周以上并行对照数据判：两口径是水平漂移还是
形态背离（相关系数、差分符号一致率）。

纯记录零行为：每天日批尾追加一行 {date, qpzm_breadth, lake_breadth,
diff, anchor_match} 到 data/breadth_parallel.jsonl；同日重跑幂等覆盖
（tmp+os.replace 原子写）。任一产物缺失/缺键 → 该侧 None 照记不炸。
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RPS_FP = os.path.join(HERE, "reports", "rps_latest.json")
SERIES_FP = os.path.join(HERE, "reports", "qd_breadth_series.json")
OUT_FP = os.path.join(HERE, "data", "breadth_parallel.jsonl")


def _load(fp):
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _qpzm_cn_breadth(rps):
    """rps_latest.json → (date, breadth)。取 label 含 A股 的环境。"""
    for e in rps.get("envs") or []:
        if "A股" in str(e.get("label", "")):
            b = e.get("breadth")
            b = round(float(b), 4) if isinstance(b, (int, float)) else None
            return str(rps.get("latest") or ""), b
    return "", None


def _lake_last(series):
    """qd_breadth_series.json → 最新有效锚 (date, breadth)。跳 null。"""
    dates = series.get("dates") or []
    brs = series.get("breadth") or []
    for d, b in zip(reversed(dates), reversed(brs)):
        if isinstance(b, (int, float)):
            return str(d), float(b)
    return None, None


def collect_pair(rps_fp=RPS_FP, series_fp=SERIES_FP):
    rps = _load(rps_fp)
    series = _load(series_fp)
    qpzm_date, qpzm_b = _qpzm_cn_breadth(rps)
    lake_date, lake_b = _lake_last(series)
    diff = None
    if lake_b is not None and isinstance(qpzm_b, (int, float)):
        diff = round(lake_b - qpzm_b, 4)
    anchor_match = None
    if lake_date and qpzm_date:
        anchor_match = lake_date == qpzm_date
    return {
        # 主锚取湖口径（穿越监测器/复盘同源，对齐周报市况节）
        "date": lake_date or qpzm_date or None,
        "lake_breadth": round(lake_b, 4) if lake_b is not None else None,
        "lake_date": lake_date,
        "qpzm_breadth": qpzm_b,
        "qpzm_date": qpzm_date or None,
        "diff": diff,
        "anchor_match": anchor_match,
    }


def append_row(row, fp=OUT_FP):
    """同日幂等覆盖，tmp+os.replace 原子落。"""
    if not row.get("date"):
        return False
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    rows = []
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
    rows = [r for r in rows if str(r.get("date")) != str(row["date"])]
    rows.append(row)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, fp)
    return True


def run(rps_fp=RPS_FP, series_fp=SERIES_FP, out_fp=OUT_FP):
    row = collect_pair(rps_fp, series_fp)
    if row.get("lake_breadth") is None and row.get("qpzm_breadth") is None:
        return None  # 两产物全缺 → 不落空行
    append_row(row, out_fp)
    return row


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
