# -*- coding: utf-8 -*-
"""盘前三态体检：启动了吗 / 算票了吗 / 简报出了吗。

为什么要单独一个工具
--------------------
2026-11-12 的盘前事故，事后复盘花的时间比修它多得多，原因不是缺日志，
而是**三种不同的失败在证据上长得一样**：

    计划任务没触发        → 没有日志、没有产物
    启动了但脚本秒退      → 没有日志、没有产物   ← 与上一条完全无法区分
    跑了但跳过了选票      → 有日志、有产物、简报照发，外观与「当天新算」一致

所以判据必须落在三种**互不相同的证据**上：

    启动   ← C:\\Temp\\preopen_launch.jsonl（preopen_task.ps1 第一行可执行代码写入，
             独立于业务代码；业务死掉也照样有）
    算票   ← 篮子文件 / 选票产物自述的 picker_run_at（不是文件 mtime：
             复用旧文件时根本不写文件，mtime 会假扮成「刚算过」）
    简报   ← 报告 HTML 的存在与生成时刻

三者齐才算这条链路今天真的走通了。缺哪一环就报哪一环，不合并成一句「今天没出票」。

用法
----
    python tools/preopen/preopen_status.py                 # 看今天
    python tools/preopen/preopen_status.py --date 20261112 # 看某天（历史复查）
    python tools/preopen/preopen_status.py --json          # 机器可读

退出码
------
    0 三态齐全
    4 未启动（无落痕，且日志无当日 start 行）
    5 启动了但没算出票（无篮子/无产物）
    6 有票但没出简报
    7 非交易日
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_preopen as audit  # noqa: E402

LAUNCH = r"C:\Temp\preopen_launch.jsonl"
LOG = r"C:\Temp\preopen_task.log"
DELIV = r"C:\项目\deliverables"
RUN_LATEST = os.path.join(DELIV, "preopen_run_latest.json")


def _hms(ts: str | None) -> str:
    return (ts or "")[11:19] or "-"


def read_launch(day: str) -> dict:
    rec = {"found": False, "n_start": 0, "first_started_at": None, "last_started_at": None,
           "ended": None, "dur_sec": None, "pids": [], "skip_pick": [], "preview_n": 0,
           "source": LAUNCH}
    if not os.path.exists(LAUNCH):
        return rec
    with io.open(LAUNCH, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("day") or "") != day:
                continue
            if r.get("kind") == "preview":
                rec["preview_n"] += 1      # -WhatIf 预演不算启动，单列出来防止假阳性
                continue
            rec["found"] = True
            if r.get("kind") == "start":
                rec["n_start"] += 1
                rec["last_started_at"] = r.get("ts")
                if rec["first_started_at"] is None:
                    rec["first_started_at"] = r.get("ts")
                if r.get("pid") not in rec["pids"]:
                    rec["pids"].append(r.get("pid"))
                if r.get("skip_pick") not in rec["skip_pick"]:
                    rec["skip_pick"].append(bool(r.get("skip_pick")))
            elif r.get("kind") == "end":
                rec["ended"] = r.get("ts")
                rec["dur_sec"] = r.get("dur_sec")
    return rec


def read_log_marks(day: str) -> dict:
    """日志旁证：落痕缺失时靠它判断「是不是真的一次都没跑」。

    注意它**不能替代落痕** —— 日志是业务代码写的，业务没起来就没它，
    这正是 P0 要修的那个盲区。这里只作交叉印证，输出里必须标「旁证」。
    """
    out = {"exists": os.path.exists(LOG), "n_start_line": 0, "n_end_line": 0,
           "mtime": None}
    if not out["exists"]:
        return out
    out["mtime"] = datetime.fromtimestamp(os.stat(LOG).st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    want = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    with io.open(LOG, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            if not line.startswith(want):
                continue
            if "=== preopen start" in line:
                out["n_start_line"] += 1
            elif "=== preopen end" in line:
                out["n_end_line"] += 1
    return out


def resolve_paths(exec_day: str) -> dict:
    """执行日 → (数据日, 篮子, 选票产物)。

    篮子优先：它直接记着「今天开盘用哪份票」。没有篮子才退回按交易日历推数据日，
    并把这个退化本身标出来 —— 推出来的不等于当时真的用的那份。
    """
    out = {"data_day": None, "basket": None, "pick_json": None, "derived": True}
    basket_path = os.path.join(DELIV, "preopen_basket_%s.json" % exec_day)
    if os.path.exists(basket_path):
        out["basket"] = basket_path
        out["derived"] = False
        try:
            b = json.load(io.open(basket_path, encoding="utf-8"))
            out["data_day"] = b.get("data_day")
            out["pick_json"] = b.get("pick_json")
            out["basket_meta"] = b
            return out
        except (ValueError, OSError):
            pass
    cal = audit.trade_cal()
    if exec_day in cal:
        i = cal.index(exec_day)
        if i > 0:
            out["data_day"] = cal[i - 1]
    if out["data_day"]:
        p = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json" % out["data_day"])
        out["pick_json"] = p if os.path.exists(p) else None
    return out


def pick_state(p: dict) -> dict:
    fp = p.get("pick_json")
    st = {"exists": bool(fp and os.path.exists(fp)), "path": fp,
          "picker_run_at": None, "picker_run_at_source": "absent",
          "n_codes": None, "reused": None}
    if not st["exists"]:
        return st
    mtime = datetime.fromtimestamp(os.stat(fp).st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    st["mtime"] = mtime
    try:
        d = json.load(io.open(fp, encoding="utf-8"))
    except (ValueError, OSError):
        st["picker_run_at_source"] = "unreadable"
        return st
    run_at = d.get("picker_run_at")
    st["picker_run_at"] = run_at or mtime
    st["picker_run_at_source"] = "artifact" if run_at else "mtime"
    st["n_codes"] = len(d.get("top15_adj") or [])
    st["data_day"] = d.get("refine_date")
    st["chain_policy_v"] = d.get("chain_policy_v")
    bm = (p.get("basket_meta") or {})
    if "reused" in bm:
        st["reused"] = bm.get("reused")
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前三态体检：启动 / 算票 / 简报")
    ap.add_argument("--date", default=None, help="执行日 YYYYMMDD，缺省今天")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    day = a.date or datetime.now().strftime("%Y%m%d")
    cal = audit.trade_cal()
    if day not in cal:
        msg = "%s 不是交易日（交易日历无此日）" % day
        print(msg)
        return 7 if not a.json else 7

    launch = read_launch(day)
    logm = read_log_marks(day)
    paths = resolve_paths(day)
    pick = pick_state(paths)
    rep = os.path.join(DELIV, "preopen_report_%s.html" % day)
    report = {"path": rep, "exists": os.path.exists(rep),
              "mtime": (datetime.fromtimestamp(os.stat(rep).st_mtime)
                        .strftime("%Y-%m-%d %H:%M:%S") if os.path.exists(rep) else None)}

    # 三态判定：证据分别来自落痕 / 产物自述 / 报告文件，不互相顶替
    started = launch["found"]
    picked = pick["exists"] and (pick["n_codes"] or 0) > 0
    reported = report["exists"]
    if not started and logm["n_start_line"]:
        # 日志有 start 行、落痕却没有 → 当天跑的是「还没有落痕的那个版本」。
        # 这与「压根没启动」是两件事，混成一个结论会让人去查错方向。
        verdict, code = "RAN_WITHOUT_BEACON", 4
    elif not started:
        verdict, code = "NOT_STARTED", 4
    elif not picked:
        verdict, code = "STARTED_NO_PICK", 5
    elif not reported:
        verdict, code = "PICK_NO_REPORT", 6
    else:
        verdict, code = "OK", 0

    if a.json:
        print(json.dumps({"exec_day": day, "verdict": verdict, "launch": launch,
                          "log_marks": logm, "paths": {k: v for k, v in paths.items()
                                                       if k != "basket_meta"},
                          "pick": pick, "report": report},
                         ensure_ascii=False, indent=1))
        return code

    print("盘前三态 · 执行日 %s" % day)
    print("-" * 60)
    # ① 启动
    if started:
        s = "启动 %d 次（首 %s / 末 %s）pid=%s" % (
            launch["n_start"], _hms(launch["first_started_at"]),
            _hms(launch["last_started_at"]), ",".join(str(x) for x in launch["pids"]))
        if launch["ended"]:
            s += " | 末次已 end（%ss）" % launch["dur_sec"]
        else:
            s += " | **无 end：有启动没跑完**"
        print("[1] 启动  ✔ " + s)
        if launch["skip_pick"] and any(launch["skip_pick"]):
            print("           其中 %d 次带 SkipPick=True（复用旧票）"
                  % sum(1 for x in launch["skip_pick"] if x))
    else:
        print("[1] 启动  ✘ 落痕无记录")
        if logm["n_start_line"]:
            print("           旁证：日志里有 %d 条当日 start 行 → 当天跑的是"
                  "**还没有落痕的版本**（P0 上线前），不是没启动" % logm["n_start_line"])
            print("           → 结论已按 RAN_WITHOUT_BEACON 处理，别照着「没启动」去查")
        elif logm["exists"]:
            print("           旁证：日志里当日 start 行 0 条（日志 mtime %s）" % logm["mtime"])
            print("           → 任务没被拉起，或在写落痕之前就失败了")
        else:
            print("           旁证：日志文件不存在 → 任务从未在这台机器上跑过")

    # ② 算票
    if picked:
        how = "复用" if pick.get("reused") is True else (
            "重算" if pick.get("reused") is False else "来源未记")
        print("[2] 算票  ✔ %d 只 | 算于 %s（%s）| %s"
              % (pick["n_codes"], pick["picker_run_at"], pick["picker_run_at_source"], how))
        if pick["picker_run_at_source"] == "mtime":
            print("           ⚠ 产物未自述 picker_run_at，这里用的是文件时间 —— "
                  "复用旧文件时它不更新，可能比实际计算晚")
    else:
        print("[2] 算票  ✘ 无可用选票产物")
        print("           数据日 %s（%s）"
              % (paths.get("data_day") or "-",
                 "来自篮子" if not paths["derived"] else "按交易日历反推，非当时实际所用"))

    # ③ 简报
    if reported:
        print("[3] 简报  ✔ %s 生成于 %s" % (os.path.basename(rep), report["mtime"]))
    else:
        print("[3] 简报  ✘ 无 preopen_report_%s.html" % day)

    print("-" * 60)
    print("结论：%s（退出码 %d）" % (verdict, code))
    return code


if __name__ == "__main__":
    sys.exit(main())
