#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W5 影子积累期 · 影子对账周报生成器（20261014 shadow-report-dev，只读）。

===========================================================================
挂载方式（注记式，二选一，本脚本不注册任何计划任务、不推飞书）：
  a. 手动 CLI：
     "C:/Program Files/Python311/python.exe" shadow_weekly_report.py [--week 2026-10-12] [--json-only]
  b. 计划任务挂载建议（只提建议，不改计划任务）：
     产线现无"周五盘后"汇总入口，最接近的周级任务是 WeakWeeklyReport
     （deploy_weak_weekly.ps1 注册，UTC 周一 07:50 = 北京周一 15:50，调
     weak_track_weekly.py --send）。如需自动出影子周报，建议在
     weak_track_weekly.py 主流程末尾以子进程追加（注记式、可随时删除）：
       subprocess.run([r"C:/Program Files/Python311/python.exe",
                       r"E:/老田的AI助手/quant_backtest/shadow_weekly_report.py",
                       "--json-only"], cwd=r"E:/老田的AI助手/quant_backtest")
     本脚本只读外部数据、只写自己名下两个输出文件，重复执行幂等、零副作用。
===========================================================================

五条观察线数据源（全部只读）：
  L1 双刀注记   portal/qd_scan_cache.json（qd_scanner 每日 14:40 覆写）
                + reports/qd_breadth_series.json（全市场宽度序列）
                + 约定历史存档目录 reports/qd_scan_daily/（暂不存在）
  L2 chanlun P5 reports/chanlun_beichi_grid.json（W3-4 网格结果，含前向收益）
                + _v7_stream_cache.json buy_ev（本周新信号计数）
  L3 linkage    data/linkage_shadow.jsonl（collector 落档，观察池 25 只）
  L4 jev        ../jev-eval/logs/review.log（覆盖率行）+ review_data/triples.jsonl
  L5 三档对照   data/gate_shadow_3tier.jsonl（gate_shadow_collect.py 产物，尚不存在→待首采）

口径：
  · 前向收益引用 chanlun_beichi_grid.json（T+1 开盘买→持有 H∈{5,10,20} 收盘卖，
    后复权 hfq、CN 双边 15bps，与 qd_backtest_1445 同判例）；本脚本不重算回测。
  · 缺数据 fail-open：标"暂无数据"，不炸报告。
  · 输出：reports/shadow_weekly_report.md（人读）+ reports/shadow_weekly_report_data.json（机器读）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta

BASE = r"E:/老田的AI助手"
QT = os.path.join(BASE, "quant_backtest")
PORTAL = os.path.join(BASE, "portal")
JEV = os.path.join(BASE, "jev-eval")

P_QD_CACHE = os.path.join(PORTAL, "qd_scan_cache.json")
P_QD_CFG = os.path.join(PORTAL, "qd_scanner_config.json")
P_QD_BREADTH = os.path.join(QT, "reports", "qd_breadth_series.json")
P_QD_DAILY_DIR = os.path.join(QT, "reports", "qd_scan_daily")  # 约定目录，暂无
P_GRID = os.path.join(QT, "reports", "chanlun_beichi_grid.json")
P_STREAM = os.path.join(QT, "_v7_stream_cache.json")
P_LINKAGE = os.path.join(QT, "data", "linkage_shadow.jsonl")
P_GATE3 = os.path.join(QT, "data", "gate_shadow_3tier.jsonl")
P_JEV_LOG = os.path.join(JEV, "logs", "review.log")
P_JEV_TRIPLES = os.path.join(JEV, "review_data", "triples.jsonl")

OUT_MD = os.path.join(QT, "reports", "shadow_weekly_report.md")
OUT_JSON = os.path.join(QT, "reports", "shadow_weekly_report_data.json")

LINKAGE_POOL_N = 25          # 观察池约定规模（K35 三线联动）
JEV_GRAD_N = 300             # jev 毕业样本门槛
JEV_GRAD_MEAN = 0.01         # jev 毕业：FOCUS 前向均值 > +1pp
REVIEW_BUCKET_N = 30         # 双刀单 bucket 样本满 30 → 提醒门控假说中期检验

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------- 工具 ----------
def week_range(week: str | None):
    """--week 接受 YYYY-MM-DD（取所在周周一）；空=本周。返回 (周一, 今天)。"""
    today = date.today()
    if week:
        d = datetime.strptime(week, "%Y-%m-%d").date()
    else:
        d = today
    monday = d - timedelta(days=d.weekday())
    return monday, min(monday + timedelta(days=6), today)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return rows


