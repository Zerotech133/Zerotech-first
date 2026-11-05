"""时间近邻权重的标定实验（只测量，不改任何默认行为）。

## 要回答的问题

剩余唯一拆散 ``S11`` 能不能靠"时间近邻加权"解决？这个问题必须用数据回答
而不是凭直觉——上一轮我凭直觉判断"近的自律倡议书占用了归属位"，实测推翻了。

## ⚠️ 2026-11-04 真值复核后本脚本的配对集已变

原先本脚本用三对论证"该合的分低且近、不该合的分高且远"，其中 ``solo-18``
被当作"不该合却合"的反例。真值复核（见
``deliverables/2026-11-04_情报体系P1_solo-18真值复核_v1.md``）确认该条本属
``S11``，标注有误，现已是 ``verify-s11-03``。因此它不再是反例，而成了
"该合、余弦够高、也确实合对了"的正例。

**修正后的论证反而更干净**，剩下三对构成完整对照：

============================  ========  =======  ========  ==========
配对                          余弦      时间差   真值      结论
============================  ========  =======  ========  ==========
``s11-01`` vs ``s11-02``      0.7133    1 天     同事件    够不着 0.75，被拆
``s04-01`` vs ``solo-05``     0.7203    **0 天** 不同事件  被门槛挡住
``s11-01`` vs ``s11-03``      0.8240    6 天     同事件    合对了
============================  ========  =======  ========  ==========

前两行是关键：**时间差更小的那对（0 天）余弦反而更高**，而两者真值相反。
任何"间隔小就降门槛"的规则都必然先放入那个不同事件的配对。这是结构性
冲突的直接证据，与标注错误无关。

## 两种加权形式

1. **乘性折扣** ``score = cos * f(gap)``，``f`` 单调递减。直观但方向就是错的：
   ``s11-02`` 的 0.7133 要过 0.75 需要 ``f(1) >= 1.05``，而 ``f <= 1``。
   数学上不可能，所以不再扫描，本脚本只做门槛衰减。
2. **门槛随间隔升高** ``threshold(gap) = base + slope * gap``。间隔越大要求越高，
   间隔越小越宽容。这是唯一在方向上可能有效的形式，下文网格扫描它。

注意"门槛随间隔**升高**"不是笔误：直觉上的"近邻加权"通常写成"近就降门槛"，
逻辑上等价，但必须承认它的代价——间隔 0 的**不同事件**也会一并放进来。

## 指标口径

配对级 precision / recall / F1，``same`` 取自 ``extension->>'truth_story'``
（无该字段的事实视为独立事件，以 ``unit_key`` 作真值）。配对级不等于最终
聚类级（后者还有传递闭包效应），但足以判断方向；方向不对就不必再跑全链路。
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import db  # noqa: E402
from knowledge.story_group import StoryGrouper, cosine  # noqa: E402

#: 网格扫描范围。slope 上限 0.03 对应"每多一天要求高 0.03"：间隔 10 天即抬高
#: 0.30，已足够把 30 天前的相似事实全挡在门外，再大就等于禁用跨窗口。
BASE_RANGE = [round(0.60 + 0.01 * i, 2) for i in range(26)]  # 0.60 .. 0.85
SLOPE_RANGE = [round(-0.030 + 0.005 * i, 3) for i in range(13)]  # -0.030 .. 0.030

_LOAD_SQL = """
SELECT unit_key, title, content, extension->>'truth_story' AS truth,
       extract(epoch FROM created_at) AS ts
  FROM osint_knowledge_unit
 WHERE status='active' AND (unit_type IS NULL OR unit_type='fact')
