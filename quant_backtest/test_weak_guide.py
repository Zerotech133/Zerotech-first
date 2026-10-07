# -*- coding: utf-8 -*-
"""weak_track_guide（§17）产线导航卡验证（K35-ad · 可重复跑）。

T1 结构：五组全在、节覆盖（§0-§8/§8/§9-§17 关键节）
T2 内容：命令/任务名抽检（WeakWeeklyReport/WeakMonthlySummary/
   weak_track_close.py/weak_track_health.py）
T3 文案：build_guide 全组渲染+两行式
T4 卡片：grey header+组块数=GROUPS 数+末块为 note
T5 真实 CLI 预览 rc=0

全部 PASS 退出码 0。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_guide as wg       # noqa: E402
import weak_track_card as wcard     # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
fails = []


def check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       "｜" + detail if detail and not cond else ""))
    if not cond:
        fails.append(name)


def main():
    secs = [s for _, items in wg.GROUPS for s, *_ in items]
    cmds = [c for _, items in wg.GROUPS for *_, c, _ in items]
    # T1 结构
    check("T1 五组", [g for g, _ in wg.GROUPS] == [
        "每日自动（无需操作）", "每周自动", "每月自动", "手动按需",
        "你唯一要做的日常"])
    check("T1 节覆盖", all(s in secs for s in
                            ["§0-§8", "§8", "§9", "§10", "§11", "§14",
                             "§15", "§16", "§17"]))
    # T2 内容抽检
    allc = "｜".join(cmds)
    check("T2 任务名", all(x in allc for x in
                           ["WeakWeeklyReport", "WeakMonthlySummary",
                            "WeakMonthlyLearn", "WeakMonthlyArchive",
                            "WeakTrackCheck", "WeakScan",
                            "WeakDailyHealth", "WeakMonthlyGuide"]))
    check("T2 命令名", all(x in allc for x in
                           ["weak_track_close.py", "weak_track_watch.py",
                            "weak_track_audit.py"]))
    # T3 文案
    msg = wg.build_guide()
    check("T3 全组渲染", "【每日自动（无需操作）】" in msg
          and "【你唯一要做的日常】" in msg)
    check("T3 两行式", "命令/任务：" in msg and "§16 守护" in msg)
    # T4 卡片
    c = wcard.guide_card(wg.GROUPS)
    check("T4 卡片", c["header"]["template"] == "grey"
          and len(c["elements"]) == len(wg.GROUPS) * 2
          and c["elements"][-1]["tag"] == "note")
    check("T4 组块内容", all(any(g in json.dumps(e, ensure_ascii=False)
                                for e in c["elements"])
                             for g, _ in wg.GROUPS))
    # T5 真实 CLI
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_guide.py")],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    check("T5 CLI rc=0", r.returncode == 0,
          r.stdout[-150:] + r.stderr[-150:])
    check("T5 预览形态", "WEAK GUIDE" in r.stdout and "预览模式" in r.stdout)

    print("\n%s（%d 项失败）" % ("ALL PASS" if not fails else "FAILED: %s" % fails,
                               len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
