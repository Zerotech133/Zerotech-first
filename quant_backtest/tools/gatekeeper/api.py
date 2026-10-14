#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gatekeeper 对外唯一入口（方案 §三接口草案，2026-10-15）。

EnvGate.decide 是消费层（qpzm 回测/日批、双刀组合、盯盘 RiskGuard、
未来策略）唯一要认的契约；闸门逻辑不认数据源（feeder 可插拔），
不认判据实现（policy 版本化，sha 冻结）。

safe 三值语义（market_env S6 既有语义）：
  True   —— 判据通过，放行；
  False  —— 触发否决（杀）；
  None   —— 特征缺维（feeder 缺源/湖字段缺失）→ 降权处理，不误杀。
"""

import dataclasses
import datetime


@dataclasses.dataclass
class GateDecision:
    """判定即账本行（ledger.append 直接落一行 JSON）。"""

    market: str                # 市场标识（cn/us/...），feeder 只认自己有源的
    date: str                  # 锚日 YYYYMMDD（feeder 解析后的实际锚位）
    safe: object               # bool | None；None=特征缺失缺维降权，不误杀
    conditions: dict           # 各判据子条件命中情况 {"breadth_lt_0.4": True, ...}
    features: dict             # 特征快照 {"breadth": 0.326, "med20": -0.021, ...}
    policy_ver: str            # "v3_breadth40_med20"
    policy_sha: str            # 冻结 sha 前 16 位（复刻 voltarget_ab 范式）
    feeder: str                # 特征源标识（qpzm_parquet/lake/...）
    ts: str                    # 判定时刻 ISO 时间

    def to_row(self):
        """→ 可 JSON 化的账本行 dict。"""
        return dataclasses.asdict(self)


class EnvGate:
    """环境闸门：确定性判定，按冻结判据 + feeder 特征算开关。

    policy：需有 .ver / .sha / .evaluate(features) -> (safe|None, conditions)
    feeder：需有 .name / .get_features(market, date) -> (anchor_date, features)
    ledger：可选 Ledger 实例；传入则每次 decide 原子追加账本行。
    """

    def __init__(self, policy, feeder, ledger=None):
        self.policy = policy
        self.feeder = feeder
        self.ledger = ledger

    def decide(self, market, date=None):
        """判定一次环境闸门。

        date=None 由 feeder 自选锚位（通常为最新可用锚）；
        返回 GateDecision（不抛特征类异常——缺维一律 safe=None 落账）。
        """
        anchor, feats = self.feeder.get_features(market, date)
        feats = dict(feats or {})
        if anchor is not None:
            feats.setdefault("anchor_date", str(anchor))
        safe, conditions = self.policy.evaluate(feats)
        dec = GateDecision(
            market=str(market),
            date=str(anchor) if anchor is not None else str(date or ""),
            safe=safe,
            conditions=conditions,
            features=feats,
            policy_ver=self.policy.ver,
            policy_sha=self.policy.sha,
            feeder=self.feeder.name,
            ts=datetime.datetime.now().isoformat(timespec="seconds"),
        )
        if self.ledger is not None:
            self.ledger.append(dec)
        return dec
