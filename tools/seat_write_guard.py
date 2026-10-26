# -*- coding: utf-8 -*-
"""seat_write_guard.py — 田丰 v3 L2 写路径白名单/执行确认（检测层）。

说明：本工具是**检测层**，不改变 Hermes 沙箱/工具权限；用于：
- 写前/审后自检：路径是否在田丰允许写范围（SOUL v3 + guard 配置）；
- 执行前自检：命令是否命中 L2 危险模式（命中=必须先报老田）。
配置：`agent_platform/registry/tianfeng_guard.json`。

用法：
  python -X utf8 tools/seat_write_guard.py --check deliverables/x.md work/y.json
  python -X utf8 tools/seat_write_guard.py --exec "taskkill /PID 1 /F"
  python -X utf8 tools/seat_write_guard.py --list --json
退出码：0 全部允许/不需确认；1 有拒绝；3 需要人工确认（exec）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _repo_paths import repo_root  # noqa: E402

REPO = repo_root(HERE)
HOME = Path.home() / ".hermes" / "profiles" / "tianfeng"
GUARD = REPO / "agent_platform" / "registry" / "tianfeng_guard.json"


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else GUARD
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _norm(path) -> str:
    p = Path(path)
    try:
        rp = p.resolve()
    except OSError:
        rp = p.absolute()
    for base, tag in ((REPO, "repo"), (HOME, "home")):
        try:
            return tag + "/" + rp.relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return rp.as_posix()


def check_path(path, cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    n = _norm(path)
    low = n.lower()
    for ex in cfg.get("allow_exceptions", []) or []:
        if n.startswith("repo/" + str(ex).strip("/") + "/"):
            return {"path": str(path), "normalized": n, "allowed": True,
                    "reason": "exception:%s" % ex}
    for sub in cfg.get("deny_substrings", []) or []:
        if str(sub).lower() in low:
            return {"path": str(path), "normalized": n, "allowed": False,
                    "reason": "deny_substring:%s" % sub}
    for pre in cfg.get("deny_repo_prefixes", []) or []:
        if n.startswith("repo/" + str(pre).strip("/") + "/"):
            return {"path": str(path), "normalized": n, "allowed": False,
                    "reason": "deny_prefix:%s" % pre}
    for pre in cfg.get("allow_repo_prefixes", []) or []:
        if n.startswith("repo/" + str(pre).strip("/") + "/") or n == "repo/" + str(pre).strip("/"):
            return {"path": str(path), "normalized": n, "allowed": True,
                    "reason": "allow_repo:%s" % pre}
    for pre in cfg.get("allow_home_prefixes", []) or []:
        if n.startswith("home/" + str(pre).strip("/") + "/"):
            return {"path": str(path), "normalized": n, "allowed": True,
                    "reason": "allow_home:%s" % pre}
    return {"path": str(path), "normalized": n, "allowed": False,
            "reason": "not_in_allowlist"}


def check_exec(cmd: str, cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load_config()
    low = str(cmd or "").lower()
    hits = [p for p in (cfg.get("exec_confirm_patterns") or []) if str(p).lower() in low]
    return {"command": cmd, "requires_confirm": bool(hits), "hits": hits}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 L2 写路径白名单/执行确认（检测层）")
    ap.add_argument("--check", nargs="*", default=[], help="检查文件/目录路径")
    ap.add_argument("--exec", default="", help="检查命令是否需要人工确认")
    ap.add_argument("--list", action="store_true", help="列出配置摘要")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--config", default="")
    args = ap.parse_args(argv)
    cfg = load_config(Path(args.config) if args.config else None)
    out = {"ok": True, "repo": str(REPO), "home": str(HOME),
           "config": str(Path(args.config) if args.config else GUARD),
           "checks": [], "exec": None}
    if args.list or (not args.check and not args.exec):
        out["summary"] = {
            "allow_repo_prefixes": cfg.get("allow_repo_prefixes", []),
            "allow_home_prefixes": cfg.get("allow_home_prefixes", []),
            "deny_repo_prefixes": cfg.get("deny_repo_prefixes", []),
            "exec_confirm_patterns": cfg.get("exec_confirm_patterns", []),
        }
    rc = 0
    for p in args.check:
        r = check_path(p, cfg)
        out["checks"].append(r)
        if not r["allowed"]:
            rc = 1
            out["ok"] = False
    if args.exec:
        e = check_exec(args.exec, cfg)
        out["exec"] = e
        if e["requires_confirm"]:
            rc = 3
            out["ok"] = False
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for r in out["checks"]:
            print(("ALLOW " if r["allowed"] else "DENY  ") + r["normalized"] + "  [" + r["reason"] + "]")
        if out["exec"]:
            print("EXEC " + ("REQUIRES_CONFIRM hits=%s" % out["exec"]["hits"])
                  if out["exec"]["requires_confirm"] else "EXEC ok")
        if out.get("summary"):
            print("allow_repo=%d allow_home=%d deny_repo=%d exec_patterns=%d"
                  % (len(out["summary"]["allow_repo_prefixes"]),
                     len(out["summary"]["allow_home_prefixes"]),
                     len(out["summary"]["deny_repo_prefixes"]),
                     len(out["summary"]["exec_confirm_patterns"])))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
