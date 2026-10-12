"""教师风险标签校准层测试。"""

import runpy
from pathlib import Path


MOD = runpy.run_path(
    str(Path(__file__).with_name("build_distillation_calibration_20261013.py"))
)


def test_ps002_keeps_conservative_abstention_and_ps003_becomes_unknown():
    labels, manifest = MOD["build_calibrated_labels"]()
    by_id = {label["sample_id"]: label for label in labels}
    assert by_id["PS-002"]["selected_strategy"] == "abstain"
    assert by_id["PS-002"]["abstain"] is True
    assert by_id["PS-002"]["risk_level"] == "medium"
    assert by_id["PS-003"]["selected_strategy"] == "abstain"
    assert by_id["PS-003"]["risk_level"] == "unknown"
    assert manifest["raw_labels_unchanged"] is True


def test_moderate_is_canonicalized_to_medium():
    labels, _ = MOD["build_calibrated_labels"]()
    assert all(label["risk_level"] != "moderate" for label in labels)
