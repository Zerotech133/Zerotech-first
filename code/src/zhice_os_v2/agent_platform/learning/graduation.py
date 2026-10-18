"""GraduationService — 毕业权重闭环（学习闭环步⑦/⑧，判据 v2）。

判据 v2：有效样本 n≥30（CRYPTO 降档 6）且该方法投 SELL 的决策子集 T+5 前向
均值 >+1pp → weight ×1.2（封顶 3.0）；<-1pp → ×0.8（地板 0.3）；否则 HOLD。
数据源：aw_decision.fwd_ret_5d + votes_json（分方法归因）。
apply_weights：写 aw_method.weight 并 registry.load_weights 热更新（闭环合拢）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from zhice_os_v2.agent_platform.config import utc_now

logger = logging.getLogger(__name__)

PROMOTE_THRESHOLD = 0.01     # +1pp
DEMOTE_THRESHOLD = -0.01     # -1pp
WEIGHT_CAP = 3.0
WEIGHT_FLOOR = 0.3
PROMOTE_MULT = 1.2
DEMOTE_MULT = 0.8


@dataclass
class GraduationReport:
    """单方法单市场的毕业评估结果。"""

    method_id: str
    market: str
    sample_size: int = 0
    mean_fwd_ret: float | None = None
    decision: str = "HOLD"            # PROMOTE / DEMOTE / HOLD
    old_weight: float = 1.0
    new_weight: float = 1.0
    reason: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "method_id": self.method_id,
            "market": self.market,
            "sample_size": self.sample_size,
            "mean_fwd_ret": self.mean_fwd_ret,
            "decision": self.decision,
            "old_weight": round(self.old_weight, 4),
            "new_weight": round(self.new_weight, 4),
            "reason": self.reason,
            "details": dict(self.details),
        }


class GraduationService:
    """毕业判据 v2（n≥30 有效标的/域，CRYPTO 降档 6；±1pp 阈值）。"""

    def __init__(self, min_samples: dict[str, int] | None = None,
                 session_factory=None, registry=None) -> None:
        self.min_samples = {"DEFAULT": 30, "CRYPTO": 6}
        if min_samples:
            self.min_samples.update(min_samples)
        self._session_factory = session_factory
        self._registry = registry

    # ── 评估 ────────────────────────────────────────────────────────────────
    def evaluate(self, method_id: str, market: str,
                 playbook_id: str | None = None) -> dict:
        """评估单方法在单市场的毕业判定。返回 GraduationReport.to_dict()。

        样本口径：aw_decision 中该 market 全部已回填 fwd_ret_5d 的决策里，
        该方法投出 SELL 票的子集（分方法归因——方法的责任只算它主张卖出的局）。
        playbook_id（T06）：非 None 时按决策的 playbook_id 精确过滤——
        按 (method, playbook) 独立毕业；None=全量旧口径（兼容既有调用）。
        """
        report = GraduationReport(method_id=method_id, market=market)
        if playbook_id is not None:
            report.details = {"playbook_id": playbook_id}
        rets = self._collect_method_rets(method_id, market, playbook_id)
        report.sample_size = len(rets)
        report.details = {**report.details,
                          "min_samples": self._min_samples(market)}
        if not rets:
            report.reason = "无有效样本（fwd_ret_5d 未回填或方法无 SELL 票）"
            report.new_weight = report.old_weight
            return report.to_dict()
        mean = sum(rets) / len(rets)
        report.mean_fwd_ret = mean
        n_min = self._min_samples(market)
        if report.sample_size < n_min:
            report.decision = "HOLD"
            report.reason = (f"样本不足 n={report.sample_size} < {n_min}"
                             f"（mean={mean:+.2%}）")
        elif mean > PROMOTE_THRESHOLD:
            report.decision = "PROMOTE"
            report.reason = f"n={report.sample_size} ≥ {n_min} 且 mean={mean:+.2%} > +1pp"
        elif mean < DEMOTE_THRESHOLD:
            report.decision = "DEMOTE"
            report.reason = f"n={report.sample_size} ≥ {n_min} 且 mean={mean:+.2%} < -1pp"
        else:
            report.decision = "HOLD"
            report.reason = f"n={report.sample_size} 但 mean={mean:+.2%} 在 ±1pp 内"
        # Playbook 维度优先读取复合键权重，避免不同打法包互相污染。
        report.old_weight = self._current_weight(method_id, playbook_id=playbook_id)
        if report.decision == "PROMOTE":
            report.new_weight = min(WEIGHT_CAP, report.old_weight * PROMOTE_MULT)
        elif report.decision == "DEMOTE":
            report.new_weight = max(WEIGHT_FLOOR, report.old_weight * DEMOTE_MULT)
        else:
            report.new_weight = report.old_weight
        return report.to_dict()

    def _min_samples(self, market: str) -> int:
        return self.min_samples.get(market, self.min_samples["DEFAULT"])

    def _current_weight(self, method_id: str,
                        playbook_id: str | None = None) -> float:
        """当前权重：优先打法包维度行；无表/无行 → 默认行/registry 权重。"""
        if self._session_factory is not None:
            try:
                from zhice_os_v2.agent_platform.models import AwMethodModel

                with self._session_factory() as session:
                    if playbook_id:
                        row = session.get(AwMethodModel, f"{method_id}@{playbook_id}")
                        if row is not None:
                            return float(row.weight)
                    row = session.get(AwMethodModel, method_id)
                    if row is not None:
                        return float(row.weight)
            except Exception as exc:
                logger.warning("aw_method 读取失败(%s) — 回退 registry 权重", exc)
        if self._registry is not None:
            m = self._registry.by_id(method_id)
            if m is not None:
                return float(m.weight)
        return 1.0

    def _collect_method_rets(self, method_id: str, market: str,
                             playbook_id: str | None = None) -> list[float]:
        """该市场全部已回填决策中，方法 method_id 投 SELL 的前向收益子集。

        playbook_id 非 None 时按 aw_decision.playbook_id 精确匹配（T06 按
        (method, playbook) 独立毕业）；旧行 playbook_id=NULL 只匹配 None 查询。
        """
        if self._session_factory is None:
            return []
        from zhice_os_v2.agent_platform.models import AwDecisionModel

        with self._session_factory() as session:
            q = session.query(AwDecisionModel).filter(
                AwDecisionModel.market == market,
                AwDecisionModel.fwd_ret_5d.isnot(None),
            )
            if playbook_id is not None:
                q = q.filter(AwDecisionModel.playbook_id == playbook_id)
            rows = q.all()
            rets: list[float] = []
            for r in rows:
                for v in (r.votes_json or []):
                    if v.get("method_id") == method_id and v.get("direction") == "SELL":
                        rets.append(float(r.fwd_ret_5d))
                        break
            return rets

    # ── 应用（闭环合拢点） ──────────────────────────────────────────────────
    def evaluate_all(self, market: str, method_ids: list[str] | None = None,
                     playbook_id: str | None = None) -> list[dict]:
        """评估该方法集（缺省=registry 全部）。playbook_id 非 None 时按包过滤。"""
        if method_ids is None:
            method_ids = self._registry.ids() if self._registry else []
        return [self.evaluate(mid, market, playbook_id=playbook_id)
                for mid in method_ids]

    def apply_weights(self, reports: list[dict] | None = None,
                      market: str = "", method_ids: list[str] | None = None,
                      playbook_id: str | None = None) -> dict[str, float]:
        """评估结果落 aw_method + 写 aw_graduation 留痕 + registry 热更新。

        playbook_id（T07）：非 None 时 aw_method 按 (method, playbook) 复合行
        落库（playbook_id 列留痕，单列主键保留兼容）；registry.load_weights
        仍按 method_id 盖章——跨包共享实例的既有语义不动。
        返回 {method_id: new_weight}（含未变化的，便于 load_weights 全量盖章）。
        """
        if reports is None:
            reports = self.evaluate_all(market, method_ids, playbook_id=playbook_id)
        weights: dict[str, float] = {}
        for rep in reports:
            mid = rep["method_id"]
            new_w = float(rep.get("new_weight", 1.0))
            weights[mid] = new_w
            self._persist(mid, rep, playbook_id=playbook_id)
        if self._registry is not None:
            self._registry.load_weights(weights)
        # T05 闭环指标：毕业权重现值 Gauge
        from zhice_os_v2.agent_platform import metrics as aw_metrics

        for mid, w in weights.items():
            aw_metrics.set_graduation_weight(mid, w)
        logger.info("毕业权重应用(playbook=%s): %s", playbook_id or "-",
                    {k: round(v, 3) for k, v in weights.items()})
        return weights

    def _persist(self, method_id: str, rep: dict,
                 playbook_id: str | None = None) -> None:
        """aw_method.weight 更新 + aw_graduation 留痕（失败不阻塞热更新）。

        playbook_id 非 None → aw_method 写 (method, playbook) 复合行：
        method_id 主键按 f"{method_id}@{playbook_id}" 存储（同表共存不冲突），
        原 method_id 单行保留为跨包默认权重（旧行为）。
        """
        if self._session_factory is None:
            return
        try:
            from zhice_os_v2.agent_platform.models import (
                AwGraduationModel,
                AwMethodModel,
            )

            row_key = f"{method_id}@{playbook_id}" if playbook_id else method_id
            with self._session_factory() as session:
                row = session.get(AwMethodModel, row_key)
                if row is None:
                    session.add(AwMethodModel(
                        method_id=row_key,
                        display_name=method_id,
                        weight=float(rep.get("new_weight", 1.0)),
                        playbook_id=playbook_id,
                    ))
                else:
                    row.weight = float(rep.get("new_weight", 1.0))
                session.add(AwGraduationModel(
                    method_id=method_id,
                    market=rep.get("market", ""),
                    eval_date=date.today(),
                    sample_size=int(rep.get("sample_size", 0)),
                    mean_fwd_ret=rep.get("mean_fwd_ret"),
                    decision=rep.get("decision", "HOLD"),
                    old_weight=float(rep.get("old_weight", 1.0)),
                    new_weight=float(rep.get("new_weight", 1.0)),
                    details_json={"reason": rep.get("reason", ""),
                                  "playbook_id": playbook_id},
                ))
                session.commit()
        except Exception as exc:
            logger.warning("aw_method/aw_graduation 写库失败(%s) — registry 热更新仍生效", exc)
