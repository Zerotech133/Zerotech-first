# -*- coding: utf-8 -*-
"""S4 影子期每日采集：分位/置信/与前向收益相关性（统计口径对齐 council_shadow）。

采集对象：REPLAY/SHADOW 模式下 adapter 的每日 Signal 产出（score=分位、
confidence=置信），落档 JSONL；统计口径与 ``quant_backtest/council_shadow.py``
对齐：
- 前向收益 rN = 信号日收盘 → 第 N 个交易日收盘（N=5/20，%），窗口不足 None；
- 分位四分桶 0-25 / 25-50 / 50-75 / 75-100，各桶 r5/r20 均值 + r5 胜率；
- Pearson r + t 统计（|t|≥2 近似 5% 显著），对 (score, r20) 样本对；
- 转正门槛 N_MIN=30 样本；n<30 只记录不评判（K33/K55 影子纪律）；
- gate 三态：样本积累中（延期） / 显著·建议转正评估（转正） /
  显著反向·建议淘汰 或 不显著·维持影子（详见 shadow_report_template.md）。

纪律：
- 只读外部数据（datamart 只读接口取前向收益），唯一写动作是本策略名下
  的影子档 JSONL（data/shadow_onboarding/<strategy_id>_shadow.jsonl）；
- 采集幂等：幂等键 strategy_id|symbol|trade_date，重复采集跳过（同
  strategy_registry.record_verdicts 幂等纪律）；
- 零 LLM 调用、不碰生产任务、不写任何库。

CLI：
    # 每日采集（REPLAY/SHADOW 模式下由日编排挂载，或手动补采）
    python -m strategy_layer.onboarding.shadow_collect \
        --strategy ma_cross --symbol 600519.SH --date 20261016

    # 影子统计输出（JSON，供 shadow_report_template.md 填充）
    python -m strategy_layer.onboarding.shadow_collect \
        --strategy ma_cross --report
"""

import argparse
import json
import math
import os
import sys
import time
from typing import Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)

from strategy_layer.onboarding.classic_adapter import (  # noqa: E402
    ClassicStrategyAdapter,
)
import logging

# 统计口径常量（对齐 council_shadow.py）
NS = (5, 20)  # 前向收益窗口
BUCKETS = ("0-25", "25-50", "50-75", "75-100")  # score 四分桶
N_MIN = 30  # 转正门槛样本量（K55 口径）
SIG_T = 2.0  # |t|≥2 近似 5% 显著

# 事件帧口径（2026-10-18 拍板定案）：影子档只记 buy/sell 事件帧，
# hold（含 fail-closed 中性 Verdict 映射出的 hold）不入统计样本。
# 依据：①与产线 adapter 信号口径同源（S3 基线即仓位变化日）；
# ②影子统计评估的是「信号的前向收益预测力」，hold 无信号语义；
# ③S4 彩排实测：逐日 hold 落档使 0-25 桶被空仓主导（154/250），
#   Pearson 九成样本来自 hold 行（s4_rehearsal_classic_20261018 发现③）。
# hold 计数保留在 collect_once 返回值（可观测性），报告分母层呈现。
EVENT_ACTIONS = ("buy", "sell")

# 影子档落档目录（本工位名下唯一写点）
SHADOW_DIR = os.path.join(_QB_ROOT, "data", "shadow_onboarding")


def shadow_path(strategy_id: str) -> str:
    """单策略影子档路径。"""
    return os.path.join(SHADOW_DIR, "%s_shadow.jsonl" % strategy_id)


