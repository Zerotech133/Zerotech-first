"""事件聚类质量评分（按真值语料打分，供验收与回归）。

为什么单独成脚本
----------------
`stories_created` 这种单一数字无法判断聚类对错：把 50 条事实聚成 3 个事件和
聚成 50 个事件，都只是"一个数字"。真正的质量要按真值算两类错误：

- **拆散（under-merge）**：同一真值故事被分到多个 story → 独立来源数被低估
- **误合并（over-merge）**：一个 story 混入多个真值故事 → 独立来源数被高估

两者危害不对称：拆散可以事后补，误合并几乎无法发现，且会直接让"N 家独立来源
在说"这句话变成假话。所以报告里必须分开给，不能合并成一个 F1 完事。

依赖 `osint_knowledge_unit.extension->>'truth_story'`（由 seed_verify_corpus.py
写入）。真实库没有这个字段，脚本会明确报错而不是给出全零的假结论。

用法
----
    python scripts/eval_story_quality.py
    python scripts/eval_story_quality.py --json

退出码：0 正常评分；1 无真值数据 / 异常。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import DatabaseUnavailableError, fetch_all  # noqa: E402

_SQL = """
SELECT u.unit_key,
       u.source_url,
       u.extension->>'truth_story' AS truth,
       m.story_id,
       m.decided_by,
       s.independent_sources,
       s.fact_count
  FROM osint_knowledge_unit u
  JOIN osint_story_member m
    ON m.unit_key = u.unit_key AND m.unit_version = u.unit_version
  JOIN osint_story s ON s.story_id = m.story_id
 WHERE u.extension ? 'truth_story'
 ORDER BY u.unit_key
"""


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按真值给聚类打分。每条独事实自成一个"真事件"。"""
    if not rows:
        return {"units": 0, "error": "无带真值且已归属的事实"}

    by_truth: dict[str, set[int]] = defaultdict(set)
    by_story: dict[int, set[str]] = defaultdict(set)
    sources: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        truth = row["truth"] or f"SOLO:{row['unit_key']}"
        by_truth[truth].add(int(row["story_id"]))
        by_story[int(row["story_id"])].add(truth)
        if row["truth"]:
            sources[row["truth"]].add(_domain(row["source_url"]))

    split = {k: sorted(v) for k, v in by_truth.items() if len(v) > 1}
    merged = {k: sorted(v) for k, v in by_story.items() if len(v) > 1}

    # 真值故事集合显式取，不能靠前缀判断——"SOLO:..." 同样以 S 开头，
    # 曾因此把 18 条独事实当成"来源数不符"计入分母。
    real_truths = {row["truth"] for row in rows if row["truth"]}

    # 只对"未被拆散"的故事核对独立来源数：被拆散时比较对象本身不成立
    source_ok = source_bad = 0
    source_detail: dict[str, dict[str, Any]] = {}
    for truth in sorted(real_truths):
        sids = by_truth[truth]
        if len(sids) != 1:
            continue
        sid = next(iter(sids))
        got = next(int(r["independent_sources"]) for r in rows if int(r["story_id"]) == sid)
        want = len(sources[truth])
        if got == want:
            source_ok += 1
        else:
            source_bad += 1
        source_detail[truth] = {"got": got, "want": want}

    decided: dict[str, int] = defaultdict(int)
    for row in rows:
        decided[str(row["decided_by"])] += 1

    return {
        "units": len(rows),
        "truth_stories": len(real_truths),
        "solo_units": sum(1 for t in by_truth if t.startswith("SOLO:")),
        "stories_created": len(by_story),
        "split_count": len(split),
        "split_detail": split,
        "over_merge_count": len(merged),
        "over_merge_detail": {str(k): v for k, v in merged.items()},
        "independent_sources_ok": source_ok,
        "independent_sources_bad": source_bad,
        "independent_sources_detail": source_detail,
        "decided_by": dict(decided),
    }


def _domain(url: str | None) -> str:
    if not url:
        return "unknown"
    parts = str(url).split("/")
    return parts[2] if len(parts) > 2 else str(url)


def main() -> int:
    parser = argparse.ArgumentParser(description="按真值给事件聚类打分")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args()

    try:
        rows = fetch_all(_SQL)
    except DatabaseUnavailableError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(
            "[error] 没有带真值的事实。先跑 seed_verify_corpus.py 灌语料，"
            "再跑 story_group_run.py。",
            file=sys.stderr,
        )
        return 1

    report = evaluate(rows)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
        return 0

    print(f"事实 {report['units']} 条 → 事件 {report['stories_created']} 个"
          f"（真值 {report['truth_stories']} 个多事实故事 + {report['solo_units']} 条独事实）")
    print(f"拆散 {report['split_count']} 个 / 误合并 {report['over_merge_count']} 处")
    for truth, sids in report["split_detail"].items():
        print(f"  拆散 {truth} -> story {sids}")
    for sid, truths in report["over_merge_detail"].items():
        print(f"  误合并 story {sid} <- {truths}")
    ok, bad = report["independent_sources_ok"], report["independent_sources_bad"]
    print(f"独立来源数正确 {ok}/{ok + bad}（仅统计未被拆散的故事）")
    for truth, item in sorted(report["independent_sources_detail"].items()):
        if item["got"] != item["want"]:
            print(f"  {truth}: 实际={item['got']} 期望={item['want']}")
    print("判定来源：" + ", ".join(f"{k}={v}" for k, v in sorted(report["decided_by"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