"""


def load_pairs() -> list[dict[str, Any]]:
    """取全部事实，两两配对并标注真值与余弦、时间差。"""
    rows = db.fetch_all(_LOAD_SQL)
    grouper = StoryGrouper()
    embedder = grouper._get_embedder()
    if embedder is None:
        raise RuntimeError("embedder 不可用，无法标定")
    vectors = embedder.embed_texts([grouper._unit_text(row) for row in rows])

    pairs: list[dict[str, Any]] = []
    for i, j in itertools.combinations(range(len(rows)), 2):
        left, right = rows[i], rows[j]
        left_truth = left["truth"] or f"SOLO:{left['unit_key']}"
        right_truth = right["truth"] or f"SOLO:{right['unit_key']}"
        pairs.append(
            {
                "cos": cosine(vectors[i], vectors[j]),
                "gap": abs(float(left["ts"]) - float(right["ts"])) / 86400.0,
                "same": left_truth == right_truth,
                "left": left["unit_key"],
                "right": right["unit_key"],
            }
        )
    return pairs


def score(threshold: float, slope: float, pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按 ``threshold(gap) = base + slope*gap`` 判合并，返回混淆矩阵与 F1。"""
    tp = fp = fn = 0
    for pair in pairs:
        predicted = pair["cos"] >= threshold + slope * pair["gap"]
        if predicted and pair["same"]:
            tp += 1
        elif predicted:
            fp += 1
        elif pair["same"]:
            fn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "base": threshold,
        "slope": slope,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def sweep(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """扫描 (base, slope) 网格。"""
    return [
        score(base, slope, pairs)
        for base in BASE_RANGE
        for slope in SLOPE_RANGE
    ]


def _print_baselines(pairs: Sequence[Mapping[str, Any]]) -> None:
    print("=== 常数门槛基线（斜率 0）===")
    for base in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85):
        row = score(base, 0.0, pairs)
        print(
            f"  base={base:.2f}  tp={row['tp']:3d} fp={row['fp']:3d} fn={row['fn']:3d} "
            f"P={row['precision']:.3f} R={row['recall']:.3f} F1={row['f1']:.3f}"
        )


def _print_key_pairs(pairs: Sequence[Mapping[str, Any]]) -> None:
    """把三对关键配对的数据摆出来，说明冲突是结构性的。

    第三对 ``s11-01`` vs ``s11-03`` 是 2026-11-04 真值复核后新增的正例（原
    ``solo-18``）：同事件、余弦 0.8240 过得了 0.75、也确实合对了。留它在表里
    是为了让"同事件也可以分很高"有一个实测锚点，避免把"余弦低"当成同事件的
    固有属性。
    """
    wanted = {
        ("verify-s11-01", "verify-s11-02"),  # 该合却拆（余弦 0.7133 / 1 天）
        ("verify-s04-01", "verify-solo-05"),  # 间隔 0 的不同事件（余弦 0.7203）
        ("verify-s11-01", "verify-s11-03"),  # 该合且合对了（余弦 0.8240 / 6 天）
    }
    print()
    print("=== 关键配对 ===")
    for pair in pairs:
        key = (pair["left"], pair["right"])
        if key in wanted or key[::-1] in wanted:
            tag = "同事件" if pair["same"] else "不同事件"
            print(
                f"  cos={pair['cos']:.4f} gap={pair['gap']:5.2f}d  [{tag}]  "
                f"{pair['left']} vs {pair['right']}"
            )


