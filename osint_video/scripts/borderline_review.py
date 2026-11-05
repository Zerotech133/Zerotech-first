"""临界带人工确认的命令行入口（配合 ``knowledge/borderline_review.py``）。

## 为什么要有这个 CLI

``StoryGrouper`` 在临界带会把 LLM 判同的配对落进待确认队列。队列是 JSONL
文件，直接 ``cat`` 能看，但看不清"这两条到底该不该合"——需要两侧标题、余弦、
LLM 理由排在一起。更重要的是：**看完必须能立刻表态**，否则人看完还得手工
编辑 JSON，多一步就少一半执行率。

所以本脚本只做三件事：看清单、批一条、驳一条。

## 用法

::

    python scripts/borderline_review.py list
    python scripts/borderline_review.py list --pending-only
    python scripts/borderline_review.py approve src:a src:b --note "同一份倡议的后续"
    python scripts/borderline_review.py reject  src:c src:d --note "主体相同但两件事"
    python scripts/borderline_review.py show    src:a src:b   # 看某一条的完整现场
    python scripts/borderline_review.py drop    src:a src:b   # 撤销人工裁决，交回机器
    python scripts/borderline_review.py compact               # 归档已裁决记录

``compact`` 建议每批裁决之后跑一次：不归档的话队列只增不减，``status`` 永远
停在 ``pending``，积压数会一路虚涨到没人再信那个告警。

裁决写进台账后，下一轮聚类即生效（``decided_by='manual'``）。要看到效果必须
重跑聚类：``python scripts/story_group_run.py --borderline-judge llm``。
## 为什么 approve/reject 不直接改库

改库意味着本脚本要与聚类算子共享写路径，两边对 ``osint_story_member`` 的
理解必须永远一致——这是最容易漂移的地方。台账是纯声明式的"人的意见"，由
算子在下一次运行时消费，职责边界干净，也让"这一版结果是人工干预过的"这件事
在 ``manual_overrides`` 计数里可查。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本在任意 cwd 下都能以包内绝对导入运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.logging_setup import setup_logging  # noqa: E402
from knowledge import borderline_review as br  # noqa: E402


def _fmt_decision(merge: bool) -> str:
    return "合并" if merge else "不合并"


def _print_pair(index: int, left: str, right: str, row: dict | None, merge: bool | None) -> None:
    """打印一条配对：编号、裁决状态、余弦、两侧标题、LLM 理由。"""
    status = "未裁决" if merge is None else _fmt_decision(merge)
    sim = row.get("similarity") if row else None
    gap = row.get("gap") if row else None
    head = f"[{index}] {status}"
    if sim is not None:
        head += f"  cos={sim:.4f}"
        if gap is not None:
            head += f"（差门槛 {gap:.4f}）"
    print(head)
    print(f"    {left}")
    print(f"    {right}")
    if row:
        print(f"    左：{row.get('left_title') or '（无标题）'}")
        print(f"    右：{row.get('right_title') or '（无标题）'}")
        reason = str(row.get("rationale") or "")
        if reason:
            print(f"    理由：{reason}")
        if row.get("queued_at"):
            print(f"    入队：{row['queued_at']}")
    print()


def _cmd_list(args: argparse.Namespace) -> int:
    overrides = br.load_overrides(args.overrides)
    pending = br.load_pending(args.queue)

    by_pair: dict[tuple[str, str], dict] = {}
    for row in pending:
        by_pair[br.parse_pair_id(br.pair_id(row.get("left"), row.get("right")))] = row

    rows = []
    for pair, row in by_pair.items():
        if args.pending_only and pair in overrides:
            continue
        rows.append((pair, row))
    # 台账里有、队列里没有的也要列：那是人工直接写进台账（未经 LLM 提升）的，
    # 恰恰最需要被复核有没有笔误。
    for pair in overrides:
        if pair not in by_pair and not args.pending_only:
            rows.append(
                (pair, {"rationale": "（人工直接写入台账，无队列记录）"})
            )

    rows.sort(key=lambda item: (item[0] not in overrides, item[0]))

    if not rows:
        print("暂无待确认项。")
        if args.pending_only:
            print("（--pending-only 只列未裁决项；去掉它可看到已裁决记录）")
        return 0

    print(
        f"共 {len(rows)} 对（台账已裁决 {len(overrides)} 条，队列累计 {len(pending)} 条）\n"
    )
    for index, (pair, row) in enumerate(rows, start=1):
        _print_pair(index, pair[0], pair[1], row, overrides.get(pair))
    print("表态：approve/reject <left> <right> --note \"...\"")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    pair = br.parse_pair_id(br.pair_id(args.left, args.right))
    overrides = br.load_overrides(args.overrides)
    row = None
    for item in br.load_pending(args.queue):
        if br.parse_pair_id(br.pair_id(item.get("left"), item.get("right"))) == pair:
            row = item
    if row is None and pair not in overrides:
        print("队列与台账里都没有这一对。", file=sys.stderr)
        return 1
    _print_pair(1, pair[0], pair[1], row, overrides.get(pair))
    if row:
        print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def _write(args: argparse.Namespace, merge: bool) -> int:
    path = br.record_override(
        args.left, args.right, merge=merge, note=args.note or "", path=args.overrides
    )
    pair = br.parse_pair_id(br.pair_id(args.left, args.right))
    print(f"已写入台账：{pair[0]} || {pair[1]} → {_fmt_decision(merge)}")
    print(f"台账文件：{path}")
    print("下一步：重跑聚类使裁决生效 —— python scripts/story_group_run.py --borderline-judge llm")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    return _write(args, True)


def _cmd_reject(args: argparse.Namespace) -> int:
    return _write(args, False)


def _cmd_drop(args: argparse.Namespace) -> int:
    path = br.resolve_path(args.overrides, br.DEFAULT_OVERRIDES_PATH)
    overrides = br.load_overrides(path)
    pair = br.parse_pair_id(br.pair_id(args.left, args.right))
    if pair not in overrides:
        print("台账里没有这一对，无需撤销。", file=sys.stderr)
        return 1
    overrides.pop(pair)
    br.save_overrides(overrides, path)
    print(f"已撤销：{pair[0]} || {pair[1]}（交回机器判定）")
    return 0


def _cmd_compact(args: argparse.Namespace) -> int:
    """把已裁决的队列行归档，让待办数回到真实值。"""
    overrides = br.load_overrides(args.overrides)
    moved, kept = br.prune_decided(args.queue, overrides=overrides)
    stats = br.queue_stats(args.queue, overrides=overrides)
    print(f"归档 {moved} 条已裁决记录，队列保留 {kept} 条（待办 {stats.pending} 条）")
    if moved:
        print(f"归档文件：{br.resolve_path(args.queue, br.DEFAULT_QUEUE_PATH)}.decided.jsonl")
        print("归档不是删除：LLM 当时的理由留在归档里，台账只存布尔。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="临界带人工确认：查看 LLM 提升的配对并给出裁决"
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help=f"台账路径，默认 {br.DEFAULT_OVERRIDES_PATH}",
    )
    parser.add_argument(
        "--queue",
        default=None,
        help=f"待确认队列路径，默认 {br.DEFAULT_QUEUE_PATH}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出队列与台账")
    p_list.add_argument(
        "--pending-only", action="store_true", help="只列尚未裁决的配对"
    )
    p_list.set_defaults(func=_cmd_list)

    for name, func, text in (
        ("approve", _cmd_approve, "裁定为同一事件（合并）"),
        ("reject", _cmd_reject, "裁定为不同事件（不合并）"),
        ("show", _cmd_show, "查看某一对的完整现场"),
        ("drop", _cmd_drop, "撤销人工裁决"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("left", help="左侧 unit_key")
        p.add_argument("right", help="右侧 unit_key")
        if name in ("approve", "reject"):
            p.add_argument("--note", default="", help="备注，写进台账供日后追溯")
        p.set_defaults(func=func)

    p_compact = sub.add_parser(
        "compact", help="归档已裁决的队列记录（队列因此有界，幂等）"
    )
    p_compact.set_defaults(func=_cmd_compact)

    args = parser.parse_args()
    setup_logging()
    try:
        return int(args.func(args))
    except br.OverrideError as exc:
        # 台账损坏时必须显式失败：静默当成空台账会让全部人工裁决凭空失效。
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
