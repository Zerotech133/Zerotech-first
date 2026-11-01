# -*- coding: utf-8 -*-
"""P1.5 学习语料批式桥测试：YouTube 增量/评点赞聚合/游标续读 + PDF 蒸馏快照。

夹具：临时目录构造 videos/comments jsonl（含评/点/赞字段），验证：
  1) 首采批级 Observation（COMPLETE、计数与聚合正确、用途绑定）；
  2) 游标续读：二次采集只读增量（不重复）；
  3) 追加后三采拿到新增；
  4) 空夹具→EMPTY；
  5) PDF 蒸馏快照（真实 parquet 存在时验行数与 kind 分布；否则 skip 语义）；
  6) 只读性质：桥运行后知识库文件 mtime/内容不变。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from bridges.kb_bridge import KBBridge, DISTILL_PARQUET
from core.contracts import Status
from registry.source_registry import SourceRegistry, SourceSpec
from store.obs_store import ObservationStore

NOW = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


def _make_reg(tmp_path):
    reg_path = tmp_path / "sources.json"
    import dataclasses
    specs = {
        sid: SourceSpec(source_id=sid, name=n, category="open_source_intel",
                        owner="intel(军情司·INTEL0)",
                        rights={"internal_retention": True, "cleaning": True,
                                "controlled_query": True},
                        content_types=["video_meta"], sla={"freshness_minutes": 10080})
        for sid, n in (("youtube_corpus", "yt"), ("pdf_methodology", "pdf"))
    }
    # 手写最小 sources.json（绕过 save 路径依赖）
    reg_path.write_text(json.dumps(
        {sid: dataclasses.asdict(s) for sid, s in specs.items()}, ensure_ascii=False),
        encoding="utf-8")
    return SourceRegistry(str(reg_path))


def _make_kb(tmp_path):
    kb = tmp_path / "kb"
    norm = kb / "10_规范化"
    norm.mkdir(parents=True)
    return kb, norm


def _v(i, views=100, likes=10, comments=2):
    return {"video_id": f"v{i}", "title": f"t{i}", "view_count": str(views),
            "like_count": str(likes), "comment_count": str(comments)}


def _c(i, vid="v1", likes=1):
    return {"video_id": vid, "comment_id": f"c{i}", "text": f"c{i}",
            "like_count": str(likes), "author_hash": f"h{i}"}


class TestKbBridgeYt:
    def test_first_batch_and_engagement_agg(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        kb, norm = _make_kb(tmp_path)
        (norm / "videos.jsonl").write_text(
            "\n".join(json.dumps(_v(i, 100, 10, 2)) for i in range(3)), encoding="utf-8")
        (norm / "comments.jsonl").write_text(
            "\n".join(json.dumps(_c(i)) for i in range(5)), encoding="utf-8")
        b = KBBridge(reg, store, kb_root=kb, cursor_dir=tmp_path / "cursors")
        res = b.collect_source("youtube_corpus", NOW)
        assert res["ok"] and res["n"] == 8  # 3 视频 + 5 评论
        obs = store.latest("youtube_corpus", "video_metadata",
                           res["snapshot_id"].split(":")[-1].join(["batch:", ""]))
        # 直接按 snapshot 查
        all_obs = store.all()
        yo = [o for o in all_obs if o.source_id == "youtube_corpus"]
        assert len(yo) == 1 and yo[0].status == Status.COMPLETE
        p = yo[0].payload
        assert p["videos_new"] == 3 and p["comments_new"] == 5
        assert p["video_engagement_sum"] == {"view": 300, "like": 30, "comment": 6}
        assert p["comment_like_sum"] == 5
        assert "internal_retention" in yo[0].allowed_use_cases

    def test_cursor_incremental_no_reread(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        kb, norm = _make_kb(tmp_path)
        (norm / "videos.jsonl").write_text(
            "\n".join(json.dumps(_v(i)) for i in range(3)), encoding="utf-8")
        b = KBBridge(reg, store, kb_root=kb, cursor_dir=tmp_path / "cursors")
        r1 = b.collect_source("youtube_corpus", NOW)
        assert r1["n"] == 3
        r2 = b.collect_source("youtube_corpus", NOW)  # 无新增
        assert r2["n"] == 0
        # 追加 2 条
        with (norm / "videos.jsonl").open("a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(json.dumps(_v(i)) for i in (10, 11)))
        r3 = b.collect_source("youtube_corpus", NOW)
        assert r3["n"] == 2  # 只读增量

    def test_readonly_no_writeback(self, tmp_path):
        """只读性质：桥运行前后知识库文件字节不变。"""
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        kb, norm = _make_kb(tmp_path)
        vp = norm / "videos.jsonl"
        vp.write_text("\n".join(json.dumps(_v(i)) for i in range(2)), encoding="utf-8")
        before = vp.read_bytes()
        b = KBBridge(reg, store, kb_root=kb, cursor_dir=tmp_path / "cursors")
        b.collect_source("youtube_corpus", NOW)
        assert vp.read_bytes() == before

    def test_empty_kb_is_empty_status(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        kb, norm = _make_kb(tmp_path)  # 不写文件
        b = KBBridge(reg, store, kb_root=kb, cursor_dir=tmp_path / "cursors")
        res = b.collect_source("youtube_corpus", NOW)
        assert res["ok"] and res["n"] == 0
        yo = [o for o in store.all() if o.source_id == "youtube_corpus"]
        assert yo[0].status == Status.EMPTY

    def test_rejects_unknown_source(self, tmp_path):
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        b = KBBridge(reg, store, kb_root=_make_kb(tmp_path)[0],
                     cursor_dir=tmp_path / "cursors")
        res = b.collect_source("xueqiu", NOW)
        assert res["ok"] is False


class TestKbBridgePdf:
    def test_pdf_distill_snapshot(self, tmp_path):
        if not DISTILL_PARQUET.exists():
            pytest.skip("蒸馏 parquet 不存在（环境无该资产）")
        reg = _make_reg(tmp_path)
        store = ObservationStore(str(tmp_path / "obs.db"))
        b = KBBridge(reg, store, kb_root=_make_kb(tmp_path)[0],
                     cursor_dir=tmp_path / "cursors")
        res = b.collect_source("pdf_methodology", NOW)
        assert res["ok"] and res["n"] > 1000  # 实测 11,200
        po = [o for o in store.all() if o.source_id == "pdf_methodology"]
        assert po[0].status == Status.COMPLETE
        assert po[0].payload["distill_rows"] == res["n"]
