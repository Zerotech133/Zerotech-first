# -*- coding: utf-8 -*-
"""index_governance_assets 生成物分类单测（K-ORG-20261031-PLANREG，2026-10-31）。

锁死：
  · `PLAN_REGISTER*` 生成物单列 `generated`，不计入 unspecified/declared/frozen 等计划状态；
  · 真实 unspecified 仍如实统计（不掩盖真实计划债）；
  · 生成 JSON/MD 自洽（counts 求和==total、MD 含 generated 行）；
  · 重复生成幂等（counts/逐文件 state 不漂移）；
  · `--plans-only` 不触碰仓外交付物索引。
全部在 tmp docs/plans 上跑，不碰真实台账。
"""
import importlib.util
import json
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "index_governance_assets.py"


def _load():
    spec = importlib.util.spec_from_file_location("index_governance_assets_t", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _fixture(tmp_path, m):
    root = tmp_path / "repo"
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True)
    _write(plans / "2026-10-01_声明.md", "# A\n\n> STATUS: active\n")
    _write(plans / "2026-10-02_待批.md", "# B\n\n> STATUS: pending_approval\n")
    _write(plans / "2026-10-03_取代.md", "# C\n\n> STATUS: superseded\n")
    _write(plans / "2026-10-04_冻结.md", "# D\n\n> STATUS: frozen\n")
    _write(plans / "2026-10-05_真未标注.md", "# E\n\n普通正文，无生命标注。\n")
    _write(plans / "2026-10-06_中文状态.md", "# F\n\n> 状态：工具 v1；演练数据以 work/ 为准。\n")
    _write(plans / "PLAN_REGISTER_20261031.md", "# 计划台账（PLAN_REGISTER）\n\n生成物\n")
    _write(plans / "PLAN_REGISTER_ALL_20261031.md",
           "# 计划台账（全量）\n\nunspecified 需 owner 补状态。\n")
    m.ROOT = root
    m.PLANS = plans
    m.OUT_PLANS = plans
    m.OUT_EXT = root / "docs" / "audit"
    m.EXT = tmp_path / "no_such_ext"
    return root, plans


def _counts(rows):
    out = {}
    for r in rows:
        out[r["state"]] = out.get(r["state"], 0) + 1
    return out


def test_generated_plan_registers_not_plan_state(tmp_path):
    m = _load()
    _root, plans = _fixture(tmp_path, m)
    rows = m.plan_rows()
    by_name = {Path(r["path"]).name: r["state"] for r in rows}
    assert by_name["PLAN_REGISTER_20261031.md"] == "generated"
    assert by_name["PLAN_REGISTER_ALL_20261031.md"] == "generated"
    # 生成物不得冒充 declared/unspecified
    assert by_name["2026-10-01_声明.md"] == "declared"
    assert by_name["2026-10-02_待批.md"] == "pending_approval"
    assert by_name["2026-10-03_取代.md"] == "superseded"
    assert by_name["2026-10-04_冻结.md"] == "frozen"
    counts = _counts(rows)
    assert counts["generated"] == 2
    # 中文「> 状态：」应识别为 declared（2026-10-31 新计划实际用法）
    assert by_name["2026-10-06_中文状态.md"] == "declared"
    # 真实 unspecified 仍如实计 1（不掩盖真实计划债）
    assert counts["unspecified"] == 1
    assert counts["declared"] == 2 and counts["frozen"] == 1


def test_is_generated_plan_helper():
    m = _load()
    assert m.is_generated_plan(Path("docs/plans/PLAN_REGISTER_ALL_20261031.md"))
    assert m.is_generated_plan(Path("docs/plans/PLAN_REGISTER_20261031.md"))
    assert not m.is_generated_plan(Path("docs/plans/2026-10-01_计划.md"))
    assert not m.is_generated_plan(Path("docs/plans/README.md"))


def test_write_plan_register_self_consistent_and_idempotent(tmp_path):
    m = _load()
    _root, plans = _fixture(tmp_path, m)
    r1 = m.write_plan_register()
    data1 = json.loads(Path(r1["json"]).read_text(encoding="utf-8"))
    assert data1["schema"] == "plan-register-all/v1"
    assert data1["total"] == len(data1["plans"]) == 8
    assert sum(data1["counts"].values()) == data1["total"]
    assert data1["counts"]["generated"] == 2
    md1 = Path(r1["md"]).read_text(encoding="utf-8")
    assert "generated" in md1 and "不参与计划状态统计" in md1
    rows1 = [(p["path"], p["state"]) for p in data1["plans"]]

    r2 = m.write_plan_register()
    data2 = json.loads(Path(r2["json"]).read_text(encoding="utf-8"))
    assert data2["counts"] == data1["counts"], "重复生成 counts 不得漂移"
    assert data2["total"] == data1["total"]
    assert [(p["path"], p["state"]) for p in data2["plans"]] == rows1


def test_main_plans_only_leaves_external_index_untouched(tmp_path):
    m = _load()
    _root, plans = _fixture(tmp_path, m)
    ext_dir = m.OUT_EXT
    _write(ext_dir / "2026-10-31_仓外交付物索引_v1.md", "SENTINEL\n")
    before = (ext_dir / "2026-10-31_仓外交付物索引_v1.md").read_text(encoding="utf-8")
    assert m.main(["--plans-only"]) == 0
    assert (ext_dir / "2026-10-31_仓外交付物索引_v1.md").read_text(encoding="utf-8") == before
    assert not (ext_dir / "2026-10-31_仓外交付物索引_v1.json").exists()
    data = json.loads((plans / "PLAN_REGISTER_ALL_20261031.json").read_text(encoding="utf-8"))
    assert data["counts"]["generated"] == 2 and data["counts"]["unspecified"] == 1
