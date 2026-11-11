# -*- coding: utf-8 -*-
"""同花顺口径强势 × 缠论买点 交集（只读，2026-11-11 盘前诊断用）。

目的：tdx_sector_daily 上游断供后，确认「换源到现在花顺板块」能否恢复 T1
（强势领涨 + 缠论买点）这条腿，并给出今天可用的双确认名单。
"""
from __future__ import annotations

import json
import os

import ths_sector_probe as probe

HERE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
DETECT = "2026-11-10"


def load_chanlun_buys(detect=DETECT):
    """缠论买点，每票取最新一次买点。返回 {code: {name,type,dt,price}}。"""
    best = {}
    with open(POOL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if str(o.get("detect_date"))[:10] != detect:
                continue
            if o.get("type") not in ("1买", "2买", "3买"):
                continue
            c = str(o["code"])
            dt = str(o.get("dt"))[:10]
            if c not in best or dt > str(best[c].get("dt"))[:10]:
                best[c] = {
                    "code": c,
                    "name": o.get("name"),
                    "type": o.get("type"),
                    "dt": dt,
                    "price": o.get("price"),
                }
    return best


def dedup(hits):
    """同一只票命中多个板块 → 保留 ex250 最高的那条。"""
    out = {}
    for h in hits:
        c = h["code"]
        if c not in out or h["ex250"] > out[c]["ex250"]:
            out[c] = h
    return out


def main():
    buys = load_chanlun_buys()
    print("缠论 %s 买点票（去重）: %d 只" % (DETECT, len(buys)))
    print()

    tables = {}
    for mm in (5, 15, 30):
        r = probe.scan(min_members=mm)
        d = dedup(r["hits"])
        tables[mm] = d
        lead = sum(1 for v in d.values() if v["tag"] == "板块领涨")
        print(
            "成员门槛 ≥%-2d : 板块评估 %3d / 命中(去重) %4d 只（板块领涨 %4d，逆板块 %4d） 基准日 %s"
            % (mm, r["n_blocks_eval"], len(d), lead, len(d) - lead, r["basis"])
        )

    print()
    for mm in (5, 15, 30):
        d = tables[mm]
        inter = sorted(set(d) & set(buys))
        print("=== 成员门槛 ≥%d : 与缠论买点交集 %d 只 ===" % (mm, len(inter)))
        if not inter:
            continue
        print(
            "%-11s %-7s %-4s %-11s %7s %6s %-14s"
            % ("code", "name", "类型", "买点日", "ex250", "收盘", "所属板块")
        )
        rows = []
        for c in inter:
            h, b = d[c], buys[c]
            rows.append((h["ex250"], c, b, h))
        for ex, c, b, h in sorted(rows, reverse=True):
            print(
                "%-11s %-7s %-4s %-11s %+7.1f %6.2f %-14s"
                % (c, str(b["name"])[:6], b["type"], b["dt"], ex, h["last"], h["block"][:12])
            )
        print()

    # 三档都在的（最稳的交集）
    stable = set(tables[5]) & set(tables[15]) & set(tables[30]) & set(buys)
    print("三档门槛全部命中的双确认票: %d 只 %s" % (len(stable), sorted(stable)))


if __name__ == "__main__":
    main()
