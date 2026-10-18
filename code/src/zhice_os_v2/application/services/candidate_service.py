"""候选生成服务。

V2 第一版支持从 StrategySpec 中读取 universe，并使用内置技术因子打分。
后续可替换为 ResearchGateway 产出的外部执行器。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from zhice_os_v2.domain.candidate.candidate import Candidate, CandidatePool
from zhice_os_v2.domain.strategy.indicators import ma_cross, momentum, rsi
from zhice_os_v2.domain.strategy.spec import FactorSpec, StrategySpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

BarProvider = Callable[[str, date, int], list[float] | None]


def _compute_factor(factor: FactorSpec, closes: list[float]) -> float | None:
    name = factor.name
    params = factor.params
    if name == "momentum":
        return momentum(closes, int(params.get("window", 20)))
    if name == "ma_cross":
        return ma_cross(closes, int(params.get("fast", 5)), int(params.get("slow", 20)))
    if name == "rsi":
        return rsi(closes, int(params.get("window", 14)))
    return None


_OPS = {
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def evaluate_signal(spec, closes: list[float] | None) -> str | None:
    """信号型策略：按 spec.rules 执行 DSL → "enter"|"exit"|None（exit 优先）。

    先用 _compute_factor 算各 rule.feature 的值（rsi/momentum/ma_cross），
    再逐条 rule 比较 op/value；命中 exit 规则优先平仓，其次 enter，皆不中返回 None。
    closes 不足以算因子（None/太短）→ 返回 None（跳过，不误动作）。
    """
    if not closes:
        return None
    feats: dict[str, float] = {}
    for f in spec.factors:
        v = _compute_factor(f, closes)
        if v is not None:
            feats[f.name] = v
    enter = exit_ = False
    for r in spec.rules:
        val = feats.get(r.feature)
        if val is None:
            continue
        cmp = _OPS.get(r.op)
        if cmp and cmp(val, r.value):
            if r.action == "exit":
                exit_ = True
            else:
                enter = True
    if exit_:
        return "exit"
    if enter:
        return "enter"
    return None


class CandidateService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def generate(
        self,
        spec: StrategySpec,
        trade_date: date,
        bar_provider: BarProvider | None = None,
        *,
        strategy_version_id: str = "",
        persist: bool = True,
    ) -> CandidatePool:
        sv = strategy_version_id or ""
        # 有策略号时用它作 pool_id/candidate_id 前缀（确定性 + 防多策略撞 id）；
        # 无策略号（如 optimizer 临时 trial）回落到 id(spec) 保持旧行为。
        suffix = sv if sv else f"{id(spec):x}"
        pool_id = f"pool-{spec.market}-{trade_date.isoformat()}-{suffix}"
        pool = CandidatePool(pool_id=pool_id, strategy_version_id=sv, market=spec.market, trade_date=trade_date)
        max_window = 20
        for f in spec.factors:
            w = int(f.params.get("window", 20))
            max_window = max(max_window, w)
        for ts_code in spec.universe.get("codes", []):
            closes = bar_provider(ts_code, trade_date, max_window) if bar_provider else None
            values: dict[str, float] = {}
            ok = True
            if closes is None:
                ok = False
            else:
                for f in spec.factors:
                    v = _compute_factor(f, closes)
                    if v is None:
                        ok = False
                        break
                    values[f.name] = v
            if not ok:
                continue
            score = float(values.get("momentum", 0) if "momentum" in values else 1.0)
            cid_scope = f"{sv}-" if sv else ""
            candidate = Candidate(
                candidate_id=f"cand-{cid_scope}{ts_code}-{trade_date.isoformat()}",
                strategy_version_id=sv,
                market=spec.market,
                ts_code=ts_code,
                score=max(0.0, min(1.0, score)),
                reason=";".join(f"{k}={v:.4f}" for k, v in values.items()),
                factor_values=values,
            )
            pool.candidates.append(candidate)
        if persist:
            self._db.save_candidate_pool(pool)
        return pool
