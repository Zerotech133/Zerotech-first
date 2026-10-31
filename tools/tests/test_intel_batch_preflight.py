# -*- coding: utf-8 -*-
"""批次入库前质量闸离线测试（不写库/不联网）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "intel_batch_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("_batch_preflight", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _master(tmp_path):
    p = tmp_path / "names.json"
    p.write_text(json.dumps({
        "300750.SZ": "宁德时代", "300542.SZ": "新晨科技", "300872.SZ": "天阳科技",
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_preflight_hard_dupe_weak_style(tmp_path):
    m = _load()
    rows = [
        {"id": "a1", "title": "宁德时代扩产", "bias": "bull", "jev": {
            "companies": ["300750"], "reason": "宁德时代扩产", "impact": "medium"}},
        {"id": "a2", "title": "天阳科技新设子公司", "bias": "neutral", "jev": {
            "companies": ["300542"], "reason": "天阳科技", "impact": "medium"}},
        {"id": "a3", "title": "网评文章", "bias": "bull", "jev": {
            "companies": [], "reason": "评论", "impact": "medium"}},
        {"id": "a4", "title": "重复标题", "bias": "neutral", "jev": {
            "companies": [], "reason": "x", "impact": "medium"}},
        {"id": "a5", "title": "重复标题", "bias": "neutral", "jev": {
            "companies": [], "reason": "y", "impact": "medium"}},
    ]
    p = tmp_path / "batch.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                 encoding="utf-8")
    rep = m.check(m.load_records(p), _master(tmp_path))
    hard = {(e.get("id"), e["error"]) for e in rep["hard_errors"]}
    assert ("a2", "company_name_mismatch") in hard
    assert rep["duplicate_titles"]
    assert rep["ok"] is False
    assert any(e["id"] == "a3" for e in rep["style_risks"])


def test_preflight_reason_anchor_cluster(tmp_path):
    m = _load()
    rows = [
        {"id": "b1", "title": "油价博弈", "bias": "bear", "jev": {
            "companies": [], "reason": "利多因素主导市场", "impact": "medium"}},
        {"id": "b2", "title": "胡塞袭击沙特", "bias": "bear", "jev": {
            "companies": [], "reason": "地缘风险", "impact": "medium"}},
        {"id": "b3", "title": "也门局势", "bias": "bear", "jev": {
            "companies": [], "reason": "冲突", "impact": "medium"}},
        {"id": "b4", "title": "伊朗表态", "bias": "bear", "jev": {
            "companies": [], "reason": "紧张", "impact": "medium"}},
    ]
    p = tmp_path / "batch.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows),
                 encoding="utf-8")
    rep = m.check(m.load_records(p), _master(tmp_path))
    assert any(e["error"] == "reason_bias_contradiction"
               for e in rep["hard_errors"])
    assert rep["counts"]["anchor_missing"] == 4
    assert rep["event_clusters"]


def test_preflight_clean(tmp_path):
    m = _load()
    rows = [{"id": "a1", "title": "宁德时代扩产", "bias": "bull", "jev": {
        "companies": ["300750"], "reason": "宁德时代扩产", "impact": "medium"}}]
    p = tmp_path / "batch.jsonl"
    p.write_text(json.dumps(rows[0], ensure_ascii=False), encoding="utf-8")
    rep = m.check(m.load_records(p), _master(tmp_path))
    assert rep["ok"] is True
    assert rep["counts"]["hard"] == 0
