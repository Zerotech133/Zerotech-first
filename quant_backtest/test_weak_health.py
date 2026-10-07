# -*- coding: utf-8 -*-
"""weak_track_health（§16）任务群健康守护验证（K35-ac · 可重复跑）。

T1 全绿：良性集全过（0/267009/267011、Ready、计划正常）无 issue
T2 状态异常：产线级非 Ready 报；案例级 Disabled 略过、Faulted 报
T3 结果异常：267014 超时报；Archive rc=2 等授权不报；未知码报
T4 错失运行：无下次计划报；next<now 报；next 未来不报
T5 字段/文案：detail 每任务两行+修复动作；模板渲染
T6 卡片：red header+detail 块
T7 真实 CLI：当前任务族全绿 → 静默 rc=0（或异常形态）

全部 PASS 退出码 0。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_health as wh        # noqa: E402
import weak_track_card as wcard      # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
fails = []


def check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       "｜" + detail if detail and not cond else ""))
    if not cond:
        fails.append(name)


def t(name, state, lrc, nxt="2026-12-01 08:00", last="2026-10-01 08:00"):
    return {"name": name, "state": state, "lrc": lrc,
            "last": last, "next": nxt}


def names(bad):
    return [c["name"] for c in bad]


def main():
    now = "2026-10-03 04:00"
    # T1 全绿（良性集 + 案例级 Ready）
    ok = [t("WeakScan", "Ready", 0), t("WeakMonthlySummary", "Ready", 267009,
                                       nxt="2026-11-01 08:30"),
          t("WeakMonthlyLearn", "Ready", 0, nxt="2026-11-01 08:40"),
          t("WeakMonthlyArchive", "Ready", 2, nxt="2026-11-01 08:50"),
          t("WeakWeeklyReport", "Ready", 267011, nxt="2026-10-06 15:50"),
          t("WeakTrack301216", "Ready", 0)]
    check("T1 全绿", wh.health_cases(ok, now=now) == [])
    # T2 状态异常
    bad2 = wh.health_cases(
        [t("WeakScan", "Disabled", 0), t("WeakTrack301216", "Disabled", 0),
         t("WeakTrack600000", "Faulted", 0)], now=now)
    check("T2 产线级非Ready报", "WeakScan" in names(bad2))
    check("T2 案例级Disabled略过", "WeakTrack301216" not in names(bad2))
    check("T2 案例级Faulted报", "WeakTrack600000" in names(bad2))
    # T3 结果异常
    bad3 = wh.health_cases(
        [t("WeakScan", "Ready", 267014), t("WeakMonthlyArchive", "Ready", 2),
         t("WeakWeeklyReport", "Ready", 5), t("WeakTrack301216", "Ready", 3)],
        now=now)
    check("T3 超时报", "WeakScan" in names(bad3)
          and any("LastResult=267014" in "、".join(c["issues"])
                  for c in bad3))
    check("T3 Archive rc=2 豁免", "WeakMonthlyArchive" not in names(bad3))
    check("T3 未知码报", names(bad3).count("WeakWeeklyReport") == 1
          and "WeakTrack301216" in names(bad3))
    # T4 错失运行
    bad4 = wh.health_cases(
        [t("WeakScan", "Ready", 0, nxt=""),
         t("WeakWeeklyReport", "Ready", 0, nxt="2026-10-02 15:50"),
         t("WeakTrack301216", "Ready", 0, nxt="")], now=now)
    check("T4 无计划报", any("无下次运行计划" in "、".join(c["issues"])
                             for c in bad4 if c["name"] == "WeakScan"))
    check("T4 已错过报", any("已错过计划" in "、".join(c["issues"])
                             for c in bad4
                             if c["name"] == "WeakWeeklyReport"))
    check("T4 案例级无计划不报", "WeakTrack301216" not in names(bad4))
    # T5 字段/文案
    f = wh.health_fields(bad2 + bad4)
    check("T5 detail 两行式", "WeakScan：状态异常(Disabled)" in f["detail"]
          and "Enable-ScheduledTask" in f["detail"]
          and "→" in f["detail"])
    msg = wh.build_health(f)
    check("T5 文案", "WEAK HEALTH" in msg and "异常任务" in msg)
    # T6 卡片
    c = wcard.health_card(f)
    check("T6 卡片", c["header"]["template"] == "red"
          and any("Enable-ScheduledTask" in json.dumps(e, ensure_ascii=False)
                  for e in c["elements"]))
    # T7 真实 CLI（当前应全绿静默；若异常则输出形态）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_health.py")],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    check("T7 CLI rc=0", r.returncode == 0,
          r.stdout[-150:] + r.stderr[-150:])
    check("T7 形态", "全部健康" in r.stdout or "WEAK HEALTH" in r.stdout)

    print("\n%s（%d 项失败）" % ("ALL PASS" if not fails else "FAILED: %s" % fails,
                               len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
