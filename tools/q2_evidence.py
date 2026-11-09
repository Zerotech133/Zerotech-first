#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六军团 Q2 证据包生成器（自动证据，不替代人工验收）。

安全边界：只读探针 + 检查测试文件存在；不执行验收、不签 owner。
用法：
  python -X utf8 tools/q2_evidence.py --write
  python -X utf8 tools/q2_evidence.py --json
"""
from __future__ import annotations

import argparse
import http.client
import json
import time
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "platform" / "q2" / "legions.yaml"
REG = ROOT / "compositions" / "registry.json"
OUT = ROOT / "docs" / "q2"


def _get(port: int, path: str, cookie: str = "", timeout: float = 5.0) -> tuple[int, float]:
    t0 = time.perf_counter()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Cookie": cookie} if cookie else {}
        c.request("GET", path, headers=headers)
        r = c.getresponse()
        r.read()
        return r.status, round((time.perf_counter() - t0) * 1000, 1)
    finally:
        c.close()


def build(cookie: str = "") -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8")) or {}
    legions = cfg.get("legions") or {}
    try:
        reg = json.loads(REG.read_text(encoding="utf-8-sig"))
        comps = {c["id"]: c for c in (reg.get("compositions") or []) if isinstance(c, dict)}
    except Exception:  # noqa: BLE001
        comps = {}
    rows = []
    for cid, meta in legions.items():
        probes = []
        for p in meta.get("probes") or []:
            want = p.get("want") or [200]
            try:
                code, ms = _get(int(p["port"]), str(p["path"]), cookie=cookie)
                ok = code in want
            except Exception as exc:  # noqa: BLE001
                code, ms, ok = None, None, False
                p = dict(p, error=str(exc))
            probes.append({"port": p.get("port"), "path": p.get("path"), "want": want,
                           "got": code, "ms": ms, "ok": ok})
        tests = []
        for t in meta.get("tests") or []:
            tp = ROOT / t
            tests.append({"path": t, "exists": tp.exists() or (tp.is_dir() if t.endswith("/") else False)})
        auto_ok = all(x["ok"] for x in probes) and all(x["exists"] for x in tests)
        rows.append({
            "id": cid, "owner": meta.get("owner"), "auto_ok": auto_ok,
            "probes": probes, "tests": tests,
            "manual_gate": meta.get("manual_gate"),
            "q2_status": "AUTO-EVIDENCE-COLLECTED（待 owner 验收/签署）" if auto_ok else "PARTIAL（自动证据有缺口）",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "manifest": comps.get(cid, {}).get("manifest"),
        })
    return {"schema": "q2/evidence-pack/v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "read_only": True, "legions": rows,
            "summary": {"total": len(rows), "auto_ready": sum(1 for r in rows if r["auto_ok"]),
                        "partial": sum(1 for r in rows if not r["auto_ok"])}}


def _md(r: dict) -> str:
    lines = [f"# {r['id']} · Q2 证据包", "",
             f"- owner：{r['owner']}｜自动状态：**{r['q2_status']}**",
             f"- 生成：{r['generated_at']}｜只读自动证据，**不替代验收**",
             "", "## 自动探针", "", "| 端口 | 路径 | 期望 | 实际 | ms | 结果 |", "|---:|---|---:|---:|---:|---|"]
    for p in r["probes"]:
        lines.append(f"| {p['port']} | {p['path']} | {p['want']} | {p.get('got')} | {p.get('ms')} | {'PASS' if p['ok'] else 'FAIL'} |")
    lines += ["", "## 测试文件", ""]
    for t in r["tests"]:
        lines.append(f"- `{t['path']}`：{'存在' if t['exists'] else '缺失'}")
    lines += ["", "## 人工门（未完成即不得 Q2 PASS）", "", f"- {r['manual_gate']}",
              "", "## 缺口 / 结论", "",
              f"- 自动证据：{'齐' if r['auto_ok'] else '有缺口'}；Q2 结论仍须 owner 验收签字。",
              "- 回滚预案/SLO 人工项：本证据包不代填。", "",
              "## 签署（待 owner）", "", "- owner：____｜日期：____｜结论：____"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Q2 证据包生成")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cookie", default="")
    args = ap.parse_args(argv)
    d = build(cookie=args.cookie)
    if args.write:
        OUT.mkdir(parents=True, exist_ok=True)
        for r in d["legions"]:
            (OUT / f"{r['id']}_Q2_evidence_v1.md").write_text(_md(r), encoding="utf-8")
        (OUT / "q2_evidence_matrix_v1.json").write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.json or not args.write:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(f"written {len(d['legions'])} packs -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
