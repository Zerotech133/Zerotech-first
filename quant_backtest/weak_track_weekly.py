# -*- coding: utf-8 -*-
"""§15 周报聚合推送（weak 产线第 15 节 · K35-z 新增）。

聚合 §12 巡检（在案健康度）+ §13 审计（已结案完整性）为一张周报卡，
每周一主动汇报——**与两节各自的静默纪律分层**：§12/§13 单独跑是
「健康不打扰」（异常才喊），§15 是「定期主动汇报」（全绿也推轻量版，
让用户知道产线活着、口径未漂）。

  在案侧（§12）：超龄/停滞/任务失联三检测 → 催办行动
  结案侧（§13）：字段/归档/档案/任务停/B类新案五审计 → 修复动作

默认预览；--send 推送 indigo 周报卡 📋（--text 回退文本）。
文案外置 weak_track_weekly_tpl.txt（判例沿用，坏模板回退内置）。

用法:
  python weak_track_weekly.py                # 预览
  python weak_track_weekly.py --send         # 推送周报卡
  python weak_track_weekly.py --send --text  # 推送文本
"""

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_watch as ww  # noqa: E402  (§12 巡检)
import weak_track_audit as wa  # noqa: E402  (§13 审计)
import weak_track_close as wc  # noqa: E402  (_load/_case_name)
import logging

TPL = os.path.join(HERE, "weak_track_weekly_tpl.txt")
STATE = os.path.join(HERE, "weak_weekly_state.json")
REP = os.path.abspath(os.path.join(HERE, "..", "reports"))
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
SHADOW_SCRIPT = os.path.join(HERE, "shadow_weekly_report.py")


