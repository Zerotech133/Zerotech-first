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
  R2 中性化端点（§5.1，2026-10-31 补齐）：同事件日匹配——同板块 + 同 SW L1
  PIT 行业 + 同市值三分位 + 同换手三分位，控制组 ≥10 只；不足按预注册回退链
  依次「去行业维度 → 去换手维度 → 该事件不计入中性化端点」；neutral_net =
  raw_net − 同组控制股在同 entry/exit 日期的收益均值；无控制样本/单元<10 →
  R2=False 并给原因（不猜、不编造）。
  §5.2 暴露回归辅方法（2026-10-31 补齐，**仅稳健性、不参与 GO**）：
  raw_net ~ log(circ_mv) + turnover + board 哑变量 + SW L1 哑变量（事件内暴露
  调整，事件类型哑变量在 E1/E2 混合时加入）；按事件日截面 OLS 后做
  Fama-MacBeth 取系数均值/SE/t；无 panel/字段缺失/样本不足 → status=unavailable
  并给原因；本段结果只作稳健性参考，**不进入 8 门与 GO/No-Go 判定**。

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
                                           "volume","amount","is_st"?,
                                           "circ_mv"?,"turnover_rate"?,
                                           "sw_l1"?}, ...]}}}
  （bars 按日期升序、必须是交易日历子集；amount=当日成交额，单位元。R2 中性化
  另需当日截面字段：circ_mv=流通市值（万元）、turnover_rate=换手（当日）、
  sw_l1=SW L1 行业（PIT，as-of 事件日；缺失即按预注册跳过行业维度）；缺
  circ_mv/turnover_rate 的事件计入「未匹配」，R2=False 并给原因。）

数据适配（2026-10-31 task-38）：`tools/h1b_data_adapter.py` 把 tdx/tushare
parquet（或 CSV 合成夹具）适配成上述 panel.json/events.jsonl——amount 千元→元
×1000、vol 手→股×100、turnover_rate 百分数/circ_mv 万元保持、ST 开区间展开、
top_list 过滤可转债/B股（只留 A 股日线 universe，审计实测 1,628 个非 A 股
code-date）。适配器不写数据仓，只写 `--out-dir`。

判定段（2026-10-31）：预注册唯一判定段 = OOS（2025-07-01~2026-10-16）；main
真实回测默认 `--segment oos`，`evaluate_all(..., segment="oos")` 在汇总/中性化/
回归之前过滤；`all`/`is` 仅作盘点，不得用于宣称结论。

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
from collections import defaultdict
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
    # R2 中性化（预注册 §5.1 同事件日匹配）：控制单元 ≥10 只；未匹配率>20% 仅参考
    "neutral_min_controls": 10,
    "neutral_unmatched_warn": 0.20,
}
BOARD_LIMITS = {"main": 0.10, "chinext": 0.20, "star": 0.20, "bj": 0.30}
ST_MAIN_LIMIT = 0.05
# §5.2 暴露回归（仅稳健性）：Fama-MacBeth 至少需 2 个可估截面日
EXPOSURE_MIN_DAYS = 2

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


def _date_token(value):
    """日期归一为 YYYYMMDD 便于比较（容忍 YYYY-MM-DD / 空；不做日历推断）。"""
    text = str(value or "").strip()
    if text in ("", "None", "none", "nan", "NaN", "NaT"):
        return ""
    return text.replace("-", "").replace("/", "")


