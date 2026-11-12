#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""public-surface gate — 存量冻结 / 增量阻断

WHY (2026-11-12)
    The GitHub main repo is PUBLIC. After an internal audit doc was committed
    by accident, a recount showed pulling those 4 files out of HEAD covered
    only 3.7% of the exposure (8 of 217 hit lines). 426 tracked files still
    carried intranet-address / missing-auth / missing-approval / vault-path
    content on the public face. Cleaning them one by one is not workable, so
    this gate freezes today's hits as a baseline and blocks any NEW one at
    push time.

JUDGEMENT RULE
    Whether the target remote is public is always probed anonymously against
    api.github.com -- never inferred from the repo name, never from memory.
    On a 404 we hit a known-public repo (git/git) as a control, otherwise an
    API outage would read as "private" and silently pass (2026-11-11 判例:
    the main site was blocked while api.github.com still answered 200, so a
    single-endpoint probe proves nothing either way).

USAGE
    python tools/check_public_surface.py --write-baseline
    python tools/check_public_surface.py --remote-url <url> [--warn]
    python tools/check_public_surface.py --status --remote-url <url>

    The baseline file is git-ignored on purpose. It is an index of which file
    hits which rule, so publishing it would hand out the very map this gate
    exists to withhold -- the tool must not become the leak. It is generated
    from HEAD automatically the first time it is missing.

EXIT
    0  no new exposure (or remote not public / not applicable)
    1  new exposure found -> block push
    2  visibility undeterminable and no cache -> fail open with a loud warning

ESCAPE HATCH
    ZB_PUBLIC_SURFACE=warn   warn only, never block
    ZB_PUBLIC_SURFACE=off    skip entirely (never make this routine)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date

BASELINE_REL = "tools/public_surface_baseline.tsv"
CACHE_REL = ".git/zb_public_surface_cache.json"
CACHE_TTL = 6 * 3600
CONTROL_REPO = "git/git"

# Rule values are written so this file never matches itself: the intranet IP
# keeps its regex backslashes, the two CJK rules use \u escapes, and the path
# rule uses adjacent-literal concatenation.
RULES = {
    "INTRANET_IP": r"10\.205\.0\.118",
    "NO_AUTH": "\u65e0\u9274\u6743",
    "NO_APPROVAL": "\u65e0\u5ba1\u6279",
    "VAULT_PATH": "_" "vault",
}

# The baseline lists every offending path, so it and this scanner would always
# match themselves. Excluded from the scan, not from the repo.
SELF_EXCLUDE = {BASELINE_REL, "tools/check_public_surface.py"}


def repo_root() -> str:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True)
    if out.returncode != 0:
        raise SystemExit("not inside a git repository")
    return out.stdout.decode("utf-8", "replace").strip()


def scan(root: str) -> dict[tuple[str, str], int]:
    """Return {(path, rule): hit_line_count} over tracked files at HEAD."""
    hits: dict[tuple[str, str], int] = {}
    for rule, pattern in RULES.items():
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "grep", "-c", "--", pattern, "HEAD"],
            cwd=root, capture_output=True,
        )
        if proc.returncode not in (0, 1):
            raise SystemExit(
                "git grep failed for rule %s: %s"
                % (rule, proc.stderr.decode("utf-8", "replace")[:200])
            )
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if not line.startswith("HEAD:"):
                continue
            path, _, cnt = line[5:].rpartition(":")
            if not cnt.isdigit() or path in SELF_EXCLUDE:
                continue
            hits[(path, rule)] = int(cnt)
    return hits


def load_baseline(root: str) -> dict[tuple[str, str], int]:
    path = os.path.join(root, BASELINE_REL)
    base: dict[tuple[str, str], int] = {}
    if not os.path.exists(path):
        return base
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.startswith("path\t"):
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not parts[2].isdigit():
                continue
            base[(parts[0], parts[1])] = int(parts[2])
    return base


