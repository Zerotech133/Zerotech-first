"""仅使用本地存量样本核对结构工程，不推断历史可交易时间或收益。"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--operator-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    project = Path(args.project_root)
    sys.path.insert(0, str(project))
    sys.path.insert(0, str(Path(args.operator_root)))
    import pyarrow.parquet as pq
    from ai_operator.contracts import canonical_json, normalize_time, utc_now
    from ai_operator.replay import replay_bars
    from ai_operator.shadow import build_fact_pack, make_shadow_decision, record_shadow_decision
    from ai_operator.memory import open_store
    from chanlun_bi import analyze

    source = project / "data" / "ashare" / "000001_SH.parquet"
    artifacts = [source, *(project / name for name in
                          ("chanlun_bi.py", "chanlun_fractal.py", "chanlun_data.py"))]
    hashes_before = {str(path): file_hash(path) for path in artifacts}
    rows = pq.read_table(source).slice(0, 120).to_pylist()
    if len(rows) != 120:
        raise RuntimeError("本地存量样本不足 120 根")
    bars = []
    prior = None
    for row in rows:
        date = datetime.strptime(str(row["trade_date"]), "%Y%m%d").strftime("%Y-%m-%d")
        received = utc_now()
        while prior is not None and received <= prior:
            received = utc_now()
        bars.append({"dt": date, "o": float(row["open"]), "h": float(row["high"]),
                     "l": float(row["low"]), "c": float(row["close"]), "v": float(row["volume"]),
                     "close_at": normalize_time(date + "T15:00:00+08:00"), "available_at": received})
        prior = received
    started = time.perf_counter()
    replay = replay_bars(bars)
    elapsed = time.perf_counter() - started
    prefix = replay_bars(bars[:60])
    prefix_equal = canonical_json(replay["snapshots"][:60]) == canonical_json(prefix["snapshots"])
    direct_matches = all(canonical_json(snapshot["structures"]) ==
                         canonical_json(analyze(bars[:index + 1], closes=[bar["c"] for bar in bars[:index + 1]]))
                         for index, snapshot in enumerate(replay["snapshots"]))
    if not prefix_equal or not direct_matches:
        raise RuntimeError("回放前缀或直接算法结果不一致")

    current = utc_now()
    algorithm_hash = hashlib.sha256(canonical_json({str(path): hashes_before[str(path)]
                                                   for path in artifacts[1:]}).encode("utf-8")).hexdigest()
    pack = build_fact_pack("000001.SH", "1d", current,
                           [{"id": "structure-snapshot", "source": str(source),
                             "occurred_at": current, "available_at": current,
                             "data": {"structures": replay["snapshots"][-1]["structures"],
                                      "source_sha256": hashes_before[str(source)],
                                      "provenance_status": "unverified",
                                      "time_policy": "本次接收时刻；非供应商历史可用时间"}}],
                           {"data": hashes_before[str(source)], "algorithm": algorithm_hash,
                            "knowledge": "none-unverified", "rules": "none-unverified"})
    expiry = normalize_time(datetime.fromisoformat(current) + timedelta(hours=1))
    decision = make_shadow_decision(pack, "observe", ["structure-snapshot"],
                                    "核对本地存量样本的结构工程，仅登记观察", expiry)
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        with open_store(directory / "acceptance.sqlite3") as store:
            first = record_shadow_decision(store, pack, decision)
            second = record_shadow_decision(store, pack, decision)
            snapshot = store.snapshot()
            exported = store.export_pending(str(directory / "export"))
        exported_events = [json.loads(line) for line in (directory / "export" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        if first != second or snapshot["events"] != 2 or snapshot["lessons"] != 0 or len(exported_events) != 2:
            raise RuntimeError("影子审计的幂等或导出验收失败")
    hashes_after = {str(path): file_hash(path) for path in artifacts}
    if hashes_before != hashes_after:
        raise RuntimeError("验收来源在运行中发生变化")
    report = {"ok": True, "verified_at": utc_now(), "operator_root": args.operator_root,
              "sample": {"path": str(source), "rows_used": len(bars), "provenance_status": "unverified",
                         "close_time_policy": "CN 日线 15:00 推导，仅工程验收",
                         "availability_policy": "逐条实测本次接收时间，不补造历史时间"},
              "source_hashes": hashes_before, "source_hashes_unchanged": True,
              "replay": {"snapshots": len(replay["snapshots"]), "prefix_60_equal": prefix_equal,
                         "direct_matches": direct_matches, "elapsed_seconds": round(elapsed, 6),
                         "last_structure_counts": {kind: len(replay["snapshots"][-1]["structures"].get(kind, []))
                                                   for kind in ("fractals", "bis", "segments", "pivots", "signals")},
                         "added": sum(len(s["changes"]["added"]) for s in replay["snapshots"]),
                         "withdrawn": sum(len(s["changes"]["withdrawn"]) for s in replay["snapshots"])},
              "shadow": {"idempotent": first == second, "snapshot": snapshot, "export": exported,
                         "exported_events": len(exported_events), "execution_allowed": decision["execution_allowed"]},
              "historical_profitability_verified": False, "production_eligible": False}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