def _iso_date(value):
    """YYYYMMDD → YYYY-MM-DD（harness/prereg 契约用 ISO；非法则原样返回）。"""
    token = _date_token(value)
    if len(token) == 8 and token.isdigit():
        return "%s-%s-%s" % (token[:4], token[4:6], token[6:8])
    return str(value or "")


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
    if int(spec.get("neutral_min_controls", 0) or 0) < 1:
        issues.append("R2 控制单元阈值必须 ≥1（预注册 §5.1 为 10）")
    warn = spec.get("neutral_unmatched_warn")
    if warn is not None and not (0.0 <= float(warn) <= 1.0):
        issues.append("未匹配率告警阈值必须在 [0,1]")
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

    bars 必须含 date/open/close；high/low/volume/amount 建议提供（硬闸/成本用）；
    R2 中性化另用 bars 的可选当日截面字段 circ_mv（万元）/turnover_rate/sw_l1。
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
            for key in ("open", "close", "high", "low", "volume", "amount",
                        "circ_mv", "turnover_rate"):
                item[key] = _num(item.get(key))
            item["is_st"] = bool(item.get("is_st"))
            item["sw_l1"] = (str(item["sw_l1"]).strip()
                             if item.get("sw_l1") else None)
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
    cal_by_token = {_date_token(day): str(day) for day in calendar}
    lo, hi = _date_token(spec["sample_in"]), _date_token(spec["sample_out"])
    raw = []
    for code, data in (panel.get("codes") or {}).items():
        bars = data.get("bars") or []
        for i in range(1, len(bars)):
            day = bars[i]["date"]
            token = _date_token(day)
            if token < lo or token > hi:
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
        day_raw = str(row.get("trade_date") or row.get("date") or "").strip()
        net = _num(row.get("net_amount"))
        if not code or not day_raw or net is None:
            continue
        token = _date_token(day_raw)
        if token < lo or token > hi:
            continue
        day = cal_by_token.get(token, day_raw)  # 对齐 panel 日期格式（ISO/数字）
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
# R2 中性化端点：同事件日匹配（预注册 §5.1；纯函数，只读 records）
# ---------------------------------------------------------------------------
def build_panel_index(panel):
    """把 load_panel 结构预处理为按 code/date 快速索引的只读视图。"""
    index = {"calendar": list(panel.get("calendar") or []), "codes": {}}
    for code, data in (panel.get("codes") or {}).items():
        bars = data.get("bars") or []
        index["codes"][str(code)] = {
            "list_date": data.get("list_date"),
            "bars": bars,
            "by_date": {bar["date"]: bar for bar in bars},
            "dates": [bar["date"] for bar in bars],
        }
    return index


def event_index_by_date(events):
    """{事件日: 当日事件代码集合}（E1/E2 合并；判非事件控制池用）。"""
    out = {}
    for event in events or []:
        day = str(event.get("date") or "")
        code = str(event.get("ts_code") or "")
        if day and code:
            out.setdefault(day, set()).add(code)
    return out


def _tercile_bounds(values):
    """当日全市场三分位切点（两边界）；样本 <3 → None（无法分层）。"""
    vals = sorted(v for v in values if v is not None)
    if len(vals) < 3:
        return None
    return (vals[int((len(vals) - 1) / 3.0)],
            vals[int(2 * (len(vals) - 1) / 3.0)])


def _tercile(value, bounds):
    if value is None or bounds is None:
        return None
    low, high = bounds
    if value <= low:
        return 0
    if value <= high:
        return 1
    return 2


def _stdev(values):
    if not values or len(values) < 2:
        return None
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _raw_return_by_dates(index, code, entry_date, exit_date):
    """控制股在**与事件相同的 entry/exit 日期**上的 close_exit/open_entry−1。"""
    row = index["codes"].get(code)
    if not row:
        return None
    entry = row["by_date"].get(entry_date)
    exit_bar = row["by_date"].get(exit_date)
    if not entry or not exit_bar:
        return None
    open_entry = _num(entry.get("open"))
    close_exit = _num(exit_bar.get("close"))
    if not open_entry or open_entry <= 0 or close_exit is None:
        return None
    return close_exit / open_entry - 1.0


