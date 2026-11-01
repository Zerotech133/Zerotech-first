# -*- coding: utf-8 -*-
"""① 冒烟：RightsGate 已批 xueqiu 三用途后，注入真实 fetcher 跑采样回执。

前置（需老田提供，缺一不可）：
  - XQ_COOKIE        : 雪球会话 cookie（鉴权）
  - XQ_LIVE_ARMED=1  : arming 开关（防误用）
  - 可选 XQ_PROXY    : 出口代理

运行：
  XQ_LIVE_ARMED=1 XQ_COOKIE=xxxx python examples/smoke_xueqiu_live.py

输出：真实采样 Observation 数、归一化条目数、manifest 状态分账
      （complete/empty/incomplete/unavailable，失败可见，被封≠删除）。

合规：只读 GET、>=2s 限速、403 连续熔断、失败可见。缺授权/凭据直接终止，不触网。
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.source_registry import SourceRegistry
from core.rights_gate import RightsGate
from adapters.xueqiu_adapter import XqAdapter
from adapters.xueqiu_live import build_live_fetcher

REG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "registry", "sources.json"
)
TARGETS = ["SH600519"]  # 示例标的，可改


def main() -> None:
    reg = SourceRegistry(REG_PATH)
    spec = reg.get("xueqiu")
    if not spec:
        print("❌ 未找到 xueqiu 源，请先跑 registry/bootstrap.py")
        return

    # 1) RightsGate 校验（老田/RightsGate 已批的三用途）
    gate = RightsGate()
    for uc in ("internal_retention", "cleaning", "controlled_query"):
        d = gate.check(spec, uc)
        print(f"[{'PASS' if d.allowed else 'BLOCK'}] {uc}: {d.reason}")
        if not d.allowed:
            print("❌ RightsGate 未授权，终止（不触网）")
            return

    # 2) arming + 凭据守卫
    if os.environ.get("XQ_LIVE_ARMED") != "1":
        print("❌ 未 armed：请设 XQ_LIVE_ARMED=1 并提供 XQ_COOKIE 后运行")
        return
    if not os.environ.get("XQ_COOKIE"):
        print("❌ 缺少 XQ_COOKIE，无法鉴权真实雪球")
        return

    # 3) 注入真实 fetcher，跑采样
    fetcher = build_live_fetcher()
    adapter = XqAdapter("xueqiu", fetcher=fetcher)
    obs = adapter.collect(TARGETS, mode="timeline", max_pages=2)

    cnt = Counter(o.status for o in obs)
    total_items = sum(len(o.payload.get("items", [])) for o in obs)
    print(f"✅ 采样目标 {TARGETS}：Observation={len(obs)}，归一化条目={total_items}")
    print("✅ 状态分账：", dict(cnt))


if __name__ == "__main__":
    main()
