"""跨窗口事件分裂探针：量化"有多少事件被时间窗切成两段"。

## 它回答什么，不回答什么

**回答**：在近期窗口内的事实里，有多少条能在**窗口外**的近期历史中找到高度
相似的近邻——即"如果窗口再宽一点，它本来会被并进同一个事件"。输出是一个
数字（``split_candidate`` 与对应比例）加最多 20 组证据，供人决定要不要上
跨窗口召回。

**不回答**：不做合并、不写库、不建事件、不修成员边。本脚本全程只读
（``db.fetch_all`` + Qdrant 只读检索）。自动合并需要另一轮评审——误合并会
直接污染 ``independent_sources``，且事后极难发现，不能由探针顺手做掉。

## 算法

1. 取窗口内事实（``created_at >= now() - window_days``，只读）；
2. 取窗口外近期历史（``now() - window_days - lookback_days <= created_at <
   now() - window_days``）；
3. 每条窗口事实与全部历史事实比一遍相似度，取 top-k；
   - 有已存向量 → 余弦（bge-m3，Qdrant 里已存的投影，不重新嵌入）；
   - 任一方缺向量 → 字符二元组 Jaccard（与聚类算子同一退路）；
4. 按 :func:`knowledge.story_recall.classify_split` 三分类并聚合。

## 性能边界

事实表会到十万级，因此：

- PG 一律**按 ``unit_key`` 游标分页**，绝不 ``fetchall()`` 全表；
- ``--limit``（窗口事实数）与 ``--history-limit``（历史事实数）双向封顶；
- ``--max-pairs`` 是**比对次数**预算，超预算立即停并置 ``truncated=true``，
  不静默跑成"看起来跑完了但只扫了一半"。

纯 Python 余弦实测约 57 µs/对（1024 维），默认 300 × 1500 = 45 万对 → 约
25 秒；退回字符二元组口径约 9 µs/对（约 4 秒）。要更快就降
``--history-limit``，要更全就调大并配 ``--json`` 落盘。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常完成（结论是数据，没有降级态） |
| 1 | 异常（PG 不可用、参数非法等） |

向量不可用**不是**异常：会退回文本口径，并在输出的 ``similarity_mode``
与 ``vector_coverage`` 里如实反映，仍然以 0 退出。

## 用法

::

    python scripts/story_split_probe.py                     # 默认 14 天窗 / 90 天回溯
    python scripts/story_split_probe.py --window-days 7 --lookback-days 30
    python scripts/story_split_probe.py --no-vectors        # 强制文本口径
    python scripts/story_split_probe.py --json --limit 1000 > probe.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

# 让脚本在任意 cwd 下都能以包内绝对导入运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import db  # noqa: E402
from common.logging_setup import setup_logging  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge.story_group import DEFAULT_WINDOW_DAYS, extract_source_id  # noqa: E402
from knowledge.story_recall import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS,
    SPLIT_FAR_MULTIPLIER,
    classify_split,
    gap_hours,
    gate_for,
    pair_similarity,
    summarize_splits,
    window_bounds,
)

#: 执行异常的退出码（探针只读，没有"降级完成"这种中间态）
EXIT_FAILED = 1
#: 单页取数条数（游标分页的页大小）
PAGE_SIZE = 500
#: 单次向 Qdrant 索取向量的 key 数（避免一次过滤条件过长被服务端拒绝）
VECTOR_CHUNK = 128
#: 输出里保留的分裂证据组数上限
TOP_SPLITS_LIMIT = 20

_SELECT_WINDOW_UNITS = """
SELECT u.unit_key,
       u.title,
       u.content,
       u.source_url,
       u.source_link,
       u.blogger_key,
       u.created_at
  FROM osint_knowledge_unit u
 WHERE u.status = 'active'
   AND (u.unit_type IS NULL OR u.unit_type = 'fact')
   AND u.created_at >= now() - make_interval(days => :window_days)
   AND u.unit_key > :last_key
 ORDER BY u.unit_key ASC
 LIMIT :lim
