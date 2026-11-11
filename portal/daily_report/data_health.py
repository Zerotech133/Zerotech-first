# -*- coding: utf-8 -*-
"""盘后数据巡检：断言「每张表的最新数据日 == 目标数据日」。

为什么要重写（2026-11-11 判例）
--------------------------------
旧版 `cohorts/health_check_YYYYMMDD.py` 的判据是：

    latest = max(daily_delta.trade_date)      # 先问「表里最新是哪天」
    然后检查 latest 那天七张表齐不齐          # 再问那天够不够

问题在于它把「表里最新的一天」当成了「今天应该有的一天」。
于是 11-10 整天零采集时，latest 退回 20261109，脚本照样报七表全 ok。
11-11 的盘后复盘就是这样判错的：真实结论是「11-10 一整天没采数据」，
却被写成了「数据延迟 3.9 小时」。

本脚本把顺序倒过来：**目标数据日由外部给定**，每张表断言

    max(date_col) == target

不等即判「零采集」，与条数够不够无关。这个断言与前视/漏采都能兜住，
因为任何一天的数据缺失都会让 max 停在更早的日期上。

用法
----
    python data_health.py --target 20261111     # 显式指定
    python data_health.py --target prev         # 上一交易日
    python data_health.py --target today        # 今天是交易日则取今天，否则上一交易日
    python data_health.py                       # 按当前时刻推断（>=18:00 取今天）

    --json-out PATH     指定 JSON 输出路径（默认为 DATA/data_health_<巡检日>.json）
    --quiet             只输出判定行

退出码：0 全部达标 / 1 有零采集·不完整·超前 / 2 环境错误（缺数据目录等）

设计取舍
--------
1. `mtime` 只作参考写入 JSON，**不参与判定**。文件 mtime 是「最后一次写入」，
   与「里面装的是哪天的数据」是两件事——11-11 那次就是把 mtime 12:57 读成了
   「扫描时刻」，而 12:57 其实是人工补数的写入时刻。
2. 每日必有表（daily / daily_basic / moneyflow / cyq_perf / margin_detail）
   严格断言 max == target。
3. 事件型表（anns_d 公告、limit_list_d 涨跌停）允许目标日无记录——市场平静时
   确实可以一只涨跌停都没有。但它们仍然不许超前（max > target 说明数据坏了）。
   且当每日必有表集体落后时，事件表一并判零采集（同一个根因，不另开解释）。
"""
import argparse
import datetime
import json
import os
import sys

import pandas as pd

DATA = r"C:\项目\zerobound-kg\factor_proto\data"
CAL_CACHE = os.path.join(DATA, "trade_cal_cache.json")

# kind: daily = 每个交易日必有记录；event = 可以当天为空
TABLES = [
    dict(id="daily",         name="日线行情", use="开盘、收盘、最高最低价、成交量",
         file="daily_delta.parquet", dcol="trade_date", kind="daily"),
    dict(id="daily_basic",   name="每日指标", use="总市值、流通市值、换手率、市盈率",
         file="daily_basic_delta.parquet", dcol="trade_date", kind="daily"),
    dict(id="moneyflow",     name="资金流",   use="大单、中单、小单的净流入",
         file="moneyflow_delta.parquet", dcol="trade_date", kind="daily"),
    dict(id="cyq_perf",      name="筹码",     use="获利盘比例、平均成本",
         file="cyq_perf_delta.parquet", dcol="trade_date", kind="daily"),
    dict(id="margin_detail", name="两融",     use="融资余额、融券余额（沪 T+0 / 深 T+1）",
         file="margin_detail_delta.parquet", dcol="trade_date", kind="daily"),
    dict(id="anns_d",        name="公告",     use="公司公告原文",
         file="anns_d_delta.parquet", dcol="ann_date", kind="event"),
    dict(id="limit_list_d",  name="涨跌停",   use="涨停、跌停、炸板的名单",
         file="limit_list_d_delta.parquet", dcol="trade_date", kind="event"),
]

COVER_MIN_RATIO = 0.90     # 目标日条数 / 近 10 日中位数，低于此判「不完整」
HIST_WINDOW = 10           # 中位数回看窗口

RC_OK, RC_BAD, RC_ENV = 0, 1, 2


# ── 交易日历（只读本地缓存，不依赖 token） ────────────────────────
def trade_cal():
    if not os.path.exists(CAL_CACHE):
        return []
    try:
        cal = json.load(open(CAL_CACHE, encoding="utf-8"))
        return sorted(str(x) for x in cal if x)
    except Exception:
        return []


def trading_days_between(a, b, cal):
    """a、b 之间的交易日数（不含 a 本身，含 b）。a > b 时返回负值。"""
    if not cal or a == b:
        return 0
    if a < b:
        return len([x for x in cal if a < x <= b])
    return -len([x for x in cal if b < x <= a])


