# -*- coding: utf-8 -*-
"""api_common 四组拆分：alert 组（2026-10-31 WO-COMMON-SPLIT-20261031）。

自 api_common.py 纯移动而来，函数体/常量零改动；对外仍可从
api_common 导入（兼容 re-export）。
"""
from __future__ import annotations

import datetime
import glob
import json
import logging
import os
import re
import sys
import time

from api_common import (
    QBT_DIR,
    REPORTS_DIR,
    STATION,
    _council_stats,
    _read_json,
    log_exc,
)


def _alert_business_day(value):
    """Parse an explicit business date, never an observation or file timestamp."""
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{8}", value):
            day = datetime.datetime.strptime(value, "%Y%m%d").date()
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = datetime.date.fromisoformat(value)
        else:
            return None
    except ValueError:
        return None
    return day

def _alert_prev_weekday(day):
    """日历不可用时的 fail-open 兜底：退到最近的周一至周五。"""
    probe = day
    while probe.weekday() >= 5:
        probe -= datetime.timedelta(days=1)
    return probe

def _alert_acceptable_days(today):
    """当期可接受业务日期集合 = {今天, 上一交易日}（2026-10-26 EXEC-R36）。

    与 news_collector/alert_push.py 的 _acceptable_source_days 同一口径：
    周末/节后开盘前 EOD 源天然只有上一交易日批次，不能按"business_date 必须
    等于今天"误杀。trading_cal 只读 portal/data/trade_cal_open_days.json（进程内
    缓存）；缺失/损坏 → fail-open 退回 weekday 口径，绝不因日历依赖弄死告警链。
    """
    days = {today}
    prev = today - datetime.timedelta(days=1)
    try:
        news_dir = os.path.join(STATION, "news_collector")
        if news_dir not in sys.path:
            sys.path.insert(0, news_dir)
        import trading_cal  # noqa: PLC0415
        days.add(trading_cal.last_trading_day(prev))
    except Exception:  # noqa: BLE001
        days.add(_alert_prev_weekday(prev))
    return days

# 警报源新鲜窗口：源的完成时间戳距今多久内算"当期"。
# 2026-10-26 OS-FIX-MEASURE 统一测量：默认 5 → **1080 分钟（18 小时）**。
#   原 5 分钟与"每日一次 EOD 源"存在结构性冲突：AlertWatch 每 5 分钟跑一次，
#   而 premarket/chanlun/nine_dim 等源每天只产出一批，批次落盘 5 分钟后就"过期"，
#   判据永远不成立 → 按设计长期 rc=3 拒跑（实测上游不可信 55.1 小时、6 源全 stale）。
#   18h 的口径是"当日 EOD 批次即当期"：覆盖最早 06:50（nine_dim）到收盘后 EOD
#   全量的真实节奏；跨日仍由下面的 day/anchor.date() != today 判 stale，
#   不会把昨天的批次当成今天，也不会把"非交易日旧批次"冒充当期。
# 环境变量 ALERT_FRESH_WINDOW_MIN 保留覆盖（运维可临时收紧/放宽）。
# 必须与 news_collector/alert_push.py 的 ALERT_FRESH_WINDOW 用同一个环境变量：
# 该文件本轮有并行 WIP 未同步默认值——消费端对 checked_at 只要求新鲜（响应时刻）、
# 对源只要求 as_of 当日，因此服务端放宽到 18h 与消费端口径兼容（服务端已先按
# 跨日剔除，放行到消费端的 as_of 必然落在当日）。
# 2026-10-26 EXEC-R36：本窗口现在只用于「同日批次新鲜度 + as_of 超前容差」；
# 跨日批次是否当期改由 business_date ∈ {今天, 上一交易日}（交易日历口径）判定，
# 与 news_collector/alert_push.py 对齐，避免两端漂移。
ALERT_FRESH_WINDOW = datetime.timedelta(
    minutes=float(os.environ.get("ALERT_FRESH_WINDOW_MIN", "1080")))

# 2026-10-27 OS-FIX-WEAKFRESH：weak（弱于板块）是**周频源**（周一 15:35），
# 日级 acceptable_days={今天,上一交易日} 会让它周二至周日恒 stale（实测 10-27
# business_date=10-23 被判 stale）。周频源单独放宽到 8 天：business_date/anchor
# 在 8 天内视为当期，>8 天仍判真断更。
WEEKLY_SOURCE_MAX_AGE = datetime.timedelta(days=8)

