# -*- coding: utf-8 -*-
"""桥文件开关与注册表同源（R27 G3 遗留 DRIFT 的收口）。

判例 2026-11-13：`run_cohorts_intel.py` / `run_houfeng_intel.py` 每日重生成
`target_queue_*.json` 时把开关写回 `false`，而注册表 11-11 R22 已是 `true`，
`tools/registry_guard.py` 每天报 DRIFT —— 功能无碍，但「看起来关了、其实开着」
会持续误导审计。

要锁死三条：
  1) 取值一律来自注册表，缺文件/坏 JSON/缺字段都回退默认值、不抛异常（日更主链不能挂）；
  2) 说明文本随开关变化，不允许再出现第二处硬编码的说法；
  3) **生成器源码里不得再出现写死 False 的开关字面量**（防回退）。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NC = REPO / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import reg_flag  # noqa: E402


def _write_reg(path, sources):
    path.write_text(json.dumps({"schema": "t", "sources": sources},
                               ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_reads_true_from_registry(tmp_path):
    p = _write_reg(tmp_path / "reg.json", {"cohorts": {"xq_include": True}})
    assert reg_flag.xq_include("cohorts", reg_path=p) is True


def test_reads_false_from_registry(tmp_path):
    p = _write_reg(tmp_path / "reg.json", {"cohorts": {"xq_include": False}})
    assert reg_flag.xq_include("cohorts", reg_path=p) is False


def test_missing_source_falls_back_to_default(tmp_path):
    p = _write_reg(tmp_path / "reg.json", {"other": {"xq_include": True}})
    assert reg_flag.xq_include("cohorts", reg_path=p) is False
    assert reg_flag.xq_include("cohorts", default=True, reg_path=p) is True


def test_source_without_field_falls_back(tmp_path):
    p = _write_reg(tmp_path / "reg.json", {"cohorts": {"file": "x.json"}})
    assert reg_flag.xq_include("cohorts", default=False, reg_path=p) is False


def test_corrupt_or_missing_file_never_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert reg_flag.xq_include("cohorts", reg_path=str(bad)) is False
    assert reg_flag.xq_include("cohorts", default=True, reg_path=str(bad)) is True
    assert reg_flag.xq_include("cohorts", reg_path=str(tmp_path / "nope.json")) is False


def test_note_text_follows_switch(tmp_path):
    on = _write_reg(tmp_path / "on.json", {"cohorts": {"xq_include": True}})
    off = _write_reg(tmp_path / "off.json", {"cohorts": {"xq_include": False}})
    assert "已并入" in reg_flag.xq_note("cohorts", reg_path=on)
    assert "仅登记" in reg_flag.xq_note("cohorts", reg_path=off)
    assert reg_flag.xq_note("cohorts", reg_path=on) != \
        reg_flag.xq_note("cohorts", reg_path=off)


def test_generators_have_no_hardcoded_flag_literal():
    """防回退：生成器里不许再写死开关字面量（必须走 reg_flag）。"""
    for fname, lit in (("run_cohorts_intel.py", '"xq_include_cohorts": False'),
                       ("run_cohorts_intel.py", '"xq_include_cohorts": True'),
                       ("run_houfeng_intel.py", '"xq_include_candidates": False'),
                       ("run_houfeng_intel.py", '"xq_include_candidates": True')):
        src = (NC / fname).read_text(encoding="utf-8")
        assert lit not in src, "%s 仍写死 %s" % (fname, lit)


def test_generators_do_use_reg_flag():
    for fname in ("run_cohorts_intel.py", "run_houfeng_intel.py"):
        src = (NC / fname).read_text(encoding="utf-8")
        assert "import reg_flag" in src, fname
        assert "reg_flag.xq_include(" in src, fname


def test_live_registry_is_consistent_with_bridges():
    """对真实文件跑一次守卫：桥文件与注册表必须同为一致态（OK，不含 DRIFT）。"""
    if not (NC / "selection_universe.json").exists():
        return
    import subprocess
    r = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(REPO / "tools" / "registry_guard.py"), "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    data = json.loads(r.stdout)
    drift = [d["source"] for d in data.get("drift", [])]
    assert drift == [], "仍有漂移：%s" % drift
