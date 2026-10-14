#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registry 测试：freeze 注册与幂等、同版本异 sha 拒改写、
active 切换、篡改检出（verify 对账）。"""

import json

import pytest

from gatekeeper.registry import Registry


def test_freeze_and_active(tmp_path):
    reg = Registry(str(tmp_path / "registry.json"))
    assert reg.active() == (None, None)
    meta = reg.freeze("v3_breadth40_med20", "a" * 16, note="现行判据")
    assert meta["sha"] == "a" * 16 and meta["note"] == "现行判据"
    ver, m = reg.active()
    assert ver == "v3_breadth40_med20" and m["sha"] == "a" * 16
    assert set(reg.list_versions()) == {"v3_breadth40_med20"}


def test_freeze_idempotent_same_sha(tmp_path):
    """同版本同 sha 重申合法（幂等），元数据不被改写。"""
    fp = str(tmp_path / "registry.json")
    reg = Registry(fp)
    reg.freeze("v3", "a" * 16, note="first")
    first = reg.list_versions()["v3"]
    reg.freeze("v3", "a" * 16)            # 重申
    again = reg.list_versions()["v3"]
    assert again == first                 # frozen_at/note 不变
    assert reg.active()[0] == "v3"


def test_freeze_rejects_sha_rewrite(tmp_path):
    """同版本号不同 sha → 拒绝（历史冻结不可改写）。"""
    reg = Registry(str(tmp_path / "registry.json"))
    reg.freeze("v3", "a" * 16)
    with pytest.raises(ValueError):
        reg.freeze("v3", "b" * 16)
    assert reg.verify("v3", "a" * 16) is True


def test_version_history_and_switch(tmp_path):
    """freeze 新版本即切 active，旧版本留史（v3→v4 链）。"""
    reg = Registry(str(tmp_path / "registry.json"))
    reg.freeze("v3_breadth40_med20", "a" * 16)
    reg.freeze("v4_obsband50", "b" * 16, note="观察带 50")
    assert reg.active() == ("v4_obsband50",
                            reg.list_versions()["v4_obsband50"])
    assert set(reg.list_versions()) == {"v3_breadth40_med20",
                                        "v4_obsband50"}
    assert reg.verify("v3_breadth40_med20", "a" * 16)


def test_tamper_detected(tmp_path):
    """手工改注册表里的冻结 sha → verify 对账检出。"""
    fp = str(tmp_path / "registry.json")
    reg = Registry(fp)
    reg.freeze("v3", "a" * 16)
    with open(fp, encoding="utf-8") as f:
        disk = json.load(f)
    disk["versions"]["v3"]["sha"] = "f" * 16        # 篡改
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(disk, f, ensure_ascii=False)
    assert reg.verify("v3", "a" * 16) is False      # 与冻结值对不上
    assert reg.verify("v3", "f" * 16) is True       # 现值即篡改值（可发现）
    assert reg.verify("v_nofile", "a" * 16) is False
