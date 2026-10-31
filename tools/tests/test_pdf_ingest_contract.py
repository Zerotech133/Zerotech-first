# -*- coding: utf-8 -*-
"""PDF 知识入库能力 · 契约测试（不跑 OCR，只校验接口/标准/注册）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "tools" / "kb_corpus" / "pdf_ingest.py"
STANDARD = REPO / "docs" / "standards" / "2026-10-31_PDF知识入库标准_v1.md"
REGISTRY = REPO / "agent_platform" / "registry" / "skill_registry.json"


def test_cli_exists_and_help():
    assert CLI.is_file(), "pdf_ingest.py 缺失"
    r = subprocess.run([sys.executable, "-X", "utf8", str(CLI), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    for cmd in ("scan", "ocr", "structure", "distill", "verify", "index", "all"):
        assert cmd in r.stdout, f"缺少子命令 {cmd}"


def test_standard_document():
    assert STANDARD.is_file(), "标准文档缺失"
    text = STANDARD.read_text(encoding="utf-8")
    for kw in ("scan", "ocr", "structure", "distill", "verify", "index", "验收", "版权"):
        assert kw in text, f"标准缺少 {kw}"


def test_registry_entry():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ids = {s["id"]: s for s in data["skills"]}
    assert "pdf_knowledge_ingest" in ids, "未登记到 skill_registry"
    entry = ids["pdf_knowledge_ingest"]
    assert entry["kind"] == "tool"
    assert entry["ref"] == "tools/kb_corpus/pdf_ingest.py"
    assert entry["enabled"] is True


def test_verify_thresholds_constant():
    r = subprocess.run([sys.executable, "-X", "utf8", "-c",
                        "import sys;sys.path.insert(0,r'%s');import pdf_ingest;"
                        "print(pdf_ingest.SCHEMA_VERSION)" % str(CLI.parent)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("1")