def cluster_sweep(slopes: Sequence[float]) -> list[dict[str, Any]]:
    """聚类级 dry-run：门槛随间隔升高时，事件数怎么变。

    配对级指标只是近似（配对不通但可通过第三方传递合并），故补这一层。用
    ``dry_run=True`` 只读不写，靠 ``stories_created`` / ``members_added`` 观察
    方向。**不改生产代码**：借 ``_recheck_similarity`` 这个既有注入点逐对改写
    阈值，跑完即还原。
    """
    from knowledge.story_group import StoryGrouper, _row_ts, resolve_recheck_similarity

    class SlopeGrouper(StoryGrouper):
        """按 ``threshold = base + slope*gap`` 动态判定（仅实验用）。"""

        def __init__(self, slope: float, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._slope = slope

        def _decide(self, unit, target, sim, mode="vector"):
            if mode != "vector" or self._slope == 0:
                return super()._decide(unit, target, sim, mode)
            gap = abs((_row_ts(unit.row) - _row_ts(target.row)).total_seconds()) / 86400.0
            self._recheck_similarity = resolve_recheck_similarity() + self._slope * gap
            try:
                return super()._decide(unit, target, sim, mode)
            finally:
                self._recheck_similarity = None

    results: list[dict[str, Any]] = []
    for slope in slopes:
        result = SlopeGrouper(slope).run(
            window_days=14, only_reviewed=True, limit=2000, dry_run=True
        )
        results.append(
            {
                "slope": slope,
                "stories_created": result.stories_created,
                "members_added": result.members_added,
                "unrelated": result.unrelated,
                "judge_calls": result.judge_calls,
            }
        )
    return results


def _print_cluster(results: Sequence[Mapping[str, Any]]) -> None:
    print()
    print("=== 聚类级 dry-run（window=14d, only_reviewed=True）===")
    print(f"  {'slope':>7} {'新建事件':>8} {'成员边':>7} {'unrelated':>10} {'判定次数':>8}")
    for row in results:
        print(
            f"  {row['slope']:+.3f} {row['stories_created']:8d} "
            f"{row['members_added']:7d} {row['unrelated']:10d} {row['judge_calls']:8d}"
        )


def main() -> int:
    pairs = load_pairs()
    positives = sum(1 for pair in pairs if pair["same"])
    print(f"配对总数 {len(pairs)}：同事件 {positives}，非同事件 {len(pairs) - positives}")

    _print_baselines(pairs)
    _print_key_pairs(pairs)

    results = sweep(pairs)
    best = max(results, key=lambda row: (row["f1"], row["precision"]))
    baseline = score(0.75, 0.0, pairs)

    print()
    print("=== 网格最优 ===")
    print(
        f"  base={best['base']:.2f} slope={best['slope']:+.3f}  "
        f"tp={best['tp']} fp={best['fp']} fn={best['fn']} "
        f"P={best['precision']:.3f} R={best['recall']:.3f} F1={best['f1']:.3f}"
    )
    print(
        f"  当前默认 base=0.75 slope=0  F1={baseline['f1']:.3f}"
        f"（提升 {best['f1'] - baseline['f1']:+.3f}）"
    )

    # 零误合并前提下的最优召回：实务上 fp 比 fn 贵得多
    strict = [row for row in results if row["fp"] == 0]
    if strict:
        best_strict = max(strict, key=lambda row: (row["recall"], row["precision"]))
        print()
        print("=== 零误合并约束下的最优 ===")
        print(
            f"  base={best_strict['base']:.2f} slope={best_strict['slope']:+.3f}  "
            f"tp={best_strict['tp']} fn={best_strict['fn']} "
            f"R={best_strict['recall']:.3f} F1={best_strict['f1']:.3f}"
        )

    print()
    print("=== 斜率是否真的有增益（按 F1 排序前 8）===")
    for row in sorted(results, key=lambda r: -r["f1"])[:8]:
        print(
            f"  base={row['base']:.2f} slope={row['slope']:+.3f}  "
            f"tp={row['tp']:2d} fp={row['fp']:2d} fn={row['fn']:2d} F1={row['f1']:.3f}"
        )

    print()
    print("=== 仅看斜率（固定 base=0.75）===")
    for row in [r for r in results if abs(r["base"] - 0.75) < 1e-9]:
        print(
            f"  slope={row['slope']:+.3f}  tp={row['tp']:2d} fp={row['fp']:2d} "
            f"fn={row['fn']:2d} F1={row['f1']:.3f}"
        )

    _print_cluster(cluster_sweep([-0.01, 0.0, 0.01, 0.02, 0.03]))

    out = Path(r"C:/Users/t/AppData/Local/Temp/time_weight_sweep.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整扫描结果写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
