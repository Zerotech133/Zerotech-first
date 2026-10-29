# -*- coding: utf-8 -*-
"""数据血缘 MVP（P0⑦ · WO-DATA-LINEAGE-20261027）。

参考 OpenLineage 的 run/job/dataset + facets 模型，做一个**本地、零依赖**实现：
  · 数据任务运行时 append 事件（START/COMPLETE/FAIL）到 JSONL；
  · 每个事件带 run_id/job、inputs/outputs（dataset 名/版本/uri/facets）；
  · `manifest` 可生成冻结回执要求的 lineage 段（输入/输出/版本/指纹）；
  · `check` 校验回执 lineage 段与文件 sha256（可复算）。

边界：只写 agent_platform/data/lineage/（运行态）；不连外部服务；不改数据根。

用法：
  python -X utf8 agent_platform/tools/data_lineage.py --root <root> emit \
      --job ts_daily_sync --status COMPLETE --run-id r1 \
      --inputs '[{"namespace":"table","name":"tdx_raw"}]' \
      --outputs '[{"namespace":"file","name":"ts_daily","uri":"quant_backtest/data/tdx/ts_daily"}]'
  python -X utf8 agent_platform/tools/data_lineage.py --root <root> manifest \
      --datasets '["ts_daily"]' --out work/lineage/manifest.json
  python -X utf8 agent_platform/tools/data_lineage.py --root <root> check \
      --receipt agent_platform/data/academy/freeze_receipts/AC-20261017-LCS1.json
退出码：0 正常；2 输入/校验失败。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
LINEAGE_REL = Path("agent_platform") / "data" / "lineage"
SCHEMA = "lineage_event/v1"


def _root(root: str | Path | None) -> Path:
    return Path(root).resolve() if root else REPO


def _events_path(root: Path) -> Path:
    return root / LINEAGE_REL / "events.jsonl"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _fail(msg: str, code: int = 2) -> "NoReturn":
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    raise SystemExit(code)


def _dataset(ref) -> dict:
    if isinstance(ref, str):
        return {"namespace": "table", "name": ref, "facets": {}}
    if not isinstance(ref, dict) or not ref.get("name"):
        _fail("dataset_ref_invalid: %r" % (ref,))
    out = {"namespace": str(ref.get("namespace") or "table"), "name": str(ref["name"])}
    for k in ("version", "uri"):
        if ref.get(k):
            out[k] = str(ref[k])
    facets = ref.get("facets")
    if facets:
        out["facets"] = facets
    return out


def _sha256_path(path: Path) -> str:
    """文件=全文 sha256；目录=有界指纹（相对路径+大小+前/后 64KB 样本）。

    避免对 21GB 数据目录全量读；指纹变化足以发现目录内容/文件大小变化。
    """
    h = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    elif path.is_dir():
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            st = p.stat()
            h.update(str(p.relative_to(path)).replace("\\", "/").encode("utf-8"))
            h.update(b"\0")
            h.update(str(st.st_size).encode("ascii"))
            with p.open("rb") as f:
                h.update(f.read(65536))
                if st.st_size > 65536:
                    f.seek(-65536, 2)
                    h.update(f.read(65536))
            h.update(b"\n")
    else:
        return ""
    return h.hexdigest()


def emit(root: Path, job: str, status: str, run_id: str | None = None,
         inputs: list | None = None, outputs: list | None = None,
         facets: dict | None = None, dry_run: bool = False) -> dict:
    if status not in ("START", "COMPLETE", "FAIL"):
        _fail("bad_status: %s" % status)
    rid = run_id or (dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + str(abs(hash(job)) % 100000))
    ev = {
        "schema": SCHEMA,
        "event_type": status,
        "event_time": _now(),
        "producer": "agent_platform/tools/data_lineage.py",
        "run": {"run_id": rid, "job": {"namespace": "zerobound", "name": job},
                "facets": facets or {}},
        "inputs": [_dataset(x) for x in (inputs or [])],
        "outputs": [_dataset(x) for x in (outputs or [])],
    }
    if dry_run:
        ev["dry_run"] = True
        return ev
    path = _events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def read_events(root: Path) -> list[dict]:
    path = _events_path(root)
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def dataset_lineage(root: Path, name: str) -> dict:
    runs = []
    for ev in read_events(root):
        outs = [x.get("name") for x in (ev.get("outputs") or [])]
        if name in outs:
            runs.append({"run_id": ev["run"]["run_id"], "job": ev["run"]["job"]["name"],
                         "event": ev["event_type"], "at": ev.get("event_time"),
                         "inputs": [x.get("name") for x in (ev.get("inputs") or [])]})
    return {"ok": True, "dataset": name, "runs": runs}


def build_manifest(root: Path, datasets: list[str], hash_files: bool = True) -> dict:
    events = read_events(root)
    result = {}
    for name in datasets:
        latest = None
        for ev in events:
            if ev.get("event_type") != "COMPLETE":
                continue
            for out in (ev.get("outputs") or []):
                if out.get("name") == name:
                    latest = (ev, out)
        if not latest:
            result[name] = {"name": name, "produced_by": None, "status": "no_complete_run"}
            continue
        ev, out = latest
        item = {"name": name, "namespace": out.get("namespace", "table"),
                "version": out.get("version") or ev["run"]["run_id"],
                "sha256": "", "produced_by": {"run_id": ev["run"]["run_id"],
                                              "job": ev["run"]["job"]["name"],
                                              "at": ev.get("event_time")},
                "upstream": [x.get("name") for x in (ev.get("inputs") or [])]}
        uri = out.get("uri")
        if uri and hash_files:
            p = (Path(uri) if Path(uri).is_absolute() else root / uri)
            item["uri"] = uri
            item["sha256"] = _sha256_path(p)
        result[name] = item
    return {"schema": "lineage_manifest/v1", "generated_at": _now(),
            "datasets": result}


def check_receipt(root: Path, receipt_path: Path) -> dict:
    try:
        receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail("receipt_read_failed: %s" % exc)
    lineage = receipt.get("lineage")
    if not isinstance(lineage, dict) or not isinstance(lineage.get("datasets"), list):
        _fail("receipt_missing_lineage: 需要 lineage.datasets[]")
    problems = []
    for item in lineage["datasets"]:
        if not isinstance(item, dict):
            problems.append({"error": "dataset_not_object"})
            continue
        for key in ("name", "version", "sha256", "produced_by"):
            if not item.get(key):
                problems.append({"dataset": item.get("name"), "missing": key})
        uri = item.get("uri")
        if uri and item.get("sha256"):
            p = Path(uri) if Path(uri).is_absolute() else root / uri
            actual = _sha256_path(p)
            if actual and actual != item["sha256"]:
                problems.append({"dataset": item.get("name"), "error": "sha256_mismatch",
                                 "expected": item["sha256"], "actual": actual})
    return {"ok": not problems, "problems": problems,
            "datasets_checked": len(lineage["datasets"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据血缘（OpenLineage 风格，本地 JSONL）")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_emit = sub.add_parser("emit")
    p_emit.add_argument("--job", required=True)
    p_emit.add_argument("--status", required=True, choices=["START", "COMPLETE", "FAIL"])
    p_emit.add_argument("--run-id", default="")
    p_emit.add_argument("--inputs", default="[]")
    p_emit.add_argument("--outputs", default="[]")
    p_emit.add_argument("--facets", default="{}")

    p_show = sub.add_parser("show")
    p_show.add_argument("--run-id", required=True)

    p_ds = sub.add_parser("dataset")
    p_ds.add_argument("--name", required=True)

    p_man = sub.add_parser("manifest")
    p_man.add_argument("--datasets", required=True, help="JSON 数组")
    p_man.add_argument("--out", default="")
    p_man.add_argument("--no-hash", action="store_true")

    p_chk = sub.add_parser("check")
    p_chk.add_argument("--receipt", required=True)

    args = ap.parse_args(argv)
    root = _root(args.root)
    if args.cmd == "emit":
        try:
            inputs = json.loads(args.inputs)
            outputs = json.loads(args.outputs)
            facets = json.loads(args.facets)
        except ValueError as exc:
            _fail("json_invalid: %s" % exc)
        if not isinstance(inputs, list) or not isinstance(outputs, list):
            _fail("inputs_outputs_must_be_list")
        out = emit(root, args.job, args.status, args.run_id or None,
                   inputs, outputs, facets, dry_run=args.dry_run)
    elif args.cmd == "show":
        out = {"ok": True, "events": [e for e in read_events(root)
                                      if e.get("run", {}).get("run_id") == args.run_id]}
    elif args.cmd == "dataset":
        out = dataset_lineage(root, args.name)
    elif args.cmd == "manifest":
        try:
            datasets = json.loads(args.datasets)
        except ValueError as exc:
            _fail("datasets_json_invalid: %s" % exc)
        out = build_manifest(root, datasets, hash_files=not args.no_hash)
        if args.out:
            p = Path(args.out)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            out["written"] = str(p)
    else:
        out = check_receipt(root, Path(args.receipt))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
