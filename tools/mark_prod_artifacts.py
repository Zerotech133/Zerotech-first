#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日自动产物：批量打/撤 `skip-worktree` 标记（R12 T-F A 类配套）。

为什么不用 .gitignore：
    portal/static/*.json 是页面直接读的静态数据，必须留在版本控制里，否则新克隆/
    新环境缺文件、页面 404。skip-worktree 让本地内容变化不进 `git status`，但索引
    里仍保存一份，部署链路零影响。

为什么需要本脚本：
    `--skip-worktree` 标记只存在本地 .git/index，**不随仓库传播**。新克隆后必须跑
    一次，否则 17 个产物立刻重新显示为脏。

用法：
    python tools/mark_prod_artifacts.py --check              # 只读报表
    python tools/mark_prod_artifacts.py                      # 批量标记
    python tools/mark_prod_artifacts.py --include-untracked  # 未入库的同类产物先 add 再标记
    python tools/mark_prod_artifacts.py --clear              # 取消全部标记
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIST = Path(__file__).resolve().parent / "prod_artifacts.txt"


def read_list(path: Path = LIST) -> list[str]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.replace("\\", "/"))
    return out


def _git(*args: str) -> tuple[int, bytes, bytes]:
    p = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True)
    return p.returncode, p.stdout, p.stderr


def _index_flags(paths: list[str]) -> dict[str, str]:
    """path → ls-files -v 的标记字符（S=skip-worktree，H=普通，空=未入库）。"""
    out: dict[str, str] = {}
    if not paths:
        return out
    rc, stdout, _ = _git("ls-files", "-v", "-z", "--", *paths)
    if rc != 0:
        return out
    for item in stdout.decode("utf-8", "surrogateescape").split("\0"):
        if not item:
            continue
        flag, _, p = item.partition(" ")
        out[p.replace("\\", "/")] = flag
    return out


def _dirty(paths: list[str]) -> set[str]:
    rc, stdout, _ = _git("status", "--porcelain", "-z", "--", *paths)
    if rc != 0:
        return set()
    dirty = set()
    for item in stdout.decode("utf-8", "surrogateescape").split("\0"):
        if len(item) < 4:
            continue
        dirty.add(item[3:].replace("\\", "/"))
    return dirty


def survey(paths: list[str]) -> list[dict]:
    flags = _index_flags(paths)
    dirty = _dirty(paths)
    rows = []
    for p in paths:
        flag = flags.get(p, "")
        if not flag:
            state = "untracked"
        elif flag.upper() == "S":
            state = "skip-worktree"
        elif p in dirty:
            state = "dirty"
        else:
            state = "tracked-clean"
        rows.append({"path": p, "flag": flag, "state": state,
                     "exists": (REPO / p).is_file()})
    return rows


def mark(paths: list[str], include_untracked: bool) -> dict:
    flags = _index_flags(paths)
    added, marked, skipped, failed = [], [], [], []
    for p in paths:
        if not (REPO / p).is_file():
            skipped.append({"path": p, "why": "文件不存在"})
            continue
        if not flags.get(p):
            if not include_untracked:
                skipped.append({"path": p, "why": "未入库（需 --include-untracked）"})
                continue
            rc, _, err = _git("add", "--", p)
            if rc != 0:
                failed.append({"path": p, "stage": "add",
                               "err": err.decode("utf-8", "replace")[:200]})
                continue
            added.append(p)
        rc, _, err = _git("update-index", "--skip-worktree", "--", p)
        if rc != 0:
            failed.append({"path": p, "stage": "update-index",
                           "err": err.decode("utf-8", "replace")[:200]})
            continue
        marked.append(p)
    return {"added": added, "marked": marked, "skipped": skipped, "failed": failed}


def clear(paths: list[str]) -> dict:
    flags = _index_flags(paths)
    done, failed = [], []
    for p in paths:
        if not flags.get(p):
            continue
        rc, _, err = _git("update-index", "--no-skip-worktree", "--", p)
        (failed if rc else done).append(
            {"path": p, "err": err.decode("utf-8", "replace")[:200]} if rc else p)
    return {"cleared": done, "failed": failed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日自动产物 skip-worktree 标记维护")
    ap.add_argument("--check", action="store_true", help="只读报表，不修改索引")
    ap.add_argument("--clear", action="store_true", help="取消全部 skip-worktree 标记")
    ap.add_argument("--include-untracked", action="store_true",
                    help="清单里尚未入库的产物先 git add 再标记")
    ap.add_argument("--list", default=str(LIST), help="清单文件路径")
    a = ap.parse_args(argv)

    paths = read_list(Path(a.list))
    if not paths:
        print("清单为空：%s" % a.list, file=sys.stderr)
        return 2

    if a.clear:
        res = clear(paths)
        print("已取消 %d 项标记" % len(res["cleared"]))
        for f in res["failed"]:
            print("  失败 %s：%s" % (f["path"], f["err"]))
        return 0 if not res["failed"] else 1

    if not a.check:
        res = mark(paths, a.include_untracked)
        if res["added"]:
            print("新入库 %d 项：%s" % (len(res["added"]), ", ".join(res["added"])))
        print("已标记 %d 项" % len(res["marked"]))
        for s in res["skipped"]:
            print("  跳过 %s：%s" % (s["path"], s["why"]))
        for f in res["failed"]:
            print("  失败 %s（%s）：%s" % (f["path"], f["stage"], f["err"]))
        if res["failed"]:
            return 1

    rows = survey(paths)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    print("清单 %d 项：" % len(rows) +
          " / ".join("%s=%d" % (k, counts[k]) for k in sorted(counts)))
    for r in rows:
        if r["state"] != "skip-worktree":
            print("  [%s] %s%s" % (r["state"], r["path"],
                                   "" if r["exists"] else "（文件不存在）"))
    ok = counts.get("skip-worktree", 0) == len(rows)
    print("结果：%s" % ("全部已标记" if ok else "存在未标记项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
