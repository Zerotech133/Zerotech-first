#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W3 P0-3 预检：D1 outcome 回写 + EvidenceBundle raw-ref 首跑验证（只读）。

对应任务：`task-17`（P0-3 D1 outcome/raw-ref 预检，2026-10-31）。
背景：最新可判行情 = 2026-10-30；2026-10-30 的 41 条裁决需 2026-11-02 收盘才能算
T+1 outcome。本脚本只做**输入可用性/口径/API 预检**，不写任何生产账本、不伪造 outcome。

只读红线（脚本自身保证）：
- 只读 `shadow_ledger.jsonl` / `decision_outcomes.jsonl` / 本地 parquet / registry json；
- 不调用 `agent_platform/decision_outcomes.py` 的 main（其默认会写 sidecar），
  只 import 其纯函数（compute_outcome/first_next_bar/DIRECTION）做离线模拟；
- 不联网（价格用本地 `quant_backtest/data/ashare/*.parquet` 回退源，等价于生产
  writer 在腾讯接口失败时的回退路径）；
- 运行前后自校验两个账本 sha256 不变。

退出码：0=READY（2026-11-02 数据已到，可执行回写）；2=BLOCKED（数据未到，预期）；1=预检自身错误。

用法::

    python -X utf8 research/w3_outcome_precheck_20261031.py                # 预检（人类可读）
    python -X utf8 research/w3_outcome_precheck_20261031.py --json         # 预检 JSON
    python -X utf8 research/w3_outcome_precheck_20261031.py --verify-post  # 11-02 回写后验收
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLATFORM = REPO / "agent_platform"
DATA = PLATFORM / "data"
LEDGER = DATA / "shadow" / "shadow_ledger.jsonl"
OUTCOMES = DATA / "shadow" / "decision_outcomes.jsonl"
L4_STATUS = DATA / "xsec" / "l4_status.json"
OUTCOME_WRITER = PLATFORM / "decision_outcomes.py"
DECISION_POLICY = PLATFORM / "engine" / "decision_policy.py"
EVIDENCE_BUNDLE = PLATFORM / "engine" / "evidence_bundle.py"
RUN_SHADOW = PLATFORM / "run_shadow.py"
TDX_DAILY = REPO / "quant_backtest" / "data" / "tdx" / "ts_daily"
ASHARE = REPO / "quant_backtest" / "data" / "ashare"

TARGET_DATE = "2026-10-30"      # 最新可判裁决日
NEXT_DATE = "2026-11-02"        # 产出 T+1 outcome 所需的下一个交易日收盘
BASKET = ("600519", "000858", "601318", "300750", "600036")


# ---------------------------------------------------------------- 小工具
def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _load_jsonl(path: Path) -> list:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _norm_date(value) -> str:
    """归一化交易日期为 YYYYMMDD（容忍 float/NaN/ISO 混杂）。"""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return str(int(value))
    text = str(value).strip().replace("-", "").replace("/", "")
    return text[:8]


def _iso_date(yyyymmdd: str) -> str:
    text = _norm_date(yyyymmdd)
    if len(text) != 8:
        return text
    return "%s-%s-%s" % (text[:4], text[4:6], text[6:])


def _suffix(code: str) -> str:
    if str(code).startswith("6"):
        return "SH"
    if str(code).startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


# ---------------------------------------------------------------- 行情输入
def ashare_last(code: str) -> dict:
    """生产 writer 的本地回退源：quant_backtest/data/ashare/<code>_<suffix>.parquet。"""
    path = ASHARE / ("%s_%s.parquet" % (code, _suffix(code)))
    info = {"code": code, "path": str(path), "exists": path.is_file(),
            "rows": 0, "last_date": "", "last_close": None,
            "has_next_date": False}
    if not info["exists"]:
        return info
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["trade_date", "close"])
    except Exception as exc:  # noqa: BLE001 - 预检不因单票坏文件中断
        info["error"] = str(exc)
        return info
    dates = [_norm_date(v) for v in df["trade_date"].tolist()]
    valid = [(d, float(c)) for d, c in zip(dates, df["close"].tolist()) if d]
    if not valid:
        return info
    valid.sort()
    info.update(rows=len(valid), last_date=_iso_date(valid[-1][0]),
                last_close=round(valid[-1][1], 4),
                has_next_date=any(d >= _norm_date(NEXT_DATE) for d, _ in valid))
    return info


