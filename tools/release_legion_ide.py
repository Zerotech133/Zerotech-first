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
  python tools/release_legion_ide.py --promote <version> --switch-current  # 指针+current junction 同一动作（W1）
  python tools/release_legion_ide.py --promote <version> --switch-current --dry-run  # 只打印计划
  python tools/release_legion_ide.py --rollback-to <version> --switch-current
  python tools/release_legion_ide.py --status                 # 指针/junction/artifact 三方对账（含 drift）
  python tools/release_legion_ide.py --verify-live            # 只读 live-version smoke（8103 /api/ide/version 对账）
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_REL = Path("Hermes/extensions/legion-ide")
FILES = ["app.js", "app.css", "a0-mainchat.js", "README.md"]
DEFAULT_RELEASE_ROOT = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
BASELINE_REL = Path("Hermes/hermes-webui-next")
# W1（2026-10-31）：生产经 HERMES_WEBUI_EXTENSION_DIR=<release_root>\current 加载，
# current 是 junction（不是指针文件）。只 promote current.json 不改 junction 会
# 出现「指针变了、目录没切」——故新增 switch_current() 把两者作为同一个发布单元切换。
CURRENT_LINK_NAME = "current"
LIVE_VERSION_URL = "http://127.0.0.1:8103/api/ide/version"


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


def baseline_status(repo: Path = REPO) -> dict:
    """Hermes 基线仓（hermes-webui-next）状态：clean / upstream / head / dirty 文件。"""
    h = Path(repo) / BASELINE_REL
    if not (h / ".git").exists():
        return {"exists": False, "ok": True, "dirty": False, "files": [],
                "upstream": False, "head": "", "note": "baseline repo absent"}
    try:
        files = [x for x in _run(["git", "status", "--porcelain"], h).splitlines() if x.strip()]
        remotes = [x for x in _run(["git", "remote"], h).splitlines() if x.strip()]
        head = _run(["git", "rev-parse", "--short", "HEAD"], h).strip()
    except RuntimeError as e:
        return {"exists": True, "ok": False, "dirty": True, "files": [],
                "upstream": False, "head": "", "note": str(e)[:200]}
    upstream = "upstream" in remotes
    return {"exists": True, "ok": (not files) and upstream, "dirty": bool(files),
            "files": files[:20], "upstream": upstream, "remotes": remotes,
            "head": head, "note": "" if (not files and upstream) else "baseline dirty or no upstream remote"}


def require_clean_baseline(repo: Path = REPO) -> dict:
    st = baseline_status(repo)
    if not st["ok"]:
        raise RuntimeError("Hermes 基线不干净/无 upstream，拒绝发布：%s；files=%s"
                           % (st["note"], st.get("files")))
    return st


def build(repo: Path = REPO, release_root: Path = DEFAULT_RELEASE_ROOT,
          commit: str = "HEAD", version: str | None = None,
          allow_dirty_baseline: bool = False) -> dict:
    baseline = baseline_status(repo)
    if not allow_dirty_baseline and not baseline["ok"]:
        raise RuntimeError("Hermes 基线不干净/无 upstream，拒绝发布：%s；files=%s"
                           % (baseline["note"], baseline.get("files")))
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
        "hermes_baseline": baseline,
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
    """校验 artifact 完整性；manifest 为空/缺 sha256 一律 fail-closed。

    W1 修复：旧实现用 `manifest.get("files") or {}` 迭代，空 manifest（如 "{}"）
    会 0 次循环后「校验通过」——等于 fail-open，切 junction 前必须拦住。
    """
    target = Path(target)
    mf = target / "manifest.json"
    if not mf.is_file():
        raise RuntimeError("manifest 不存在: %s" % mf)
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except ValueError as e:
        raise RuntimeError("manifest 非 JSON: %s (%s)" % (mf, e))
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest 非对象: %s" % mf)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("manifest.files 为空或非对象，拒绝发布: %s" % mf)
    for name, meta in files.items():
        if not isinstance(meta, dict) or not str(meta.get("sha256") or ""):
            raise RuntimeError("manifest.files[%s] 缺 sha256: %s" % (name, mf))
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


def _path_present(p: Path) -> bool:
    """True for a real file/dir OR a junction/symlink whose target is gone."""
    return os.path.lexists(str(p)) or p.exists()


def _is_link(p: Path) -> bool:
    """True for junction/symlink (junctions are links but not symlinks on Windows)."""
    try:
        if getattr(p, "is_junction", None) and p.is_junction():
            return True
    except OSError:
        return False
    return os.path.islink(str(p))


def _link_target(p: Path) -> str:
    try:
        return str(os.readlink(str(p)))
    except (OSError, ValueError):
        return ""


def current_link_path(release_root: Path | str) -> Path:
    return Path(release_root) / CURRENT_LINK_NAME


