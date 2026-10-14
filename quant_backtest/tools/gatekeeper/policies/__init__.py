#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据包：版本化策略壳 + sha 冻结参数。

现行 v3_breadth40_med20（复刻 qpzm_backtest.env_gate 判据）；
未来 v4_观察带50 / v5_HMM并联 走同一壳注册（方案 §二）。
参数阈值不写死在代码里——冻结进 policy.json（同目录），
复用 voltarget_ab.ensure_config 范式，转正评审只认冻结版。
"""
