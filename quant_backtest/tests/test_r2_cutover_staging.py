"""R2 灰度切流护栏单元测试（工位 #373，门 N12 R2 发布审批包）。

验证：默认不武装、前置检查项全未满足、未武装 trigger 返回 not_armed、
武装后前置缺失仍返回 preconditions_unmet，绝不因调用而「实际切流」。
"""
import sys
from pathlib import Path

QBT = Path(__file__).resolve().parents[1]
if str(QBT / "tools") not in sys.path:
    sys.path.insert(0, str(QBT / "tools"))

from r2_cutover_staging import R2CutoverStaging  # noqa: E402


EXPECTED_PRECONDITIONS = [
    "R0_closed",
    "R1_BLOCK_enforced",
    "N3_adopted",
    "N5_signed",
    "N6_ceremony_done",
    "N7_done",
    "N8_semantics_decided",
    "N10_nav_mounted",
    "N11_pool_decided",
    "N12_five_elements_approved",
]


def test_default_not_armed():
    """默认 is_armed()==False（休眠/不武装）。"""
    s = R2CutoverStaging()
    assert s.is_armed() is False


def test_checklist_unmet_all():
    """validate_checklist()[0] is False 且 missing 含全部前置。"""
    s = R2CutoverStaging()
    ok, missing = s.validate_checklist()
    assert ok is False
    assert set(missing) == set(EXPECTED_PRECONDITIONS)
    assert len(missing) == len(EXPECTED_PRECONDITIONS)


def test_trigger_not_armed():
    """未武装时 trigger() 返回 not_armed，绝不切流。"""
    s = R2CutoverStaging()
    r = s.trigger()
    assert r["triggered"] is False
    assert r["reason"] == "not_armed"


def test_arm_then_trigger_still_blocked():
    """arm() 后 is_armed()==True，但 trigger() 仍返回 preconditions_unmet。"""
    s = R2CutoverStaging()
    s.arm()
    assert s.is_armed() is True
    r = s.trigger()
    assert r["triggered"] is False
    assert r["reason"] == "preconditions_unmet"
    assert set(r["missing"]) == set(EXPECTED_PRECONDITIONS)


def test_no_real_cutover_on_call():
    """调用 arm/trigger 绝不产生「实际切流」信号（triggered 恒为 False）。"""
    s = R2CutoverStaging(armed=True)
    assert s.is_armed() is True
    r = s.trigger()
    assert r["triggered"] is False  # 当前所有前置未满足，不可达真实切流
    assert "missing" in r


def test_preconditions_dict_extensible():
    """preconditions 字典存在且默认全为 False，便于扩展。"""
    s = R2CutoverStaging()
    assert isinstance(s.preconditions, dict)
    for k in EXPECTED_PRECONDITIONS:
        assert k in s.preconditions
        assert s.preconditions[k] is False
