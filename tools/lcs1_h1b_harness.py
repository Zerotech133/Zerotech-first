# -*- coding: utf-8 -*-
"""LCS-1 H1b 回测骨架（预注册 v1，2026-11-01；**不跑真实回测**）。

预注册真源：``deliverables/2026-11-01_LCS1_H1b_预注册_v1.md``。
锁定口径（不得在本文件内搜索/替换参数）：
  W=5（事件后回避 t+1~t+5）→ t+6 开盘入场（不可交易顺延 t+7/t+8/t+9，最多 4 候选）
  → 持有 H=5 个交易日（入场日算第 1 日，第 5 日收盘离场，无 bar/跌停顺延 ≤3 日）
  → 成本 10bps 往返（15bps 压力仅稳健性）；raw_net = close_exit/open_entry - 1 - 成本。
  硬闸：t+1 可交易（下一交易日、有 bar、open>0/vol>0、open<涨停价、非 ST、
  20 日均成交额 ≥3000 万、上市 ≥250 交易日）；不满足即剔除，不宽进。
  事件：E1 涨停（板别涨停价：主板 10%/创业板·科创 20%/北交所 30%/ST 主板 5%）、
  E2 龙虎榜净买入>0（top_list 按 code-date 聚合）；样本 2023-09-04~2026-10-16；
  同股同事件间隔 <20 交易日合并为 1 独立事件（保留首事件）。
  判定：OOS 2025-07-01~2026-10-16 唯一判定段；8 门 M1-M4 + R1-R4
  （见 §七评分卡；GO 需 M1-M4 全过、R1-R4 ≥3、总分 ≥7/8）。

本文件只提供**可复现骨架**：纯函数 + 数据接口（load_events/load_panel/
check_freeze）。纪律：
- **数据未冻结拒绝真实回测**：DATA1 四字段回执（data_version/sample_in/
  sample_out/cost_model）+ 7 数据集 sha256 + 冻结检查缺失时，直接拒绝并列出
  缺字段；**绝不跑真数据、不编结果**；
- 不写任何生产账、不自动交易、不构成投资建议；输出带 ``result_claim=False``；
- 真实回测在 DATA1 冻结后按下方步骤执行，产物由回执另出。

冻结后运行步骤：
  1) DATA1 出冻结回执 JSON（见 ``FREEZE_DATASETS``/``FREEZE_CHECKS``）::

       {"data_version": "lcs1-h1b-2026XXXX", "frozen_at": "2026-11-XX",
        "sample_in": "2023-09-04", "sample_out": "2026-10-16",
        "cost_model": {"round_trip_bps": 10, "stress_bps": 15,
                       "includes_borrow": false},
        "datasets": {"ts_daily": {"sha256": "..."}, ...7 项...},
        "checks": {"sw_missing_rate_reported": true, "list_date_complete": true,
                   "unadjusted_prices": true, "top_list_gap_declared": true}}

  2) 预注册参数 + 冻结回执校验（不跑数据；缺字段照实列出）::

       python -X utf8 tools/lcs1_h1b_harness.py --dry-run --manifest freeze.json --json

  3) 冻结通过后跑骨架回测（研究用；本步只在冻结后执行）::

       python -X utf8 tools/lcs1_h1b_harness.py --events events.jsonl \
           --panel panel.json --manifest freeze.json --json

数据接口：
  events JSONL：{"date":"2024-01-02","ts_code":"600000","event_type":"E1"}
  或 E2 {"date":...,"ts_code":...,"event_type":"E2","net_amount":1234567.0}
  panel JSON：{"calendar":["2024-01-02",...],
               "codes":{"600000":{"list_date":"2020-01-02",
                                  "bars":[{"date","open","high","low","close",
                                           "volume","amount","is_st"?}, ...]}}}
  （bars 按日期升序、必须是交易日历子集；amount=当日成交额，单位元。）

退出码：0=成功（dry-run/冻结校验/骨架回测完成）/ 1=数据未冻结拒绝 /
2=参数或输入错误。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---------------------------------------------------------------------------
# 预注册常量（锁定；代码内不得搜索/替换 W/H 或改口径）
# ---------------------------------------------------------------------------
SPEC = {
    "prereg_doc": "deliverables/2026-11-01_LCS1_H1b_预注册_v1.md",
    "W": 5,                       # 回避窗口：t+1~t+5
    "H": 5,                       # 持有交易日（入场日算第 1 日）
    "entry_offset": 6,            # t+6 开盘入场（= W + 1）
    "entry_fallbacks": 3,         # t+7/t+8/t+9（最多 4 个候选）
    "exit_fallbacks": 3,          # 离场无 bar/跌停顺延 ≤3 日
    "cost_bps": 10,               # 主口径往返成本
    "stress_cost_bps": 15,        # 稳健性压力口径
    "event_types": ("E1", "E2"),
    "sample_in": "2023-09-04",
    "sample_out": "2026-10-16",
    "is_end": "2025-06-30",
    "oos_start": "2025-07-01",
    "cluster_gap_days": 20,       # 同股事件间隔 <20 交易日合并
    "min_independent": 30,        # M1
    "liquidity_20d_cny": 30_000_000,
    "min_listed_days": 250,
    "gates": ("M1", "M2", "M3", "M4", "R1", "R2", "R3", "R4"),
    "go_total_score": 7,
    "robust_min_pass": 3,
    "holm_alpha": 0.05,
    "bootstrap": 1000,
}
BOARD_LIMITS = {"main": 0.10, "chinext": 0.20, "star": 0.20, "bj": 0.30}
ST_MAIN_LIMIT = 0.05

FREEZE_DATASETS = ("ts_daily", "top_list", "ts_daily_basic", "ts_namechange",
                   "ts_stock_basic", "sw_classify_l1", "sw_l1_member")
FREEZE_CHECKS = ("sw_missing_rate_reported", "list_date_complete",
                 "unadjusted_prices", "top_list_gap_declared")
NOTE = ("骨架工具：不跑真实数据/不自动交易/不宣称结果/不构成投资建议；"
        "任何结论须冻结后按预注册另出回执并经 ACAD1 复核 + 老田签。")


# ---------------------------------------------------------------------------
# 基础口径
# ---------------------------------------------------------------------------
def round_cent(value):
    """四舍五入到分（A 股涨跌停价口径）。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"),
                                              rounding=ROUND_HALF_UP))


