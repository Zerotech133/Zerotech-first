# -*- coding: utf-8 -*-
"""合成示例数据生成（确定性，无网络；不含任何真实行情/财务数据）。"""
import csv
import math
import os

CODES = ["SAMPLE001", "SAMPLE002"]


def _bars(code: str, n: int = 400):
    rows = []
    price = 20.0 if code.endswith("1") else 35.0
    for i in range(n):
        ymd = 20250101
        # 用序号生成连续日期（示例用，不要求真实交易日）
        d = 10000000 + (i // 28) * 10000 + (i % 28 + 1)
        drift = 0.0004 if code.endswith("1") else -0.0002
        wave = math.sin(i / 21.0) * 0.012 + math.sin(i / 7.0) * 0.004
        price *= (1 + drift + wave)
        high = price * (1 + 0.008 + 0.004 * abs(math.sin(i / 13.0)))
        low = price * (1 - 0.008 - 0.004 * abs(math.cos(i / 11.0)))
        vol = 1_000_000 * (1 + 0.5 * abs(math.sin(i / 9.0)))
        rows.append({"code": code, "date": str(ymd), "open": round(price * 0.999, 2),
                     "high": round(high, 2), "low": round(low, 2), "close": round(price, 2),
                     "volume": round(vol, 0)})
    return rows


def _fundamentals(code: str):
    growth = 0.15 if code.endswith("1") else -0.08
    rows = []
    rev = 100.0
    npt = 12.0
    for i, end in enumerate(["20241231", "20250630", "20251231"]):
        rev *= (1 + growth / 2)
        npt *= (1 + growth)
        rows.append({"code": code, "end_date": end, "revenue": round(rev, 2),
                     "net_profit": round(npt, 2),
                     "gross_margin": 32.0 if code.endswith("1") else 21.0,
                     "debt_ratio": 35.0 if code.endswith("1") else 68.0})
    return rows


def _valuation(code: str, n: int = 400):
    base = 2.2 if code.endswith("1") else 1.1
    rows = []
    for i in range(n):
        ps = base * (1 + 0.25 * math.sin(i / 40.0))
        rows.append({"code": code, "trade_date": str(10000000 + (i // 28) * 10000 + (i % 28 + 1)),
                     "pe": round(max(0.0, ps * 18), 2), "ps": round(ps, 3),
                     "pb": round(ps * 3, 2)})
    return rows


def _events(code: str):
    cat = "业绩" if code.endswith("1") else "诉讼"
    return [
        {"code": code, "date": "20250415", "category": cat,
         "title": "示例事件：定期报告披露", "source": "synthetic://demo"},
        {"code": code, "date": "20250720", "category": "回购",
         "title": "示例事件：回购进展公告", "source": "synthetic://demo"},
    ]


def write_sample(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in (("kline.csv", [r for c in CODES for r in _bars(c)]),
                       ("fundamentals.csv", [r for c in CODES for r in _fundamentals(c)]),
                       ("valuation.csv", [r for c in CODES for r in _valuation(c)]),
                       ("events.csv", [r for c in CODES for r in _events(c)])):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
