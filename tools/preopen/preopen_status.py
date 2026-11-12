# -*- coding: utf-8 -*-
"""盘前四态体检：启动了吗 / 算票了吗 / 简报出了吗 / 收盘复盘了吗。

为什么要单独一个工具
--------------------
2026-11-12 的盘前事故，事后复盘花的时间比修它多得多，原因不是缺日志，
而是**三种不同的失败在证据上长得一样**：

    计划任务没触发        → 没有日志、没有产物
    启动了但脚本秒退      → 没有日志、没有产物   ← 与上一条完全无法区分
    跑了但跳过了选票      → 有日志、有产物、简报照发，外观与「当天新算」一致

所以判据必须落在互不相同的证据上：

    启动   ← C:\\Temp\\preopen_launch.jsonl（preopen_task.ps1 第一行可执行代码写入，
             独立于业务代码；业务死掉也照样有）
    算票   ← 篮子文件 / 选票产物自述的 picker_run_at（不是文件 mtime：
             复用旧文件时根本不写文件，mtime 会假扮成「刚算过」）
    简报   ← 报告 HTML 的存在与生成时刻
    复盘   ← close_review_<exec_day>.json（收盘后那条链路的产物）

前三者齐才算盘前链路今天真的走通了；复盘按 15:40 之后才「该有」，早盘查不报缺。
缺哪一环就报哪一环，不合并成一句「今天没出票」。

用法
----
    python tools/preopen/preopen_status.py                 # 看今天
    python tools/preopen/preopen_status.py --date 20261112 # 看某天（历史复查）
    python tools/preopen/preopen_status.py --json          # 机器可读

退出码
------
    0 三态齐全（复盘已出，或未到 15:40 不要求）
    4 未启动（无落痕，且日志无当日 start 行）
    5 启动了但没算出票（无篮子/无产物）
    6 有票但没出简报
    7 非交易日
    8 盘前走通但收盘复盘缺失（仅当已过 15:40 或查历史日才判）
"""
from __future__ import annotations

import argparse
import glob
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


def _find_pick_by_run_at(exec_day: str):
    """按产物自述的 picker_run_at 找「执行日当天算的那份票」。

    不能只按「执行日的前一交易日」推数据日 —— 那条规则假设数据日总是前一天，
    而 2026-11-12 实测：`check_20261111.json` 是 11-12 才算出来的（数据日 1111）。
    推断在先、产物在后，两者对不上时信产物：自述里带着真实计算时刻。
    """
    want = "%s-%s-%s" % (exec_day[:4], exec_day[4:6], exec_day[6:])
    hits = []
    for p in glob.glob(os.path.join(DELIV, "preopen_pick15_v2_adj_check_*.json")):
        try:
            d = json.load(io.open(p, encoding="utf-8"))
        except (ValueError, OSError):
            continue
        ts = str(d.get("picker_run_at") or "")
        if ts[:10] == want:
            hits.append((ts, os.path.getmtime(p), p))
    return max(hits)[2] if hits else None


def resolve_paths(exec_day: str) -> dict:
    """执行日 → (数据日, 篮子, 选票产物)。

    篮子优先：它直接记着「今天开盘用哪份票」。没有篮子才退回按交易日历推数据日，
    并把这个退化本身标出来 —— 推出来的不等于当时真的用的那份。
    """
    out = {"data_day": None, "basket": None, "pick_json": None, "derived": True,
           "pick_source": "calendar"}
    basket_path = os.path.join(DELIV, "preopen_basket_%s.json" % exec_day)
    if os.path.exists(basket_path):
        out["basket"] = basket_path
        out["derived"] = False
        out["pick_source"] = "basket"
        try:
            b = json.load(io.open(basket_path, encoding="utf-8"))
            out["data_day"] = b.get("data_day")
            out["pick_json"] = b.get("pick_json")
            out["basket_meta"] = b
            return out
        except (ValueError, OSError):
            pass
    # 退一步：按产物自述找当日算的那份（比按日历推更接近事实）
    fp = _find_pick_by_run_at(exec_day)
    if fp:
        out["pick_json"] = fp
        out["pick_source"] = "run_at"
        try:
            out["data_day"] = json.load(io.open(fp, encoding="utf-8")).get("refine_date")
        except (ValueError, OSError):
            pass
        return out
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