def match_control(index, trade, events_by_date=None, spec=None):
    """为单笔 trade 找同事件日匹配控制组（预注册 §5.1）。

    匹配维度和回退链（不放宽）：
      1) 板块 + SW L1（事件日 PIT）+ 市值三分位 + 换手三分位；
      2) 不足 10 只 → 去行业维度（板块 + 市值 + 换手）；
      3) 仍不足 → 去换手维度（板块 + 市值）；
      4) 仍不足 → 该事件不计入中性化端点（unmatched，不猜）。
    事件行业缺失（预注册允许）→ 直接从第 2 步起（fallback=no_industry）。
    返回 {matched, control_n, control_mean, neutral_net, fallback, attempts,
    reason}；中性化值 = trade["raw_net"] − 控制组收益均值。
    """
    spec = spec or SPEC
    events_by_date = events_by_date or {}
    code = str(trade.get("ts_code") or "")
    day = str(trade.get("event_date") or "")
    entry_date = str(trade.get("entry_date") or "")
    exit_date = str(trade.get("exit_date") or "")
    row = index["codes"].get(code) or {}
    event_bar = (row.get("by_date") or {}).get(day)
    if not event_bar:
        return {"matched": False, "control_n": 0, "control_mean": None,
                "neutral_net": None, "fallback": "unmatched",
                "attempts": {}, "reason": "event_bar_missing"}
    event_sw = event_bar.get("sw_l1")
    event_mv = _num(event_bar.get("circ_mv"))
    event_turn = _num(event_bar.get("turnover_rate"))
    if event_mv is None or event_turn is None:
        return {"matched": False, "control_n": 0, "control_mean": None,
                "neutral_net": None, "fallback": "unmatched",
                "attempts": {}, "reason": "event_cross_section_missing"}
    event_codes = events_by_date.get(day) or set()
    control_pool = []
    mv_values, turn_values = [], []
    for cand_code, cand in index["codes"].items():
        bar = (cand.get("by_date") or {}).get(day)
        if bar is None:
            continue                    # 当日无 bar 不进截面
        if cand_code != code:
            mv_values.append(_num(bar.get("circ_mv")))
            turn_values.append(_num(bar.get("turnover_rate")))
        if cand_code == code or cand_code in event_codes:
            continue                    # 非事件股才是控制池
        control_pool.append((cand_code, bar))
    if not control_pool:
        return {"matched": False, "control_n": 0, "control_mean": None,
                "neutral_net": None, "fallback": "unmatched",
                "attempts": {}, "reason": "no_control_pool"}
    mv_bounds = _tercile_bounds(mv_values)
    turn_bounds = _tercile_bounds(turn_values)
    if mv_bounds is None or turn_bounds is None:
        return {"matched": False, "control_n": 0, "control_mean": None,
                "neutral_net": None, "fallback": "unmatched",
                "attempts": {}, "reason": "cross_section_lt_3"}
    board = board_of(code)
    event_mv_tier = _tercile(event_mv, mv_bounds)
    event_turn_tier = _tercile(event_turn, turn_bounds)
    attempts = []
    if event_sw:
        attempts.append(("full", ("board", "industry", "mv", "turnover")))
    attempts.append(("no_industry", ("board", "mv", "turnover")))
    attempts.append(("no_turnover", ("board", "mv")))
    attempts_sizes = {}
    for label, dims in attempts:
        returns = []
        for cand_code, bar in control_pool:
            if "board" in dims and board_of(cand_code) != board:
                continue
            if "industry" in dims and str(bar.get("sw_l1") or "") != str(event_sw):
                continue
            if "mv" in dims:
                cand_mv = _num(bar.get("circ_mv"))
                if cand_mv is None or _tercile(cand_mv, mv_bounds) != event_mv_tier:
                    continue
            if "turnover" in dims:
                cand_turn = _num(bar.get("turnover_rate"))
                if cand_turn is None or \
                        _tercile(cand_turn, turn_bounds) != event_turn_tier:
                    continue
            ret = _raw_return_by_dates(index, cand_code, entry_date, exit_date)
            if ret is None:
                continue
            returns.append(ret)
        attempts_sizes[label] = len(returns)
        if len(returns) >= spec["neutral_min_controls"]:
            control_mean = _mean(returns)
            return {"matched": True, "control_n": len(returns),
                    "control_mean": control_mean,
                    "neutral_net": trade["raw_net"] - control_mean,
                    "fallback": label, "attempts": attempts_sizes,
                    "reason": ""}
    return {"matched": False, "control_n": max(attempts_sizes.values()),
            "control_mean": None, "neutral_net": None,
            "fallback": "unmatched", "attempts": attempts_sizes,
            "reason": "control_cell_lt_%d" % spec["neutral_min_controls"]}


