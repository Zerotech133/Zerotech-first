"""R0-E：CN JSONL 批次的本地只读研究探针。

自声明 manifest 只能校验源字节的一致性，不能证明独立冻结身份；
转换只使用已哈希的内存快照。不注册策略、不调用聚合 run、
不连接外部服务；默认仅返回内存证据。
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .adapters.chan_monitor import ChanMonitorAdapter
from .aggregator import SignalAggregator, preaggregate_symbol
from .signal import Signal
from .xunyou import build_pool


_DATE = re.compile(r"[0-9]{8}\Z")
_CN = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)\Z")


def inspect_batch(
    trade_date: str, source_path: str | Path, manifest_path: str | Path, *, adapter=None
) -> dict:
    """读取显式批次；返回状态、各段计数及未验证段，不产生任何写入。"""
    if not isinstance(trade_date, str) or not _DATE.fullmatch(trade_date):
        raise ValueError("trade_date 必须为 YYYYMMDD")
    try:
        date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}")
    except ValueError as exc:
        raise ValueError("trade_date 不是有效日期") from exc
    source = Path(source_path).resolve()
    manifest = Path(manifest_path).resolve()
    result = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
        "cutover_verdict": "BLOCK_CUTOVER",
        "trade_date": trade_date,
        "status": "NOT_VERIFIED",
        "source": {
            "path": str(source),
            "manifest": str(manifest),
            "sha256": None,
            "business_date": None,
            "observed_date": None,
            "identity_status": "UNVERIFIED",
            "identity_reason": "manifest 为调用方自声明，缺少独立可信冻结源及接收凭证",
        },
        "counts": {
            "source_events": None,
            "signals": None,
            "kept": None,
            "combined": None,
            "pool": None,
        },
        "chain_status": "NOT_VERIFIED",
        "failures": [],
        "pool_status": "NOT_VERIFIED",
        "brief_status": "NOT_VERIFIED",
        "receipt_status": "NOT_VERIFIED",
        "real_source_verdict": "BLOCKED_REAL_DATA",
    }

    def fail(status: str, reason: str, strategy: str | None = None) -> dict:
        result["status"] = status
        result["failures"].append(
            {
                "stage": "adapter" if strategy else "source",
                "strategy": strategy,
                "reason": reason,
            }
        )
        return result

    if not source.is_file() or not manifest.is_file():
        return fail("MISSING_SOURCE", "源文件或批次 manifest 不存在")
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("manifest 必须是对象")
        if Path(doc["source_path"]).resolve() != source:
            raise ValueError("manifest.source_path 与显式源路径不一致")
        business = doc["business_date"]
        observed = doc["observed_date"]
        digest = doc["source_sha256"]
        count = doc["event_count"]
        if (
            not isinstance(business, str)
            or not _DATE.fullmatch(business)
            or not isinstance(observed, str)
            or date.fromisoformat(observed).isoformat() != observed
            or observed.replace("-", "") < business
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or type(count) is not int
            or count < 0
        ):
            raise ValueError("批次身份字段无效")
        result["source"].update(business_date=business, observed_date=observed)
        if business != trade_date:
            return fail("WRONG_DAY", "manifest.business_date 与请求业务日不一致")
        raw = source.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        result["source"]["sha256"] = actual
        if actual != digest:
            return fail("INVALID_SOURCE", "源内容 SHA256 与 manifest 不一致")
        rows = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
        if len(rows) != count:
            return fail("INVALID_SOURCE", "源事件数与 manifest.event_count 不一致")
        result["counts"]["source_events"] = count
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("源事件必须为对象")
            if (
                row.get("detect_date")
                != f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
            ):
                return fail("WRONG_DAY", "源事件 detect_date 与业务日不一致")
            if not isinstance(row.get("code"), str) or not _CN.fullmatch(row["code"]):
                raise ValueError("批次只接受标准 CN 代码")
            if row.get("type") not in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
                raise ValueError("批次包含未支持的信号类型")
            if (
                not isinstance(row.get("dt"), str)
                or date.fromisoformat(row["dt"]).isoformat() != row["dt"]
            ):
                raise ValueError("源事件 dt 必须是有效的 YYYY-MM-DD")
            if row["dt"] > row["detect_date"]:
                raise ValueError("源事件 dt 晚于扫描业务日")
            if not isinstance(row.get("price"), (int, float)) or isinstance(
                row["price"], bool
            ):
                raise ValueError("源事件缺少有效价格")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        return fail("INVALID_SOURCE", f"{type(exc).__name__}: {exc}")

    if count == 0:
        result["counts"].update(signals=0, kept=0, combined=0, pool=0)
        result.update(status="UNVERIFIED", chain_status="SELF_DECLARED_EMPTY")
        return result

    # score 不再读取可变路径：转换与哈希校验共用 raw 解出的行。
    class SnapshotAdapter(ChanMonitorAdapter):
        def _load_rows(self):
            return rows

    worker = (
        adapter if adapter is not None else SnapshotAdapter(pool_globs=[str(source)])
    )
    sid = str(getattr(worker, "strategy_id", "") or "unknown")
    try:
        if adapter is not None:
            # 注入 adapter 仅校验故障/漏行；其输出不作为评分依据。
            observed_signals = worker.score(trade_date)
            if not isinstance(observed_signals, list) or len(observed_signals) != count:
                raise ValueError(
                    "adapter 信号数与源事件数不一致（可能静默降级/部分失败）"
                )
            if any(
                not isinstance(s, Signal)
                or s.strategy != sid
                or s.trade_date != trade_date
                or not _CN.fullmatch(s.symbol)
                for s in observed_signals
            ):
                raise ValueError("adapter 输出不符合 CN Signal 契约")
            if Counter((s.symbol, s.dt) for s in observed_signals) != Counter(
                (r["code"], r["dt"]) for r in rows
            ):
                raise ValueError("adapter 信号身份与源事件不一致")
            if sid != ChanMonitorAdapter.strategy_id:
                raise ValueError("只支持 chan_monitor 快照转换")
        signals = SnapshotAdapter(pool_globs=[str(source)]).score(trade_date)
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            return fail("INVALID_SOURCE", "adapter 执行期间源文件已变化")
        if not isinstance(signals, list) or len(signals) != count:
            raise ValueError("快照信号数与源事件数不一致（可能静默降级/部分失败）")
        if any(
            not isinstance(s, Signal)
            or s.strategy != ChanMonitorAdapter.strategy_id
            or s.trade_date != trade_date
            or not _CN.fullmatch(s.symbol)
            for s in signals
        ):
            raise ValueError("快照输出不符合 CN Signal 契约")
        if Counter((s.symbol, s.dt) for s in signals) != Counter(
            (r["code"], r["dt"]) for r in rows
        ):
            raise ValueError("快照信号身份与源事件不一致")
        result["counts"]["signals"] = len(signals)
        grouped = {}
        for sig in signals:
            grouped.setdefault(sig.symbol, []).append((sid, 1.0, sig))
        combined = []
        divergences = []
        kept_total = 0
        fuser = SignalAggregator()
        for symbol in sorted(grouped):
            kept, superseded = preaggregate_symbol(grouped[symbol])
            kept_total += len(kept)
            item = fuser._fuse_symbol(trade_date, symbol, kept, superseded=superseded)
            combined.append(item)
            if item["divergence"]:
                divergences.append({"symbol": symbol, "trade_date": trade_date})
        pool = build_pool(trade_date, combined, divergences)
    except Exception as exc:
        return fail("ADAPTER_ERROR", f"{type(exc).__name__}: {exc}", sid)

    result["counts"].update(
        kept=kept_total, combined=len(combined), pool=pool["stats"]["buy_total"]
    )
    result.update(
        status="UNVERIFIED",
        chain_status="VERIFIED_IN_MEMORY",
        pool_status="IN_MEMORY_ONLY",
        combined=combined,
        pool=pool,
        brief={"status": "NOT_VERIFIED", "reason": "未执行真实简报或上行接收"},
    )
    return result


def write_research_evidence(result: dict, target: str | Path) -> Path:
    """仅显式已有 research 目录，独占创建 JSON；绝不覆盖或创建目录。"""
    path = Path(target)
    if (
        not path.is_absolute()
        or path.suffix.lower() != ".json"
        or path.parent.name.lower() != "research"
        or {part.lower() for part in path.parts}
        & {"production", "picks", "reports", "data"}
        or not path.parent.is_dir()
        or path.parent.resolve() != path.parent
    ):
        raise ValueError("输出须为显式绝对 research 目录下的 JSON 路径（目录须已存在）")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R0-E 本地冻结批次只读探测")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--research-output", help="显式指定已存在 research 目录中的新 JSON 路径"
    )
    args = parser.parse_args(argv)
    result = inspect_batch(args.trade_date, args.source, args.manifest)
    if args.research_output:
        write_research_evidence(result, args.research_output)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1  # 当前仅自声明 manifest，绝不以退出码 0 暗示冻结批次已核验


if __name__ == "__main__":
    raise SystemExit(main())
