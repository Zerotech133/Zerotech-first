# -*- coding: utf-8 -*-
"""扩展 manifest 校验器（O3）：只读校验 extensions/*/manifest.json。

用法：
  python -X utf8 tools/extension_lint.py
  python -X utf8 tools/extension_lint.py --json
  python -X utf8 tools/extension_lint.py --strict   # 有 error → rc=1
规则：docs/specs/2026-10-27_扩展契约_v1.md；schema：extensions/_schema/manifest.schema.json
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXT = os.path.join(REPO, "extensions")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
VER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
TOKEN_Q_RE = re.compile(r"(?i)(token|key|apikey|access_token|secret)=")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})")
TYPES = {"tool", "mcp", "skill", "filter"}
TRANSPORTS = {"stdio", "http", "cli", "inprocess"}
RISKS = {"read-only", "write"}
LIFECYCLE = {"active", "deprecated", "retired"}
REQUIRED = ("name", "version", "type", "owner", "description", "risk", "docs", "tests")


def _exists(rel):
    p = rel if os.path.isabs(rel) else os.path.join(REPO, rel)
    return os.path.exists(p)


def lint_one(path, data):
    """返回 (errors, warnings) 两个字符串列表。"""
    errors, warns = [], []
    if not isinstance(data, dict):
        return ["manifest 不是 JSON object"], warns
    for k in REQUIRED:
        if not data.get(k):
            errors.append("缺少必填字段: %s" % k)
    name = str(data.get("name") or "")
    if name and not NAME_RE.match(name):
        errors.append("name 需 kebab-case 且匹配 ^[a-z][a-z0-9-]{1,63}$: %r" % name)
    dirname = os.path.basename(os.path.dirname(path))
    if name and dirname and name != dirname:
        warns.append("name(%s) 与目录名(%s) 不一致" % (name, dirname))
    ver = str(data.get("version") or "")
    if ver and not VER_RE.match(ver):
        errors.append("version 需 x.y.z: %r" % ver)
    t = data.get("type")
    if t and t not in TYPES:
        errors.append("type 非法: %r" % t)
    owner = str(data.get("owner") or "").strip()
    if data.get("owner") is not None and not owner:
        errors.append("owner 为空")
    desc = str(data.get("description") or "")
    if desc and len(desc) < 8:
        errors.append("description 太短（<8）")
    risk = data.get("risk")
    if risk and risk not in RISKS:
        errors.append("risk 非法: %r" % risk)
    if risk == "write" and not data.get("l2_approved"):
        errors.append("risk=write 必须 l2_approved=true")
    transport = data.get("transport")
    if transport is not None and transport not in TRANSPORTS:
        errors.append("transport 非法: %r" % transport)
    if transport in ("stdio", "cli"):
        entry = str(data.get("entry") or "")
        if not entry:
            errors.append("transport=%s 需要 entry" % transport)
        elif not _exists(entry):
            errors.append("entry 路径不存在: %s" % entry)
    if transport == "http":
        url = str(data.get("url") or "")
        if not url:
            errors.append("transport=http 需要 url")
        else:
            if not url.lower().startswith("https://"):
                errors.append("http url 必须 https://")
            if TOKEN_Q_RE.search(url):
                errors.append("url 不得携带 token/key 查询参数")
        tok = str(data.get("token_env") or "")
        if not tok:
            errors.append("transport=http 需要 token_env（token 仅 env 注入）")
        elif not ENV_RE.match(tok):
            errors.append("token_env 需大写环境变量名: %r" % tok)
    docs = data.get("docs") or []
    for d in docs:
        if not _exists(str(d)):
            errors.append("docs 路径不存在: %s" % d)
    tests = data.get("tests") or []
    for tst in tests:
        if not _exists(str(tst)):
            errors.append("tests 路径不存在: %s" % tst)
    allow = data.get("allow_tools")
    if allow is not None and allow != "*" and not isinstance(allow, list):
        errors.append('allow_tools 需数组或 "*"')
    lc = data.get("lifecycle")
    if lc is not None and lc not in LIFECYCLE:
        errors.append("lifecycle 非法: %r" % lc)
    raw = json.dumps(data, ensure_ascii=False)
    if SECRET_RE.search(raw):
        errors.append("manifest 中出现明文 token 形态字符串")
    return errors, warns


def lint_dir(ext_dir=DEFAULT_EXT):
    results = {}
    names = {}
    paths = sorted(glob.glob(os.path.join(ext_dir, "*", "manifest.json")))
    for p in paths:
        rel = os.path.relpath(p, REPO)
        try:
            with io.open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            results[rel] = {"ok": False, "errors": ["JSON 解析失败: %s" % e], "warnings": []}
            continue
        errors, warns = lint_one(p, data)
        name = str((data or {}).get("name") or "")
        if name:
            names.setdefault(name, []).append(rel)
        results[rel] = {"ok": not errors, "errors": errors, "warnings": warns}
    for name, rs in names.items():
        if len(rs) > 1:
            for r in rs:
                results[r]["errors"].append("name 重复: %s (%s)" % (name, ", ".join(rs)))
                results[r]["ok"] = False
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="扩展 manifest 校验（只读）")
    ap.add_argument("--ext-dir", default=DEFAULT_EXT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有 error → rc=1")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.ext_dir):
        print("extensions 目录不存在：%s" % args.ext_dir, file=sys.stderr)
        return 2
    results = lint_dir(args.ext_dir)
    errors = sum(1 for r in results.values() if not r["ok"])
    warns = sum(len(r["warnings"]) for r in results.values())
    if args.json:
        print(json.dumps({"ok": errors == 0, "manifests": len(results),
                          "error_manifests": errors, "warnings": warns,
                          "results": results}, ensure_ascii=False, indent=2))
    else:
        print("extension lint: manifests=%d error_manifests=%d warnings=%d" % (
            len(results), errors, warns))
        for rel, r in sorted(results.items()):
            mark = "OK  " if r["ok"] else "FAIL"
            print("  [%s] %s" % (mark, rel))
            for e in r["errors"]:
                print("     - " + e)
            for w in r["warnings"]:
                print("     ~ " + w)
    return 0 if (errors == 0 or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