def _read_weak_scan_marker():
    """weak_scan 整轮完成标记（reports/weak_scan_last.json）→ dict|None。

    2026-10-26 OS-FIX-MEASURE2：weak 源原先没有"整轮扫描完成"证据——CSV/
    jsonl 的 mtime 只能证明"有东西写过"，零命中扫描更是一点痕迹都没有，
    于是 dated() 永远 unproven。weak_scan.py 在成功出口（含零命中）原子写
    completed_at/trade_date/source/case_count；本函数只读该 marker。
    缺失/坏/非 dict → None（不判错，由调用方维持 unproven）。
    """
    path = os.path.join(STATION, "reports", "weak_scan_last.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None

def _alert_source_health():
    """Report independently verified source evidence, not display-card counts.

    Business dates validate a period but do not themselves establish as_of.
    Only a producer-provided, timezone-aware completion timestamp corroborated
    by the business date can be trusted. Filesystem mtime and aggregator clocks
    are never substitutes for producer evidence.

    business_date carries the source's real business day (YYYY-MM-DD, or None
    when unproven); as_of stays the producer completion time. They are shown
    separately so a fresh completion timestamp cannot disguise an old business
    day in cross/council style sources.
    """
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today = now.date()
    acceptable_days = _alert_acceptable_days(today)
    sources = {name: {"status": "error", "as_of": None, "business_date": None,
                      "reason": "missing"} for name in
               ("premarket", "chanlun", "nine_dim", "weak", "cross",
                "council")}

    def mark(name, status, reason, anchor=None, business_date=None):
        sources[name] = {"status": status,
                         "as_of": anchor.astimezone(now.tzinfo).isoformat()
                         if anchor is not None else None,
                         "business_date": business_date,
                         "reason": reason}

    def producer_time(value):
        """Require a source-supplied ISO datetime with an explicit UTC offset."""
        if isinstance(value, datetime.datetime):
            return value if value.utcoffset() is not None else None
        if (not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})", value)):
            return None
        try:
            parsed = datetime.datetime.fromisoformat(value)
            return parsed if parsed.utcoffset() is not None else None
        except ValueError:
            return None

    def dated(name, raw_day, raw_time=None, empty=False, weekly=False):
        day = _alert_business_day(raw_day)
        anchor = producer_time(raw_time)
        biz = day.isoformat() if day is not None else None
        anchor_day = (anchor.astimezone(now.tzinfo).date()
                      if anchor is not None else None)
        if day is None:
            mark(name, "error", "invalid_schema")
        elif day > today:
            mark(name, "error", "future_anchor", business_date=biz)
        elif anchor is None:
            mark(name, "stale", "unproven_completion", business_date=biz)
        elif anchor_day < day:
            mark(name, "error", "invalid_anchor", business_date=biz)
        elif anchor_day > today or anchor > now + ALERT_FRESH_WINDOW:
            mark(name, "error", "future_anchor", business_date=biz)
        elif weekly:
            # 周频源：business_date 与完成时间都在 8 天内即当期；>8 天真断更。
            if ((today - day).days > 8
                    or now - anchor > WEEKLY_SOURCE_MAX_AGE):
                mark(name, "stale", "stale_anchor", anchor, business_date=biz)
            else:
                mark(name, "empty" if empty else "ok", None, anchor,
                     business_date=biz)
        elif day not in acceptable_days:
            # 2026-10-26 EXEC-R36：business_date 只允许「今天 ∪ 上一交易日」；
            # 更早 = 真断更仍拒收（周末/节后开盘前的上一交易日 EOD 批次属正常）。
            mark(name, "stale", "stale_anchor", anchor, business_date=biz)
        elif day == today and now - anchor > ALERT_FRESH_WINDOW:
            # 同日批次仍守 18h 新鲜窗（"当日旧批次不当期"）；跨日批次由
            # business_date 在当期集合内放行，不再按 age 误杀上一交易日 EOD。
            mark(name, "stale", "stale_anchor", anchor, business_date=biz)
        else:
            mark(name, "empty" if empty else "ok", None, anchor,
                 business_date=biz)

    def read(name, path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            mark(name, "error", "missing")
            return None
        except (OSError, UnicodeError, ValueError):
            mark(name, "error", "invalid_json")
            return None
        if not isinstance(data, dict):
            mark(name, "error", "invalid_schema")
            return None
        return data

    pre = read("premarket", os.path.join(REPORTS_DIR,
                                          "premarket_latest.json"))
    if pre is not None:
        verdict = pre.get("verdict")
        if (not isinstance(verdict, str) or not verdict.strip() or
                verdict.strip() in ("—", "-", "暂无", "未知")):
            mark("premarket", "error", "invalid_schema")
        elif pre.get("blocked") or pre.get("status") == "blocked":
            mark("premarket", "error", "upstream_blocked")
        elif "trade_date" in pre:
            dated("premarket", pre["trade_date"], pre.get("ts"))
        else:
            mark("premarket", "stale", "unproven_completion")

    pool_path = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
    try:
        with open(pool_path, encoding="utf-8") as handle:
            pool = [json.loads(line) for line in handle if line.strip()]
        if any(not isinstance(row, dict) or
               _alert_business_day(row.get("detect_date")) is None
               for row in pool):
            mark("chanlun", "error", "invalid_schema")
        elif not pool:
            mark("chanlun", "stale", "unproven_completion")
        else:
            last_day = max(row["detect_date"] for row in pool)
            latest = [row for row in pool if row["detect_date"] == last_day]
            anchors = [producer_time(row.get("completed_at")) for row in latest]
            # Signal dates alone cannot establish that this scan finished.
            dated("chanlun", last_day,
                  max(anchors) if anchors and all(anchors) else None)
    except FileNotFoundError:
        mark("chanlun", "error", "missing")
    except (OSError, UnicodeError, ValueError):
        mark("chanlun", "error", "invalid_json")

    nine = read("nine_dim", os.path.join(QBT_DIR, "data",
                                          "nine_dim_scores.json"))
    if nine is not None:
        generated = nine.get("generated_at")
        n_alert = nine.get("n_alert")
        if (not isinstance(n_alert, int) or isinstance(n_alert, bool) or
                n_alert < 0 or not _alert_business_day(nine.get("trade_date"))):
            mark("nine_dim", "error", "invalid_schema")
        elif nine.get("blocked") or nine.get("status") == "blocked":
            mark("nine_dim", "error", "upstream_blocked")
        else:
            dated("nine_dim", nine["trade_date"], generated,
                  empty=n_alert == 0)

    # 2026-10-26 EXEC-R36：修正顺序 bug——**先读新 marker**（weak_scan.py 成功
    # 出口原子写的整轮完成证据），再回退旧 jsonl 流。原实现先 glob
    # weak_track_*.jsonl，为空直接 stale 并 return，永远走不到 marker 读取；
    # 即使周一 15:35 WeakScan 写了新 marker 也仍判 unproven。
    marker = _read_weak_scan_marker()
    print("[alert_health] weak source: marker=%s"
          % ("hit" if marker else "miss"))
    if marker and marker.get("trade_date") and marker.get("completed_at"):
        # 2026-10-27 OS-FIX-WEAKFRESH：weak 周频（周一生产），按 weekly 口径判定。
        dated("weak", marker.get("trade_date"), marker.get("completed_at"),
              empty=marker.get("case_count") == 0, weekly=True)
    else:
        weak_paths = [path for path in glob.glob(os.path.join(
            STATION, "reports", "weak_track_*.jsonl"))
                      if ".closed_" not in os.path.basename(path)]
        if not weak_paths:
            mark("weak", "stale", "unproven_completion")
        else:
            try:
                for path in weak_paths:
                    with open(path, encoding="utf-8") as handle:
                        rows = [json.loads(line) for line in handle
                                if line.strip()]
                    if not rows or not all(isinstance(row, dict) and row.get("ts")
                                           for row in rows):
                        raise TypeError("invalid weak status stream")
                # 旧案例流只能证明"案例写过"，不能证明"整轮扫描完成"（零命中
                # 扫描无案例）；无 marker 时维持 unproven，等 weak_scan 写 marker。
                mark("weak", "stale", "unproven_completion")
            except (OSError, UnicodeError, ValueError, TypeError):
                mark("weak", "error", "invalid_json")

    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import cross_poll
        cross = cross_poll.build_cross()
        if not isinstance(cross, dict) or not isinstance(cross.get("stocks"), list):
            mark("cross", "error", "invalid_schema")
        elif cross.get("blocked") or (cross.get("k_gate") or {}).get("mode") == "blocked":
            mark("cross", "error", "upstream_blocked")
        elif (cross.get("degraded") or
              (cross.get("k_gate") or {}).get("mode") == "degraded_k3" or
              any(not isinstance(state, dict) or state.get("status") != "ok"
                  for state in (cross.get("src_state") or {}).values())):
            mark("cross", "error", "upstream_degraded")
        else:
            # 2026-10-26 OS-FIX-MEASURE2：cross 生产者时间戳 = 聚合完成时间
            # completed_at（+08:00 ISO）+ trade_date（各源 anchor 最大日）。
            # 两者齐备才能证明"当期跑完"；缺一维持 unproven（不拿 generated_at
            # 这个聚合器墙钟冒充交易日）。
            if cross.get("trade_date") and cross.get("completed_at"):
                dated("cross", cross.get("trade_date"),
                      cross.get("completed_at"))
            else:
                mark("cross", "stale", "unproven_completion")
    except Exception:
        mark("cross", "error", "source_error")

    # Council is optional and is not one of the five mandatory signal feeds.
    try:
        council = _council_stats()
        if not isinstance(council, dict):
            mark("council", "error", "invalid_schema")
        elif council.get("d"):
            dated("council", council["d"], council.get("ts"))
        else:
            mark("council", "stale", "unproven_completion")
    except Exception:
        mark("council", "error", "source_error")
    return {"version": 1, "checked_at": now.isoformat(timespec="seconds"),
            "sources": sources}

def build_alerts():
    """量化域首页「今日预警速览」数据源：盘前/缠论/九维/weak/共振。
    全部读产线落盘快照（与各专项页同源同口径），单项失败即缺项不拖垮整体。
    弱于板块口径=页面口径（排除 .closed_ 且末行可解析），非注册表口径。
    E8 分级：每条 alert 附 level ∈ {"market","eng","strategy"}（缺省 "eng"），
    盘前倾向/大盘裁决类=market，策略引擎/信号类=eng，8001/quantapi 类=strategy。"""
    out = []
    # ① 盘前倾向（premarket_latest.json：verdict + 六度分值 + 溢价概率）
    pre = _read_json(os.path.join(REPORTS_DIR, "premarket_latest.json"))
    pre = pre if isinstance(pre, dict) else {}
    if pre.get("verdict"):
        vals = pre.get("vals") if isinstance(pre.get("vals"), dict) else {}
        l6 = pre.get("l6") if isinstance(pre.get("l6"), dict) else {}
        an6 = l6.get("ann_stats") if isinstance(l6.get("ann_stats"), dict) else {}
        rp6 = l6.get("rpt_stats") if isinstance(l6.get("rpt_stats"), dict) else {}
        six = [{"k": k, "v": vals.get(k)} for k in
               ("外围", "政策", "流动性", "板块", "情绪")]
        six.append({"k": "公告研报", "v": None,
                    "n": (an6.get("total") or 0) + (rp6.get("total") or 0)})
        out.append({"name": "盘前倾向",
                    "value": str(pre["verdict"]).split("（")[0],
                    "sub": "开盘溢价概率 %s%%" % pre.get("prob", "—"),
                    "href": "/premarket", "delta": None,
                    "six": six, "overall": pre.get("overall"),
                    "level": "market"})
    # ② 缠论预警池（chanlun_pool.jsonl：最新检出日 买/卖 计数 + 较前日差）
    try:
        rows = []
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_common.py", exc)
        days = sorted({r.get("detect_date", "") for r in rows
                       if isinstance(r, dict) and isinstance(r.get("detect_date"), str)
                       and r.get("detect_date")})
        if days:
            def _nb(dd):
                return sum(1 for r in rows if isinstance(r, dict) and
                           r.get("detect_date") == dd and
                           str(r.get("type", "")).endswith("买"))
            last = days[-1]
            ns = sum(1 for r in rows if isinstance(r, dict) and
                     r.get("detect_date") == last and
                     str(r.get("type", "")).endswith("卖"))
            out.append({"name": "缠论信号",
                        "value": "买 %d · 卖 %d" % (_nb(last), ns),
                        "sub": "检出日 %s" % last,
                        "href": "/bt-chanlun-pool",
                        "delta": (_nb(last) - _nb(days[-2]))
                        if len(days) > 1 else None,
                        "level": "eng"})
    except Exception:
        log_exc("build_alerts:chanlun_pool.jsonl")  # C2批1
    # ③ 九维警报池（nine_dim_scores.json：n_alert 阈值入池数）
    snap = _read_json(os.path.join(QBT_DIR, "data", "nine_dim_scores.json"))
    if isinstance(snap, dict) and snap:
        out.append({"name": "九维警报池",
                    "value": "%s 只" % snap.get("n_alert",
                                                snap.get("n_buy", 0)),
                    "sub": "综合分 ≥%s 入池 · %s" % (
                        snap.get("alert_th", 75),
                        snap.get("trade_date", "—")),
                    "href": "/bt-9dim", "delta": None,
                    "level": "eng"})
    # ④ 弱于板块在案（weak_track_*.jsonl 排除结案流）
    try:
        n_act = sum(
            1 for fp in glob.glob(os.path.join(
                STATION, "reports", "weak_track_*.jsonl"))
            if ".closed_" not in os.path.basename(fp)
            and _alerts_last_jsonl(fp) is not None)
        out.append({"name": "弱于板块在案", "value": "%d 案" % n_act,
                    "sub": "扫描→建案→跟踪→复评→结案",
                    "href": "/bt-weak", "delta": None,
                    "level": "eng"})
    except Exception:
        log_exc("build_alerts:weak_track 在案统计")  # C2批1
    # ⑤ 共振做多（cross_poll.build_cross 内置 60s 缓存；口径=共振页 longs）
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import cross_poll
        cross_display = cross_poll.build_cross()
        stocks = cross_display.get("stocks", []) if isinstance(cross_display, dict) else []
        stocks = stocks if isinstance(stocks, list) else []
        n_long = sum(1 for s in stocks if isinstance(s, dict) and
                     s.get("n_hit", 0) >= 2
                     and s.get("state") == "long"
                     and s.get("n_long", 0) >= 2)
        out.append({"name": "共振做多", "value": "%d 只" % n_long,
                    "sub": "多策略信号共振 · 买入级",
                    "href": "/bt-cross", "delta": None,
                    "level": "eng"})
    except Exception:
        log_exc("build_alerts:cross_poll 共振")  # C2批1
    # ⑥ 投委会（K61：最近跑批夜三会计数 + 影子转正进度，态势带卡；
    #    K62：管理预警计数 + 风格分桶分布上屏；K64d：待确认队列计数）
    try:
        cs = _council_stats()
        if cs["d"]:
            warn = (" · ⚠预警 %d" % cs["n_warn"]) if cs.get("n_warn") \
                else ""
            pend = (" · 待确认 %d" % cs["n_pend"]) if cs.get("n_pend") \
                else ""
            sty = cs.get("styles") or {}
            sty_s = ""
            if sty:
                sty_s = " · 桶 %s/%s" % (sty.get("激进", 0),
                                         sty.get("稳健", 0))
            out.append({"name": "🏛 投委会",
                        "value": "立案 %d" % cs["n_prop"],
                        "sub": "盯盘需注意 %d · 复盘 %d · 影子 %d/%d%s%s%s" % (
                            cs["n_watch"], cs["n_rev"],
                            cs["shadow_n"], cs["shadow_need"],
                            warn, pend, sty_s),
                        "href": "/council", "delta": None,
                        "level": "eng"})
    except Exception:
        log_exc("build_alerts:council_stats")  # C2批1
    return {"ts": time.strftime("%Y-%m-%d %H:%M"), "alerts": out,
            "source_health": _alert_source_health()}

# ── K39 今日预警速览：各策略产线最新预警值聚合 ──────────────
def _alerts_last_jsonl(fp):
    """JSONL 末行解析（weak 在案口径：末行可解析才计入）。"""
    try:
        last = ""
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                last = ln
        last = last.strip()
        return json.loads(last) if last else None
    except Exception:
        # 豁免（C2批1）：口径即「末行可解析才计入」（weak 在案口径注释）
        return None