def board_of(code):
    """代码 → 板块：main / chinext / star / bj。"""
    code = str(code or "")
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("4", "8", "920")):
        return "bj"
    return "main"


def limit_rate(code, is_st=False):
    """涨跌停比例：主板 10% / 创业板·科创 20% / 北交所 30% / ST 主板 5%。"""
    board = board_of(code)
    if is_st and board == "main":
        return ST_MAIN_LIMIT
    return BOARD_LIMITS[board]


def limit_up_price(prev_close, code, is_st=False):
    rate = limit_rate(code, is_st=is_st)
    return round_cent(float(prev_close) * (1.0 + rate))


def limit_down_price(prev_close, code, is_st=False):
    rate = limit_rate(code, is_st=is_st)
    return round_cent(float(prev_close) * (1.0 - rate))


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _bar_is_st(bar):
    return bool((bar or {}).get("is_st"))


def _calendar_index_map(calendar):
    return {str(day): i for i, day in enumerate(calendar or [])}


def validate_spec(spec=None):
    """预注册参数自检：返回问题列表（空=一致）。"""
    spec = spec or SPEC
    issues = []
    if spec.get("W", 0) <= 0 or spec.get("H", 0) <= 0:
        issues.append("W/H 必须为正")
    if spec.get("entry_offset") != spec.get("W", 0) + 1:
        issues.append("entry_offset 必须 = W+1（t+W+1 = t+6）")
    if spec.get("entry_offset") + spec.get("entry_fallbacks", 0) < spec.get("W", 0) + 1:
        issues.append("入场候选必须从 t+W+1 起")
    if not (0 < spec.get("cost_bps", 0) < spec.get("stress_cost_bps", 0)):
        issues.append("成本必须 0<主口径<压力口径")
    if spec.get("sample_in", "") > spec.get("is_end", "") or \
            spec.get("oos_start", "") <= spec.get("is_end", ""):
        issues.append("样本窗口/IS/OOS 边界不一致")
    if spec.get("oos_start", "") >= spec.get("sample_out", ""):
        issues.append("OOS 起点必须早于样本终点")
    if len(spec.get("gates", ())) != 8:
        issues.append("评分卡必须 8 门（M1-M4 + R1-R4）")
    if spec.get("go_total_score", 0) > 8 or spec.get("robust_min_pass", 0) > 4:
        issues.append("GO 阈值越界（总分≤8、稳健门≤4）")
    return issues


# ---------------------------------------------------------------------------
# 数据接口（只读；数据未冻结不调用）
# ---------------------------------------------------------------------------
def load_events(path):
    """读事件 JSONL/JSON 列表；返回规范化事件列表（按日期排序）。"""
    path = Path(path)
    raw = []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise ValueError("events 行非 JSON（%s）：%s" % (path, exc))
        raw.extend(value if isinstance(value, list) else [value])
    events = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError("events[%d] 非对象" % idx)
        date = str(row.get("date") or row.get("trade_date") or "").strip()
        code = str(row.get("ts_code") or row.get("symbol") or "").strip()
        etype = str(row.get("event_type") or "").strip().upper()
        if not date or not code or etype not in SPEC["event_types"]:
            raise ValueError("events[%d] 缺 date/ts_code/event_type(∈E1,E2)" % idx)
        events.append({"date": date, "ts_code": code, "event_type": etype,
                       "net_amount": _num(row.get("net_amount"))})
    events.sort(key=lambda x: (x["date"], x["event_type"], x["ts_code"]))
    return events


