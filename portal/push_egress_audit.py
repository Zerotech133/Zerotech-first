# -*- coding: utf-8 -*-
"""推送出口审计（P0-1，2026-10-26）。

扫描仓库内所有"发送/推送"调用点，分为：
  hub      经 8097 闸门（feishu_client.send_msg / send_feishu / push_card 等）
  direct   urllib/requests 直连 open.feishu.cn（绕过 hub）
  cli      lark-cli 子进程兜底
  sdk      lark_oapi SDK 直接发送
  unknown  其他疑似发送

输出：portal/work/egress_audit.json + 摘要（可追加到交付报告）。
用法：python push_egress_audit.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "work", "egress_audit.json")
SKIP_DIRS = {"site-packages", "venv", "node_modules", ".git", "__pycache__",
             ".worktrees", "outputs", "backups", "_archive", "dist", "build",
             "projects-archive"}
SCAN_ROOTS = ["portal", "news_collector", "quant_backtest", "jev-eval", "tools"]
PATTERNS = [
    ("hub", re.compile(r"\b(send_msg|send_msg_tracked|send_card|push_card|push_card_multi|send_text)\s*\(")),
    ("hub", re.compile(r"\bsend_content_to\s*\(")),
    ("direct", re.compile(r"open\.feishu\.cn")),
    ("direct", re.compile(r"im/v1/messages")),
    ("cli", re.compile(r"lark-cli")),
    ("sdk", re.compile(r"\.im\.v1\.message\.(create|reply|send)")),
]
SKIP_FILES = {"feishu_client.py", "send_feishu.py", "push_admin_server.py",
              "push_egress_audit.py"}


def scan(root=None):
    hits = []
    for base in SCAN_ROOTS:
        d = os.path.join(root or ROOT, base)
        if not os.path.isdir(d):
            continue
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in filenames:
                if not fn.endswith(".py") or fn in SKIP_FILES:
                    continue
                if fn.startswith("test_"):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(fp) > 2_000_000:
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except OSError:
                    continue
                for i, line in enumerate(lines, 1):
                    if line.lstrip().startswith("#"):
                        continue
                    for kind, rx in PATTERNS:
                        if rx.search(line):
                            hits.append({
                                "file": os.path.relpath(fp, root or ROOT),
                                "line": i, "kind": kind,
                                "code": line.strip()[:120]})
                            break
    return hits


EXEMPT_PATH = os.path.join(HERE, "work", "egress_exemptions.json")


def _exemptions():
    try:
        with open(EXEMPT_PATH, encoding="utf-8") as f:
            items = (json.load(f) or {}).get("items") or []
    except (OSError, ValueError):
        items = []
    return {(str(x.get("file") or "").replace("\\", "/"), int(x.get("line") or 0))
            for x in items}


def summarize(hits):
    by_kind = {}
    ex = _exemptions()
    for h in hits:
        f = h["file"].replace("\\", "/")
        h["exempt"] = (f, h["line"]) in ex
        by_kind.setdefault(h["kind"], []).append(h)
    direct = [h for h in hits if h["kind"] == "direct"]
    cli = [h for h in hits if h["kind"] == "cli"]
    direct_open = [h for h in direct if not h.get("exempt")]
    return {"total": len(hits),
            "by_kind": {k: len(v) for k, v in by_kind.items()},
            "direct_sites": direct_open[:30],
            "direct_exempt": [h for h in direct if h.get("exempt")][:30],
            "cli_sites": cli[:20],
            "hub_sites": len(by_kind.get("hub") or []),
            "exempt_n": sum(1 for h in hits if h.get("exempt")),
            "note": "hub=经 8097 闸门；direct=直连飞书 API（旁路）；cli=lark-cli 兜底；exempt=已登记豁免"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    hits = scan()
    s = summarize(hits)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"hits": hits, "summary": s}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=1))
    else:
        print("推送出口审计：调用点 %d 处 · hub %d / direct %d / cli %d / sdk %d"
              % (s["total"], s["by_kind"].get("hub", 0),
                 s["by_kind"].get("direct", 0), s["by_kind"].get("cli", 0),
                 s["by_kind"].get("sdk", 0)))
        print("直连旁路（未登记，需收敛/登记）：")
        print("已登记豁免：%d 处" % s.get("exempt_n", 0))
        for h in s["direct_sites"][:12]:
            print("  - %s:%d  %s" % (h["file"], h["line"], h["code"][:80]))
        print("结果：", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
