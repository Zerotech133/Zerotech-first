# -*- coding: utf-8 -*-
"""ACAD1→D1 桥对账器测试（2026-11-16 WO-ACAD1-D1-RECONCILE）。

在 tmp 内构造三份账本，验证「实测 vs 期望」与各类不一致的判定。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "acad1_bridge_reconcile_t", REPO / "tools" / "acad1_bridge_reconcile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rc = _load()
DATE = "2026-11-16"


def _seed(tmp_path, n=2, dup=False, plans_per=2, paper_only=True):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    codes = ["60000%d.SH" % i for i in range(n)]
    dec = [{"code": c, "source": "acad1_pool_bridge", "paper_only": paper_only,
            "evidence": [{"kind": "acad1_pool", "value": DATE + "/x"}]} for c in codes]
    if dup:
        dec.append(dict(dec[0]))
    ho = [{"code": c, "paper_only": paper_only,
           "rationale": "ACAD1 官方池 %s → D1" % DATE} for c in codes]
    wp = []
    for c in codes:
        for kind in ("stop", "take")[:plans_per]:
            wp.append({"plan_id": "wp-acad1-%s-%s-%s" % (DATE.replace("-", ""), c.split(".")[0], kind),
                       "paper_only": paper_only})
    for name, rows in (("d1_decisions.jsonl", dec), ("d1_handoffs.jsonl", ho),
                       ("watch_plans.jsonl", wp)):
        (d / name).write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    return d


def test_reconcile_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=2))
    res = rc.reconcile(DATE, expect=2)
    assert res["ok"] is True
    assert all(c["ok"] for c in res["checks"])
    assert [c["actual"] for c in res["checks"]][:3] == [2, 2, 4]


def test_reconcile_detects_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=2, dup=True))
    res = rc.reconcile(DATE, expect=2)
    assert res["ok"] is False
    bad = [c for c in res["checks"] if not c["ok"]]
    assert any("重复写入" in c["item"] for c in bad)


def test_reconcile_detects_missing_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=2, plans_per=1))  # 每只只有 1 条计划
    res = rc.reconcile(DATE, expect=2)
    assert res["ok"] is False
    wp = [c for c in res["checks"] if c["item"].startswith("watch_plans")][0]
    assert wp["actual"] == 2 and wp["expect"] == 4


def test_reconcile_detects_paper_only_violation(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=1, paper_only=False))
    res = rc.reconcile(DATE, expect=1)
    assert res["ok"] is False
    assert any("paper_only" in c["item"] and not c["ok"] for c in res["checks"])


def test_reconcile_detects_code_set_mismatch(tmp_path, monkeypatch):
    data = _seed(tmp_path, n=2)
    ho = data / "d1_handoffs.jsonl"
    rows = [json.loads(l) for l in ho.read_text(encoding="utf-8").splitlines()]
    rows[1]["code"] = "999999.SH"  # 交棒代码与裁决不一致
    ho.write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(rc, "DATA", data)
    res = rc.reconcile(DATE, expect=2)
    assert res["ok"] is False
    assert any("代码集合" in c["item"] and not c["ok"] for c in res["checks"])


def test_coverage_names_missing_codes(tmp_path, monkeypatch):
    """覆盖率为 P2 独立判定：池内多出未裁标的 → 只该项 FAIL，并指名缺失代码。"""
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=2))
    res = rc.reconcile(DATE, None, {"600000.SH", "600001.SH", "999999.SZ"})
    cov = [c for c in res["checks"] if c["item"].startswith("覆盖率")][0]
    assert cov["ok"] is False and cov["detail"] == ["999999.SZ"]
    # 其余项不受影响（这正是"单独红"的含义）
    assert all(c["ok"] for c in res["checks"] if not c["item"].startswith("覆盖率"))
    assert res["ok"] is False


def test_coverage_pass_when_pool_subset(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=3))
    res = rc.reconcile(DATE, None, {"600000.SH", "600001.SH"})  # 池只是已判的子集
    cov = [c for c in res["checks"] if c["item"].startswith("覆盖率")][0]
    assert cov["ok"] is True and res["ok"] is True


def test_main_check_returns_nonzero_on_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=2, dup=True))
    monkeypatch.setattr(rc, "pool_cn_codes", lambda timeout=15: {"600000.SH", "600001.SH"})
    assert rc.main(["--pool-date", DATE, "--check"]) == 1
    out = capsys.readouterr().out
    assert "RECONCILE FAIL" in out


def test_main_markdown_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rc, "DATA", _seed(tmp_path, n=1))
    monkeypatch.setattr(rc, "pool_cn_codes", lambda timeout=15: {"600000.SH"})
    assert rc.main(["--pool-date", DATE, "--markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("| 产出 | 实测 | 期望 | 结果 |")
    assert "OK" in out and "MISMATCH" not in out