def bucket_of(pct):
    """与 qd_scanner.breadth_bucket 同口径：<40 / 40-50 / >=50（θ50 门控观察）。"""
    if pct is None:
        return ""
    if pct < 40:
        return "<40"
    if pct < 50:
        return "40-50"
    return ">=50"


def gate_would_block(pct):
    """θ50 体系 40% 档门控假设：宽度<40 → 本单会被拦（只标注不拦截）。"""
    return pct is not None and pct < 40


def in_week(s, monday, today):
    """宽松判断时间戳/日期串是否落在周内（支持 YYYYMMDD / YYYY-MM-DD / ISO ts）。"""
    if not s:
        return False
    s = str(s)
    for pat in ("%Y-%m-%d", "%Y%m%d"):
        try:
            d = datetime.strptime(s[:10].replace("/", "-")[:10], pat).date()
            return monday <= d <= today
        except Exception:
            continue
    return False


def mt_date(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


# ---------- L1 双刀注记 ----------
def line_knife(monday, today):
    r = {"line": "双刀注记观察", "status": "green", "notes": [], "data": {}}
    cache = read_json(P_QD_CACHE)
    cfg = read_json(P_QD_CFG)

    # 注记开关状态
    note_on = bool(isinstance(cfg, dict) and cfg.get("qd_breadth_note"))
    r["data"]["note_switch_on"] = note_on
    if not note_on:
        r["data"]["note_switch_on"] = False

    sigs = []
    note = None
    if isinstance(cache, dict):
        note = cache.get("breadth_note")
        for key in ("signals_now", "signals_recent", "tail_signals"):
            sigs.extend([s for s in cache.get(key) or [] if isinstance(s, dict)])
        r["data"]["scan_ts"] = cache.get("ts")
        r["data"]["scan_in_week"] = in_week(cache.get("ts", ""), monday, today)
    r["data"]["signals_total"] = len(sigs)
    annotated = [s for s in sigs if "breadth" in s]
    r["data"]["signals_annotated"] = len(annotated)

    # 本周信号与 bucket 分布（注记字段优先，退回 cache 级 breadth_note）
    buckets = Counter(s.get("breadth_bucket") for s in annotated if s.get("breadth_bucket"))
    gate_hits = sum(1 for s in annotated if s.get("gate_would_block"))
    if not annotated and isinstance(note, dict):
        b = note.get("bucket")
        if b:
            buckets[b] += len(sigs)
        if note.get("breadth") is not None and gate_would_block(note["breadth"]):
            gate_hits = len(sigs)
        r["data"]["note_cache"] = {k: note.get(k) for k in ("breadth", "bucket", "breadth_ref")}
    r["data"]["bucket_dist"] = dict(buckets)
    r["data"]["gate_would_block_hits"] = gate_hits

    # 宽度序列本周取值（bucket 归属依据）
    bs = read_json(P_QD_BREADTH)
    weekly_breadth = []
    if isinstance(bs, dict) and bs.get("dates"):
        for d, v in zip(bs["dates"], bs.get("breadth") or []):
            if in_week(d, monday, today):
                weekly_breadth.append({"date": d, "breadth": round(v * 100, 1),
                                       "bucket": bucket_of(v * 100)})
        r["data"]["breadth_series_week"] = weekly_breadth
        r["data"]["breadth_series_ts"] = bs.get("ts")
        r["data"]["breadth_series_last"] = (weekly_breadth or [None])[-1]

    # 历史 bucket×前向收益回算：需每日产物存档（约定 reports/qd_scan_daily/）
    if os.path.isdir(P_QD_DAILY_DIR):
        r["data"]["history_backcalc"] = "存档目录存在（回算逻辑后续版本接入）"
    else:
        r["data"]["history_backcalc"] = ("暂无历史留存：qd_scanner 产物为单文件覆写"
                                        "（portal/qd_scan_cache.json），bucket×前向收益"
                                        "（T+5/10/20，湖后复权、CN 15bps，复用 qd_backtest_1445 "
                                        "口径）待注记产物逐日落档后回算")
    r["data"]["review_trigger"] = ("bucket 样本满 %d 个即提醒 θ50 门控假说中期检验"
                                   % REVIEW_BUCKET_N)

    # 判定
    if cache is None:
        r["status"] = "red"
        r["notes"].append("qd_scan_cache.json 缺失")
    elif not note_on:
        r["status"] = "yellow"
        r["notes"].append("qd_breadth_note 开关未开，注记未生效")
    elif not annotated and not isinstance(note, dict):
        r["status"] = "yellow"
        r["notes"].append("开关已开但最新扫描产物无 breadth 注记（待 14:40 下次扫描落档）")
    elif any(b == ">=50" and n >= REVIEW_BUCKET_N for b, n in buckets.items()):
        r["notes"].append("触发评审提醒：bucket 样本满 %d，建议做门控假说中期检验" % REVIEW_BUCKET_N)
    else:
        r["notes"].append("注记链路正常，样本积累中")
    return r


# ---------- L2 chanlun P5 反向观察 ----------
def line_chanlun(monday, today):
    r = {"line": "chanlun P5 反向观察", "status": "green", "notes": [], "data": {}}
    grid = read_json(P_GRID)
    if not isinstance(grid, dict):
        r["status"] = "red"
        r["notes"].append("chanlun_beichi_grid.json 缺失")
        return r

    meta = grid.get("meta") or {}
    res = ((grid.get("results") or {}).get("p5") or {}).get("ratio1.0") or {}
    fwd = {}
    for h in ("H5", "H10", "H20"):
        e = res.get(h) or {}
        fwd[h] = {"diff_pp": e.get("diff_pp"),
                  "n_beichi": e.get("n_beichi"), "n_none": e.get("n_none"),
                  "mean_beichi_pct": e.get("mean_beichi_pct"),
                  "mean_none_pct": e.get("mean_none_pct"),
                  "verdict": e.get("verdict")}
    r["data"]["grid_d_end"] = meta.get("D_END")
    r["data"]["grid_mtime"] = mt_date(P_GRID)
    r["data"]["p5_ratio1.0"] = fwd
    r["data"]["p5_3buy_sample"] = meta.get("sample", {}).get("p5_3buy")

    # 缓存过期标注：D_END 早于本周一 → 网格缓存未覆盖本周
    stale = True
    try:
        stale = datetime.strptime(str(meta.get("D_END")), "%Y%m%d").date() < monday
    except Exception:
        pass
    r["data"]["grid_cache_stale_for_week"] = stale
    if stale:
        r["notes"].append("网格缓存截至 %s，未覆盖本周（前向收益差为缓存口径，过期需重跑 grid）"
                          % meta.get("D_END"))

    # 本周新 3 买（stream cache 事件流，bei 标注计数）；前向收益未到期不计
    ev_in_week = {"3buy_total": 0, "3buy_bei": 0, "3buy_nobei": 0}
    sc = read_json(P_STREAM)
    if isinstance(sc, dict):
        for k, evs in (sc.get("buy_ev") or {}).items():
            if not in_week(k, monday, today):
                continue
            for (_code, tp, bei) in evs or []:
                if tp == "3买":
                    ev_in_week["3buy_total"] += 1
                    ev_in_week["3buy_bei" if bei else "3buy_nobei"] += 1
    r["data"]["week_new_3buy"] = ev_in_week
    if ev_in_week["3buy_total"] == 0:
        r["notes"].append("本周无新 3 买信号（本周增量前向收益未到期，不计算）")

    # 反向观察跟踪：任一 hold 的 diff<=-1pp（标注组更差）视为反向观察仍在位
    diffs = {h: v.get("diff_pp") for h, v in fwd.items()
             if isinstance(v.get("diff_pp"), (int, float))}
    neg = {h: d for h, d in diffs.items() if d <= -1.0}
    if neg:
        r["status"] = "yellow"
        r["notes"].append("反向观察项仍在位：%s 标注组前向收益更差（%s pp）——周报固定字段持续跟踪"
                          % ("/".join(neg), ", ".join("%s=%.2f" % (h, v) for h, v in neg.items())))
    else:
        r["notes"].append("反向观察信号减弱（无 hold 的 diff<=-1pp），维持仅标注")
    return r


# ---------- L3 linkage 影子 ----------
def line_linkage(monday, today):
    r = {"line": "linkage 影子", "status": "green", "notes": [], "data": {}}
    rows = read_jsonl(P_LINKAGE)
    if not rows:
        r["status"] = "red"
        r["notes"].append("linkage_shadow.jsonl 缺失或为空")
        return r

    wk = [x for x in rows if in_week(x.get("ts") or x.get("ref_date"), monday, today)]
    sig_all = Counter(x.get("signal") for x in rows)
    sig_wk = Counter(x.get("signal") for x in wk)
    codes_wk = {x.get("code") for x in wk}
    r["data"]["rows_total"] = len(rows)
    r["data"]["rows_week"] = len(wk)
    r["data"]["signal_dist_total"] = dict(sig_all)
    r["data"]["signal_dist_week"] = dict(sig_wk)
    r["data"]["pool_cover_week"] = "%d/%d" % (len(codes_wk), LINKAGE_POOL_N)
    r["data"]["signal2_total"] = sig_all.get("2", 0)   # 信号②滞涨累计样本
    r["data"]["signal2_week"] = sig_wk.get("2", 0)

    if len(wk) == 0:
        r["status"] = "yellow"
        r["notes"].append("本周无增量行（collector 未落档？）")
    elif sig_all.get("2", 0) < 30:
        r["status"] = "yellow"
        r["notes"].append("采集正常，但信号②滞涨累计样本 %d 条（<30，继续攒）" % sig_all.get("2", 0))
    else:
        r["notes"].append("采集正常，信号②样本已满 30")
    return r


# ---------- L4 jev 毕业冲刺 ----------
def line_jev(monday, today):
    r = {"line": "jev 毕业冲刺", "status": "green", "notes": [], "data": {}}

    # 覆盖率：只读 review.log 最新一条回填覆盖率行，不跑 backfill
    cov = None
    try:
        with open(P_JEV_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r"(\d{4}-\d{2}-\d{2})T.*回填覆盖率 (\d+)/(\d+) = ([\d.]+)%", line)
                if m:
                    cov = {"ts": m.group(1), "filled": int(m.group(2)),
                           "total": int(m.group(3)), "pct": float(m.group(4))}
    except Exception:
        pass
    r["data"]["coverage_latest"] = cov
    if cov is None:
        r["data"]["coverage_latest"] = "暂无数据"

    # FOCUS 前向收益（triples.jsonl outcome 已回填部分）
    n_focus = n_out = n_gt1 = 0
    rets = []
    n_focus_wk = 0
    for d in read_jsonl(P_JEV_TRIPLES):
        if d.get("decision") != "FOCUS":
            continue
        n_focus += 1
        if in_week(d.get("ts"), monday, today):
            n_focus_wk += 1
        try:
            if d.get("outcome") and d.get("ret_nd") is not None:
                v = float(d["ret_nd"])
                n_out += 1
                rets.append(v)
                if v > JEV_GRAD_MEAN:
                    n_gt1 += 1
        except Exception:
            pass
    mean_ret = sum(rets) / len(rets) if rets else None
    r["data"]["focus_total"] = n_focus
    r["data"]["focus_week"] = n_focus_wk
    r["data"]["focus_with_outcome"] = n_out
    r["data"]["focus_ret_gt1pp"] = n_gt1
    r["data"]["focus_mean_ret"] = round(mean_ret, 4) if mean_ret is not None else None
    r["data"]["grad_rule"] = ("毕业=FOCUS 前向均值>+1pp 且 n≥%d（n 走 hourly backfill 积累，"
                              "本脚本只读不跑 backfill）" % JEV_GRAD_N)

    if cov and cov.get("pct", 0) < 50:
        r["status"] = "red"
        r["notes"].append("回填覆盖率 %.1f%% 低于 50%% 阈值" % cov["pct"])
    elif n_out >= JEV_GRAD_N and mean_ret is not None and mean_ret > JEV_GRAD_MEAN:
        r["notes"].append("毕业条件达成：n=%d，均值 %.2fpp" % (n_out, mean_ret * 100))
    elif n_out >= JEV_GRAD_N:
        r["status"] = "yellow"
        r["notes"].append("n=%d 已达标，但 FOCUS 前向均值 %.2fpp 未过 +1pp，冲刺中"
                          % (n_out, (mean_ret or 0) * 100))
    else:
        r["status"] = "yellow"
        r["notes"].append("样本 n=%d/%d，继续积累（当前均值 %s）"
                          % (n_out, JEV_GRAD_N,
                             "%.2fpp" % (mean_ret * 100) if mean_ret is not None else "暂无"))
    return r


# ---------- L5 闸门三档对照 ----------
def line_gate3(monday, today):
    r = {"line": "闸门三档对照", "status": "green", "notes": [], "data": {}}
    if not os.path.exists(P_GATE3):
        r["status"] = "gray"
        r["notes"].append("data/gate_shadow_3tier.jsonl 不存在（gate_shadow_collect.py "
                          "另一代理并行在建）→ 待首采，不报错")
        r["data"]["pending_first_collect"] = True
        return r
    rows = read_jsonl(P_GATE3)
    wk = [x for x in rows if in_week(x.get("ts") or x.get("date"), monday, today)]
    per = Counter()
    for x in wk:
        per["%s|%s" % (x.get("strategy") or x.get("gate") or "?",
                       x.get("tier") or x.get("bucket") or "?")] += 1
    r["data"]["rows_total"] = len(rows)
    r["data"]["rows_week"] = len(wk)
    r["data"]["strategy_tier_week"] = dict(per)
    if not wk:
        r["status"] = "yellow"
        r["notes"].append("文件在但本周无记录")
    else:
        r["notes"].append("首采已发生，本周 %d 条" % len(wk))
    return r


# ---------- 渲染 ----------
def status_mark(s):
    return {"green": "🟢 绿灯", "yellow": "🟡 黄灯", "red": "🔴 红灯",
            "gray": "⚪ 待首采"}.get(s, s)


def render_md(week_label, monday, today, lines, issues, risks):
    L = []
    L.append("# 影子对账周报（W5 影子积累期）")
    L.append("")
    L.append("- 周区间：%s（%s ~ %s）" % (week_label, monday.isoformat(), today.isoformat()))
    L.append("- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("- 生成器：quant_backtest/shadow_weekly_report.py（只读，零产线改动；可幂等重跑）")
    L.append("")
    L.append("## 一、五线状态总表")
    L.append("")
    L.append("| 观察线 | 本周增量 | 累计 | 判定 | 备注 |")
    L.append("|---|---|---|---|---|")
    for ln in lines:
        d = ln["data"]
        if ln["line"] == "双刀注记观察":
            wk = "%s 个信号（注记覆盖 %s），gate_would_block 触发 %s" % (
                d.get("signals_total", "-"), d.get("signals_annotated", "-"),
                d.get("gate_would_block_hits", "-"))
            acc = "宽度序列 %s 个交易日" % len(d.get("breadth_series_week") or [])
        elif ln["line"] == "chanlun P5 反向观察":
            nb = d.get("week_new_3buy") or {}
            wk = "新 3 买 %d（bei %d / 无 %d）" % (
                nb.get("3buy_total", 0), nb.get("3buy_bei", 0), nb.get("3buy_nobei", 0))
            acc = "P5 样本 %s（缓存截至 %s）" % (d.get("p5_3buy_sample", "-"), d.get("grid_d_end", "-"))
        elif ln["line"] == "linkage 影子":
            wk = "%d 行（信号② %d）" % (d.get("rows_week", 0), d.get("signal2_week", 0))
            acc = "%d 行；观察池覆盖 %s；信号②累计 %d" % (
                d.get("rows_total", 0), d.get("pool_cover_week", "-"), d.get("signal2_total", 0))
        elif ln["line"] == "jev 毕业冲刺":
            cov = d.get("coverage_latest")
            cov_s = ("%.1f%%（%s）" % (cov["pct"], cov["ts"])) if isinstance(cov, dict) else str(cov)
            wk = "FOCUS %d 条" % d.get("focus_week", 0)
            acc = "FOCUS %d，有 outcome %d，覆盖率 %s" % (
                d.get("focus_total", 0), d.get("focus_with_outcome", 0), cov_s)
        else:
            wk = "%s 条" % d.get("rows_week", 0)
            acc = "%s 条" % d.get("rows_total", 0)
        L.append("| %s | %s | %s | %s | %s |" % (
            ln["line"], wk, acc, status_mark(ln["status"]),
            (ln["notes"] or [""])[0]))
    L.append("")
    L.append("## 二、各线明细")
    L.append("")
    for ln in lines:
        L.append("### %s —— %s" % (ln["line"], status_mark(ln["status"])))
        for n in ln["notes"]:
            L.append("- %s" % n)
        L.append("")
        L.append("```json")
        L.append(json.dumps(ln["data"], ensure_ascii=False, indent=1))
        L.append("```")
        L.append("")
    L.append("## 三、发现的问题")
    L.append("")
    for x in issues or ["（无）"]:
        L.append("- %s" % x)
    L.append("")
    L.append("## 四、遗留风险")
    L.append("")
    for x in risks or ["（无）"]:
        L.append("- %s" % x)
    L.append("")
    L.append("## 五、触发评审的条件提醒")
    L.append("")
    L.append("- 双刀：任一 bucket（重点 >=50 档）样本满 %d 个 → 提醒做 θ50 门控假说中期检验。" % REVIEW_BUCKET_N)
    L.append("- chanlun P5：反向观察（标注 3 买更差）在 |diff|>=1pp 期间持续跟踪；两周连续减弱可申请撤项。")
    L.append("- linkage：信号②滞涨样本满 30 → 可做首次分层统计。")
    L.append("- jev：n≥%d 且 FOCUS 前向均值>+1pp → 提交毕业评审。" % JEV_GRAD_N)
    L.append("- 三档对照：首采落地后首次周报自动解除\"待首采\"。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="影子对账周报生成器（只读）")
    ap.add_argument("--week", default=None,
                    help="周锚点 YYYY-MM-DD（取所在周），默认本周")
    ap.add_argument("--json-only", action="store_true", help="只写 JSON 与 MD 数据文件，不打印全文")
    args = ap.parse_args()

    monday, today = week_range(args.week)
    week_label = "%04d-W%02d" % monday.isocalendar()[:2]

    lines = [
        line_knife(monday, today),
        line_chanlun(monday, today),
        line_linkage(monday, today),
        line_jev(monday, today),
        line_gate3(monday, today),
    ]

    issues, risks = [], []
    for ln in lines:
        if ln["status"] == "red":
            issues.append("%s：红灯 —— %s" % (ln["line"], "；".join(ln["notes"])))
        elif ln["status"] == "yellow":
            issues.append("%s：黄灯 —— %s" % (ln["line"], "；".join(ln["notes"])))
        elif ln["status"] == "gray":
            risks.append("%s：待首采 —— %s" % (ln["line"], "；".join(ln["notes"])))
    # 结构性遗留风险（首跑已知）
    risks.append("双刀线无逐日产物存档（portal/qd_scan_cache.json 为覆写单文件），"
                 "bucket×前向收益回算只能从存档建立之日起算。")
    risks.append("chanlun 网格缓存截至其 D_END，跨周未重跑时前向收益差为缓存口径（报告中已标注）。")
    risks.append("jev 覆盖率行依赖 review.log 存在；若日志轮转会丢失历史覆盖率序列。")

    payload = {"week_label": week_label, "monday": monday.isoformat(),
               "generated_at": datetime.now().isoformat(timespec="seconds"),
               "lines": lines, "issues": issues, "risks": risks}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_md(week_label, monday, today, lines, issues, risks))

    if not args.json_only:
        with open(OUT_MD, encoding="utf-8") as f:
            print(f.read())
    print("已写出: %s" % OUT_MD)
    print("已写出: %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