def _prev_link_for(root: Path, version: str) -> Path:
    """Previous-artifact junction name, matching the 2026-10-31 manual convention."""
    prev = root / ("current_prev_%s" % version)
    if not _path_present(prev):
        return prev
    i = 2
    while _path_present(root / ("current_prev_%s_%d" % (version, i))):
        i += 1
    return root / ("current_prev_%s_%d" % (version, i))


def _make_link(link: Path, target: Path) -> None:
    """Create a directory junction (Windows) or symlink (POSIX) at *link*."""
    if os.name == "nt":
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            raise RuntimeError("mklink /J 失败: %s" % ((r.stderr or r.stdout or "")[:300]))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)
    if not _path_present(link):
        raise RuntimeError("junction 创建后不可见: %s" % link)


def switch_current_link(release_root: Path | str, version: str,
                        dry_run: bool = False) -> dict:
    """把 release_root\\current 指向 release_root\\<version>（原子感切换，可回滚）。

    安全约束（fail-closed）：
    - 先 verify_dir(target)：artifact 不完整/被篡改 → 不动任何指针；
    - 只在 release_root 内部切换，拒绝逃逸路径；
    - 旧 current 改名保留为 current_prev_<version>（不删除，便于秒回滚）；
    - 新 junction 先建临时名，再 rename 成 current，缩小半切换窗口。
    ``dry_run=True`` 只返回计划动作，不触碰文件系统。
    """
    root = Path(release_root)
    target = root / version
    if not root.is_dir():
        raise RuntimeError("release_root 不存在: %s" % root)
    verify_dir(target)  # fail-closed: 指针/Junction 永不指向未校验 artifact
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(root.resolve())
    except ValueError:
        raise RuntimeError("artifact 越出 release_root: %s" % target)
    link = root / CURRENT_LINK_NAME
    prev = _prev_link_for(root, version)
    plan = {"version": version, "link": str(link), "target": str(target),
            "prev": str(prev), "dry_run": bool(dry_run)}
    if dry_run:
        plan["planned"] = True
        return plan
    tmp = root / (".current_new_%s_%d" % (version, os.getpid()))
    if _path_present(tmp):
        raise RuntimeError("临时 junction 已存在: %s" % tmp)
    moved_prev = False
    try:
        _make_link(tmp, target)
        if _path_present(link):
            os.rename(str(link), str(prev))  # 旧目录留作回滚位
            moved_prev = True
        os.rename(str(tmp), str(link))
    except Exception:
        try:
            if _path_present(tmp):
                os.rmdir(str(tmp)) if _is_link(tmp) else None
        except OSError:
            pass
        if moved_prev and not _path_present(link):
            try:
                os.rename(str(prev), str(link))
            except OSError:
                pass
        raise
    plan["planned"] = False
    plan["switched"] = True
    plan["previous_target"] = _link_target(prev) if moved_prev else ""
    return plan


def _write_current_json(root: Path, cur: dict) -> None:
    """Atomic pointer write (tmp + os.replace) so a crash never leaves half JSON."""
    tmp = root / "current.json.tmp"
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(root / "current.json"))


def promote(release_root: Path, version: str, *, switch_current: bool = False,
            dry_run: bool = False) -> dict:
    """更新 current.json 指针；switch_current=True 时同时切 current junction。

    W1 语义：一次发布 = 指针 + 目录（junction）同一动作；只传旧参数时保持
    P0-0 兼容行为（只写指针，不切 junction），由调用方显式选择。
    """
    root = Path(release_root)
    target = root / version
    verify_dir(target)
    if dry_run:
        out = {"version": version, "dir": str(target), "dry_run": True,
               "note": "dry-run：未写指针、未切 junction"}
        if switch_current:
            out["switch"] = switch_current_link(root, version, dry_run=True)
        return out
    link_result = None
    if switch_current:
        link_result = switch_current_link(root, version)
    cur = {"version": version, "dir": str(target),
           "promoted_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "note": ("指针 + junction 同一动作（W1）；Hermes 经 HERMES_WEBUI_EXTENSION_DIR "
                    "读取，无需重启") if switch_current else
                   "仅更新指针（未切 junction；旧调用方兼容行为）"}
    try:
        _write_current_json(root, cur)
    except Exception:
        # 指针写失败 → 回滚 junction，避免「目录切了、指针没切」反向漂移
        if link_result and link_result.get("previous_target"):
            try:
                switch_current_link(root, Path(link_result["previous_target"]).name)
            except Exception:  # noqa: BLE001 - 回滚失败时保留原始异常
                pass
        raise
    if link_result:
        cur["junction_switch"] = link_result
    return cur


def rollback_to(release_root: Path, version: str, *, switch_current: bool = False,
                dry_run: bool = False) -> dict:
    return promote(release_root, version, switch_current=switch_current, dry_run=dry_run)


def _display_target(p: str) -> str:
    """Strip the Windows extended-length prefix for readable status output."""
    s = str(p or "")
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[len("\\\\?\\UNC\\"):]
    if s.startswith("\\\\?\\"):
        return s[len("\\\\?\\"):]
    return s


