# -*- coding: utf-8 -*-
"""语法门：一方文件编译检查（SyntaxError 阻断；SyntaxWarning 可按门禁策略阻断）。

范围：`git ls-files '*.py'`（仅追踪文件）减去 vendored/外部目录（Hermes/ 等）。
缓存：mtime+size 命中则复用（重复门禁 <2s）。
用法：
  python tools/check_syntax.py                # 报告，warning 不失败
  python tools/check_syntax.py --fail-on-warning   # gate 模式（warning 也失败）
退出码：0 通过 / 1 有错 / 2 环境异常。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CACHE = Path(r"C:\项目\work\.cache\syntax_cache.json")
VENDOR_PREFIXES = ("Hermes/", "CowAgent/", "deliverables/", "third_party/", "vendor/")
PY_VERSION = "%d.%d" % sys.version_info[:2]


def tracked_files(base: Path | None = None) -> list[str]:
    base = Path(base) if base is not None else REPO
    try:
        out = subprocess.run(["git", "-C", str(base), "ls-files", "*.py"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=120).stdout
    except Exception:  # noqa: BLE001
        return []
    files = []
    for rel in out.splitlines():
        rel = rel.replace("\\", "/")
        if not rel or any(rel.startswith(p) for p in VENDOR_PREFIXES):
            continue
        files.append(rel)
    return files


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def check(files: list[str], fail_on_warning: bool = False, base: Path | None = None) -> dict:
    base = Path(base) if base is not None else REPO
    cache = _load_cache()
    new_cache = {}
    bad, warned, skipped = [], [], []
    t0 = time.time()
    for rel in files:
        p = base / rel
        try:
            st = p.stat()
        except OSError:
            # 文件在清单中但工作树缺失：多会话并发下的暂态（他人删除/合并中），跳过而非阻塞语法门
            skipped.append({"file": rel, "err": "missing_in_worktree"})
            continue
        key = "%s:%d" % (st.st_mtime_ns, st.st_size)
        prev = cache.get(rel)
        if isinstance(prev, dict) and prev.get("key") == key and prev.get("v") == PY_VERSION:
            if prev.get("bad"):
                bad.append({"file": rel, "err": prev.get("bad")})
            elif prev.get("warn"):
                warned.append({"file": rel, "warn": prev.get("warn")})
            new_cache[rel] = prev
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            with warnings.catch_warnings(record=True) as rec:
                warnings.simplefilter("always")
                compile(src, rel, "exec")
            ws = [str(w.message) for w in rec
              if "SyntaxWarning" in str(w.category)
              or "invalid escape sequence" in str(w.message)]
            entry = {"key": key, "v": PY_VERSION}
            if ws:
                entry["warn"] = ws[0]
                warned.append({"file": rel, "warn": ws[0]})
            new_cache[rel] = entry
        except SyntaxError as e:
            msg = "line %s: %s" % (e.lineno, e.msg)
            new_cache[rel] = {"key": key, "v": PY_VERSION, "bad": msg}
            bad.append({"file": rel, "err": msg})
        except Exception as e:  # noqa: BLE001
            msg = type(e).__name__ + ": " + str(e)[:100]
            new_cache[rel] = {"key": key, "v": PY_VERSION, "bad": msg}
            bad.append({"file": rel, "err": msg})
    _save_cache(new_cache)
    code = 0
    if bad or (fail_on_warning and warned):
        code = 1
    return {"tool": "check_syntax", "code": code, "scanned": len(files),
            "errors": bad[:30], "warnings": warned[:30], "skipped": skipped[:30],
            "error_count": len(bad), "warning_count": len(warned), "skipped_count": len(skipped),
            "elapsed_s": round(time.time() - t0, 2)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一方文件语法门")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-warning", action="store_true")
    ap.add_argument("--files", default="", help="逗号分隔的相对路径（调试用）")
    ap.add_argument("--repo", default="", help="仓库/工作树根（默认本工具所在仓）")
    args = ap.parse_args(argv)
    base = Path(args.repo) if args.repo else REPO
    files = [x for x in args.files.split(",") if x] or tracked_files(base)
    if not files:
        print("no files (git ls-files failed?)", file=sys.stderr)
        return 2
    res = check(files, args.fail_on_warning, base)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("语法门：%d 文件 / 错误 %d / 告警 %d / %.2fs" % (
            res["scanned"], res["error_count"], res["warning_count"], res["elapsed_s"]))
        for b in res["errors"][:10]:
            print("  ERR ", b["file"], b["err"])
        for w in res["warnings"][:10]:
            print("  WARN", w["file"], w["warn"])
        print("result:", "PASS" if res["code"] == 0 else "FAIL")
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