def review_state(day: str) -> dict:
    """收盘复盘：与盘前三态不同的证据源（收盘链路自己的产物）。

    早盘查当天时这份必然还没有 —— 所以「缺失」要不要算错，取决于现在几点，
    由 main 里的 due 判断；这里只报事实，不替调用方下结论。
    """
    p = os.path.join(DELIV, "close_review_%s.json" % day)
    st = {"path": p, "exists": os.path.exists(p), "top_o2c": None, "excess_o2c": None,
          "win": None, "n": None, "written_at": None, "quote_source": None,
          "evidence": None, "source_changed": False}
    if not st["exists"]:
        return st
    try:
        d = json.load(io.open(p, encoding="utf-8"))
    except (ValueError, OSError):
        return st
    ev = d.get("evidence") or {}
    st.update(top_o2c=d.get("top_o2c"), excess_o2c=d.get("excess_o2c"),
              win=d.get("win_o2c"), n=d.get("n"), written_at=d.get("written_at"),
              quote_source=d.get("quote_source"), evidence=(ev.get("sha256") or "")[:12],
              source_changed=bool(ev.get("source_changed")))
    return st


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前四态体检：启动 / 算票 / 简报 / 复盘")
    ap.add_argument("--date", default=None, help="执行日 YYYYMMDD，缺省今天")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--assume-review", action="store_true",
                    help="内部用：调用方自己正在产出复盘（close_review 会传）。"
                         "手跑别加 —— 那会把「复盘缺失」误报成 OK")
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
    rev = review_state(day)
    now = datetime.now()
    # 「复盘缺失」要不要算错，取决于现在几点：历史日必须已有；当天 15:40 之前
    # 它本来就不该有，早盘报红只会训练人忽略告警。
    review_due = (day < now.strftime("%Y%m%d")) or (now.hour, now.minute) >= (15, 40)

    # 四态判定：证据分别来自落痕 / 产物自述 / 报告文件 / 复盘产物，不互相顶替
    started = launch["found"]
    picked = pick["exists"] and (pick["n_codes"] or 0) > 0
    reported = report["exists"]
    # close_review 在**写盘之前**调本工具取链路状态，那时复盘文件自然还不存在。
    # 用 --assume-review 把这一步当成已发生，否则每天的复盘产物都会自述 NO_REVIEW。
    reviewed = rev["exists"] or a.assume_review
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
    elif not reviewed and review_due:
        verdict, code = "NO_REVIEW", 8
    else:
        verdict, code = "OK", 0

    if a.json:
        print(json.dumps({"exec_day": day, "verdict": verdict, "launch": launch,
                          "log_marks": logm, "paths": {k: v for k, v in paths.items()
                                                       if k != "basket_meta"},
                          "pick": pick, "report": report,
                          "review": rev, "review_due": review_due,
                          "review_assumed": bool(a.assume_review and not rev["exists"])},
                         ensure_ascii=False, indent=1))
        return code

    print("盘前四态 · 执行日 %s" % day)
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
        how = {"basket": "来自篮子", "run_at": "按产物自述的计算时刻匹配",
               "calendar": "按交易日历反推，非当时实际所用"}.get(paths.get("pick_source"),
                                                                  paths.get("pick_source"))
        print("           数据日 %s（%s）" % (paths.get("data_day") or "-", how))

    # ③ 简报
    if reported:
        print("[3] 简报  ✔ %s 生成于 %s" % (os.path.basename(rep), report["mtime"]))
    else:
        print("[3] 简报  ✘ 无 preopen_report_%s.html" % day)

    # ④ 收盘复盘
    if rev["exists"]:
        s = "组合开→收 %s | 同池超额 %s | 胜 %s/%s" % (
            "-" if rev["top_o2c"] is None else "%+.2f%%" % rev["top_o2c"],
            "-" if rev["excess_o2c"] is None else "%+.2f%%" % rev["excess_o2c"],
            rev["win"], rev["n"])
        print("[4] 复盘  ✔ %s｜%s｜行情 %s · 证据 %s"
              % (os.path.basename(rev["path"]), s,
                 rev["quote_source"] or "-", rev["evidence"] or "-"))
        if rev["source_changed"]:
            print("           ⚠ 该日行情证据与上次留存不一致（同日重跑换了数据源？），"
                  "两次数字不可直接比较")
    elif a.assume_review:
        print("[4] 复盘  ✔ 本份正在生成（调用方传了 --assume-review，"
              "产物落盘后即为存在）")
    elif review_due:
        print("[4] 复盘  ✘ 无 close_review_%s.json —— 盘前走通了，但收盘结果没被记录"
              % day)
        print("           跑一次：python tools/review/close_review.py --date %s" % day)
    else:
        print("[4] 复盘  ⏳ 未到点（收盘复盘 15:40 才有；现在 %s）"
              % now.strftime("%H:%M"))

    print("-" * 60)
    print("结论：%s（退出码 %d）" % (verdict, code))
    return code


if __name__ == "__main__":
    sys.exit(main())
