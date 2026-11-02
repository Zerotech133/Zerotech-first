# -*- coding: utf-8 -*-
"""card_precheck_laomu：清洁/领域门/去重/预审统计。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cpl", REPO / "tools/kb_corpus/card_precheck_laomu.py")
cpl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpl)


def test_clean_concept_strips_markdown_and_dash():
    assert cpl.clean_concept("**突破幅度——刺穿一点**") == "突破幅度"
    assert cpl.clean_concept("- **第 10 个**") == "第 10 个"


def test_evaluate_rejects_filler_and_accepts_domain_noun():
    base = set()
    ans = [""]
    bad = cpl.evaluate({"concept": "看这张图", "a": "看这张图，这是我们的交易记录。", "source": "x.md L1"}, base, ans)
    assert bad["action"] == "reject"
    good = cpl.evaluate({"concept": "**突破幅度**", "a": "突破幅度衡量价格穿出关键位后继续延伸的空间。", "source": "x.md L1"}, base, ans)
    assert good["action"] == "pass", good["issues"]


def test_build_dry_run_no_write():
    out = cpl.build(write=False)
    assert out["ok"] and out["dry_run"] is True
    assert out["summary"]["pass"] >= 1
