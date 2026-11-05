# -*- coding: utf-8 -*-
"""§13 结案质量审计推送（weak 产线第 13 节 · K35-x 新增）。

§12 盯在案失速（超龄/停滞/失联），§13 盯已结案侧完整性——对每个
weak_close_*.md 九项审计，发现 §9 走到一半或数据残缺：

  1. 字段缺失：头部 ts/open/chg 任一空（§10/§11 依赖的解析字段）；
  2. 归档缺失：声明的 weak_track_<c6>.closed_<ymd>.jsonl 不在 reports/；
  3. 档案缺失：案例档案 weak_review_<c6>_<open>.md 不在 weak_cases/；
  4. 任务未停：结案后每日任务仍 Ready（B 类滚动新案重启同任务除外）；
  5. B 类新案缺：B 类结案但既无「🔄 新案已建」也无「🔄 新案未立」
     （生成失败/未生成）；声明未立是合法终态，但须人写进报告，
     机器不从结案结论自动推断；
  6. 日期坏点：状态流（含已归档流）里 ts/px_d 非法——坏串会让
     close 按 px_d 去重算的「跟踪 N 日」塌缩失真（写入端已由
     weak_track_check._guard_px_d 拦截，此项扫历史存量）；
  7. 非交易日：px_d 是合法日期但当天不开市（周末/已登记休市）——
     多见于节假日行情缺数被补位，同样会把休市日算成交易日（判据见
     cn_trading_days.py，来源上交所休市公告）；
  8. 日期倒退：px_d 比上一条小（只在两侧都是交易日时才判——休市前后
     px_d 本就不连续，把「休市日值 → 真实最后交易日」算倒退会误伤）；
  9. 日历未覆盖：px_d 落在尚未登记休市安排的年份（如 2027）——此时只能
     按周末判断，节假日会被漏判成交易日。2027 元旦已由固定休市日兜住，
     其余须等上交所年度公告后补登 cn_trading_days.HOLIDAYS。

全部干净 → 打印提示 rc=0 不推送（不打扰）；有异常 → --send 推送
turquoise 审计卡（🧾）。默认预览。文案外置 weak_track_audit_tpl.txt
（判例沿用，坏模板回退内置）。

用法:
  python weak_track_audit.py                # 预览
  python weak_track_audit.py --send         # 推送审计卡
  python weak_track_audit.py --send --text  # 推送文本
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cn_trading_days as ctd  # noqa: E402
import weak_track_summary as wts  # noqa: E402
import weak_track_watch as ww  # noqa: E402
import logging

REP = os.path.join(os.path.dirname(HERE), "reports")
CASES = os.path.join(REP, "weak_cases")
TPL = os.path.join(HERE, "weak_track_audit_tpl.txt")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

_FIX = {
    "字段缺失": "重跑 §9 结案（close md 头部解析字段不全）",
    "归档缺失": "检查 reports/ 归档改名是否执行（§9 第 3 步）",
    "档案缺失": "补案例档案或更正头部档案行",
    "任务未停": "Disable 每日任务（§9 第 2 步），或确认 B 类新案已建",
    "B 类新案缺": (
        "手动跑命令块⑨ 生成新案；结案已定性不立新案的，在结案报告补"
        "「🔄 新案未立：<依据>」行"
    ),
    "日期坏点": "行情源坏日期已入库：核对 px_d 是否多日塌成同一串，必要时修流重算时长",
    "非交易日": "px_d 落在周末/休市日（行情源把非交易日当交易日）——核当日行情是否未刷新，修流重算时长",
    "日历未覆盖": (
        "px_d 落在未登记休市安排的年份（如 2027），只能按周末判断、节假日会漏判"
        "——补登 cn_trading_days.HOLIDAYS 该年公告区间"
    ),
    "日期倒退": "px_d 序列回退：多半是行情缓存回滚/数据源陈旧，核当日行情是否未刷新",
}


def _bad_date_stats(rep, c6):
    """状态流日期契约体检 → dict(bad, bad_sample, hol, hol_sample, back, back_sample)。

    口径（K35-ad，2026-11-04 按真实流 + 上交所休市公告校准）：
      * 坏点 = 字段存在且非缺失、但取值不是真实日期（月日越界等）。
        **缺失（""/"?"/None）不算坏**——那是行情缺数，不是日期错。
        历史曾把 px_d="?" 记成坏日期，属误判，此处显式排除。
      * 非交易日 = px_d 格式合法、但当天不开市（周末 / 已登记休市）。
        真实案例：2026 国庆 10-01~10-07 休市，流里 px_d 被写成 2026-10-02。
        这类值会让 weak_track_close.nd 把休市日算成交易日，虚增跟踪天数。
      * 倒退 = px_d 小于上一条 px_d。**只在两侧都是交易日时才判**——休市
        前后 px_d 本就不连续（10-08 早于 09-30 才是怪事），把「休市日值 → 真
        实最后交易日」算成倒退会把正确的值一起算进异常（301216 就是这种）。
      * 日历未覆盖 = px_d 落在**尚未登记休市安排的年份**（如 2027），此时
        只能按周末判断，节假日会漏判成交易日。这类隐患不报错、只静默失真，
        故单独列出。2027 元旦已由固定休市日兜住，其余待公告补登。

    写入端 weak_track_check._guard_px_d 已拦坏值与非交易日，此处专扫历史存量。
    """
    n, sample = 0, []
    hol, hsample = 0, []
    back, bsample = 0, []
    gap, gsample = 0, []
    prev = ""
    for fp in sorted(glob.glob(os.path.join(rep, "weak_track_%s*.jsonl" % c6))):
        try:
            fh = open(fp, encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "quant_backtest/weak_track_audit.py", exc
            )
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                for k in ("ts", "px_d"):
                    v = r.get(k)
                    if v in (None, "", "?"):
                        continue
                    if not wts.valid_day(v):
                        n += 1
                        if len(sample) < 2:
                            sample.append("%s=%s" % (k, v))
                pd = str(r.get("px_d") or "")
                if not wts.valid_day(pd):
                    continue
                pd = pd[:10]
                why = ctd.why_not(pd)
                if why:
                    hol += 1
                    if len(hsample) < 2:
                        hsample.append("%s(%s)" % (pd, why))
                    continue  # 非交易日不参与倒退判定
                if ctd.confidence(pd) == "weekday_only":
                    gap += 1
                    if len(gsample) < 2:
                        gsample.append("%s(%s年未登记)" % (pd, pd[:4]))
                if prev and pd < prev:
                    back += 1
                    if len(bsample) < 2:
                        bsample.append("%s→%s" % (prev, pd))
                prev = pd
    return {
        "bad": n,
        "bad_sample": "、".join(sample),
        "hol": hol,
        "hol_sample": "、".join(hsample),
        "back": back,
        "back_sample": "、".join(bsample),
        "gap": gap,
        "gap_sample": "、".join(gsample),
    }


def _task_ready(c6, tm):
    """该标的关联每日任务是否 Ready（含 301216 首例任务名特例）。"""
    keys = [k for k in tm if c6 in k]
    if c6 == "301216":
        keys.append("WeakTrackCheck")
    return any(tm.get(k) == "Ready" for k in keys)


def _ncase_line(cases_dir, md):
    """close md 尾部的 B 类新案行（🔄 新案已建 / 新案未立 / 生成失败），无则空。

    认三种：已建（滚动成功）、未立（结案即定性不立新案，合法终态）、
    生成失败（真异常）。调用方按前缀区分，此处只取原文。
    """
    try:
        txt = open(os.path.join(cases_dir, md), encoding="utf-8").read()
        for ln in reversed(txt.splitlines()):
            if ln.startswith("🔄"):
                return ln
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_audit.py", exc)
    return ""


def audit_cases(cases_dir=None, rep=None, task_map=None):
    """全部结案 md → 审计结果列表（issues 非空即异常）。"""
    cases_dir = cases_dir or CASES
    rep = rep or REP
    tm = task_map if task_map is not None else ww._task_map()
    out = []
    for c in wts.parse_cases(cases_dir):
        c6 = c["code"].split(".")[0]
        issues = []
        missing = [k for k in ("ts", "open") if not c[k]] + (
            ["chg"] if c["chg"] is None else []
        )
        if missing:
            issues.append("字段缺失:" + "+".join(missing))
        if c["arch"] and not os.path.isfile(os.path.join(rep, c["arch"])):
            issues.append("归档缺失")
        if c["open"] and not glob.glob(
            os.path.join(cases_dir, "weak_review_%s_%s.md" % (c6, c["open"]))
        ):
            issues.append("档案缺失")
        nc = _ncase_line(cases_dir, c["md"])
        nc_ok = nc.startswith("🔄 新案已建")
        # 「🔄 新案未立」是合法终态：结案结论已定性为破位归档/不立新案时，
        # 本就不该有滚动新案。但这行必须由人写进报告——机器不从结案结论
        # 里自动推断，否则「漏建新案」会被静默当成「不立新案」。
        nc_skip = nc.startswith("🔄 新案未立")
        if c["type"] == "B" and not (nc_ok or nc_skip):
            issues.append("B 类新案缺")
        # 任务未停只认「已建新案」（滚动新案会重启同任务）；声明不立新案时
        # 任务仍 Ready 属真异常，照报。
        if _task_ready(c6, tm) and not (c["type"] == "B" and nc_ok):
            issues.append("任务未停")
        ds = _bad_date_stats(rep, c6)
        if ds["bad"]:
            issues.append("日期坏点:%d行(%s)" % (ds["bad"], ds["bad_sample"]))
        if ds["hol"]:
            issues.append("非交易日:%d行(%s)" % (ds["hol"], ds["hol_sample"]))
        if ds["gap"]:
            issues.append("日历未覆盖:%d行(%s)" % (ds["gap"], ds["gap_sample"]))
        if ds["back"]:
            issues.append("日期倒退:%d处(%s)" % (ds["back"], ds["back_sample"]))
        out.append(
            {
                "code": c["code"],
                "c6": c6,
                "name": c.get("name") or "",
                "type": c["type"],
                "issues": issues,
            }
        )
    return out


def audit_fields(cases):
    """审计结果 → 推送字段 dict（文案与卡片共用单一数据源）。"""
    bad = [c for c in cases if c["issues"]]
    return {
        "n": len(cases),
        "n_bad": len(bad),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "detail": "\n".join(
            "- %s %s（%s）：%s\n  → %s"
            % (
                c["code"],
                c["name"],
                c["type"],
                "、".join(c["issues"]),
                "；".join(_FIX[i.split(":")[0]] for i in c["issues"]),
            )
            for c in bad
        ),
    }


def build_audit(f):
    """审计字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(
                l
                for l in open(TPL, encoding="utf-8").read().splitlines(True)
                if not l.lstrip().startswith("#")
            )
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_audit.py", exc)
    return (
        "🧾 [WEAK AUDIT] 结案质量审计（§13）\n"
        "已结案 {n} 例｜异常 {n_bad} 例｜{ts}\n"
        "{detail}"
    ).format(**f)


