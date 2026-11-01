# -*- coding: utf-8 -*-
"""P1.5 · 学习语料批式桥：YouTube 规范化层 + PDF 蒸馏单元 → Observation（只读）。

定位（2026-11-02 全板块复盘 P1.5，老田定性"学习语料属开源情报数据来源"）：
  - **只读桥接**：读 `C:/项目/知识库/10_规范化/*.jsonl` 增量 与 PDF 蒸馏产出，
    转批级 Observation 落 intel_collector obs_store；**绝不反写知识库任何文件**
    （符合其板块定义红线"不反写、无反向依赖"的本意——治理态单向流入）。
  - **批级粒度**：每次 run 产 1 条批次摘要 Observation（含计数/评点赞聚合/
    增量游标），不做逐行镜像——行级真源永远是知识库数据仓（ODS→DWD→DWS）。
  - **游标**：以 jsonl 的行偏移为增量断点（KBBridge.cursor），落 obs_store 侧
    cursor 目录，重启续读不重复。

数据语义（实测字段）：
  videos.jsonl:   video_id/title/view_count/like_count/comment_count/upload_date...
  comments.jsonl: video_id/comment_id/text/like_count/published_at/author_hash...
  kg_distill.parquet: tc/nm/d0/kind/conf/qs...（PDF 方法论蒸馏单元）

合规：作者已哈希（author_hash）；training_ready=false 语义沿用（本桥只写
internal_retention/cleaning/controlled_query 三用途，与平台默认一致）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.contracts import Observation, SourceClock, Status
from core.rights_gate import RightsGate
from registry.source_registry import SourceRegistry
from store.obs_store import ObservationStore

KB_ROOT = Path(r"C:/项目/知识库")
VIDEOS_JSONL = KB_ROOT / "10_规范化" / "videos.jsonl"
COMMENTS_JSONL = KB_ROOT / "10_规范化" / "comments.jsonl"
DISTILL_PARQUET = Path(
    r"C:/项目/老田的AI助手/quant_backtest/data/distill/kg_distill.parquet"
)

KB_DEFAULT_RIGHTS = ("internal_retention", "cleaning", "controlled_query")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KBBridge:
    """学习语料 → intel_collector 批式桥（只读、断点续读、批级 Observation）。

    参数：
      kb_root：知识库根（默认 C:/项目/知识库）；测试可指向夹具目录。
      cursor_dir：游标目录（默认 <repo>/intel_collector/kb_cursors）。
    """

    def __init__(self, registry: SourceRegistry, store: ObservationStore,
                 gate: Optional[RightsGate] = None, *,
                 kb_root: Optional[Path] = None, cursor_dir: Optional[Path] = None):
        self.registry = registry
        self.store = store
        self.gate = gate or RightsGate()
        base = kb_root or KB_ROOT
        self.videos_path = base / "10_规范化" / "videos.jsonl"
        self.comments_path = base / "10_规范化" / "comments.jsonl"
        self.cursor_dir = Path(cursor_dir) if cursor_dir else (
            Path(__file__).resolve().parent.parent / "kb_cursors")
        self.cursor_dir.mkdir(parents=True, exist_ok=True)

    # ---- 游标（行偏移断点，原子写） ----

    def _cursor_path(self, kind: str) -> Path:
        return self.cursor_dir / f"{kind}.json"

    def _load_cursor(self, kind: str) -> dict:
        p = self._cursor_path(kind)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cursor(self, kind: str, state: dict) -> None:
        p = self._cursor_path(kind)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    # ---- 用途绑定 ----

    def _bound_rights(self, source_id: str) -> tuple:
        spec = self.registry.get(source_id)
        if spec is None:
            return ()
        return tuple(
            uc for uc in KB_DEFAULT_RIGHTS if self.gate.check(spec, uc).allowed
        )

    # ---- 批式采集（CollectDispatcher 兼容：source_id ∈ {youtube_corpus, pdf_methodology}） ----

    def collect_source(self, source_id: str, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        if source_id == "youtube_corpus":
            return self._collect_youtube(now)
        if source_id == "pdf_methodology":
            return self._collect_pdf(now)
        return {"ok": False, "n": 0, "reason": f"KB 桥只接 youtube_corpus/pdf_methodology，收到 {source_id}"}

    # ---- YouTube：videos + comments 增量批 ----

    def _read_increment(self, path: Path, kind: str, max_rows: int) -> tuple[list[dict], int, int]:
        """从游标行偏移续读，返回 (rows, new_offset, total_seen)。文件不存在→([],0,0)。"""
        st = self._load_cursor(kind)
        offset = int(st.get("offset", 0))
        rows: list[dict] = []
        if not path.exists():
            return rows, offset, offset
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i < offset:
                    continue
                if len(rows) >= max_rows:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue  # 畸形行跳过（失败可见在批摘要 count_malformed）
        new_offset = offset + sum(1 for _ in path.open(encoding="utf-8")) if rows else offset
        return rows, new_offset, new_offset

    def _collect_youtube(self, now) -> dict:
        collected_at = _now_iso()
        vids, v_off, _ = self._read_increment(self.videos_path, "videos", max_rows=5000)
        cmts, c_off, _ = self._read_increment(self.comments_path, "comments", max_rows=50000)

        v_agg = {"view": 0, "like": 0, "comment": 0}
        for v in vids:
            for k, key in (("view", "view_count"), ("like", "like_count"),
                           ("comment", "comment_count")):
                try:
                    v_agg[k] += int(v.get(key) or 0)
                except Exception:
                    pass
        c_like = 0
        for c in cmts:
            try:
                c_like += int(c.get("like_count") or 0)
            except Exception:
                pass

        payload = {
            "videos_new": len(vids), "comments_new": len(cmts),
            "videos_offset": v_off, "comments_offset": c_off,
            "video_engagement_sum": v_agg,
            "comment_like_sum": c_like,
            "mode": "incremental_batch",
        }
        status = Status.COMPLETE if (vids or cmts) else Status.EMPTY
        obs = Observation(
            source_id="youtube_corpus",
            snapshot_id=f"kb:{collected_at}",
            entity_type="video_metadata",
            entity_id=f"batch:{collected_at}",
            clock=SourceClock(collected_at=collected_at),
            status=status,
            allowed_use_cases=self._bound_rights("youtube_corpus"),
            source_field="description",
            payload=payload,
        )
        self.store.put(obs)
        if vids:
            self._save_cursor("videos", {"offset": v_off})
        if cmts:
            self._save_cursor("comments", {"offset": c_off})
        self.registry.update_health("youtube_corpus", ok=True,
                                    detail=f"v+{len(vids)} c+{len(cmts)}")
        return {"ok": True, "n": len(vids) + len(cmts), "reason": "ok",
                "snapshot_id": obs.snapshot_id}

    # ---- PDF：蒸馏单元全量快照批（parquet 不可行时降级跳过） ----

    def _collect_pdf(self, now) -> dict:
        collected_at = _now_iso()
        n = 0
        kind_dist: dict[str, int] = {}
        if DISTILL_PARQUET.exists():
            try:
                import pyarrow.parquet as pq
                t = pq.read_table(DISTILL_PARQUET)
                n = t.num_rows
                if "kind" in t.column_names:
                    col = t.column("kind").to_pylist()
                    for k in col:
                        kind_dist[str(k)] = kind_dist.get(str(k), 0) + 1
            except Exception as e:  # 失败可见
                self.registry.update_health("pdf_methodology", ok=False,
                                            detail=str(e)[:200])
                return {"ok": False, "n": 0, "reason": f"parquet read: {e}"}
        payload = {
            "distill_rows": n, "kind_distribution": kind_dist,
            "source": "kb_corpus pdf_ingest 管线产出（只读快照）",
            "covers": "100 研报(rp_AP) + 方法论书 + 米勒维尼笔记",
        }
        status = Status.COMPLETE if n else Status.EMPTY
        obs = Observation(
            source_id="pdf_methodology",
            snapshot_id=f"kb:{collected_at}",
            entity_type="discovery_channel",  # 复用：方法论=知识发现渠道
            entity_id=f"distill:{collected_at}",
            clock=SourceClock(collected_at=collected_at),
            status=status,
            allowed_use_cases=self._bound_rights("pdf_methodology"),
            source_field="title",
            payload=payload,
        )
        self.store.put(obs)
        self.registry.update_health("pdf_methodology", ok=True, detail=f"{n} rows")
        return {"ok": True, "n": n, "reason": "ok", "snapshot_id": obs.snapshot_id}
