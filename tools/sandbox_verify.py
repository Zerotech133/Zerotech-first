# -*- coding: utf-8 -*-
"""sandbox-verification：临时 git worktree + 可选 venv + pytest 证据包。

用法：
  python -X utf8 tools/sandbox_verify.py run --tests tools/test_codebase_intel.py [--venv] [--keep] [--json]
  python -X utf8 tools/sandbox_verify.py evidence --last
证据落 `work/sandbox/evidence-<ts>.json`；worktree 默认跑完删除。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EVID_DIR = REPO / "work" / "sandbox"


def _run(cmd, cwd, timeout):
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        rc, out, err = cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = -9, (e.stdout or ""), (e.stderr or "") + "\nTIMEOUT"
    return rc, out, err, round(time.time() - t0, 2)


def _find_requirements(worktree: Path) -> str:
    for name in ("requirements.txt", "requirements-dev.txt"):
        f = worktree / name
        if f.exists():
            return name
    return ""


def run(tests: list, repo: Path | None = None, venv: bool = False,
        keep: bool = False, timeout: int = 600, install: bool | None = None,
        requirements: str = "") -> dict:
    repo = Path(repo) if repo else REPO
    tests = [str(t) for t in tests]
    if not tests:
        raise ValueError("必须提供 --tests（至少一个测试路径）")
    EVID_DIR.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix="sandbox-", dir=str(EVID_DIR)))
    worktree = sandbox / "wt"
    evidence = {"schema": "sandbox_verify/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "repo": str(repo), "tests": tests, "venv": venv,
                "worktree": str(worktree), "steps": []}
    try:
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout.strip()
        evidence["git_sha"] = sha
        cp = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach",
                             str(worktree), "HEAD"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        evidence["steps"].append({"step": "worktree_add", "rc": cp.returncode,
                                  "stderr": cp.stderr.strip()[:400]})
        if cp.returncode != 0:
            raise RuntimeError("worktree add 失败: " + cp.stderr.strip()[:200])
        python = sys.executable
        if venv:
            vdir = sandbox / "venv"
            rc, out, err, secs = _run([sys.executable, "-m", "venv", "--system-site-packages",
                                       str(vdir)], sandbox, 180)
            evidence["steps"].append({"step": "venv", "rc": rc, "secs": secs,
                                      "stderr": err.strip()[:400]})
            if rc == 0:
                python = str(vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
                req = requirements or _find_requirements(worktree)
                if install is not False and req:
                    rc2, out2, err2, secs2 = _run(
                        [python, "-m", "pip", "install", "--disable-pip-version-check",
                         "-r", req], worktree, 900)
                    evidence["steps"].append({"step": "pip_install", "requirements": req,
                                              "rc": rc2, "secs": secs2,
                                              "stdout_tail": out2[-1000:],
                                              "stderr_tail": err2[-1000:]})
                    if rc2 != 0:
                        evidence["ok"] = False
                        evidence["error"] = "pip install failed rc=%s" % rc2
                        return evidence
                elif req:
                    evidence["steps"].append({"step": "pip_install", "skipped": True,
                                              "requirements": req})
        rc, out, err, secs = _run([python, "-m", "pytest", "-q"] + tests, worktree, timeout)
        evidence["pytest"] = {"rc": rc, "secs": secs,
                              "stdout_tail": out[-4000:], "stderr_tail": err[-2000:]}
        evidence["ok"] = rc == 0
        return evidence
    except Exception as e:  # noqa: BLE001
        evidence["ok"] = False
        evidence["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        return evidence
    finally:
        if not keep:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force",
                            str(worktree)], capture_output=True, text=True)
            shutil.rmtree(str(sandbox), ignore_errors=True)
            evidence["cleaned"] = True
        else:
            evidence["kept"] = str(sandbox)
        EVID_DIR.mkdir(parents=True, exist_ok=True)
        f = EVID_DIR / ("evidence-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        f.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
        evidence["evidence_path"] = str(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 sandbox-verification：临时 worktree + pytest 证据包")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tests", nargs="+", required=True)
    r.add_argument("--repo", default="")
    r.add_argument("--venv", action="store_true")
    r.add_argument("--keep", action="store_true")
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--install", action="store_true", help="venv 中安装 requirements")
    r.add_argument("--no-install", action="store_true", help="venv 不自动安装")
    r.add_argument("--requirements", default="", help="依赖文件（默认 requirements.txt）")
    r.add_argument("--json", action="store_true")
    e = sub.add_parser("evidence")
    e.add_argument("--last", action="store_true")
    e.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        install = True if args.install else (False if args.no_install else None)
        ev = run(args.tests, Path(args.repo) if args.repo else None,
                 args.venv, args.keep, args.timeout, install, args.requirements)
        if args.json:
            print(json.dumps(ev, ensure_ascii=False, indent=1))
        else:
            print("ok=%s rc=%s" % (ev.get("ok"), (ev.get("pytest") or {}).get("rc")))
            print("evidence", ev.get("evidence_path"))
        return 0 if ev.get("ok") else 1
    files = sorted(EVID_DIR.glob("evidence-*.json"))
    if not files:
        print("no evidence")
        return 1
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else
          "last %s ok=%s" % (files[-1].name, data.get("ok")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
