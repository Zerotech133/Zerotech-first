"""P1 验收语料灌入工具（合成数据，仅供验收 / 回归，不进生产）。

为什么需要它
------------
事件层的正确性无法只看"聚出多少个事件"来判断——把 150 条事实聚成 150 个事件
和聚成 3 个事件，`stories_created` 差 50 倍，但都可能"看起来跑通了"。判断对错
必须有真值：这批事实里哪几条本来就该是同一件事。

`tests/fixtures/verify_corpus.json` 就是这份真值。每条 unit 落库时把真值写进
`osint_knowledge_unit.extension->>'truth_story'`，于是任何下游都能直接 JOIN 出
"应该几个事件""应该几个来源"，不必再人工比对。

用法
----
    python scripts/seed_verify_corpus.py            # 灌入（幂等，unit_key 冲突即跳过）
    python scripts/seed_verify_corpus.py --reset    # 先删掉上一轮灌入的行再灌
    python scripts/seed_verify_corpus.py --json     # 机器可读输出

退出码：0 成功；1 异常。**无降级态**——灌语料失败就是失败，不存在"部分可用"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import DatabaseUnavailableError, execute, execute_many, fetch_all  # noqa: E402

#: 真值语料位置。与 tests/ 共用同一份，避免"测试用一套、验收用另一套"。
CORPUS_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "verify_corpus.json"

#: 灌入行的 unit_key 统一前缀，便于 --reset 精确回收，绝不误删真实数据。
SEED_PREFIX = "verify-"

_INSERT = """
INSERT INTO osint_knowledge_unit (
    unit_key, unit_version, unit_type, title, content, source_channel,
    source_url, content_hash, review_status, entities, topics, extension,
    status, created_at, updated_at
) VALUES (
    :unit_key, 1, 'fact', :title, :content, 'manual',
    :source_url, :content_hash, 'reviewed',
    CAST(:entities AS JSONB), CAST(:topics AS JSONB), CAST(:extension AS JSONB),
    'active', now() + make_interval(days => :day_offset), now()
)
ON CONFLICT (unit_key, unit_version) DO NOTHING
"""

_DELETE = "DELETE FROM osint_knowledge_unit WHERE unit_key LIKE :prefix"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load_corpus(path: Path = CORPUS_PATH) -> dict[str, Any]:
    """读真值语料；结构不合法时抛 ValueError（宁可早失败，不灌半份）。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("stories", "singletons"):
        if key not in raw:
            raise ValueError(f"语料缺少 {key} 字段：{path}")
    for story in raw["stories"]:
        if not story.get("story_id") or not story.get("units"):
            raise ValueError(f"故事条目缺少 story_id 或 units：{story!r:.80}")
    return raw


def build_rows(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    """把语料摊平成待插入行。真值写入 extension.truth_story。"""
    rows: list[dict[str, Any]] = []
    for story in corpus["stories"]:
        story_id = str(story["story_id"])
        for index, unit in enumerate(story["units"], start=1):
            rows.append(
                _row(
                    unit_key=f"{SEED_PREFIX}{story_id.lower()}-{index:02d}",
                    unit=unit,
                    topics=unit.get("topics") or story.get("topics") or [],
                    truth_story=story_id,
                )
            )
    for index, unit in enumerate(corpus["singletons"], start=1):
        rows.append(
            _row(
                unit_key=f"{SEED_PREFIX}solo-{index:02d}",
                unit=unit,
                topics=unit.get("topics") or [],
                truth_story=None,
            )
        )
    return rows


def _row(
    *, unit_key: str, unit: dict[str, Any], topics: list[Any], truth_story: str | None
) -> dict[str, Any]:
    title = str(unit.get("title") or "").strip()
    content = str(unit.get("content") or "").strip()
    domain = str(unit.get("domain") or "example.com").strip()
    day_offset = int(unit.get("day_offset") or 0)
    # 语料里 day_offset 是"相对今天的天数偏移"，负数表示过去。正数会让事实落在
    # 未来，时间窗查询会把它全收进窗口、历史窗口一条不留，表现为"探针全是 0"。
    # 这个错法曾真实发生（SQL 里写成 now() - interval(负数)），必须挡在入口。
    if day_offset > 0:
        raise ValueError(
            f"{unit_key} 的 day_offset 必须 <= 0（负数表示过去），实得 {day_offset}"
        )
    return {
        "unit_key": unit_key,
        "title": title,
        "content": content,
        # 域名决定 source_id（按媒体去重算独立来源数），必须真实落在 URL 里
        "source_url": f"https://{domain}/article/{unit_key}",
        "content_hash": _hash(f"{unit_key}|{title}|{content}"),
        "entities": json.dumps(unit.get("entities") or [], ensure_ascii=False),
        "topics": json.dumps(topics, ensure_ascii=False),
        "extension": json.dumps({"truth_story": truth_story}, ensure_ascii=False),
        "day_offset": day_offset,
    }


def truth_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """真值口径的期望值——不是猜的，是语料自己声明的。"""
    by_story: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        truth = json.loads(row["extension"])["truth_story"]
        if truth:
            by_story.setdefault(truth, []).append(row)
    multi = {k: v for k, v in by_story.items() if len(v) >= 2}
    singles = [row for row in rows if not json.loads(row["extension"])["truth_story"]]
    return {
        "units": len(rows),
        "stories_with_multiple_facts": len(multi),
        "singleton_units": len(singles),
        # 多事实故事 + 每条独事实各算一个事件
        "expected_stories": len(multi) + len(singles),
        "expected_independent_sources": {
            k: len({r["source_url"].split("/")[2] for r in v}) for k, v in multi.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="灌入 P1 验收语料（合成数据）")
    parser.add_argument("--reset", action="store_true", help="先删除上一轮灌入的行")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--corpus", default=str(CORPUS_PATH), help="语料文件路径")
    args = parser.parse_args()

    try:
        corpus = load_corpus(Path(args.corpus))
        rows = build_rows(corpus)

        removed = 0
        if args.reset:
            removed = execute(_DELETE, {"prefix": f"{SEED_PREFIX}%"})
        inserted = execute_many(_INSERT, rows)

        stored = fetch_all(
            "SELECT count(*) AS n FROM osint_knowledge_unit WHERE unit_key LIKE :prefix",
            {"prefix": f"{SEED_PREFIX}%"},
        )
        payload = {
            "rows_prepared": len(rows),
            "removed": removed,
            "inserted": inserted,
            "in_db": int(stored[0]["n"]) if stored else 0,
            **truth_summary(rows),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"[done] 语料已灌入：准备 {payload['rows_prepared']} 行，"
                  f"新插入 {inserted} 行，回收 {removed} 行，库内共 {payload['in_db']} 行")
            print(f"[truth] 期望事件数 {payload['expected_stories']}"
                  f"（多事实故事 {payload['stories_with_multiple_facts']} 个"
                  f" + 独事实 {payload['singleton_units']} 条），"
                  f"事实总数 {payload['units']}")
            print("[truth] 各故事期望独立来源数："
                  + ", ".join(f"{k}={v}" for k, v in payload["expected_independent_sources"].items()))
        return 0
    except (DatabaseUnavailableError, ValueError, OSError) as exc:
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
