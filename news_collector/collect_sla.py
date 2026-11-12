# -*- coding: utf-8 -*-
"""采集新鲜度 SLA 视图（研发方案 R-04 落地 / 现场发现 D7）

一屏回答四个问题（替代人肉 `ls -lt`）：
  1) 每个源最后一次成功落盘是什么时候、滞后多少小时；
  2) 队列期望覆盖多少票、实际覆盖多少票、缺口多大；
  3) 有没有积压（xq `_deferred.json` 跨日待重试）与空文件；
  4) 连续几轮不达标（跨轮记忆，来自本脚本自己的 state 文件）。

设计纪律（对齐《OSINT 方法论结合方案》与台账口径）：
  - **只读**：不改任何采集产物、不触网、不启浏览器；唯一写入是本脚本自己的
    `data/collect_sla.json`、`data/collect_sla_state.json`（状态记忆）。
  - **阈值不做事实裁定**：全部标 `DEFAULT`（经验值），可用 `--thresholds` 覆盖；
    实际调参归 11-15 微调日与上级，本席只提供读数。
  - **口径可溯源**：每行带 `dir` / `expected_from` / `last_ok_src`，换人可复核。
  - **`last_ok` 是「内容最后新增」，不是「运行成功」**：irm/sse/taoguba/guba 都是
    append-on-new 写盘，源端当天 0 新帖时 mtime 不动。故带 `runlog_rel` 的源另报
    `last_run_ts`（共享运行日志 mtime，源级不可区分），且只用于把 RED **降级解释**
    为 AMBER，绝不把 RED 洗成 GREEN。
  - **节假日感知**：过去 3 天内每出现一个周末日，阈值 +`weekend_slack_hours`，
    与 `sched_heartbeat.py::_line_days` 同判例，避免周末必然误报。

退出码：0=无 RED（GREEN/AMBER 均算通过）；1=存在 RED；2=读盘/依赖错误。
注意：若将来注册到计划任务，**必须**用固定 exit 0 的 wrapper（判例：
「给 Task Scheduler 的退出码语义是任务跑完了吗，不是业务结论好不好」）。

用法：
  python news_collector/collect_sla.py                # 表格 + 落 JSON/state
  python news_collector/collect_sla.py --json         # 只打 JSON
  python news_collector/collect_sla.py --md out.md    # 额外落 markdown 表
  python news_collector/collect_sla.py --no-state     # 不读写 state（纯快照）
  python news_collector/collect_sla.py --now "2026-11-13T09:00:00"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # aios 仓库根

OUT_F = os.path.join(HERE, "data", "collect_sla.json")
STATE_F = os.path.join(HERE, "data", "collect_sla_state.json")

# DEFAULT 阈值（经验值，非裁定）。AMBER 用交易日 26h（覆盖「隔夜+盘中」），
# RED 用 48h（两个交易日未刷新）；连续 3 轮 RED 直接判 RED（哪怕滞后未到 48h）。
DEFAULT_THRESHOLDS = {
    "amber_stale_hours": 26.0,
    "red_stale_hours": 48.0,
    "red_consecutive": 3,
    "weekend_slack_hours": 24.0,
}

# 源声明表。`rel` 相对 aios 仓库根；`expected_from` 说明覆盖分母的取数口径。
SOURCES = [
    {
        "id": "news", "label": "新闻流(自采)", "kind": "news",
        "rel": "news_collector/news.db", "status_rel": "news_collector/status.json",
        "expected_from": None,
        "evidence": "news_collector.py: stats/news.db max(ts)；子源 ok/got/new 读 status.json",
    },
    {
        "id": "irm", "label": "互动易", "kind": "jsonl_dir",
        "rel": "data/irm", "expected": "chanlun_actionable",
        "runlog_rel": "news_collector/logs/target_queue_intel_daily.log",
        "evidence": "irm_collector.py:42 OUT_DIR=dirname(HERE)/data/irm",
    },
    {
        "id": "sse", "label": "上证e互动", "kind": "jsonl_dir",
        "rel": "data/sse", "expected": "chanlun_actionable", "market": "sh",
        "runlog_rel": "news_collector/logs/target_queue_intel_daily.log",
        "evidence": "sse_collector.py:33 OUT_DIR=dirname(HERE)/data/sse",
    },
    {
        "id": "taoguba", "label": "淘股吧", "kind": "jsonl_dir",
        "rel": "data/taoguba", "expected": "chanlun_actionable",
        "runlog_rel": "news_collector/logs/target_queue_intel_daily.log",
        "evidence": "taoguba_collector.py:40 OUT_DIR=dirname(HERE)/data/taoguba",
    },
    {
        "id": "guba", "label": "东财股吧", "kind": "jsonl_dir",
        "rel": "quant_backtest/data/guba", "expected": "chanlun_actionable",
        "evidence": "quant_backtest/guba_crawl.py:43 OUT_DIR=HERE/data/guba（guba_pw.py 复用）",
    },
    {
        "id": "xq", "label": "雪球timeline", "kind": "jsonl_dir",
        "rel": "news_collector/data/xq_timeline", "expected": "registry_all",
        "deferred_rel": "news_collector/data/xq_timeline/_deferred.json",
        "evidence": "xq_stock_timeline.py:34 OUT_DIR=HERE/data/xq_timeline；覆盖分母=registry 并集",
    },
    {
        "id": "qianping", "label": "千股千评", "kind": "qianping",
        "rel": "news_collector/data/qianping", "expected": "chanlun_actionable",
        "evidence": "qianping_collector.py:30 OUT_DIR=HERE/data/qianping；latest.json 带 generated_at/n_queue",
    },
]

STATE_GREEN, STATE_AMBER, STATE_RED, STATE_NODATA = "GREEN", "AMBER", "RED", "NODATA"


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d"):
        try:
            return datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _codes6(items, field="code"):
    out = set()
    for it in items or []:
        v = str(it.get(field, "") or "").split(".")[0]
        if v:
            out.add(v.zfill(6))
    return out


def _load_chanlun(actionable=True):
    """缠论队列代码集。口径要点：irm/sse/taoguba/qianping/guba 采集器默认吃
    `actionable`（每日先验池），故覆盖分母取 actionable；`all` 作为副口径另报，
    避免用 465 只的全历史池冤枉当日只跑 65 只的源。"""
    path = os.path.join(HERE, "target_queue_chanlun.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return set(), "missing"
    key = "actionable" if actionable else "all"
    items = cfg.get(key) or cfg.get("all") or []
    return _codes6(items), "target_queue_chanlun.json:%s" % key


def _load_registry_all():
    """registry 并集。优先复用 load_queue 的源转码实现，失败则用本地等价实现。

    复制实现的存在理由：xq_stock_timeline 顶部 import xq_kol（连带 playwright），
    监控脚本不该为了读几个 json 拉起浏览器依赖。两条路径口径必须一致，
    已在 docstring 与本函数返回值 `how` 中标明实际走哪条。
    """
    reg_f = os.path.join(HERE, "selection_universe.json")
    if not os.path.exists(reg_f):
        return set(), "registry-missing"
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from xq_stock_timeline import _codes_from_source  # noqa: WPS433

        with open(reg_f, encoding="utf-8") as f:
            srcs = (json.load(f) or {}).get("sources", {})
        codes = set()
        for key, src in srcs.items():
            if src.get("xq_include"):
                codes |= _codes_from_source(src)
        return codes, "load_queue(_codes_from_source)"
    except Exception:
        pass
    # 本地等价实现（与 _codes_from_source 同判据：code_field → pool → scope/all）
    codes = set()
    try:
        with open(reg_f, encoding="utf-8") as f:
            srcs = (json.load(f) or {}).get("sources", {})
    except Exception:
        return set(), "registry-unreadable"
    for _key, src in srcs.items():
        if not src.get("xq_include"):
            continue
        f = src.get("file", "")
        path = f if os.path.isabs(f) else os.path.join(HERE, f)
        if not os.path.exists(path):
            alt = os.path.join(ROOT, f)
            path = alt if os.path.exists(alt) else path
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            continue
        cf = src.get("code_field")
        if cf:
            items = cfg.get(src.get("scope") or "basket") or cfg.get("basket") or []
            codes |= _codes6(items, cf)
        elif src.get("pool") and src["pool"] in cfg:
            codes |= _codes6(cfg[src["pool"]])
        else:
            items = cfg.get(src.get("scope")) or cfg.get("all") or []
            codes |= _codes6(items)
    return codes, "local-mirror"


def _weekend_slack(now, hours):
    """过去 3 天里每出现一个周末日 +hours（同 sched_heartbeat._line_days 判例）。"""
    extra = 0.0
    for back in range(1, 4):
        if (now - timedelta(days=back)).weekday() >= 5:
            extra += hours
    return extra


def _scan_jsonl_dir(path):
    """返回 (last_mtime, n_files, codes_present, n_empty, empty_sample)。"""
    if not os.path.isdir(path):
        return None, 0, set(), 0, []
    last = None
    n = 0
    codes = set()
    empty_codes = []
    try:
        names = os.listdir(path)
    except OSError:
        return None, 0, set(), 0, []
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        fp = os.path.join(path, name)
        try:
            st = os.stat(fp)
        except OSError:
            continue
        n += 1
        codes.add(name[:-6].split(".")[0].zfill(6))
        if st.st_size == 0:
            empty_codes.append(name[:-6].split(".")[0])
        mt = datetime.fromtimestamp(st.st_mtime)
        if last is None or mt > last:
            last = mt
    return last, n, codes, len(empty_codes), sorted(empty_codes)[:5]


def _scan_qianping(path):
    fp = os.path.join(path, "latest.json")
    if not os.path.exists(fp):
        return None, 0, None, None
    try:
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None, 0, None, None
    return _parse_dt(d.get("generated_at")), int(d.get("n_queue") or 0), d.get("trade_day"), None


def _scan_news(db_path, status_path):
    last = None
    total = 0
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
            try:
                row = con.execute("select count(*), max(ts) from news").fetchone()
                total = int(row[0] or 0)
                last = _parse_dt(row[1])
            finally:
                con.close()
        except Exception:
            pass
    sub_ok = sub_bad = None
    sub_fail = []
    if os.path.exists(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                st = json.load(f)
            subs = st.get("sources") or {}
            sub_bad, sub_ok, sub_fail = 0, 0, []
            for k, v in subs.items():
                if v.get("ok"):
                    sub_ok += 1
                elif v.get("skipped"):
                    pass  # 显式降级（有裁定依据），不计失败
                else:
                    sub_bad += 1
                    sub_fail.append(k)
        except Exception:
            pass
    return last, total, sub_ok, sub_bad, sub_fail


def _judge(now, last, thr, slack, consec):
    if last is None:
        return STATE_NODATA, None, "无落盘可判（目录/文件不存在）"
    stale = (now - last).total_seconds() / 3600.0
    if stale <= thr["amber_stale_hours"] + slack:
        state = STATE_GREEN
        note = "新鲜"
    elif stale <= thr["red_stale_hours"] + slack:
        state = STATE_AMBER
        note = "滞后超 AMBER 线（%.0fh），未到 RED 线" % thr["amber_stale_hours"]
    else:
        state = STATE_RED
        note = "滞后超 RED 线（%.0fh）" % thr["red_stale_hours"]
    if consec >= thr["red_consecutive"] and state != STATE_GREEN:
        state = STATE_RED
        note = "连续 %d 轮不达标，提级 RED（滞后 %.1fh）" % (consec, stale)
    return state, stale, note


def build_report(now=None, thresholds=None, state=None):
    thr = dict(DEFAULT_THRESHOLDS)
    thr_src = "DEFAULT"
    if thresholds:
        thr.update(thresholds)
        thr_src = "override"
    now = now or datetime.now()
    slack = _weekend_slack(now, thr["weekend_slack_hours"])
    chanlun_act, chanlun_act_src = _load_chanlun(True)
    chanlun_all, _chanlun_all_src = _load_chanlun(False)
    registry_all, registry_how = _load_registry_all()
    state = state or {}

    rows = []
    for spec in SOURCES:
        sid = spec["id"]
        path = os.path.join(ROOT, spec["rel"])
        row = {
            "source": sid, "label": spec["label"], "dir": spec["rel"],
            "evidence": spec["evidence"],
        }
        expect = set()
        expect_all = None
        if spec.get("expected") == "chanlun_actionable":
            expect = chanlun_act
            expect_all = chanlun_all
            extra = ""
            if spec.get("market") == "sh":
                # 上证 e 互动只覆盖沪市（60/68），拿 65 只全市场当分母是分母错误
                expect = {c for c in chanlun_act if c[:2] in ("60", "68")}
                expect_all = {c for c in chanlun_all if c[:2] in ("60", "68")}
                extra = "；本源仅沪市(60/68)"
            row["expected_from"] = "%s（采集器默认 actionable）%s；副口径 all=%d" % (
                chanlun_act_src, extra, len(expect_all))
        elif spec.get("expected") == "registry_all":
            expect = registry_all
            row["expected_from"] = "selection_universe.json xq_include=true 并集 (%s)" % registry_how
        else:
            row["expected_from"] = None

        covered = None
        covered_all = None
        kind = spec["kind"]
        if kind == "news":
            last, total, sub_ok, sub_bad, sub_fail = _scan_news(
                os.path.join(ROOT, spec["rel"]), os.path.join(ROOT, spec["status_rel"]))
            row.update({"last_ok_src": "news.db max(ts)", "n_files": total,
                        "n_rows": total, "sub_ok": sub_ok, "sub_fail_n": sub_bad,
                        "sub_fail": sub_fail})
        elif kind == "qianping":
            last, n_q, trade_day, _ = _scan_qianping(path)
            covered = n_q if expect else None
            row.update({"last_ok_src": "latest.json generated_at",
                        "covered": covered, "trade_day": trade_day,
                        "n_files": len([x for x in (os.listdir(path) if os.path.isdir(path) else [])
                                        if x.endswith(".jsonl")])})
        else:
            last, n_files, codes, n_empty, empties = _scan_jsonl_dir(path)
            covered = len(codes & expect) if expect else len(codes)
            covered_all = len(codes & expect_all) if expect_all else None
            row.update({"last_ok_src": "max(*.jsonl mtime)", "n_files": n_files,
                        "empty_files": n_empty, "empty_sample": empties})
            if spec.get("deferred_rel"):
                dp = os.path.join(ROOT, spec["deferred_rel"])
                n_def, max_att = 0, 0
                if os.path.exists(dp):
                    try:
                        with open(dp, encoding="utf-8") as f:
                            dj = json.load(f)
                        if isinstance(dj, dict):
                            n_def = len(dj)
                            vals = list(dj.values())
                            max_att = max([v for v in vals if isinstance(v, int)] or [0])
                    except Exception:
                        pass
                row.update({"deferred_n": n_def, "deferred_max_attempt": max_att})

        row["expected"] = len(expect) if expect else None
        row["expected_all"] = len(expect_all) if expect_all else None
        row["covered"] = covered
        row["covered_all"] = covered_all
        if expect and covered is not None:
            row["gap"] = max(0, len(expect) - covered)
            row["coverage_pct"] = round(100.0 * covered / len(expect), 1) if expect else None
        else:
            row["gap"] = None
            row["coverage_pct"] = None

        prev = (state.get(sid) or {})
        prev_state = prev.get("state")
        consec = int(prev.get("consecutive_bad") or 0)
        st, stale, note = _judge(now, last, thr, slack, consec)
        basis = "content"

        # 运行日志近似心跳：append-on-new 型采集器「0 新增」时内容 mtime 不动，
        # 只看内容会把「跑了但没新帖」误判成断供。共享日志无法区分源级时刻，
        # 故只用于「降级解释」——不能把 RED 洗成 GREEN，最多降到 AMBER 并要求人工确认。
        run_ts = None
        if spec.get("runlog_rel"):
            rp = os.path.join(ROOT, spec["runlog_rel"])
            if os.path.exists(rp):
                run_ts = datetime.fromtimestamp(os.stat(rp).st_mtime)
        if run_ts and last and run_ts > last and st in (STATE_RED, STATE_AMBER):
            run_stale = (now - run_ts).total_seconds() / 3600.0
            if run_stale <= thr["amber_stale_hours"] + slack:
                st = STATE_AMBER
                note = ("内容最后新增 %s（%.1fh 前），但共享运行日志在 %s（%.1fh 前）"
                        "——疑 0 新增/源端无新帖，需人工确认是否真断供"
                        % (last.strftime("%m-%d %H:%M"), stale,
                           run_ts.strftime("%m-%d %H:%M"), run_stale))
                basis = "runlog"
        row["last_run_ts"] = run_ts.strftime("%Y-%m-%d %H:%M") if run_ts else None
        row["state_basis"] = basis
        if st in (STATE_RED, STATE_AMBER, STATE_NODATA):
            consec += 1
        else:
            consec = 0
        row.update({
            "last_ok_ts": last.strftime("%Y-%m-%d %H:%M") if last else None,
            "stale_hours": None if stale is None else round(stale, 1),
            "state": st, "note": note,
            "prev_state": prev_state,
            "consecutive_bad": consec,
        })
        rows.append(row)

    n_red = sum(1 for r in rows if r["state"] == STATE_RED)
    n_amber = sum(1 for r in rows if r["state"] == STATE_AMBER)
    n_nodata = sum(1 for r in rows if r["state"] == STATE_NODATA)
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": thr,
        "thresholds_source": thr_src,
        "weekend_slack_hours": slack,
        "summary": {"n_red": n_red, "n_amber": n_amber, "n_nodata": n_nodata,
                    "n_sources": len(rows),
                    "verdict": "RED" if n_red else ("AMBER" if (n_amber or n_nodata) else "GREEN")},
        "rows": rows,
        "note": "只读快照；state=GREEN/AMBER/RED/NODATA；阈值为 DEFAULT 经验值，调参归 11-15 微调日",
    }


def render_md(rep):
    lines = [
        "# 采集新鲜度 SLA · %s" % rep["generated_at"],
        "",
        "判定：%s（RED=%d / AMBER=%d / NODATA=%d，共 %d 源）；阈值来源 `%s`，周末放宽 %.0fh。"
        % (rep["summary"]["verdict"], rep["summary"]["n_red"], rep["summary"]["n_amber"],
           rep["summary"]["n_nodata"], rep["summary"]["n_sources"],
           rep["thresholds_source"], rep["weekend_slack_hours"]),
        "",
        "| 源 | 判定 | 最后成功 | 滞后(h) | 覆盖/期望 | 缺口 | 连续不达标 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rep["rows"]:
        cov = "-" if r.get("expected") is None else "%s/%s" % (r.get("covered"), r["expected"])
        if r.get("covered_all") is not None:
            cov += "（all %s/%s）" % (r["covered_all"], r["expected_all"])
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["label"], r["state"], r.get("last_ok_ts") or "-",
            "-" if r.get("stale_hours") is None else r["stale_hours"],
            cov, "-" if r.get("gap") is None else r["gap"],
            r.get("consecutive_bad"), r.get("note")))
    lines += [
        "",
        "口径：最后成功 = 各源产物落盘时间/库内 max(ts)（非日志行）；覆盖分母见各行 `expected_from`。",
        "本表只给读数与判据，不含任何研判结论。",
    ]
    return "\n".join(lines) + "\n"


def render_table(rep):
    head = "%-12s %-6s %-16s %8s %10s %6s %5s  %s" % (
        "源", "判定", "最后成功", "滞后h", "覆盖/期望", "缺口", "连坏", "备注")
    out = [head, "-" * len(head)]
    for r in rep["rows"]:
        cov = "-" if r.get("expected") is None else "%s/%s" % (r.get("covered"), r["expected"])
        if r.get("covered_all") is not None:
            cov += "(all %s/%s)" % (r["covered_all"], r["expected_all"])
        out.append("%-12s %-6s %-16s %8s %10s %6s %5s  %s" % (
            r["label"], r["state"], r.get("last_ok_ts") or "-",
            "-" if r.get("stale_hours") is None else r["stale_hours"],
            cov, "-" if r.get("gap") is None else r["gap"],
            r.get("consecutive_bad"), r.get("note")))
    s = rep["summary"]
    out.append("")
    out.append("判定=%s  RED=%d AMBER=%d NODATA=%d  阈值来源=%s  周末放宽=%.0fh"
               % (s["verdict"], s["n_red"], s["n_amber"], s["n_nodata"],
                  rep["thresholds_source"], rep["weekend_slack_hours"]))
    return "\n".join(out)


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="采集新鲜度 SLA 视图（只读）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--md", default="", help="额外落 markdown 表到该路径")
    ap.add_argument("--no-state", action="store_true", help="不读不写 state（纯快照，无跨轮记忆）")
    ap.add_argument("--no-write", action="store_true", help="不落 collect_sla.json")
    ap.add_argument("--thresholds", default="", help="阈值覆盖 JSON 文件")
    ap.add_argument("--now", default="", help="注入当前时间（ISO），用于回放/测试")
    args = ap.parse_args(argv)

    now = None
    if args.now:
        now = _parse_dt(args.now)
        if now is None:
            print("ERROR 无法解析 --now: %s" % args.now, file=sys.stderr)
            return 2

    thresholds = None
    if args.thresholds:
        try:
            with open(args.thresholds, encoding="utf-8") as f:
                thresholds = json.load(f)
        except Exception as e:
            print("ERROR 读取阈值文件失败: %s" % e, file=sys.stderr)
            return 2

    state = {}
    if not args.no_state and os.path.exists(STATE_F):
        try:
            with open(STATE_F, encoding="utf-8") as f:
                state = json.load(f).get("sources", {})
        except Exception:
            state = {}

    rep = build_report(now=now, thresholds=thresholds, state=state)

    if not args.no_write:
        _atomic_write_json(OUT_F, rep)
    if not args.no_state:
        _atomic_write_json(STATE_F, {
            "updated": rep["generated_at"],
            "sources": {r["source"]: {"state": r["state"],
                                      "consecutive_bad": r["consecutive_bad"],
                                      "last_ok_ts": r.get("last_ok_ts")}
                        for r in rep["rows"]},
        })
    if args.md:
        mp = args.md if os.path.isabs(args.md) else os.path.join(os.getcwd(), args.md)
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(mp, "w", encoding="utf-8") as f:
            f.write(render_md(rep))

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(render_table(rep))
    return 0 if rep["summary"]["n_red"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