def neutralize_trade(index, trade, events_by_date=None, spec=None):
    """单笔 trade 附中性化字段（缺控制/单元<10 → neutral_net=None + 原因）。"""
    result = match_control(index, trade, events_by_date, spec)
    row = dict(trade)
    row.update({
        "neutral_net": result["neutral_net"],
        "neutral_control_n": result["control_n"],
        "neutral_control_mean": result["control_mean"],
        "neutral_fallback": result["fallback"],
        "neutral_reason": result["reason"],
        "neutral_attempts": result["attempts"],
    })
    return row


def neutralize_trades(trades, panel, events_by_date=None, spec=None):
    """批量中性化；返回 (trades_with_neutral, summary)。

    summary 含 n/matched/unmatched/match_rate/unmatched_rate/fallback_counts；
    未匹配率超过预注册告警阈值（20%）时置未匹配告警标记（bool）。
    """
    spec = spec or SPEC
    index = build_panel_index(panel)
    events_by_date = events_by_date or {}
    rows, fallback_counts, unmatched = [], {}, 0
    for trade in trades or []:
        row = neutralize_trade(index, trade, events_by_date, spec)
        rows.append(row)
        if row.get("neutral_net") is None:
            unmatched += 1
        else:
            label = row.get("neutral_fallback") or "unknown"
            fallback_counts[label] = fallback_counts.get(label, 0) + 1
    total = len(rows)
    matched = total - unmatched
    unmatched_rate = (unmatched / total) if total else None
    summary = {
        "n": total,
        "matched": matched,
        "unmatched": unmatched,
        "match_rate": round(matched / total, 3) if total else None,
        "unmatched_rate": (round(unmatched_rate, 3)
                           if unmatched_rate is not None else None),
        "fallback_counts": dict(sorted(fallback_counts.items())),
        "min_controls": spec["neutral_min_controls"],
        "warn_unmatched_over_20pct": bool(
            unmatched_rate is not None
            and unmatched_rate > spec["neutral_unmatched_warn"]),
    }
    return rows, summary