def _cal_note():
    """休市日历的数据源、时效与缺口——不参与异常计数。

    快照可用时显示来源与新鲜度（可追溯）；快照不可用或未覆盖某年才报警，
    避免「全绿」被误解成「日历永远可信」。
    """
    info = ctd.snapshot_info()
    ys = ctd.needs_update()
    if info:
        line = "休市日历：Tushare trade_cal 快照 %s 止（%s 更新，%d 行）" % (
            info["end"].isoformat(),
            info["mtime"].strftime("%m-%d %H:%M"),
            info["rows"],
        )
        if ys:
            line += "｜⚠ 快照未覆盖：%s" % "、".join(str(y) for y in ys)
        return line
    if not ys:
        return ""
    tail = ""
    g = ctd.gap_deadline()
    if g and g.get("first_gap_day"):
        tail = "；首个降级判定日 %s（距今 %d 天）" % (
            g["first_gap_day"],
            g["days_left"],
        )
    return (
        "⚠ 休市日历无快照可用，退回内置表：待补登 %s"
        "（恢复 data/tushare/trade_cal/ 快照即可，无需手改）%s"
        % ("、".join(str(y) for y in ys), tail)
    )


def main():
    ap = argparse.ArgumentParser(description="§13 结案质量审计推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    cases = audit_cases()
    if not cases:
        print(
            "暂无已结案案例（%s 下无 weak_close_*.md）——§9 结案后本节才有数据" % CASES
        )
        return 0
    f = audit_fields(cases)
    note = _cal_note()
    if note:
        print(note)
    if not f["n_bad"]:
        print(
            "结案质量审计全绿（%d 例，九项全过：字段/归档/档案/"
            "任务停/B类新案/日期坏点/非交易日/日历未覆盖/日期倒退）" % f["n"]
        )
        return 0
    msg = build_audit(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.audit_card(f), ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.audit_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_audit.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
