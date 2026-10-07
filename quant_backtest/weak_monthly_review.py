# -*- coding: utf-8 -*-
"""D3 weak 案例库月度复盘自动化（K-weak 月报聚合）。

纯聚合工具，不改动 weak_track_check.py 本体：扫 reports/weak_track_*.jsonl
全部状态流（--month YYYY-MM 过滤，默认上个月）+ data/chanlun_pool.jsonl
立案信号，产出当月案例库汇总复盘报告 reports/weak_monthly_<YYYYMM>.md，
可选 --send 经 news_collector/send_feishu.py 文本通道推摘要（前 20 行）。

指标口径：
  - 当月活跃案例数   = 当月有状态流记录的 code 数
  - 当月新增立案数   = chanlun_pool.jsonl 中 detect_date 落当月的信号数（按 code 去重）
  - 触发复评次数/个股 = 当月 trig 非空的记录数 / 涉及 code 数（§8 复评打点）
  - 触发类型         = 解析 trig 内容：含「超额收敛」/「池内新信号」/
                       两者兼有计「两者皆触」（按记录计数，一条记录一类）
  - 跟踪天数         = 该 code 当月首末记录日历日差 +1（含首尾）
  - 250 日超额分布   = 当月末次记录 ex250：≤-20pp / -20~-5pp / >-5pp 三档
  - 趋势分布         = 当月末次记录 trend[0] 归一：多/空/缠绕/样本不足

用法:
  python weak_monthly_review.py                # 默认上个月，落盘报告
  python weak_monthly_review.py --month 2026-09
  python weak_monthly_review.py --send         # 落盘 + 飞书推摘要
  python weak_monthly_review.py --dry          # 只打印，不落盘不推送
"""
import argparse
import datetime
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.abspath(os.path.join(HERE, "..", "reports"))
POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

# trend[0] 归一分档（weak_track_check.trend_tag 产出的原始串）
TREND_BUCKETS = ("多", "空", "缠绕", "样本不足")
EX_BUCKETS = ("≤-20pp", "-20~-5pp", ">-5pp")


