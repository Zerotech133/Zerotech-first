# -*- coding: utf-8 -*-
"""futquant_notify: FutQuant 晨报哨兵（K197'，一次性提醒任务）。

触发: 计划任务 FutQuantNotify（一次性次日 09:35）→ 本脚本轮询等待
FutQuant 四步流水线（fut_scan → fut_backtest → fut_walkforward →
cn_fut_backtest）收尾标志 = C:\\Temp\\fut_daily_task.log 最后一行
「cn_fut exit=...」且行内时间戳 ≥ 当日 09:30。

完成后汇总:
  · 四步 exit 码（非 0 标红提示）
  · 产物在位: fut_scan/fut_strat/fut_walkforward/cn_fut_backtest 当日 json
  · F3 矩阵读数: n 品种 / 错误数 / 跑赢 B&H 数 / 最佳品种策略
  · F3 全错时显式标红（避免“四步全绿”掩盖矩阵不可用）
经 news_collector/send_feishu.py 推飞书（outbox 临时文件+子进程，
SEND_OK 口径，与 chanlun_scan._push_feishu_text 同通道）。

幂等: 状态文件 data/futquant_notify_state.json（当日已处理即退）；
SEND_OK 才写状态。超时（60 分钟未见收尾行——挂死场景 alert_fail 不会
触发，本哨兵补盲）也推一条未完成告警。

失败告警分工: rc≠0 → start_fut_daily.ps1 内 alert_fail 即时告警；
挂死/未跑 → 本哨兵超时路径。两者互补不重复。
"""

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "futquant_notify_state.json")
TASK_LOG = r"C:\Temp\fut_daily_task.log"
OUT_DIR = os.path.join(os.path.dirname(HERE), "tools", "out")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

POLL_SEC = 60  # 轮询间隔
POLL_MAX = 60  # 最多 60 分钟
CUTOFF = "09:30"  # 收尾行时间下限（计划任务启动时刻）
# 实际产物名：fut_backtest.py 写 fut_strat_*.json；cn_fut 步骤写
# cn_fut_backtest_*.json。不要再检查不存在的 fut_backtest_*.json。
ARTIFACT_PREFIXES = ("fut_scan", "fut_strat", "fut_walkforward", "cn_fut_backtest")
LINE_RE = re.compile(
    r"fut_scan exit=(\d+) fut_backtest exit=(\d+) "
    r"fut_walkforward exit=(\d+) cn_fut exit=(\d+) (\d{4}-\d{2}-\d{2}T"
    r"\d{2}:\d{2}:\d{2})"
)


def _now():
    return datetime.now()


def _today():
    return _now().strftime("%Y-%m-%d")


def _write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _done():
    """收尾标志: task.log 最后一行 cn_fut exit 且时间戳 ≥ 今日 09:30。"""
    try:
        with open(TASK_LOG, encoding="utf-8", errors="replace") as f:
            lines = [x for x in f.read().splitlines() if "cn_fut exit=" in x]
        if not lines:
            return None
        m = LINE_RE.search(lines[-1])
        if not m:
            return None
        ts = m.group(5)
        if ts[:10] == _today() and ts[11:16] >= CUTOFF:
            return tuple(int(m.group(i)) for i in (1, 2, 3, 4)) + (ts,)
        return None
    except OSError:
        return None


