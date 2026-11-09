# -*- coding: utf-8 -*-
"""W2：export.py 导出前 PII 清洗（L1）回归测试。

用临时 KB 目录 + 最小 ZIP 现场跑 main()，验证：
- 标题/简介/评论正文在写出前被 scrub+normalize；
- 报告与 pii receipt 记录变更统计；
- --no-pii-clean 逃生开关保持原文（仅排障）；
- 找不到 pii_policy 时 fail-closed（rc=2，不产出）。
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXPORT_PY = REPO / "tools" / "kb_corpus" / "export.py"
INTEL_KG = Path(r"C:\项目\intel-kg")


def _load_export():
    spec = importlib.util.spec_from_file_location("kb_corpus_export_under_test", EXPORT_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_zip(sub: Path) -> Path:
    sub.mkdir(parents=True, exist_ok=True)
    zpath = sub / "obs_001.zip"
    meta = {
        "entity_type": "video_metadata",
        "collected_at": "2026-01-02T00:00:00+08:00",
        "snapshot_id": "S1",
        "payload": {"video_id": "v1", "info": {
            "channel_id": "c1",
            "title": "课程 联系 13800138000",
            "description": "访问 https://example.com/a 或邮件 a@b.com",
            "upload_date": "20260101", "duration": 60,
            "view_count": 10, "like_count": 1, "comment_count": 1,
            "tags": ["t"], "language": "zh",
        }},
    }
    comments = {
        "entity_type": "video_comments",
        "collected_at": "2026-01-02T00:00:00+08:00",
        "snapshot_id": "S1",
        "payload": {"video_id": "v1", "comments": [{
            "id": "c1", "parent": "root",
            "text": "联系 138   0013   8000 谢谢",
            "author_id": "u1", "like_count": 1,
            "published_at": "2026-01-01",
        }]},
    }
    body = "\n".join(json.dumps(x, ensure_ascii=False) for x in (meta, comments)) + "\n"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("observations/part-000.jsonl.gz", gzip.compress(body.encode("utf-8")))
    return zpath


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEL_KG_ROOT", str(INTEL_KG))
    sub = tmp_path / "01_数据分包"
    _make_zip(sub)
    mod = _load_export()
    mod.KB = tmp_path
    mod.SUB = sub
    mod.OUTD = tmp_path / "10_规范化"
    mod.REP = tmp_path / "90_报告"
    return mod, tmp_path


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_export_cleans_title_description_and_comments(kb):
    mod, root = kb
    assert mod.main(["--limit-zips", "1"]) == 0

    videos = _read_jsonl(root / "10_规范化" / "videos.jsonl")
    comments = _read_jsonl(root / "10_规范化" / "comments.jsonl")
    assert len(videos) == 1 and len(comments) == 1
    v, c = videos[0], comments[0]
    assert "[PHONE]" in v["title"] and "13800138000" not in v["title"]
    assert "[URL]" in v["description"] and "https://example.com/a" not in v["description"]
    assert "[EMAIL]" in v["description"] and "a@b.com" not in v["description"]
    assert "[PHONE]" in c["text"] and "138" not in c["text"]
    assert len(c["author_hash"]) == 16

    report = json.loads((root / "90_报告" / "p1_export_report.json").read_text(encoding="utf-8"))
    assert report["pii"]["enabled"] is True
    assert report["pii"]["videos_changed"] == 1
    assert report["pii"]["comments_changed"] == 1
    assert report["pii"]["hit_rules"]["phone"] >= 2

    receipt = json.loads((root / "90_报告" / "p1_export_pii_receipt.json").read_text(encoding="utf-8"))
    assert receipt["action"] == "export_pii_clean"
    assert receipt["stats"]["videos_changed"] == 1
    assert receipt["stats"]["comments_changed"] == 1


def test_export_no_pii_clean_keeps_raw_text(kb):
    mod, root = kb
    assert mod.main(["--limit-zips", "1", "--no-pii-clean"]) == 0
    v = _read_jsonl(root / "10_规范化" / "videos.jsonl")[0]
    c = _read_jsonl(root / "10_规范化" / "comments.jsonl")[0]
    assert "13800138000" in v["title"]
    assert "138   0013   8000" in c["text"]
    report = json.loads((root / "90_报告" / "p1_export_report.json").read_text(encoding="utf-8"))
    assert report["pii"]["enabled"] is False


def test_export_fail_closed_without_policy(kb, monkeypatch):
    mod, root = kb
    monkeypatch.setattr(mod, "_find_intel_kg_root", lambda: None)
    assert mod.main(["--limit-zips", "1"]) == 2
    assert not (root / "10_规范化" / "videos.jsonl").exists()
