"""只读 A4 探针与内存比较合约的命令行入口。"""

import argparse
import json
import sys

from . import a4_readonly_probe as probe
from .a4_comparison_contract import evaluate_comparison


def main(argv=None):
    parser = argparse.ArgumentParser(description="A4 只读观察比较（禁止切换）")
    parser.add_argument("--trade-date", required=True, metavar="YYYYMMDD")
    parser.add_argument("--history", metavar="PATH")
    parser.add_argument("--pool-path", action="append", metavar="PATH")
    args = parser.parse_args(argv)
    history = args.history if args.history is not None else str(
        probe.PORTAL_HISTORY / f"todays_{args.trade_date}.json"
    )
    try:
        observation = probe.inspect(args.trade_date, pool_paths=args.pool_path,
                                    history_path=history)
        comparison = evaluate_comparison(observation)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = {
        "trade_date": observation["trade_date"],
        "history_status": observation["history"]["status"],
        "cn_detect_date": observation["cn_detect_date"],
        "signals_a": observation["signals_a"],
        "unique_a": observation["unique_a"],
        "source_file_statuses": [
            {"path": item["path"], "status": item["status"]}
            for item in observation["source_files"]
        ],
        "input_manifest_sha256": observation["input_manifest"]["sha256"],
        "issues": observation["issues"],
        "comparison": comparison,
        "cutover_verdict": "BLOCK_CUTOVER",
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
