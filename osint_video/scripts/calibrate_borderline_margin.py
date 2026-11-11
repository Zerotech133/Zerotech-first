"""标定临界带宽度 ``BORDERLINE_MARGIN``（多宽才够覆盖"差一点就合"的真同事件）。

为什么需要单独标定
------------------
``BORDERLINE_MARGIN=0.05`` 当初是拍出来的，不是量出来的。这个数直接决定三件
事，而且三件事的方向相反：

- **太小**：真同事件里相似度稍低的那几对永远进不了复核带，拆散照旧存在，
  开了 LLM 复核却像是"没起作用"；
- **太大**：带内涌入大量无关配对，白付调用成本；更糟的是每多一对就多一次
  "LLM 误判为同事件"的机会，而误合并会污染 ``independent_sources`` 且事后
  几乎不可能被发现；
- **超过可达上限就完全无效**：候选召回门是 ``RECALL_SIMILARITY``(0.60)，
  低于它的配对在进判定环节之前就被拦掉了，带宽再大也覆盖不到。

所以带宽不是"越宽越安全"，它有一个由召回门决定的天花板，和一个由真值分布
决定的性价比拐点。两者都必须量出来。

本脚本怎么量
------------
一次 ``trace_pairs=True`` 的 dry-run 就能拿到**全部**被判定过的候选对（含
相似度与规则结论），再按 ``extension->>'truth_story'`` 给每对标真值，然后按
带宽切片统计。**不需要按每个 margin 重跑一遍库**——配对集合由召回门
（0.60）决定，与带宽无关，带宽只决定"带内那批交给谁判"。

三个输出指标：

- ``covered``：带内**真该合**的对数 = 开复核能救回的上界；
- ``noise``：带内**不该合**的对数 = 复核成本 + 误合并暴露面；
- ``missed``：带外更低相似度处仍真该合的对数 = 这个带宽救不回的。

建议取"``missed`` 归零的最小 ``margin``"：再宽只会增加 ``noise``，救不回
更多真事件。

用法
----
    python scripts/calibrate_borderline_margin.py
    python scripts/calibrate_borderline_margin.py --margins 0,0.02,0.05,0.08,0.15
    python scripts/calibrate_borderline_margin.py --detail --json

注意：脚本是**只读**的（``dry_run``），不改库；但需要库里有 ``truth_story``
真值（由 ``scripts/seed_verify_corpus.py`` 写入）。真实生产库没有这个字段，
脚本会明确报错，不会给出全零的假结论。

退出码：0 正常；1 无真值 / 无可观测配对。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import DatabaseUnavailableError, fetch_all  # noqa: E402
from knowledge import calibration_stamp as stamp  # noqa: E402
from knowledge.story_group import (  # noqa: E402
    BORDERLINE_MARGIN,
    RECHECK_SIMILARITY,
    RECALL_SIMILARITY,
    StoryGrouper,
    resolve_recheck_similarity,
    unordered_pair,
)

_TRUTH_SQL = """
SELECT unit_key, extension->>'truth_story' AS truth
  FROM osint_knowledge_unit
 WHERE extension ? 'truth_story'
