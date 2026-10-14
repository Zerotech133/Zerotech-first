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
P_JEV_STATUS = os.path.join(JEV, "review_data", "graduation_status_v2.json")
P_JEV_STATUS_PY = os.path.join(JEV, "jev_graduation_status.py")

# [20261014_m metrics-dev] P1 判据度量批新增路径（全部只读）
P_ND_HIST = os.path.join(QT, "data", "nine_dim_history.jsonl")   # k33 警报池影子库
P_STRONG = os.path.join(QT, "reports", "strong_latest.json")     # strong 快照
P_ASHARE = os.path.join(QT, "data", "ashare")                    # 湖个股日线
P_BC_STATE = os.path.join(QT, "data", "breadth_cross_state.json")  # 穿越监测状态
P_BC_EVENTS = os.path.join(QT, "data", "breadth_cross_events.jsonl")
K33_N_TH = 30               # k33 对账样本门槛（checklist B2 同口径）
NINEDIM_ZERO_MISS_DAYS = 20  # ninedim 零漏报观察窗（交易日）
G3_TIMER_DAYS = 60          # G3 统一计时窗（交易日）
STRONG_COV_TH = 0.9         # strong coverage 判据
LINKAGE_HIT_TH = 0.55       # linkage 命中率判据
LINKAGE_HIT_N = 30          # linkage 命中率样本门槛
# G3 统一进度表起算日（注记式：日期可在下方 dict 直接改，不影响其他逻辑）
G3_ANCHORS = [
    {"name": "双刀注记（θ50 观察）", "date": "20261014",
     "src": "注记批上线日 20261014（knife-dev 批；注记式可改）"},
    {"name": "jev 影子冲刺", "date": "20260922",
     "src": "triples 首条 ts=2026-09-22（自动推导兜底值）"},
    {"name": "k33 警报池影子", "date": "20261002",
     "src": "K33 定稿日 2026-10-02（k33_shadow_report_001）"},
    {"name": "linkage 影子采集", "date": "20261014",
     "src": "linkage_shadow.jsonl 首行 ts（自动推导兜底值）"},
    {"name": "三档对照（G3）", "date": "20261014",
     "src": "gate_shadow_3tier.jsonl 首行 ts（自动推导兜底值）"},
]

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
def jev_v2_status():
    """[v2 集成 20261014] 取毕业判据 v2 全域状态。

    优先读 graduation_status_v2.json；缺失/口径不符时用状态脚本刷新一次。
    任何异常抛给调用方（line_jev 捕获后回退 v1 口径）。
    """
    st = read_json(P_JEV_STATUS)
    if not (isinstance(st, dict) and st.get("adopted") == "v2" and st.get("domains")):
        import subprocess
        out = subprocess.run(
            [r"C:/Program Files/Python311/python.exe", P_JEV_STATUS_PY, "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        if out.returncode != 0:
            raise RuntimeError("状态脚本退出码 %d" % out.returncode)
        st = json.loads(out.stdout)
    if not (isinstance(st, dict) and st.get("adopted") == "v2" and st.get("domains")):
        raise RuntimeError("状态文件/脚本输出口径不符")
    return st


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

    # [v2 集成 20261014] 优先 v2 口径；失败回退 v1 并标注
    v2 = None
    try:
        v2 = jev_v2_status()
    except Exception as e:
        r["data"]["grad_v2_error"] = "%s: %s" % (type(e).__name__, e)
        r["notes"].append("v2 状态获取失败（%s），本次回退 v1 口径" % r["data"]["grad_v2_error"])

    # FOCUS 前向收益（triples.jsonl outcome 已回填部分；v1 口径保留作对照/回退）
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

    if v2 is not None:
        # ── v2 正式口径 ──
        doms = v2["domains"]
        r["data"]["grad_v2"] = {"adopted": v2.get("adopted"),
                                "computed_at": v2.get("computed_at"),
                                "sample_window": (v2.get("sample") or {}).get("window"),
                                "light": v2.get("light"),
                                "domains": {m: {k: d.get(k) for k in
                                                ("n_eff", "cluster_mean_pp", "focus_pass_gap_pp",
                                                 "pass", "state")}
                                            for m, d in doms.items()}}
        r["data"]["grad_rule"] = ("v2（2026-10-14 采纳）：每域独立，n_eff≥30 且 FOCUS 聚类均值>+1pp；"
                                  "辅助 FOCUS−PASS>0；CN 入口缺位单列；CRYPTO 过渡期剔除单独监控")
        passed = [m for m, d in doms.items() if d.get("pass")]
        if cov and cov.get("pct", 0) < 50:
            r["status"] = "red"
            r["notes"].append("回填覆盖率 %.1f%% 低于 50%% 阈值" % cov["pct"])
        if passed:
            r["notes"].append("v2 毕业条件达成：%s 达标" % "、".join(passed))
        else:
            gaps = ["%s FOCUS−PASS %+.2fpp" % (m, d["focus_pass_gap_pp"])
                    for m, d in doms.items()
                    if d.get("focus_pass_gap_pp") is not None and d["focus_pass_gap_pp"] > 0]
            light = v2.get("light")
            if r["status"] != "red" and light == "yellow":
                r["status"] = "yellow"
                r["notes"].append("v2 无域达标，但有正 FOCUS−PASS 的域（%s）——regime 敏感型未过线，继续冲刺"
                                  % "；".join(gaps))
            else:
                r["status"] = "red"
                r["notes"].append("v2 全域未达标且 FOCUS−PASS 全负")
        r["return_v1_if_v2_fails"] = False
        return r

    # ── v1 口径（回退路径 / 对照） ──
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


# ======================================================================
# [20261014_m metrics-dev] P1 上线判据度量（计划 14.3 缺口清单逐项落地）
# 原则：全部只读；每项独立 try/except，单线失败标 error 不炸周报；
#       缺数据如实标"待首采/样本不足"；核心计算抽成可单测的纯函数。
# ======================================================================

def load_calendar():
    """交易日历：qd_breadth_series.json dates（20230103 起，每日覆写）。
    失败 → None（调用方标 error）。"""
    bs = read_json(P_QD_BREADTH)
    if not (isinstance(bs, dict) and bs.get("dates")):
        return None
    return [str(d) for d in bs["dates"]]


def trading_days_between(start8, end8, calendar):
    """(start8, end8] 内的交易日数（calendar 为升序 YYYYMMDD 列表）。
    start 不在日历中也成立（取日历序比较）；end8 早于 start8 → 0。"""
    return sum(1 for d in calendar if start8 < d <= end8)


def lake_close(code, ashare_dir=P_ASHARE):
    """湖个股日线 → 升序 [(d8, close)]；缺失/坏数据 → []。"""
    import pandas as pd
    p = os.path.join(ashare_dir, str(code).replace(".", "_") + ".parquet")
    if not os.path.exists(p):
        return []
    try:
        df = pd.read_parquet(p, columns=["trade_date", "close"])
        df = df.drop_duplicates("trade_date", keep="last")
        df = df.sort_values("trade_date")
        return [(str(int(d)), float(c)) for d, c in
                zip(df["trade_date"], df["close"])]
    except Exception:
        return []


def _close_at(bars, d8, stale_days=7):
    """bars（升序 [(d8,close)]）中 ≤d8 的最近一根 → (d8, close)；无 → None。"""
    best = None
    for bd, bc in bars:
        if bd <= d8:
            best = (bd, bc)
        else:
            break
    return best


def calc_linkage_hitrate(rows, calendar, close_fn, block_fn=None,
                         horizons=(5, 10)):
    """linkage 滚动命中率（纯函数，可单测）。

    口径（linkage_hitrate_backtest 同判例，T+k=信号日收盘→T+k 收盘）：
      命中A 绝对        ：个股前向收益 > 0
      命中B 跑赢板块    ：个股前向收益 − 板块前向收益 > 0（板块可得时）
    样本：linkage_shadow.jsonl 行（ref_date+signal）；episode 去重——
    同 (code,signal) 在 k 个交易日窗口内连续触发只计首日。
    maturity：目标日 ≤ 日历末日 且 个股在目标日后有 bar → matured。
    返回 {"h5": {...}, "h10": {...}}，每档：
      n/episodes/matured/pending/hits_abs/rate_abs/hits_ex/rate_ex
    """
    out = {}
    cal_idx = {d: i for i, d in enumerate(calendar)}
    sig_by_code = {}
    for x in rows:
        rd = str(x.get("ref_date") or "").replace("-", "")
        if len(rd) != 8:
            continue
        sig_by_code.setdefault(
            (str(x.get("code")), str(x.get("signal"))), []).append(rd)
    for h in horizons:
        seen_last = {}                      # episode 去重：上次触发日
        n = episodes = matured = pending = hits_abs = hits_ex = 0
        ex_n = 0                            # 有板块对照的成熟样本数
        for (code, sig), dates in sorted(sig_by_code.items()):
            bars = close_fn(code)           # 逐 code 缓存交给 close_fn 外层
            bbars = block_fn(str((rows and _row_block(rows, code, sig)) or "")) \
                if block_fn else None
            for rd in sorted(dates):
                i = cal_idx.get(rd)
                if i is None:               # 非交易日锚：取其后首个交易日
                    later = [j for j, d in enumerate(calendar) if d >= rd]
                    if not later:
                        pending += 1
                        continue
                    i = later[0]
                # episode 去重（按 (code,sig) 维度）
                key = (code, sig)
                if key in seen_last and i - seen_last[key] < h:
                    continue
                seen_last[key] = i
                if i + h >= len(calendar):
                    episodes += 1
                    pending += 1            # 前向窗口未走完
                    continue
                tgt = calendar[i + h]
                c0 = _close_at(bars, calendar[i])
                c1 = _close_at(bars, tgt)
                episodes += 1
                if not (c0 and c1 and c1[0] > c0[0]):
                    pending += 1            # 行情不可得/未更新到目标日
                    continue
                matured += 1
                n += 1
                r_s = c1[1] / c0[1] - 1
                if r_s > 0:
                    hits_abs += 1
                if bbars:
                    b0, b1 = _close_at(bbars, calendar[i]), _close_at(bbars, tgt)
                    if b0 and b1 and b1[0] > b0[0]:
                        ex_n += 1
                        if r_s - (b1[1] / b0[1] - 1) > 0:
                            hits_ex += 1
        out["h%d" % h] = {
            "episodes": episodes, "matured": matured, "pending": pending,
            "hits_abs": hits_abs,
            "rate_abs": round(hits_abs / matured, 4) if matured else None,
            "ex_n": ex_n, "hits_ex": hits_ex,
            "rate_ex": round(hits_ex / ex_n, 4) if ex_n else None,
        }
    return out


def _row_block(rows, code, sig):
    """取该 (code,signal) 样本的板块代码（880xxx）。"""
    for x in rows:
        if str(x.get("code")) == code and str(x.get("signal")) == sig:
            b = (x.get("block") or {}).get("code")
            if b:
                return str(b)
    return ""


def crit_k33_recon(hist_path=P_ND_HIST):
    """判据1：chanlun k33 对账≥30 —— 警报池影子库周度对账计数。"""
    r = {"metric": "k33_alert_recon_n", "th": K33_N_TH}
    if not os.path.exists(hist_path):
        r["state"] = "待首采"
        r["note"] = "nine_dim_history.jsonl 不存在——警报池尚无入池记录，对账 n=0"
        r["n"] = 0
        return r
    rows = read_jsonl(hist_path)
    alerts = [x for x in rows if x.get("pool_type") == "alert"]
    first = min((str(x.get("dt") or "")[:10] for x in alerts if x.get("dt")),
                default=None)
    # 字段形态检查（含 zg/zd 漂移检查——样本库出现 zg/zd 即标注）
    need = {"dt", "code", "pool_type"}
    miss = sum(1 for x in alerts if not need.issubset(x.keys()))
    zg_zd = sum(1 for x in alerts if "zg" in x or "zd" in x)
    r.update({"n": len(alerts), "rows_total": len(rows),
              "first_alert_date": first, "field_missing_rows": miss,
              "zg_zd_rows": zg_zd})
    if zg_zd:
        r["note"] = "样本库出现 zg/zd 字段 %d 行（字段漂移，按留痕文档口径核对后再计）" % zg_zd
    if len(alerts) >= K33_N_TH:
        r["state"] = "已达标"
        r["note"] = "对账样本 n=%d ≥%d（thinned 等效以 k33_shadow_check 例行 B 流程为准）" % (
            len(alerts), K33_N_TH)
    else:
        r["state"] = "样本不足"
        r["note"] = "对账样本 n=%d/%d，继续攒（低频属预期，勿调 ALERT_TH）" % (
            len(alerts), K33_N_TH)
    return r


def crit_linkage_hitrate(monday, today):
    """判据2：linkage 命中率≥55% —— 前向收益回填滚动命中率。"""
    rows = read_jsonl(P_LINKAGE)
    if not rows:
        return {"metric": "linkage_hitrate", "state": "待首采",
                "note": "linkage_shadow.jsonl 缺失或为空"}
    cal = load_calendar()
    if not cal:
        return {"metric": "linkage_hitrate", "state": "error",
                "note": "交易日历（qd_breadth_series）不可得"}
    cache = {}

    def close_fn(code):
        if code not in cache:
            try:
                cache[code] = lake_close(code)
            except Exception:
                cache[code] = []
        return cache[code]

    def block_fn(bcode):
        if not bcode:
            return None
        try:
            import linkage
            bars = linkage.block_bars(bcode, "1d", limit=260)
            return [(str(b["dt"]).replace("-", ""), b["close"]) for b in bars]
        except Exception:
            return None

    hr = calc_linkage_hitrate(rows, cal, close_fn, block_fn, horizons=(5, 10))
    r = {"metric": "linkage_hitrate", "th": LINKAGE_HIT_TH, "n_th": LINKAGE_HIT_N,
         "caliber": "A 绝对(个股T+k收盘/T+k前收-1>0)；B 跑赢板块(超额>0)",
         "horizons": hr}
    m5 = hr["h5"]["matured"]
    rate5 = hr["h5"]["rate_abs"]
    if not m5:
        r["state"] = "样本不足"
        r["note"] = "T+5 成熟样本 0（首采 %s 起，前向窗口未走完），待后续周报滚动出数" % (
            str(rows[0].get("ref_date") or rows[0].get("ts"))[:10])
    elif m5 < LINKAGE_HIT_N:
        r["state"] = "样本不足"
        r["note"] = "T+5 成熟样本 %d/%d，当前命中率 %s（未达评审样本量）" % (
            m5, LINKAGE_HIT_N, "%.1f%%" % (rate5 * 100) if rate5 is not None else "-")
    elif rate5 is not None and rate5 >= LINKAGE_HIT_TH:
        r["state"] = "已达标"
        r["note"] = "T+5 命中率 %.1f%% ≥%.0f%%（n=%d）——达到挂账评审线" % (
            rate5 * 100, LINKAGE_HIT_TH * 100, m5)
    else:
        r["state"] = "未达标"
        r["note"] = "T+5 命中率 %.1f%% <%.0f%%（n=%d），继续观察" % (
            rate5 * 100, LINKAGE_HIT_TH * 100, m5)
    return r


def crit_strong_coverage(strong_path=P_STRONG):
    """判据3：strong coverage≥0.9 —— 快照 member_coverage/full_scan 趋势。"""
    s = read_json(strong_path)
    if not isinstance(s, dict):
        return {"metric": "strong_coverage", "state": "待首采",
                "note": "reports/strong_latest.json 缺失"}
    cov = s.get("member_coverage")
    full = s.get("full_scan")
    r = {"metric": "strong_coverage", "th": STRONG_COV_TH,
         "snapshot_date": s.get("date"), "member_coverage": cov,
         "full_scan": full}
    if full is None:
        r["note_extra"] = "full_scan 字段未落（旧版快照），strong_scan 产出含该字段后开始记录趋势"
    if isinstance(cov, (int, float)) and cov >= STRONG_COV_TH:
        r["state"] = "已达标"
        r["note"] = "member_coverage=%.3f ≥%.1f" % (cov, STRONG_COV_TH)
    else:
        r["state"] = "未达标"
        r["note"] = "member_coverage=%s <%.1f（--full 未常态化，coverage_note: 每板块仅评前 20 成员）" % (
            cov, STRONG_COV_TH)
    return r


def crit_ninedim_timer(hist_path=P_ND_HIST, calendar=None):
    """判据4：ninedim 零漏报 20 日 —— 警报池首条日期起算计时。"""
    if not os.path.exists(hist_path):
        return {"metric": "ninedim_zero_miss_timer", "state": "未启动",
                "note": "警报池空（history 不存在），零漏报计时未启动"}
    rows = [x for x in read_jsonl(hist_path) if x.get("pool_type") == "alert"]
    if not rows:
        return {"metric": "ninedim_zero_miss_timer", "state": "未启动",
                "note": "警报池空（0 条 alert），零漏报计时未启动"}
    first = min(str(x.get("dt") or "").replace("-", "")[:8]
                for x in rows if x.get("dt"))
    if not calendar:
        calendar = load_calendar() or []
    today8 = date.today().strftime("%Y%m%d")
    day = trading_days_between(first, today8, calendar)
    if day <= 0:
        day = 1                                  # 首日即计 day 1
    return {"metric": "ninedim_zero_miss_timer", "first_alert_date": first,
            "state": "计时中", "day": day, "window": NINEDIM_ZERO_MISS_DAYS,
            "note": "警报池首条 %s，零漏报计时中 day %d/%d（交易日）" % (
                first, day, NINEDIM_ZERO_MISS_DAYS)}


def crit_qpzm_gate_counts(gate3_path=P_GATE3):
    """判据5：qpzm 错杀 30 日运行 —— 三档影子各策略各档累计行数。"""
    if not os.path.exists(gate3_path):
        return {"metric": "qpzm_gate_shadow_counts", "state": "待首采",
                "note": "gate_shadow_3tier.jsonl 不存在（待 gate_shadow_collect 首采）"}
    rows = read_jsonl(gate3_path)
    if not rows:
        return {"metric": "qpzm_gate_shadow_counts", "state": "待首采",
                "note": "gate_shadow_3tier.jsonl 为空"}
    per, ok_per = {}, {}
    dates = []
    for x in rows:
        k = "%s|θ%s" % (x.get("strategy") or "?", x.get("theta") or "?")
        per[k] = per.get(k, 0) + 1
        if x.get("source_status") == "ok":
            ok_per[k] = ok_per.get(k, 0) + 1
        d = str(x.get("date") or "")
        if len(d) == 8:
            dates.append(d)
    return {"metric": "qpzm_gate_shadow_counts", "state": "采集中",
            "rows_total": len(rows), "per_strategy_theta": per,
            "per_strategy_theta_ok": ok_per,
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
            "note": "累计 %d 行（ok %d 行），日期跨 %s~%s——W8 评审以 ok 行为准" % (
                len(rows), sum(ok_per.values()),
                min(dates) if dates else "-", max(dates) if dates else "-")}


def crit_g3_progress(calendar=None, anchors=None):
    """判据6：G3 60 日计时 —— 统一进度表（起算日→已积累交易日数）。"""
    if calendar is None:
        calendar = load_calendar()
    if not calendar:
        return {"metric": "g3_progress", "state": "error",
                "note": "交易日历不可得（qd_breadth_series 缺 dates）"}
    anchors = G3_ANCHORS if anchors is None else anchors
    today8 = date.today().strftime("%Y%m%d")
    table = []
    for a in anchors:
        d8 = str(a.get("date") or "").replace("-", "")
        days = trading_days_between(d8, today8, calendar)
        table.append({"name": a.get("name"), "start": d8, "trading_days": days,
                      "pct_of_%d" % G3_TIMER_DAYS:
                          round(days / G3_TIMER_DAYS * 100, 1),
                      "src": a.get("src")})
    return {"metric": "g3_progress", "state": "计时中",
            "window": G3_TIMER_DAYS, "calendar_last": calendar[-1],
            "table": table,
            "note": "G3 统一进度表：60 交易日计时窗，各线已积累天数如上（注记式起算日，可改）"}


def metrics_criteria(monday, today):
    """六项上线判据度量汇总（每项独立 try/except，单线失败不炸周报）。"""
    crit = {}
    steps = (
        ("c1_k33_recon", crit_k33_recon),
        ("c2_linkage_hitrate", lambda: crit_linkage_hitrate(monday, today)),
        ("c3_strong_coverage", crit_strong_coverage),
        ("c4_ninedim_timer", crit_ninedim_timer),
        ("c5_qpzm_gate_counts", crit_qpzm_gate_counts),
        ("c6_g3_progress", crit_g3_progress),
    )
    for key, fn in steps:
        try:
            crit[key] = fn()
        except Exception as e:
            crit[key] = {"metric": key, "state": "error",
                         "note": "%s: %s" % (type(e).__name__, e)}
    return crit


def render_criteria_md(crit):
    """判据度量 → MD 小节（表格 + 逐项 JSON）。"""
    L = []
    L.append("## 五、上线判据度量（P1 判据度量批 20261014_m）")
    L.append("")
    L.append("W8 评审判据周度读数（缺数据如实标\"待首采/样本不足\"）：")
    L.append("")
    L.append("| 判据 | 读数 | 状态 |")
    L.append("|---|---|---|")
    for key, c in crit.items():
        m = c.get("metric", key)
        note = c.get("note", "")
        if m == "k33_alert_recon_n":
            read = "对账样本 n=%s/%s" % (c.get("n", "-"), c.get("th", 30))
        elif m == "linkage_hitrate":
            h5 = (c.get("horizons") or {}).get("h5") or {}
            read = "T+5 命中率 %s（成熟 n=%s）" % (
                ("%.1f%%" % (h5["rate_abs"] * 100)) if h5.get("rate_abs") is not None else "-",
                h5.get("matured", 0))
        elif m == "strong_coverage":
            read = "member_coverage=%s" % c.get("member_coverage", "-")
        elif m == "ninedim_zero_miss_timer":
            read = ("计时中 day %s/%s（首条 %s）" % (c.get("day"), c.get("window"), c.get("first_alert_date"))
                    if c.get("state") == "计时中" else "未启动（警报池空）")
        elif m == "qpzm_gate_shadow_counts":
            read = "累计 %s 行（ok %s）" % (c.get("rows_total", "-"),
                                           sum((c.get("per_strategy_theta_ok") or {}).values()) or 0)
        elif m == "g3_progress":
            t = (c.get("table") or [])
            read = "；".join("%s %sd/%s" % (x.get("name"), x.get("trading_days"), c.get("window")) for x in t[:6])
        else:
            read = note[:60]
        L.append("| %s | %s | %s |" % (m, read, c.get("state", "-")))
    L.append("")
    for key, c in crit.items():
        L.append("<details><summary>%s</summary>" % key)
        L.append("")
        L.append("```json")
        L.append(json.dumps(c, ensure_ascii=False, indent=1))
        L.append("```")
        L.append("")
        L.append("</details>")
        L.append("")
    return "\n".join(L)


# ---------- 市况·正确路径（复盘落地 20261014 breadth-mon 联动） ----------
CROSS_TH = 0.45
FAKE_TH = 0.40
# 复盘静态参照（出处：历史数据全景复盘与当前正确路径_2026-10-14，156 周
# regime_timeline 实测，非本周动态计算，仅供决策参照）
_REGIME_REF = ("<50% 区间的历史参照（复盘 2026-10-14）：同款宽度状态后 20 日"
               "均值 +3.9%、胜率 67.4%；<35%→45% 穿越确认后 20 日均值 "
               "+6.4%（23 次）；口径=不抄底、等穿越确认。")


def market_path_status():
    """市况与正确路径状态（只读）。宽度序列 + 穿越监测状态合成一个 dict。

    days_below_45：序列末尾连续 <45% 的锚数（=穿越等待已持续交易日数）；
    monitor：breadth_cross_state.json 原样（armed/pending/last_date）；
    指引文本按复盘口径生成（不构成任何策略行为变更）。
    """
    with open(P_QD_BREADTH, encoding="utf-8") as f:
        d = json.load(f)
    pairs = [(str(a), float(b)) for a, b in zip(d.get("dates") or [],
                                                d.get("breadth") or [])
             if b is not None]
    if not pairs:
        raise ValueError("宽度序列为空")
    date, br = pairs[-1]
    streak = 0
    for _, b in reversed(pairs):
        if b < CROSS_TH:
            streak += 1
        else:
            break
    lo20 = min(b for _, b in pairs[-20:])
    mon = {}
    try:
        with open(P_BC_STATE, encoding="utf-8") as f:
            mon = json.load(f)
    except Exception:
        mon = {"note": "穿越监测状态文件缺失（breadth-mon 未播种或被清理）"}
    # W8 议题二前置（20261015）：HMM 软闸门影子读数。研究口径只读，
    # 失败不炸市况节（hmm 影子字典带 error 时渲染侧降级为一行）。
    hmm = {}
    try:
        import hmm_shadow as _hsh
        hmm = _hsh.compute_shadow()
    except Exception as e:
        hmm = {"error": "%s: %s" % (type(e).__name__, str(e)[:100])}
    pending = mon.get("pending")
    if pending:
        guide = ("穿越观察窗第 %s 日（%s 穿越）：窗内跌破 40%% 将告警撤回，"
                 "满 5 日告警确认保持" % (pending.get("days_since", 0) + 1,
                                          pending.get("cross_date", "?")))
    elif br >= CROSS_TH:
        guide = "宽度 ≥45%：穿越态，按信号驱动入场、宽度仅作环境背景"
    elif br < FAKE_TH:
        guide = ("深度弱势（<40%）：总闸纪律优先，不抄底；等上穿 45% 穿越确认"
                 "（届时飞书告警）")
    else:
        guide = ("40~45% 观察带：不抄底；关注能否站上 45%（穿越确认线），"
                 "站稳 5 日才算确认")
    return {
        "breadth": round(br, 4), "breadth_pct": "%.2f%%" % (br * 100),
        "as_of": date, "dist_to_cross_pp": round((CROSS_TH - br) * 100, 2),
        "days_below_45_streak": streak, "low20": round(lo20, 4),
        "monitor": mon, "guide": guide, "regime_ref": _REGIME_REF,
        "hmm": hmm,
    }


def render_market_path_md(st):
    """市况·正确路径 → MD 小节（第六节）。"""
    L = []
    L.append("## 六、市况·正确路径（复盘 2026-10-14）")
    L.append("")
    L.append("- 最新宽度：**%s**（锚 %s；距穿越线 45%% 还差 %.2fpp）"
             % (st["breadth_pct"], st["as_of"], st["dist_to_cross_pp"]))
    L.append("- 已连续 %d 个交易日 <45%%；近 20 日最低宽度 %s"
             % (st["days_below_45_streak"],
                "%.2f%%" % (st["low20"] * 100)))
    L.append("- 穿越监测器：%s" % json.dumps(st["monitor"], ensure_ascii=False))
    L.append("- 指引：%s" % st["guide"])
    L.append("- %s" % st["regime_ref"])
    # HMM 影子小行（W8 议题二前置，研究口径）
    hmm = st.get("hmm") or {}
    if hmm.get("error"):
        L.append("- HMM 影子：不可用（%s）" % hmm["error"])
    else:
        L.append("- HMM 影子（研究口径，非交易信号）：K=%d（窗 %d 日）"
                 "风险态后验=%.2f%%、迟滞门 %s；硬阈值线 45%%/40%% 并列"
                 "——两口径背离即 W8 议题二核心观察点"
                 % (hmm.get("K", 0), hmm.get("window_days", 0),
                    hmm.get("p_risk_filtered", 0.0) * 100,
                    "ON" if hmm.get("hysteresis_on") else "off"))
    L.append("")
    return "\n".join(L)


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
            gv = d.get("grad_v2")
            if gv:
                parts = []
                for m in ("US", "HK", "CRYPTO", "CN"):
                    dd = (gv.get("domains") or {}).get(m)
                    if dd is None:
                        continue
                    if dd.get("state") == "入口缺位":
                        parts.append("%s 入口缺位" % m)
                    elif dd.get("state") == "过渡期剔除":
                        cm = dd.get("cluster_mean_pp")
                        parts.append("%s 过渡监控 %s" % (m, "%+.2fpp" % cm if cm is not None else "无值"))
                    else:
                        parts.append("%s n_eff%s/%spp%s" % (
                            m, dd.get("n_eff", "-"),
                            "%+.2f" % dd["cluster_mean_pp"] if dd.get("cluster_mean_pp") is not None else "-",
                            "✓" if dd.get("pass") else "✗"))
                acc += "；v2 口径：" + "，".join(parts)
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
    L.append("- jev：v2 口径（2026-10-14 采纳）任一 live 域达标（n_eff≥30 且 FOCUS 聚类均值>+1pp）"
             "→ 提交毕业评审；CRYPTO 过渡期剔除仅监控，CN 入口缺位单列。"
             "（v1 对照口径：n≥%d 且 FOCUS 前向均值>+1pp。）" % JEV_GRAD_N)
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

    # [20261014_m] P1 判据度量（逐项 try/except，单线失败不炸周报）
    try:
        criteria = metrics_criteria(monday, today)
    except Exception as e:
        criteria = {"error": {"state": "error",
                              "note": "%s: %s" % (type(e).__name__, e)}}

    # [20261014_bm] 市况·正确路径（复盘落地联动 breadth-mon，只读）
    try:
        market_path = market_path_status()
    except Exception as e:
        market_path = {"state": "error",
                       "note": "%s: %s" % (type(e).__name__, e)}

    payload = {"week_label": week_label, "monday": monday.isoformat(),
               "generated_at": datetime.now().isoformat(timespec="seconds"),
               "lines": lines, "issues": issues, "risks": risks,
               "criteria": criteria, "market_path": market_path}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    md = render_md(week_label, monday, today, lines, issues, risks)
    # [20261014_m] 判据度量小节插为第五节；[20261014_bm] 市况节第六，
    # 原"触发评审"顺延为第七节
    marker = "## 五、触发评审的条件提醒"
    crit_md = render_criteria_md(criteria)
    try:
        path_md = render_market_path_md(market_path)
    except Exception:
        path_md = "## 六、市况·正确路径（复盘 2026-10-14）\n\n- 本节生成失败（见 JSON market_path.error）\n"
    if marker in md:
        md = md.replace(marker, crit_md + "\n" + path_md
                        + "## 七、触发评审的条件提醒")
    else:
        md += "\n" + crit_md
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    if not args.json_only:
        with open(OUT_MD, encoding="utf-8") as f:
            print(f.read())
    print("已写出: %s" % OUT_MD)
    print("已写出: %s" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