def ashare_bars(code: str, n: int = 60) -> list:
    """与 production `_local_kline` 同形：[[YYYY-MM-DD, open, close, high, low, vol], ...]。"""
    path = ASHARE / ("%s_%s.parquet" % (code, _suffix(code)))
    if not path.is_file():
        return []
    try:
        import pandas as pd

        df = pd.read_parquet(path).sort_values("trade_date").tail(int(n))
    except Exception:  # noqa: BLE001
        return []
    bars = []
    for _, row in df.iterrows():
        date = _iso_date(_norm_date(row["trade_date"]))
        if not date:
            continue
        try:
            bars.append([date, float(row["open"]), float(row["close"]),
                         float(row["high"]), float(row["low"]),
                         float(row["volume"])])
        except (TypeError, ValueError):
            continue
    return bars


def tdx_daily_last() -> dict:
    """研究账数据源：quant_backtest/data/tdx/ts_daily（冻结版本 tdx-parquet@2026-10-28）。"""
    info = {"path": str(TDX_DAILY), "parts": 0, "global_latest": "",
            "basket": {}, "has_next_date": False}
    if not TDX_DAILY.is_dir():
        return info
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
        return info
    wanted = {"%s.%s" % (code, _suffix(code)): code for code in BASKET}
    basket_last = {}
    global_latest = ""
    parts = sorted(TDX_DAILY.glob("*.parquet"))
    for part in parts:
        try:
            df = pd.read_parquet(part, columns=["ts_code", "trade_date", "close"])
        except Exception:  # noqa: BLE001
            continue
        dates = [_norm_date(v) for v in df["trade_date"].tolist()]
        if dates:
            latest = max(dates)
            if latest > global_latest:
                global_latest = latest
        sub = df[df["ts_code"].isin(wanted)]
        for _, row in sub.iterrows():
            code = wanted.get(str(row["ts_code"]))
            date = _norm_date(row["trade_date"])
            if not code or not date:
                continue
            prev = basket_last.get(code)
            if prev is None or date > prev[0]:
                basket_last[code] = (date, float(row["close"]))
    info["parts"] = len(parts)
    info["global_latest"] = _iso_date(global_latest)
    info["has_next_date"] = global_latest >= _norm_date(NEXT_DATE)
    info["basket"] = {
        code: {"last_date": _iso_date(basket_last[code][0]),
               "last_close": round(basket_last[code][1], 4),
               "has_next_date": basket_last[code][0] >= _norm_date(NEXT_DATE)}
        for code in BASKET if code in basket_last
    }
    return info


