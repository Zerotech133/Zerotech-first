"""标定判定提示词版本（``llm_judge.PROMPT_VARIANTS``）。

为什么需要单独标定
------------------
技术债 #16：v1 提示词下"同一主体、同一时期、两项并列事项"会被**随机**判成
``progress``。真机四对同形样本里三对判 unrelated、一对判 progress——同样的
推理只在一对上发生。这不是采样抖动（``temperature=0`` 已钉死），是 v1 的
progress 定义只写了"主体相同、事态推进"，没写"必须是同一个具体事项在推进"。

这种缺陷**测不出来也看不出来**：库里的数照样"看起来合理"，只有拿一组
已知答案的配对去问，才知道它到底把哪几对并错了。

本脚本怎么量
------------
读 ``tests/fixtures/judge_probe.json`` 的探针集（24 对，全部来自真机验证语料，
以"同一主体"硬样本为主），逐对调真模型，只统计一件事：

    合并 / 不合并 与真值是否一致。

**只看合并决策、不看标签名**：``same_story`` / ``progress`` / ``roundup`` 三者
都落成员边，对独立来源数的影响完全相同；只有 ``unrelated`` 是不合并。若按
标签名算准确率，把一对真该合的判成 ``same_story`` 而非 ``progress`` 会白扣
一分，把调参方向带偏。

两个关键数：

- ``false_merge``：真值不该合、却判成合并 —— 污染独立来源数，事后几乎不可
  发现，**这是唯一的硬伤**；
- ``missed_merge``：真值该合、却判成不合并 —— 同一事件被拆成多条，热度低估。

用法
----
    python scripts/calibrate_judge_prompt.py                  # 跑全部版本
    python scripts/calibrate_judge_prompt.py --variants v1    # 只跑 v1
    python scripts/calibrate_judge_prompt.py --repeat 3       # 稳定性
    python scripts/calibrate_judge_prompt.py --dry-run        # 只校验探针集

注意：脚本是**只读**的，不写库、不改任何默认值。翻默认版本必须人工确认后
改 ``DEFAULT_PROMPT_VARIANT``（或 ``JUDGE_PROMPT_VARIANT``），不在脚本里自动做。

退出码：0 正常；1 探针集有问题 / 判定后端不可用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import DatabaseUnavailableError, fetch_all  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge import calibration_stamp as stamp  # noqa: E402
from knowledge.llm_judge import (  # noqa: E402
    PROMPT_VARIANTS,
    OllamaRelationJudge,
)

_PROBE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "judge_probe.json"
)

_UNIT_SQL = """
SELECT unit_key, unit_version, title, content, source_url, source_link,
       blogger_key, created_at
  FROM osint_knowledge_unit
 WHERE unit_key = ANY(:keys)
"""


# ------------------------------------------------------------ 探针集 --


def load_probe(path: Path | None = None) -> dict[str, Any]:
    """读探针集。缺文件时抛 ``FileNotFoundError``，不静默返回空集。"""
    target = Path(path) if path else _PROBE_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def verify_probe(probe: Mapping[str, Any], units: Mapping[str, Any]) -> list[str]:
    """校验探针集自身是否可用，返回问题列表（空 = 可用）。

    这几条校验的存在理由都是"错得很安静"：漏一条样本不会报错，只会让
    ``false_merge`` 的分子分母同时少一项，结论看起来更漂亮；一个自配对会让
    模型必然判同，白送一个真阳性。标定数据本身不可信时，后面的数全是噪音。
    """
    problems: list[str] = []
    cases = probe.get("cases") or []
    if not cases:
        return ["探针集为空"]

    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for case in cases:
        cid = str(case.get("id") or "")
        left = str(case.get("left") or "")
        right = str(case.get("right") or "")
        if not cid:
            problems.append("存在无 id 的样本")
        elif cid in seen_ids:
            problems.append("样本 id 重复：%s" % cid)
        seen_ids.add(cid)

        if not left or not right:
            problems.append("%s：left/right 不完整" % (cid or "<无 id>"))
            continue
        if left == right:
            problems.append("%s：左右是同一条事实，模型必然判同" % cid)
        pair = (left, right) if left <= right else (right, left)
        if pair in seen_pairs:
            problems.append("%s：配对重复，同一对会被计两次" % cid)
        seen_pairs.add(pair)

        for key in (left, right):
            if key not in units:
                problems.append("%s：库里找不到 %s" % (cid, key))
        if not isinstance(case.get("expected_merge"), bool):
            problems.append("%s：expected_merge 必须是布尔值" % cid)
    return problems


# -------------------------------------------------------------- 统计 --


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把逐对结果压成合并决策的混淆矩阵（纯函数，可离线单测）。"""
    expected_pos = [r for r in results if r["expected_merge"]]
    expected_neg = [r for r in results if not r["expected_merge"]]
    missed = [r for r in expected_pos if not r["merged"]]
    false_merge = [r for r in expected_neg if r["merged"]]
    failed = [r for r in results if r.get("failed")]

    by_category: dict[str, dict[str, int]] = {}
    for row in results:
        cat = str(row.get("category") or "unknown")
        bucket = by_category.setdefault(cat, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if bool(row["merged"]) == bool(row["expected_merge"]):
            bucket["correct"] += 1

    total = len(results)
    correct = total - len(missed) - len(false_merge)
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "should_merge": len(expected_pos),
        "merged_ok": len(expected_pos) - len(missed),
        "missed_merge": len(missed),
        "should_not_merge": len(expected_neg),
        "kept_apart_ok": len(expected_neg) - len(false_merge),
        "false_merge": len(false_merge),
        "failed_calls": len(failed),
        "by_category": by_category,
        "missed_ids": [str(r["id"]) for r in missed],
        "false_merge_ids": [str(r["id"]) for r in false_merge],
    }