"""

_DEFAULT_MARGINS = (0.0, 0.02, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15)

#: 落标定戳所需的最小样本。低于这个量的"建议值"是退化解，不是结论：
#: 候选 1 对、真同事件 0 对时，"missed 归零的最小带宽"必然是 0.00，而这只能
#: 说明库里没有可比样本，不能说明带宽可以收窄到 0。
MIN_PAIRS_FOR_STAMP: int = 5
MIN_SAME_PAIRS_FOR_STAMP: int = 1


def stamp_readiness(pairs: Sequence[Mapping[str, Any]]) -> tuple[bool, str]:
    """样本是否足以支撑一枚标定戳，返回 ``(ok, 原因)``。

    纯函数：把"该不该落戳"的判断从 ``main`` 里拆出来，才能在离线环境里钉住——
    这条判断一旦写错，后果是一枚**看起来合法的假戳**，而上线前自检会据此把
    红灯变绿，比没有戳更危险。
    """
    total = len(pairs)
    same = sum(1 for p in pairs if p["truth_same"])
    if total < MIN_PAIRS_FOR_STAMP:
        return False, (
            f"候选样本 {total} 对，低于下限 {MIN_PAIRS_FOR_STAMP}"
        )
    if same < MIN_SAME_PAIRS_FOR_STAMP:
        return False, (
            f"候选样本 {total} 对但其中真同事件 {same} 对"
            f"（下限 {MIN_SAME_PAIRS_FOR_STAMP}）——没有正例时"
            "\"missed 归零的最小带宽\"必然是 0.00，这是无样本而非有结论"
        )
    return True, f"候选 {total} 对（真同事件 {same} 对）"


def _stamp_blocked_hint() -> str:
    return (
        "最常见原因：工作库已分组（跑过多轮聚类），已合并的配对不再进候选集。"
        "请在未分组的库上标定（story/member 为空，或另起一个库灌语料后再标定）。"
    )


def load_truth() -> dict[str, str]:
    """``{unit_key: truth_story}``。只含有真值标注的事实。"""
    rows = fetch_all(_TRUTH_SQL)
    out: dict[str, str] = {}
    for row in rows:
        key = str(row.get("unit_key") or "")
        truth = row.get("truth")
        if key and truth:
            out[key] = str(truth)
    return out


def dedupe_pairs(observed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把双向观测压成唯一配对。

    同一对事实会在两个方向各判一次（A 作 pivot 见过 B，B 作 pivot 又见 A），
    ``pair_record`` 已按无序对存键，但仍是两条记录。不去重会把"带内 2 对"
    报成 4 对，据此判断"带宽内真该合不少"就会把带宽放宽一倍。

    去重时保留**首次**出现的那条：两方向的相似度由同一个余弦算出，理应相同；
    若不同（不该发生），首次与实际判定顺序一致，比取最大值更贴近真实行为。
    """
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for row in observed:
        key = unordered_pair(row.get("left"), row.get("right"))
        seen.setdefault(key, row)
    return list(seen.values())


def label_pairs(
    pairs: list[dict[str, Any]], truth: dict[str, str]
) -> list[dict[str, Any]]:
    """给每对标 ``same`` / ``diff`` 真值。

    判据：两侧都有真值标注且相等 → 同一事件。任一侧无标注（``singleton``，
    语料里 ``truth_story`` 为 null）→ 不同事件——**这是刻意的**：独事实按定义
    就是"独自构成一个事件"，把它当成"未知"会让所有 singleton 配对既不进
    ``covered`` 也不进 ``noise``，两侧统计同时缩水，带宽便无从判断。

    两侧都没有标注的事实根本不该出现在候选里（它们不在真值语料内），出现了
    就是语料污染，直接跳过而不是猜一个标签。
    """
    out: list[dict[str, Any]] = []
    for row in pairs:
        left, right = row["left"], row["right"]
        lt, rt = truth.get(left), truth.get(right)
        if lt is None and rt is None:
            continue
        same = bool(lt is not None and rt is not None and lt == rt)
        out.append({**row, "truth_same": same, "left_truth": lt, "right_truth": rt})
    return out


def slice_by_margin(
    pairs: list[dict[str, Any]],
    margin: float,
    threshold: float,
    gate: float,
) -> dict[str, Any]:
    """按给定带宽切一刀，返回带内/带外统计。

    只统计**向量口径**的配对：文本口径的门槛是 ``RULE_DIRECT_SIMILARITY``
    (0.85)，与 0.75 不是同一个位置，混进来会让"带内"的边界含义漂移。真机上
    嵌入可用时全部走向量口径，文本口径只在降级轮出现，标定应基于正常路径。
    """
    low = threshold - margin
    covered = noise = 0
    missed = 0
    covered_pairs: list[dict[str, Any]] = []
    noise_pairs: list[dict[str, Any]] = []
    missed_pairs: list[dict[str, Any]] = []
    for row in pairs:
        if row.get("mode") != "vector":
            continue
        sim = float(row["similarity"])
        if sim >= threshold:
            # 门槛之上由余弦直判，与本带宽无关。
            continue
        if sim < gate:
            # 召回门之下：进不了判定环节，任何带宽都覆盖不到。
            continue
        if sim >= low:
            if row["truth_same"]:
                covered += 1
                covered_pairs.append(row)
            else:
                noise += 1
                noise_pairs.append(row)
        elif row["truth_same"]:
            missed += 1
            missed_pairs.append(row)
    return {
        "margin": round(margin, 4),
        "band": [round(low, 4), round(threshold, 4)],
        "covered": covered,
        "noise": noise,
        "missed": missed,
        "call_upper_bound": covered + noise,
        "covered_pairs": covered_pairs,
        "noise_pairs": noise_pairs,
        "missed_pairs": missed_pairs,
    }


