# -*- coding: utf-8 -*-
"""strong freshness_guard 预检工具（只读，不动任何数据）。

目的：远端板块仓修复后，一键回答「现在跑 strong_scan.py 会不会被
freshness_guard 放行」，避免盲跑触发 exit 3 拦截。

判据（逐条复刻 strong_scan.py:57 freshness_guard，不发明阈值）：
  · 板块锚: linkage.block_bars("881019", "1d", limit=3) 最新 dt
  · 个股锚: chanlun_data.load_kline(c, "1d", limit=3)，
    c ∈ (600519.SH, 000001.SZ, 300750.SZ)，最新 dt 必须与板块锚相等
  · CN 水位: SELECT count(*) FROM v2_bar_daily
    WHERE market='CN' AND trade_date=<板块锚 dt> ≥ 5000
三项全过 → WOULD_PASS；任一不过 → WOULD_BLOCK + guard 同口径原因。
另附诊断：板块锚落后个股锚几个交易日（v2_bar_daily 交易日历为准）。

只读承诺：parquet/DB 仅读；不写文件、不跑扫描、不改状态。
退出码：0=WOULD_PASS，1=WOULD_BLOCK，2=预检自身失败（数据/环境缺失）。

用法:
  python strong_guard_precheck.py          # 输出判定
  python strong_guard_precheck.py --help   # 帮助
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# —— 与 strong_scan.py 完全一致的守卫常量（勿在此处另设值）——
GUARD_SAMPLE = ("600519.SH", "000001.SZ", "300750.SZ")
GUARD_BLOCK = "881019"
GUARD_MIN_CN = 5000

EXIT_PASS = 0
EXIT_BLOCK = 1
EXIT_ERROR = 2


def _gauge(stock_details):
    """个股锚最大 dt（多个股锚取最新，用于追平缺口诊断）。"""
    dts = [s["dt"] for s in stock_details if s.get("dt")]
    return max(dts) if dts else None


def _trading_days_between(start_d, end_d):
    """(start_d, end_d] 区间内的 CN 交易日列表（只读查询）。

    start_d ≤ end_d；用于板块锚(start_d)落后个股锚(end_d)时的追平缺口。
    """
    import chanlun_data

    rows = chanlun_data._db_query(
        "SELECT DISTINCT trade_date FROM v2_bar_daily "
        "WHERE market='CN' AND trade_date > %s AND trade_date <= %s "
        "ORDER BY trade_date",
        [start_d, end_d],
    )
    return [str(r[0])[:10] for r in rows]


def precheck():
    """复刻 freshness_guard 判定 → dict（含明细与追平缺口）。

    判定分支顺序与 strong_scan.py:72-95 一致：
    板块锚空 → 个股锚空 → 个股 dt ≠ 板块 dt → CN 计数不足 → 放行。
    """
    import chanlun_data
    import linkage

    out = {
        "verdict": "WOULD_PASS",
        "reasons": [],
        "basis": None,
        "block_anchor": None,
        "stocks": [],
        "cn_at_blk": None,
        "cn_watermark": None,
        "gap_days": [],
        "cn_latest": None,
    }

    # 1) 板块锚
    blk = linkage.block_bars(GUARD_BLOCK, "1d", limit=3)
    if not blk:
        raise RuntimeError(
            "板块锚 %s 无K线（data/tdx/tdx_sector_daily parquet 缺失或为空）"
            % GUARD_BLOCK
        )
    blk_d = str(blk[-1]["dt"])[:10]
    out["block_anchor"] = {
        "code": GUARD_BLOCK,
        "dt": blk_d,
        "source": "tdx_sector_daily",
    }
    out["basis"] = blk_d

    # 2) 个股锚 ×3（guard 同款抽样循环）
    for c in GUARD_SAMPLE:
        r = chanlun_data.load_kline(c, "1d", limit=3)
        if not r.get("ok") or not r.get("bars"):
            raise RuntimeError(
                "个股锚 %s 无K线（%s）——guard 将拦截，预检无法核对"
                % (c, r.get("error", ""))
            )
        d = str(r["bars"][-1]["dt"])[:10]
        out["stocks"].append({"code": c, "dt": d, "source": r.get("source", "")})
        if d != blk_d:
            out["verdict"] = "WOULD_BLOCK"
            out["reasons"].append(
                "个股锚 %s=%s（%s）≠ 板块锚 %s=%s"
                % (c, d, r.get("source"), GUARD_BLOCK, blk_d)
            )

    stock_latest = _gauge(out["stocks"])

    # 3) CN 全量水位（guard 同款计数 SQL，blk_d 当日）
    rows = chanlun_data._db_query(
        "SELECT count(*) FROM v2_bar_daily WHERE market='CN' AND trade_date=%s", [blk_d]
    )
    n_cn = int(rows[0][0]) if rows else 0
    out["cn_at_blk"] = n_cn
    if n_cn < GUARD_MIN_CN:
        out["verdict"] = "WOULD_BLOCK"
        out["reasons"].append(
            "zhice CN %s bar 计数 %d < 阈 %d（全量未补齐，名单将半 T-1）"
            % (blk_d, n_cn, GUARD_MIN_CN)
        )

    # —— 诊断（不影响判定）：CN 最新交易日 + 板块锚追平缺口 ——
    try:
        rows = chanlun_data._db_query(
            "SELECT max(trade_date) FROM v2_bar_daily WHERE market='CN'", []
        )
        cn_latest = str(rows[0][0])[:10] if rows and rows[0][0] else None
        out["cn_latest"] = cn_latest
        if cn_latest:
            rows = chanlun_data._db_query(
                "SELECT count(*) FROM v2_bar_daily WHERE market='CN' AND trade_date=%s",
                [cn_latest],
            )
            out["cn_watermark"] = {
                "dt": cn_latest,
                "count": int(rows[0][0]) if rows else 0,
            }
        gap_ref = stock_latest or cn_latest
        if gap_ref and gap_ref > blk_d:
            out["gap_days"] = _trading_days_between(blk_d, gap_ref)
    except Exception as e:
        out["gap_days"] = None
        sys.stderr.write("! 诊断查询失败（不影响判定）: %s\n" % str(e)[:120])

    return out


def _print_report(o):
    print("== strong freshness_guard 预检（只读） ==")
    print(
        "[判据源] strong_scan.py freshness_guard：板块锚 %s ×3 个股锚同日 "
        "+ CN 计数 ≥ %d" % (GUARD_BLOCK, GUARD_MIN_CN)
    )
    print(
        "[板块锚] %s (tdx_sector_daily): %s"
        % (GUARD_BLOCK, o["block_anchor"]["dt"] if o["block_anchor"] else "?")
    )
    for s in o["stocks"]:
        mark = (
            "OK"
            if o["block_anchor"] and s["dt"] == o["block_anchor"]["dt"]
            else "MISMATCH"
        )
        print("[个股锚] %s: %s (%s) [%s]" % (s["code"], s["dt"], s["source"], mark))
    if o["cn_at_blk"] is not None:
        print(
            "[CN 水位] v2_bar_daily @ 板块锚 %s: %d bars（阈 %d，%s）"
            % (
                o["block_anchor"]["dt"] if o["block_anchor"] else "?",
                o["cn_at_blk"],
                GUARD_MIN_CN,
                "OK" if o["cn_at_blk"] >= GUARD_MIN_CN else "LOW",
            )
        )
    if o["cn_watermark"]:
        print(
            "[CN 最新] trade_date=%s（@该日 %d bars）"
            % (o["cn_watermark"]["dt"], o["cn_watermark"]["count"])
        )
    if isinstance(o["gap_days"], list):
        if o["gap_days"]:
            print(
                "[追平缺口] 板块锚落后 %d 个交易日: %s"
                % (len(o["gap_days"]), ", ".join(o["gap_days"]))
            )
        else:
            print("[追平缺口] 0（板块锚已追平个股锚）")
    print("[判定] %s" % o["verdict"])
    for r in o["reasons"]:
        print("[原因] %s" % r)
    if o["verdict"] == "WOULD_PASS":
        print("[结论] 现在跑 strong_scan.py 预期放行（数据基准日 %s）" % o["basis"])


def main():
    ap = argparse.ArgumentParser(
        description="strong_scan freshness_guard 只读预检：输出板块锚/个股"
        "锚/CN 水位与 WOULD_PASS/WOULD_BLOCK 判定。退出码 "
        "0=放行 1=拦截 2=预检失败。"
    )
    ap.parse_args()
    try:
        o = precheck()
    except Exception as e:
        sys.stderr.write("[预检失败] %s\n" % str(e)[:300])
        return EXIT_ERROR
    _print_report(o)
    return EXIT_PASS if o["verdict"] == "WOULD_PASS" else EXIT_BLOCK


if __name__ == "__main__":
    sys.exit(main())