# ---------------------------------------------------------------- 生产模块（只 import 纯函数）
def _load_production_modules():
    for path in (str(PLATFORM), str(PLATFORM / "engine")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import decision_outcomes as writer  # noqa: E402
    import decision_policy as policy  # noqa: E402

    return writer, policy


# ---------------------------------------------------------------- 检查项
def check_data(writer) -> dict:
    ashare = {code: ashare_last(code) for code in BASKET}
    tdx = tdx_daily_last()
    latest_ashare = max((v["last_date"] for v in ashare.values() if v["last_date"]),
                        default="")
    latest_tdx = tdx.get("global_latest") or ""
    ready = (all(v["has_next_date"] for v in ashare.values())
             and bool(tdx.get("has_next_date")))
    result = {
        "target_decision_date": TARGET_DATE,
        "required_next_trade_date": NEXT_DATE,
        "latest_trade_date": {"ashare_local": latest_ashare, "tdx_daily": latest_tdx},
        "ashare_local_fallback": {"root": str(ASHARE), "symbols": ashare,
                                  "all_have_next_date":
                                      all(v["has_next_date"] for v in ashare.values())},
        "tdx_daily": tdx,
        "ready": ready,
    }
    if not ready:
        result["blocked_by"] = "%s_close" % NEXT_DATE
        result["reason"] = ("最新行情=%s/%s，缺 %s 收盘；无法计算 T+1 outcome"
                            % (latest_ashare, latest_tdx, NEXT_DATE))
    return result


def check_targets(writer, policy) -> dict:
    ledger = _load_jsonl(LEDGER)
    outcomes = _load_jsonl(OUTCOMES)
    done_run_ids = {str(row.get("run_id")) for row in outcomes
                    if row.get("run_id")}
    target_rows = [row for row in ledger
                   if _norm_date(row.get("date")) == _norm_date(TARGET_DATE)
                   and row.get("status") == "done"
                   and row.get("run_id")
                   and str(row.get("run_id")) not in done_run_ids]
    failed_rows = [row for row in ledger
                   if _norm_date(row.get("date")) == _norm_date(TARGET_DATE)
                   and row.get("status") != "done"]
    targets = []
    for row in target_rows:
        targets.append({
            "decision_id": policy.decision_id(str(row.get("symbol") or ""),
                                              TARGET_DATE,
                                              str(row.get("run_id") or "")),
            "run_id": str(row.get("run_id") or ""),
            "date": TARGET_DATE,
            "symbol": str(row.get("symbol") or ""),
            "recommendation": row.get("recommendation"),
            "confidence": row.get("confidence"),
            "price_at_decision": row.get("price_at_decision"),
            "model": row.get("model"),
            "fallback": row.get("fallback"),
            "expected_next_trade_date": NEXT_DATE,
        })
    # 用生产纯函数 + 本地行情模拟：10-30 行现应全部算不出（无 11-02 bar）
    simulated = []
    for row in target_rows:
        try:
            outcome = writer.compute_outcome(row, ashare_bars)
        except Exception:  # noqa: BLE001 - 预检不因单行中断
            outcome = None
        simulated.append(outcome is not None)
    return {
        "ledger_rows": len(ledger),
        "ledger_max_date": max((_iso_date(_norm_date(r.get("date"))) for r in ledger),
                               default=""),
        "target_date_done_rows": len(target_rows),
        "target_date_failed_rows": len(failed_rows),
        "targets": targets,
        "computed_now": sum(1 for ok in simulated if ok),
        "not_computable_now": sum(1 for ok in simulated if not ok),
    }


def check_writer_api(writer) -> dict:
    source = OUTCOME_WRITER.read_text(encoding="utf-8", errors="replace")
    return {
        "file": str(OUTCOME_WRITER),
        "entry": "python -X utf8 agent_platform/decision_outcomes.py "
                 "[--no-write] [--json] [--ledger <path>] [--out <path>]",
        "functions": ["compute_outcome", "compute_all", "first_next_bar",
                      "summarize", "render_markdown"],
        "default_writes_sidecar": True,
        "has_no_write_flag": "--no-write" in source,
        "has_decision_id_field": "decision_id" in source,
        "price_source": {
            "primary": "engine/tools.kline_daily -> 腾讯 web.ifzq.gtimg.cn 前复权（联网）",
            "fallback": "_local_kline -> quant_backtest/data/ashare/<code>_<SUFFIX>.parquet",
        },
        "output_fields": ["ts", "run_id", "date", "symbol", "recommendation",
                          "confidence", "price_at_decision", "next_trade_date",
                          "next_close", "next_day_return", "hit",
                          "horizon_days", "model", "fallback"],
        "gaps": [
            "writer 不写 decision_id（W3 要求字段）；需 11-02 后按 run_id/symbol/date "
            "复算 join，或由 owner 另单补 writer（本预检不改生产代码）",
            "WATCH/HOLD 无方向 -> hit=null；hit+model 同非空需带方向裁决（当前无）",
            "默认可写 sidecar，11-02 首跑必须先 --no-write 预演再写",
        ],
        "hit_rule": {
            "source": "agent_platform/decision_outcomes.py::DIRECTION",
            "direction_map": dict(writer.DIRECTION),
            "rule": "BUY/STRONG_BUY: hit=1 iff next_day_return>0 else 0; "
                    "SELL/STRONG_SELL: hit=1 iff next_day_return<0 else 0; "
                    "WATCH/HOLD: hit=null; horizon_days=1",
        },
    }


def check_model_join(writer) -> dict:
    ledger = _load_jsonl(LEDGER)
    outcomes = _load_jsonl(OUTCOMES)
    target_rows = [row for row in ledger
                   if _norm_date(row.get("date")) == _norm_date(TARGET_DATE)
                   and row.get("status") == "done" and row.get("run_id")]
    existing_model = sum(1 for row in outcomes if row.get("model"))
    existing_hit = sum(1 for row in outcomes if row.get("hit") is not None)
    existing_both = sum(1 for row in outcomes
                        if row.get("hit") is not None and row.get("model"))
    targets_with_model = sum(1 for row in target_rows if row.get("model"))
    return {
        "join_path": "shadow_ledger row.model -> writer compute_outcome 的 "
                     "row.get('model') -> outcome row.model（按 run_id 同源）",
        "existing_outcome_rows": len(outcomes),
        "existing_model_nonnull": existing_model,
        "existing_hit_nonnull": existing_hit,
        "existing_hit_and_model_nonnull": existing_both,
        "target_date_done_rows": len(target_rows),
        "targets_with_model": targets_with_model,
        "expected_after_run": {
            "outcome_rows": len(outcomes) + len(target_rows),
            "model_nonnull": existing_model + targets_with_model,
            "hit_nonnull": existing_hit,
            "hit_and_model_nonnull": existing_both,
            "note": "10-30 裁决全 WATCH（无方向）-> 新增行 hit 仍为 null；"
                    "W3 验收『hit+model 同非空』不会因 11-02 回写而满足",
        },
    }


def check_sanity(writer) -> dict:
    """用生产纯函数 + 本地行情复算 10-29 已落账 outcome，验证数据/口径一致。"""
    ledger = _load_jsonl(LEDGER)
    recorded = {str(row.get("run_id")): row for row in _load_jsonl(OUTCOMES)
                if row.get("run_id")}
    checked = 0
    mismatches = []
    for row in ledger:
        if _norm_date(row.get("date")) != _norm_date("2026-10-29"):
            continue
        if row.get("status") != "done":
            continue
        rec = recorded.get(str(row.get("run_id")))
        if rec is None:
            continue
        try:
            sim = writer.compute_outcome(row, ashare_bars)
        except Exception as exc:  # noqa: BLE001
            sim = None
            mismatches.append({"symbol": row.get("symbol"),
                               "error": str(exc)})
            continue
        checked += 1
        same = (sim is not None
                and abs(float(sim["next_close"]) - float(rec["next_close"])) < 1e-6
                and sim["hit"] == rec["hit"])
        if not same:
            mismatches.append({
                "run_id": row.get("run_id"), "symbol": row.get("symbol"),
                "sim": sim, "recorded": rec,
            })
    return {"decision_date": "2026-10-29", "next_date": "2026-10-30",
            "checked": checked, "mismatches": mismatches,
            "consistent": not mismatches}


def check_evidence_bundle() -> dict:
    bundle_source = EVIDENCE_BUNDLE.read_text(encoding="utf-8", errors="replace")
    shadow_source = RUN_SHADOW.read_text(encoding="utf-8", errors="replace")
    ledger = _load_jsonl(LEDGER)
    post_wiring = [row for row in ledger if "decision_id" in row]
    latest = max(ledger, key=lambda r: _norm_date(r.get("date")), default={})
    refs = []
    for row in post_wiring:
        for ref in row.get("evidence_refs") or []:
            if isinstance(ref, dict):
                refs.append(ref)
    raw_refs = [ref for ref in refs
                if not str(ref.get("uri") or "").startswith("make_context://")]
    # 接线提交（只读 git log，失败不阻塞预检）
    try:
        wiring = subprocess.run(
            ["git", "log", "--format=%h %cI %s", "-4", "--",
             str(RUN_SHADOW.relative_to(REPO))],
            cwd=str(REPO), capture_output=True, text=True, timeout=20)
        wiring_lines = [line for line in wiring.stdout.splitlines() if line]
    except Exception:  # noqa: BLE001
        wiring_lines = []
    return {
        "status": "wired_pending_first_run",
        "wiring_commits": wiring_lines,
        "run_shadow_imports": {
            "evidence_bundle": "from evidence_bundle import EvidenceBundle" in shadow_source,
            "decision_policy": "from decision_policy import apply_policy" in shadow_source,
        },
        "ledger_rows_total": len(ledger),
        "ledger_rows_post_wiring": len(post_wiring),
        "ledger_latest_date": _iso_date(_norm_date(latest.get("date"))),
        "refs_current": {
            "uri_template": "make_context://<symbol>/<segment>",
            "hash": "sha256(摘要文本)[:16]",
            "raw_ref": bool(raw_refs),
            "note": "EvidenceBundle 当前 ref 是摘要自证，不是 raw K 线/事件/L4 引用",
        },
        "first_run_assertions": [
            "新行（date=%s）必须含 decision_id/watch_reason/evidence_refs/"
            "policy_checks/evidence_sufficient" % NEXT_DATE,
            "evidence_refs 必须可定位 raw K 线/事件/L4（当前实现会 FAIL）",
            "WATCH 必有 watch_reason（l4_unresolved/upstream_l4_missing/model_watch）",
        ],
    }


def check_l4() -> dict:
    payload = {}
    if L4_STATUS.is_file():
        try:
            payload = json.loads(L4_STATUS.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
    symbols = payload.get("symbols") or {}
    unresolved = {code: info.get("status") for code, info in symbols.items()
                  if str(info.get("status")) in ("missing", "unresolved")}
    return {
        "path": str(L4_STATUS),
        "generated_at": payload.get("generated_at"),
        "symbols": {code: {"status": info.get("status"),
                           "verdict": info.get("verdict"),
                           "source": info.get("source"),
                           "as_of": info.get("as_of")}
                    for code, info in symbols.items()},
        "all_unresolved": bool(symbols) and len(unresolved) == len(symbols),
        "effect": "engine/decision_policy.py l4_lock：L4=missing/unresolved 时 "
                  "BUY/SELL 强制降为 WATCH -> 后续裁决仍无方向 -> hit 仍 null",
    }


# ---------------------------------------------------------------- 汇总
def run_precheck() -> dict:
    writer, policy = _load_production_modules()
    before = {"ledger": _sha256(LEDGER), "outcomes": _sha256(OUTCOMES)}
    data = check_data(writer)
    targets = check_targets(writer, policy)
    writer_api = check_writer_api(writer)
    model_join = check_model_join(writer)
    sanity = check_sanity(writer)
    evidence = check_evidence_bundle()
    l4 = check_l4()
    after = {"ledger": _sha256(LEDGER), "outcomes": _sha256(OUTCOMES)}
    blockers = []
    if not data["ready"]:
        blockers.append({
            "id": "blocked_by=%s_close" % NEXT_DATE,
            "severity": "primary(time)",
            "detail": data.get("reason", ""),
            "unblock_condition": "本地 ashare/tdx 出现 %s 收盘（5 票齐全）" % NEXT_DATE,
        })
    if l4.get("all_unresolved"):
        blockers.append({
            "id": "l4_unresolved",
            "severity": "structural",
            "detail": "L4 全 unresolved -> policy l4_lock -> BUY/SELL 被降 WATCH -> "
                      "hit 保持 null；即使 11-02 回写也不满足 hit+model 同非空",
            "unblock_condition": "L4 复核为 established，或历史带方向裁决可回填 model"
                                 "（当前不可回填）",
        })
    if not writer_api.get("has_decision_id_field"):
        blockers.append({
            "id": "writer_missing_decision_id",
            "severity": "data-contract",
            "detail": "decision_outcomes writer 不写 decision_id；11-02 新行需按 "
                      "run_id/symbol/date 复算 join",
        })
    if not evidence["refs_current"]["raw_ref"]:
        blockers.append({
            "id": "raw_ref_still_summary_hash",
            "severity": "evidence",
            "detail": "EvidenceBundle refs 仍是 make_context:// 摘要 hash，非 raw K 线/"
                      "事件/L4；接线首跑后需单独验证并整改",
        })
    ready = data["ready"] and not [b for b in blockers
                                   if b["severity"] == "primary(time)"]
    return {
        "schema": "w3_outcome_precheck/v1",
        "task": "task-17 P0-3 D1 outcome/raw-ref 预检",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "files_unchanged": before == after,
        "file_sha256": {"ledger": before["ledger"][:16],
                        "outcomes": before["outcomes"][:16]},
        "readiness": {
            "status": "READY" if ready else "BLOCKED",
            "blocked_by": None if ready else "blocked_by=%s_close" % NEXT_DATE,
        },
        "data_availability": data,
        "outcome_targets": targets,
        "hit_rule": writer_api["hit_rule"],
        "model_join": model_join,
        "writer_api": writer_api,
        "validation": {
            "sanity_10_29_to_10_30": sanity,
            "simulate_10_30_now": {
                "computed": targets["computed_now"],
                "not_computable": targets["not_computable_now"],
                "reason": "缺 %s 收盘 bar（first_next_bar 返回 None）" % NEXT_DATE,
            },
        },
        "evidence_bundle": evidence,
        "l4": l4,
        "blockers": blockers,
        "next_steps": [
            {"step": 1, "when": "%s_close 后" % NEXT_DATE,
             "owner": "DATA1/行情源",
             "action": "确认 ashare（回退源）+ tdx 出现 %s 日线（5 票齐全）" % NEXT_DATE,
             "command": "python -X utf8 research/w3_outcome_precheck_20261031.py --json",
             "guard": "退出码 0=READY；2=仍 BLOCKED"},
            {"step": 2, "when": "READY 后",
             "owner": "D1(鲁肃)",
             "action": "写账本前 dry-run（不落盘）",
             "command": "python -X utf8 agent_platform/decision_outcomes.py --no-write",
             "guard": "new=N（预期 %d），sidecar 文件 sha 不变" % targets["target_date_done_rows"]},
            {"step": 3, "when": "dry-run 复核通过",
             "owner": "D1(鲁肃) + DSH-Lead(写入批准)",
             "action": "正式追加 sidecar outcome（唯一写动作）",
             "command": "python -X utf8 agent_platform/decision_outcomes.py",
             "guard": "仅 append decision_outcomes.jsonl；不改 shadow_ledger、不改裁决"},
            {"step": 4, "when": "写入后",
             "owner": "W3/W5 复核",
             "action": "验证 outcome 行字段（model 非空/hit 口径/decision_id join）",
             "command": None,
             "guard": "hit+model 同非空仍为 0（WATCH 无方向），不伪造 hit"},
            {"step": 5, "when": "11-02 首跑 D1 后",
             "owner": "D1/研发",
             "action": "EvidenceBundle 首跑断言（decision_id/watch_reason/"
                       "evidence_refs/policy_checks 落账；raw ref 另行整改）",
             "command": None,
             "guard": "当前 refs 为 make_context 摘要 hash -> raw-ref 断言预期 FAIL"},
        ],
    }


# ---------------------------------------------------------------- 11-02 写后验证
def _outcome_index(rows: list) -> dict:
    return {str(row.get("run_id")): row for row in rows if row.get("run_id")}


def _ashare_close_on(code: str, date: str):
    for bar in ashare_bars(code, 240):
        if _norm_date(bar[0]) == _norm_date(date):
            return float(bar[2])
    return None


def verify_post() -> dict:
    """11-02 回写后的只读验收：行情对齐/hit 口径/model join/无伪造/EvidenceBundle 首跑断言。

    退出语义：PASS（必检项全过）、FAIL（必检项失败）、PENDING（行情未到，仍阻塞）。
    已知设计缺口（required=False）：writer 不落 decision_id、refs 仍摘要 hash。
    """
    writer, policy = _load_production_modules()
    report = run_precheck()
    details = []

    def note(check_id, ok, detail, required=True):
        details.append({"check": check_id, "ok": bool(ok),
                        "required": bool(required), "detail": str(detail)})

    if report["readiness"]["status"] != "READY":
        return {
            "schema": "w3_outcome_postcheck/v1",
            "status": "PENDING",
            "blocked_by": report["readiness"].get("blocked_by"),
            "reason": "行情未到，无法验证回写；先等 %s 收盘后重跑" % NEXT_DATE,
            "details": details,
        }

    ledger = _load_jsonl(LEDGER)
    outcomes = _load_jsonl(OUTCOMES)
    ledger_rows = [row for row in ledger
                   if _norm_date(row.get("date")) == _norm_date(TARGET_DATE)
                   and row.get("status") == "done" and row.get("run_id")]
    ledger_by_run = {str(row.get("run_id")): row for row in ledger_rows}
    outcome_by_run = _outcome_index(outcomes)
    missing = [str(row.get("run_id")) for row in ledger_rows
               if str(row.get("run_id")) not in outcome_by_run]
    note("outcome_coverage", not missing,
         "10-30 裁决缺 outcome: %d 条 %s" % (len(missing), missing[:5]))

    new_rows = [outcome_by_run[str(row.get("run_id"))] for row in ledger_rows
                if str(row.get("run_id")) in outcome_by_run]
    bad_date, bad_close, bad_hit = [], [], []
    bad_model, bad_price, bad_ts_order = [], [], []
    for row in new_rows:
        run_id = str(row.get("run_id"))
        ledger_row = ledger_by_run.get(run_id) or {}
        if _iso_date(_norm_date(row.get("next_trade_date"))) != NEXT_DATE:
            bad_date.append(run_id)
        expected_close = _ashare_close_on(str(row.get("symbol") or ""), NEXT_DATE)
        actual_close = row.get("next_close")
        if (expected_close is None or actual_close is None
                or abs(float(actual_close) - expected_close) > 1e-6):
            bad_close.append({"run_id": run_id, "symbol": row.get("symbol"),
                              "outcome_close": actual_close,
                              "local_close": expected_close})
        # hit 口径独立复算（只依赖 recommendation/next_day_return）
        ret = row.get("next_day_return")
        recommendation = str(row.get("recommendation") or "").upper()
        direction = writer.DIRECTION.get(recommendation)
        expected_hit = None
        if direction is not None and isinstance(ret, (int, float)):
            expected_hit = 1 if (ret > 0 if direction > 0 else ret < 0) else 0
        if row.get("hit") != expected_hit:
            bad_hit.append({"run_id": run_id, "hit": row.get("hit"),
                            "expected": expected_hit})
        if not row.get("model"):
            bad_model.append(run_id)
        # 无伪造：outcome 的裁决价必须等于原 ledger 裁决价
        try:
            if abs(float(row.get("price_at_decision"))
                   - float(ledger_row.get("price_at_decision"))) > 1e-9:
                bad_price.append(run_id)
        except (TypeError, ValueError):
            bad_price.append(run_id)
        if row.get("ts") is not None and ledger_row.get("ts") is not None:
            try:
                if float(row["ts"]) < float(ledger_row["ts"]):
                    bad_ts_order.append(run_id)
            except (TypeError, ValueError):
                pass

    note("next_trade_date", not bad_date,
         "%d 行 next_trade_date != %s" % (len(bad_date), NEXT_DATE))
    note("next_close_local_align", not bad_close,
         "本地回退源对齐失败 %d: %s" % (len(bad_close), bad_close[:3]))
    note("hit_rule_recompute", not bad_hit,
         "hit 口径不符 %d: %s" % (len(bad_hit), bad_hit[:3]))
    note("model_join", not bad_model,
         "model 为空 %d 行（11-02 后 join 应从 ledger.model 带入）" % len(bad_model))
    note("no_fabrication", not bad_price and not bad_ts_order,
         "price 不一致 %d / ts 早于裁决 %d" % (len(bad_price), len(bad_ts_order)))

    decision_ids = {policy.decision_id(str(row.get("symbol") or ""),
                                       TARGET_DATE,
                                       str(row.get("run_id") or ""))
                    for row in ledger_rows}
    note("decision_id_join", len(decision_ids) == len(ledger_rows),
         "复算 decision_id %d/%d（writer 未落该字段，需外部 join 或另单补 writer）"
         % (len(decision_ids), len(ledger_rows)), required=False)
    note("hit_and_model_nonnull",
         any(row.get("hit") is not None and row.get("model") for row in new_rows),
         "新增行 hit+model 同非空=%d（WATCH 无方向 -> 预期 0；W3 验收不因回写满足）"
         % sum(1 for row in new_rows
               if row.get("hit") is not None and row.get("model")),
         required=False)

    next_rows = [row for row in ledger
                 if _norm_date(row.get("date")) >= _norm_date(NEXT_DATE)]
    need_fields = {"decision_id", "watch_reason", "evidence_refs",
                   "policy_checks", "evidence_sufficient"}
    note("evidence_fields", bool(next_rows)
         and all(need_fields <= set(row) for row in next_rows),
         "%s 新行=%d；缺新字段（决策账 EvidenceBundle 首跑验证，非 outcome 必检）"
         % (NEXT_DATE, len(next_rows)), required=False)
    refs = [ref for row in next_rows
            for ref in (row.get("evidence_refs") or [])
            if isinstance(ref, dict)]
    raw_refs = [ref for ref in refs
                if not str(ref.get("uri") or "").startswith("make_context://")]
    note("evidence_raw_ref", bool(refs) and len(raw_refs) == len(refs),
         "refs=%d raw=%d（当前实现 make_context 摘要 hash -> 预期 FAIL，按已知缺口登记）"
         % (len(refs), len(raw_refs)), required=False)
    watch_rows = [row for row in next_rows
                  if str(row.get("recommendation") or "").upper() == "WATCH"]
    note("watch_reason", bool(watch_rows)
         and all(row.get("watch_reason") for row in watch_rows),
         "WATCH=%d 有 reason=%d（EvidenceBundle 首跑验证，非 outcome 必检）"
         % (len(watch_rows), sum(1 for row in watch_rows
                                 if row.get("watch_reason"))), required=False)

    required_fail = [item for item in details
                     if item["required"] and not item["ok"]]
    return {
        "schema": "w3_outcome_postcheck/v1",
        "status": "PASS" if not required_fail else "FAIL",
        "checked_new_outcomes": len(new_rows),
        "required_failures": [item["check"] for item in required_fail],
        "details": details,
    }


def render_human(report: dict) -> str:
    data = report["data_availability"]
    targets = report["outcome_targets"]
    lines = [
        "[W3 outcome 预检] %s" % report["checked_at"],
        "readiness=%s  %s  read_only=%s  files_unchanged=%s"
        % (report["readiness"]["status"],
           report["readiness"].get("blocked_by") or "-",
           report["read_only"], report["files_unchanged"]),
        "行情最新: ashare=%s / tdx=%s ; 需要 %s 收盘"
        % (data["latest_trade_date"]["ashare_local"],
           data["latest_trade_date"]["tdx_daily"],
           data["required_next_trade_date"]),
        "待回写裁决: %d 条（%s, done）; 当前可算=%d"
        % (targets["target_date_done_rows"], TARGET_DATE,
           targets["computed_now"]),
        "首条目标: %s" % (targets["targets"][0]["decision_id"]
                          if targets["targets"] else "-"),
        "sanity(10-29→10-30): checked=%d mismatches=%d"
        % (report["validation"]["sanity_10_29_to_10_30"]["checked"],
           len(report["validation"]["sanity_10_29_to_10_30"]["mismatches"])),
        "model join: 现 model=%d hit=%d 同非空=%d -> 回写后 model=%d hit=%d 同非空=%d"
        % (report["model_join"]["existing_model_nonnull"],
           report["model_join"]["existing_hit_nonnull"],
           report["model_join"]["existing_hit_and_model_nonnull"],
           report["model_join"]["expected_after_run"]["model_nonnull"],
           report["model_join"]["expected_after_run"]["hit_nonnull"],
           report["model_join"]["expected_after_run"]["hit_and_model_nonnull"]),
        "EvidenceBundle: 接线=%s 接线后行=%d refs_raw=%s"
        % (report["evidence_bundle"]["status"],
           report["evidence_bundle"]["ledger_rows_post_wiring"],
           report["evidence_bundle"]["refs_current"]["raw_ref"]),
        "L4 all_unresolved=%s" % report["l4"]["all_unresolved"],
        "blockers:",
    ]
    for blocker in report["blockers"]:
        lines.append("  - [%s] %s: %s"
                     % (blocker["severity"], blocker["id"],
                        blocker["detail"][:120]))
    lines.append("下一步: %s" % (report["next_steps"][0]["command"] if
                                report["next_steps"] else "-"))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="W3 P0-3 D1 outcome/raw-ref 预检（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verify-post", action="store_true",
                    help="11-02 回写后的只读验收（PASS/FAIL/PENDING）")
    args = ap.parse_args(argv)
    if args.verify_post:
        try:
            result = verify_post()
        except Exception as exc:  # noqa: BLE001 - 验收自身错误
            print("[postcheck] 验收自身错误：%s" % exc, file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print("[W3 outcome 写后验收] status=%s %s"
                  % (result.get("status"), result.get("blocked_by") or "-"))
            if result.get("reason"):
                print("  reason:", result["reason"])
            for item in result.get("details") or []:
                print("  - [%s] %s: %s"
                      % ("PASS" if item["ok"] else "FAIL", item["check"],
                         item["detail"]))
        if result.get("status") == "PENDING":
            return 2
        return 0 if result.get("status") == "PASS" else 3
    try:
        report = run_precheck()
    except Exception as exc:  # noqa: BLE001 - 预检自身错误
        print("[precheck] 预检自身错误：%s" % exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_human(report))
    return 0 if report["readiness"]["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
