# -*- coding: utf-8 -*-
"""OS-FIX-W4CLOSE：push_flow 测试归因清单（feishu_console）回归。

归因语义：只对清单里 ts+kind+target 三者精确匹配的行从 lost24 剔除，
并在检查项文本里单独显示「已归因测试 N」；不删流水、不改 ok。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import feishu_console as fc  # noqa: E402


def test_load_annotations_skips_bad_lines(tmp_path):
    p = tmp_path / "ann.jsonl"
    good = {"ts": "2026-10-26T02:19:29",
            "kind": "auto:push_digest",
            "target": "ou_1", "reason": "W40 未挂调度测试发送"}
    p.write_text(json.dumps(good, ensure_ascii=False) + "\nnot-json\n\n",
                 encoding="utf-8")
    anns = fc._load_annotations(str(p))
    assert len(anns) == 1
    assert anns[0]["kind"] == "auto:push_digest"


def test_missing_annotations_file_is_empty(tmp_path):
    assert fc._load_annotations(str(tmp_path / "nope.jsonl")) == []


def test_is_annotated_requires_exact_triplet(tmp_path):
    anns = [{"ts": "2026-10-26T02:19:29", "kind": "auto:push_digest",
             "target": "ou_1"}]
    row = {"ts": "2026-10-26T02:19:29", "kind": "auto:push_digest",
           "target": "ou_1"}
    assert fc._is_annotated(row, anns) is True
    assert fc._is_annotated({**row, "ts": "2026-10-26T02:19:38"}, anns) is False
    assert fc._is_annotated({**row, "kind": "auto:news_collector"}, anns) is False
    assert fc._is_annotated({**row, "target": "ou_2"}, anns) is False
    assert fc._is_annotated(row, []) is False