# -------------------------------------------------------------- 执行 --


def load_units(keys: Sequence[str]) -> dict[str, Any]:
    rows = fetch_all(_UNIT_SQL, {"keys": list(keys)})
    return {str(r.get("unit_key") or ""): r for r in rows}


def run_variant(
    variant: str, cases: Sequence[Mapping[str, Any]], units: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """跑一个提示词版本，返回逐对结果。不写库。"""
    # 预算按样本数放大：探针集是一次性全量跑，不需要受 cron 轮次预算约束。
    judge = OllamaRelationJudge(prompt_variant=variant, max_calls=max(20, len(cases)))
    results: list[dict[str, Any]] = []
    for case in cases:
        left = units[str(case["left"])]
        right = units[str(case["right"])]
        # 相似度对判定无影响（刻意不进提示词），传固定值只为满足端口签名。
        verdict = judge.judge(left, right, 0.7)
        merged = verdict.relation != "unrelated"
        results.append(
            {
                "id": str(case["id"]),
                "category": str(case.get("category") or ""),
                "expected_merge": bool(case["expected_merge"]),
                "relation": verdict.relation,
                "merged": merged,
                "correct": merged == bool(case["expected_merge"]),
                "failed": bool(verdict.failed),
                "rationale": verdict.rationale,
                "why": str(case.get("why") or ""),
            }
        )
    return results


def repeat_variant(
    variant: str,
    cases: Sequence[Mapping[str, Any]],
    units: Mapping[str, Any],
    times: int,
) -> tuple[list[dict[str, Any]], int]:
    """重复跑同一版本，返回（首轮结果, 各轮之间标签发生翻转的样本数）。

    ``temperature=0`` 不保证跨请求逐位一致（服务端批处理会改变数值路径），
    所以要量翻转率。翻转率高说明这个版本本身不稳，"准确率"就只是个期望值。
    """
    runs = [run_variant(variant, cases, units) for _ in range(max(1, times))]
    flips = 0
    for idx, case in enumerate(cases):
        labels = {run[idx]["relation"] for run in runs}
        if len(labels) > 1:
            flips += 1
    return runs[0], flips


def format_table(summaries: Mapping[str, Mapping[str, Any]]) -> str:
    head = "| 版本 | 应合并 | 漏合 | 不应合并 | 误合并 | 准确率 | 失败调用 |"
    sep = "|---|---|---|---|---|---|---|"
    lines = [head, sep]
    for variant, s in summaries.items():
        lines.append(
            "| %s | %d | %d | %d | **%d** | %.1f%% | %d |"
            % (
                variant,
                s["should_merge"],
                s["missed_merge"],
                s["should_not_merge"],
                s["false_merge"],
                s["accuracy"] * 100,
                s["failed_calls"],
            )
        )
    return "\n".join(lines)


def format_detail(results: Sequence[Mapping[str, Any]], variant: str) -> str:
    wrong = [r for r in results if not r["correct"]]
    if not wrong:
        return "  %s：全部判对" % variant
    lines = ["  %s 判错 %d 对：" % (variant, len(wrong))]
    for r in wrong:
        kind = "误合并" if r["merged"] else "漏合"
        lines.append(
            "    [%s] %s → %s（真值%s合并）\n      应为：%s"
            % (
                kind,
                r["id"],
                r["relation"],
                "应" if r["expected_merge"] else "不应",
                r["why"],
            )
        )
        lines.append("      实际理由：%s" % r["rationale"][:160])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="标定判定提示词版本（只读）")
    parser.add_argument(
        "--variants",
        default=None,
        help="逗号分隔的版本名，缺省跑 PROMPT_VARIANTS 全部（如 v1,v2）",
    )
    parser.add_argument("--probe", default=None, help="探针集路径，缺省用仓库内 fixture")
    parser.add_argument(
        "--repeat", type=int, default=1, help="每个版本重复次数，用来量标签翻转率"
    )
    parser.add_argument("--detail", action="store_true", help="打印判错明细")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="只校验探针集与库内事实，不调模型"
    )
    parser.add_argument(
        "--no-stamp",
        action="store_true",
        help="不写标定新鲜度戳（默认会写；仅当跑的是当前生效版本时）",
    )
    args = parser.parse_args(argv)

    probe = load_probe(Path(args.probe) if args.probe else None)
    cases = probe.get("cases") or []
    keys = {str(c.get("left")) for c in cases} | {str(c.get("right")) for c in cases}
    try:
        units = load_units(sorted(keys))
    except DatabaseUnavailableError as exc:
        print("[ERROR] 数据库不可用：%s" % exc, file=sys.stderr)
        return 1

    problems = verify_probe(probe, units)
    if problems:
        print("[ERROR] 探针集不可用，标定结论无意义：", file=sys.stderr)
        for item in problems:
            print("  - %s" % item, file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            "[OK] 探针集 %d 对，事实齐备（应合并 %d / 不应合并 %d）"
            % (
                len(cases),
                sum(1 for c in cases if c["expected_merge"]),
                sum(1 for c in cases if not c["expected_merge"]),
            )
        )
        return 0

    wanted = (
        [v.strip() for v in args.variants.split(",") if v.strip()]
        if args.variants
        else list(PROMPT_VARIANTS)
    )
    unknown = [v for v in wanted if v not in PROMPT_VARIANTS]
    if unknown:
        print(
            "[ERROR] 未知版本 %s（可用：%s）"
            % ("/".join(unknown), "/".join(PROMPT_VARIANTS)),
            file=sys.stderr,
        )
        return 1

    payload: dict[str, Any] = {"probe_size": len(cases), "repeat": args.repeat}
    summaries: dict[str, dict[str, Any]] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    flips: dict[str, int] = {}

    for variant in wanted:
        results, flip = repeat_variant(variant, cases, units, args.repeat)
        details[variant] = results
        summaries[variant] = summarize(results)
        flips[variant] = flip
        summaries[variant]["label_flips"] = flip

    payload["variants"] = summaries

    # ---- 标定戳 ----
    # 只有"这次跑过当前生效版本"才落戳。跑 v1 却给 v2 盖章，等于用一份没跑过的
    # 结论证明 v2 有效——这是分版本最典型的自欺，且事后完全看不出来（戳里只有
    # 时间没有版本，除非刻意记下）。所以版本必须与生效配置对齐才写。
    if not args.no_stamp:
        active = str(get_settings().judge_prompt_variant)
        if active not in summaries:
            print(
                "[WARN] 本次未跑当前生效版本 %s，不写标定戳"
                "（避免用其他版本的结论给生效版本盖章）" % active,
                file=sys.stderr,
            )
        else:
            try:
                fingerprint, units_count = stamp.corpus_fingerprint()
                summary = summaries[active]
                stamp.write_stamp(
                    stamp.KIND_PROMPT,
                    fingerprint=fingerprint,
                    units=units_count,
                    detail={
                        "probe_fingerprint": stamp.probe_fingerprint(),
                        "variant": active,
                        "probe_size": len(cases),
                        "repeat": args.repeat,
                        "errors": int(summary["failed_calls"]),
                        "false_merge": int(summary["false_merge"]),
                        "missed_merge": int(summary["missed_merge"]),
                        "label_flips": int(flips[active]),
                    },
                )
            except (DatabaseUnavailableError, OSError) as exc:
                print(f"[WARN] 标定戳写入失败（结论仍有效）：{exc}", file=sys.stderr)

    if args.json:
        payload["details"] = details
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(format_table(summaries))
    if args.repeat > 1:
        print(
            "\n标签翻转（%d 轮之间至少变过一次的样本数）：%s"
            % (
                args.repeat,
                "，".join("%s=%d" % (v, n) for v, n in flips.items()),
            )
        )
    if args.detail:
        print()
        for variant in wanted:
            print(format_detail(details[variant], variant))

    # 只提示不判定：翻默认版本是要人拍板的决定，脚本不做。
    if len(summaries) > 1:
        best = min(summaries, key=lambda v: (summaries[v]["false_merge"], summaries[v]["missed_merge"]))
        print(
            "\n误合并优先的排序下更优：%s（翻默认前请确认漏合没有反向恶化）" % best
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