# ---------------------------------------------------------------------------
# §5.2 暴露回归辅方法（横截面 OLS + 按日 Fama-MacBeth；仅稳健性，不参与 GO）
# ---------------------------------------------------------------------------
def _solve_linear(matrix, vector):
    """高斯-约当解线性方程组；奇异/非满秩 → None。"""
    size = len(matrix)
    aug = [list(matrix[i]) + [vector[i]] for i in range(size)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        for j in range(col, size + 1):
            aug[col][j] /= pivot_value
        for row in range(size):
            if row == col:
                continue
            factor = aug[row][col]
            if factor == 0.0:
                continue
            for j in range(col, size + 1):
                aug[row][j] -= factor * aug[col][j]
    return [aug[i][size] for i in range(size)]


def _invert_matrix(matrix):
    size = len(matrix)
    columns = []
    for i in range(size):
        unit = [1.0 if j == i else 0.0 for j in range(size)]
        column = _solve_linear(matrix, unit)
        if column is None:
            return None
        columns.append(column)
    return [[columns[j][i] for j in range(size)] for i in range(size)]


def _ols(y_values, x_rows):
    """普通最小二乘：返回 {beta, se, n, k}；样本不足/奇异 → None。"""
    n_obs = len(y_values)
    k = len(x_rows[0]) if x_rows else 0
    if not k or n_obs <= k:
        return None
    xtx = [[sum(x_rows[r][i] * x_rows[r][j] for r in range(n_obs))
            for j in range(k)] for i in range(k)]
    xty = [sum(x_rows[r][i] * y_values[r] for r in range(n_obs))
           for i in range(k)]
    beta = _solve_linear(xtx, xty)
    if beta is None:
        return None
    residuals = [y_values[r] - sum(x_rows[r][j] * beta[j] for j in range(k))
                 for r in range(n_obs)]
    rss = sum(value * value for value in residuals)
    sigma2 = rss / (n_obs - k)
    inverse = _invert_matrix(xtx)
    if inverse is None:
        return None
    se = [math.sqrt(max(0.0, sigma2 * inverse[j][j])) for j in range(k)]
    return {"beta": beta, "se": se, "n": n_obs, "k": k}


def _feature_names(rows):
    """回归自变量：截距 + log(circ_mv) + turnover + board/SW/事件哑变量。"""
    features = ["intercept", "log_circ_mv", "turnover"]
    boards = sorted({row["board"] for row in rows})
    for board in boards[1:]:
        features.append("board:" + board)
    industries = sorted({row["sw_l1"] for row in rows if row.get("sw_l1")})
    for industry in industries[1:]:
        features.append("sw:" + industry)
    event_types = sorted({row["event_type"] for row in rows
                          if row.get("event_type")})
    for etype in event_types[1:]:
        features.append("event:" + etype)
    return features


def _design_matrix(rows, features):
    matrix = []
    for row in rows:
        values = []
        for feature in features:
            if feature == "intercept":
                values.append(1.0)
            elif feature == "log_circ_mv":
                values.append(math.log(row["circ_mv"]))
            elif feature == "turnover":
                values.append(row["turnover"])
            elif feature.startswith("board:"):
                values.append(1.0 if row["board"] == feature[6:] else 0.0)
            elif feature.startswith("sw:"):
                values.append(1.0 if row["sw_l1"] == feature[3:] else 0.0)
            elif feature.startswith("event:"):
                values.append(1.0 if row["event_type"] == feature[6:] else 0.0)
            else:  # pragma: no cover - 防新增维度漏接线
                values.append(0.0)
        matrix.append(values)
    return matrix


def _exposure_rows(trades, panel):
    """从 trades+panel 提取回归样本（只用事件日可知字段，事件内暴露调整）。

    返回 (rows, dropped, missing_fields)；缺 circ_mv/turnover_rate/sw_l1 的
    事件行丢弃并记录字段名，供 unavailable 原因使用。
    """
    index = build_panel_index(panel)
    rows, dropped, missing = [], defaultdict(int), set()
    for trade in trades or []:
        code = str(trade.get("ts_code") or "")
        day = str(trade.get("event_date") or "")
        y_value = _num(trade.get("raw_net"))
        data = index["codes"].get(code)
        bar = (data or {}).get("by_date", {}).get(day) if data else None
        if bar is None:
            dropped["event_bar_missing"] += 1
            continue
        if y_value is None:
            dropped["raw_net_missing"] += 1
            continue
        circ_mv = _num(bar.get("circ_mv"))
        turnover = _num(bar.get("turnover_rate"))
        sw_l1 = str(bar.get("sw_l1") or "").strip()
        row_missing = []
        if circ_mv is None or circ_mv <= 0:
            row_missing.append("circ_mv")
        if turnover is None:
            row_missing.append("turnover_rate")
        if not sw_l1:
            row_missing.append("sw_l1")
        if row_missing:
            for field in row_missing:
                missing.add(field)
                dropped["%s_missing" % field] += 1
            continue
        rows.append({
            "y": y_value, "circ_mv": circ_mv, "turnover": turnover,
            "sw_l1": sw_l1, "board": board_of(code),
            "event_type": str(trade.get("event_type") or ""),
            "event_date": day,
        })
    return rows, dict(sorted(dropped.items())), sorted(missing)


def cross_section_regression(trades, panel, spec=None):
    """§5.2 暴露回归辅方法（仅稳健性，**不参与 GO 判定**）。

    模型：``raw_net = α + β1·log(circ_mv) + β2·turnover + Σγ·board +
    Σδ·SW_L1 + ε``（E1/E2 混合时含事件类型哑变量）；按事件日做截面 OLS，
    再按日 Fama-MacBeth 取系数均值/SE/t。无 panel/字段缺失/可估截面日不足
    返回 status=unavailable + 原因。返回的 ``used_in_go`` 恒为 False。
    """
    note = "仅稳健性（预注册 §5.2），不参与 GO 判定"
    if not panel:
        return {"status": "unavailable", "reason": "panel_missing",
                "missing_fields": [], "dropped": {}, "n": 0, "n_days": 0,
                "days_estimable": 0, "coefficients": {},
                "used_in_go": False, "note": note}
    rows, dropped, missing = _exposure_rows(trades, panel)
    if not rows:
        return {"status": "unavailable",
                "reason": "fields_missing" if missing else "no_trades",
                "missing_fields": missing, "dropped": dropped,
                "n": 0, "n_days": 0, "days_estimable": 0,
                "coefficients": {}, "used_in_go": False, "note": note}
    features = _feature_names(rows)
    by_day = defaultdict(list)
    for row in rows:
        by_day[row["event_date"]].append(row)
    estimable_days, skipped = [], {}
    for day in sorted(by_day):
        day_rows = by_day[day]
        fit = _ols([row["y"] for row in day_rows],
                   _design_matrix(day_rows, features))
        if fit is None:
            skipped[day] = "insufficient_obs_or_singular"
            continue
        estimable_days.append((day, fit["beta"]))
    if len(estimable_days) < EXPOSURE_MIN_DAYS:
        return {
            "status": "unavailable",
            "reason": "insufficient_cross_section_sample",
            "n": len(rows), "n_days": len(by_day),
            "days_estimable": len(estimable_days),
            "min_days_required": EXPOSURE_MIN_DAYS,
            "regressors": len(features), "skipped_days": skipped,
            "missing_fields": missing, "dropped": dropped,
            "coefficients": {}, "used_in_go": False, "note": note,
        }
    coefficients = {}
    for idx, feature in enumerate(features):
        values = [beta[idx] for _, beta in estimable_days]
        mean = _mean(values)
        stdev = _stdev(values)
        se = (stdev / math.sqrt(len(values))) if stdev is not None else 0.0
        t_value = (mean / se) if se and se > 0 else None
        coefficients[feature] = {
            "mean": mean,
            "se": se,
            "t": t_value,
            "n_days": len(values),
        }
    return {
        "status": "ok",
        "model": ("raw_net ~ intercept + log_circ_mv + turnover + board dummies"
                  " + SW_L1 dummies + event-type dummies"),
        "n": len(rows), "n_days": len(by_day),
        "days_estimable": len(estimable_days),
        "regressors": len(features), "coefficients": coefficients,
        "missing_fields": missing, "dropped": dropped,
        "used_in_go": False, "note": note,
    }


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
    """按事件类型汇总 raw_net + 中性化 neutral_net（OOS 过滤由调用方传入）。"""
    spec = spec or SPEC
    out = {}
    for etype in spec["event_types"]:
        subset = [t for t in trades if t.get("event_type") == etype]
        nets = [t["raw_net"] for t in subset]
        stress = [t["stress_net"] for t in subset]
        dates = [t["entry_date"] for t in subset]
        stdev = _stdev(nets)
        neutral = [t["neutral_net"] for t in subset
                   if t.get("neutral_net") is not None]
        neutral_fallback = {}
        for row in subset:
            if row.get("neutral_net") is None:
                continue
            label = row.get("neutral_fallback") or "unknown"
            neutral_fallback[label] = neutral_fallback.get(label, 0) + 1
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
            "neutral_n": len(neutral),
            "neutral_net_median": _median(neutral),
            "neutral_net_mean": _mean(neutral),
            "neutral_net_p": one_sided_p(_mean(neutral), _stdev(neutral),
                                         len(neutral)),
            "neutral_net_reason": ("" if neutral
                                   else _neutral_reason(subset)),
            "neutral_fallback_counts": dict(sorted(neutral_fallback.items())),
            "subperiod_means": _subperiod_means(subset),
        }
    return out


