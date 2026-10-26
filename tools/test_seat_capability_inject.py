# -*- coding: utf-8 -*-
"""田丰完整席位能力/技能卡结构测试（K-QUANT-20261026-TFSEAT）。"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
MOD_PATH = HERE / "seat_capability_inject.py"


def _load():
    spec = importlib.util.spec_from_file_location("seat_capability_inject_t", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()

TIANFENG_EXPECTED = {
    "devtask", "ticket-gate", "council-route", "health-card", "task-exit-audit",
    "experience-review", "z-series", "memory-card", "council-hall", "docs-index",
    "llm-glm", "mutual-review", "case-sign-chain", "governance-ai",
    "worktree-hygiene",
}
REQUIRED_KEYS = {"name", "what", "how", "boundary", "status"}
VALID_STATUS = {"ready", "pending_key", "pending_wiring"}


def test_tianfeng_capability_set_complete():
    caps = M.CAPABILITIES["tianfeng"]
    names = [c["name"] for c in caps]
    assert len(names) == len(set(names)), "能力名重复"
    assert set(names) == TIANFENG_EXPECTED
    assert len(caps) == 15


def test_tianfeng_capability_fields_valid():
    for c in M.CAPABILITIES["tianfeng"]:
        assert REQUIRED_KEYS <= set(c), c
        assert c["status"] in VALID_STATUS, c
        assert c["what"] and c["how"] and c["boundary"], c


def test_every_tianfeng_capability_has_skill_card():
    card_names = set(M.SKILL_CARDS["tianfeng"])
    cap_names = {c["name"] for c in M.CAPABILITIES["tianfeng"]}
    assert card_names == cap_names, "缺卡: %s / 多卡: %s" % (
        sorted(cap_names - card_names), sorted(card_names - cap_names))


def test_skill_card_format():
    for name, body in M.SKILL_CARDS["tianfeng"].items():
        assert body.startswith("---\nname: "), name
        assert "description:" in body.split("---")[1], name
        assert "\n# " in body, name


def test_existing_seats_unchanged():
    assert len(M.CAPABILITIES["zhaoyun"]) == 14
    assert len(M.CAPABILITIES["zhuge"]) == 8
    assert set(M.SKILL_CARDS["zhaoyun"]) == {
        "task-exit-audit", "task-launch-probe", "mutual-review"}
