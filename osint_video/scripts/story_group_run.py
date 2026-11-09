"""事件层聚类算子的一次性执行入口（systemd timer / cron 调用）。

## 为什么需要这个脚本

``knowledge/story_group.py`` 只是库，运维侧需要一个可被 systemd 托管的
进程入口。没有入口，方案就无法落地——这是 P0 必须补的一块。

## 退出码约定（供监控直接消费）

| 退出码 | 含义 | 告警策略 |
|---|---|---|
| 0 | 正常完成 | 无 |
| 2 | **降级完成**（嵌入不可用，退化为文本相似度） | **必须告警**：合并率会异常低，但不报错 |
| 1 | 执行异常 | 立即告警 |

降级用独立退出码而非 0/1，是为了让监控能区分"没跑成"与"跑成了但质量
下降"——后者更隐蔽，也更危险。

## 用法

::

    python scripts/story_group_run.py                 # 默认：14 天窗、只聚已复核
    python scripts/story_group_run.py --dry-run       # 干跑，只报分组计划
    python scripts/story_group_run.py --all           # 放开到未复核事实
    python scripts/story_group_run.py --window-days 30 --limit 5000
    python scripts/story_group_run.py --borderline-judge llm --dry-run
                                                      # 只对临界带的 2 对开 LLM 复核

## 临界带复核与人工确认

``--borderline-judge llm`` 只把 ``[门槛-0.05, 门槛)`` 这条窄带交给 Ollama，
不动其余区间的行为。被它抬过门槛的配对会：① 以 ``decided_by='llm_recheck'``
落库；② 写进待确认队列供人二审。人工用 ``scripts/borderline_review.py``
裁决后写进台账，下一轮聚类台账优先于 LLM（``decided_by='manual'``）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本在任意 cwd 下都能以包内绝对导入运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.logging_setup import setup_logging  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge import borderline_review as br  # noqa: E402
from knowledge import run_ledger as rl  # noqa: E402
from knowledge.story_group import (  # noqa: E402
    DEFAULT_WINDOW_DAYS,
    StoryGrouper,
)

#: 降级完成的退出码
EXIT_DEGRADED = 2
#: 执行异常的退出码
EXIT_FAILED = 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把事实聚成事件（P0 事件层算子，幂等可重跑）"
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=f"时间窗（天），默认 {DEFAULT_WINDOW_DAYS}",
    )
    parser.add_argument(
        "--limit", type=int, default=2000, help="单轮处理上限，默认 2000"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="放开到未复核事实（默认为 False，只聚类 review_status='reviewed'）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只计算分组计划，不写库"
    )
    parser.add_argument(
        "--judge",
        choices=("rule", "llm"),
        default="rule",
        help=(
            "第二层判定器（覆盖整个 [召回门, 直判门槛) 区间）："
            "rule=可解释规则（缺省，P0 行为）；"
            "llm=Ollama 四分类判定（P1）。llm 档在判定后端不可用时"
            "自动回落 rule 并以 2 退出，不会静默少合并。"
        ),
    )
    parser.add_argument(
        "--borderline-judge",
        choices=("off", "llm"),
        default=None,
        help=(
            "临界带复核判定器（只覆盖 [门槛-0.05, 门槛) 这条窄带）："
            "off=沿用主判定器结论；llm=该带改由 Ollama 复核。"
            "缺省取配置项 STORY_BORDERLINE_JUDGE。"
            "真机该带仅 2 对，开它比全网开 LLM 便宜一个数量级。"
        ),
    )
    parser.add_argument(
        "--judge-prompt",
        default=None,
        help=(
            "判定提示词版本（llm_judge.PROMPT_VARIANTS 的键，缺省取配置项 "
            "JUDGE_PROMPT_VARIANT）。版本会写进 osint_story.model_version；"
            "不同版本的判定结论不可直接对比，A/B 请用 "
            "scripts/calibrate_judge_prompt.py"
        ),
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help=(
            "人工裁决台账路径，缺省取配置项 STORY_BORDERLINE_OVERRIDES"
            "（data/story_borderline_overrides.json）。文件不存在=无人工裁决"
        ),
    )
    parser.add_argument(
        "--review-queue",
        default=None,
        help="待人工确认队列路径，缺省取配置项 STORY_BORDERLINE_PENDING",
    )
    parser.add_argument(
        "--no-queue",
        action="store_true",
        help="不写待确认队列（仍会计数，只是不留明细）",
    )
    parser.add_argument(
        "--cross-window",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "跨窗口召回：窗内两路都没挂上时，再到窗外 window_days×3 天内"
            "已归属的锚点里找一次（护栏：vector 口径 + 共同主体 + 余弦≥0.70）。"
            "缺省取配置项 STORY_CROSS_WINDOW（默认关）。"
            "打开会改变事件数——同一件事跨月发酵不再被切成两个。"
            "评估用 --dry-run，看 anchors_loaded 与 cross_window_merged。"
        ),
    )
    parser.add_argument(
        "--write-queue",
        action="store_true",
        help="dry-run 时也写待确认队列（默认 dry-run 不落任何文件）",
    )
    parser.add_argument(
        "--record",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "是否把本轮结果写进轮次台账（缺省取配置项 STORY_RUN_RECORD，默认开）。"
            "台账是纯追加的观测设施，不改变聚类行为；"
            "标定库演练等噪声场合用 --no-record 单轮关掉。"
            "关掉后本轮仍会做体检，只是不留历史"
        ),
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="轮次台账路径，缺省取配置项 STORY_RUN_LEDGER（data/story_run_ledger.jsonl）",
    )
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    borderline_mode = args.borderline_judge or settings.story_borderline_judge
    # 跨窗口召回：参数 > 配置 > 默认关。用 None 而不是 False 作缺省，是为了
    # 让"没说"与"显式关"可区分——`--no-cross-window` 能在配置已打开时单轮关掉，
    # 用于 A/B 对比，否则要比对"开与关"就只能改 .env 再改回来。
    cross_window = (
        settings.story_cross_window if args.cross_window is None else args.cross_window
    )

    # 台账必须在跑之前读：损坏时**立即失败**，不要跑完一轮再发现人工裁决
    # 一条都没生效——那等于白跑，而且结果看起来"正常"。
    try:
        overrides = br.load_overrides(
            args.overrides or settings.story_borderline_overrides
        )
    except br.OverrideError as exc:
        print(f"[ERROR] 人工台账不可用，已中止：{exc}", file=sys.stderr)
        return EXIT_FAILED

    judge = None
    if args.judge == "llm":
        # 局部导入：未装 httpx 或不需要 LLM 判定的部署不背这个依赖
        from knowledge.llm_judge import OllamaRelationJudge  # noqa: PLC0415

        judge = OllamaRelationJudge(prompt_variant=args.judge_prompt)
        # 必须在循环外预热：冷加载实测约 46 秒，若让第一对事实承担，
        # 它会独自吃掉整个超时预算并触发熔断，连带废掉整轮判定。
        if not judge.warmup():
            print(
                "[WARN] 判定模型预热失败，本轮将回落规则判定并以 2 退出。",
                file=sys.stderr,
            )

    borderline_judge = None
    if borderline_mode == "llm":
        from knowledge.llm_judge import OllamaRelationJudge  # noqa: PLC0415

        # 与主判定器复用同一实例的前提是"主判定器也是 LLM"：那种情况下
        # 预热已经做过、熔断状态也共享，再建一个只是多一份预算计数。
        # 主判定器是规则时必须新建，且必须自己预热——否则临界带第一对
        # 会独自承担 46 秒冷加载，直接吃满超时并误判为后端故障。
        borderline_judge = (
            judge
            if judge is not None
            else OllamaRelationJudge(prompt_variant=args.judge_prompt)
        )
        if judge is None and not borderline_judge.warmup():
            print(
                "[WARN] 临界复核模型预热失败，本轮临界带将回落规则判定。",
                file=sys.stderr,
            )

    try:
        grouper = StoryGrouper(
            judge=judge,
            borderline_judge=borderline_judge,
            borderline_overrides=overrides,
            cross_window=cross_window,
        )
        result = grouper.run(
            window_days=args.window_days,
            only_reviewed=not args.all,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        # PG 不可用等硬故障：输出结构化结果便于日志采集，再以 1 退出
        print(
            json.dumps(
                {"ok": False, "error": str(exc), "error_type": type(exc).__name__},
                ensure_ascii=False,
            )
        )
        return EXIT_FAILED

    # 待确认队列：只有"被复核提升"的配对才入队——那正是人要看的全部内容。
    # dry-run 默认不落盘（保持纯观测语义），要落得显式加 --write-queue。
    queued = 0
    queue_skipped = ""
    if result.borderline_promoted_samples and not args.no_queue:
        if args.dry_run and not args.write_queue:
            queue_skipped = "dry_run"
        else:
            try:
                queued = br.append_pending(
                    result.borderline_promoted_samples,
                    args.review_queue or settings.story_borderline_pending,
                    overrides=overrides,
                )
            except OSError as exc:
                # 队列写不进去不该让整轮聚类算失败：判定与落库已经完成，
                # 缺的只是"人工二审的入口"。但必须大声说出来。
                queue_skipped = f"write_failed:{exc}"
                print(f"[WARN] 待确认队列写入失败：{exc}", file=sys.stderr)

    # 积压画像：队列是"等人处理"的东西，没人处理就会一直涨。
    # 统计口径用台账切分"真待办 / 已裁决"，否则僵尸行会把积压数虚报上去，
    # 演练一次之后就没人再信这个告警了。
    backlog = br.QueueStats()
    alert = None
    try:
        backlog = br.queue_stats(
            args.review_queue or settings.story_borderline_pending,
            overrides=overrides,
        )
        alert = br.backlog_alert(
            backlog,
            max_pending=settings.story_review_backlog_max,
            max_age_days=settings.story_review_max_age_days,
        )
    except OSError as exc:
        # 读不到队列不该让聚类失败，但也不能假装积压为 0。
        alert = f"队列读取失败：{exc}"

    payload = {
        "ok": True,
        **result.as_dict(),
        "borderline_judge": borderline_mode,
        # 跨窗口是"会改变事件数"的开关，必须进 payload：不做成可见字段，
        # 同一批语料今天 28 个事件明天 26 个就无从解释。
        "cross_window": cross_window,
        # 判定器名带版本（llm-ollama-v2），与落库的 model_version 同源。
        # 换提示词等于换判定器，日志里不带版本就没法解释"同一批语料为什么
        # 昨天 28 个事件今天 27 个"。
        "judge_name": getattr(getattr(grouper, "judge", None), "name", "rule"),
        # 临界带复核可能与主判定器不同源（主判定器是 rule 时它仍是 LLM），
        # 单独报一个：只报主判定器会让人以为本轮没跑 LLM，而事实相反。
        "borderline_judge_name": getattr(borderline_judge, "name", None),
        "overrides_loaded": len(overrides),
        "review_queue_written": queued,
        "review_queue_skipped": queue_skipped,
        "review_backlog": {**backlog.as_dict(), "alert": alert},
    }
    # 轮次台账：把本轮结果落成**可跨轮比较**的历史。
    #
    # 放在 print 之前，是为了让体检结论也进 payload——监控读一行 JSON 就够了，
    # 不必再去解析台账文件。写失败只降级为告警：台账是观测设施，它的故障不该
    # 让整轮聚类算失败；但也不能静默，否则台账失效时会看起来"一直很健康"。
    record_enabled = settings.story_run_record if args.record is None else args.record
    ledger_path = args.ledger or settings.story_run_ledger
    ledger_info: dict = {"enabled": record_enabled, "path": ledger_path, "recorded": False}
    if record_enabled:
        try:
            record = rl.build_record(
                payload,
                db_name=settings.pg_db,
                window_days=args.window_days,
                limit=args.limit,
                only_reviewed=not args.all,
            )
            # 先读历史再追加：check_round 要拿"本轮之前"的基线，把本轮混进去
            # 就会自己跟自己比，趋势恒为 0 变化。
            history = rl.load_rounds(ledger_path)
            findings = rl.check_round(record, history)
            rl.append_round(ledger_path, record)
            ledger_info["recorded"] = True
            ledger_info["findings"] = [
                {"level": f.level, "code": f.code, "message": f.message, "action": f.action}
                for f in findings
            ]
            for finding in findings:
                if finding.level in ("warn", "fail"):
                    suffix = f" → {finding.action}" if finding.action else ""
                    print(
                        f"[{finding.level.upper()}] 轮次体检 {finding.code}："
                        f"{finding.message}{suffix}",
                        file=sys.stderr,
                    )
        except OSError as exc:
            ledger_info["error"] = str(exc)
            print(f"[WARN] 轮次台账写入失败（本轮结果仍有效）：{exc}", file=sys.stderr)
    payload["ledger"] = ledger_info

    print(json.dumps(payload, ensure_ascii=False))

    if cross_window:
        # 只报"事件数"看不出跨窗有没有起作用：锚点 0 条说明窗外没有已归属事实
        # （不是护栏挡掉了），合并 0 对说明护栏全挡掉了。两者处置完全不同。
        print(
            f"[INFO] 跨窗口召回已开：窗外锚点 {result.anchors_loaded} 条，"
            f"跨窗合并 {result.cross_window_merged} 对"
            f"（护栏：vector 口径 + 共同主体 + 余弦≥0.70）。",
            file=sys.stderr,
        )
        if result.anchors_loaded == 0:
            print(
                "       锚点为 0 说明窗外没有已归属事实——"
                "跨窗召回只在'历史事件已存在'时才有作用，"
                "空库首轮不会有效果，这不是故障。",
                file=sys.stderr,
            )
    if result.borderline_rechecked:
        print(
            f"[INFO] 临界带复核 {result.borderline_rechecked} 对，"
            f"其中 {result.borderline_promoted} 对被改判为同一事件并合并"
            f"（decided_by=llm_recheck）。这批配对全部低于直判门槛，"
            "是误合并风险最集中的地方，建议用 scripts/borderline_review.py 过一遍。",
            file=sys.stderr,
        )
    if result.manual_overrides:
        print(
            f"[INFO] 本轮命中人工台账 {result.manual_overrides} 对（decided_by=manual）。"
            "此结果已被人工干预，与全自动跑出的结果不可直接对比。",
            file=sys.stderr,
        )
    if alert:
        # 用 WARN 而不是 INFO：积压从来不靠人主动查日志发现，只能靠它自己喊。
        # 这里不退出非零——聚类本身是成功的，卡住的是复核，两件事不该混。
        print(f"[WARN] 待确认队列积压：{alert}", file=sys.stderr)
        print(
            "       处理：python scripts/borderline_review.py list --pending-only",
            file=sys.stderr,
        )
    elif backlog.pending:
        age = (
            "最老一条入队时间不可解析"
            if backlog.oldest_days is None
            else f"最老 {backlog.oldest_days:.1f} 天"
        )
        print(
            f"[INFO] 待确认队列 {backlog.pending} 条待办（{age}），未达告警阈值。",
            file=sys.stderr,
        )

    if result.borderline:
        # 临界带是调阈值唯一靠得住的依据：只报"事件数"看不出门槛该升还是该降。
        # 写在 stderr 而不是 payload 里，是为了不污染面向机器的 JSON 输出。
        print(
            f"[INFO] 临界未合并 {result.borderline} 对（相似度在直判门槛下方 0.05 内）。"
            "该带内同一事件多 → 门槛偏高；无关事实多 → 门槛合适或偏低。",
            file=sys.stderr,
        )
        if args.dry_run:
            for sample in result.borderline_samples[:5]:
                print(
                    f"   cos={sample['similarity']:.4f}（门槛 {sample['threshold']:.2f}，"
                    f"差 {sample['gap']:.4f}，判定={sample['decided_by']}）",
                    file=sys.stderr,
                )
                print(
                    f"     {sample['left_title']}  ||  {sample['right_title']}",
                    file=sys.stderr,
                )

    if result.degraded:
        # 降级必须显式可见，否则运维只会看到"事件变少了"而误判为数据不足
        print(
            f"[WARN] 本轮为降级运行，原因：{result.degrade_reason}。"
            "合并率将显著下降（judge_failures=%d/%d），"
            "请检查 Ollama、bge-m3 与判定模型是否可用。"
            % (result.judge_failures, result.judge_calls),
            file=sys.stderr,
        )
        return EXIT_DEGRADED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
