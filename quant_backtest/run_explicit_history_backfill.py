"""显式缺口回补入口。

导入本模块只提供计划函数，不会在导入阶段访问 TuShare；真正的公网请求
必须由命令行显式执行，并仍然只写入 staging。
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from history_backfill import pull_one


def build_explicit_retry_units(receipts):
    """从历史回执提取未完成单元，已完成单元不会重复请求。"""
    retry = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("complete") is True and receipt.get("status") == "candidate_ready":
            continue
        params = receipt.get("request_params") or {}
        trade_date = str(params.get("trade_date", ""))
        table = str(receipt.get("table", ""))
        if table and len(trade_date) == 8 and trade_date.isdigit():
            retry.append({"table": table, "trade_date": trade_date})
    return sorted(retry, key=lambda item: (item["table"], item["trade_date"]))


def main() -> None:
    stage = Path("quant_backtest/data/tushare_staging/explicit_gaps")
    items = [
        pull_one("ts_daily_basic", "daily_basic", "20260928", stage),
        pull_one("ts_margin_detail", "margin_detail", "20261008", stage),
    ]
    print(json.dumps([{key: item.get(key) for key in (
        "table", "api_name", "status", "complete", "row_count", "unique_keys",
        "duplicate_keys", "body_sha256", "staged_sha256", "reused")}
        for item in items], ensure_ascii=False))


if __name__ == "__main__":
    main()