def _month_range(month):
    """'YYYY-MM' -> (首日 'YYYY-MM-DD', 次月首日 'YYYY-MM-DD')。"""
    y, m = int(month[:4]), int(month[5:7])
    start = datetime.date(y, m, 1)
    end = datetime.date(y + (m == 12), (m % 12) + 1, 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _default_month():
    t = datetime.date.today()
    y, m = (t.year - 1, 12) if t.month == 1 else (t.year, t.month - 1)
    return "%04d-%02d" % (y, m)


def _trend_bucket(s):
    s = str(s or "")
    if "样本不足" in s:
        return "样本不足"
    if s.startswith("多"):
        return "多"
    if s.startswith("空"):
        return "空"
    return "缠绕"


def _ex_bucket(ex):
    if ex is None:
        return None
    if ex <= -20:
        return EX_BUCKETS[0]
    if ex <= -5:
        return EX_BUCKETS[1]
    return EX_BUCKETS[2]


def _trig_type(trig):
    """一条 trig 记录归一类：两者皆触 / 超额收敛 / 池内新信号。"""
    txt = " | ".join(str(x) for x in trig)
    has_ex = "超额收敛" in txt
    has_pool = "池内新信号" in txt
    if has_ex and has_pool:
        return "两者皆触"
    if has_ex:
        return "超额收敛"
    if has_pool:
        return "池内新信号"
    return "其他"


def _case_status(last_ex, trig_cnt):
    if last_ex is not None and last_ex > -5:
        return "已收敛"
    if trig_cnt:
        return "已触发复评"
    return "跟踪中"


def collect(month):
    """聚合指定月份指标。返回 (metrics dict, per-case list)。"""
    lo, hi = _month_range(month)
    codes = {}          # code -> list[record]（当月）
    for fp in sorted(glob.glob(os.path.join(REPORTS, "weak_track_*.jsonl"))):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                rows = [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            continue
        for r in rows:
            d = str(r.get("ts", ""))[:10]
            if lo <= d < hi:
                codes.setdefault(str(r.get("code", "?")), []).append(r)

    # 当月新增立案（pool detect_date 落当月，按 code 去重）
    new_codes = set()
    new_sigs = 0
    if os.path.exists(POOL):
        try:
            with open(POOL, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        q = json.loads(line)
                    except ValueError:
                        continue
                    if lo <= str(q.get("detect_date", ""))[:10] < hi:
                        new_sigs += 1
                        new_codes.add(str(q.get("code", "?")))
        except OSError:
            pass

    trig_rows = trig_codes = 0
    trig_type = {"超额收敛": 0, "池内新信号": 0, "两者皆触": 0, "其他": 0}
    cases = []
    for code in sorted(codes):
        recs = sorted(codes[code], key=lambda r: str(r.get("ts", "")))
        days = recs[0], recs[-1]
        d0 = str(days[0].get("ts", ""))[:10]
        d1 = str(days[-1].get("ts", ""))[:10]
        try:
            track_days = (datetime.date.fromisoformat(d1)
                          - datetime.date.fromisoformat(d0)).days + 1
        except ValueError:
            track_days = 1
        trig = [r for r in recs if r.get("trig")]
        trig_cnt = len(trig)
        trig_rows += trig_cnt
        if trig:
            trig_codes += 1
        for r in trig:
            trig_type[_trig_type(r["trig"])] += 1
        last = recs[-1]
        last_ex = last.get("ex250")
        cases.append({
            "code": code,
            "days": track_days,
            "trig": trig_cnt,
            "ex250": last_ex,
            "trend": _trend_bucket((last.get("trend") or [""])[0]),
            "status": _case_status(last_ex, trig_cnt),
        })

    ex_dist = {b: 0 for b in EX_BUCKETS}
    for c in cases:
        b = _ex_bucket(c["ex250"])
        if b:
            ex_dist[b] += 1
    tr_dist = {b: 0 for b in TREND_BUCKETS}
    for c in cases:
        tr_dist[c["trend"]] += 1

    metrics = {
        "month": month,
        "active": len(codes),
        "new_case": len(new_codes),
        "new_sig_total": new_sigs,
        "trig_rows": trig_rows,
        "trig_codes": trig_codes,
        "trig_type": trig_type,
        "ex_dist": ex_dist,
        "trend_dist": tr_dist,
    }
    return metrics, cases


def render(metrics, cases, now=None):
    m = metrics
    now = now or datetime.datetime.now()
    tt = m["trig_type"]
    ex = m["ex_dist"]
    tr = m["trend_dist"]
    lines = [
        "# weak 案例库月度复盘（%s）" % m["month"],
        "",
        "## 一、当月总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 当月活跃案例数 | %d |" % m["active"],
        "| 当月新增立案数（按 code 去重） | %d |" % m["new_case"],
        "| 触发复评次数 | %d |" % m["trig_rows"],
        "| 触发涉及个股数 | %d |" % m["trig_codes"],
        "",
        "## 二、分布指标",
        "",
        "| 维度 | 分档 | 计数 |",
        "|---|---|---|",
        "| 触发类型 | 超额收敛 | %d |" % tt["超额收敛"],
        "| 触发类型 | 池内新信号 | %d |" % tt["池内新信号"],
        "| 触发类型 | 两者皆触 | %d |" % tt["两者皆触"],
        "| 250日超额 | ≤-20pp | %d |" % ex[EX_BUCKETS[0]],
        "| 250日超额 | -20~-5pp | %d |" % ex[EX_BUCKETS[1]],
        "| 250日超额 | >-5pp | %d |" % ex[EX_BUCKETS[2]],
        "| 趋势（个股） | 多 | %d |" % tr["多"],
        "| 趋势（个股） | 空 | %d |" % tr["空"],
        "| 趋势（个股） | 缠绕 | %d |" % tr["缠绕"],
        "| 趋势（个股） | 样本不足 | %d |" % tr["样本不足"],
        "",
        "## 三、逐案例明细",
        "",
        "| code | 跟踪天数 | 触发次数 | 最新超额(pp) | 最新趋势 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    if cases:
        for c in cases:
            exs = "n/a" if c["ex250"] is None else "%+.1f" % c["ex250"]
            lines.append("| %s | %d | %d | %s | %s | %s |"
                         % (c["code"], c["days"], c["trig"], exs,
                            c["trend"], c["status"]))
    else:
        lines.append("| （无） | - | - | - | - | - |")
    lines += [
        "",
        "---",
        "生成时间：%s" % now.strftime("%Y-%m-%d %H:%M:%S"),
        "口径：状态流 reports/weak_track_*.jsonl 按 ts 日落当月过滤；"
        "新增立案=chanlun_pool.jsonl detect_date 落当月按 code 去重；"
        "触发=trig 非空记录；跟踪天数=当月首末记录日历日差+1；"
        "超额/趋势取当月末次记录。纯聚合，不改 weak_track_check.py。",
    ]
    return "\n".join(lines) + "\n"


def _send_feishu(text):
    """经 news_collector/send_feishu.py 文本通道推送。"""
    sys.path.insert(0, NEWS_DIR)
    try:
        from send_feishu import send_text  # noqa: E402
    except Exception as e:
        print("SEND_FAIL import send_feishu: %s" % e)
        return False
    return bool(send_text(text))


def main():
    ap = argparse.ArgumentParser(description="weak 案例库月度复盘")
    ap.add_argument("--month", default=None, help="YYYY-MM，默认上个月")
    ap.add_argument("--send", action="store_true", help="推飞书摘要")
    ap.add_argument("--dry", action="store_true",
                    help="只打印报告，不落盘不推送")
    args = ap.parse_args()
    month = args.month or _default_month()
    if len(month) != 7 or month[4] != "-":
        ap.error("--month 需为 YYYY-MM")

    metrics, cases = collect(month)
    report = render(metrics, cases)

    if args.dry:
        print(report)
        return 0

    out = os.path.join(REPORTS, "weak_monthly_%s.md"
                       % month.replace("-", ""))
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print("REPORT_OK " + out)

    if args.send:
        head = "\n".join(report.splitlines()[:20])
        ok = _send_feishu(head)
        print("SEND_OK" if ok else "SEND_FAIL（报告已落盘，仅推送失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
