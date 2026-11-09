"""存量事实向量回填：把 knowledge_unit 投影到新 collection（1024 维 bge-m3）。

## 为什么必须单独回填

旧 collection ``osint_knowledge_units_v1`` 是 384 维（nomic/small 系），
bge-m3 是 **1024 维**。Qdrant 的 collection 维度建好即不可变，**无法原地
升级**，只能新建 collection 再回填。旧 collection 保留作回滚路径。

## 成本（回填前必须先算账）

现网单条文本嵌入约 4-8 秒（bge-m3 在 iGPU 上，CPU 兜底时更慢）。
按 90 天历史估算约 15 万条 → **75 小时以上**。因此本脚本设计为：

- 按 ``unit_key`` 游标分页，**可中断续跑**（中断后重跑会跳过已完成的 key）
- 每批落一次进度日志，便于外部判断卡在哪
- 单条失败不中断整批，最后汇总失败数

先估规模再动手::

    python scripts/backfill_vectors.py --days 90 --dry-run

## 用法

::

    # 1) 先干跑，确认规模与耗时估算
    python scripts/backfill_vectors.py --days 90 --dry-run

    # 2) 小样本验证维度与链路
    python scripts/backfill_vectors.py --days 90 --limit 100

    # 3) 全量回填（建议 nohup 或 systemd-run 挂后台，输出落文件）
    python scripts/backfill_vectors.py --days 90 --limit 100000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import db  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge.embedder import Embedder, EmbeddingUnavailableError  # noqa: E402
from knowledge.indexer import VectorIndex  # noqa: E402

#: 新 collection 默认名（与旧 v1 并存，便于回滚）
DEFAULT_COLLECTION = "osint_knowledge_units_v2"

_SELECT_SQL = """
SELECT u.unit_key,
       u.unit_version,
       u.title,
       u.content,
       u.resource_key,
       u.blogger_key,
       u.topics,
       u.status,
       u.review_status,
       u.truth_status,
       u.content_hash
  FROM osint_knowledge_unit u
 WHERE u.status = 'active'
   AND u.created_at >= now() - make_interval(days => :days)
   AND u.unit_key > :last_key
 ORDER BY u.unit_key ASC
 LIMIT :lim
"""


def _fetch_batch(days: int, last_key: str, limit: int) -> list[dict]:
    """按 unit_key 游标取一批待回填单元。"""
    return db.fetch_all(_SELECT_SQL, {"days": days, "last_key": last_key, "lim": limit})


def _text_of(row: dict) -> str:
    """构造嵌入文本：标题前置 + 正文截断（与聚类算子保持同一口径）。"""
    title = str(row.get("title") or "").strip()
    content = str(row.get("content") or "").strip()
    return f"{title}\n{content[:2000]}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="存量事实向量回填（bge-m3 / 1024 维）")
    parser.add_argument("--days", type=int, default=90, help="回填时间窗（天）")
    parser.add_argument("--limit", type=int, default=100000, help="总条数上限")
    parser.add_argument("--batch", type=int, default=64, help="每批条数，默认 64")
    parser.add_argument(
        "--collection", default=DEFAULT_COLLECTION, help="目标 collection 名"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计规模，不写向量")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    embedder = Embedder()
    print(
        json.dumps(
            {
                "event": "start",
                "model": settings.embed_model,
                "dim": embedder.dim,
                "collection": args.collection,
                "days": args.days,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )

    index = None
    if not args.dry_run:
        # 显式传 1024，绝不复用 settings 里的旧 384
        index = VectorIndex(
            collection=args.collection, vector_size=embedder.dim
        )

    total = 0
    ok = 0
    failed = 0
    last_key = ""
    started = time.monotonic()

    while total < args.limit:
        want = min(args.batch, args.limit - total)
        rows = _fetch_batch(args.days, last_key, want)
        if not rows:
            break

        last_key = str(rows[-1]["unit_key"])
        total += len(rows)

        if args.dry_run:
            print(
                json.dumps(
                    {"event": "progress", "scanned": total, "last_key": last_key},
                    ensure_ascii=False,
                )
            )
            continue

        texts = [_text_of(row) for row in rows]
        try:
            vectors = embedder.embed_texts(texts)
        except EmbeddingUnavailableError as exc:
            # 嵌入链路断了：立即退出，不空转（避免把 75 小时的活跑成白工）
            print(
                json.dumps(
                    {"event": "abort", "reason": "embedding_unavailable",
                     "detail": str(exc), "scanned": total, "ok": ok},
                    ensure_ascii=False,
                )
            )
            return 1

        for row, vector in zip(rows, vectors):
            try:
                index.upsert(row, vector=vector)  # type: ignore[union-attr]
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(
                    json.dumps(
                        {"event": "item_failed", "unit_key": row["unit_key"],
                         "error": str(exc)[:200]},
                        ensure_ascii=False,
                    )
                )

        elapsed = time.monotonic() - started
        rate = ok / elapsed if elapsed > 0 else 0.0
        print(
            json.dumps(
                {
                    "event": "progress",
                    "scanned": total,
                    "ok": ok,
                    "failed": failed,
                    "rate_per_sec": round(rate, 3),
                    "eta_hours": round((args.limit - total) / rate / 3600, 1)
                    if rate > 0
                    else None,
                    "last_key": last_key,
                },
                ensure_ascii=False,
            )
        )

    summary = {
        "event": "done",
        "scanned": total,
        "ok": ok,
        "failed": failed,
        "elapsed_sec": round(time.monotonic() - started, 1),
        "dry_run": args.dry_run,
        "collection": args.collection,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
