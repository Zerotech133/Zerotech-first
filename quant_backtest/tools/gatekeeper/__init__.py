#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gatekeeper —— 策略 OS 底座的环境闸门独立子模块（第一个通用金融件）。

方案：《环境闸门独立子模块与Hermes进化方案_2026-10-15》§二/§三。
对外唯一入口 EnvGate.decide(market, date) -> GateDecision（判定即账本行）。

两条不可妥协边界（方案 §二）：
  1. 判定是确定性的——开关由冻结判据+特征算出，LLM 永不直接决定产线闸门；
  2. 进化走治理链——行为变更必须 sha 冻结新版本后由 freeze 落注册表。
"""

from .api import EnvGate, GateDecision

__all__ = ["EnvGate", "GateDecision"]