def resolve_target(spec, now=None):
    """把 --target 的取值解析成 8 位日期，返回 (target, source, is_trading_day)。"""
    now = now or datetime.datetime.now()
    cal = trade_cal()
    today = now.strftime("%Y%m%d")

    def prev_of(d):
        cand = [x for x in cal if x < d]
        return cand[-1] if cand else None

    if spec in (None, ""):
        # 缺省：18:00 之后认为当天数据应该已到；否则取上一交易日
        if cal and today in cal and now.hour >= 18:
            return today, "auto(>=18:00)", True
        t = prev_of(today) or today
        return t, "auto(<18:00 取上一交易日)", t in cal or (cal and t == today)

    if spec == "today":
        if cal and today in cal:
            return today, "cli(today,是交易日)", True
        t = prev_of(today) or today
        return t, "cli(today,非交易日→上一交易日)", False

    if spec == "prev":
        t = prev_of(today)
        if not t:
            raise SystemExit("RC=2 无法从交易日历推出上一交易日（缓存缺失？）")
        return t, "cli(prev)", t in cal

    s = str(spec).strip()
    if len(s) != 8 or not s.isdigit():
        raise SystemExit("RC=2 --target 需为 8 位日期（如 20261111）或 today / prev，得到 %r" % spec)
    return s, "cli(显式)", s in cal


def fmt_mtime(p):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m-%d %H:%M")
    except OSError:
        return None


def by_exchange(codes):
    ex = pd.Series(list(codes)).astype(str).str[-2:]
    return {k: int((ex == k).sum()) for k in ("SH", "SZ", "BJ")}


def inspect_one(t, target, daily_all_lag):
    p = os.path.join(DATA, t["file"])
    item = {
        "id": t["id"], "name": t["name"], "use": t["use"], "kind": t["kind"],
        "file": t["file"], "date_col": t["dcol"],
        "n_at_target": 0, "n_at_max": 0, "prev_median": 0, "by_ex": {},
        "max_in_table": None, "file_mtime": None,
        "ok": False, "verdict": "", "reason": "",
    }
    if not os.path.exists(p):
        item["verdict"] = "missing_file"
        item["reason"] = "数据文件不存在：%s" % t["file"]
        return item
    item["file_mtime"] = fmt_mtime(p)

    try:
        # 一次读两列：日期列判断言，ts_code 判交易所分布。分两次读会把 5573 行读两遍。
        df = pd.read_parquet(p, columns=["ts_code", t["dcol"]])
    except Exception as e:
        item["verdict"] = "unreadable"
        item["reason"] = "读不出来：%s" % str(e)[:80]
        return item
    if df.empty:
        item["verdict"] = "empty_file"
        item["reason"] = "文件存在但一行数据都没有"
        return item

    s = df[t["dcol"]].astype(str).str[:8]
    cnt = s.value_counts()
    max_d = str(s.max())
    item["max_in_table"] = max_d
    item["n_at_max"] = int(cnt.get(max_d, 0))
    item["n_at_target"] = int(cnt.get(target, 0))
    prev = cnt[[i for i in cnt.index if i < target]]
    prev = prev.sort_index().tail(HIST_WINDOW)
    med = int(prev.median()) if len(prev) else 0
    item["prev_median"] = med

    # ── 核心断言：表内最新日必须等于目标数据日 ───────────────────
    if max_d > target:
        item["verdict"] = "ahead"
        item["reason"] = "有未来数据：表内最新 %s 晚于目标 %s" % (max_d, target)
        return item

    if max_d < target:
        if t["kind"] == "event" and not daily_all_lag:
            # 每日必有表已经齐了，事件表当天为空属市场平静，不是故障
            item["ok"] = True
            item["verdict"] = "no_event"
            item["reason"] = "目标日无此类记录（每日必有表已齐，判定为当日无事件）"
        else:
            item["verdict"] = "zero_collect"
            item["reason"] = "零采集：表内最新停在 %s，目标 %s 无任何记录" % (max_d, target)
        return item

    # max_d == target
    n = item["n_at_target"]
    if n == 0:
        item["verdict"] = "zero_collect"
        item["reason"] = "零采集：表内最大日等于目标日但没有记录（空壳）"
        return item
    # 目标日的交易所分布（只在有记录时算，避免全 0 造成误解）
    try:
        sel = df[s == target]
        item["by_ex"] = by_exchange(sel.ts_code.astype(str).tolist())
    except Exception:
        item["by_ex"] = {}

    if t["kind"] == "event":
        item["ok"] = True
        item["verdict"] = "ok"
        item["reason"] = "当日 %d 条" % n
        return item

    if med and n < COVER_MIN_RATIO * med:
        item["ok"] = False
        item["verdict"] = "incomplete"
        item["reason"] = "覆盖不足：%d 条，仅为近 %d 日中位数 %d 的 %.0f%%" % (
            n, HIST_WINDOW, med, 100.0 * n / med)
        return item

    item["ok"] = True
    item["verdict"] = "ok"
    item["reason"] = "当日 %d 条" % n
    return item


