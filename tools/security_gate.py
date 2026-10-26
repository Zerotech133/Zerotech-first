# -*- coding: utf-8 -*-
"""security-gate：密钥/AST 危险模式扫描 + 证据落盘（只读）。

用法：
  python -X utf8 tools/security_gate.py scan --changed
  python -X utf8 tools/security_gate.py scan --path agent_platform tools
  python -X utf8 tools/security_gate.py scan --all [--json]
证据：work/security/security-<ts>.json；有 high 退出码 1。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EVID = REPO / "work" / "security"
EXCLUDE = {".git", ".worktrees", "node_modules", "__pycache__", ".venv", "venv",
           "dist", "build", ".pytest_cache", "work", "quant_backtest"}
SECRETS = [
    ("aws_ak", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("private_key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}")),
]
DANGER_CALLS = {"eval": "high", "exec": "high", "os.system": "high"}
DANGER_KW = {"shell": "high", "verify": "medium"}


def _iter(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if any(x in EXCLUDE for x in rel.parts):
            continue
        if p.suffix.lower() not in (".py", ".js", ".ts", ".json", ".yaml", ".yml", ".env", ".ps1", ".bat"):
            continue
        try:
            if p.stat().st_size > 400_000:
                continue
        except OSError:
            continue
        yield p, rel.as_posix()


def _scan_secrets(text: str, rel: str):
    out = []
    for name, rx in SECRETS:
        for m in rx.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append({"file": rel, "line": line, "rule": name, "severity": "high",
                        "detail": "疑似硬编码密钥（已脱敏）"})
    return out


def _scan_ast(text: str, rel: str):
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Name):
                name = fn.id
            elif isinstance(fn, ast.Attribute):
                name = "%s.%s" % (getattr(fn.value, "id", ""), fn.attr)
            for k, sev in DANGER_CALLS.items():
                if name == k or name.endswith("." + k):
                    out.append({"file": rel, "line": getattr(node, "lineno", 0), "rule": "danger_call:" + k,
                                "severity": sev, "detail": "危险调用，需评审/隔离"})
            for kw in node.keywords:
                if kw.arg in DANGER_KW and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    sev = "high" if kw.arg == "shell" else "medium"
                    out.append({"file": rel, "line": getattr(node, "lineno", 0),
                                "rule": "danger_kw:%s=True" % kw.arg, "severity": sev,
                                "detail": "危险参数，需评审"})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "loads" and getattr(node.func.value, "id", "") == "pickle":
                out.append({"file": rel, "line": getattr(node, "lineno", 0),
                            "rule": "pickle_loads", "severity": "high", "detail": "反序列化风险"})
    return out


def scan(paths=None, changed=False, all_files=False) -> dict:
    t0 = time.time()
    targets = []
    if changed:
        cp = subprocess.run(["git", "-C", str(REPO), "diff", "--name-only", "HEAD"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
        targets = [REPO / x for x in cp.stdout.splitlines() if (REPO / x).exists()]
    elif paths:
        targets = [Path(p) if Path(p).is_absolute() else REPO / p for p in paths]
    else:
        targets = None
    findings, file_count = [], 0
    if targets is None:
        it = _iter(REPO)
    else:
        def _gen():
            for t in targets:
                if t.is_dir():
                    yield from _iter(t)
                elif t.is_file():
                    yield t, t.relative_to(REPO).as_posix() if str(t).startswith(str(REPO)) else t.as_posix()
        it = _gen()
    for p, rel in it:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_count += 1
        findings.extend(_scan_secrets(text, rel))
        if p.suffix == ".py":
            findings.extend(_scan_ast(text, rel))
    high = [f for f in findings if f["severity"] == "high"]
    out = {"schema": "security_gate/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "files_scanned": file_count, "findings": findings,
           "high": len(high), "total": len(findings),
           "ok": len(high) == 0, "elapsed_ms": int((time.time() - t0) * 1000)}
    EVID.mkdir(parents=True, exist_ok=True)
    f = EVID / ("security-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["evidence_path"] = str(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 security-gate（只读扫描）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--changed", action="store_true")
    s.add_argument("--all", action="store_true")
    s.add_argument("--path", nargs="*", default=None)
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = scan(paths=args.path, changed=args.changed, all_files=args.all)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("files=%d findings=%d high=%d ok=%s" % (res["files_scanned"], res["total"],
                                                       res["high"], res["ok"]))
        for f in res["findings"][:30]:
            print("  [%s] %s:%s %s" % (f["severity"], f["file"], f["line"], f["rule"]))
        print("evidence", res["evidence_path"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