def shadow_weekly_tail():
    """[20261014_p1_tail] 任务尾部注记式追加影子对账周报（P1 尾巴小件3）。

    背景：影子周报（W5 shadow-report-dev）原靠手动 CLI / WorkBuddy 周五
    自动化，计划建议同时在产线 WeakWeeklyReport（周一 15:50）任务尾部
    顺带刷新产物——只改代码入口，不注册/不修改计划任务。
    注记式：subprocess 调 shadow_weekly_report.py --json-only（该脚本
    只读外部数据、只写自己名下 reports/shadow_weekly_report_data.json
    与 .md 两个产物，幂等零副作用、不推飞书）；超时 300s；任何失败
    只落日志，绝不影响 §15 周报主流程。
    回退：删除本函数与 main() 内两处调用即可（零其他耦合）。
    """
    if not os.path.isfile(SHADOW_SCRIPT):
        print("SHADOW_TAIL skip：脚本不存在 %s" % SHADOW_SCRIPT)
        return
    try:
        r = subprocess.run(
            [sys.executable, SHADOW_SCRIPT, "--json-only"],
            cwd=HERE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        tail = (r.stdout or "").strip().splitlines()
        print("SHADOW_TAIL rc=%s %s" % (r.returncode, tail[-1] if tail else "(无输出)"))
        if r.returncode != 0 and (r.stderr or "").strip():
            print("SHADOW_TAIL stderr: %s" % r.stderr.strip()[:200])
    except Exception as e:
        print(
            "SHADOW_TAIL FAIL（只落日志，不影响周报主流程）: %s: %s"
            % (type(e).__name__, e)
        )


def weekly_activity(cases, rep=None, week_start=None):
    """周窗口活动统计（K35-ab）：新立案/结案/打点行/触发+活跃明细。

    口径：week_start（默认本周一）至今；新立案=状态流首行 ts 落周内
    （含已归档）；结案=close md ts 落周内；打点=周内状态流行数；触发=
    周内 trig 非空的行数（§8 复评打点标记）。
    """
    rep = rep or REP
    ws = week_start or (
        datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    )
    new_open = rows_week = trig_week = 0
    act = []
    for fp in sorted(glob.glob(os.path.join(rep, "weak_track_*.jsonl"))):
        rows = wc._load(fp)
        if not rows:
            continue
        wk = [r for r in rows if str(r.get("ts", ""))[:10] >= ws.strftime("%Y-%m-%d")]
        if str(rows[0].get("ts", ""))[:10] >= ws.strftime("%Y-%m-%d"):
            new_open += 1
        rows_week += len(wk)
        nt = sum(1 for r in wk if r.get("trig"))
        trig_week += nt
        if wk:
            c6 = str(rows[-1].get("code", "?")).split(".")[0]
            act.append((c6, wc._case_name(c6), len(wk), nt))
    closed_week = sum(1 for c in cases if c["ts"] >= ws.strftime("%Y-%m-%d"))
    detail = "\n".join(
        "- %s %s：打点 %s 行%s" % (c6, nm or "", n, ("·触发 %s" % t) if t else "")
        for c6, nm, n, t in act
    )
    return {
        "new_open": new_open,
        "closed_week": closed_week,
        "rows_week": rows_week,
        "trig_week": trig_week,
        "act_detail": detail,
    }


def _trend_text(closed_total, open_bad, closed_bad):
    """与上周 state 对比 → 环比文案（首期显示「首期无环比」）。"""
    if os.path.isfile(STATE):
        try:
            prev = json.load(open(STATE, encoding="utf-8"))
            d_c = closed_total - int(prev.get("closed", closed_total))
            d_o = open_bad - int(prev.get("open_bad", open_bad))
            d_a = closed_bad - int(prev.get("closed_bad", closed_bad))
            fmt = lambda d: ("+%d" % d) if d > 0 else str(d)  # noqa: E731
            return "结案 %s｜巡检异常 %s｜审计异常 %s" % (fmt(d_c), fmt(d_o), fmt(d_a))
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_weekly.py", exc)
    return "首期无环比"


def weekly_fields(watch, audit, act, trend):
    """两节结果+周内活动 → 周报字段 dict（文案与卡片共用单一数据源）。

    watch/audit=watch_fields/audit_fields 产物；act=weekly_activity 产物；
    trend=环比文案。green/verdict/extra 语义见 build_weekly。
    """
    green = not watch["n_bad"] and not audit["n_bad"]
    parts = []
    if watch["n_bad"]:
        parts.append("【巡检明细（§12）】\n" + watch["detail"])
    if audit["n_bad"]:
        parts.append("【审计明细（§13）】\n" + audit["detail"])
    return {
        "n_open": watch["n"],
        "n_open_bad": watch["n_bad"],
        "n_closed": audit["n"],
        "n_closed_bad": audit["n_bad"],
        "green": green,
        "trend": trend,
        "new_open": act["new_open"],
        "closed_week": act["closed_week"],
        "rows_week": act["rows_week"],
        "trig_week": act["trig_week"],
        "act_detail": act["act_detail"],
        "verdict": (
            "本周全绿：无超龄/停滞/失联，结案产物五项全过。"
            if green
            else "存在待办，见下方明细。"
        ),
        "extra": "\n".join(parts),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "wdetail": watch["detail"],
        "adetail": audit["detail"],
    }


def build_weekly(f):
    """周报字段 → 文本文案（外置模板，坏模板回退内置）。

    条件块（act_detail/recent/extra）空值时模板占位留空行，输出前统一
    压缩连续空行（K35-ab 判例：静态模板无法条件渲染的折衷）。
    f 需含 recent（main 经 wcard.recent_lines 注入，本函数纯消费）。
    """
    if os.path.isfile(TPL):
        try:
            text = "".join(
                l
                for l in open(TPL, encoding="utf-8").read().splitlines(True)
                if not l.lstrip().startswith("#")
            )
            return re.sub(r"\n{3,}", "\n\n", text.format(**f).rstrip("\n"))
        except (KeyError, IndexError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_weekly.py", exc)
    return re.sub(
        r"\n{3,}",
        "\n\n",
        (
            "📋 [WEAK WEEKLY] weak 产线周报（§15）\n"
            "在案 {n_open} 案（巡检异常 {n_open_bad}）｜已结案 {n_closed} 例"
            "（审计异常 {n_closed_bad}）｜较上周：{trend}\n"
            "本周动态：新立案 {new_open}｜结案 {closed_week}｜打点 {rows_week} 行"
            "｜触发 {trig_week} 次\n{act_detail}\n{recent}\n{verdict}\n{extra}"
        )
        .format(**f)
        .rstrip("\n"),
    )


def main():
    ap = argparse.ArgumentParser(description="§15 周报聚合推送")
    ap.add_argument("--send", action="store_true", help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    ap.add_argument(
        "--max-days", type=int, default=60, help="§12 超龄阈值（天，默认 60）"
    )
    ap.add_argument(
        "--stall-days", type=int, default=3, help="§12 停滞阈值（天，默认 3）"
    )
    a = ap.parse_args()
    import weak_track_summary as wts

    cases = wts.parse_cases(wts.CASES)
    wf = ww.watch_fields(ww.watch_cases(max_days=a.max_days, stall_days=a.stall_days))
    af = wa.audit_fields(wa.audit_cases())
    act = weekly_activity(cases)
    f = weekly_fields(wf, af, act, _trend_text(af["n"], wf["n_bad"], af["n_bad"]))
    import weak_track_card as wcard

    f = dict(f, recent=wcard.recent_lines(cases))
    if not f["n_open"] and not f["n_closed"]:
        print("产线空转（无在案、无已结案）——周报跳过本期（state 不更新）")
        shadow_weekly_tail()  # 空转也顺带刷影子周报（独立于产线管道）
        return 0
    msg = build_weekly(f)
    print(msg)
    # 环比基线：本期出报后落盘（下期对比用；预览不写 state）
    if a.send:
        with open(STATE, "w", encoding="utf-8") as fo:
            json.dump(
                {
                    "closed": af["n"],
                    "open_bad": wf["n_bad"],
                    "closed_bad": af["n_bad"],
                    "ts": time.strftime("%Y-%m-%d %H:%M"),
                },
                fo,
            )
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.weekly_card(f), ensure_ascii=False, indent=1))
        shadow_weekly_tail()  # 预览路径同样顺带刷影子周报（注记式）
        return 0
    if not a.text:
        import weak_track_card as wcard

        print("\n推送(卡片)：" + wcard.send_card(wcard.weekly_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_weekly.txt")
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
    shadow_weekly_tail()  # [20261014_p1_tail] 任务尾部追加影子周报（注记式）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
