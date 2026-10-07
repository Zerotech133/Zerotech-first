# -*- coding: utf-8 -*-
"""§12 超期未结案巡检推送（weak 产线第 12 节 · K35-w 新增）。

§8 盯数据信号（价格/超额触发复评），§12 盯生命周期失速：在案案例
（reports/weak_track_<code6>.jsonl，非 .closed_ 归档）三条检测——

  1. 超龄  ：立案（首行 ts）距今 > --max-days（默认 60 天）→ 按 §8
             第四步结案三选一，或回 §5 重写情景推演；
  2. 停滞  ：末行 ts 距今 > --stall-days（默认 3 天）→ 每日 15:30 任务
             没在产出，查任务日志/数据链；
  3. 失联  ：每日跟踪任务（WeakTrack<code6> / 首例 WeakTrackCheck）
             不存在或已停用 → Enable 重启；若已结案但状态流未归档，
             说明 §9 走到一半（停任务后未改名），补跑结案。

全部健康 → 打印提示 rc=0 不推送（不打扰）；有异常 → --send 推送
琥珀卡（催办警告色）。默认预览。文案外置 weak_track_watch_tpl.txt
（判例沿用，坏模板回退内置）。

用法:
  python weak_track_watch.py                     # 预览
  python weak_track_watch.py --send              # 推送琥珀卡
  python weak_track_watch.py --send --text       # 推送文本
  python weak_track_watch.py --max-days 90       # 口径可调
"""
import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_close as wc          # noqa: E402  (_load/_case_name)

REP = os.path.join(os.path.dirname(HERE), "reports")
TPL = os.path.join(HERE, "weak_track_watch_tpl.txt")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))


def _task_map():
    """全部 WeakTrack* 计划任务 → {任务名: State}（一次查询，测试可 monkeypatch）。"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-ScheduledTask -TaskPath '\\' | Where-Object "
         "{$_.TaskName -like 'WeakTrack*'} | ForEach-Object "
         "{ \"$($_.TaskName)|$($_.State)\" }"],
        capture_output=True, text=True, timeout=60)
    out = {}
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if "|" in ln:
            k, v = ln.split("|", 1)
            out[k.strip()] = v.strip()
    return out


def _d(s):
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def watch_cases(rep=None, task_map=None, max_days=60, stall_days=3):
    """在案状态流 → 巡检结果列表（每案 flags 非空即异常）。"""
    rep = rep or REP
    today = datetime.date.today()
    tm = task_map if task_map is not None else _task_map()
    out = []
    for fp in sorted(glob.glob(os.path.join(rep, "weak_track_*.jsonl"))):
        if ".closed_" in os.path.basename(fp):       # 归档的不在案
            continue
        rows = wc._load(fp)
        if not rows:
            continue
        c6 = str(rows[-1].get("code", "?")).split(".")[0]
        d0, d1 = _d(rows[0].get("ts")), _d(rows[-1].get("ts"))
        days_open = (today - d0).days if d0 else None
        days_stall = (today - d1).days if d1 else None
        # 任务关联：任务名含本 code6 的（WeakTrack<c6>/R2 等）；
        # 301216 特例含首例历史任务名 WeakTrackCheck——不给全案兜底
        keys = [k for k in tm if c6 in k]
        if c6 == "301216":
            keys.append("WeakTrackCheck")
        states = [tm[k] for k in keys if tm.get(k)]
        flags = []
        if days_open is not None and days_open > max_days:
            flags.append("超龄")
        if days_stall is not None and days_stall > stall_days:
            flags.append("停滞")
        if not any(s == "Ready" for s in states):
            flags.append("任务失联")
        out.append({
            "code": rows[-1].get("code", "?"), "c6": c6,
            "name": wc._case_name(c6),
            "days_open": days_open, "days_stall": days_stall,
            "task": ("Ready" if any(s == "Ready" for s in states)
                     else "、".join(states) or "缺失"),
            "flags": flags, "md": os.path.basename(fp),
        })
    return out


_ACT = {
    "超龄": "按 §8 第四步结案三选一，或回 §5 重写情景推演",
    "停滞": "查每日任务日志与数据链（15:30 应有一行）",
    "任务失联": "Enable 重启任务；已结案则补跑 §9（状态流未归档=结案半途）",
}


def watch_fields(cases):
    """巡检结果 → 推送字段 dict（文案与卡片共用单一数据源）。"""
    bad = [c for c in cases if c["flags"]]
    return {
        "n": len(cases), "n_bad": len(bad),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "detail": "\n".join(
            "- %s %s：%s｜跟踪 %s 天·末次 %s 天前·任务 %s\n  → %s" % (
                c["code"], c.get("name") or "", "、".join(c["flags"]),
                c["days_open"] if c["days_open"] is not None else "—",
                c["days_stall"] if c["days_stall"] is not None else "—",
                c["task"], "；".join(_ACT[f] for f in c["flags"]))
            for c in bad),
    }


def build_watch(f):
    """巡检字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(l for l in
                           open(TPL, encoding="utf-8").read().splitlines(True)
                           if not l.lstrip().startswith("#"))
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError):
            pass
    return ("⏰ [WEAK WATCH] 超期未结案巡检（§12）\n"
            "在案 {n} 案｜异常 {n_bad} 案｜{ts}\n"
            "{detail}").format(**f)


def main():
    ap = argparse.ArgumentParser(description="§12 超期未结案巡检推送")
    ap.add_argument("--send", action="store_true",
                    help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true",
                    help="文本版推送（默认飞书卡片）")
    ap.add_argument("--max-days", type=int, default=60,
                    help="跟踪超龄阈值（天，默认 60）")
    ap.add_argument("--stall-days", type=int, default=3,
                    help="状态流停滞阈值（天，默认 3）")
    a = ap.parse_args()
    cases = watch_cases(max_days=a.max_days, stall_days=a.stall_days)
    if not cases:
        print("无在案案例（%s 下无未归档 weak_track_*.jsonl）" % REP)
        return 0
    f = watch_fields(cases)
    if not f["n_bad"]:
        print("全部在案案例健康（%d 案，无超龄/停滞/失联）——"
              "巡检口径：超龄 %d 天 / 停滞 %d 天" % (
                  f["n"], a.max_days, a.stall_days))
        return 0
    msg = build_watch(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard
            print("\n（卡片版预览，--text 可回退文本）")
            print(json.dumps(wcard.watch_card(f),
                             ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard
        print("\n推送(卡片)：" + wcard.send_card(wcard.watch_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox",
                           "pending_alert_weak_watch.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