def _norm(p: str) -> str:
    r"""Normalize a path for comparison, stripping Windows extended-length prefixes.

    ``os.readlink`` on a junction returns the ``\\?\`` extended-length form while
    current.json stores the plain form; without this the drift check would always
    report a false mismatch.
    """
    s = os.path.normpath(str(p)) if p else ""
    if s.startswith("\\\\?\\UNC\\"):
        s = "\\\\" + s[len("\\\\?\\UNC\\"):]
    elif s.startswith("\\\\?\\"):
        s = s[len("\\\\?\\"):]
    return os.path.normcase(s)


def status(release_root: Path) -> dict:
    """current.json 指针 / current junction / artifact 三方对账（只读）。

    新增 drift 字段：True 表示「指针所指版本 ≠ junction 实际目录」——W1 要禁止的
    「指针变了、目录没切」。旧字段 current/versions 保持不变。
    """
    root = Path(release_root)
    cur = root / "current.json"
    info = json.loads(cur.read_text(encoding="utf-8")) if cur.is_file() else None
    link = root / CURRENT_LINK_NAME
    link_target = _display_target(_link_target(link)) if _path_present(link) else ""
    pointer_dir = str((info or {}).get("dir") or "")
    drift = bool(pointer_dir and link_target and _norm(pointer_dir) != _norm(link_target))
    link_ok = bool(link_target) and Path(link_target).is_dir()
    return {
        "current": info,
        "pointer_version": str((info or {}).get("version") or ""),
        "junction": {"path": str(link), "exists": _path_present(link),
                     "target": link_target, "target_exists": link_ok,
                     "version": Path(link_target).name if link_target else ""},
        "drift": drift,
        "versions": sorted([p.name for p in root.iterdir() if p.is_dir()]) if root.is_dir() else [],
    }


def verify_live(release_root: Path, url: str = LIVE_VERSION_URL,
                timeout: float = 5.0) -> dict:
    """只读 live-version smoke：线上 /api/ide/version 必须与本地指针/目录一致。

    不重启、不写盘；仅 GET 公开版本端点（8103）。返回 checks 便于测试/门禁用。
    """
    st = status(Path(release_root))
    live = {}
    error = ""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            live = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        error = "%s: %s" % (type(e).__name__, str(e)[:200])
    checks = {
        "live_reachable": bool(live),
        "live_all_match": bool(live.get("all_match")),
        "version_matches_pointer": bool(live) and str(live.get("version") or "") == st["pointer_version"],
        "artifact_matches_live": bool(live) and _norm(str(live.get("artifact_dir") or ""))
        == _norm(st["junction"]["target"]),
        "junction_not_drifted": not st["drift"],
    }
    return {"ok": all(checks.values()), "url": url, "error": error,
            "pointer_version": st["pointer_version"],
            "junction_target": st["junction"]["target"],
            "live": {k: live.get(k) for k in ("ok", "version", "commit", "artifact_dir", "all_match")},
            "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="legion-ide 发布（P0-0 方案 A）")
    ap.add_argument("--release-root", default=str(DEFAULT_RELEASE_ROOT))
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--verify-baseline", action="store_true")
    ap.add_argument("--allow-dirty-baseline", action="store_true")
    ap.add_argument("--verify-dir", default="")
    ap.add_argument("--verify-http", default="")
    ap.add_argument("--base", default="http://127.0.0.1:8787/extensions")
    ap.add_argument("--promote", default="")
    ap.add_argument("--rollback-to", default="")
    ap.add_argument("--switch-current", action="store_true",
                    help="promote/rollback 时同时切 current junction（W1：指针+目录同一动作）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写指针/不切 junction")
    ap.add_argument("--verify-live", action="store_true",
                    help="只读 live-version smoke：GET 8103 /api/ide/version 与指针/junction 对账")
    ap.add_argument("--live-url", default=LIVE_VERSION_URL)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.release_root)
    try:
        if args.verify_baseline:
            print(json.dumps({"ok": True, "baseline": baseline_status()}, ensure_ascii=False, indent=1))
            return 0
        if args.build:
            res = build(release_root=root, commit=args.commit, version=args.version or None,
                        allow_dirty_baseline=args.allow_dirty_baseline)
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
            print(json.dumps({"ok": True, "current": promote(
                root, args.promote, switch_current=args.switch_current,
                dry_run=args.dry_run)}, ensure_ascii=False))
            return 0
        if args.rollback_to:
            print(json.dumps({"ok": True, "current": rollback_to(
                root, args.rollback_to, switch_current=args.switch_current,
                dry_run=args.dry_run)}, ensure_ascii=False))
            return 0
        if args.verify_live:
            res = verify_live(root, url=args.live_url)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0 if res["ok"] else 2
        if args.status:
            print(json.dumps({"ok": True, **status(root)}, ensure_ascii=False, indent=1))
            return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e))},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print("no action: use --build/--verify/--promote/--rollback-to/--status/--verify-live", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
