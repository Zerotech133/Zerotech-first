# -*- coding: utf-8 -*-
"""risk_bridge.py —— RiskGuard(zerobound-kg) ↔ 多策略链路(strategy_layer) 数据桥。

L4 解耦设计 §3 第 1 步（2026-10-17，见 deliverables/RiskGuard_L4解耦设计_20261017.md）：

  · 跨仓零 import 耦合：只**读文件**——影子账本 ledger.jsonl（K-STOCK-401
    协作协议 v0 的产物）+ 当日四态快照 CSV。不写任何 RiskGuard 侧文件。
  · 影子先行：只提炼水位供展示/研判，绝不改写 signals_combined，
    绝不参与拦截决策（enforce 升级须 10-20 满窗裁决拍板）。
  · 松耦合降级：账本/快照缺失或损坏 → 返回空上下文（available=False），
    上游（dashboard_api）据此隐藏风控卡，绝不抛异常拖垮页面。

数据契约（实探 2026-10-17）：
  账本行: {"ts", "rule_id"(R1-market-quality/R2-liquidity/R3-event-risk/
          R4-position-concentration), "action"(pass/shadow_block/block),
          "mode"(shadow/enforce), "score", "security", "reason", ...}
  快照列: security,state,n_sup,n_con,density,density_h1,density_h2,
          n_hyp,basis（UTF-8 BOM；state ∈ 酝酿/爆发/衰竭/萧条）
"""
import csv
import json
import os
import re

# ASCII junction 路径（判例：调度链与跨仓读路径一律 ASCII）
LEDGER_PATH = os.environ.get(
    "RG_LEDGER",
    r"C:\aios\zerobound-kg\riskguard_ledger\ledger.jsonl")
SNAPSHOT_GLOB = os.environ.get(
    "RG_SNAPSHOT_GLOB",
    r"C:\aios\zerobound-kg\docs\planning\z4-fourstate-snapshot-*.csv")

TAIL_LINES = 20000          # 账本尾部读取行数（全账本 ~32k 行、9MB）
R3_SHOW = 8                 # R3 明细展示条数
EXHAUSTED_SHOW = 30         # 衰竭标的展示条数
_R_MARKET = re.compile(r"市场级闸")


def _tail_lines(path, n=TAIL_LINES):
    """读文件尾部 n 行；缺失/损坏返回 []（降级不抛）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(deque_safe(f, n))
    except OSError:
        return []
    except Exception:  # noqa: BLE001 — 任何损坏一律降级空
        return []


def deque_safe(f, n):
    """流式保留尾部 n 行（避免 9MB 全量驻留）。"""
    from collections import deque
    d = deque(maxlen=n)
    for line in f:
        if line.strip():
            d.append(line)
    return d


def _parse(rows):
    out = []
    for line in rows:
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001 — 坏行跳过
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def _agg_rule(rows, rule_prefix):
    """按 rule_id 前缀聚合最近批次：计数 + 最新 ts + 最新市场级占比。"""
    agg = {"pass": 0, "block": 0, "ts": "", "last_score": None,
           "market_gate": None}
    for r in rows:
        rid = r.get("rule_id") or ""
        if not rid.startswith(rule_prefix):
            continue
        if "block" in (r.get("action") or ""):
            agg["block"] += 1
        else:
            agg["pass"] += 1
        ts = r.get("ts") or ""
        if ts > agg["ts"]:
            agg["ts"] = ts
            if isinstance(r.get("score"), (int, float)):
                agg["last_score"] = round(r["score"], 4)
            if rule_prefix.startswith("R1") and _R_MARKET.search(
                    r.get("reason") or ""):
                agg["market_gate"] = {
                    "score": agg["last_score"],
                    "action": r.get("action"),
                    "reason": (r.get("reason") or "")[:120],
                }
    return agg


def _latest_snapshot():
    """最新一份四态快照 CSV → (日期 yyyymmdd, 衰竭标的清单)。"""
    import glob as _glob
    files = sorted(_glob.glob(SNAPSHOT_GLOB))
    if not files:
        return None, []
    path = files[-1]
    m = re.search(r"z4-fourstate-snapshot-(\d{8})\.csv$", path)
    day = m.group(1) if m else None
    exhausted = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("state") or "").strip() == "衰竭":
                    exhausted.append({
                        "security": row.get("security"),
                        "density": row.get("density"),
                        "n_sup": _int(row.get("n_sup")),
                        "n_con": _int(row.get("n_con")),
                    })
    except OSError:
        return day, []
    return day, exhausted


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def risk_context():
    """组装只读风控上下文（dashboard_api GET /api/risk 的数据源）。

    返回 dict；available=False 表示 RiskGuard 侧数据不可达（迁移/清库/
    路径漂移），上游隐藏风控卡而非报错。
    """
    rows = _parse(_tail_lines(LEDGER_PATH))
    r1 = _agg_rule(rows, "R1-market-quality")
    r2 = _agg_rule(rows, "R2-liquidity")
    r3 = _agg_rule(rows, "R3-event-risk")
    r4 = _agg_rule(rows, "R4-position-concentration")
    snap_day, exhausted = _latest_snapshot()
    has_data = bool(rows)
    return {
        "available": has_data,
        "ledger": LEDGER_PATH if has_data else None,
        "tail_lines": len(rows),
        "rules": {"R1": r1, "R2": r2, "R3": r3, "R4": r4},
        "market_gate": r1.get("market_gate"),
        "snapshot": {
            "day": snap_day,
            "exhausted_n": len(exhausted),
            "exhausted": exhausted[:EXHAUSTED_SHOW],
        },
        "note": "影子语义：只记不拦；enforce 须 GUARD_KEYS 全校准+|t|>=3"
                "对账+拍板（10-20 满窗裁决）",
    }


if __name__ == "__main__":
    print(json.dumps(risk_context(), ensure_ascii=False, indent=1)[:1500])
