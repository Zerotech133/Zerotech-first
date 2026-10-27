# -*- coding: utf-8 -*-
"""xq_r3_calibration.py — RiskGuard R3 事件拦截 vs 雪球实时舆情 一致性核对管道

背景
----
RiskGuard R3（事件风险规则）历史拦截账本：C:/项目/zerobound-kg/riskguard_ledger/ledger.jsonl
雪球实时舆情：XueqiuCrawl 每日 18:00 首跑（2026-10-18 今晚），帖子入 xueqiu_info.db/xq_status，
打标入 xq_status_classified（ticker 提取 v1.1 修复、六类 content_type）。

历史对齐不可行（雪球存量全是 2012-2023），本管道提供两种模式：

1) 实时模式（默认）：**未来一致性核对**——实时舆情积累 2-4 周后，核对
   「R3 拦截的标的/时点 vs 舆情是否提前异动（bearish 突增）」。
   ⚠ 依赖今晚 18:00 首跑后的实时数据积累。空态/数据不足一律显式输出（fail-visible）。

2) 历史回测模式（--backtest）：用库内历史回填帖（xq_status 存量）做第一版历史校准。
   拦截日（2026-09/10）与历史帖（2012-2023）无法事件级对齐，采用**历年同窗回放**：
   对每个拦截 (ticker, 月-日)，在历史各年同月同日锚定虚拟拦截日，跑同一套
   compare_around_block/verdict_of 判定。属季节同窗近似，非事件对齐（输出中显式声明）。
   舆情源与实时管道同口径（xq_status_classified）；另附 raw xq_status 名称匹配覆盖探针
   （stock_names.json 历史证券名/别名，仅取 ≥3 字专名），用于判断 classified 回补价值。

用法
----
    python tools/research/xq_r3_calibration.py                      # 实时全量核对
    python tools/research/xq_r3_calibration.py --lookback-days 28   # 限定舆情回看窗口
    python tools/research/xq_r3_calibration.py --high-conf 0.7      # 高置信 bearish 阈值
    python tools/research/xq_r3_calibration.py --backtest           # 历史同窗回放回测

只读纪律：ledger.jsonl、xueqiu_info.db、stock_names.json 全部只读；标准库 only。
"""

import argparse
import collections
import json
import os
import re
import sqlite3
import sys
from datetime import date, timedelta

DEFAULT_LEDGER = "C:/项目/zerobound-kg/riskguard_ledger/ledger.jsonl"
DEFAULT_DB = "C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db"
DEFAULT_NAMES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "stock_names.json"
)

PRE_DAYS = 3  # 拦截日前 3 天（不含拦截日）
POST_DAYS = 1  # 拦截日后 1 天（含拦截日当天）


# ---------------------------------------------------------------- 账本解析


def normalize_security(sec):
    """security 归一：A股 '300808.SZ' → '300808'（6 位）；港股 '00700.HK' → '00700'（5 位）。

    无法归一返回 None（调用方计数并显式跳过）。
    """
    if not sec or not isinstance(sec, str):
        return None
    code = sec.split(".")[0].strip()
    if code.isdigit() and len(code) == 6:
        return code
    if code.isdigit() and len(code) == 5:  # 港股 5 位
        return code
    return None


