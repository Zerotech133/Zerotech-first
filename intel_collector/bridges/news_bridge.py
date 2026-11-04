# -*- coding: utf-8 -*-
"""P2 通讯线数据桥：news_collector/news.db → 批级 Observation（只读）。

定位（开源情报 P2 车道，2026-11-04）：
  - **只读桥接**：读 `C:/项目/老田的AI助手/news_collector/news.db`（SQLite）
    news 表增量，转批级 Observation 落 intel_collector obs_store；
    **绝不反写 news_collector 任何文件/库**——行级真源永远是 news_collector。
  - **批级粒度**：每次 run 产 1 条批次摘要 Observation（new 条数/来源分布/
    时间跨度），不做逐行镜像。
  - **游标**：news 表为追加型、rowid 单调递增（实测 1~119,960；id 为 TEXT
    主键如 "xd:notice:..." 不可数值游标，rowid 是安全增量列）。断点落
    intel_collector 侧 cursor 目录，tmp+os.replace 原子写，重启续读不重复。
  - **只读连接**：sqlite3 以 URI mode=ro 打开，杜绝任何写入路径。

数据语义（实测 schema，news_collector/news.db）：
  id TEXT PK / source TEXT / ts TEXT('YYYY-MM-DD HH:MM') / title TEXT /
  content TEXT / hit / pushed / digested / created_at TEXT('YYYY-MM-DD HH:MM') /
  stocks TEXT(JSON数组) / jev TEXT(JSON判定) / model / judged_at / attempt / last_error

SLA 适配：registry 现档 news.freshness_minutes=30（拉取式语义），本桥为批式
快照不适用；构造参数 sla_freshness_minutes 允许运行时覆盖——仅在桥内存生效
（effective_freshness_minutes 属性 + Observation payload 记录），**绝不变异
spec.sla**（registry.update_health→save() 会把内存态落盘，变异即污染
sources.json）。

合规：与平台默认一致，仅绑定 internal_retention/cleaning/controlled_query
三用途（RightsGate 逐用途校验通过的才写入 allowed_use_cases）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.contracts import Observation, SourceClock, Status
from core.rights_gate import RightsGate
from registry.source_registry import SourceRegistry
from store.obs_store import ObservationStore

NEWS_DB_PATH = Path(r"C:/项目/老田的AI助手/news_collector/news.db")

NEWS_DEFAULT_RIGHTS = ("internal_retention", "cleaning", "controlled_query")

NEWS_SOURCE_ID = "news"            # 复用 registry/sources.json 已登记的 news 源
DISPATCH_KEY = "news_db"           # CollectDispatcher 派发键（collect_source 入口）


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NewsBridge:
    """通讯线批式桥：news.db 增量 → 批级 Observation（只读、断点续读）。

    参数：
      registry/store/gate：intel_collector 三件套（源档/观测库/权利闸）。
      db_path：news.db 路径（默认 C:/项目/老田的AI助手/news_collector/news.db；
               测试可指向 tmp 夹具库）。
      cursor_dir：游标目录（默认 <repo>/intel_collector/news_cursors）。
      max_rows：单次 run 最多读取的增量行数（默认 5000，防大跨步）。
      sla_freshness_minutes：批式 SLA 覆盖（None=用源档现值 30 分钟）。
               批式快照 30 分钟过紧，Lead 集成时可传如 1440——覆盖只在桥内存
               生效，不落 sources.json。
    """

    def __init__(self, registry: SourceRegistry, store: ObservationStore,
                 gate: Optional[RightsGate] = None, *,
                 db_path: Optional[Path] = None,
                 cursor_dir: Optional[Path] = None,
                 max_rows: int = 5000,
                 sla_freshness_minutes: Optional[int] = None):
        self.registry = registry
        self.store = store
        self.gate = gate or RightsGate()
        self.db_path = Path(db_path) if db_path else NEWS_DB_PATH
        self.cursor_dir = Path(cursor_dir) if cursor_dir else (
            Path(__file__).resolve().parent.parent / "news_cursors")
        self.cursor_dir.mkdir(parents=True, exist_ok=True)
        self.max_rows = max_rows
        self.sla_freshness_minutes = sla_freshness_minutes

    # ---- SLA 有效值（覆盖优先；只读，绝不变异 spec.sla） ----

    @property
    def effective_freshness_minutes(self) -> Optional[int]:
        if self.sla_freshness_minutes is not None:
            return self.sla_freshness_minutes
        spec = self.registry.get(NEWS_SOURCE_ID)
        return spec.sla.get("freshness_minutes") if spec else None

    # ---- 游标（rowid 断点，原子写 tmp+os.replace） ----

    def _cursor_path(self) -> Path:
        return self.cursor_dir / "news.json"

    def _load_cursor(self) -> dict:
        p = self._cursor_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_cursor(self, state: dict) -> None:
        p = self._cursor_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    # ---- 用途绑定（闸门通过的才写入 Observation） ----

    def _bound_rights(self, source_id: str) -> tuple:
        spec = self.registry.get(source_id)
        if spec is None:
            return ()
        return tuple(
            uc for uc in NEWS_DEFAULT_RIGHTS if self.gate.check(spec, uc).allowed
        )

    # ---- CollectDispatcher 协议：collect_source("news_db") ----

    def collect_source(self, source_id: str, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        if source_id != DISPATCH_KEY:
            return {"ok": False, "n": 0,
                    "reason": f"News 桥只接 {DISPATCH_KEY}，收到 {source_id}"}
        return self._collect_news(now)

    # ---- news 表增量批（rowid 游标） ----

    def _read_increment(self) -> tuple[list[dict], int]:
        """从游标 rowid 续读增量，返回 (rows, new_cursor_rowid)。

        只读连接（URI mode=ro）；库不存在→([], cursor 现值)。
        rows 为空时游标不推进（不重读、不落盘）。
        """
        st = self._load_cursor()
        last_rowid = int(st.get("last_rowid", 0))
        if not self.db_path.exists():
            return [], last_rowid
        # URI 只读打开：即便桥被误用也无写入路径（Windows 路径转 POSIX）
        con = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT rowid, id, source, ts, title, created_at "
                "FROM news WHERE rowid > ? ORDER BY rowid ASC LIMIT ?",
                (last_rowid, self.max_rows),
            )
            rows = [
                {"rowid": r[0], "id": r[1], "source": r[2], "ts": r[3],
                 "title": r[4], "created_at": r[5]}
                for r in cur.fetchall()
            ]
        finally:
            con.close()
        new_cursor = rows[-1]["rowid"] if rows else last_rowid
        return rows, new_cursor

    def _collect_news(self, now) -> dict:
        collected_at = _now_iso()

        try:
            rows, new_cursor = self._read_increment()
        except sqlite3.DatabaseError as e:  # 失败可见：不产 Observation、不推游标
            self.registry.update_health(NEWS_SOURCE_ID, ok=False, detail=str(e)[:200])
            return {"ok": False, "n": 0, "reason": f"sqlite: {e}"}

        # 批级摘要：来源分布 + 时间跨度（ts=新闻时间 / created_at=入库时间，
        # 均 'YYYY-MM-DD HH:MM' 文本可比；缺失容错为 None）
        source_dist: dict[str, int] = {}
        ts_vals: list[str] = []
        ca_vals: list[str] = []
        for r in rows:
            s = r.get("source") or "unknown"
            source_dist[s] = source_dist.get(s, 0) + 1
            if r.get("ts"):
                ts_vals.append(r["ts"])
            if r.get("created_at"):
                ca_vals.append(r["created_at"])
        payload = {
            "new": len(rows),
            "source_distribution": dict(
                sorted(source_dist.items(), key=lambda kv: -kv[1])),
            "time_span": {
                "ts": (min(ts_vals), max(ts_vals)) if ts_vals else None,
                "created_at": (min(ca_vals), max(ca_vals)) if ca_vals else None,
            },
            "cursor_rowid_before": self._load_cursor().get("last_rowid", 0),
            "cursor_rowid_after": new_cursor,
            "db_path": str(self.db_path),
            "sla_freshness_minutes": self.effective_freshness_minutes,
            "sla_overridden": self.sla_freshness_minutes is not None,
            "mode": "incremental_batch",
        }
        status = Status.COMPLETE if rows else Status.EMPTY
        obs = Observation(
            source_id=NEWS_SOURCE_ID,
            snapshot_id=f"news:{collected_at}",
            entity_type="news_item",
            entity_id=f"batch:{collected_at}",
            clock=SourceClock(collected_at=collected_at),
            status=status,
            allowed_use_cases=self._bound_rights(NEWS_SOURCE_ID),
            source_field="title",
            payload=payload,
        )
        self.store.put(obs)
        if rows:
            self._save_cursor({"last_rowid": new_cursor, "updated_at": collected_at})
        self.registry.update_health(
            NEWS_SOURCE_ID, ok=True, detail=f"news+{len(rows)}")
        return {"ok": True, "n": len(rows), "reason": "ok",
                "snapshot_id": obs.snapshot_id}
