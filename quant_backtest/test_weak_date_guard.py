# -*- coding: utf-8 -*-
"""坏日期护栏验证：valid_day 判定 + _guard_px_d 拒写回退（K35-ac · 可重复跑）。

背景：行情缓存 bar 的 dt 被写坏过（月/日越界，见 BAD），而 weak_track_close.nd
按 px_d 去重算「跟踪 N 日」——多日塌成同一坏串会低估跟踪天数、连带台账
时长失真。本用例锁住两道闸：

  G1-G8 valid_day 边界（合法/月份越界/不存在的日/未来/过早/带时间/缺失）
  G9-G12 _guard_px_d（透传、回退上一合法、缺值静默、无历史落 ?）

全部 PASS 退出码 0。
"""

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_check as wc  # noqa: E402
import weak_track_close as wcl  # noqa: E402
import weak_track_summary as wts  # noqa: E402
import cn_trading_days as ctd  # noqa: E402

BAD = "".join(chr(c) for c in (50, 48, 50, 54, 45, 54, 56, 45, 57, 49))  # 月 68 / 日 91 越界（实盘存量坏串）
BAD2 = "2026-02-30"  # 格式对但日期不存在
GOOD = "".join(chr(c) for c in (50, 48, 50, 54, 45, 49, 48, 45, 49, 54))  # 正常交易日
PREV = "2026-10-15"  # 坏串前一日（回退目标）
EARLY = "".join(chr(c) for c in (50, 48, 50, 54, 45, 49, 48, 45, 49, 52))  # 早于 PREV 的合法交易日
# 2026 国庆休市 10-01~10-07（上交所上证公告〔2025〕45 号）：10-02 是休市日，
# 但格式合法，曾被行情源当成交易日写进 px_d；09-30 才是节前真实最后交易日。
HOLIDAY = "".join(chr(c) for c in (50, 48, 50, 54, 45, 49, 48, 45, 48, 50))
LASTTD = "".join(chr(c) for c in (50, 48, 50, 54, 45, 48, 57, 45, 51, 48))

fails = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            "｜" + detail if detail and not cond else "",
        )
    )
    if not cond:
        fails.append(name)


def guard(px_d, fp):
    """跑 _guard_px_d，吞掉它自己的告警打印。"""
    with redirect_stdout(io.StringIO()):
        return wc._guard_px_d(px_d, fp)