def _today_artifacts():
    """当日产物清单 [(名, 存在)]。"""
    out = []
    for pre in ARTIFACT_PREFIXES:
        hits = sorted(glob.glob(os.path.join(OUT_DIR, pre + "_*.json")))
        hit = hits[-1] if hits else None
        ok = (
            bool(hit)
            and os.path.getmtime(hit)
            >= _now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        out.append((pre, ok))
    return out


def _matrix_readout():
    """F3 矩阵读数: n_ok/n_err/跑赢 B&H 数/最佳品种。"""
    hits = sorted(glob.glob(os.path.join(OUT_DIR, "fut_strat_*.json")))
    if not hits:
        return None
    try:
        with open(hits[-1], encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        return None
    rows = j.get("results") or []
    ok = [r for r in rows if not r.get("error")]
    erred = [r for r in rows if r.get("error")]
    sample_error = str((erred[0].get("error") if erred else "") or "")[:140]
    beat, best = 0, None
    for r in ok:
        rk = [
            x for x in (r.get("ranking") or []) if x.get("total_return_pct") is not None
        ]
        if not rk:
            continue
        top = max(rk, key=lambda x: x["total_return_pct"])
        bh = r.get("buy_and_hold_pct")
        if bh is not None and top["total_return_pct"] > bh:
            beat += 1
        if best is None or (bh is not None and top["total_return_pct"] - bh > best[1]):
            wf = (r.get("wf") or {}).get("robustness_score")
            tag = "（WF%.2f）" % wf if wf is not None else ""
            best = (
                "%s %s%s" % (r.get("symbol"), top["strategy"], tag),
                top["total_return_pct"] - (bh or 0),
            )
    return {
        "n": len(rows),
        "n_ok": len(ok),
        "beat": beat,
        "best": best,
        "all_error": bool(rows) and not ok,
        "sample_error": sample_error,
    }


def _summary(exits, ts):
    rc = exits[:4]
    arts = _today_artifacts()
    m = _matrix_readout()
    bad = any(v != 0 for v in rc) or any(not ok for _, ok in arts)
    bad = bad or (m is None) or bool(m and m.get("all_error"))
    lines = [
        "🛰 FutQuant 晨报哨兵 · %s（收尾 %s）%s"
        % (_today(), ts[11:16], " ⚠️" if bad else ""),
        "四步 exit: scan=%d backtest=%d walkforward=%d cn_fut=%d %s"
        % (
            rc[0],
            rc[1],
            rc[2],
            rc[3],
            "✅ 全绿"
            if all(v == 0 for v in rc)
            else "⚠️ 有非 0——详 C:\\Temp\\fut_daily_task.log 与 alert_fail 告警",
        ),
    ]
    lines += ["产物 %s: %s" % (name, "✅" if ok else "❌ 缺") for name, ok in arts]
    if m is None:
        lines.append("F3 矩阵读数不可得（fut_strat json 缺/坏）")
    elif m.get("all_error"):
        lines.append(
            "F3 矩阵: ❌ %d/%d 全部失败 · %s"
            % (m["n"], m["n"], (m.get("sample_error") or "无错误详情")[:120])
        )
    elif m.get("best"):
        lines.append(
            "F3 矩阵: %d 品种（错 %d）· 跑赢 B&H %d · 最佳 %s 超额 %+.1f%%"
            % (m["n"], m["n"] - m["n_ok"], m["beat"], m["best"][0], m["best"][1])
        )
    else:
        lines.append("F3 矩阵: %d 品种（错 %d）" % (m["n"], m["n"] - m["n_ok"]))
    lines.append("明早无人值守首轮=本哨兵首个正式班次")
    return "\n".join(lines)


def _push(text):
    os.makedirs(os.path.join(NEWS_DIR, "outbox"), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.join(NEWS_DIR, "outbox"), suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        ok = "SEND_OK" in (r.stdout or "")
        print((r.stdout or r.stderr or "").strip()[:100])
        return ok
    except Exception as e:
        print("notify push error: %s" % str(e)[:80])
        return False


def main():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == _today():
            print("already handled today: %s" % st.get("status"))
            return
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/futquant_notify.py", exc)

    exits = None
    for _ in range(POLL_MAX):
        exits = _done()
        if exits:
            break
        time.sleep(POLL_SEC)

    if exits:
        ok = _push(_summary(exits, exits[4]))
        status = "pushed" if ok else "push_failed"
        if not ok:
            sys.exit(4)
    else:
        _push(
            "⚠️ FutQuant 哨兵 · %s\n%d 分钟未见 %s 后收尾行"
            "（fut_daily_task.log 无今日时间戳行）——任务可能挂死/未跑，"
            "请查任务 FutQuant 与 C:\\Temp\\fut_daily_task.log。"
            % (_today(), POLL_MAX, CUTOFF)
        )
        status = "timeout"
    _write_json(
        STATE,
        {
            "date": _today(),
            "status": status,
            "ts": _now().isoformat(timespec="seconds"),
        },
    )
    print("state=%s" % status)
    sys.exit(0 if status != "timeout" else 3)


if __name__ == "__main__":
    main()
