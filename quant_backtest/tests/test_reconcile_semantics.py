"""门 N8 reconcile 语义升级调度（休眠默认）单元测试。

仿 tests/test_freeze_alignment.py 约定：
- QBT 为仓根，将 str(QBT) 与 str(QBT/"strategy_layer") 加入 sys.path。
- 全部使用合成 probe dict（如 {"status":"UNVERIFIED"}），不触真实数据。
- 核心契约：reconcile 默认模式一律返回 status="NOT_VERIFIED"，绝不输出 READY；
  与 r0_evidence_reconciliation.py 当前恒 NOT_VERIFIED/REJECTED、无 READY 出口的行为对齐。
"""
import sys
from pathlib import Path

QBT = Path(__file__).resolve().parents[1]
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))
if str(QBT / "strategy_layer") not in sys.path:
    sys.path.insert(0, str(QBT / "strategy_layer"))

from strategy_layer.reconcile_semantics import (  # noqa: E402
    JACCARD_R1_VS_PRODUCTION,
    MODE_A_PRODUCTION_MULTISOURCE,
    MODE_B_R1_SINGLESOURCE,
    MODE_C_DUALTRACK,
    RECONCILE_SEMANTICS_ENABLED,
    reconcile,
)

# 合成 probe（不触真实数据）
_PROBE = {"status": "UNVERIFIED"}


def test_reconcile_mode_a_not_verified():
    """A 模式（生产多源融合）休眠：结论维持 NOT_VERIFIED，不输出 READY。"""
    out = reconcile("20261021", MODE_A_PRODUCTION_MULTISOURCE, _PROBE)
    assert out["status"] == "NOT_VERIFIED"
    assert out.get("ready") is not True
    assert out["cutover_verdict"] == "BLOCK_CUTOVER"


def test_reconcile_mode_b_not_verified():
    """B 模式（R1 单源基线）休眠：结论维持 NOT_VERIFIED。"""
    out = reconcile("20261021", MODE_B_R1_SINGLESOURCE, _PROBE)
    assert out["status"] == "NOT_VERIFIED"
    assert out.get("ready") is not True


def test_reconcile_mode_c_not_verified():
    """C 模式（双轨标注）休眠：结论维持 NOT_VERIFIED。"""
    out = reconcile("20261021", MODE_C_DUALTRACK, _PROBE)
    assert out["status"] == "NOT_VERIFIED"
    assert out.get("ready") is not True


def test_reconcile_unknown_mode_stays_not_verified():
    """未知模式也绝不升级为 READY，保持保守。"""
    out = reconcile("20261021", "Z", _PROBE)
    assert out["status"] == "NOT_VERIFIED"


def test_jaccard_fact_constant_recorded():
    """记录 N8 清单事实常量：R1 单源 vs 生产多源的 Jaccard = 0.0437。"""
    assert JACCARD_R1_VS_PRODUCTION == 0.0437


def test_reconcile_switch_activated():
    """路径 A 授权：RECONCILE_SEMANTICS_ENABLED 已翻 True，语义方向 B 生效。"""
    assert RECONCILE_SEMANTICS_ENABLED is True