def main():
    ap = argparse.ArgumentParser(description="盘后数据巡检：每表断言 max(date)==目标数据日")
    ap.add_argument("--target", default=None, help="8 位日期 / today / prev；缺省按当前时刻推断")
    ap.add_argument("--data-dir", default=None,
                    help="数据根目录（默认为 DATA 常量）。用于拿历史备份复现某天的判定")
    ap.add_argument("--json-out", default=None, help="JSON 输出路径")
    ap.add_argument("--quiet", action="store_true", help="只输出判定行")
    args = ap.parse_args()

    global DATA
    if args.data_dir:
        DATA = args.data_dir
        if not os.path.isdir(DATA):
            print("RC=%d --data-dir 不存在：%s" % (RC_ENV, DATA))
            return RC_ENV

    if not os.path.isdir(DATA):
        print("RC=%d 数据目录不存在：%s" % (RC_ENV, DATA))
        return RC_ENV

    try:
        target, source, is_td = resolve_target(args.target)
    except SystemExit as e:
        print(e)
        return RC_ENV

    now = datetime.datetime.now()
    cal = trade_cal()

    # 先判每日必有表是否集体落后 —— 事件型表要靠这个信号决定怎么判
    daily_tables = [t for t in TABLES if t["kind"] == "daily"]
    pre_max = {}
    for t in daily_tables:
        p = os.path.join(DATA, t["file"])
        if not os.path.exists(p):
            pre_max[t["id"]] = None
            continue
        try:
            s = pd.read_parquet(p, columns=[t["dcol"]])[t["dcol"]].astype(str).str[:8]
            pre_max[t["id"]] = str(s.max()) if len(s) else None
        except Exception:
            pre_max[t["id"]] = None
    known = [v for v in pre_max.values() if v]
    daily_all_lag = bool(known) and all(v < target for v in known)

    sets = [inspect_one(t, target, daily_all_lag) for t in TABLES]

    universal_max = min(known) if known else None
    newest_max = max(known) if known else None
    lag_days = trading_days_between(universal_max, target, cal) if universal_max else 0

    n_ok = len([x for x in sets if x["verdict"] in ("ok", "no_event")])
    n_zero = len([x for x in sets if x["verdict"] == "zero_collect"])
    n_inc = len([x for x in sets if x["verdict"] == "incomplete"])
    n_ahead = len([x for x in sets if x["verdict"] == "ahead"])
    n_env = len([x for x in sets if x["verdict"] in ("missing_file", "unreadable", "empty_file")])

    if n_env:
        verdict = "env_error"
    elif n_ahead:
        verdict = "ahead"
    elif n_zero == len(sets):
        verdict = "zero_collect"
    elif n_zero:
        verdict = "partial"
    elif n_inc:
        verdict = "incomplete"
    else:
        verdict = "ok"

    res = {
        "check_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "target": target,
        "target_source": source,
        "target_is_trading_day": is_td,
        "verdict": verdict,
        "universal_max": universal_max,
        "lag_trading_days": lag_days,
        "note": ("mtime 只记录文件最后写入时间，不参与判定；判据一律为 max(%s) == %s"
                 % ("/".join(sorted({t["dcol"] for t in TABLES})), target)),
        "summary": {"n_tables": len(sets), "ok": n_ok, "zero_collect": n_zero,
                    "incomplete": n_inc, "ahead": n_ahead, "env": n_env},
        "sets": sets,
    }

    out = args.json_out or os.path.join(DATA, "data_health_%s.json" % now.strftime("%Y%m%d"))
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError as e:
        print("RC=%d 写 JSON 失败：%s" % (RC_ENV, e))
        return RC_ENV

    if not args.quiet:
        print("盘后数据巡检  %s" % res["check_time"])
        print("目标数据日 %s（%s）%s" % (target, source,
                                    "" if is_td else "  [注意] 该日不在交易日历中"))
        print("每日必有表最新日 %s%s" % (
            (universal_max if universal_max == newest_max
             else "%s ~ %s（最落后 %s）" % (universal_max, newest_max, universal_max)),
            ("  落后目标 %d 个交易日" % lag_days) if lag_days else ""))
        print("")
        print("%-10s %-12s %8s %10s %10s  %s" % ("表", "名称", "目标日条数", "表内最新日", "近10日中午位", "判定"))
        for x in sets:
            print("%-10s %-12s %8d %10s %10d  %s%s" % (
                x["id"], x["name"], x["n_at_target"], x["max_in_table"] or "-",
                x["prev_median"], x["verdict"],
                ("  " + x["reason"]) if x["verdict"] not in ("ok", "no_event") else ""))
        print("")

    label = {
        "ok": "全部达标",
        "zero_collect": "零采集 —— 目标日一行数据都没有（不是延迟，是没采）",
        "partial": "部分落后 —— 一部分表已到目标日，另一部分停在更早",
        "incomplete": "覆盖不足 —— 目标日有记录但条数明显少于常态",
        "ahead": "有未来数据 —— 表内出现晚于目标日的记录，须查源",
        "env_error": "环境异常 —— 文件缺失或读不出来",
    }[verdict]
    print("判定：%s（%d/%d 达标）" % (label, n_ok, len(sets)))
    print("JSON → %s" % out)

    if verdict == "zero_collect" and universal_max:
        print("提示：先补拉 %s 的数据再谈其他；只看到条数够就判达标，是 11-11 判错的原型。" % target)
    return RC_OK if verdict == "ok" else RC_BAD


if __name__ == "__main__":
    sys.exit(main())
