# -*- coding: utf-8 -*-
"""weak 状态流 px_d 修复：把「休市日/周末」的 px_d 改回真实最后交易日。

为什么需要
    2026 国庆休市 10-01~10-07（上交所上证公告〔2025〕45 号），行情源在假期
    里继续返回「上一个交易日」的行情，但把 dt 写成了假期内的日期，导致
    weak_track_<code>.jsonl 出现 px_d=2026-10-02 —— 一个格式合法、当天却
    根本没开市的日子。后果：
      * weak_track_close.nd 按 px_d 去重计「跟踪 N 日」，休市日被算成交易日
        （301216 实测：distinct px_d 7 个，其中 1 个是 10-02）；
      * §13 审计的「日期倒退」把「休市日值 → 真实最后交易日」误判成回退，
        把正确的 09-30 一起算进异常。

修法
    px_d 改写成 **严格早于该日期的最近交易日**（cn_trading_days.prev_trading_day）：
    10-02 → 09-30、周六 → 周五。多条假期行会塌成同一天，这是正确的——
    它们记录的本就是同一个交易日（09-30）的收盘价。ts（跑批时刻）不动，
    原始值写进 px_d_fix 留痕，不做无声改写。

安全
    * 默认 **dry-run**，只打印将要改什么，不写盘；
    * --apply 才动手，且逐文件先备份 `<file>.bak-<YYYYMMDD-HHMM>`；
    * 逐行原子重写（临时文件 + os.replace），改完重扫一遍自检；
    * 只碰 px_d，不改 px/ex250/trig/ts 等任何字段。

用法
    python weak_track_repair_px_d.py                 # dry-run，全部流
    python weak_track_repair_px_d.py --code 301216    # 只看某标的
    python weak_track_repair_px_d.py --code 301216 --apply
"""

import argparse
import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cn_trading_days as ctd  # noqa: E402
import weak_track_summary as wts  # noqa: E402

REP = os.path.join(os.path.dirname(HERE), "reports")


def _fix_one(fp, apply):
    """修一个流文件 → dict(rows, fixed, mapping, path)；不写盘时 apply=False。"""
    raw = []  # [(obj 或 None, 原始行文本)]
    with open(fp, encoding="utf-8") as f:
        for line in f:
            text = line.rstrip("\n")
            if not text.strip():  # 空行原样保留
                raw.append((None, text))
                continue
            try:
                raw.append((json.loads(text), text))
            except ValueError:
                raw.append((None, text))  # 坏行原样保留，不丢数据

    mapping = {}
    fixed = 0
    touched = set()
    for i, (r, _text) in enumerate(raw):
        if not isinstance(r, dict):
            continue
        pd = str(r.get("px_d") or "")
        if pd in ("", "?") or not wts.valid_day(pd):
            continue
        pd = pd[:10]
        if not ctd.why_not(pd):
            continue
        new = ctd.prev_trading_day(pd)
        if not new:
            continue  # 回溯不到（日历未覆盖），不动，交审计报
        r["px_d_fix"] = {"from": pd, "to": new, "rule": "prev_trading_day"}
        r["px_d"] = new
        fixed += 1
        touched.add(i)
        mapping["%s→%s" % (pd, new)] = mapping.get("%s→%s" % (pd, new), 0) + 1

    if apply and fixed:
        bak = "%s.bak-%s" % (fp, time.strftime("%Y%m%d-%H%M"))
        shutil.copy2(fp, bak)
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for i, (r, text) in enumerate(raw):
                # 未改动的行按原文写回（含坏行/空行），避免无意义格式抖动
                f.write((json.dumps(r, ensure_ascii=False) if i in touched else text) + "\n")
        os.replace(tmp, fp)
    return {
        "path": fp,
        "rows": sum(1 for r, _ in raw if r is not None),
        "fixed": fixed,
        "mapping": mapping,
    }


def _rescan(fp):
    """改后自检：还剩多少非交易日 px_d。"""
    n = 0
    with open(fp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            pd = str(r.get("px_d") or "")
            if wts.valid_day(pd) and ctd.why_not(pd):
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="weak 状态流 px_d 休市日修复（默认 dry-run）")
    ap.add_argument("--code", help="只修某标的（301216 / 301216.SZ）")
    ap.add_argument("--rep", default=REP, help="reports 目录（默认 ../reports）")
    ap.add_argument("--apply", action="store_true", help="真实写盘（默认只预览）")
    a = ap.parse_args()

    pat = "weak_track_%s*.jsonl" % (a.code.split(".")[0] if a.code else "*")
    files = sorted(glob.glob(os.path.join(a.rep, pat)))
    if not files:
        print("没有匹配的状态流：%s" % os.path.join(a.rep, pat))
        return 1

    total = 0
    for fp in files:
        res = _fix_one(fp, a.apply)
        total += res["fixed"]
        if not res["fixed"]:
            print("  %-52s %3d 行  无需修复" % (os.path.basename(fp), res["rows"]))
            continue
        print(
            "  %-52s %3d 行  改 %d 行"
            % (os.path.basename(fp), res["rows"], res["fixed"])
        )
        for k, v in sorted(res["mapping"].items()):
            print("      %s × %d" % (k, v))
        if a.apply:
            left = _rescan(fp)
            print("      自检：剩余非交易日 px_d = %d %s" % (left, "OK" if not left else "未清干净"))

    print(
        "\n合计需修 %d 行 · %s"
        % (total, "已写盘" if a.apply else "dry-run（加 --apply 才写）")
    )
    if not a.apply and total:
        print("修复口径：px_d → 严格早于该日的最近交易日；ts/px/其它字段不动；")
        print("          原值写进 px_d_fix 留痕；每条流先备份 .bak-<时间戳>。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
