# -*- coding: utf-8 -*-
"""军团 IDE 扩展发布工具（P0-0 方案 A）。

原则：
- 源码留在主仓工作树（开发用）；**发布 artifact 从 git HEAD 构建**，绝不打包未提交 WIP；
- 生产 `HERMES_WEBUI_EXTENSION_DIR` 应指向 release artifact，而不是主仓工作树；
- 每次发布写 manifest（commit/version/hash），支持 promote/rollback 指针与校验；
- 本工具不重启 Hermes、不改 launcher；切换生产目录需人工/L2 窗口。

用法：
  python tools/release_legion_ide.py --build                  # 从 HEAD 构建一个新版本
  python tools/release_legion_ide.py --verify-dir <dir>       # 校验 artifact 完整性
  python tools/release_legion_ide.py --verify-http <dir> --base http://127.0.0.1:8787/extensions
  python tools/release_legion_ide.py --promote <version>      # 更新 current.json 指针（不重启）
  python tools/release_legion_ide.py --rollback-to <version>  # 指针回滚（不重启）
  python tools/release_legion_ide.py --status                 # 查看 current/最近版本
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_REL = Path("Hermes/extensions/legion-ide")
FILES = ["app.js", "app.css", "a0-mainchat.js", "README.md"]
DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")


def _run(args, cwd=None):
    r = subprocess.run(args, cwd=str(cwd or REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("git failed: %s\n%s" % (" ".join(args), (r.stderr or "")[:300]))
    return r.stdout


def git_head(repo: Path = REPO) -> str:
    return _run(["git", "rev-parse", "HEAD"], repo).strip()


def git_show(rel: Path, commit: str = "HEAD", repo: Path = REPO) -> bytes:
    r = subprocess.run(["git", "show", "%s:%s" % (commit, rel.as_posix())],
                       cwd=str(repo), capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("git show %s:%s failed: %s" % (commit, rel, (r.stderr or b"")[:200]))
    return r.stdout


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(repo: Path = REPO, release_root: Path = DEFAULT_RELEASE_ROOT,
          commit: str = "HEAD", version: str | None = None) -> dict:
    commit_hash = git_head(repo)
    if commit == "HEAD":
        commit = commit_hash
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ver = version or ("%s_%s" % (ts, commit_hash[:8]))
    target = Path(release_root) / ver
    target.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema": "legion-ide-release/v1",
        "version": ver,
        "commit": commit,
        "built_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_rel": SOURCE_REL.as_posix(),
        "files": {},
    }
    for name in FILES:
        data = git_show(SOURCE_REL / name, commit, repo)
        (target / name).write_bytes(data)
        manifest["files"][name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    verify_dir(target)
    return {"dir": str(target), "manifest": manifest}


def verify_dir(target: Path) -> dict:
    target = Path(target)
    mf = target / "manifest.json"
    if not mf.is_file():
        raise RuntimeError("manifest 不存在: %s" % mf)
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    for name, meta in (manifest.get("files") or {}).items():
        p = target / name
        if not p.is_file():
            raise RuntimeError("缺少文件: %s" % p)
        actual = sha256_bytes(p.read_bytes())
        if actual != meta["sha256"]:
            raise RuntimeError("hash 不符: %s (%s != %s)" % (name, actual, meta["sha256"]))
    return manifest


def verify_http(target: Path, base: str = "http://127.0.0.1:8787/extensions") -> dict:
    manifest = verify_dir(target)
    out = {}
    for name, meta in (manifest.get("files") or {}).items():
        if not name.endswith((".js", ".css")):
            continue
        url = base.rstrip("/") + "/" + name
        with urllib.request.urlopen(url, timeout=10) as r:
            data = r.read()
        # 生产 checkout 为 CRLF、git artifact 为 LF：按 LF 归一后比较内容
        norm = lambda b: b.replace(b"\r\n", b"\n")
        served = sha256_bytes(norm(data))
        artifact = sha256_bytes(norm((target / name).read_bytes()))
        out[name] = {"served": served, "artifact": artifact, "match": served == artifact}
    return out


def promote(release_root: Path, version: str) -> dict:
    root = Path(release_root)
    target = root / version
    verify_dir(target)
    cur = {"version": version, "dir": str(target),
           "promoted_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "note": "仅更新指针；Hermes 需重启/切换 env 后才生效（L2 窗口）"}
    (root / "current.json").write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return cur


def rollback_to(release_root: Path, version: str) -> dict:
    return promote(release_root, version)


def status(release_root: Path) -> dict:
    root = Path(release_root)
    cur = root / "current.json"
    out = {"current": json.loads(cur.read_text(encoding="utf-8")) if cur.is_file() else None,
           "versions": sorted([p.name for p in root.iterdir() if p.is_dir()]) if root.is_dir() else []}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="legion-ide 发布（P0-0 方案 A）")
    ap.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT))
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--verify-dir", default="")
    ap.add_argument("--verify-http", default="")
    ap.add_argument("--base", default="http://127.0.0.1:8787/extensions")
    ap.add_argument("--promote", default="")
    ap.add_argument("--rollback-to", default="")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.release_root)
    try:
        if args.build:
            res = build(release_root=root, commit=args.commit, version=args.version or None)
            print(json.dumps({"ok": True, "dir": res["dir"], "version": res["manifest"]["version"],
                              "commit": res["manifest"]["commit"]}, ensure_ascii=False))
            return 0
        if args.verify_dir:
            print(json.dumps({"ok": True, "manifest": verify_dir(Path(args.verify_dir))},
                             ensure_ascii=False, indent=1))
            return 0
        if args.verify_http:
            print(json.dumps({"ok": True, "http": verify_http(Path(args.verify_http), args.base)},
                             ensure_ascii=False, indent=1))
            return 0
        if args.promote:
            print(json.dumps({"ok": True, "current": promote(root, args.promote)}, ensure_ascii=False))
            return 0
        if args.rollback_to:
            print(json.dumps({"ok": True, "current": rollback_to(root, args.rollback_to)}, ensure_ascii=False))
            return 0
        if args.status:
            print(json.dumps({"ok": True, **status(root)}, ensure_ascii=False, indent=1))
            return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e))},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print("no action: use --build/--verify/--promote/--rollback-to/--status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