"""

_SELECT_HISTORY_UNITS = """
SELECT u.unit_key,
       u.title,
       u.content,
       u.source_url,
       u.source_link,
       u.blogger_key,
       u.created_at
  FROM osint_knowledge_unit u
 WHERE u.status = 'active'
   AND (u.unit_type IS NULL OR u.unit_type = 'fact')
   AND u.created_at >= now() - make_interval(days => :total_days)
   AND u.created_at <  now() - make_interval(days => :window_days)
   AND u.unit_key > :last_key
 ORDER BY u.unit_key ASC
 LIMIT :lim
"""


# -------------------------------------------------------------- 取数 --


def _iter_units(
    sql: str, params: Mapping[str, Any], *, limit: int, batch: int = PAGE_SIZE
) -> Iterator[list[dict[str, Any]]]:
    """按 ``unit_key`` 游标分页取数，逐页 yield（调用方决定是否提前停）。

    游标用 ``unit_key > :last_key`` 而不是 ``OFFSET``：后者在十万级表上会随
    翻页线性变慢，且中途有写入时可能跳行/重行。``unit_key`` 有唯一索引
    （``idx_knowledge_unit_key``），游标翻页是索引顺序扫描。
    """
    last_key = ""
    fetched = 0
    while fetched < limit:
        want = min(batch, limit - fetched)
        page = db.fetch_all(sql, {**params, "last_key": last_key, "lim": want})
        if not page:
            return
        last_key = str(page[-1]["unit_key"])
        fetched += len(page)
        yield page
        if len(page) < want:
            return


def _load_vectors(
    keys: Sequence[str], *, collection: str | None
) -> dict[str, list[float]]:
    """从 Qdrant 读取已存向量（只读）；任何失败都退化为空表。

    这里**不**复用 ``VectorIndex``：它的构造函数会 ``create_collection``，
    对只读探针来说是多余且有副作用的。缺依赖 / 连不上 / collection 不存在
    都只记 warning——向量是加速口径，不是结论的前置条件。
    """
    if not keys:
        return {}
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qmodels
    except ImportError:
        print("[WARN] qdrant-client 未安装，退回字符二元组口径", file=sys.stderr)
        return {}

    settings = get_settings()
    target = collection or settings.qdrant_collection
    vectors: dict[str, list[float]] = {}
    client = None
    try:
        client = QdrantClient(url=settings.qdrant_url, timeout=10)
        for start in range(0, len(keys), VECTOR_CHUNK):
            chunk = list(keys[start : start + VECTOR_CHUNK])
            points, _ = client.scroll(
                collection_name=target,
                # should = OR：老版本 qdrant-client 没有 MatchAny，用 OR 条件兼容
                scroll_filter=qmodels.Filter(
                    should=[
                        qmodels.FieldCondition(
                            key="unit_key", match=qmodels.MatchValue(value=key)
                        )
                        for key in chunk
                    ]
                ),
                limit=len(chunk),
                with_payload=["unit_key"],
                with_vectors=True,
            )
            for point in points:
                payload = point.payload or {}
                key = str(payload.get("unit_key") or "")
                vector = point.vector
                if not key or not isinstance(vector, list):
                    continue
                vectors[key] = [float(item) for item in vector]
    except Exception as exc:  # noqa: BLE001 - 向量是可选加速项，不阻断探针
        print(
            f"[WARN] 读取 Qdrant 向量失败（collection={target}）：{exc}；"
            "本轮部分配对退回字符二元组口径",
            file=sys.stderr,
        )
        return vectors
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响结论
                pass
    return vectors


# -------------------------------------------------------------- 比对 --


def _top_matches(
    pivot: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    vectors: Mapping[str, Sequence[float]],
    *,
    top_k: int,
) -> list[tuple[float, str, Mapping[str, Any]]]:
    """取 pivot 在历史中的 top-k 相似事实 ``[(相似度, 口径, 行), ...]``。"""
    pivot_vector = vectors.get(str(pivot["unit_key"]))
    scored: list[tuple[float, str, Mapping[str, Any]]] = []
    for other in history:
        similarity, mode = pair_similarity(
            pivot,
            other,
            left_vector=pivot_vector,
            right_vector=vectors.get(str(other["unit_key"])),
        )
        scored.append((similarity, mode, other))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:top_k]


def _build_pairs(
    window_rows: Sequence[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
    vectors: Mapping[str, Sequence[float]],
    *,
    window_days: int,
    top_k: int,
    max_pairs: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """逐条窗口事实取 top-k 近邻并分类。

    Returns:
        ``(pairs, comparisons, truncated)``。``pairs`` 是已分类的配对（每条
        至多 ``top_k`` 个），``comparisons`` 是实际比对次数（含未被选中的），
        ``truncated`` 表示是否撞到 ``max_pairs`` 预算。
    """
    pairs: list[dict[str, Any]] = []
    comparisons = 0
    truncated = False

    for pivot in window_rows:
        if comparisons >= max_pairs:
            truncated = True
            break
        if not history_rows:
            break
        # 只比对到预算为止，避免最后一条把预算冲过头
        remaining = max_pairs - comparisons
        budget_slice = (
            history_rows[:remaining] if remaining < len(history_rows) else history_rows
        )
        comparisons += len(budget_slice)
        if len(budget_slice) < len(history_rows):
            truncated = True

        for similarity, mode, other in _top_matches(
            pivot, budget_slice, vectors, top_k=top_k
        ):
            label = classify_split(
                pivot["created_at"],
                other["created_at"],
                window_days=window_days,
                similarity=similarity,
                threshold=gate_for(mode),
            )
            pairs.append(
                {
                    "classification": label,
                    "similarity": round(float(similarity), 4),
                    "mode": mode,
                    "gap_hours": gap_hours(pivot["created_at"], other["created_at"]),
                    "left_unit_key": str(pivot["unit_key"]),
                    "left_title": str(pivot.get("title") or "")[:120],
                    "left_source": _source_of(pivot),
                    "right_unit_key": str(other["unit_key"]),
                    "right_title": str(other.get("title") or "")[:120],
                    "right_source": _source_of(other),
                    "right_created_at": _iso(other.get("created_at")),
                }
            )

    return pairs, comparisons, truncated


def _source_of(row: Mapping[str, Any]) -> str:
    """来源域名（复用算子的独立来源口径，便于判断是否同一家媒体）。"""
    return extract_source_id(row)


def _iso(value: Any) -> str | None:
    """时间转 ISO 字符串；非 datetime 原样 ``str``（PG 驱动理论上只给 datetime）。"""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value) if value is not None else None


def _similarity_mode(pairs: Sequence[Mapping[str, Any]]) -> str:
    """汇总本轮实际使用的相似度口径：``vector`` / ``text`` / ``mixed`` / ``none``。"""
    modes = {str(item.get("mode")) for item in pairs}
    if not modes:
        return "none"
    if len(modes) == 1:
        return modes.pop()
    return "mixed"


# -------------------------------------------------------------- 主流程 --


def _embed_vectors(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    """现算向量兜底：Qdrant 不可用 / 存量未回填时，直接用 bge-m3 算。

    为什么必须有这条路：探针的结论建立在"相似度能否越过阈值"之上，而字符
    二元组在"同一事件、不同表述"上系统性偏低（实测同一事件的不同报道常只有
    0.1–0.3）。把向量当纯加速口径、缺失就退字符，等于**换掉了结论所依赖的
    度量**，会把跨窗口裂缝整批漏掉——输出一个漂亮的 ``split_ratio=0``，
    看起来像"没有问题"。宁可多花几十秒现算。

    任何失败都只降级、不中断：探针是只读测量工具，不该因为嵌入不可用就报错。
    """
    if not rows:
        return {}
    try:
        from knowledge.embedder import Embedder
    except ImportError as exc:  # noqa: PLC0415 - 可选依赖，缺失不算错
        print(f"[WARN] 无法导入嵌入器，退回字符二元组口径：{exc}", file=sys.stderr)
        return {}
    keys = [str(row["unit_key"]) for row in rows]
    texts = [
        f"{row.get('title') or ''}\n{str(row.get('content') or '')[:2000]}".strip()
        for row in rows
    ]
    try:
        vectors = Embedder().embed_texts(texts)
    except Exception as exc:  # noqa: BLE001 - 嵌入不可用一律降级
        print(
            f"[WARN] 现算向量失败（{type(exc).__name__}: {exc}），退回字符二元组口径",
            file=sys.stderr,
        )
        return {}
    if len(vectors) != len(keys):
        print(
            f"[WARN] 向量数量不符（{len(vectors)} != {len(keys)}），放弃现算结果",
            file=sys.stderr,
        )
        return {}
    return {key: [float(x) for x in vec] for key, vec in zip(keys, vectors)}


def _collect(
    *,
    window_days: int,
    lookback_days: int,
    limit: int,
    history_limit: int,
    top_k: int,
    max_pairs: int,
    collection: str | None,
    use_vectors: bool,
    embed_fallback: bool = True,
) -> dict[str, Any]:
    """跑一轮探针，返回完整结果字典（纯只读，可被测试直接调用）。"""
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    start, end = window_bounds(now, window_days)

    window_rows: list[dict[str, Any]] = []
    for page in _iter_units(
        _SELECT_WINDOW_UNITS, {"window_days": int(window_days)}, limit=limit
    ):
        window_rows.extend(page)

    history_rows: list[dict[str, Any]] = []
    for page in _iter_units(
        _SELECT_HISTORY_UNITS,
        {
            "window_days": int(window_days),
            "total_days": int(window_days + lookback_days),
        },
        limit=history_limit,
    ):
        history_rows.extend(page)

    keys = [str(row["unit_key"]) for row in window_rows + history_rows]
    vectors = _load_vectors(keys, collection=collection) if use_vectors else {}
    if use_vectors and embed_fallback:
        missing = [
            row
            for row in window_rows + history_rows
            if str(row["unit_key"]) not in vectors
        ]
        if missing:
            print(
                f"[INFO] 向量缺失 {len(missing)}/{len(keys)} 条，改用 bge-m3 现算兜底",
                file=sys.stderr,
            )
            vectors.update(_embed_vectors(missing))

    pairs, comparisons, truncated = _build_pairs(
        window_rows,
        history_rows,
        vectors,
        window_days=window_days,
        top_k=top_k,
        max_pairs=max_pairs,
    )
    summary = summarize_splits(pairs)

    split_pairs = [
        item for item in pairs if item["classification"] == "split_candidate"
    ]
    split_pairs.sort(key=lambda item: float(item["similarity"]), reverse=True)
    units_with_split = len({item["left_unit_key"] for item in split_pairs})
    categories = {item["classification"] for item in pairs}

    payload: dict[str, Any] = {
        "ok": True,
        "window_days": int(window_days),
        "lookback_days": int(lookback_days),
        "veto_days": int(window_days) * SPLIT_FAR_MULTIPLIER,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "top_k": int(top_k),
        "units_scanned": len(window_rows),
        "history_scanned": len(history_rows),
        "comparisons_made": comparisons,
        "vector_coverage": round(len(vectors) / len(keys), 4) if keys else 0.0,
        "similarity_mode": _similarity_mode(pairs),
        "truncated": bool(truncated),
        "units_with_split": units_with_split,
        "split_unit_ratio": (
            round(units_with_split / len(window_rows), 4) if window_rows else 0.0
        ),
        "categories_seen": sorted(categories),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "top_splits": split_pairs[:TOP_SPLITS_LIMIT],
    }
    payload.update(summary)
    return payload


def _print_human_summary(payload: Mapping[str, Any]) -> None:
    """把结论打到 stderr（stdout 保持纯 JSON，便于管道消费）。"""
    print(
        "[probe] 窗口 {window_days}d 内 {units_scanned} 条事实，"
        "历史 {history_scanned} 条；比对 {comparisons_made} 次，"
        "判定 {pairs_checked} 对".format(**payload),
        file=sys.stderr,
    )
    print(
        "[probe] 窗内 {in_window} / 疑似分裂 {split_candidate} / 过远 {too_far}；"
        "split_ratio={split_ratio}（跨窗口径 {split_ratio_outside_window}）".format(
            **payload
        ),
        file=sys.stderr,
    )
    print(
        "[probe] 涉及 {units_with_split} 条窗口事实（{split_unit_ratio}）；"
        "相似度口径={similarity_mode}，向量覆盖={vector_coverage}".format(**payload),
        file=sys.stderr,
    )
    if payload.get("truncated"):
        print(
            "[WARN] 撞到 --max-pairs 预算，本轮只覆盖部分配对；结论是下界，不是全量",
            file=sys.stderr,
        )
    if not payload.get("split_candidate"):
        print("[probe] 未发现疑似分裂：跨窗口召回暂无数据支撑", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="跨窗口事件分裂探针（只读）：量化被时间窗切裂的事件比例"
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"算子窗口长度（天），默认 {DEFAULT_WINDOW_DAYS}",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"窗口外回溯上限（天），默认 {DEFAULT_LOOKBACK_DAYS}",
    )
    parser.add_argument(
        "--limit", type=int, default=300, help="窗口内事实扫描上限，默认 300"
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=1500,
        help="窗口外历史事实扫描上限，默认 1500",
    )
    parser.add_argument(
        "--top-k", type=int, default=5, help="每条事实比对的近邻数，默认 5"
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=500_000,
        help="比对次数预算，默认 500000（超出即停，输出 truncated=true）",
    )
    parser.add_argument(
        "--collection", default=None, help="Qdrant collection 名，默认取 settings"
    )
    parser.add_argument(
        "--no-vectors", action="store_true", help="跳过向量，强制用字符二元组口径"
    )
    parser.add_argument(
        "--no-embed-fallback",
        action="store_true",
        help="Qdrant 缺向量时不现算 bge-m3（默认会现算；关掉会显著漏报跨窗口裂缝）",
    )
    parser.add_argument(
        "--json", action="store_true", help="只输出 JSON（不打人类可读摘要）"
    )
    args = parser.parse_args(argv)

    setup_logging()

    if args.window_days < 1 or args.lookback_days < 1:
        print(
            json.dumps(
                {"ok": False, "error": "window-days 与 lookback-days 必须 >= 1"},
                ensure_ascii=False,
            )
        )
        return EXIT_FAILED
    if args.top_k < 1:
        print(
            json.dumps(
                {"ok": False, "error": "top-k 必须 >= 1"}, ensure_ascii=False
            )
        )
        return EXIT_FAILED

    try:
        payload = _collect(
            window_days=args.window_days,
            lookback_days=args.lookback_days,
            limit=max(1, args.limit),
            history_limit=max(0, args.history_limit),
            top_k=args.top_k,
            max_pairs=max(1, args.max_pairs),
            collection=args.collection,
            use_vectors=not args.no_vectors,
            embed_fallback=not args.no_embed_fallback,
        )
    except Exception as exc:  # noqa: BLE001 - 入口统一转结构化错误 + 退出码 1
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "error_type": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        return EXIT_FAILED

    print(json.dumps(payload, ensure_ascii=False, default=str))
    if not args.json:
        _print_human_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
