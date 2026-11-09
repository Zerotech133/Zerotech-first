# -*- coding: utf-8 -*-
"""K-ORG-20261107-ENTROPY 影响面探针：对比 V4 规则改动前后的 HIGH_ENTROPY 判定差异。

两种口径：

  --mode rule（默认，与决策备忘同口径）
      对每个 tracked 文本文档的每一行，用 HIGH_ENTROPY 正则 `[A-Za-z0-9_=]{20,}` 逐个抽候选，
      直接调 `is_high_entropy_candidate(cand, line)`，比较 old/new 布尔。
      这是"规则本身"的影响面——备忘里 1503/1135/13 都是这个口径。

  --mode scan（端到端）
      调两版 `scan_file()` 比命中集，key 只取 (路径, 行号, 规则)。
      **不能把脱敏预览算进 key**：一行被判为密钥的候选变少后，整行脱敏结果会变，
      同一个命中会因预览不同而呈现成"一失一得"的假差异。

用法：
  python tools/research/probe_entropy_v4.py --mode rule [--json out.json]
  python tools/research/probe_entropy_v4.py --mode scan
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "tools" / "scan_secrets.py"
HE_RE = r"[A-Za-z0-9_=]{20,}"


def load_mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tracked_files() -> list:
    # 必须用 -z：默认 core.quotepath=true 会把中文文件名转成 "…\346\265\213…" 形态，
    # Path(repo/rel) 拼出来不存在 → 文件被静默跳过（本仓大量中文名交付件，直接漏测）。
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                         capture_output=True)
    assert out.returncode == 0, out.stderr[-500:]
    return [x.decode("utf-8") for x in out.stdout.split(b"\x00") if x.strip()]


def read_text(f: Path):
    try:
        if os.path.getsize(f) > 8 * 1024 * 1024:
            return None
        raw = f.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def mode_rule(old, new, files):
    import re as _re
    rx = _re.compile(HE_RE)
    newly = []
    lines_old, lines_new, lines_newly = set(), set(), set()
    tot_old = tot_new = 0
    for rel in files:
        text = read_text(REPO / rel)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in rx.finditer(line):
                cand = m.group(0).strip().strip("\"'`=:,;")
                if not cand:
                    continue
                a = old.is_high_entropy_candidate(cand, line)
                b = new.is_high_entropy_candidate(cand, line)
                tot_old += a
                tot_new += b
                if a:
                    lines_old.add((rel, lineno))
                if b:
                    lines_new.add((rel, lineno))
                if a and not b:
                    newly.append((rel, lineno, cand))
                    lines_newly.add((rel, lineno))
    return tot_old, tot_new, newly, len(lines_old), len(lines_new), len(lines_newly)


def mode_scan(old, new, files):
    def key(mod, f):
        hits, _ = mod.scan_file(str(f))
        return {(ln, rule) for (ln, rule, _p, exempt) in hits if not exempt}
    lost, gained = [], []
    for rel in files:
        f = REPO / rel
        if not f.is_file():
            continue
        a, b = key(old, f), key(new, f)
        for ln, rule in a - b:
            lost.append((rel, ln, rule))
        for ln, rule in b - a:
            gained.append((rel, ln, rule))
    return lost, gained


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rule", "scan"], default="rule")
    ap.add_argument("--json", default="")
    ap.add_argument("--include-untracked", action="store_true",
                    help="把未跟踪但未被 gitignore 的文件也纳入（交付件常处于未跟踪态）")
    args = ap.parse_args()

    head_src = subprocess.run(["git", "show", "HEAD:tools/scan_secrets.py"], cwd=str(REPO),
                              capture_output=True).stdout
    assert head_src, "取不到 HEAD 版 scan_secrets.py"
    tmp = Path(tempfile.mkdtemp(prefix="entropy_v4_")) / "scan_secrets_head.py"
    tmp.write_bytes(head_src)
    old, new = load_mod(tmp, "scan_secrets_head"), load_mod(SRC, "scan_secrets_new")

    files = tracked_files()
    if args.include_untracked:
        out = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                             cwd=str(REPO), capture_output=True)
        extra = [x.decode("utf-8") for x in out.stdout.split(b"\x00") if x.strip()]
        files = files + extra
        print("未跟踪补充：%d" % len(extra))
    print("待查文件：%d" % len(files))

    if args.mode == "rule":
        tot_old, tot_new, newly, lo, ln_, lnl = mode_rule(old, new, files)
        print("HIGH_ENTROPY 判 True 的候选数：old=%d  new=%d  差=%d" %
              (tot_old, tot_new, tot_old - tot_new))
        print("判 True 的行数：old=%d  new=%d（差 %d）" % (lo, ln_, lo - ln_))
        print("\n【新豁免候选】%d 条（涉及 %d 行）：" % (len(newly), lnl))
        for rel, ln, cand in newly:
            print("  %-56s :%-5d %s" % (rel, ln, cand))
        by_file = collections.Counter(r for r, _l, _c in newly)
        print("\n【按文件】")
        for k, v in by_file.most_common():
            print("  %-56s %d" % (k, v))
        # 判定不看具体条数（备忘的 13 是旧口径估计），只看"每一条是否都符合路径两侧闭合"
        import re as _re
        bad = [x for x in newly if not _re.fullmatch(r"[a-z0-9_]{12,}", x[2])]
        ok = not bad and tot_new == tot_old - len(newly)
        print("\n判定：%s（新豁免全部为全小写路径成分 = %s；候选总数守恒 = %s）"
              % ("PASS" if ok else "CHECK", not bad, tot_new == tot_old - len(newly)))
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"tot_old": tot_old, "tot_new": tot_new, "newly": newly},
                ensure_ascii=False, indent=1), encoding="utf-8")
        return 0 if ok else 1

    lost, gained = mode_scan(old, new, files)
    print("\n【scan_file 口径 lost】%d" % len(lost))
    for rel, ln, rule in lost:
        print("  %-56s :%-5d %s" % (rel, ln, rule))
    print("\n【scan_file 口径 gained】%d" % len(gained))
    for rel, ln, rule in gained:
        print("  %-56s :%-5d %s" % (rel, ln, rule))
    ok = all(r == "HIGH_ENTROPY" for _r, _l, r in lost) and not gained
    print("\n判定：%s（期望 lost 全为 HIGH_ENTROPY、gained=0）" % ("PASS" if ok else "CHECK"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