# ---------------------------------------------------------------------- #
# 采集：adapter 信号 → 影子档 JSONL（幂等 append）
# ---------------------------------------------------------------------- #
def collect_once(
    adapter: ClassicStrategyAdapter, trade_date: str, out_path: Optional[str] = None
) -> dict:
    """跑 adapter.score(trade_date) 并幂等落档。返回 {recorded, replayed, ...}。

    行格式：ts / strategy_id / symbol / action / score / confidence /
    trade_date / mode（固定 "SHADOW" 标注影子产出，与产线信号隔离）。

    事件帧口径（拍板定案）：仅 action ∈ EVENT_ACTIONS（buy/sell）落档；
    hold 与中性/失败信号跳过并计入返回值 skipped_non_event（不入统计
    样本，仅作可观测性计数）。Agent propose 的 hold Verdict（含
    fail-closed 中性）经垫片转 Signal 后同样在此收口过滤。
    """
    out_path = out_path or shadow_path(adapter.strategy_id)
    td8 = str(trade_date).replace("-", "")
    seen = _load_keys(out_path)
    try:
        signals = adapter.score(td8) or []
    except Exception as exc:  # adapter 约定不 raise，双保险
        print("[shadow_collect] adapter.score 失败，本轮采集 0 条: %s" % exc)
        signals = []
    recorded = replayed = skipped_non_event = 0
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as f:
        for sig in signals:
            if str(getattr(sig, "action", "")).lower() not in EVENT_ACTIONS:
                skipped_non_event += 1
                continue
            key = "%s|%s|%s" % (adapter.strategy_id, sig.symbol, td8)
            if key in seen:
                replayed += 1
                continue
            row = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "strategy_id": adapter.strategy_id,
                "symbol": sig.symbol,
                "action": sig.action,
                "score": float(sig.score),
                "confidence": float(sig.confidence),
                "trade_date": td8,
                "mode": "SHADOW",
                "shadow_key": key,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            seen.add(key)
            recorded += 1
    return {
        "recorded": recorded,
        "replayed": replayed,
        "skipped_non_event": skipped_non_event,
        "path": out_path,
    }


def _load_keys(path: str) -> set:
    keys = set()
    if not os.path.exists(path):
        return keys
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    k = json.loads(line).get("shadow_key")
                    if k:
                        keys.add(k)
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/onboarding/shadow_collect.py", exc)
    return keys


def load_rows(path: str) -> List[dict]:
    """读影子档（坏行跳过）。"""
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------------- #
# 前向收益（默认 datamart 只读接口；测试注入）
# ---------------------------------------------------------------------- #
def _default_fwd(symbol: str, trade_date: str) -> Optional[Dict[int, float]]:
    """信号日前向收益 {5: %, 20: %}（council_shadow._fwd 同款只读接口）。

    失败/窗口不足返回 None 或缺键，不致命。
    """
    try:
        from agent_tools import call as T

        r = T(
            "datamart_read",
            table="ts_daily",
            codes=[symbol],
            start=trade_date,
            limit=40,
        )
        d2 = r.get("data") if isinstance(r.get("data"), dict) else r
        rows = d2.get("rows") or []
    except Exception:
        return None
    d8 = str(trade_date).replace("-", "")[:8]
    closes: List[float] = []
    for row in rows:
        dt = str(
            next(
                (row[k] for k in ("trade_date", "d", "date", "datetime") if row.get(k)),
                "",
            )
        )[:10].replace("-", "")[:8]
        if dt < d8:
            continue
        px = next(
            (
                row[k]
                for k in ("close", "Close", "close_price")
                if row.get(k) is not None
            ),
            None,
        )
        if px is None:
            continue
        closes.append(float(px))
    if not closes:
        return None
    base = closes[0]
    return {n: round((closes[n] / base - 1) * 100, 2) for n in NS if len(closes) > n}


# ---------------------------------------------------------------------- #
# 统计（纯函数；口径对齐 council_shadow.compute_stats）
# ---------------------------------------------------------------------- #
def _bucket(score: float) -> str:
    i = 0 if score < 25 else 1 if score < 50 else 2 if score < 75 else 3
    return BUCKETS[i]


def _agg(rows: List[dict]) -> dict:
    r5s = [r["r5"] for r in rows if r.get("r5") is not None]
    r20s = [r["r20"] for r in rows if r.get("r20") is not None]
    return {
        "n": len(rows),
        "win5": round(100.0 * sum(1 for x in r5s if x > 0) / len(r5s), 1)
        if r5s
        else None,
        "r5": round(sum(r5s) / len(r5s), 2) if r5s else None,
        "r20": round(sum(r20s) / len(r20s), 2) if r20s else None,
    }


