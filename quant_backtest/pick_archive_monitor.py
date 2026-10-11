# -*- coding: utf-8 -*-
"""T1 清单实盘归档监控（P5 系列配套，2026-10-11）。

监控 portal/data/picks_history 每日归档，为形态 B 复评（报告
docs/plans/2026-10-11_P5c形态B_离线回测报告.md §6）盯四件事：
  ① 新鲜度——15:10 任务是否按时产出（最新档 vs 最近收盘交易日）
  ② 断档——首档以来交易日序列缺档
  ③ 层样本进度——五影子层累计 n vs 门槛 10（q_first_buy/q_toxic/
     env_diff_exempt/panic_buy/env_diff；附 T1 内计数）
  ④ 满窗进度——各档 T+5/T+10 收益窗口是否可算（exec=D+2 未到则
     收益统计不可用，防幸存者偏差，同 t1_channel_verdicts 加固口径）

职责切分：本脚本只读归档+交易日历（秒级），不算收益——满窗后收益
对照跑 pick_layer_verdicts.py。只读工具，用法：
python pick_archive_monitor.py [--min] [--push]
  --min  精简模式（只出告警行）
  --push 告警时推飞书（复用 alert_fail.alert 判例：编码三态/掉电
        归类/outbox；健康静默不推；推送失败不反噬退出码）
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HIST = os.path.join(HERE, "..", "portal", "data", "picks_history")
GATE_N = 10
LAYERS = ("q_first_buy", "q_toxic", "env_diff_exempt", "panic_buy",
          "env_diff")
COUNT_KEYS = ("total", "T1_resonance", "q_first_buy_shadow",
              "q_toxic_shadow", "env_diff_exempt", "env_diff_shadow",
              "panic_buy_shadow")


def d8(s):
    return str(s)[:10].replace("-", "")


def load_archives():
    out = []
    for fp in sorted(glob.glob(os.path.join(HIST, "todays_*.json"))):
        try:
            doc = json.load(open(fp, encoding="utf-8"))
        except Exception as ex:
            out.append({"day": d8(os.path.basename(fp)[7:17]),
                        "bad": str(ex), "stocks": [], "counts": {}})
            continue
        out.append({"day": d8((doc.get("trade_basis") or "")[:10]
                              or os.path.basename(fp)[7:17]),
                    "doc": doc, "stocks": doc.get("stocks") or [],
                    "counts": doc.get("counts") or {}})
    return [a for a in out if a["day"].isdigit()]


def bar(n, total=GATE_N, width=10):
    full = min(n * width // total, width) if total else width
    return "█" * full + "░" * (width - full)


def _push_alerts(alerts, latest, today):
    """告警推飞书（--push）：复用 alert_fail.alert 判例（编码三态/
    掉电归类/outbox→send_feishu）；发送失败仅打印，不反噬监控退出码。
    健康静默——无告警不推，避免每日噪音。"""
    try:
        from alert_fail import alert
        lines = ["T1 实盘归档监控告警（最近收盘交易日 %s / 最新档 %s）"
                 % (today, latest)]
        lines += ["[ALERT] " + a for a in alerts]
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False,
                encoding="utf-8") as f:
            f.write("\n".join(lines))
            tmp = f.name
        try:
            alert("PickArchiveMonitor", 2, tmp)
        finally:
            os.unlink(tmp)
    except Exception as e:
        print("[push] 告警推送失败（不影响监控）：%s" % str(e)[:80])


def main():
    quiet = "--min" in sys.argv
    import strong_ts_window_research as R
    cal = R.trade_calendar()
    today = max(cal)
    archs = load_archives()
    alerts = []
    if not archs:
        print("[FATAL] picks_history 无归档可读")
        return 1

    # ① 新鲜度：最新档 vs 最近收盘交易日（>1 交易日缺档即告警）
    latest = archs[-1]["day"]
    last2 = cal[-2] if len(cal) >= 2 else today
    fresh_ok = latest >= last2
    if not fresh_ok:
        alerts.append("缺档：最新档 %s，最近收盘交易日 %s——检查 15:10 "
                      "计划任务 portal/build_todays_picks.py"
                      % (latest, today))
    # ② 断档：首档以来交易日缺档（任务上线日之后的）
    start_i = cal.index(archs[0]["day"])
    expect = set(d for d in cal[start_i:] if d <= today)
    missing = sorted(expect - {a["day"] for a in archs})
    if missing:
        alerts.append("断档 %d 日：%s%s"
                      % (len(missing), ",".join(missing[:6]),
                         "..." if len(missing) > 6 else ""))

    # ③ 层样本进度（累计票数 / 其中 T1）
    lay_n = {L: [0, 0] for L in LAYERS}
    # ④ 满窗进度
    win_rows = []
    for a in archs:
        D = a["day"]
        i = cal.index(D) if D in cal else -1
        exec_d = cal[i + 2] if 0 <= i and i + 2 < len(cal) else None
        t5 = R.after_days(cal, exec_d, 5) if exec_d else None
        t10 = R.after_days(cal, exec_d, 10) if exec_d else None
        win_rows.append((D, exec_d, bool(t5), bool(t10)))
        for st in a["stocks"]:
            for L in LAYERS:
                if st.get(L):
                    lay_n[L][0] += 1
                    if st.get("tier") == "T1":
                        lay_n[L][1] += 1

    # 完整性：counts 新字段（旧档 2026-10-09 首档无 q_* 字段，提示即可）
    last_counts = archs[-1]["counts"]
    miss_keys = [k for k in COUNT_KEYS if k not in last_counts]
    if miss_keys:
        alerts.append("最新档 counts 缺字段：%s（旧版产出？确认 15:10 任务"
                      "跑的是当前 build_todays_picks.py）" % ",".join(miss_keys))
    bad = [a for a in archs if a.get("bad")]
    if bad:
        alerts.append("损坏档 %d 份：%s"
                      % (len(bad), ",".join(a["day"] for a in bad)))

    if quiet:
        for a in alerts:
            print("[ALERT]", a)
        if alerts and "--push" in sys.argv:
            _push_alerts(alerts, latest, today)
        return 0 if not alerts else 2

    print("=== T1 实盘归档监控（最近收盘交易日 %s）===" % today)
    print("① 新鲜度：最新档 %s %s｜档数 %d｜总票 %d"
          % (latest, "✓" if fresh_ok else "✗（见告警）", len(archs),
             sum(len(a["stocks"]) for a in archs)))
    print("② 断档：%s" % ("无 ✓" if not missing
                          else "%d 日（见告警）" % len(missing)))
    print("③ 影子层样本进度（累计票/其中T1，门槛 n≥%d）：" % GATE_N)
    for L in LAYERS:
        n, n1 = lay_n[L]
        print("  %-16s %3d/%-3d %s %s" % (L, n, n1, bar(n),
              "达标" if n >= GATE_N else "积累中"))
    print("④ 满窗进度（档期倒序，最多列 5）：")
    for D, ex, w5, w10 in win_rows[-5:][::-1]:
        print("  %s 档：exec=%s｜T+5 %s｜T+10 %s"
              % (D, ex or "未到(D+2)",
                 "满✓" if w5 else "未满", "满✓" if w10 else "未满"))
    ready5 = [r for r in win_rows if r[2]]
    ready10 = [r for r in win_rows if r[3]]
    print("  可算收益档：T+5 %d 份 / T+10 %d 份"
          % (len(ready5), len(ready10)))
    if alerts:
        print("\n--- 告警 ---")
        for a in alerts:
            print("[ALERT]", a)
        if "--push" in sys.argv:
            _push_alerts(alerts, latest, today)
    else:
        print("\n健康 ✓ 无告警")
    print("下一步：满窗档 ≥1 后跑 python quant_backtest/"
          "pick_layer_verdicts.py 看分层收益对照")
    return 0 if not alerts else 2


if __name__ == "__main__":
    raise SystemExit(main())
