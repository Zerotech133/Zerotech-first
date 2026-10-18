"""_candidate_id_from_plan_id 反解逻辑回归锁。

TradePlan domain 无 candidate_id 字段，plan_id 由 create_from_candidate 构造为
f"plan-{candidate_id}"；save_trade_plan 此前 INSERT 恒写 candidate_id=""，致 audit_trace
按该列 JOIN 血缘全断。修复：从 plan_id 无损反解回填。非 plan- 约定 id（seed/测试）落 ""。
"""
from __future__ import annotations

import pytest

from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import (
    _candidate_id_from_plan_id,
)


@pytest.mark.parametrize(
    "plan_id, expected",
    [
        # 真实工作流 plan_id（承载 candidate_id）→ 无损反解
        ("plan-cand-v1-crypto-sv-3-DOGE/USDT-2026-09-03", "cand-v1-crypto-sv-3-DOGE/USDT-2026-09-03"),
        ("plan-cand-v1-hk-sv-2-00700-2026-09-03", "cand-v1-hk-sv-2-00700-2026-09-03"),
        # 反解是 create_from_candidate 构造的严格逆：plan-{cid} → cid
        ("plan-cand-x", "cand-x"),
    ],
)
def test_reparse_plan_prefix(plan_id, expected):
    assert _candidate_id_from_plan_id(plan_id) == expected


@pytest.mark.parametrize(
    "plan_id",
    [
        "p1",  # 测试风格 id
        "p-appr-xxx",  # 以 p 开头但非 plan- 前缀
        "us_plan_23",  # seed fixture，非 plan- 约定
        "",  # 空
    ],
)
def test_non_plan_prefix_returns_empty(plan_id):
    assert _candidate_id_from_plan_id(plan_id) == ""