def write_baseline(root: str, hits: dict[tuple[str, str], int]) -> str:
    path = os.path.join(root, BASELINE_REL)
    rows = sorted(hits.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    lines = [
        "# public-surface baseline - auto-generated, do not hand-edit",
        "# generated: %s" % date.today().isoformat(),
        "#",
        "# 语义: 每个 (path, rule) 记录当前命中行数上限 count。",
        "#   当前命中 > count，或出现基线里没有的新文件 -> 阻断 push。",
        "#   <= count -> 放行（存量，冻结在此）。",
        "# 为什么是「计数上限」而不是「行号」: 文件行号一变动，按行号记的",
        "#   豁免就会全部失配、把存量误报成新增（scan_secrets allowlist 的",
        "#   已知代价）。改内容不改条数是最常见的情形，按数量卡更稳。",
        "#",
        "# 重建: python tools/check_public_surface.py --write-baseline",
        "# 修复后请手动删掉对应行，让上限降下来——否则上限只增不减。",
        "",
        "path\trule\tcount",
    ]
    for (p, r), c in rows:
        lines.append("%s\t%s\t%d" % (p, r, c))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def parse_slug(url: str):
    """github.com URL -> (owner, repo); anything else -> None."""
    u = url.strip()
    if u.startswith("git@") and ":" in u:
        host, _, path = u.partition(":")
    else:
        rest = u.split("://", 1)[-1]
        host, _, path = rest.partition("/")
    host = host.split("@")[-1].lower()
    if host not in ("github.com", "www.github.com"):
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _api_json(url: str, timeout: float = 6.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": "zb-public-surface-gate",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def _read_cache(root: str) -> dict:
    try:
        with open(os.path.join(root, CACHE_REL), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_cache(root: str, data: dict) -> None:
    try:
        path = os.path.join(root, CACHE_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def probe_visibility(root: str, owner: str, repo: str) -> tuple[str, str]:
    """-> (visibility, evidence). visibility in {public, private, unknown}."""
    slug = "%s/%s" % (owner, repo)
    cache = _read_cache(root)
    entry = cache.get(slug) or {}
    if entry.get("visibility") in ("public", "private"):
        if time.time() - float(entry.get("ts", 0)) < CACHE_TTL:
            return entry["visibility"], "cache(%s)" % entry.get("evidence", "")

    status, data = _api_json("https://api.github.com/repos/%s" % slug)
    if status == 200 and isinstance(data, dict):
        vis = "private" if data.get("private") else "public"
        evidence = "api 200 private=%s" % data.get("private")
    elif status == 404:
        ctl_status, _ = _api_json("https://api.github.com/repos/%s" % CONTROL_REPO)
        if ctl_status == 200:
            vis, evidence = "private", "api 404 + control 200"
        else:
            return "unknown", "api 404 but control failed (%s) -> API unreachable" % ctl_status
    else:
        return "unknown", "api request failed (status=%s)" % status

    cache[slug] = {"visibility": vis, "evidence": evidence, "ts": time.time()}
    _write_cache(root, cache)
    return vis, evidence


def main() -> int:
    ap = argparse.ArgumentParser(description="public-surface exposure gate")
    ap.add_argument("--remote-url", default="", help="push target URL (from pre-push $2)")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--status", action="store_true", help="only report visibility")
    ap.add_argument("--warn", action="store_true", help="report but never block")
    args = ap.parse_args()

    root = repo_root()

    if args.write_baseline:
        path = write_baseline(root, scan(root))
        print("[public-surface] baseline written: %s" % os.path.relpath(path, root))
        return 0

    slug = parse_slug(args.remote_url) if args.remote_url else None
    if args.remote_url and slug is None:
        print("[public-surface] 目标远端不是 github.com，不适用本门禁：%s" % args.remote_url)
        return 0

    if slug is None:
        print("[public-surface] 未提供 --remote-url，跳过（无法判定公开面）")
        return 0

    vis, evidence = probe_visibility(root, *slug)
    if args.status:
        print("[public-surface] %s/%s -> %s (%s)" % (slug[0], slug[1], vis, evidence))
        return 0

    if vis == "private":
        print("[public-surface] 目标远端 %s/%s 为私有（%s），公开面无需检查" % (slug[0], slug[1], evidence))
        return 0
    if vis == "unknown":
        print("[public-surface] ⚠ 判不了 %s/%s 的可见性（%s）" % (slug[0], slug[1], evidence))
        print("[public-surface]   fail-open 放行，但请手工确认：curl -s api.github.com/repos/%s/%s"
              % (slug[0], slug[1]))
        return 2

    # 基线不入库（见 .gitignore）：它是一份「哪些文件命中哪类规则」的索引，
    # 推上 public 面等于把检索地图直接发出去——比原本的分散暴露更糟。缺失
    # 时按当前 HEAD 自动生成，语义就是「接受此刻的存量」，与存量冻结的初衷
    # 一致；新机器第一次 push 会走到这里，不必手工干预。
    baseline_path = os.path.join(root, BASELINE_REL)
    if not os.path.exists(baseline_path):
        write_baseline(root, scan(root))
        print("[public-surface] 基线不存在，已按当前 HEAD 生成（存量冻结）：%s" % BASELINE_REL)
        print("[public-surface]   若当前 HEAD 本就不该含这些内容，请先清理再重建基线。")
    base = load_baseline(root)

    current = scan(root)
    problems = []
    for (path, rule), cnt in current.items():
        cap = base.get((path, rule), 0)
        if cnt > cap:
            problems.append((path, rule, cnt, cap))

    if not problems:
        print("[public-surface] 公开面增量检查通过 ✓（%s 为 public，存量已冻结）" % ("%s/%s" % slug))
        return 0

    problems.sort(key=lambda t: (t[2] - t[3]), reverse=True)
    print("[public-surface] ⚠ 公开面出现 %d 处新增暴露，目标远端是 public：" % len(problems))
    for path, rule, cnt, cap in problems[:20]:
        if cap == 0:
            print("    新增文件  [%s]  %s  (%d 行)" % (rule, path, cnt))
        else:
            print("    命中增加  [%s]  %s  (%d -> %d 行)" % (rule, path, cap, cnt))
    if len(problems) > 20:
        print("    ... 另有 %d 处" % (len(problems) - 20))
    print("[public-surface] 处理办法：① 改写成不带敏感特征的表述；或 ② 移出发往 public 的提交。")
    print("[public-surface]  ③ 确认是合理存量：先把文件移出，再用 --write-baseline 重建基线。")
    if args.warn or os.environ.get("ZB_PUBLIC_SURFACE") == "warn":
        print("[public-surface] ZB_PUBLIC_SURFACE=warn → 仅警告放行（禁常态化）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