def _neutral_reason(trades):
    """无中性化样本时给出原因（优先计数最多者；全部缺失 → unavailable）。"""
    counts = {}
    for row in trades or []:
        reason = str(row.get("neutral_reason") or "")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    if counts:
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return "neutral_net_unavailable"


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
    p_holm / ci_low / neutral_n / neutral_net_median / neutral_net_mean /
    subperiod_means。R2 由中性化端点结果决定；无控制/单元<10 → R2=False 并在
    ``reasons["R2"]`` 给原因（neutral_net_reason）。
    返回 {gates, score, robust_pass, verdict, go, failed, reasons}。
    """
    spec = spec or SPEC
    m = metrics or {}
    gates = {}
    gates["M1"] = int(m.get("n_indep") or 0) >= spec["min_independent"]
    gates["M2"] = _gt(m.get("median_net"), 0.0)
    gates["M3"] = _gt(m.get("mean_net"), 0.0)
    gates["M4"] = m.get("p_holm") is not None and m["p_holm"] < spec["holm_alpha"]
    gates["R1"] = _gt(m.get("ci_low"), 0.0)
    gates["R2"] = (int(m.get("neutral_n") or 0) >= 1
                   and _gt(m.get("neutral_net_median"), 0.0)
                   and _ge(m.get("neutral_net_mean"), 0.0))
    subs = m.get("subperiod_means") or []
    gates["R3"] = sum(1 for v in subs if _gt(v, 0.0)) >= 2
    gates["R4"] = _gt(m.get("mean_net_stress"), 0.0)
    score = sum(1 for name in spec["gates"] if gates.get(name))
    robust_pass = sum(1 for name in ("R1", "R2", "R3", "R4")
                      if gates.get(name))
    failed = [name for name in spec["gates"] if not gates.get(name)]
    reasons = {}
    if not gates["R2"]:
        reasons["R2"] = (str(m.get("neutral_net_reason") or "")
                         or "neutral_net_median>0 且 mean≥0 未达（R2）")
    main_pass = all(gates.get(name) for name in ("M1", "M2", "M3", "M4"))
    if not all(gates.get(name) for name in ("M2", "M3", "M4")):
        verdict = "No-Go"
    elif not main_pass or robust_pass < spec["robust_min_pass"] \
            or score < spec["go_total_score"]:
        verdict = "补证"
    else:
        verdict = "GO"
    return {"gates": gates, "score": score, "robust_pass": robust_pass,
            "verdict": verdict, "go": verdict == "GO", "failed": failed,
            "reasons": reasons,
            "note": "GO 仅代表值得进入 H1c 仿真/产品化预研设计，不代表可交易"}


def _gt(value, threshold):
    return value is not None and float(value) > threshold


def _ge(value, threshold):
    return value is not None and float(value) >= threshold


SEGMENTS = ("all", "is", "oos")


def filter_trades_by_segment(trades, segment="all", spec=None):
    """按预注册段过滤 trades（IS 2023-09-04~2025-06-30；OOS 2025-07-01~2026-10-16）。

    - ``event_date``（事件日/决策日）为分段键，与 H1 切分口径一致（OOS=is_end 次日）；
    - 预注册判定段 = ``oos``（见 §七）；``all`` 仅供盘点，不得用于宣称结论；
    - 非法 segment → ValueError（fail-visible，不静默当 all）。
    """
    spec = spec or SPEC
    segment = str(segment or "all").strip().lower()
    if segment not in SEGMENTS:
        raise ValueError("segment 必须 ∈ %s，收到 %r" % (SEGMENTS, segment))
    if segment == "all":
        return list(trades or [])
    is_end = _date_token(spec["is_end"])
    out = []
    for trade in trades or []:
        day = _date_token(trade.get("event_date") or "")
        if not day:
            continue
        if segment == "is" and day <= is_end:
            out.append(trade)
        elif segment == "oos" and day > is_end:
            out.append(trade)
    return out


def _segment_window(segment, spec):
    if segment == "is":
        return [spec["sample_in"], spec["is_end"]]
    if segment == "oos":
        return [spec["oos_start"], spec["sample_out"]]
    return [spec["sample_in"], spec["sample_out"]]


def evaluate_all(trades, spec=None, panel=None, events=None, segment=None):
    """按事件类型汇总 + R2 中性化 + §5.2 暴露回归 + Holm 校正 + 8 门判定。

    ``segment``：``all``/``is``/``oos``（默认 ``all`` 保旧行为；预注册判定段=``oos``，
    main 真实回测路径默认用 oos）。过滤在汇总/中性化/回归**之前**执行，8 门与
    GO/No-Go 只基于该段。

    panel 给出时才做 R2 中性化（§5.1，同事件日匹配）与 §5.2 暴露回归；events
    用于判事件日非事件控制池。§5.2 仅稳健性输出（used_in_go=False），**不进入
    gates/GO 判定**；不提供 panel → R2=False、暴露回归 unavailable，均给原因。
    """
    spec = spec or SPEC
    segment = str(segment or "all").strip().lower()
    if segment not in SEGMENTS:
        raise ValueError("segment 必须 ∈ %s，收到 %r" % (SEGMENTS, segment))
    total = len(trades or [])
    trades = filter_trades_by_segment(trades, segment, spec)
    neutral_summary = None
    if panel is not None:
        trades, neutral_summary = neutralize_trades(
            trades, panel, event_index_by_date(events or []), spec)
    stats = summarize_trades(trades, spec)
    pvals = [stats[e]["p_raw"] for e in spec["event_types"]]
    holm = holm_adjust(pvals)
    for idx, etype in enumerate(spec["event_types"]):
        stats[etype]["p_holm"] = holm[idx]
    results = {etype: evaluate_event_gates(stats[etype], spec)
               for etype in spec["event_types"]}
    return {"by_event": stats, "gates": results,
            "segment": segment,
            "segment_window": _segment_window(segment, spec),
            "n_trades_total": total,
            "n_trades_segment": len(trades),
            "neutralization": neutral_summary,
            "exposure_regression": cross_section_regression(trades, panel,
                                                            spec)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _public_spec():
    return {key: SPEC[key] for key in
            ("W", "H", "entry_offset", "entry_fallbacks", "exit_fallbacks",
             "cost_bps", "stress_cost_bps", "event_types", "sample_in",
             "sample_out", "is_end", "oos_start", "min_independent",
             "liquidity_20d_cny", "min_listed_days", "gates",
             "go_total_score", "robust_min_pass", "holm_alpha",
             "neutral_min_controls", "neutral_unmatched_warn")}


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
    ap.add_argument("--segment", default="oos",
                    choices=list(SEGMENTS),
                    help="判定段（预注册判定=oos；all/is 仅盘点，默认 oos）")
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
    evaluation = evaluate_all(backtest["trades"], SPEC, panel=panel,
                              events=events, segment=args.segment)
    segment_counts = {
        name: len(filter_trades_by_segment(backtest["trades"], name, SPEC))
        for name in SEGMENTS
    }
    payload = {
        "status": "backtest_skeleton_ran",
        "data_version": freeze["manifest"].get("data_version"),
        "funnel": backtest["funnel"],
        "segment": args.segment,
        "segment_counts": segment_counts,
        "evaluation": evaluation,
        "result_claim": False,
        "note": NOTE,
        "oos_window": [SPEC["oos_start"], SPEC["sample_out"]],
    }
    _emit(payload, args.json)
    if not args.json:
        print("[lcs1_h1b_harness] 骨架回测完成（研究用，非结论）："
              "events=%d traded=%d | 判定段=%s（该段 trades=%d；all/is/oos=%d/%d/%d）"
              % (backtest["funnel"]["events"],
                 backtest["funnel"]["traded"],
                 evaluation.get("segment"),
                 evaluation.get("n_trades_segment", 0),
                 segment_counts["all"], segment_counts["is"],
                 segment_counts["oos"]))
        for etype, res in evaluation["gates"].items():
            print("  %s：verdict=%s score=%d/8 稳健门=%d/4 未过=%s"
                  % (etype, res["verdict"], res["score"],
                     res["robust_pass"], ",".join(res["failed"]) or "-"))
            if res.get("reasons", {}).get("R2"):
                print("    %s R2 未过原因：%s"
                      % (etype, res["reasons"]["R2"]))
        neutral = evaluation.get("neutralization") or {}
        if neutral:
            print("  R2 中性化：matched=%d/%d（match_rate=%s，回退=%s）%s"
                  % (neutral.get("matched", 0), neutral.get("n", 0),
                     neutral.get("match_rate"),
                     json.dumps(neutral.get("fallback_counts") or {},
                                ensure_ascii=False),
                     " ⚠未匹配率>20%"
                     if neutral.get("warn_unmatched_over_20pct") else ""))
        exposure = evaluation.get("exposure_regression") or {}
        if exposure.get("status") == "ok":
            coef = exposure.get("coefficients") or {}
            print("  §5.2 暴露回归（仅稳健性，不参与 GO）：days=%d/%d "
                  "log_mv_t=%s turnover_t=%s"
                  % (exposure.get("days_estimable", 0),
                     exposure.get("n_days", 0),
                     (coef.get("log_circ_mv") or {}).get("t"),
                     (coef.get("turnover") or {}).get("t")))
        else:
            print("  §5.2 暴露回归：unavailable（%s）"
                  % (exposure.get("reason") or "unknown"))
        print("  note: %s" % NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
