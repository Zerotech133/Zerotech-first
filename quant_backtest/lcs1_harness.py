#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LCS-1 最小回测 harness（A1/A2/A4 骨架，不做 A6 交易结论）。

范围（WO-LCS1-HARNESS-MIN-20261029）：
- A1 零写 dry-run：只输出 plan，不创建 out 目录；
- A2 可复现：同输入两次 fingerprint 一致；
- A4 manifest：输入数据 sha256 清单 + verify 可复算；
- 不做策略信号、不输出收益/回撤/Go-NoGo（A6 另行处理）。

用法：
  python -X utf8 quant_backtest/lcs1_harness.py plan [--config c.json] [--data-root PATH]
  python -X utf8 quant_backtest/lcs1_harness.py run  [--config c.json] --out work/lcs1_harness/x
  python -X utf8 quant_backtest/lcs1_harness.py verify --manifest work/lcs1_harness/x/manifest.json

默认 config 从 DATA1 冻结回执 + backtests 登记推导（只读）；也可传自定义 JSON。
退出码：0 通过 / 1 verify 失败 / 2 配置/输入错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = REPO / "agent_platform/data/academy/freeze_receipts/AC-20261017-LCS1.json"
DEFAULT_BACKTESTS = REPO / "agent_platform/data/academy/backtests.jsonl"
DEFAULT_DATA_ROOT = REPO / "quant_backtest/data/tdx"
DEFAULT_TABLES = ["ts_daily", "ts_adj_factor", "ts_daily_basic", "ts_stock_basic.parquet"]
REQUIRED = ("data_version", "sample_in", "sample_out", "cost_model", "strategy_spec")
SPEC_PATH = "deliverables/2026-10-27_STRAT1_LCS1策略Spec_draft_v0.1.md"


class HarnessError(Exception):
    pass


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError("read_failed: %s (%s)" % (path, exc))


def default_config() -> dict:
    receipt = _read_json(DEFAULT_RECEIPT)
    bt = None
    if DEFAULT_BACKTESTS.is_file():
        for line in DEFAULT_BACKTESTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cand = json.loads(line)
                if cand.get("topic_id") == "AC-20261017-LCS1":
                    bt = cand
                    break
    if not isinstance(bt, dict):
        raise HarnessError("backtests_missing: AC-20261017-LCS1")
    return {
        "topic_id": "AC-20261017-LCS1",
        "data_version": receipt.get("data_version") or bt.get("data_version"),
        "sample_in": receipt.get("sample_in") or bt.get("sample_in"),
        "sample_out": receipt.get("sample_out") or bt.get("sample_out"),
        "cost_model": receipt.get("cost_model") or bt.get("cost_model"),
        "strategy_spec": SPEC_PATH,
        "tables": list(DEFAULT_TABLES),
        "freeze_receipt": str(DEFAULT_RECEIPT.relative_to(REPO)).replace("\\", "/"),
        "sha256": receipt.get("sha256"),
    }


def load_config(path: str | None) -> dict:
    cfg = default_config() if not path else _read_json(Path(path))
    missing = [k for k in REQUIRED if not str(cfg.get(k) or "").strip()
               or str(cfg.get(k)).startswith("待")]
    if missing:
        raise HarnessError("config_missing_fields: %s" % missing)
    if not cfg.get("tables"):
        cfg["tables"] = list(DEFAULT_TABLES)
    return cfg


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_inputs(data_root: Path, tables: list[str]) -> list[dict]:
    entries = []
    for table in tables:
        p = data_root / table
        if p.is_dir():
            for f in sorted(p.glob("*.parquet")):
                entries.append({"path": str(f.relative_to(data_root)).replace("\\", "/"),
                                "sha256": _sha256_file(f), "size": f.stat().st_size})
        elif p.is_file():
            entries.append({"path": str(p.relative_to(data_root)).replace("\\", "/"),
                            "sha256": _sha256_file(p), "size": p.stat().st_size})
        else:
            raise HarnessError("missing_input_table: %s" % table)
    if not entries:
        raise HarnessError("no_input_files")
    return entries


def build_plan(cfg: dict, data_root: Path) -> dict:
    inputs = hash_inputs(data_root, list(cfg.get("tables") or DEFAULT_TABLES))
    digest = hashlib.sha256(json.dumps(
        {"config": cfg, "inputs": inputs}, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return {
        "schema": "lcs1_harness_plan/v1",
        "topic_id": cfg.get("topic_id") or "AC-20261017-LCS1",
        "data_root": str(data_root),
        "config": cfg,
        "input_files": len(inputs),
        "input_bytes": sum(x["size"] for x in inputs),
        "inputs": inputs,
        "fingerprint": digest,
        "note": "A1/A2/A4 skeleton; no strategy signal / no A6 result",
    }


def cmd_plan(cfg: dict, data_root: Path) -> int:
    plan = build_plan(cfg, data_root)
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    return 0


def cmd_run(cfg: dict, data_root: Path, out: Path) -> int:
    plan = build_plan(cfg, data_root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    manifest = {
        "schema": "lcs1_harness_manifest/v1",
        "topic_id": plan["topic_id"],
        "fingerprint": plan["fingerprint"],
        "config": cfg,
        "data_root": str(data_root),
        "inputs": plan["inputs"],
        "outputs": [{"path": "plan.json", "sha256": _sha256_file(out / "plan.json")}],
        "note": "A4 manifest; A6 pending",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "fingerprint": plan["fingerprint"],
                      "input_files": plan["input_files"]}, ensure_ascii=False))
    return 0


def cmd_verify(manifest_path: Path) -> int:
    m = _read_json(manifest_path)
    data_root = Path(m.get("data_root") or DEFAULT_DATA_ROOT)
    cfg = m.get("config") or {}
    plan = build_plan(cfg, data_root)
    problems = []
    if plan["fingerprint"] != m.get("fingerprint"):
        problems.append({"error": "fingerprint_mismatch",
                         "expected": m.get("fingerprint"), "actual": plan["fingerprint"]})
    for item in m.get("inputs") or []:
        p = data_root / item.get("path", "")
        if not p.is_file():
            problems.append({"path": item.get("path"), "error": "missing"})
        elif _sha256_file(p) != item.get("sha256"):
            problems.append({"path": item.get("path"), "error": "sha256_mismatch"})
    out = {"ok": not problems, "manifest": str(manifest_path),
           "inputs_checked": len(m.get("inputs") or []), "problems": problems}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LCS-1 最小 harness（A1/A2/A4）")
    ap.add_argument("cmd", choices=("plan", "run", "verify"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    ap.add_argument("--out", default=str(REPO / "work/lcs1_harness/run"))
    ap.add_argument("--manifest", default=None)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "verify":
            if not args.manifest:
                raise HarnessError("--manifest required for verify")
            return cmd_verify(Path(args.manifest))
        cfg = load_config(args.config)
        if args.cmd == "plan":
            return cmd_plan(cfg, Path(args.data_root))
        return cmd_run(cfg, Path(args.data_root), Path(args.out))
    except HarnessError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