def load_r3_blocks(ledger_path):
    """解析 ledger.jsonl 中的 R3 拦截事件。

    - 只取 rule_id 以 R3 开头且 action != 'pass'（block / shadow_block）的真实拦截；
    - score 为 None 或 reason 含 'insufficient' 的跳过（样本不足，非真实拦截）；
    - 返回 (events, stats)：events 按 (ticker, date) 聚合
      {ticker: {date: {"n": 次数, "max_score": float, "first_ts": str, "reasons": [...]}}}；
    - stats 为显式计数（解析失败/跳过/无法归一），供空态输出。
    """
    events = {}
    stats = {
        "lines": 0,
        "bad_json": 0,
        "r3_total": 0,
        "r3_pass": 0,
        "skipped_no_score": 0,
        "skipped_insufficient": 0,
        "skipped_bad_security": 0,
        "blocked_records": 0,
    }
    if not os.path.exists(ledger_path):
        raise FileNotFoundError("账本不存在: %s" % ledger_path)

    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_json"] += 1
                continue
            if not str(d.get("rule_id", "")).startswith("R3"):
                continue
            stats["r3_total"] += 1
            if d.get("action") == "pass":
                stats["r3_pass"] += 1
                continue
            reason = d.get("reason") or ""
            if d.get("score") is None:
                stats["skipped_no_score"] += 1
                continue
            if "insufficient" in reason:
                stats["skipped_insufficient"] += 1
                continue
            ticker = normalize_security(d.get("security"))
            if ticker is None:
                stats["skipped_bad_security"] += 1
                continue
            stats["blocked_records"] += 1
            ts = d.get("ts") or ""
            day = ts[:10]
            slot = events.setdefault(ticker, {})
            rec = slot.setdefault(
                day, {"n": 0, "max_score": 0.0, "first_ts": ts, "reasons": []}
            )
            rec["n"] += 1
            rec["max_score"] = max(rec["max_score"], float(d["score"]))
            if len(rec["reasons"]) < 3:
                rec["reasons"].append(reason[:80])
    return events, stats


# ---------------------------------------------------------------- 舆情取数


