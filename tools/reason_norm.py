#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""龙虎榜上榜原因归一化（reason -> 维度）。

设计要点
--------
1. 不做字符串硬映射。远端同一口径有 20 种表述（11-11 实测），硬映射遇新写法
   会静默漏判；本模块解析成 window/metric/threshold/instrument 四维。
2. 判定顺序不可换。先 delist，再「偏离值」，再振幅/换手率，最后才认「涨跌幅达到」。
   否则「日涨幅偏离值达到7%」会被「日涨幅达到」规则先吃掉，7% 与 15% 两种
   完全不同口径就被混成一列。
3. 涨、跌分开。绝不用 abs() 合并方向——同一标的昨日涨停今日跌停会分别上榜，
   合并后方向信息就没了。
4. 归一化不改变原文。返回结构里必带 raw，落盘仍以原始 reason 为准。
5. 未识别不静默。返回 None 且 --check 非零退出，逼新增写法被看见。

用法
----
    python tools/reason_norm.py --explain "日涨幅偏离值达到7%的前5只证券"
    python tools/reason_norm.py --check                # 扫 data/top_list/*.jsonl
    python tools/reason_norm.py --check --glob "data/top_list/*.jsonl" --out report.json
"""

from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(_ROOT, "config", "reason_norm.json")
DATA_GLOB = os.path.join(_ROOT, "data", "top_list", "*.jsonl")

_RE_WINDOW = re.compile(r"连续\s*([0-9]+|[一二三四五六七八九十]+)\s*个交易日")
_RE_PCT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
_RE_DIR = re.compile(r"(涨幅|跌幅)")
_RE_CB = re.compile(r"可转债")

_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_to_int(token):
    """中文数字转整数，覆盖到 99（够用：交易日窗口最大见到 30）。"""
    if token.isdigit():
        return int(token)
    token = token.strip()
    if token in ("十",):
        return 10
    if "十" in token:
        left, _, right = token.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for ch in token:
        total = total * 10 + _CN_DIGITS.get(ch, 0)
    return total or 0


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize(reason, cfg=None):
    """分析单条 reason。

    返回 dict（含 code / window / metric / threshold / instrument / direction /
    raw / clean / rule），无法识别时返回 None。
    """
    if reason is None:
        return None
    raw = str(reason)
    if not raw.strip():
        return None
    cfg = cfg or load_config()

    clean = raw
    # 去噪只用于匹配，不参与输出值
    for item in cfg.get("noise_patterns", []):
        clean = re.sub(item["pattern"], " ", clean)

    # 1) 特殊情形（配置驱动，扫描 clean 而非 raw：“非上市首日”已被去噪，
    #    不会在这里被误判成“上市首日”）
    for sc in cfg.get("special_cases", []):
        if sc["match"] in clean:
            instrument = "cb" if _RE_CB.search(raw) else "stock"
            return {
                "raw": raw, "clean": clean, "rule": sc["id"],
                "window": "special", "metric": sc["metric"],
                "threshold": 0, "instrument": instrument, "direction": "none",
                "code": "%s_special_%s_0" % (instrument, sc["metric"]),
            }

    # 2) 指标判定（顺序敏感）
    metric = None
    direction = "none"
    if "偏离值" in clean:
        m = _RE_DIR.search(clean)
        if m:
            metric = "chg_dev" if m.group(1) == "涨幅" else "drop_dev"
            direction = "up" if m.group(1) == "涨幅" else "down"
    elif "振幅" in clean:
        metric = "amplitude"
    elif "换手率" in clean:
        metric = "turnover"
    else:
        m = _RE_DIR.search(clean)
        # 只认「涨幅达 / 跌幅达」，不要求「达到」——实测存在漏字的「涨幅达15%」写法。
        # 不再借 "累计" 兜底：带累计的表述必然含偏离值，已在上一分支处理。
        if m and re.search(r"%s达" % m.group(1), clean):
            metric = "chg" if m.group(1) == "涨幅" else "drop"
            direction = "up" if m.group(1) == "涨幅" else "down"

    if metric is None:
        return None

    # 3) 窗口
    window = "d1"
    mw = _RE_WINDOW.search(raw)
    if mw:
        n = _cn_to_int(mw.group(1))
        window = "d%d" % n

    # 4) 阈值
    threshold = 0
    mp = _RE_PCT.search(raw)
    if mp:
        val = float(mp.group(1))
        threshold = int(val) if val == int(val) else val

    # 5) 品种
    instrument = "cb" if _RE_CB.search(raw) else "stock"

    return {
        "raw": raw, "clean": clean, "rule": metric,
        "window": window, "metric": metric,
        "threshold": threshold, "instrument": instrument,
        "direction": direction,
        "code": "%s_%s_%s_%s" % (instrument, window, metric, threshold),
    }


def check(cfg=None, pattern=DATA_GLOB, out_path=None):
    """扫全部快照，报告未识别写法与维度分布。"""
    cfg = cfg or load_config()
    files = sorted(globmod.glob(pattern))
    seen = {}
    unmatched = {}
    total = 0
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                total += 1
                r = row.get("reason")
                res = normalize(r, cfg)
                if res is None:
                    unmatched[str(r)] = unmatched.get(str(r), 0) + 1
                else:
                    seen[res["code"]] = seen.get(res["code"], 0) + 1

    report = {
        "files": len(files),
        "rows": total,
        "distinct_codes": len(seen),
        "codes": dict(sorted(seen.items(), key=lambda kv: -kv[1])),
        "unmatched": unmatched,
        "ok": not unmatched,
    }
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="龙虎榜 reason 归一化")
    ap.add_argument("--explain", help="解析单条 reason 并打印维度")
    ap.add_argument("--check", action="store_true", help="扫描快照，报告未识别写法")
    ap.add_argument("--glob", default=DATA_GLOB, help="check 的输入 glob")
    ap.add_argument("--out", help="把 check 结果写成 JSON")
    args = ap.parse_args(argv)

    if args.explain:
        res = normalize(args.explain)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res else 3

    if args.check:
        rep = check(pattern=args.glob, out_path=args.out)
        print("files=%d rows=%d distinct_codes=%d"
              % (rep["files"], rep["rows"], rep["distinct_codes"]))
        for code, n in rep["codes"].items():
            print("  %5d  %s" % (n, code))
        if rep["unmatched"]:
            print("UNMATCHED (%d 种):" % len(rep["unmatched"]))
            for k, v in rep["unmatched"].items():
                print("  %5d  %s" % (v, k))
            return 2
        print("ALL MATCHED")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
