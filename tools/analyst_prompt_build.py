# -*- coding: utf-8 -*-
"""题面生成器：判读题面里的每个数字都必须从事实表算出来，不许手写。

缘起（判例 #37）：2026-11-13 我手写题面时把「大晟文化（净买 +0.31 亿）」误列为
「净卖且上涨」，glm-5.3 当场指出，本地 9b 不但没发现，还在此错面上继续编造
「主力派发/散户接盘」。手写题面 = 把错误前置注入判读，且事后无法归因。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"C:\aios")


def load_top_list(day: str):
    p = ROOT / "data" / "top_list" / ("%s.jsonl" % day)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def turnover_prompt(day: str, threshold: int = 20, rows=None) -> str:
    if rows is None:
        rows = [r for r in load_top_list(day)
                if "换手率" in (r.get("reason") or "")
                and ("%d%%" % threshold) in (r.get("reason") or "").replace("％", "%")]
    if not rows:
        raise SystemExit("该日无换手率榜：%s" % day)

    rows.sort(key=lambda r: r["net_amount"])
    sell = [r for r in rows if r["net_amount"] < 0]
    buy = [r for r in rows if r["net_amount"] > 0]
    flat_net = [r for r in rows if r["net_amount"] == 0]
    up = [r for r in rows if r["pct_change"] > 0]
    down = [r for r in rows if r["pct_change"] < 0]
    flat_px = [r for r in rows if r["pct_change"] == 0]
    sell_up = [r for r in sell if r["pct_change"] > 0]
    buy_up = [r for r in buy if r["pct_change"] > 0]
    sell_down = [r for r in sell if r["pct_change"] <= 0]
    # 判例 #37 判据②：互斥集恒等式自校验。任一不成立即拒绝出题——
    # 宁可不判，不可拿一张加不起来的表去判。
    checks = {
        "行数与净额方向": len(sell) + len(buy) + len(flat_net) == len(rows),
        "行数与涨跌": len(up) + len(down) + len(flat_px) == len(rows),
        "净卖 = 净卖且涨 + 净卖且不涨": len(sell_up) + len(sell_down) == len(sell),
        "上涨 = 净卖且涨 + 净买且涨 + 净额为零且涨": (
            len(sell_up) + len(buy_up) + len([r for r in flat_net if r["pct_change"] > 0])
            == len(up)),
    }
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise SystemExit("题面自校验失败，拒绝出题：%s | 明细 rows=%d sell=%d buy=%d flat=%d "
                         "up=%d down=%d flat_px=%d sell_up=%d sell_down=%d buy_up=%d"
                         % (bad, len(rows), len(sell), len(buy), len(flat_net),
                            len(up), len(down), len(flat_px),
                            len(sell_up), len(sell_down), len(buy_up)))

    d = day
    date_cn = "%d 月 %d 日" % (int(d[4:6]), int(d[6:8]))
    lines = [
        "你是马谡，军情司职业分析师。红线：只答「哪一面先动」，不答「因为什么」。",
        "",
        "%s「换手率≥%d%%」上榜 %d 只。字段说明：net_amount 是龙虎榜【买卖前五席位】净额，"
        "不是全市场资金流，也不是主力资金。" % (date_cn, threshold, len(rows)),
        "",
        "席位净卖 %d 只 / 净买 %d 只；股价上涨 %d 只 / 下跌 %d 只。" %
        (len(sell), len(buy), len(up), len(down)),
        "席位净卖且上涨 %d 只：%s" % (len(sell_up), "、".join(
            "%s%+.2f%%(净卖%.2f亿/成交%.2f亿)" % (r["name"], r["pct_change"],
                                              abs(r["net_amount"]) / 1e8, r["amount"] / 1e8)
            for r in sell_up) or "无") ,
        "席位净买且上涨 %d 只：%s" % (len(buy_up), "、".join(
            "%s%+.2f%%(净买%.2f亿/成交%.2f亿)" % (r["name"], r["pct_change"],
                                              r["net_amount"] / 1e8, r["amount"] / 1e8)
            for r in buy_up) or "无"),
        "席位净卖且下跌 %d 只：%s" % (len([r for r in sell if r["pct_change"] <= 0]), "、".join(
            r["name"] for r in sell if r["pct_change"] <= 0) or "无"),
        "",
        "问：这组数据能不能支撑「资金正在流出」这个说法？若不能，给一句口径准确的替代表述。",
        "要求：150 字以内；不得出现「主力资金」「散户」这类无法从本数据验证的指称；直接作答，不复述题目。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    day = sys.argv[1] if len(sys.argv) > 1 else "20261111"
    text = turnover_prompt(day)
    out = ROOT / "tmp" / ("judge_prompt_%s_turnover.txt" % day)
    out.write_text(text, encoding="utf-8")
    print(text)
    print("\n--- 已写 %s（%d 字） ---" % (out, len(text)))