def open_xq_db_readonly(db_path):
    """只读打开雪球库；返回 (con, classified_exists)。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError("雪球库不存在: %s" % db_path)
    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='xq_status_classified'"
    ).fetchone()
    return con, row is not None


def load_sentiment_daily(con, since_date):
    """拉取 xq_status_classified 中 since_date 以来的舆情序列，按 (ticker, date) 聚合。

    返回 {ticker: {date: {"posts": n, "bearish": n, "bearish_hc": n}}}
    - bearish_hc = sentiment='bearish' 且 confidence >= high_conf；
    - tickers 列为 JSON 数组字符串，在 Python 侧解析（ticker 提取 v1.1 已修复噪音）。
    """
    rows = con.execute(
        """SELECT s.created_at, c.tickers, c.sentiment, c.confidence
             FROM xq_status_classified c
             JOIN xq_status s ON s.status_id = c.status_id
            WHERE s.created_at >= ?
              AND c.parse_ok = 1""",
        (since_date.isoformat(),),
    ).fetchall()

    out = collections.defaultdict(
        lambda: collections.defaultdict(
            lambda: {"posts": 0, "bearish": 0, "bearish_hc": 0}
        )
    )
    for created_at, tickers_json, sentiment, confidence in rows:
        day_s = (created_at or "")[:10]
        try:
            day = date.fromisoformat(day_s)
        except ValueError:
            continue
        try:
            tickers = json.loads(tickers_json or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(tickers, list):
            continue
        is_bear = sentiment == "bearish"
        is_hc = is_bear and confidence is not None and confidence >= HIGH_CONF
        for t in tickers:
            if not isinstance(t, str) or not t:
                continue
            agg = out[t][day]
            agg["posts"] += 1
            if is_bear:
                agg["bearish"] += 1
            if is_hc:
                agg["bearish_hc"] += 1
    return {t: dict(days) for t, days in out.items()}


# ---------------------------------------------------------------- 对比与判定


def _window_sum(daily, d_from, d_to):
    """[d_from, d_to] 闭区间内舆情聚合。"""
    agg = {"posts": 0, "bearish": 0, "bearish_hc": 0, "days_with_data": 0}
    d = d_from
    while d <= d_to:
        a = daily.get(d)
        if a and a["posts"] > 0:
            for k in ("posts", "bearish", "bearish_hc"):
                agg[k] += a[k]
            agg["days_with_data"] += 1
        d += timedelta(days=1)
    return agg


def compare_around_block(daily, block_date):
    """「拦截日前 3 天 vs 后 1 天」舆情对比框架。

    pre  = [block_date-3d, block_date-1d]
    post = [block_date, block_date+1d]（拦截日当天计入 post）
    """
    pre = _window_sum(
        daily, block_date - timedelta(days=PRE_DAYS), block_date - timedelta(days=1)
    )
    post = _window_sum(daily, block_date, block_date + timedelta(days=POST_DAYS - 1))
    pre_rate = pre["bearish"] / pre["posts"] if pre["posts"] else None
    post_rate = post["bearish"] / post["posts"] if post["posts"] else None
    return {
        "pre": pre,
        "post": post,
        "pre_bearish_rate": pre_rate,
        "post_bearish_rate": post_rate,
    }


def verdict_of(cmp_result):
    """判定：consistent=舆情提前异动 / missed=舆情无感知 / not_yet=数据不足。

    consistent 判据（框架，2-4 周数据到位后可再调阈值）：
      post 有数据 且 pre 有数据 且 (post bearish 占比 > pre 占比
      且 post 高置信 bearish >= 1 或 post bearish >= 2)。
    """
    pre, post = cmp_result["pre"], cmp_result["post"]
    if post["posts"] == 0 or pre["posts"] == 0:
        return "not_yet"
    spike = cmp_result["post_bearish_rate"] > cmp_result["pre_bearish_rate"] and (
        post["bearish_hc"] >= 1 or post["bearish"] >= 2
    )
    return "consistent" if spike else "missed"


# ---------------------------------------------------------------- 历史回测（--backtest）


def build_name_matcher(tickers, names_path):
    """从 stock_names.json 构建拦截标的的名称匹配集。

    收集 code2name 当前名 + name2code/alias_of 历史名/别名，剥离 ST/*ST/G/S 前缀与
    退/(退) 装饰，仅保留 ≥3 字专名（压制「同德」「信通」这类撞名噪音）。
    返回 {code6: [name, ...]}；匹配语义为「名称是帖子文本的子串」。
    """
    with open(names_path, encoding="utf-8") as f:
        d = json.load(f)
    c2n = d.get("code2name", {})
    by_full = collections.defaultdict(set)
    for nm, cd in list(d.get("name2code", {}).items()) + list(
        d.get("alias_of", {}).items()
    ):
        by_full[cd].add(nm)

    def suffix6(code):
        suf = "SZ" if code[:2] in ("00", "30") else "SH"
        return code + "." + suf

    names_by_code = {}
    for code in sorted(tickers):
        cands = set()
        cur = c2n.get(suffix6(code), "")
        if cur:
            cands.add(cur)
        cands |= by_full.get(suffix6(code), set())
        cleaned = set()
        for nm in cands:
            core = re.sub(r"^\*?ST|^G(?=[^A-Z])|^S(?=[^TZ])", "", nm.replace("*", ""))
            core = re.sub(r"\(退\)$", "", core).rstrip("退")
            if len(core) >= 3 and "ST" not in core:
                cleaned.add(core)
        names_by_code[code] = sorted(cleaned)
    return names_by_code


def raw_name_coverage(con, tickers, names_by_code):
    """raw xq_status 名称匹配覆盖探针（未打标帖无法给 bearish，只做覆盖统计）。

    返回 {ticker: {"posts": 总帖数, "years": {year: n}}}；对全库 17,338 帖做子串匹配。
    """
    pat = {t: names for t, names in names_by_code.items() if names}
    hits = {t: {"posts": 0, "years": collections.Counter()} for t in tickers}
    for created_at, title, desc in con.execute(
        "SELECT created_at, title, desc FROM xq_status"
    ):
        text = (title or "") + " " + (desc or "")
        if not text:
            continue
        year = (created_at or "")[:4]
        for t, names in pat.items():
            if any(nm in text for nm in names):
                hits[t]["posts"] += 1
                hits[t]["years"][year] += 1
    return hits


def classified_year_range(con):
    """classified 数据的 (min_year, max_year)；表空/缺表返回 (None, None)。"""
    row = con.execute(
        "SELECT MIN(substr(s.created_at,1,4)), MAX(substr(s.created_at,1,4)) "
        "FROM xq_status_classified c JOIN xq_status s ON s.status_id=c.status_id "
        "WHERE c.parse_ok=1"
    ).fetchone()
    if not row or not row[0]:
        return None, None
    return int(row[0]), int(row[1])


def backtest_replay(sentiment, events, year_lo, year_hi):
    """历年同窗回放：对每个拦截 (ticker, 月-日) 在历史各年同月同日锚定虚拟拦截日。

    每年回放的舆情序列只取该年数据（回放窗不跨年，保持年纯性；2/29 锚点回落 2/28）。
    返回 case 列表 [(ticker, 拦截日, 回放年, cmp结果, 判定)]。
    """
    cases = []
    for t in sorted(events):
        daily_all = sentiment.get(t, {})
        for day_s in sorted(events[t]):
            bd = date.fromisoformat(day_s)
            for y in range(year_lo, year_hi + 1):
                try:
                    anchor = date(y, bd.month, bd.day)
                except ValueError:  # 2/29
                    anchor = date(y, 2, 28)
                daily_y = {d: a for d, a in daily_all.items() if d.year == y}
                cmp_res = compare_around_block(daily_y, anchor)
                cases.append((t, day_s, y, cmp_res, verdict_of(cmp_res)))
    return cases


def run_backtest(args, events, con):
    """--backtest 模式主体。返回退出码。"""
    print("\n[2b] 历史回测模式：历年同窗回放（季节同窗近似，非事件对齐）")
    print(
        "    拦截日 %s ~ %s，历史锚定按「同月同日」逐年回放，判定口径与实时管道一致。"
        % (min(min(v) for v in events.values()), max(max(v) for v in events.values()))
    )

    # classified 主口径舆情（全量历史区间）
    y_lo, y_hi = classified_year_range(con)
    if y_lo is None:
        print("    [空态] classified 无可回放数据，历史回测终止（fail-visible）。")
        return 1
    print("    classified 回放年范围: %d ~ %d" % (y_lo, y_hi))
    sentiment = load_sentiment_daily(con, date(y_lo, 1, 1))
    r3_tickers = sorted(events)
    covered_cls = [t for t in r3_tickers if t in sentiment]
    print("    classified 对拦截标的覆盖: %d/%d" % (len(covered_cls), len(r3_tickers)))

    # raw 覆盖探针（信息性，不参与判定）
    try:
        names_by_code = build_name_matcher(r3_tickers, args.names)
    except (OSError, json.JSONDecodeError) as e:
        print("    [警告] stock_names.json 不可用（%s），跳过 raw 覆盖探针。" % e)
        names_by_code = {}
    raw_hits = (
        raw_name_coverage(con, r3_tickers, names_by_code) if names_by_code else {}
    )
    raw_covered = [t for t in r3_tickers if raw_hits.get(t, {}).get("posts")]
    raw_total = sum(raw_hits[t]["posts"] for t in raw_covered)
    print(
        "    raw 名称匹配覆盖探针: %d/%d 标的、共 %d 帖（未打标，无法给 bearish 判定）"
        % (len(raw_covered), len(r3_tickers), raw_total)
    )
    for t in raw_covered:
        print(
            "        %s 命中名=%s 帖=%d 年分布=%s"
            % (
                t,
                "/".join(names_by_code[t][:4]),
                raw_hits[t]["posts"],
                dict(sorted(raw_hits[t]["years"].items())),
            )
        )

    # 回放明细
    cases = backtest_replay(sentiment, events, y_lo, y_hi)
    verdict_counts = collections.Counter()
    by_ticker = collections.defaultdict(collections.Counter)
    print("\n[3b] 历年同窗回放明细（仅列有数据或判定非 not_yet 的行）")
    print(
        "    %-9s %-10s %4s %6s %4s %4s %6s | %-10s"
        % ("ticker", "拦截日", "回放年", "前占比", "后帖", "后bear", "后占比", "判定")
    )
    print("    " + "-" * 72)
    n_shown = 0
    for t, day_s, y, cmp_res, v in cases:
        verdict_counts[v] += 1
        by_ticker[t][v] += 1
        if cmp_res["pre"]["posts"] or cmp_res["post"]["posts"]:
            n_shown += 1
            print(
                "    %-9s %-10s %4d %6s %4d %4d %6s | %-10s"
                % (
                    t,
                    day_s,
                    y,
                    fmt_pct(cmp_res["pre_bearish_rate"]),
                    cmp_res["post"]["posts"],
                    cmp_res["post"]["bearish"],
                    fmt_pct(cmp_res["post_bearish_rate"]),
                    v,
                )
            )
    if n_shown == 0:
        print("    （无任何回放窗命中数据——全部 not_yet）")
    print("    " + "-" * 72)

    # 汇总
    total_cases = len(cases)
    print("\n[4b] 三裁决分布（共 %d 个 标的×拦截日×回放年 组合）" % total_cases)
    for k in ("consistent", "missed", "not_yet"):
        n = verdict_counts.get(k, 0)
        print(
            "    %-10s %3d  (%s)"
            % (k, n, fmt_pct(n / total_cases) if total_cases else "-")
        )
    print("\n    分标的汇总（consistent/missed/not_yet）:")
    for t in r3_tickers:
        c = by_ticker.get(t, collections.Counter())
        print(
            "        %-9s %d/%d/%d"
            % (t, c.get("consistent", 0), c.get("missed", 0), c.get("not_yet", 0))
        )

    # 结论段（fail-visible）
    print("\n[5b] 历史回测结论")
    if not covered_cls:
        print(
            "    [不可判定] classified 对 %d 个拦截标的零覆盖——历史 backtest 无法给出"
            % len(r3_tickers)
        )
        print(
            "    consistent/missed 结论。raw 库存在 %d 帖名称命中（%d/%d 标的）但全部未打标。"
            % (raw_total, len(raw_covered), len(r3_tickers))
        )
        print(
            "    可行动作：① 对 xq_status 存量 17,338 帖回补 classified（xq_classify 管道）后重跑 --backtest；"
            "② 或等待实时舆情积累后走实时模式。"
        )
    elif verdict_counts.get("not_yet", 0) == total_cases:
        print("    [空态] 全部回放组合 not_yet——拦截标的在历史同窗内无舆情数据。")
    else:
        nc = verdict_counts.get("consistent", 0)
        nm_ = verdict_counts.get("missed", 0)
        print(
            "    历史同窗回放：consistent=%d missed=%d not_yet=%d。"
            % (nc, nm_, verdict_counts.get("not_yet", 0))
        )
        print(
            "    注意：季节同窗近似≠事件对齐，consistent 仅代表「历史同期 bearish 突增模式存在」，"
            "不构成 R3 规则精度的直接证据。"
        )
    return 0


# ---------------------------------------------------------------- 汇总输出


def fmt_pct(x):
    return "%.1f%%" % (100 * x) if x is not None else "-"


def main():
    global HIGH_CONF
    ap = argparse.ArgumentParser(description="R3 拦截 vs 雪球实时舆情一致性核对")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=28,
        help="舆情回看窗口（相对今天），覆盖拦截日前后对比即可（backtest 模式忽略）",
    )
    ap.add_argument(
        "--high-conf", type=float, default=0.7, help="高置信 bearish 的 confidence 阈值"
    )
    ap.add_argument(
        "--backtest",
        action="store_true",
        help="历史回测模式：对拦截记录按历年同窗（同月同日）回放",
    )
    ap.add_argument(
        "--names",
        default=DEFAULT_NAMES,
        help="stock_names.json 路径（backtest raw 覆盖探针用）",
    )
    args = ap.parse_args()
    HIGH_CONF = args.high_conf

    print("=" * 72)
    print(
        "R3 拦截 vs 雪球舆情 一致性核对（%s）"
        % ("历史回测：历年同窗回放" if args.backtest else "实时管道 v0：预置框架")
    )
    print("=" * 72)

    # 1) R3 拦截事件
    events, lstats = load_r3_blocks(args.ledger)
    print("\n[1] R3 拦截账本解析  %s" % args.ledger)
    print(
        "    行数=%d  坏JSON=%d  R3总数=%d（pass=%d）"
        % (lstats["lines"], lstats["bad_json"], lstats["r3_total"], lstats["r3_pass"])
    )
    print(
        "    跳过：score=None=%d  insufficient=%d  security无法归一=%d"
        % (
            lstats["skipped_no_score"],
            lstats["skipped_insufficient"],
            lstats["skipped_bad_security"],
        )
    )
    print("    真实拦截记录=%d  涉及标的=%d" % (lstats["blocked_records"], len(events)))
    if not events:
        print("    [空态] 无真实 R3 拦截事件，管道终止（fail-visible）。")
        return 1

    # 2) 舆情库状态
    con, exists = open_xq_db_readonly(args.db)
    if args.backtest:
        rc = run_backtest(args, events, con)
        con.close()
        return rc
    today = date.today()
    since = today - timedelta(days=args.lookback_days)
    print("\n[2] 雪球舆情库  %s" % args.db)
    print(
        "    舆情回看窗口: %s ~ %s (lookback=%d 天, 高置信阈值=%.2f)"
        % (since.isoformat(), today.isoformat(), args.lookback_days, HIGH_CONF)
    )
    if not exists:
        print(
            "    [空态] xq_status_classified 表不存在 —— 雪球实时舆情今晚 18:00 首跑后"
            "才会创建。覆盖率 0%%，全部标的判定为 not_yet。2-4 周后重跑即全量研究。"
        )
    else:
        total = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
        minmax = con.execute(
            "SELECT MIN(s.created_at), MAX(s.created_at) FROM xq_status_classified c "
            "JOIN xq_status s ON s.status_id=c.status_id"
        ).fetchone()
        print(
            "    classified 总帖数=%d  时间范围: %s ~ %s"
            % (total, minmax[0], minmax[1])
        )
        if total == 0:
            print("    [空态] xq_status_classified 为空（首跑尚未写入）。")

    sentiment = load_sentiment_daily(con, since) if exists else {}
    if exists and not sentiment:
        print("    [空态] 回看窗口内无已打标帖子（ticker 提取无命中或数据未积累）。")

    # 3) 覆盖率
    r3_tickers = sorted(events)
    covered = [t for t in r3_tickers if t in sentiment]
    print(
        "\n[3] R3 标的覆盖率: %d/%d = %s"
        % (len(covered), len(r3_tickers), fmt_pct(len(covered) / len(r3_tickers)))
    )
    if not covered:
        print(
            "    [空态] 实时舆情尚未覆盖任何 R3 标的（覆盖率 0%）。"
            "属预期：首跑今晚 18:00，覆盖需 2-4 周积累。"
        )

    # 4) 每标的舆情摘要与判定
    verdict_counts = collections.Counter()
    print("\n[4] 每标的舆情摘要与判定")
    print(
        "    %-9s %-10s %4s %4s %6s | %4s %4s %6s | %-10s"
        % (
            "ticker",
            "拦截日",
            "前帖",
            "前bear",
            "前占比",
            "后帖",
            "后bear",
            "后占比",
            "判定",
        )
    )
    print("    " + "-" * 72)
    for t in r3_tickers:
        for day_s in sorted(events[t]):
            block_date = date.fromisoformat(day_s)
            daily = sentiment.get(t, {})
            cmp_res = compare_around_block(daily, block_date)
            v = verdict_of(cmp_res)
            verdict_counts[v] += 1
            pre, post = cmp_res["pre"], cmp_res["post"]
            print(
                "    %-9s %-10s %4d %4d %6s | %4d %4d %6s | %-10s"
                % (
                    t,
                    day_s,
                    pre["posts"],
                    pre["bearish"],
                    fmt_pct(cmp_res["pre_bearish_rate"]),
                    post["posts"],
                    post["bearish"],
                    fmt_pct(cmp_res["post_bearish_rate"]),
                    v,
                )
            )
            if v == "not_yet" and pre["posts"] == 0 and post["posts"] == 0:
                print("             [数据不足] 该标的/时点窗口内无舆情，无法判定")
    print("    " + "-" * 72)

    # 5) 判定汇总
    total_cases = sum(verdict_counts.values())
    print("\n[5] 判定汇总（共 %d 个 标的×拦截日 组合）" % total_cases)
    for k in ("consistent", "missed", "not_yet"):
        n = verdict_counts.get(k, 0)
        print(
            "    %-10s %3d  (%s)"
            % (k, n, fmt_pct(n / total_cases) if total_cases else "-")
        )
    if verdict_counts.get("not_yet", 0) == total_cases:
        print("\n    [空态] 全部 not_yet —— 当前为管道空态验证，非研究结论。")
    print("\n完成。建议 2-4 周后（实时舆情积累后）重跑本脚本获取全量判定。")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
