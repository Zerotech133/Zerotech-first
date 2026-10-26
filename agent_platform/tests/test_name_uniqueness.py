"""名称唯一性 CI 测试（WO-NAME-UNIQ-20261027）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "check_name_uniqueness.py"
_spec = importlib.util.spec_from_file_location("check_name_uniqueness", str(_TOOL))
CN = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CN)


def _root(tmp_path, *, cards=None, hr=None, org=None):
    root = tmp_path
    (root / "agent_platform/hr/A1").mkdir(parents=True, exist_ok=True)
    (root / "agent_platform/hr/A1/resume.yaml").write_text(
        "cn_name: 甲\n", encoding="utf-8")
    for aid, cn in (hr or {}).items():
        p = root / "agent_platform/hr" / aid
        p.mkdir(parents=True, exist_ok=True)
        (p / "resume.yaml").write_text("cn_name: %s\n" % cn, encoding="utf-8")
    (root / "agent_platform/registry").mkdir(parents=True, exist_ok=True)
    (root / "agent_platform/registry/org.json").write_text(json.dumps(
        {"departments": [{"dept": "dev", "agent_profiles": org or []}]},
        ensure_ascii=False), encoding="utf-8")
    (root / "portal").mkdir(parents=True, exist_ok=True)
    agents = {
        "A1": {"cn_name": "甲", "name": "甲", "entry_source": "org_profiles"},
        "B1": {"cn_name": "乙", "name": "乙", "entry_source": "org_profiles"},
        "ZHAOYUN": {"cn_name": "赵云·治理席（不占编）", "name": "赵云·治理席（不占编）",
                    "entry_source": "virtual_governance"},
    }
    agents.update(cards or {})
    (root / "portal/agent_ide_bundle.json").write_text(
        json.dumps({"agents": agents}, ensure_ascii=False), encoding="utf-8")
    return root


def test_ok_fixture(tmp_path):
    rep = CN.check(_root(tmp_path))
    assert rep["ok"] is True and rep["violations"] == []
    assert rep["stats"]["in_headcount"] == 2 and rep["stats"]["virtual"] == 1


def test_duplicate_in_headcount(tmp_path):
    root = _root(tmp_path, cards={"B1": {"cn_name": "甲", "name": "甲",
                                        "entry_source": "org_profiles"}})
    rep = CN.check(root)
    assert rep["ok"] is False
    assert any(v["code"] == "DUP_IN_HEADCOUNT" for v in rep["violations"])


def test_virtual_requires_qualifier_and_no_shadow(tmp_path):
    root = _root(tmp_path, cards={"ZHAOYUN": {"cn_name": "赵云",
                                              "name": "赵云",
                                              "entry_source": "virtual_governance"}})
    rep = CN.check(root)
    assert any(v["code"] == "VIRTUAL_UNQUALIFIED" for v in rep["violations"])
    root2 = _root(tmp_path / "r2", cards={
        "ZHAOYUN": {"cn_name": "甲", "name": "甲", "entry_source": "virtual_governance"}})
    rep2 = CN.check(root2)
    assert any(v["code"] == "VIRTUAL_SHADOWS_HEADCOUNT" for v in rep2["violations"])


def test_dup_hr_and_missing_name(tmp_path):
    root = _root(tmp_path, hr={"B1": "甲"})
    rep = CN.check(root)
    assert any(v["code"] == "DUP_HR_NAME" for v in rep["violations"])
    root2 = _root(tmp_path / "r2", cards={"A1": {"name": "", "entry_source": "org_profiles"}})
    rep2 = CN.check(root2)
    assert any(v["code"] == "NAME_MISSING" for v in rep2["violations"])


def test_cli_exit_codes(tmp_path, capsys):
    good = _root(tmp_path / "good")
    assert CN.main(["--root", str(good), "--json"]) == 0
    capsys.readouterr()  # 清掉 good 的输出，避免两段 JSON 拼接
    bad = _root(tmp_path / "bad", cards={"B1": {"cn_name": "甲", "name": "甲",
                                                "entry_source": "org_profiles"}})
    assert CN.main(["--root", str(bad), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
