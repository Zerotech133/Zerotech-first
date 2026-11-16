# -*- coding: utf-8 -*-
"""文档同步校验器的「自我破坏」探针（2026-11-16 WO-DOC-SYNC-PROBE）。

用途：证明某条 `block_equals_cmd` 校验**真的会红**，而不是永远 PASS。
做法：在**副本**上制造三种破坏，逐种断言失败原因含预期关键词，并比对原件 sha256 未变。

    python -X utf8 tools/doc_sync_probe.py \
        --doc docs/ops/xxx.md --block "| 产出 | 实测 | 期望 | 结果 |" --lines 9 \
        --cmd "python -X utf8 tools/acad1_bridge_reconcile.py --pool-date 2026-11-16 --markdown"

退出码：0 = 三种场景全符预期且原件未变；1 = 有不符。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_checker():
    spec = importlib.util.spec_from_file_location("ac_probe", REPO / "tools" / "acceptance_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="block_equals_cmd 的自我破坏探针（只动副本）")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--block", required=True)
    ap.add_argument("--lines", type=int, required=True)
    ap.add_argument("--cmd", required=True, help="产出该块的命令（字符串，内部 shlex 拆分）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    doc = Path(args.doc) if Path(args.doc).is_absolute() else REPO / args.doc
    if not doc.is_file():
        print("DOC_MISSING %s" % doc)
        return 1
    cmd = shlex.split(args.cmd, posix=False)
    cmd = [c.strip('"') for c in cmd]
    before = sha256(doc)
    ac = _load_checker()

    work = Path(tempfile.mkdtemp(prefix="docsync_"))
    scenarios = []
    try:
        src = doc.read_text(encoding="utf-8")
        spec_base = {"block": args.block, "lines": args.lines, "cmd": cmd}

        # A 原样 → 必须 PASS
        a = work / "a.md"
        a.write_text(src, encoding="utf-8")
        ok, ev = ac.check_block_equals_cmd(dict(spec_base, path=str(a)), REPO)
        scenarios.append({"name": "A 原样", "expect": "PASS", "got": "PASS" if ok else "FAIL",
                          "ok": ok, "evidence": ev})

        # B 手改表内一个字符 → 必须 FAIL 且报"第 N 行"
        lines = src.splitlines()
        idx = [i for i, l in enumerate(lines) if l.strip() == args.block.strip()]
        mutated = src
        if len(idx) == 1:
            target = None
            # 跳过表头本身与纯分隔行（|---|---|），改第一行有内容的
            for j in range(idx[0] + 1, min(idx[0] + args.lines, len(lines))):
                core = lines[j].replace("|", "").replace("-", "").replace(":", "").strip()
                if core:
                    target = j
                    break
            if target is not None:
                old = lines[target]
                new = old.replace("|", "| ", 1) if "|" in old else old + " ·"
                mutated = src.replace(old, new, 1)
        b = work / "b.md"
        b.write_text(mutated, encoding="utf-8")
        ok, ev = ac.check_block_equals_cmd(dict(spec_base, path=str(b)), REPO)
        scenarios.append({"name": "B 手改一字", "expect": "FAIL(含'第')",
                          "got": "PASS" if ok else "FAIL", "ok": (not ok) and ("第" in ev),
                          "evidence": ev})

        # C 起始行重复 → 必须 FAIL 且报"需唯一"
        c = work / "c.md"
        c.write_text(src + "\n" + args.block + "\n|---|---|---|---|\n", encoding="utf-8")
        ok, ev = ac.check_block_equals_cmd(dict(spec_base, path=str(c)), REPO)
        scenarios.append({"name": "C 起始行重复", "expect": "FAIL(含'需唯一')",
                          "got": "PASS" if ok else "FAIL",
                          "ok": (not ok) and ("需唯一" in ev), "evidence": ev})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after = sha256(doc)
    unchanged = before == after
    bad = sum(0 if s["ok"] else 1 for s in scenarios) + (0 if unchanged else 1)

    if args.json:
        import json as _json
        print(_json.dumps({"schema": "doc_sync_probe/v1", "doc": str(doc),
                           "sha256_before": before, "sha256_after": after,
                           "source_unchanged": unchanged, "scenarios": scenarios,
                           "ok": bad == 0}, ensure_ascii=False, indent=1))
    else:
        for s in scenarios:
            print("%-14s 期望=%-18s 实得=%-4s %s | %s"
                  % (s["name"], s["expect"], s["got"], "OK" if s["ok"] else "MISMATCH", s["evidence"]))
        print("原件 sha256 未变 : %s（%s…）" % (unchanged, before[:16]))
        print("PROBE %s（%d 个不符预期）" % ("PASS" if bad == 0 else "FAIL", bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