def load_panel(path):
    """读 panel JSON：{calendar?, codes:{code:{list_date?, bars:[...]}}}。

    bars 必须含 date/open/close；high/low/volume/amount 建议提供（硬闸/成本用）。
    返回 {"calendar": [...], "codes": {code: {"list_date", "bars"}}}。
    """
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or not isinstance(doc.get("codes"), dict):
        raise ValueError("panel 需 {codes:{code:{bars:[...]}}}")
    calendar = [str(x) for x in (doc.get("calendar") or [])]
    codes = {}
    for code, row in doc["codes"].items():
        if not isinstance(row, dict) or not isinstance(row.get("bars"), list):
            raise ValueError("panel.codes.%s 需 {bars:[...]}" % code)
        bars = []
        for bar in row["bars"]:
            if not isinstance(bar, dict) or not str(bar.get("date") or ""):
                raise ValueError("panel.codes.%s bars 需含 date 的对象" % code)
            item = dict(bar)
            item["date"] = str(item["date"])
            for key in ("open", "close", "high", "low", "volume", "amount"):
                item[key] = _num(item.get(key))
            item["is_st"] = bool(item.get("is_st"))
            bars.append(item)
        bars.sort(key=lambda b: b["date"])
        codes[str(code)] = {"list_date": (str(row["list_date"])
                                          if row.get("list_date") else None),
                            "bars": bars}
    if not calendar:
        calendar = sorted({bar["date"] for row in codes.values()
                           for bar in row["bars"]})
    return {"calendar": calendar, "codes": codes}