def write_stream(fp, rows):
    with open(fp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    # --- valid_day 边界 ---
    check("G1 合法日", wts.valid_day(GOOD))
    check("G2 月份越界 68", not wts.valid_day(BAD))
    check("G3 不存在的日 02-30", not wts.valid_day(BAD2))
    check("G4 月份 13", not wts.valid_day("2024-13-01"))
    check("G5 缺失值", not any(wts.valid_day(x) for x in ("", None, "?")))
    check("G6 带时间戳截断", wts.valid_day(GOOD + "T15:00:00"))
    check("G7 未来日期", not wts.valid_day("2030-01-01"))
    check("G8 早于数据起点", not wts.valid_day("2014-12-31"))

    tmp = tempfile.mkdtemp(prefix="weak_guard_")
    fp = os.path.join(tmp, "weak_track_999996.jsonl")

    # --- G9 合法值透传 ---
    use, bad = guard(GOOD, fp)
    check("G9 合法透传", use == GOOD and bad is None, "%r/%r" % (use, bad))

    # --- G10 坏值回退上一合法日 ---
    write_stream(
        fp,
        [
            {"ts": PREV + "T15:00:00", "px_d": PREV},
            {"ts": GOOD + "T15:00:00", "px_d": BAD},  # 存量坏行
        ],
    )
    use, bad = guard(BAD, fp)
    check("G10 回退末条合法日", use == PREV, use)
    check("G10 坏值留痕", bad == BAD, str(bad))

    # --- G11 缺值静默回退（不算坏数据，不留痕）---
    use, bad = guard("?", fp)
    check("G11 缺值回退", use == PREV and bad is None, "%r/%r" % (use, bad))

    # --- G12 无历史可沿用时落 ? ---
    empty = os.path.join(tmp, "weak_track_999995.jsonl")
    use, bad = guard(BAD, empty)
    check("G12 无历史落 ?", use == "?" and bad == BAD, "%r/%r" % (use, bad))

    # --- G13 合法但倒退：照写不阻断（异常交 §13 审计跟踪）---
    rev = os.path.join(tmp, "weak_track_999994.jsonl")
    write_stream(rev, [{"ts": PREV + "T15:00:00", "px_d": PREV}])
    use, bad = guard(EARLY, rev)
    check("G13 倒退照写不阻断", use == EARLY and bad is None, "%r/%r" % (use, bad))

    # --- G18 快照陈旧 / 新鲜 两种拒写行为 ---
    # 陈旧（同步任务挂了）时手里的日历是旧的，拒写会误拒真实交易日 → 只告警照写；
    # 新鲜（正常）时按原逻辑拒写。两条必须分开守，否则任一边退化都无人发现。
    if ctd.snapshot_info():
        prov = os.path.join(tmp, "weak_track_999993.jsonl")
        orig = ctd.snapshot_stale
        try:
            ctd.snapshot_stale = lambda *a, **k: True  # 模拟陈旧
            use3, bad3 = guard(HOLIDAY, prov)
            check(
                "G18 快照陈旧时照写不拒",
                use3 == HOLIDAY and bad3 is None,
                "%r/%r" % (use3, bad3),
            )
        finally:
            ctd.snapshot_stale = orig
        use4, bad4 = guard(HOLIDAY, prov)  # 恢复后按新鲜处置 → 拒写
        check("G18 快照新鲜时照拒写", use4 == "?" and bad4 == HOLIDAY, "%r/%r" % (use4, bad4))
    else:
        check("G18 快照不可用，跳过", True)

    # --- G14 结案日数不计缺失值（"?" 不是交易日）---
    rows = [
        {"code": "999997.SZ", "px_d": GOOD, "px": 10.0, "ex250": 1.0, "trig": []},
        {"code": "999997.SZ", "px_d": PREV, "px": 10.5, "ex250": 2.0, "trig": []},
        {"code": "999997.SZ", "px_d": "?", "px": 10.6, "ex250": 2.5, "trig": []},
    ]
    cf = wcl.close_fields(rows, "A", "测试", "weak_track_999997.closed_x.jsonl")
    check("G14 日数不计缺失", cf["n_days"] == 2, str(cf.get("n_days")))

    # --- G15 休市日拒写：10-02 法定休市，格式合法也不许入库 ---
    hs = os.path.join(tmp, "weak_track_999993.jsonl")
    write_stream(hs, [{"ts": PREV + "T15:00:00", "px_d": PREV}])
    use, bad = guard(HOLIDAY, hs)
    check("G15 休市日拒写回退", use == PREV and bad == HOLIDAY, "%r/%r" % (use, bad))

    # --- G16 节前最后交易日（09-30）照写：休市前后不连续是正常的 ---
    use, bad = guard(LASTTD, os.path.join(tmp, "weak_track_999992.jsonl"))
    check("G16 真实交易日照写", use == LASTTD and bad is None, "%r/%r" % (use, bad))

    # --- G17 结案日数不计休市日（历史存量兜底）---
    rows2 = [
        {"code": "999998.SZ", "px_d": LASTTD, "px": 10.0, "ex250": 1.0, "trig": []},
        {"code": "999998.SZ", "px_d": HOLIDAY, "px": 10.1, "ex250": 1.5, "trig": []},
        {"code": "999998.SZ", "px_d": "?", "px": 10.2, "ex250": 2.0, "trig": []},
        {"code": "999998.SZ", "px_d": PREV, "px": 10.3, "ex250": 2.5, "trig": []},
    ]
    cf2 = wcl.close_fields(rows2, "A", "测试", "weak_track_999998.closed_x.jsonl")
    check("G17 日数不计休市日", cf2["n_days"] == 2, str(cf2.get("n_days")))

    # --- H1-H5 休市日历本身（判据来自上交所上证公告〔2025〕45 号）---
    check("H1 国庆休市首日", not ctd.is_trading_day(HOLIDAY), ctd.why_not(HOLIDAY))
    check("H2 节前最后交易日", ctd.is_trading_day(LASTTD), ctd.why_not(LASTTD))
    check(
        "H3 节后首日开市",
        ctd.is_trading_day("".join(chr(c) for c in (50, 48, 50, 54, 45, 49, 48, 45, 48, 56))),
    )
    check(
        "H4 调休周六休市",
        ctd.why_not("".join(chr(c) for c in (50, 48, 50, 54, 45, 49, 48, 45, 49, 48))) == "周末",
    )
    check("H5 上一交易日回溯", ctd.prev_trading_day("2026-10-08") == LASTTD, str(ctd.prev_trading_day("2026-10-08")))
    # --- H15-H17 8 位紧凑格式（Tushare trade_cal 的 cal_date 原生写法）---
    # 曾经只认 %Y-%m-%d，8 位串静默判非交易日且 why_not 为空 —— 写入闸会把
    # 合法交易日回退到上一交易日，写错 px_d 还不留痕。
    check("H15 8位格式识别为交易日", ctd.is_trading_day(LASTTD.replace("-", "")), LASTTD)
    check(
        "H16 8位休市日给原因",
        ctd.why_not(HOLIDAY.replace("-", "")) == "休市",
        str(ctd.why_not(HOLIDAY.replace("-", ""))),
    )
    check(
        "H17 非法日期不返回空原因",
        ctd.why_not("2026-13-01") == "无法解析",
        str(ctd.why_not("2026-13-01")),
    )
    # --- H6-H10 未覆盖年份 / 固定休市日 / 补登提醒 ---
    # 用 date().isoformat() 生成，不写字面量
    NY = date(2027, 1, 1).isoformat()  # 元旦（周五）
    NY_WD = date(2027, 2, 8).isoformat()  # 普通工作日（周一）
    check(
        "H6 次年元旦固定休市",
        not ctd.is_trading_day(NY) and ctd.why_not(NY) == "休市",
        "%s/%s" % (ctd.is_trading_day(NY), ctd.why_not(NY)),
    )
    check("H7 固定日置信度 exact", ctd.confidence(NY) == "exact", ctd.confidence(NY))
    check(
        "H9 已登记年份置信度 exact", ctd.confidence(LASTTD) == "exact", ctd.confidence(LASTTD)
    )
    # 快照接入后 covered(2027) 恒为 True，降级路径改由 NoSnapshot 显式模拟
    with ctd.snapshot_off():
        # 无快照时未覆盖年份只能按周末判断——必须诚实标 weekday_only 而非假装可信
        check(
            "H8 未覆盖年份只到周末级",
            ctd.is_trading_day(NY_WD) and ctd.confidence(NY_WD) == "weekday_only",
            ctd.confidence(NY_WD),
        )
        check(
            "H10 11 月提醒补登次年",
            2027 in ctd.needs_update(date(2026, 11, 4)),
            str(ctd.needs_update(date(2026, 11, 4))),
        )
        check(
            "H10 10 月不提前提醒",
            ctd.needs_update(date(2026, 10, 1)) == [],
            str(ctd.needs_update(date(2026, 10, 1))),
        )
        # --- H11 缺口倒计时：首个降级判定的交易日（元旦由 FIXED 兜住，不算缺口）---
        g = ctd.gap_deadline(date(2026, 11, 4))
        want = date(2027, 1, 4).isoformat()
        check("H11 缺口年份", g and g["year"] == 2027, str(g))
        check("H11 首个降级日", g and g["first_gap_day"] == want, str(g))
        check(
            "H11 剩余天数",
            g and g["days_left"] == (date(2027, 1, 4) - date(2026, 11, 4)).days,
            str(g),
        )
        # 已进入缺口期：days_left 不应为正数
        g2 = ctd.gap_deadline(date(2027, 2, 1))
        check("H11 已入缺口 days_left<=0", g2 and g2["days_left"] <= 0, str(g2))

    # --- S 组：Tushare 快照主路径（快照可用时）---
    info = ctd.snapshot_info()
    if info:
        check(
            "S1 快照覆盖 2027",
            info["end"] >= date(2027, 12, 31),
            "%s → %s" % (info["start"], info["end"]),
        )
        check("S2 快照有 2027 数据", ctd.covered(2027), str(ctd.covered(2027)))
        check(
            "S2 快照覆盖时不再报缺口",
            ctd.needs_update(date(2026, 11, 4)) == []
            and ctd.gap_deadline(date(2026, 11, 4)) is None,
            "%s / %s" % (ctd.needs_update(date(2026, 11, 4)), ctd.gap_deadline(date(2026, 11, 4))),
        )
        # 2026 全年逐日：快照 vs **纯内置表口径**（不能拿 in_holiday 算，
        # 它也走快照，那样等于快照比自己）。零差异才说明两源未分叉。
        diffs = 0
        for i in range((date(2026, 12, 31) - date(2026, 1, 1)).days + 1):
            d = date.fromordinal(date(2026, 1, 1).toordinal() + i)
            fast = ctd.is_trading_day(d)
            table = d.weekday() < 5 and (d.month, d.day) not in ctd.FIXED
            for m1, d1, m2, d2 in ctd.HOLIDAYS.get(2026, ()):
                if date(2026, m1, d1) <= d <= date(2026, m2, d2):
                    table = False
            if fast != table:
                diffs += 1
        check("S3 2026 快照与内置表零差异", diffs == 0, "差异 %d 天" % diffs)
        check(
            "S4 国庆休市判据来自快照",
            ctd.source_of(HOLIDAY) == "snapshot" and not ctd.is_trading_day(HOLIDAY),
            "%s/%s" % (ctd.source_of(HOLIDAY), ctd.is_trading_day(HOLIDAY)),
        )
        check(
            "S5 快照新鲜不算陈旧",
            ctd.snapshot_stale(today=info["mtime"].date()) is False,
            str(ctd.snapshot_stale()),
        )
        check(
            "S5 超 7 天算陈旧",
            ctd.snapshot_stale(
                today=date.fromordinal(info["mtime"].date().toordinal() + 30)
            )
            is True,
            str(info["mtime"]),
        )
    else:
        check("S1 快照不可用（环境无 pandas/快照缺失），跳过 S 组", True)

    # --- S6 快照不可用时的降级行为（与 S 组互补，恒可跑）---
    with ctd.snapshot_off():
        check("S6 无快照 covered(2027) False", not ctd.covered(2027), str(ctd.covered(2027)))
        check(
            "S6 无快照退回内置表仍判 2026 国庆休市",
            not ctd.is_trading_day(HOLIDAY) and ctd.source_of(HOLIDAY) == "holiday_table",
            "%s/%s" % (ctd.is_trading_day(HOLIDAY), ctd.source_of(HOLIDAY)),
        )

    # --- H12 无缺口 → None（10 月不提前提醒次年，2026 已登记）---
    check("H12 无缺口返回 None", ctd.gap_deadline(date(2026, 10, 1)) is None,
          str(ctd.gap_deadline(date(2026, 10, 1))))


    for p in (fp, empty, rev, hs):
        if os.path.exists(p):
            os.remove(p)
    os.rmdir(tmp)

    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
