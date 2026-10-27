# -*- coding: utf-8 -*-
"""weak_track_watch（§12）巡检判定与推送验证（K35-w · 可重复跑）。

T1 健康案：新案+任务 Ready → 无 flags
T2 超龄：立案 > 阈值 → 超龄
T3 停滞：末行 > 阈值 → 停滞
T4 失联：任务缺失/停用 → 任务失联（含结案半途语义）
T5 组合+文案：三 flags 案 + 明细含催办行动行
T6 归档排除：.closed_ 跳过；空目录 []
T7 健康全绿：n_bad=0
T8 真实 CLI 预览：rc=0 且零副作用

隔离：T1-T7 monkeypatch 状态流目录与任务表；全部 PASS 退出码 0。
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_watch as ww  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
TMP = os.path.join(tempfile.gettempdir(), "weak_watch_test")
TODAY = datetime.date.today()
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


def row(code, ts, px=16.0):
    return json.dumps(
        {"code": code, "ts": ts, "px": px, "px_d": ts[:10]}, ensure_ascii=False
    )


def put(c6, first_off, last_off):
    """fake 状态流：首行 first_off 天前、末行 last_off 天前。"""
    f = lambda n: (TODAY - datetime.timedelta(days=n)).strftime("%Y-%m-%d %H:%M")
    code = c6 + (".SH" if c6.startswith("6") else ".SZ")
    fp = os.path.join(TMP, "weak_track_%s.jsonl" % c6)
    open(fp, "w", encoding="utf-8").write(
        row(code, f(first_off)) + "\n" + row(code, f(last_off)) + "\n"
    )


TM_OK = {
    "WeakTrack301216": "Ready",
    "WeakTrackCheck": "Ready",
    "WeakTrack600519": "Ready",
    "WeakTrack000333": "Ready",
}


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)

    put("301216", 10, 1)  # 健康
    put("600519", 70, 1)  # 超龄
    put("000333", 10, 5)  # 停滞
    put("000006", 10, 1)  # 任务失联（TM_OK 无 WeakTrack000006）
    put("999999", 80, 9)  # 三连：超龄+停滞+失联
    open(
        os.path.join(TMP, "weak_track_888888.closed_20260901.jsonl"),
        "w",
        encoding="utf-8",
    ).write(row("888888.SZ", "2026-09-01 15:30"))

    cases = ww.watch_cases(TMP, task_map=TM_OK)
    check("T6 归档排除共 5 案", len(cases) == 5, str(len(cases)))
    g = lambda c6: next(c for c in cases if c["c6"] == c6)
    check("T1 健康案无 flags", g("301216")["flags"] == [])
    check(
        "T1 天数 10/1", (g("301216")["days_open"], g("301216")["days_stall"]) == (10, 1)
    )
    check("T2 超龄", g("600519")["flags"] == ["超龄"])
    check("T3 停滞", g("000333")["flags"] == ["停滞"])
    check("T4 失联", g("000006")["flags"] == ["任务失联"])
    c5 = g("999999")
    check("T5 三连 flags", c5["flags"] == ["超龄", "停滞", "任务失联"])
    check("T5 任务显示缺失", c5["task"] == "缺失")
    f = ww.watch_fields(cases)
    check("T5 计数 5/4", (f["n"], f["n_bad"]) == (5, 4))
    d = f["detail"]
    check("T5 明细含行动", "结案三选一" in d and "Enable 重启" in d and "数据链" in d)
    check("T5 明细健康案不进", "301216" not in d)
    msg = ww.build_watch(f)
    check("T5 文案含头", "WEAK WATCH" in msg and "异常 4 案" in msg)

    # T7：全部健康（只留健康案）
    shutil.rmtree(TMP)
    os.makedirs(TMP)
    put("301216", 10, 1)
    f7 = ww.watch_fields(ww.watch_cases(TMP, task_map=TM_OK))
    check("T7 全绿 n_bad=0", (f7["n"], f7["n_bad"]) == (1, 0))
    check(
        "T7 空目录 []", ww.watch_cases(os.path.join(TMP, "empty"), task_map=TM_OK) == []
    )

    # T8：真实 CLI 预览（万凯在案、任务 Ready → 健康提示 rc=0）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_watch.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    check("T8 CLI rc=0", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
    check(
        "T8 健康或巡检输出",
        ("全部在案案例健康" in r.stdout) or ("WEAK WATCH" in r.stdout),
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
