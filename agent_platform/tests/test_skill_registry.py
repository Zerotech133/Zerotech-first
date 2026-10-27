"""技能注册表测试（WO-SKILLS-REGISTRY）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import logging

_WT = Path(__file__).resolve().parents[2]
_TOOLS = _WT / "agent_platform" / "tools"
# 真实 profile 只存在于开发机（~/.hermes/profiles/zhaoyun）；服务器 verify 环境没有，
# 依赖它的「真环境全量验证」用例在服务器上 skip（否则新门的零允许清单会误报 FAIL）。
_DEV_PROFILE = Path.home() / ".hermes" / "profiles" / "zhaoyun"
_DEV_ENV_SKIP = pytest.mark.skipif(
    not _DEV_PROFILE.is_dir(),
    reason="依赖本机 Hermes 真实 profile（服务器 verify 环境无 ~/.hermes/profiles/zhaoyun）")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = _load("skill_registry", _TOOLS / "skill_registry.py")
BS = _load("build_skills", _TOOLS / "build_skills.py")
CA = _load("capability_self_audit", _TOOLS / "capability_self_audit.py")


def test_registry_total_and_files():
    reg = SR.load_registry()
    assert reg.get("total") == 14, reg.get("total")
    assert len(reg.get("skills") or []) == 14
    for s in reg["skills"]:
        p = _WT / s["path"]
        assert p.exists(), s["name"]
        assert (p.parent / "scripts" / "verify.py").exists(), s["name"]
        assert s.get("verify_cmd", "").endswith("verify.py")


@_DEV_ENV_SKIP
def test_verify_all_passes():
    rep = SR.verify_all()
    assert rep["ok"] is True, rep.get("failed")
    assert rep["total"] == 14


def test_verify_unknown_and_traversal():
    bad = SR.verify("no-such-skill")
    assert bad["ok"] is False
    try:
        SR._verify_script("../escape")
        raise AssertionError("should reject traversal")
    except ValueError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tests/test_skill_registry.py", exc)


def test_build_is_idempotent_with_tmp_caps(tmp_path, monkeypatch):
    caps = tmp_path / "CAPABILITIES.json"
    caps.write_text(
        json.dumps(
            {
                "generated_at": "2026-10-27T06:00:00+08:00",
                "total": 2,
                "ready": 2,
                "capabilities": [
                    {
                        "name": "cap-a",
                        "what": "A",
                        "how": "python x.py",
                        "boundary": "只读",
                        "status": "ready",
                    },
                    {
                        "name": "cap-b",
                        "what": "B",
                        "how": "手工",
                        "boundary": "只读",
                        "status": "ready",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(BS, "SKILLS", tmp_path / "skills")
    one = BS.build(caps)
    two = BS.build(caps)
    assert one["total"] == two["total"] == 2
    assert (tmp_path / "skills" / "cap-a" / "SKILL.md").exists()
    assert (tmp_path / "skills" / "registry.json").exists()


@_DEV_ENV_SKIP
def test_audit_uses_registry_for_real_profile():
    rep = CA.check()  # 默认真实 profile → 走 skills_registry
    assert rep.get("source") == "skills_registry"
    assert "drift" in rep and isinstance(rep["drift"], list)
    assert rep["ok"] is True, rep.get("drift")


def test_audit_fallback_for_tmp_profile(tmp_path):
    prof = tmp_path / "profile"
    (prof / "skills").mkdir(parents=True)
    (prof / "skills" / "CAPABILITIES.json").write_text(
        json.dumps(
            {
                "generated_at": "2020-01-01T00:00:00+08:00",
                "total": 0,
                "ready": 0,
                "capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    rep = CA.check(prof)
    assert rep.get("source") != "skills_registry"
    assert rep["stale"] is True
