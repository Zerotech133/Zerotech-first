"""事件判定阈值标定工具：实测"同事件"与"无关"的余弦分布。

## 为什么需要它

``story_group.py`` 里的 ``RECALL_SIMILARITY=0.60`` 与 ``RECHECK_SIMILARITY=0.85``
是**先验取值**（来自 AIHOT 的 14 天窗口实验），不是在本项目语料上标定出来的。
若不验证就上线，典型故障是：阈值偏高 → 大量真事件被拆成两个；阈值偏低 →
不同事件被合并，独立来源数虚高，日报排序整体失真。后者更难发现。

本工具是三件事的最小实现：
1. 用真实 bge-m3 算给定样本对的余弦
2. 输出同事件组 / 无关组的分布与可分性
3. 在候选阈值网格上给出召回率与误召率，辅助定参

对应 AIHOT 的 SelectBench：先标小样本，再定门槛。

## 用法

::

    # 用内置样本对（10 对，覆盖同事件/无关各半）
    python scripts/calibrate_story_threshold.py

    # 用自己的标注集：JSON 数组 [[label, text_a, text_b], ...]，label 1=同事件 0=无关
    python scripts/calibrate_story_threshold.py --pairs my_pairs.json

    # 机器可读输出（供回归比对）
    python scripts/calibrate_story_threshold.py --json

## 判读标准

- **可分性**：``同事件最小值 > 无关最大值`` 才算干净可分
- 若不可分，看重叠区间大小；重叠越大，越依赖第二层判定器（LLM 复核）
- 选定阈值应使误召率趋近 0（误合并代价 > 漏合并代价）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge.embedder import Embedder, EmbeddingUnavailableError  # noqa: E402
from knowledge.story_group import (  # noqa: E402
    RECALL_SIMILARITY,
    RECHECK_SIMILARITY,
    cosine,
)

#: 内置样本：(label, 文本A, 文本B)，label 1=同一件事，0=无关
BUILTIN_PAIRS: list[tuple[int, str, str]] = [
    # ---- 同事件：宁德时代发布麒麟电池 ----
    (
        1,
        "宁德时代发布第三代麒麟电池，能量密度提升20%",
        "宁德时代推出新一代麒麟电池，能量密度较上一代提高两成",
    ),
    (
        1,
        "宁德时代发布第三代麒麟电池，能量密度提升20%",
        "宁德时代麒麟电池新品发布，整车续航突破1000公里",
    ),
    # ---- 同事件：茅台三季报 ----
    (
        1,
        "贵州茅台三季度净利润同比增长15%",
        "茅台三季报净利增长15%，直销渠道占比继续提升",
    ),
    # ---- 同事件：央行降准 ----
    (
        1,
        "央行宣布降准0.5个百分点，释放长期资金约1万亿元",
        "人民银行下调存款准备金率0.5个百分点，可释放万亿流动性",
    ),
    # ---- 同事件：某公司被立案调查 ----
    (
        1,
        "证监会决定对某上市公司立案调查，涉嫌信息披露违法",
        "该公司公告称收到证监会立案告知书，因涉嫌信披违规",
    ),
    # ---- 无关：跨行业、跨主题 ----
    (0, "英伟达发布新一代GPU架构，算力提升三倍", "某地暴雨导致地铁全线停运"),
    (0, "国足公布新一期集训名单，两名新人入选", "宁德时代发布第三代麒麟电池，能量密度提升20%"),
    (0, "央行宣布降准0.5个百分点，释放长期资金约1万亿元", "英伟达发布新一代GPU架构，算力提升三倍"),
    (0, "茅台三季报净利增长15%，直销渠道占比继续提升", "某地暴雨导致地铁全线停运"),
    (0, "国足公布新一期集训名单，两名新人入选", "证监会决定对某上市公司立案调查，涉嫌信息披露违法"),
]


def _load_pairs(path: str | None) -> list[tuple[int, str, str]]:
    """从 JSON 文件加载标注集；未提供则用内置样本。"""
    if not path:
        return BUILTIN_PAIRS
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(int(item[0]), str(item[1]), str(item[2])) for item in raw]


def _stats(values: list[float]) -> dict[str, float | None]:
    """安全统计：空列表返回 None 而非抛异常。"""
    if not values:
        return {"min": None, "max": None, "mean": None, "median": None}
    return {
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="标定事件判定阈值（同事件 vs 无关）")
    parser.add_argument("--pairs", help="标注集 JSON 路径（[[label,a,b],...]）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()

    pairs = _load_pairs(args.pairs)
    embedder = Embedder()

    texts: list[str] = []
    for _, left, right in pairs:
        texts.extend([left, right])

    try:
        vectors = embedder.embed_texts(texts)
    except EmbeddingUnavailableError as exc:
        print(f"[error] 嵌入不可用：{exc}", file=sys.stderr)
        return 1

    scored: list[dict] = []
    for idx, (label, left, right) in enumerate(pairs):
        sim = cosine(vectors[2 * idx], vectors[2 * idx + 1])
        scored.append({"label": label, "similarity": sim, "a": left, "b": right})

    pos = [item["similarity"] for item in scored if item["label"] == 1]
    neg = [item["similarity"] for item in scored if item["label"] == 0]

    pos_stats = _stats(pos)
    neg_stats = _stats(neg)

    separable = (
        pos_stats["min"] is not None
        and neg_stats["max"] is not None
        and pos_stats["min"] > neg_stats["max"]
    )

    # 候选阈值网格：看不同门槛下漏合/误合的比例
    grid = []
    for threshold in (0.50, 0.60, 0.70, 0.75, 0.80, 0.85):
        recall = sum(1 for v in pos if v >= threshold) / len(pos) if pos else 0.0
        false_hit = sum(1 for v in neg if v >= threshold) / len(neg) if neg else 0.0
        grid.append(
            {
                "threshold": threshold,
                "recall": round(recall, 3),
                "false_hit": round(false_hit, 3),
            }
        )

    suggestion = None
    if separable:
        suggestion = round((float(pos_stats["min"]) + float(neg_stats["max"])) / 2, 3)

    report = {
        "model": embedder.model_name,
        "dim": embedder.dim,
        "pairs": len(pairs),
        "same_story": {**pos_stats, "count": len(pos)},
        "unrelated": {**neg_stats, "count": len(neg)},
        "separable": separable,
        "suggested_recall_threshold": suggestion,
        "current_recall_threshold": RECALL_SIMILARITY,
        "current_recheck_threshold": RECHECK_SIMILARITY,
        "grid": grid,
        "details": [
            {
                "label": item["label"],
                "similarity": round(item["similarity"], 4),
                "a": item["a"],
                "b": item["b"],
            }
            for item in scored
        ],
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"模型 {report['model']} / {report['dim']} 维 / {report['pairs']} 对样本")
        print(
            f"同事件组：min={pos_stats['min']} max={pos_stats['max']} "
            f"mean={pos_stats['mean']} median={pos_stats['median']}"
        )
        print(
            f"无关组  ：min={neg_stats['min']} max={neg_stats['max']} "
            f"mean={neg_stats['mean']} median={neg_stats['median']}"
        )
        print(f"可分性：{'干净可分' if separable else '存在重叠，需靠第二层判定器'}")
        if suggestion is not None:
            print(f"建议召回阈值：{suggestion}")
        print(f"当前取值：召回={RECALL_SIMILARITY} 复核={RECHECK_SIMILARITY}")
        print("阈值网格（recall=同事件被召回比例，false_hit=无关被误召比例）：")
        for row in grid:
            mark = " ←当前" if abs(row["threshold"] - RECALL_SIMILARITY) < 1e-9 else ""
            print(
                f"  {row['threshold']:.2f}  recall={row['recall']:.2f} "
                f"false_hit={row['false_hit']:.2f}{mark}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
