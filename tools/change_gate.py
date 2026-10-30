#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评估闸门 v0：prompt/模型变更的可比较、可回滚基线。

不自己跑 LLM 评测；由 eval_harness/promptfoo/p0_eval_runner 产出分数后传入。
- snapshot：保存 prompt 快照 + 当前分数 + 模型，作为基线；
- check：新分数低于基线（超出容差）→ 退出码 1；
- rollback：把 prompt 恢复到基线快照，当前版本留 .bak；
- status：列出基线。

只写 work/eval_gate/，不写 prompt 源（rollback 时除外，需显式调用）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "work" / "eval_gate"
DEFAULT_PROMPT = ROOT / "agent_platform" / "evals" / "prompts" / "zhuge.md"
DEFAULT_MODEL = "ollama/qwen3.5:9b"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load(store_dir: Path) -> dict:
    p = store_dir / "baselines.json"
    if not p.exists():
        return {"schema": "eval_gate/v1", "baselines": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(name: str, prompt: Path, score: float, metric: str,
             model: str, note: str, store_dir: Path | None = None) -> dict:
    store = Path(store_dir) if store_dir else DEFAULT_DIR
    prompt = Path(prompt)
    text = prompt.read_text(encoding="utf-8")
    digest = _sha(text)
    snap = store / "prompts" / ("%s.%s.txt" % (name, digest[:12]))
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(text, encoding="utf-8")
    data = _load(store)
    data["baselines"][name] = {
        "name": name,
        "prompt": str(prompt),
        "prompt_sha256": digest,
        "snapshot": str(snap),
        "metric": metric,
        "score": float(score),
        "model": model,
        "note": note,
        "updated_at": _now(),
    }
    _atomic_json(store / "baselines.json", data)
    return data["baselines"][name]


def check(name: str, score: float, prompt: Path | None = None,
          tolerance: float = 0.0, store_dir: Path | None = None) -> dict:
    store = Path(store_dir) if store_dir else DEFAULT_DIR
    data = _load(store)
    base = (data.get("baselines") or {}).get(name)
    if not base:
        return {"ok": False, "error": "baseline_not_found", "name": name}
    delta = float(score) - float(base["score"])
    prompt_changed = None
    if prompt is not None:
        try:
            prompt_changed = _sha(Path(prompt).read_text(encoding="utf-8")) != base["prompt_sha256"]
        except OSError:
            prompt_changed = None
    ok = delta >= -abs(float(tolerance))
    return {
        "ok": ok,
        "name": name,
        "metric": base["metric"],
        "baseline": base["score"],
        "current": float(score),
        "delta": round(delta, 4),
        "tolerance": float(tolerance),
        "prompt_changed": prompt_changed,
        "model": base.get("model"),
        "baseline_at": base.get("updated_at"),
    }


def rollback(name: str, prompt: Path | None = None,
             store_dir: Path | None = None) -> dict:
    store = Path(store_dir) if store_dir else DEFAULT_DIR
    data = _load(store)
    base = (data.get("baselines") or {}).get(name)
    if not base:
        return {"ok": False, "error": "baseline_not_found", "name": name}
    target = Path(prompt) if prompt else Path(base["prompt"])
    snap = Path(base["snapshot"])
    if not snap.exists():
        return {"ok": False, "error": "snapshot_missing", "snapshot": str(snap)}
    if target.exists():
        bak = target.with_name(target.name + ".bak")
        shutil.copy2(target, bak)
    else:
        bak = None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snap, target)
    return {"ok": True, "name": name, "prompt": str(target),
            "backup": str(bak) if bak else None,
            "restored_sha256": base["prompt_sha256"]}


def status(store_dir: Path | None = None) -> dict:
    store = Path(store_dir) if store_dir else DEFAULT_DIR
    data = _load(store)
    return {"ok": True, "baselines": data.get("baselines") or {}}


def _score_from_json(path: Path) -> float | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    for key in ("score", "pass_rate", "rate", "overall_score", "average_score"):
        if isinstance(d.get(key), (int, float)):
            return float(d[key])
    ov = d.get("overall")
    if isinstance(ov, dict) and isinstance(ov.get("rate"), (int, float)):
        return float(ov["rate"])
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="评估闸门 v0（prompt/模型变更）")
    ap.add_argument("--store-dir", default=str(DEFAULT_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("snapshot")
    ps.add_argument("--name", required=True)
    ps.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    ps.add_argument("--score", type=float)
    ps.add_argument("--from-json")
    ps.add_argument("--metric", default="score")
    ps.add_argument("--model", default=DEFAULT_MODEL)
    ps.add_argument("--note", default="")
    pc = sub.add_parser("check")
    pc.add_argument("--name", required=True)
    pc.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    pc.add_argument("--score", type=float)
    pc.add_argument("--from-json")
    pc.add_argument("--tolerance", type=float, default=0.0)
    pc.add_argument("--json", action="store_true")
    pr = sub.add_parser("rollback")
    pr.add_argument("--name", required=True)
    pr.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    sub.add_parser("status")
    args = ap.parse_args(argv)
    store = Path(args.store_dir)

    if args.cmd == "snapshot":
        score = args.score if args.score is not None else (
            _score_from_json(Path(args.from_json)) if args.from_json else None)
        if score is None:
            print("score required (or valid --from-json)", file=sys.stderr)
            return 2
        out = snapshot(args.name, Path(args.prompt), score, args.metric,
                       args.model, args.note, store)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "check":
        score = args.score if args.score is not None else (
            _score_from_json(Path(args.from_json)) if args.from_json else None)
        if score is None:
            print("score required (or valid --from-json)", file=sys.stderr)
            return 2
        out = check(args.name, score, Path(args.prompt), args.tolerance, store)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    if args.cmd == "rollback":
        out = rollback(args.name, Path(args.prompt), store)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    print(json.dumps(status(store), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
