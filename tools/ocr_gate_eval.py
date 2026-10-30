#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR 门噪声评估采样器（P2-3，2026-10-30）。

用途：对最近 N 个「含代码改动」的提交跑 `ocr review --commit <sha> --format json`，
汇总 findings（severity/category）与 token 成本，产出可人工判噪的报告；供决定
OCR warn 门是否转强制提供数据（默认 dry-run，不消耗 token）。

用法：
  python tools/ocr_gate_eval.py                         # dry-run：列出将要跑的提交
  python tools/ocr_gate_eval.py --last 3 --run           # 实跑最近 3 个代码提交
  python tools/ocr_gate_eval.py --commits a,b --run
  python tools/ocr_gate_eval.py --run --json             # 机器可读
退出码：0 正常（含单条 OCR 失败）；2 ocr 未安装/未配置。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TMP = Path(__import__("tempfile").gettempdir())
CODE_SUFFIX = (".py", ".ps1", ".bat", ".cmd", ".js", ".ts", ".go", ".rs", ".sql")
EXCLUDE_PREFIX = ("agent_platform/data/", "quant_backtest/data/")


def _git(*args):
    p = subprocess.run(["git", "-C", str(REPO)] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip()


def recent_code_commits(limit=3):
    """从最近 200 个提交里取含代码文件改动的提交（按时间倒序）。"""
    rc, out = _git("log", "--name-only", "--format=%H|%s", "-200")
    if rc != 0:
        return []
    commits, cur = [], None
    for ln in out.splitlines():
        if "|" in ln and len(ln.split("|")[0]) >= 7 and " " not in ln.split("|")[0]:
            cur = {"sha": ln.split("|")[0], "subject": ln.split("|", 1)[1], "files": []}
            commits.append(cur)
        elif ln.strip() and cur is not None:
            cur["files"].append(ln.strip())
    picked = []
    for c in commits:
        files = c["files"]
        code = [f for f in files if f.endswith(CODE_SUFFIX)
                and not f.startswith(EXCLUDE_PREFIX)]
        if code:
            c["code_files"] = code
            picked.append(c)
        if len(picked) >= limit:
            break
    return picked


def _ocr_ready():
    exe = shutil.which("ocr")
    if not exe:
        return None, "ocr 未安装（npm i -g @alibaba-group/open-code-review）"
    cfg = Path.home() / ".opencodereview" / "config.json"
    if not cfg.exists():
        return exe, "ocr 配置文件缺失（%s）" % cfg
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except ValueError:
        return exe, "ocr 配置无法解析"
    prov = data.get("provider")
    provs = data.get("providers") or {}
    if not prov or not (provs.get(prov) or {}).get("api_key"):
        return exe, "provider=%s 未配置 api_key" % prov
    return exe, ""


def run_one(exe, sha, timeout_min=12):
    out = TMP / ("ocr_eval_%s.json" % sha[:12])
    t0 = time.time()
    try:
        r = subprocess.run([exe, "review", "--commit", sha, "--format", "json",
                            "--output", str(out), "--timeout", str(timeout_min)],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout_min * 60)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        return {"sha": sha, "ok": False, "error": "timeout", "elapsed_s": timeout_min * 60}
    res = {"sha": sha, "ok": rc == 0 and out.exists(), "rc": rc,
           "elapsed_s": round(time.time() - t0, 1)}
    if out.exists():
        try:
            d = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            d = {}
        comments = d.get("comments") or []
        res["summary"] = d.get("summary")
        res["comments"] = [{"path": c.get("path"), "severity": c.get("severity"),
                            "category": c.get("category"),
                            "content": (c.get("content") or "")[:400]}
                           for c in comments]
        sev = {}
        cat = {}
        for c in comments:
            sev[c.get("severity") or "?"] = sev.get(c.get("severity") or "?", 0) + 1
            cat[c.get("category") or "?"] = cat.get(c.get("category") or "?", 0) + 1
        res["by_severity"], res["by_category"] = sev, cat
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OCR 门噪声评估采样器")
    ap.add_argument("--last", type=int, default=3)
    ap.add_argument("--commits", default="")
    ap.add_argument("--run", action="store_true", help="实跑（消耗 token）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    exe, why = _ocr_ready()
    if args.commits:
        targets = [{"sha": s.strip(), "subject": "(manual)", "code_files": []}
                   for s in args.commits.split(",") if s.strip()]
    else:
        targets = recent_code_commits(args.last)
    payload = {"tool": "ocr_gate_eval", "ocr_ready": bool(exe) and not why,
               "ocr_note": why, "commits": [], "generated_at":
               time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not args.run:
        print("== DRY-RUN（加 --run 实跑，消耗 token）==")
        if why:
            print("ocr 状态：%s" % why)
        for c in targets:
            print("  %s  %s  files=%d" % (c["sha"][:12], c.get("subject", "")[:70],
                                          len(c.get("code_files", []))))
        return 0
    if why:
        print("OCR 不可用：%s" % why, file=sys.stderr)
        return 2
    results = []
    for c in targets:
        r = run_one(exe, c["sha"])
        r["subject"] = c.get("subject", "")
        results.append(r)
        print("[%s] rc=%s %ss severity=%s" % (r["sha"][:12], r.get("rc"),
                                              r.get("elapsed_s"), r.get("by_severity")))
    payload["commits"] = results
    sev_total = {}
    for r in results:
        for k, v in (r.get("by_severity") or {}).items():
            sev_total[k] = sev_total.get(k, 0) + v
    payload["severity_total"] = sev_total
    out = REPO / "agent_platform" / "data" / "reports" / "ocr_gate_eval"
    try:
        out.mkdir(parents=True, exist_ok=True)
        fp = out / ("eval_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
        fp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("report -> %s" % fp)
    except OSError as exc:
        print("落盘失败：%r" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
