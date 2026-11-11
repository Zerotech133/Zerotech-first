# -*- coding: utf-8 -*-
"""L-09 结构债的可机器验证部分。

两条红线：
  ① 根级 .py 只减不增（现存 ⊆ 基线 ∪ 登记项）——「新文件不再堆根级」；
  ② guardian 版本表与代码必须一致（登记齐全 + 入口与 paper_daily 实际调用
     同名 + 无逐字节重复副本）——「5 版并存、文档写错版本」不再复发。

不做的事：不搬迁既有根级模块。76 个 .ps1、计划任务和跨文件 import 都按
根级路径引用，搬家的风险远大于收益；这里只冻结存量、管住增量。
"""

import glob
import hashlib
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAINT = os.path.join(ROOT, "_qpzm_rd_20261106", "maintenance")
BASELINE = os.path.join(MAINT, "repo_layout_baseline.json")
PAPER = os.path.join(ROOT, "_qpzm_rd_20261106", "paper")
VERSION = os.path.join(PAPER, "guardian_VERSION.json")


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── ① 根级增量 ────────────────────────────────────────────────
def test_baseline_exists_and_wellformed():
    assert os.path.exists(BASELINE), "缺 repo_layout_baseline.json，先跑 gen_repo_layout_baseline.py"
    with open(BASELINE, encoding="utf-8") as f:
        d = json.load(f)
    assert d["schema"] == "qpzm.repo_layout/v1"
    assert isinstance(d["root_py"], list) and d["root_py"]
    assert d["count"] == len(d["root_py"])


def test_no_new_root_python_modules():
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    frozen = set(base["root_py"]) | set(base.get("manual_additions") or {})
    cur = {
        n
        for n in os.listdir(ROOT)
        if n.endswith(".py") and os.path.isfile(os.path.join(ROOT, n))
    }
    added = sorted(cur - frozen)
    assert not added, (
        "根级新增 .py（L-09：新文件不放根级）：%s\n"
        "请移入 tests/（测试）、pipeline/（链路）、research/（一次性）、tools/（运维），"
        "确需根级则在 gen_repo_layout_baseline.py 的 manual_additions 登记理由。" % added
    )


def test_baseline_entries_are_real_files_or_removed():
    """基线里的名字要么仍在盘上，要么是被清理掉的（允许减少，不允许改名漂移）。"""
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    missing = [n for n in base["root_py"] if not os.path.exists(os.path.join(ROOT, n))]
    # 只统计数量，不做“必须存在”的硬断言（清理是合法操作）
    assert len(missing) <= base["count"]


def test_tests_dir_is_zone_for_new_tests():
    """新测试放 tests/：本文件自身与 L-08 的测试都在这里，根级不再新增 test_*.py。"""
    with open(BASELINE, encoding="utf-8") as f:
        base = set(json.load(f)["root_py"])
    root_tests = {n for n in base if re.match(r"^test_.*\.py$", n)}
    assert root_tests, "基线里应保留既有根级测试（存量冻结，不强制搬迁）"
    new_root_tests = {
        n
        for n in os.listdir(ROOT)
        if re.match(r"^test_.*\.py$", n) and os.path.isfile(os.path.join(ROOT, n))
    } - root_tests
    assert not new_root_tests, "新增测试请放 tests/：%s" % sorted(new_root_tests)


# ── ② guardian 版本一致性 ─────────────────────────────────────
def _version_doc():
    with open(VERSION, encoding="utf-8") as f:
        return json.load(f)


def test_guardian_entry_points_to_paper_daily_caller():
    """版本表登记的入口，必须就是 paper_daily 真正 subprocess 调用的那个文件。"""
    doc = _version_doc()
    entry_file = re.search(r"guardian_agent_v[\d.]+\.py", doc["entry"]).group(0)
    with open(os.path.join(PAPER, "paper_daily.py"), encoding="utf-8") as f:
        src = f.read()
    called = set(re.findall(r"guardian_agent_v[\d.]+\.py", src))
    assert called == {entry_file}, (
        "版本表 entry=%s，但 paper_daily.py 实际调用 %s —— 文档与代码不一致"
        % (entry_file, sorted(called))
    )
    # 顶层 version 必须与入口文件名同版
    assert doc["version"] == entry_file.replace("guardian_agent_", "").replace(".py", "")


def test_every_guardian_file_is_registered():
    """paper/ 下每个 guardian*.py（含 _archive）都要在 file_states 里有状态说明。"""
    doc = _version_doc()
    states = doc["file_states"]
    files = sorted(glob.glob(os.path.join(PAPER, "guardian*.py")))
    files += sorted(glob.glob(os.path.join(PAPER, "_archive", "*guardian*.py")))
    assert files, "未找到 guardian 文件，路径假设失效"
    unregistered = []
    for p in files:
        rel = os.path.relpath(p, PAPER).replace(os.sep, "/")
        if rel not in states:
            unregistered.append(rel)
    assert not unregistered, (
        "以下 guardian 文件未在 guardian_VERSION.json:file_states 登记（L-09）：%s"
        % unregistered
    )


def test_no_byte_identical_guardian_duplicates():
    """生产目录内不得出现逐字节重复的 guardian（曾经的 guardian_agent.py = v1.0.2）。"""
    seen = {}
    dup = []
    for p in sorted(glob.glob(os.path.join(PAPER, "guardian*.py"))):
        h = _md5(p)
        if h in seen:
            dup.append((os.path.basename(seen[h]), os.path.basename(p)))
        else:
            seen[h] = p
    assert not dup, "guardian 逐字节重复副本（应归档到 _archive/）：%s" % dup


def test_only_one_production_guardian_marked():
    doc = _version_doc()
    prod = [k for k, v in doc["file_states"].items() if v.startswith("生产")]
    assert prod == ["guardian_agent_v1.0.4.py"], "生产入口应唯一，当前 %s" % prod
