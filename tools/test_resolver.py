# -*- coding: utf-8 -*-
"""W1 resolver：7 组合引用全解析台账（分母锁防静默扩/缩）；未知引用不假绿。

G7 分母锁演进（2026-11-05 W1-PILOT-TOOLFIX-20261104）：
- 旧锁 = manifests 6 / refs 131（18459f82e，2026-11-04，当时仅 6 组合）。
- 之后合法并入 dev-legion（WO-CAOCAO-FRAME）与 ACAD1-DATAREFS 等，旧单值分母无法
  区分「合法演进 vs 静默漂移」。改为**组合级台账**：每个组合的引用数逐一锁定，
  同时锁组合集合，任何增删都必须显式更新本表（审计可见），保留防扩/缩分母语义。
- 2026-11-05 审计后基线 = 7 组合 / 161 引用；data-warehouse 的 v2_zhice_tables
  聚合口径移出 requires、改指 wave1 已登记物理表（11→+8 refs），v2_bar_daily 补
  declared 登记（DB 证据见 dataset_refs_declared.json）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import resolver as R  # noqa: E402

#: G7 分母锁台账（组合 id → 引用数）；新增/删除组合或引用必须同步改这里。
EXPECTED_COMPOSITIONS = {
    "acad1-legion": 34,
    "data-warehouse": 23,
    "dev-legion": 14,
    "huangzhong-acceptance": 11,
    "lusu-d1-flow": 33,
    "quant-domain": 16,
    "quant-strategy-line": 30,
}


def test_all_compositions_resolve():
    res = R.check_all(strict=True)
    s = res["summary"]
    counts = {m["id"]: len(m["refs"]) for m in res["manifests"]}
    assert counts == EXPECTED_COMPOSITIONS, (
        "分母锁漂移：实际 %r 期望 %r" % (counts, EXPECTED_COMPOSITIONS))
    assert s["manifests"] == len(EXPECTED_COMPOSITIONS) == 7
    assert s["refs"] == sum(EXPECTED_COMPOSITIONS.values()) == 161
    assert s["errors"] == 0 and s["warnings"] == 0
    assert s["ok"] is True


def test_unknown_agent_is_error():
    # 直接检查解析核心（不落 manifest 文件）
    regs = __import__("composition").load_registries()
    errs, warns = [], []
    __import__("composition").resolve_ref("agent/NOT_EXIST", regs, errs, warns)
    assert errs and "未登记" in errs[0]


def test_manifest_check_returns_refs():
    p = ROOT / "compositions" / "acad1-legion" / "manifest.yaml"
    res = R.check_manifest(p)
    assert res["ok"] is True and len(res["refs"]) > 10


def test_manifest_strict_propagates_warning(monkeypatch):
    import resolver as R2
    monkeypatch.setattr(R2, "check_manifest", lambda p: {
        "manifest": str(p), "ok": True, "errors": 0, "warnings": 1, "refs": []})
    assert R2.main(["--manifest", "x.yaml", "--strict", "--json"]) == 1
    # 非 strict 保持原行为
    assert R2.main(["--manifest", "x.yaml", "--json"]) == 0