def _corr(pairs: List[tuple]) -> Optional[dict]:
    """Pearson r + t 统计（|t|≥2 近似 5% 显著）；样本 <3 不评判。"""
    if len(pairs) < 3:
        return None
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if vx <= 0 or vy <= 0:
        return None
    r = sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(vx * vy)
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return {"n": n, "r": round(r, 3), "t": round(t, 2)}


def compute_shadow_stats(
    rows: List[dict],
    fwd_fn: Optional[Callable[[str, str], Optional[Dict[int, float]]]] = None,
) -> dict:
    """影子档行 → 统计 dict（gate 三态判据见 shadow_report_template.md）。

    fwd_fn 注入模式供沙盒测试（(symbol, trade_date) -> {5:%,20:%}）；
    缺省走 datamart 只读接口。n<30 只记录不评判。
    """
    fwd = fwd_fn or _default_fwd
    samples: List[dict] = []
    cache: Dict[str, Optional[Dict[int, float]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        td = str(row.get("trade_date") or "")[:8]
        if not symbol or len(td) != 8:
            continue
        key = "%s|%s" % (symbol, td)
        if key not in cache:
            cache[key] = fwd(symbol, td)
        f = cache[key] or {}
        samples.append(
            {
                "symbol": symbol,
                "trade_date": td,
                "score": float(row.get("score") or 0.0),
                "confidence": float(row.get("confidence") or 0.0),
                "action": row.get("action"),
                "r5": f.get(5),
                "r20": f.get(20),
            }
        )
    have = [s for s in samples if s["r5"] is not None or s["r20"] is not None]
    by_score_bucket = [
        {"bucket": b, **_agg([s for s in have if _bucket(s["score"]) == b])}
        for b in BUCKETS
    ]
    pairs = [(s["score"], s["r20"]) for s in samples if s.get("r20") is not None]
    corr = _corr(pairs)
    if len(pairs) < N_MIN:
        gate = {
            "status": "样本积累中·影子延期",
            "n": len(pairs),
            "need": N_MIN,
            "corr": corr,
        }
    elif corr and corr["t"] >= SIG_T:
        gate = {"status": "显著正向·建议转正评估（人工拍板）", "corr": corr}
    elif corr and corr["t"] <= -SIG_T:
        gate = {"status": "显著反向·建议淘汰", "corr": corr}
    else:
        gate = {"status": "不显著·维持影子（可再延一个观察窗）", "corr": corr}
    return {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_total": len(samples),
        "n_have_fwd": len(have),
        "by_score_bucket": by_score_bucket,
        "corr": corr,
        "gate": gate,
        "samples": sorted(samples, key=lambda s: s["trade_date"], reverse=True),
    }


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/onboarding/shadow_collect.py", exc)
    ap = argparse.ArgumentParser(description="S4 影子期每日采集与统计")
    ap.add_argument("--strategy", help="策略名（strategies.REGISTRY 键）")
    ap.add_argument("--symbol", default=None, help="标的，逗号分隔（采集模式必填）")
    ap.add_argument("--market", default="CN")
    ap.add_argument("--date", default=None, help="采集交易日 YYYYMMDD")
    ap.add_argument(
        "--report",
        action="store_true",
        help="统计模式：读影子档算分位/相关性/gate 三态",
    )
    ap.add_argument("--out", default=None, help="影子档路径覆写（测试用）")
    a = ap.parse_args(argv)

    if a.report:
        rows = load_rows(a.out or shadow_path(a.strategy or ""))
        st = compute_shadow_stats(rows)
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0

    if not (a.strategy and a.symbol and a.date):
        print(
            "shadow_collect: 采集模式需 --strategy/--symbol/--date；统计模式需 --report"
        )
        return 2
    symbols = [s.strip() for s in a.symbol.split(",") if s.strip()]
    adapter = ClassicStrategyAdapter(a.strategy, symbols, market=a.market)
    res = collect_once(adapter, a.date, out_path=a.out)
    print(
        "[SHADOW] %s %s recorded=%d replayed=%d skipped_non_event=%d -> %s"
        % (
            a.strategy,
            a.date,
            res["recorded"],
            res["replayed"],
            res["skipped_non_event"],
            res["path"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