def recommend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """取 ``missed`` 归零的最小带宽；全都不归零则回报缺口。

    为什么是"最小"而不是"F1 最优"：带宽只影响**谁来判**，不影响判得对不对
    （那由复核器决定）。在 ``covered`` 已饱和的前提下，更宽的带纯粹是增加
    ``noise``——多付调用、多暴露一次误合并机会。因此这是"满足约束的最小值"
    问题，不是最优化问题。
    """
    saturated = [r for r in rows if r["missed"] == 0]
    if saturated:
        best = min(saturated, key=lambda r: (r["margin"], r["noise"]))
        return {"margin": best["margin"], "reason": "missed 归零的最小带宽"}
    narrowest = min(rows, key=lambda r: r["missed"])
    return {
        "margin": narrowest["margin"],
        "reason": (
            "即使取到可达上限仍有 %d 对真该合落在带外——这些配对需要降门槛或"
            "降召回门，不是带宽能解决的" % narrowest["missed"]
        ),
    }


def render(rows: list[dict[str, Any]], rec: dict[str, Any], threshold: float) -> str:
    lines = [
        "临界带宽度标定（threshold=%.2f，召回门=%.2f，可达上限 margin=%.2f）"
        % (threshold, RECALL_SIMILARITY, threshold - RECALL_SIMILARITY),
        "",
        "| margin | 带宽 | 带内该合 | 带内不该合 | 带外漏合 | 调用上界 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            "| %.2f | [%.2f, %.2f) | %d | %d | %d | %d |"
            % (
                r["margin"],
                r["band"][0],
                r["band"][1],
                r["covered"],
                r["noise"],
                r["missed"],
                r["call_upper_bound"],
            )
        )
    lines.append("")
    lines.append("建议：margin=%.2f（%s）" % (rec["margin"], rec["reason"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="标定 BORDERLINE_MARGIN：按真值统计各带宽下的覆盖与噪声"
    )
    parser.add_argument(
        "--margins",
        default=",".join(str(m) for m in _DEFAULT_MARGINS),
        help="待评估的带宽列表，逗号分隔（默认 0,0.02,...,0.15）",
    )
    parser.add_argument("--window-days", type=int, default=60)
    parser.add_argument(
        "--all",
        action="store_true",
        help="放开到未复核事实（默认只聚已复核，与生产一致）",
    )
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument(
        "--detail",
        action="store_true",
        help="打印每个带宽下的配对明细（含标题，供人工判断标注是否正确）",
    )
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    parser.add_argument(
        "--no-stamp",
        action="store_true",
        help="不写标定新鲜度戳（默认会写，供上线前自检比对语料是否变更）",
    )
    args = parser.parse_args(argv)

    try:
        truth = load_truth()
    except DatabaseUnavailableError as exc:
        print(f"[ERROR] 数据库不可用：{exc}", file=sys.stderr)
        return 1
    if not truth:
        print(
            "[ERROR] 库中没有 truth_story 真值标注，无法标定。"
            "先跑 scripts/seed_verify_corpus.py 写入真值语料。",
            file=sys.stderr,
        )
        return 1

    grouper = StoryGrouper(trace_pairs=True)
    result = grouper.run(
        window_days=args.window_days,
        only_reviewed=not args.all,
        limit=args.limit,
        dry_run=True,
    )
    if result.pairs_observed_truncated:
        print(
            "[ERROR] 配对观测被 PAIR_TRACE_LIMIT 截断，样本不完整，结论不可用。"
            "请缩小 --limit 或提高上限后重跑。",
            file=sys.stderr,
        )
        return 1

    threshold = resolve_recheck_similarity()
    pairs = label_pairs(dedupe_pairs(result.pairs_observed), truth)
    if not pairs:
        print("[ERROR] 没有可标注的候选对，检查语料与真值是否匹配。", file=sys.stderr)
        return 1

    margins = [float(x) for x in args.margins.split(",") if x.strip()]
    rows = [slice_by_margin(pairs, m, threshold, RECALL_SIMILARITY) for m in margins]
    rec = recommend(rows)

    # 落到标定戳：指纹用本次**实际标定所用**的真值语料，而不是重新查一遍库——
    # 重查会把"标定过程中语料被改"这种并发情形掩盖成"戳是新的"。写戳失败不该
    # 让标定结论作废，但必须显式报出来，否则自检会显示"从未标定"而人不知道原因。
    #
    # **样本不足不落戳**：2026-11-05 真机踩到。工作库跑过多轮聚类（29 事件/42 成员/
    # 50 事实）后再标定，已合并的配对不再进候选集，候选从 2 对塌到 1 对、真同事件
    # 0 对，于是"missed 归零的最小带宽"退化成 0.00——这个 0.00 是**没有样本**推出来
    # 的，不是"带宽可以收窄到 0"。若照此落戳，上线前自检会拿一枚空依据的戳把红灯
    # 变绿，比没有戳更危险。
    same_pairs = sum(1 for p in pairs if p["truth_same"])
    if not args.no_stamp:
        ready, reason = stamp_readiness(pairs)
        if not ready:
            print(
                f"[WARN] {reason}，**不写标定戳**。\n        {_stamp_blocked_hint()}",
                file=sys.stderr,
            )
        else:
            try:
                # 指纹取**整个真值语料**（含 truth_story 为 null 的独立事件），
                # 而不是本脚本 ``load_truth`` 过滤后的子集。两个理由：
                # 1. 与提示词标定共用同一份定义，两枚戳才可比——否则带宽戳记 33 条、
                #    提示词戳记 50 条，上线自检会永远报"语料变了"（2026-11-05 实测）；
                # 2. "某条从 null 变成有标注"同样改变带宽标定的输入（33→34），
                #    只盯非空子集就检不出这类变更。
                fingerprint, units = stamp.corpus_fingerprint()
                stamp.write_stamp(
                    stamp.KIND_MARGIN,
                    fingerprint=fingerprint,
                    units=units,
                    detail={
                        "recommended_margin": rec["margin"],
                        "reason": rec["reason"],
                        "threshold": threshold,
                        "recall_gate": RECALL_SIMILARITY,
                        "pairs": len(pairs),
                        "pairs_same": same_pairs,
                        "truth_units": len(truth),
                    },
                )
            except OSError as exc:
                print(f"[WARN] 标定戳写入失败（结论仍有效）：{exc}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "threshold": threshold,
                    "recall_gate": RECALL_SIMILARITY,
                    "current_margin": BORDERLINE_MARGIN,
                    "pairs_total": len(pairs),
                    "pairs_same": sum(1 for p in pairs if p["truth_same"]),
                    "recommend": rec,
                    "rows": [
                        {k: v for k, v in r.items() if not k.endswith("_pairs")}
                        for r in rows
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0

    print(render(rows, rec, threshold))
    print("")
    print(
        "样本：%d 对候选（其中真同事件 %d 对），阈值 %.2f 由配置解析"
        % (len(pairs), sum(1 for p in pairs if p["truth_same"]), threshold)
    )
    if threshold != RECHECK_SIMILARITY:
        print(
            "注意：当前阈值 %.2f 与常量 %.2f 不同（环境变量覆盖），"
            "标定结果只对当前配置成立。" % (threshold, RECHECK_SIMILARITY)
        )

    if args.detail:
        for r in rows:
            print("")
            print("--- margin=%.2f ---" % r["margin"])
            for tag, items in (
                ("带内·该合", r["covered_pairs"]),
                ("带内·不该合", r["noise_pairs"]),
                ("带外·漏合", r["missed_pairs"]),
            ):
                for p in items:
                    print(
                        "  %s cos=%.4f  %s | %s"
                        % (
                            tag,
                            p["similarity"],
                            str(p.get("left_title") or "")[:26],
                            str(p.get("right_title") or "")[:26],
                        )
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