def load_manifest(path):
    """读 DATA1 冻结回执 JSON（缺失/坏 JSON → None）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def check_freeze(manifest, spec=None):
    """校验 DATA1 冻结回执（四字段 + 7 数据集 sha256 + 4 项检查）。

    返回 {frozen, missing_fields, problems, manifest}；缺字段精确到路径，
    供"数据未冻结拒绝真实回测"逐项报告。
    """
    spec = spec or SPEC
    result = {"frozen": False, "missing_fields": [], "problems": [],
              "manifest": manifest if isinstance(manifest, dict) else None}
    if not isinstance(manifest, dict):
        result["missing_fields"].append("<manifest>（未提供或非 JSON 对象）")
        return result
    missing, problems = result["missing_fields"], result["problems"]
    for key in ("data_version", "frozen_at", "sample_in", "sample_out"):
        if not str(manifest.get(key) or "").strip():
            missing.append(key)
    if str(manifest.get("sample_in") or "") != spec["sample_in"]:
        problems.append("sample_in 不一致：%r ≠ 预注册 %r"
                        % (manifest.get("sample_in"), spec["sample_in"]))
    if str(manifest.get("sample_out") or "") != spec["sample_out"]:
        problems.append("sample_out 不一致：%r ≠ 预注册 %r"
                        % (manifest.get("sample_out"), spec["sample_out"]))
    cost = manifest.get("cost_model")
    if not isinstance(cost, dict):
        missing.append("cost_model")
    else:
        if cost.get("round_trip_bps") != spec["cost_bps"]:
            problems.append("cost_model.round_trip_bps 必须=%s"
                            % spec["cost_bps"])
        if cost.get("stress_bps") != spec["stress_cost_bps"]:
            problems.append("cost_model.stress_bps 必须=%s"
                            % spec["stress_cost_bps"])
        if cost.get("includes_borrow") is not False:
            problems.append("不做空口径：cost_model.includes_borrow 必须 false")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, dict):
        missing.append("datasets")
    else:
        for name in FREEZE_DATASETS:
            info = datasets.get(name)
            if not isinstance(info, dict):
                missing.append("datasets.%s" % name)
                continue
            sha = str(info.get("sha256") or "").strip().lower()
            if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
                missing.append("datasets.%s.sha256" % name)
    checks = manifest.get("checks")
    if not isinstance(checks, dict):
        missing.append("checks")
    else:
        for name in FREEZE_CHECKS:
            if checks.get(name) is not True:
                missing.append("checks.%s" % name)
    result["frozen"] = not missing and not problems
    return result


# ---------------------------------------------------------------------------
# 事件检测（E1 涨停 / E2 龙虎榜净买入；样本窗口 + 同股簇合并）
# ---------------------------------------------------------------------------
def detect_events(panel, top_list=None, spec=None):
    """检测 E1/E2 事件并做样本窗口过滤 + 同股（同事件类型）<20 日簇合并。

    panel: load_panel 结构；top_list: [{ts_code/trade_date/net_amount}]。
    返回事件列表（按 date/type/code 排序；已合并簇、保留首事件）。
    """
    spec = spec or SPEC
    calendar = panel.get("calendar") or []
    cal_idx = _calendar_index_map(calendar)
    lo, hi = spec["sample_in"], spec["sample_out"]
    raw = []
    for code, data in (panel.get("codes") or {}).items():
        bars = data.get("bars") or []
        for i in range(1, len(bars)):
            day = bars[i]["date"]
            if day < lo or day > hi:
                continue
            prev_close = _num(bars[i - 1].get("close"))
            close = _num(bars[i].get("close"))
            if prev_close is None or close is None or prev_close <= 0:
                continue
            if close == limit_up_price(prev_close, code, _bar_is_st(bars[i])):
                raw.append({"date": day, "ts_code": code, "event_type": "E1",
                            "net_amount": None})
    agg = {}
    for row in top_list or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ts_code") or row.get("symbol") or "").strip()
        day = str(row.get("trade_date") or row.get("date") or "").strip()
        net = _num(row.get("net_amount"))
        if not code or not day or net is None:
            continue
        if day < lo or day > hi:
            continue
        agg[(code, day)] = agg.get((code, day), 0.0) + net
    for (code, day), net in agg.items():
        if net > 0:
            raw.append({"date": day, "ts_code": code, "event_type": "E2",
                        "net_amount": net})
    # 同股同类型事件簇合并：间隔 <cluster_gap_days 交易日保留首事件
    merged = []
    for etype in spec["event_types"]:
        by_code = {}
        for ev in raw:
            if ev["event_type"] == etype:
                by_code.setdefault(ev["ts_code"], []).append(ev)
        for code, evs in by_code.items():
            evs.sort(key=lambda x: (str(x["date"]), cal_idx.get(
                str(x["date"]), 10 ** 9)))
            keep_idx = -10 ** 9
            for ev in evs:
                idx = cal_idx.get(str(ev["date"]))
                if idx is None:
                    merged.append(ev)  # 日历外事件保留，后续硬闸会剔除
                    continue
                if idx - keep_idx < spec["cluster_gap_days"]:
                    continue
                merged.append(ev)
                keep_idx = idx
    merged.sort(key=lambda x: (x["date"], x["event_type"], x["ts_code"]))
    return merged


# ---------------------------------------------------------------------------
# 硬闸 t+1 可交易（不满足即剔除，不宽进）
# ---------------------------------------------------------------------------
def gate_t1(panel, event, spec=None):
    """t+1 可交易硬闸；返回 (ok, reasons)。"""
    spec = spec or SPEC
    reasons = []
    data = (panel.get("codes") or {}).get(event["ts_code"])
    if not data:
        return False, ["no_panel"]
    bars = data.get("bars") or []
    dates = [bar["date"] for bar in bars]
    i = bisect.bisect_left(dates, str(event["date"]))
    if i >= len(bars) or bars[i]["date"] != str(event["date"]):
        return False, ["event_bar_missing"]
    calendar = panel.get("calendar") or []
    cal_idx = _calendar_index_map(calendar)
    day_idx = cal_idx.get(bars[i]["date"])
    if day_idx is None:
        return False, ["event_date_not_in_calendar"]
    if day_idx + 1 >= len(calendar):
        return False, ["t1_no_next_trading_day"]
    t1 = bars[i + 1] if i + 1 < len(bars) else None
    if t1 is None or t1["date"] != calendar[day_idx + 1]:
        return False, ["t1_suspended_or_missing（无停牌跳跃）"]
    event_bar = bars[i]
    if _bar_is_st(event_bar) or _bar_is_st(t1):
        reasons.append("st")
    open_t1, vol_t1 = _num(t1.get("open")), _num(t1.get("volume"))
    if not (open_t1 and open_t1 > 0):
        reasons.append("t1_open_invalid")
    if not (vol_t1 and vol_t1 > 0):
        reasons.append("t1_volume_zero")
    close_t = _num(event_bar.get("close"))
    if close_t and close_t > 0 and open_t1:
        lu = limit_up_price(close_t, event["ts_code"], _bar_is_st(t1))
        if open_t1 >= lu:
            reasons.append("t1_open_at_limit_up（一字板不可交易）")
    # 流动性：事件日 20 日均成交额 ≥3000 万
    window = bars[max(0, i - 19):i + 1]
    amounts = [_num(bar.get("amount")) for bar in window]
    if len(window) < 20:
        reasons.append("liquidity_window_lt_20")
    elif any(v is None for v in amounts):
        reasons.append("amount_missing")
    elif sum(amounts) / len(amounts) < spec["liquidity_20d_cny"]:
        reasons.append("liquidity_below_30m")
    # 上市 ≥250 交易日
    list_date = data.get("list_date")
    if not list_date:
        reasons.append("list_date_missing")
    else:
        first = bisect.bisect_left(calendar, str(list_date))
        listed = day_idx - first + 1
        if listed < spec["min_listed_days"]:
            reasons.append("listed_days_lt_250(%d)" % listed)
    return (not reasons), reasons


# ---------------------------------------------------------------------------
# 入场 / 离场（回避 W 后 t+6 开盘；持有 H 退出）
# ---------------------------------------------------------------------------
def find_entry(panel, event, spec=None):
    """t+6 起顺延最多 4 候选；返回 (entry_idx | None, reasons)。"""
    spec = spec or SPEC
    data = (panel.get("codes") or {}).get(event["ts_code"])
    if not data:
        return None, ["no_panel"]
    bars = data.get("bars") or []
    dates = [bar["date"] for bar in bars]
    i = bisect.bisect_left(dates, str(event["date"]))
    reasons = []
    for fallback in range(spec["entry_fallbacks"] + 1):
        offset = spec["entry_offset"] + fallback
        idx = i + offset
        tag = "t+%d" % offset
        if idx >= len(bars):
            reasons.append("%s_no_bar" % tag)
            continue
        bar = bars[idx]
        open_px = _num(bar.get("open"))
        vol = _num(bar.get("volume"))
        prev_close = _num(bars[idx - 1].get("close")) if idx >= 1 else None
        if not (open_px and open_px > 0):
            reasons.append("%s_open_invalid" % tag)
            continue
        if not (vol and vol > 0):
            reasons.append("%s_volume_zero" % tag)
            continue
        if _bar_is_st(bar):
            reasons.append("%s_st" % tag)
            continue
        if prev_close:
            lu = limit_up_price(prev_close, event["ts_code"], _bar_is_st(bar))
            if open_px >= lu:
                reasons.append("%s_open_at_limit_up" % tag)
                continue
        return idx, reasons
    return None, reasons + ["entry_candidates_exhausted"]


def find_exit(panel, event, entry_idx, spec=None):
    """入场日 +H-1 收盘离场；无 bar/跌停顺延 ≤3 日；返回 (exit_idx|None, reasons)。"""
    spec = spec or SPEC
    data = (panel.get("codes") or {}).get(event["ts_code"])
    bars = data.get("bars") if data else None
    if not bars:
        return None, ["no_panel"]
    reasons = []
    for fallback in range(spec["exit_fallbacks"] + 1):
        idx = entry_idx + spec["H"] - 1 + fallback
        tag = "exit+%d" % fallback
        if idx >= len(bars):
            reasons.append("%s_no_bar" % tag)
            continue
        bar = bars[idx]
        close = _num(bar.get("close"))
        prev_close = _num(bars[idx - 1].get("close")) if idx >= 1 else None
        if close is None or not bar.get("date"):
            reasons.append("%s_close_missing" % tag)
            continue
        if prev_close:
            ld = limit_down_price(prev_close, event["ts_code"],
                                  _bar_is_st(bar))
            if close <= ld:
                reasons.append("%s_limit_down（跌停不可卖）" % tag)
                continue
        return idx, reasons
    return None, reasons + ["exit_candidates_exhausted"]


# ---------------------------------------------------------------------------
# 单笔 trade（H1b-A 主口径）与 H1b-B 回避 KPI
# ---------------------------------------------------------------------------
def simulate_trade(panel, event, spec=None):
    """单事件 → trade dict（excluded 时给 reason；不伪造收益）。"""
    spec = spec or SPEC
    ok, gate_reasons = gate_t1(panel, event, spec)
    if not ok:
        return {"status": "excluded", "stage": "t1_gate",
                "ts_code": event["ts_code"], "event_type": event["event_type"],
                "event_date": event["date"], "reasons": gate_reasons}
    data = panel["codes"][event["ts_code"]]
    bars = data["bars"]
    dates = [bar["date"] for bar in bars]
    i = bisect.bisect_left(dates, str(event["date"]))
    entry_idx, entry_reasons = find_entry(panel, event, spec)
    if entry_idx is None:
        return {"status": "excluded", "stage": "entry",
                "ts_code": event["ts_code"], "event_type": event["event_type"],
                "event_date": event["date"], "reasons": entry_reasons}
    exit_idx, exit_reasons = find_exit(panel, event, entry_idx, spec)
    if exit_idx is None:
        return {"status": "excluded", "stage": "exit",
                "ts_code": event["ts_code"], "event_type": event["event_type"],
                "event_date": event["date"], "reasons": exit_reasons}
    entry_bar, exit_bar = bars[entry_idx], bars[exit_idx]
    open_entry = _num(entry_bar.get("open"))
    close_exit = _num(exit_bar.get("close"))
    gross = close_exit / open_entry - 1.0
    raw_net = gross - spec["cost_bps"] / 10000.0
    stress_net = gross - spec["stress_cost_bps"] / 10000.0
    avoid_ret = None
    t_plus_w = i + spec["W"]
    if i + 1 < len(bars) and t_plus_w < len(bars):
        open_t1 = _num(bars[i + 1].get("open"))
        close_tw = _num(bars[t_plus_w].get("close"))
        if open_t1 and open_t1 > 0 and close_tw is not None:
            avoid_ret = close_tw / open_t1 - 1.0
    return {
        "status": "traded",
        "ts_code": event["ts_code"], "event_type": event["event_type"],
        "event_date": event["date"],
        "entry_date": entry_bar["date"], "entry_open": open_entry,
        "exit_date": exit_bar["date"], "exit_close": close_exit,
        "hold_days": spec["H"], "gross_ret": gross,
        "raw_net": raw_net, "stress_net": stress_net,
        "avoid_ret": avoid_ret,
        "entry_fallbacks": entry_idx - (i + spec["entry_offset"]),
        "exit_fallbacks": exit_idx - (entry_idx + spec["H"] - 1),
        "cost_bps": spec["cost_bps"],
    }


def run_backtest(events, panel, spec=None):
    """骨架回测：逐事件 simulate_trade；返回 trades/excluded/funnel（不写盘）。"""
    spec = spec or SPEC
    trades, excluded = [], []
    for event in events:
        outcome = simulate_trade(panel, event, spec)
        (trades if outcome["status"] == "traded" else excluded).append(outcome)
    funnel = {
        "events": len(events),
        "traded": len(trades),
        "excluded": len(excluded),
        "excluded_by_stage": {},
    }
    for row in excluded:
        stage = row.get("stage") or "unknown"
        funnel["excluded_by_stage"][stage] = (
            funnel["excluded_by_stage"].get(stage, 0) + 1)
    return {"trades": trades, "excluded": excluded, "funnel": funnel}


# ---------------------------------------------------------------------------
# 统计 + 8 门判定（预注册 §七）
# ---------------------------------------------------------------------------
def _mean(values):
    return sum(values) / len(values) if values else None


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def one_sided_p(mean, stdev, n):
    """单侧 H1: mean>0 的近似 p（正态近似；n<2 返回 None）。"""
    if n < 2 or mean is None or stdev is None or stdev <= 0:
        return None
    z = mean / (stdev / math.sqrt(n))
    return 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))


def holm_adjust(pvalues):
    """Holm 校正（主家族）；None 视为 1.0。返回与输入同序的校正 p。"""
    m = len(pvalues)
    finite = [(i, 1.0 if p is None else float(p)) for i, p in enumerate(pvalues)]
    finite.sort(key=lambda x: x[1])
    adjusted = [1.0] * m
    running = 0.0
    for rank, (idx, p) in enumerate(finite):
        running = max(running, min(1.0, p * (m - rank)))
        adjusted[idx] = running
    return adjusted


def cluster_bootstrap_ci(values, clusters, n_boot=1000, seed=7):
    """事件日簇 bootstrap 的均值 95%CI 下界（确定性 seed；无样本 → None）。"""
    if not values or not clusters or len(values) != len(clusters):
        return None
    grouped = {}
    for value, cluster in zip(values, clusters):
        grouped.setdefault(str(cluster), []).append(value)
    keys = sorted(grouped)
    rng = random.Random(seed)
    means = []
    for _ in range(max(1, int(n_boot))):
        sample = [rng.choice(keys) for _ in range(len(keys))]
        picked = [v for key in sample for v in grouped[key]]
        means.append(sum(picked) / len(picked))
    means.sort()
    return means[int(0.025 * (len(means) - 1))]


def summarize_trades(trades, spec=None):
    """按事件类型汇总 raw_net（OOS 过滤由调用方传入）；返回 metrics dict。"""
    spec = spec or SPEC
    out = {}
    for etype in spec["event_types"]:
        subset = [t for t in trades if t.get("event_type") == etype]
        nets = [t["raw_net"] for t in subset]
        stress = [t["stress_net"] for t in subset]
        dates = [t["entry_date"] for t in subset]
        stdev = None
        if len(nets) > 1:
            m = _mean(nets)
            var = sum((v - m) ** 2 for v in nets) / (len(nets) - 1)
            stdev = math.sqrt(var)
        out[etype] = {
            "n_indep": len(subset),
            "mean_net": _mean(nets),
            "median_net": _median(nets),
            "win_rate": (sum(1 for v in nets if v > 0) / len(nets)
                         if nets else None),
            "mean_net_stress": _mean(stress),
            "p_raw": one_sided_p(_mean(nets), stdev, len(nets)),
            "ci_low": cluster_bootstrap_ci(nets, dates,
                                           n_boot=spec["bootstrap"]),
            "values": nets,
            "entry_dates": dates,
            "avoid_ret_median": _median([t["avoid_ret"] for t in subset
                                         if t.get("avoid_ret") is not None]),
            "neutral_net_median": None,
            "neutral_net_mean": None,
            "subperiod_means": _subperiod_means(subset),
        }
    return out


def _subperiod_means(trades):
    """3 个等长子区间（按 entry_date 排序三等分）raw_net 均值。"""
    ordered = sorted(trades, key=lambda t: t["entry_date"])
    if not ordered:
        return [None, None, None]
    parts = [ordered[i::3] for i in range(3)]
    if any(not part for part in parts):
        # 重建连续三段，避免 i::3 分片在 n<3 时出现空段
        n = len(ordered)
        cuts = [0, n // 3, (2 * n) // 3, n]
        parts = [ordered[cuts[k]:cuts[k + 1]] for k in range(3)]
    return [_mean([t["raw_net"] for t in part]) for part in parts]


def evaluate_event_gates(metrics, spec=None):
    """单事件 8 门判定（预注册 §7.1/§7.2，不放宽）。

    metrics 需含：n_indep / median_net / mean_net / mean_net_stress /
    p_holm / ci_low / neutral_net_median / neutral_net_mean / subperiod_means。
    返回 {gates, score, robust_pass, verdict, go, reasons}。
    """
    spec = spec or SPEC
    m = metrics or {}
    gates = {}
    gates["M1"] = int(m.get("n_indep") or 0) >= spec["min_independent"]
    gates["M2"] = _gt(m.get("median_net"), 0.0)
    gates["M3"] = _gt(m.get("mean_net"), 0.0)
    gates["M4"] = m.get("p_holm") is not None and m["p_holm"] < spec["holm_alpha"]
    gates["R1"] = _gt(m.get("ci_low"), 0.0)
    gates["R2"] = (_gt(m.get("neutral_net_median"), 0.0)
                   and _ge(m.get("neutral_net_mean"), 0.0))
    subs = m.get("subperiod_means") or []
    gates["R3"] = sum(1 for v in subs if _gt(v, 0.0)) >= 2
    gates["R4"] = _gt(m.get("mean_net_stress"), 0.0)
    score = sum(1 for name in spec["gates"] if gates.get(name))
    robust_pass = sum(1 for name in ("R1", "R2", "R3", "R4")
                      if gates.get(name))
    reasons = [name for name in spec["gates"] if not gates.get(name)]
    main_pass = all(gates.get(name) for name in ("M1", "M2", "M3", "M4"))
    if not all(gates.get(name) for name in ("M2", "M3", "M4")):
        verdict = "No-Go"
    elif not main_pass or robust_pass < spec["robust_min_pass"] \
            or score < spec["go_total_score"]:
        verdict = "补证"
    else:
        verdict = "GO"
    return {"gates": gates, "score": score, "robust_pass": robust_pass,
            "verdict": verdict, "go": verdict == "GO", "failed": reasons,
            "note": "GO 仅代表值得进入 H1c 仿真/产品化预研设计，不代表可交易"}


def _gt(value, threshold):
    return value is not None and float(value) > threshold


def _ge(value, threshold):
    return value is not None and float(value) >= threshold


def evaluate_all(trades, spec=None):
    """按事件类型汇总 + Holm 校正 + 8 门判定（骨架输出）。"""
    spec = spec or SPEC
    stats = summarize_trades(trades, spec)
    pvals = [stats[e]["p_raw"] for e in spec["event_types"]]
    holm = holm_adjust(pvals)
    for idx, etype in enumerate(spec["event_types"]):
        stats[etype]["p_holm"] = holm[idx]
    results = {etype: evaluate_event_gates(stats[etype], spec)
               for etype in spec["event_types"]}
    return {"by_event": stats, "gates": results}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _public_spec():
    return {key: SPEC[key] for key in
            ("W", "H", "entry_offset", "entry_fallbacks", "exit_fallbacks",
             "cost_bps", "stress_cost_bps", "event_types", "sample_in",
             "sample_out", "is_end", "oos_start", "min_independent",
             "liquidity_20d_cny", "min_listed_days", "gates",
             "go_total_score", "robust_min_pass", "holm_alpha")}


def _emit(payload, as_json):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="LCS-1 H1b 回测骨架（预注册口径；数据未冻结拒绝真实回测）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只校验预注册参数 + 冻结回执缺字段（不跑数据）")
    ap.add_argument("--manifest", default=None, help="DATA1 冻结回执 JSON")
    ap.add_argument("--events", default=None, help="事件 JSONL（真实回测用）")
    ap.add_argument("--panel", default=None, help="行情 panel JSON（真实回测用）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)

    issues = validate_spec(SPEC)
    freeze = check_freeze(load_manifest(args.manifest)) if args.manifest else {
        "frozen": False,
        "missing_fields": ["<manifest>（--manifest 未提供：DATA1 冻结回执缺失）"],
        "problems": [], "manifest": None}

    if args.dry_run:
        payload = {
            "status": "dry_run_ok" if not issues else "param_invalid",
            "spec": _public_spec(),
            "param_issues": issues,
            "freeze": {"frozen": freeze["frozen"],
                       "missing_fields": freeze["missing_fields"],
                       "problems": freeze["problems"]},
            "result_claim": False,
            "note": NOTE,
        }
        _emit(payload, args.json)
        if not args.json:
            print("[lcs1_h1b_harness] dry-run：参数校验%s"
                  % ("通过" if not issues else "失败：%s" % "；".join(issues)))
            for key in ("W", "H", "entry_offset", "cost_bps",
                        "stress_cost_bps"):
                print("  %s=%s" % (key, SPEC[key]))
            print("  sample=%s~%s | IS≤%s | OOS≥%s | 评分卡=%s"
                  % (SPEC["sample_in"], SPEC["sample_out"], SPEC["is_end"],
                     SPEC["oos_start"], ",".join(SPEC["gates"])))
            print("  数据冻结：%s"
                  % ("已冻结 ✓" if freeze["frozen"] else "未冻结（拒绝真实回测）"))
            for field in freeze["missing_fields"] + freeze["problems"]:
                print("    - 缺/问题：%s" % field)
            print("  note: %s" % NOTE)
        return 0 if not issues else 2

    # ---- 真实回测：冻结前一律拒绝 ----
    if not args.manifest:
        payload = {"status": "refused", "reason": "data_not_frozen",
                   "missing_fields": ["<manifest>（DATA1 冻结回执缺失）"],
                   "result_claim": False, "note": NOTE}
        _emit(payload, args.json)
        if not args.json:
            print("[lcs1_h1b_harness] 拒绝真实回测：数据未冻结（缺 DATA1 回执）")
            print("  note: %s" % NOTE)
        return 1
    if not freeze["frozen"]:
        payload = {"status": "refused", "reason": "data_not_frozen",
                   "missing_fields": freeze["missing_fields"],
                   "problems": freeze["problems"],
                   "result_claim": False, "note": NOTE}
        _emit(payload, args.json)
        if not args.json:
            print("[lcs1_h1b_harness] 拒绝真实回测：冻结回执不完整")
            for field in freeze["missing_fields"] + freeze["problems"]:
                print("  - %s" % field)
            print("  note: %s" % NOTE)
        return 1
    if not args.events or not args.panel:
        payload = {"status": "error", "error": "missing_input",
                   "detail": "冻结通过后必须给 --events 与 --panel",
                   "result_claim": False, "note": NOTE}
        _emit(payload, args.json)
        return 2
    try:
        events = load_events(args.events)
        panel = load_panel(args.panel)
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": "input_invalid",
                              "detail": "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:300])},
                             ensure_ascii=False, indent=2))
        else:
            print("[lcs1_h1b_harness] 输入错误：%s" % exc, file=sys.stderr)
        return 2
    backtest = run_backtest(events, panel, SPEC)
    evaluation = evaluate_all(backtest["trades"], SPEC)
    payload = {
        "status": "backtest_skeleton_ran",
        "data_version": freeze["manifest"].get("data_version"),
        "funnel": backtest["funnel"],
        "evaluation": evaluation,
        "result_claim": False,
        "note": NOTE,
        "oos_window": [SPEC["oos_start"], SPEC["sample_out"]],
    }
    _emit(payload, args.json)
    if not args.json:
        print("[lcs1_h1b_harness] 骨架回测完成（研究用，非结论）："
              "events=%d traded=%d"
              % (backtest["funnel"]["events"],
                 backtest["funnel"]["traded"]))
        for etype, res in evaluation["gates"].items():
            print("  %s：verdict=%s score=%d/8 稳健门=%d/4 未过=%s"
                  % (etype, res["verdict"], res["score"],
                     res["robust_pass"], ",".join(res["failed"]) or "-"))
        print("  note: %s" % NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
