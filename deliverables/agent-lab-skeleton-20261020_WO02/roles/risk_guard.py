"""风控观察席（risk_guard）骨架。

职责：只观察与预警——阈值越界、异常行为、越权企图。
铁律：观察席不执行任何动作、不撤销、不派工；交易等动作必须
人工确认 + 独立风控 PASS（对齐既有 RiskGuard 红线）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from roles.base_agent import BaseAgent, Report

# 占位阈值：全部须由人工按真实基线标定后写入配置，不得直接投产使用。
PLACEHOLDER_THRESHOLDS = {
    "task_backlog_total": 50,          # 单位：张，持续窗口 10 分钟
    "report_delivery_lag_hours": 24,   # 简报/日报迟交
    "risk_alert_rate_per_hour": 5,
}


class RiskGuard(BaseAgent):
    def __init__(self, thresholds: Dict[str, float] | None = None, **kw: Any) -> None:
        super().__init__(name="risk_guard", role="guard:observer",
                         level="command", **kw)
        self.thresholds = thresholds or dict(PLACEHOLDER_THRESHOLDS)
        self.events: List[Dict[str, Any]] = []

    # ---- 输入：指标流 / 事件流 ----

    def evaluate(self, metrics: Dict[str, float],
                 event: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """评估一条指标/事件，产出预警（同时追加到 self.events）。

        阈值比较：metrics 逐项对照 self.thresholds（默认
        PLACEHOLDER_THRESHOLDS），仅键匹配的指标才比较；越界
        （value > threshold）产出预警 dict（metric/value/threshold/severity）。

        事件规则：event["type"] == "bypass_attempt"（越权企图）→ 立即
        产出高 severity 预警。

        TODO(领域实现):
        - 阈值按真实基线标定（当前为占位值，PLACEHOLDER_THRESHOLDS
          注释保持"占位须标定"，不得直接投产）；
        - 完整事件规则待实现：越级汇报尝试、伪造署名、未签准执行企图；
        - 预警去重与升级窗口。
        """
        alerts: List[Dict[str, Any]] = []

        # TODO(领域实现): 完整事件规则待实现（见 docstring）；
        # 当前仅覆盖越权企图一类，命中即最高级预警。观察席只预警不执行。
        if event is not None and event.get("type") == "bypass_attempt":
            alerts.append({
                "metric": "bypass_attempt",
                "value": event,
                "threshold": None,
                "severity": "high",
            })

        for key, value in (metrics or {}).items():
            threshold = self.thresholds.get(key)
            if threshold is None:
                continue  # 键不匹配不比较：占位阈值须标定后方可覆盖新指标
            if value > threshold:
                alerts.append({
                    "metric": key,
                    "value": value,
                    "threshold": threshold,
                    # TODO(领域实现): severity 分级规则待标定，当前占位为 medium。
                    "severity": "medium",
                })

        self.events.extend(alerts)
        return alerts

    # ---- 输出：预警（只上行，不执行） ----

    def emit_alert(self, subject: str, body: str) -> None:
        """预警必须同时上行中台与本人视角；观察席自身不得调用任何执行接口。"""
        self.report_up(Report(level="principal", source=self.name,
                              subject=f"[预警] {subject}", body=body))
