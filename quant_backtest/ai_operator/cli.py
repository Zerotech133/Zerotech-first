"""所有写入都由显式子命令触发，查询使用只读数据库。"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(f"命令行参数错误：{message}")


def _parser():
    parser = _JSONArgumentParser(description="多 AI 记忆审计与离线资料核查")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="显式初始化记忆数据库")
    init.add_argument("--db", required=True, help="数据库路径")

    ingest = commands.add_parser("import-legacy", help="只读导入旧委员会 JSONL")
    ingest.add_argument("--db", required=True)
    ingest.add_argument("--reports-dir", required=True)

    snapshot = commands.add_parser("snapshot", help="只读查看记忆与审计统计")
    snapshot.add_argument("--db", required=True)
    snapshot.add_argument("--as-of", help="带时区的经验可用时间")

    recall = commands.add_parser("recall", help="按历史时点召回已审核经验")
    recall.add_argument("--db", required=True)
    recall.add_argument("--environment", required=True, choices=("shadow", "paper", "advisory", "legacy"))
    recall.add_argument("--as-of", required=True)
    recall.add_argument("--code")
    recall.add_argument("--strategy-id")
    recall.add_argument("--limit", type=int, default=5)

    export = commands.add_parser("export", help="将已提交事件导出为可重建 JSONL")
    export.add_argument("--db", required=True)
    export.add_argument("--output-dir", required=True)

    inspect = commands.add_parser("inspect-bundle", help="离线核查 ZIP 或目录，不执行包内代码")
    inspect.add_argument("--path", required=True)

    replay = commands.add_parser("replay", help="按已收盘行情逐根回放本地简化结构算法")
    replay.add_argument("--input", required=True, help="带显式收盘与可用时间的 JSON K 线列表")
    replay.add_argument("--algorithm-id", default="local-chanlun-bi-v1")

    fact = commands.add_parser("fact-pack", help="只读生成有时点约束和哈希的影子事实包")
    fact.add_argument("--input", required=True)

    shadow = commands.add_parser("shadow-decision", help="将影子研究建议显式登记到已有审计库")
    shadow.add_argument("--input", required=True)
    shadow.add_argument("--db", required=True)

    outcome = commands.add_parser("outcome-label", help="按显式交易日历生成已成熟或待成熟的价格观察标签")
    outcome.add_argument("--input", required=True)

    dataset = commands.add_parser("dataset-split", help="按标签时点、隔离间隔和内容分组切分数据集")
    dataset.add_argument("--input", required=True)

    summary = commands.add_parser("dataset-summary", help="校验切分结果并生成描述性标签统计")
    summary.add_argument("--input", required=True)

    source = commands.add_parser("capture-source", help="只读接收本地日线并记录当前来源快照")
    source.add_argument("--input", required=True)

    calendar = commands.add_parser("capture-calendar", help="只读接收显式逐日交易日历并校验覆盖")
    calendar.add_argument("--input", required=True)

    for name, description in (("calendar-reference", "从官方正文独立生成日历推导参考"),
                              ("calendar-compare", "只读对照官方参考与接收日历")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--input", required=True)

    source_inputs = commands.add_parser("source-inputs", help="将来源和日历快照对齐为审计标签输入")
    source_inputs.add_argument("--input", required=True)

    captured_outcome = commands.add_parser("captured-outcome", help="核对证券身份后生成来源快照的审计标签")
    captured_outcome.add_argument("--input", required=True)

    for name, description in (("capture-journal", "现场接收来源并显式写入独立审计库"),
                              ("capture-history", "只读查看按时点可用的接收链"),
                              ("source-review", "登记与库内快照绑定的来源证据审核"),
                              ("review-gate", "只读核对证据缺口与待人审状态"),
                              ("journal-outcome", "从库内接收记录生成审计标签"),
                              ("human-review", "提交人工审核声明，认证未配置时拒绝"),
                              ("review-history", "只读回放具体来源审核的人工状态与资格缺口")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--input", required=True)
        command.add_argument("--db", required=True)
    return parser


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def _existing_database(path):
    if not Path(path).is_file():
        raise FileNotFoundError("数据库不存在，请先显式执行 init 或 import-legacy")


def _read_input(path):
    with Path(path).open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    return _decode_input(raw)


def _decode_input(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 对象包含重复键：{key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"JSON 不允许非有限数：{value}")

    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("JSON 输入超过 8 MiB 限额")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=object_pairs,
                          parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("JSON 输入编码或嵌套层级非法") from exc


def _request_object(value, fields, optional=()):
    if type(value) is not dict or not set(fields).issubset(value) or set(value) - set(fields) - set(optional):
        raise ValueError("JSON 请求字段不完整或包含未知字段")
    return value


def main(argv=None):
    """成功返回 0；失败返回 2；包证据不足返回 3。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        if args.command == "inspect-bundle":
            from .bundles import inspect_bundle

            result = inspect_bundle(args.path)
            _emit(result)
            if not result.get("ok", False):
                return 2
            return 3 if result.get("status") == "unverified" else 0

        if args.command == "replay":
            from .replay import replay_bars

            _emit(replay_bars(_read_input(args.input), algorithm_id=args.algorithm_id))
            return 0
        if args.command == "fact-pack":
            from .shadow import build_fact_pack

            value = _request_object(_read_input(args.input), ("code", "period", "as_of", "facts", "versions"))
            _emit(build_fact_pack(**value))
            return 0
        if args.command == "outcome-label":
            from .outcomes import label_decision

            value = _request_object(_read_input(args.input),
                                    ("pack", "decision", "bars", "calendar", "source", "evaluated_at"),
                                    ("horizons",))
            _emit(label_decision(**value))
            return 0
        if args.command == "dataset-split":
            from .dataset import split_dataset

            value = _request_object(_read_input(args.input),
                                    ("rows", "feature_allowlist", "train_end", "validation_end", "test_end", "as_of"),
                                    ("embargo_seconds",))
            _emit(split_dataset(**value))
            return 0
        if args.command == "dataset-summary":
            from .dataset import summarize_split

            _emit(summarize_split(_read_input(args.input)))
            return 0
        if args.command == "capture-source":
            from .provenance import capture_snapshot

            value = _request_object(_read_input(args.input),
                                    ("path", "source_id", "code", "price_basis", "revision"), ("format",))
            _emit(capture_snapshot(**value))
            return 0
        if args.command == "capture-calendar":
            from .trading_calendar import capture_calendar

            value = _request_object(_read_input(args.input),
                                    ("paths", "exchange", "source_id", "revision", "coverage_start", "coverage_end"),
                                    ("format",))
            _emit(capture_calendar(**value))
            return 0
        if args.command == "source-inputs":
            from .source_inputs import snapshot_for_outcomes

            value = _request_object(_read_input(args.input), ("snapshot", "calendar_capture"))
            _emit(snapshot_for_outcomes(**value))
            return 0
        if args.command == "calendar-reference":
            from .calendar_reference import build_calendar_reference

            value = _request_object(_read_input(args.input),
                                    ("documents", "coverage_start", "coverage_end"))
            _emit(build_calendar_reference(**value))
            return 0
        if args.command == "calendar-compare":
            from .calendar_reference import compare_calendar_reference

            value = _request_object(_read_input(args.input), ("reference", "calendar_capture"))
            _emit(compare_calendar_reference(**value))
            return 0
        if args.command == "captured-outcome":
            from .source_inputs import label_captured_decision

            value = _request_object(_read_input(args.input),
                                    ("snapshot", "calendar_capture", "pack", "decision", "evaluated_at"),
                                    ("horizons",))
            _emit(label_captured_decision(**value))
            return 0
        if args.command == "human-review":
            from .memory import open_store

            _request_object(_read_input(args.input), ("statement", "proof"))
            _existing_database(args.db)
            with open_store(args.db, readonly=True):
                pass
            # 认证必须由可信应用接线，JSON请求不能安装验证器或自填授权。
            raise ValueError("人工审核认证适配器未配置，拒绝批准、拒绝或撤销登记")
        if args.command == "review-history":
            from .memory import open_store
            from .review_history import assess_human_review_history

            value = _request_object(_read_input(args.input), ("capture_event_id", "review_event_id", "as_of"))
            _existing_database(args.db)
            with open_store(args.db, readonly=True) as store:
                _emit(assess_human_review_history(store, **value))
            return 0
        if args.command in {"capture-journal", "source-review"}:
            from .memory import open_store

            if args.command == "capture-journal":
                from .capture_journal import capture_to_store
                value = _request_object(_read_input(args.input), ("source_request", "calendar_request"),
                                        ("previous_event_id",))
                handler = capture_to_store
            else:
                from .review_gate import record_source_review
                value = _request_object(_read_input(args.input), ("capture_event_id", "reviewer", "evidence"))
                handler = record_source_review
            _existing_database(args.db)
            with open_store(args.db, readonly=True):
                pass
            with open_store(args.db) as store:
                _emit(handler(store, **value))
            return 0
        if args.command in {"capture-history", "review-gate", "journal-outcome"}:
            from .memory import open_store

            if args.command == "capture-history":
                from .capture_journal import capture_history
                value = _request_object(_read_input(args.input), ("capture_event_id", "as_of"), ("limit",))
                handler = capture_history
            elif args.command == "review-gate":
                from .review_gate import assess_review_gate
                value = _request_object(_read_input(args.input), ("capture_event_id", "review_event_id", "as_of"))
                handler = assess_review_gate
            else:
                from .review_gate import label_journal_decision
                value = _request_object(_read_input(args.input),
                                        ("capture_event_id", "pack", "decision", "evaluated_at"), ("horizons",))
                handler = label_journal_decision
            _existing_database(args.db)
            with open_store(args.db, readonly=True) as store:
                _emit(handler(store, **value))
            return 0
        if args.command == "shadow-decision":
            from .shadow import make_shadow_decision, record_shadow_decision
            from .memory import open_store

            _existing_database(args.db)
            value = _request_object(_read_input(args.input),
                                    ("pack", "action", "evidence_ids", "rationale", "valid_until"))
            decision = make_shadow_decision(**value)
            with open_store(args.db, readonly=True):
                pass
            with open_store(args.db) as store:
                _emit(record_shadow_decision(store, value["pack"], decision))
            return 0

        from .memory import open_store

        if args.command == "init":
            with open_store(args.db) as store:
                _emit(store.snapshot())
        elif args.command == "import-legacy":
            from .legacy import ingest_legacy

            if not Path(args.reports_dir).is_dir():
                raise NotADirectoryError("旧记录目录不存在或不是目录")
            with open_store(args.db) as store:
                result = ingest_legacy(store, args.reports_dir)
                _emit(result)
                if result.get("errors", 0):
                    return 2
        elif args.command == "snapshot":
            _existing_database(args.db)
            with open_store(args.db, readonly=True) as store:
                _emit(store.snapshot(as_of=args.as_of))
        elif args.command == "recall":
            _existing_database(args.db)
            if args.limit <= 0:
                raise ValueError("limit 必须为正整数")
            query = {"environment": args.environment}
            if args.code:
                query["code"] = args.code
            if args.strategy_id:
                query["strategy_id"] = args.strategy_id
            with open_store(args.db, readonly=True) as store:
                _emit(store.recall(query, args.as_of, limit=args.limit))
        elif args.command == "export":
            _existing_database(args.db)
            with open_store(args.db) as store:
                _emit(store.export_pending(args.output_dir))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        _emit({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
