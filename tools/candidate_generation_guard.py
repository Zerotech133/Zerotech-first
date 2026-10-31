# -*- coding: utf-8 -*-
"""candidate_generation_guard.py — 候选池生成层只读门（2026-11-01）。

背景：2026-10-31 外部 30 卡池复核暴露的不是“逐票选择”，而是生成层缺陷：
  · CN 评分饱和（同市场全部 100，无区分度）
  · 缠论三买几何失效（broken/reclaimed/expired 混写）
  · 规则周期与证据周期不同源（5m 规则配日线 RSI14）
  · CRYPTO 量比常量 / PROM 评分=裸动量
  · 理由字段模板化、pre_close/公司行动缺失

本工具只做“生成层事实门”，不选票、不代签、不改 reviewed/handoff：
  G1 三买几何：触发 bar close > 中枢上沿；当前 bar 分 active/broken/reclaimed/expired
  G2 时效：signal_age_days、触发价→现价偏移
  G3 同源：strategy_timeframe/evidence_timeframe、rule_rsi_period/evidence_rsi_period
  G4 生理区间：score∈[1,99]、volume_ratio∈[0.01,100]、raw momentum score 识别
  G5 公司行动：|daily_return|>10% 必须有 corporate_action/event，否则 unknown
 池级戳：score 饱和、volume_ratio 常量、同源超跌、杠铃、CRYPTO 无效配额

输入：卡片 JSON / JSONL / {cards:[...]}；字段别名尽量宽松。
输出：JSON + Markdown；退出码 0=全 pass / 1=有 fail / 2=unknown 或输入错误。
只读：不联网、不写 DB/账本/任务板；仅 --out 显式落盘。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

SCHEMA = "candidate_generation_guard/v1"
GATE_VERSION = "2026-11-01"
SIGNAL_TYPES_3BUY = ("3买", "三买", "3buy", "third_buy")
MAX_SIGNAL_AGE_DAYS = 10
MAX_TRIGGER_DRIFT = 0.08
MAX_CENTER_DISTANCE = 3.0
SCORE_MIN, SCORE_MAX = 1.0, 99.0
VOL_MIN, VOL_MAX = 0.01, 100.0
CORP_ACTION_RETURN = 10.0
SAME_FACTOR_DIST = 5.0
BARBELL_RET20 = 20.0
POOL_SCORE_SATURATION_MIN = 3
POOL_VOLUME_CONST_MIN = 2


def _first(card, names, default=None):
    for n in names:
        if isinstance(card, dict) and card.get(n) not in (None, ""):
            return card.get(n)
    return default


def _num(card, names):
    v = _first(card, names)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(card, names):
    v = _first(card, names)
    return str(v) if v not in (None, "") else ""


def _bool(card, names):
    v = _first(card, names)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "是")
    return None


def normalize_card(card):
    """把不同来源的卡片字段归一成门检查所需视图；缺字段保持 None。"""
    if not isinstance(card, dict):
        return {}
    c = {
        "id": _text(card, ("candidate_id", "card_id", "id", "code", "symbol")),
        "market": _text(card, ("market", "book", "region")).upper(),
        "strategy": _text(card, ("strategy_id", "strategy", "strategy_family")),
        "score": _num(card, ("score", "pool_score", "total_score")),
        "signal_type": _text(card, ("signal_type", "type", "card_type", "chan_type")),
        "center_high": _num(card, ("center_high", "zhongshu_high", "zs_high", "center_upper")),
        "center_low": _num(card, ("center_low", "zhongshu_low", "zs_low", "center_lower")),
        "close": _num(card, ("close", "price", "current_price", "last_close")),
        "trigger_close": _num(card, ("trigger_close", "entry_px", "signal_price", "trigger_price")),
        "trigger_date": _text(card, ("trigger_date", "trigger_ts", "signal_date", "dt")),
        "asof": _text(card, ("asof", "trade_date", "date")),
        "strategy_timeframe": _text(card, ("strategy_timeframe", "rule_timeframe", "timeframe", "period")),
        "evidence_timeframe": _text(card, ("evidence_timeframe", "evidence_tf", "evidence_period")),
        "rule_rsi_period": _num(card, ("rule_rsi_period", "rsi_period", "rule_period")),
        "evidence_rsi_period": _num(card, ("evidence_rsi_period", "display_rsi_period", "rsi14_period")),
        "rsi14": _num(card, ("rsi14", "display_rsi", "rsi")),
        "volume_ratio": _num(card, ("volume_ratio", "vol_ratio", "量比")),
        "daily_return": _num(card, ("daily_return", "change_pct", "pct_chg", "涨幅", "涨跌幅")),
        "ret20": _num(card, ("ret20", "return_20d", "chg_20d", "20d_return")),
        "dist_low": _num(card, ("dist_low", "dist_20d_low_pct", "dist_low_pct", "距20日低")),
        "pre_close": _num(card, ("pre_close", "prev_close", "昨收")),
        "corporate_action": _bool(card, ("corporate_action", "event_adjusted", "company_action", "除权除息")),
        "score_method": _text(card, ("score_method", "scoring_method")),
        "raw_momentum": _num(card, ("raw_momentum", "momentum_20d", "raw_score")),
        "rationale": _text(card, ("rationale", "reason", "evidence_text", "理由")),
        "evidence": card.get("evidence") if isinstance(card.get("evidence"), (dict, list)) else None,
        "raw": card,
    }
    if not c["market"]:
        # 从代码/市场字段推断（宽松；不猜股票池）
        code = c["id"].upper()
        if code.endswith((".SH", ".SZ", ".BJ")):
            c["market"] = "CN"
        elif code.endswith(".HK") or (len(code) == 5 and code.isdigit()):
            c["market"] = "HK"
    return c


def _parse_date(text):
    s = str(text or "").strip().replace("/", "-")
    if len(s) >= 10:
        s = s[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def gate_chan_geometry(card):
    """G1：三买几何；非三买返回 pass(not_applicable)。"""
    typ = str(card.get("signal_type") or "")
    if not any(x.lower() in typ.lower() for x in SIGNAL_TYPES_3BUY):
        return {"gate": "G1", "status": "pass", "reason": "not_chan_3buy"}
    high, low = card.get("center_high"), card.get("center_low")
    close, trig = card.get("close"), card.get("trigger_close")
    if high is None or low is None or close is None:
        return {"gate": "G1", "status": "unknown", "reason": "missing center/close"}
    if high <= low:
        return {"gate": "G1", "status": "fail", "reason": "center_high<=center_low"}
    if trig is not None and trig <= high:
        return {"gate": "G1", "status": "fail", "reason": "trigger_close<=center_high"}
    if close < low:
        return {"gate": "G1", "status": "fail", "reason": "broken: close<center_low"}
    if close <= high:
        return {"gate": "G1", "status": "fail", "reason": "reclaimed: center_low<=close<=center_high"}
    width = high - low
    if width > 0 and (close - high) / width > MAX_CENTER_DISTANCE:
        return {"gate": "G1", "status": "fail", "reason": "expired: center too far"}
    return {"gate": "G1", "status": "pass", "reason": "active: close>center_high"}


def gate_staleness(card, asof=None):
    """G2：时效；缺触发日期/价时 unknown，不伪造。"""
    trig_date = _parse_date(card.get("trigger_date"))
    asof_date = _parse_date(asof or card.get("asof"))
    issues = []
    if trig_date and asof_date:
        age = (asof_date - trig_date).days
        if age > MAX_SIGNAL_AGE_DAYS:
            issues.append("signal_age_days=%d>%d" % (age, MAX_SIGNAL_AGE_DAYS))
    trig_close, close = card.get("trigger_close"), card.get("close")
    if trig_close and close and trig_close > 0:
        drift = abs(close / trig_close - 1.0)
        if drift > MAX_TRIGGER_DRIFT:
            issues.append("trigger_drift=%.2f%%>%.0f%%" % (drift * 100, MAX_TRIGGER_DRIFT * 100))
    if issues:
        return {"gate": "G2", "status": "fail", "reason": ";".join(issues)}
    if (trig_date is None or asof_date is None) and (trig_close is None or close is None):
        return {"gate": "G2", "status": "unknown", "reason": "missing trigger/asof"}
    return {"gate": "G2", "status": "pass", "reason": "within age/drift"}


def gate_source(card):
    """G3：策略周期/RSI 周期与展示证据同源。"""
    st, et = card.get("strategy_timeframe"), card.get("evidence_timeframe")
    rp, ep = card.get("rule_rsi_period"), card.get("evidence_rsi_period")
    issues = []
    if st and et and st != et:
        issues.append("timeframe %s!=%s" % (st, et))
    if rp is not None and ep is not None and abs(rp - ep) > 1e-9:
        issues.append("rsi_period %g!=%g" % (rp, ep))
    if issues:
        return {"gate": "G3", "status": "fail", "reason": ";".join(issues)}
    if (st and not et) or (rp is not None and ep is None):
        return {"gate": "G3", "status": "unknown", "reason": "missing evidence timeframe/period"}
    if not st and not et and rp is None and ep is None:
        return {"gate": "G3", "status": "pass", "reason": "not_applicable"}
    return {"gate": "G3", "status": "pass", "reason": "same source"}


def gate_physiology(card):
    """G4：评分/量比生理区间与裸动量评分识别。"""
    issues, unknown = [], []
    score = card.get("score")
    if score is not None:
        if not (SCORE_MIN <= score <= SCORE_MAX):
            issues.append("score=%.2f outside [1,99]" % score)
        raw = card.get("raw_momentum")
        if raw is not None and abs(score - raw) < 0.5 and "percentile" not in str(card.get("score_method") or "").lower():
            issues.append("score≈raw_momentum without percentile")
    elif card.get("signal_type") or card.get("strategy"):
        unknown.append("missing score")
    vol = card.get("volume_ratio")
    if vol is not None and not (VOL_MIN <= vol <= VOL_MAX):
        issues.append("volume_ratio=%.4g outside [0.01,100]" % vol)
    if issues:
        return {"gate": "G4", "status": "fail", "reason": ";".join(issues)}
    if unknown:
        return {"gate": "G4", "status": "unknown", "reason": ";".join(unknown)}
    return {"gate": "G4", "status": "pass", "reason": "score/volume plausible"}


def gate_corporate(card):
    """G5：大波动必须有公司行动检查；缺证据标 unknown，不直接当趋势。"""
    ret = card.get("daily_return")
    if ret is None:
        return {"gate": "G5", "status": "unknown", "reason": "missing daily_return"}
    if abs(ret) <= CORP_ACTION_RETURN:
        return {"gate": "G5", "status": "pass", "reason": "|ret|<=10%"}
    ca = card.get("corporate_action")
    if ca is True:
        return {"gate": "G5", "status": "pass", "reason": "corporate action checked"}
    if card.get("pre_close") is None:
        return {"gate": "G5", "status": "unknown", "reason": "big move + missing pre_close"}
    return {"gate": "G5", "status": "unknown", "reason": "big move without corporate_action"}


GATES = (gate_chan_geometry, gate_staleness, gate_source, gate_physiology, gate_corporate)


def evaluate_card(card, asof=None):
    c = normalize_card(card)
    checks = []
    for fn in GATES:
        try:
            checks.append(fn(c, asof) if fn is gate_staleness else fn(c))
        except Exception as exc:  # noqa: BLE001
            checks.append({"gate": getattr(fn, "__name__", "?"), "status": "unknown",
                           "reason": "exception:%s" % type(exc).__name__})
    statuses = {x["status"] for x in checks}
    status = "fail" if "fail" in statuses else ("unknown" if "unknown" in statuses else "pass")
    return {"candidate_id": c["id"], "market": c["market"], "strategy": c["strategy"],
            "surface_status": status, "checks": checks, "normalized": {
                "score": c["score"], "signal_type": c["signal_type"],
                "strategy_timeframe": c["strategy_timeframe"],
                "volume_ratio": c["volume_ratio"], "daily_return": c["daily_return"]}}


def pool_stamps(cards):
    """池级三戳：同源超跌 / 杠铃 / 无效配额；另加 score 饱和/量比常量。"""
    by_market = {}
    for card in cards:
        c = normalize_card(card)
        by_market.setdefault(c["market"] or "UNKNOWN", []).append(c)
    stamps = []
    for market, rows in by_market.items():
        scores = [r["score"] for r in rows if r["score"] is not None]
        if len(scores) >= POOL_SCORE_SATURATION_MIN and max(scores) - min(scores) < 1e-9:
            stamps.append({"market": market, "stamp": "score_saturation",
                           "severity": "fail", "n": len(scores), "value": scores[0]})
        vols = [r["volume_ratio"] for r in rows if r["volume_ratio"] is not None]
        if len(vols) >= POOL_VOLUME_CONST_MIN and max(vols) - min(vols) < 1e-12:
            stamps.append({"market": market, "stamp": "volume_ratio_constant",
                           "severity": "fail", "n": len(vols), "value": vols[0]})
        dists = [r["dist_low"] for r in rows if r["dist_low"] is not None]
        if dists:
            share = sum(1 for x in dists if x < SAME_FACTOR_DIST) / len(dists)
            if share > 0.60:
                stamps.append({"market": market, "stamp": "same_factor_oversold",
                               "severity": "warn", "share": round(share, 4)})
        rets = [r["ret20"] for r in rows if r["ret20"] is not None]
        if rets:
            deep = sum(1 for x in rets if x <= -BARBELL_RET20)
            high = sum(1 for x in rets if x >= BARBELL_RET20)
            share = (deep + high) / len(rets)
            if share > 0.70:
                stamps.append({"market": market, "stamp": "barbell_extremes",
                               "severity": "warn", "share": round(share, 4)})
        if market == "CRYPTO" and (any(s["stamp"] in ("volume_ratio_constant", "score_saturation")
                                      and s["market"] == market for s in stamps)
                                   or any(r["score_method"] == "" for r in rows)):
            stamps.append({"market": "CRYPTO", "stamp": "quota_waste",
                           "severity": "warn", "reason": "evidence invalid"})
    # 去重
    seen, out = set(), []
    for s in stamps:
        key = (s.get("market"), s.get("stamp"), s.get("value"), s.get("share"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def evaluate(cards, asof=None, source=""):
    cards = list(cards or [])
    results = [evaluate_card(c, asof) for c in cards]
    stamps = pool_stamps(cards)
    fails = sum(1 for r in results if r["surface_status"] == "fail")
    unknowns = sum(1 for r in results if r["surface_status"] == "unknown")
    pool_fail = any(s.get("severity") == "fail" for s in stamps)
    if fails or pool_fail:
        status = "fail"
    elif unknowns:
        status = "unknown"
    else:
        status = "pass"
    return {
        "schema": SCHEMA,
        "gate_version": GATE_VERSION,
        "readonly": True,
        "side_effects": [],
        "asof": asof,
        "source": source,
        "summary": {"cards": len(cards), "pass": len(cards) - fails - unknowns,
                    "fail": fails, "unknown": unknowns,
                    "pool_stamps": len(stamps), "status": status},
        "cards": results,
        "pool_stamps": stamps,
        "notes": [
            "只做生成层事实门；不选票、不代签、不改 reviewed/handoff、不写账本。",
            "缺失字段显式 unknown，不伪造 pass；池级 stamp 可 fail/warn。",
            "G1 三买几何、G2 时效、G3 同源、G4 生理区间、G5 公司行动。",
        ],
    }


def render_md(rep):
    s = rep["summary"]
    lines = ["# 候选池生成层门 · %s" % GATE_VERSION, "",
             "> 只读：不选票/不代签/不改 reviewed/handoff/不写账本", "",
             "## 汇总", "",
             "| cards | pass | fail | unknown | pool_stamps | status |",
             "|---:|---:|---:|---:|---:|---|",
             "| %d | %d | %d | %d | %d | **%s** |" % (
                 s["cards"], s["pass"], s["fail"], s["unknown"], s["pool_stamps"],
                 s["status"]), ""]
    if rep["pool_stamps"]:
        lines += ["## 池级戳", ""]
        for x in rep["pool_stamps"]:
            lines.append("- **%s** %s：%s" % (x.get("stamp"), x.get("market"),
                                             {k: v for k, v in x.items() if k not in ("stamp", "market")}))
        lines.append("")
    lines += ["## 逐卡", "", "| card | market | status | 失败/未知项 |", "|---|---|---|---|"]
    for c in rep["cards"]:
        bad = [x for x in c["checks"] if x["status"] != "pass"]
        detail = "; ".join("%s:%s:%s" % (x["gate"], x["status"], x["reason"]) for x in bad) or "全部 pass"
        lines.append("| %s | %s | %s | %s |" % (c["candidate_id"], c["market"], c["surface_status"], detail))
    lines += ["", "## 边界", "",
              "本报告是生成层事实核对，不是通过/否决建议；[ ] 仍由 ACAD1 人工复核。"]
    return "\n".join(lines) + "\n"


def load_cards(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".jsonl":
        return [json.loads(x) for x in text.splitlines() if x.strip()]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("cards", "items", "candidates", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    raise ValueError("unsupported input shape: %s" % type(data).__name__)


def main(argv=None):
    ap = argparse.ArgumentParser(description="候选池生成层只读门")
    ap.add_argument("--input", nargs="+", required=True, help="卡片 JSON/JSONL")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    cards = []
    try:
        for p in args.input:
            cards.extend(load_cards(p))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "input_error: %s" % exc}, ensure_ascii=False))
        return 2
    rep = evaluate(cards, asof=args.asof, source=",".join(args.input))
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if args.json or not args.markdown:
        print(text)
    if args.markdown:
        print(render_md(rep))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0 if rep["summary"]["status"] == "pass" else (1 if rep["summary"]["status"] == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
