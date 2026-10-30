# -*- coding: utf-8 -*-
"""零界只读 BFF CLI：不启服务、不写库，直接输出只读视图 JSON。

用法：
  python tools/bff_readonly.py --view overview
  python tools/bff_readonly.py --view cainiao
  python tools/bff_readonly.py --view zhice
  python tools/bff_readonly.py --view contracts

红线：
- 只调用 portal.api_zhice_bff 的只读函数；
- 不提供 --write/--sql；
- 输出 JSON 到 stdout，可选 --out 落盘证据。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTAL = REPO / "portal"
sys.path.insert(0, str(PORTAL))

import api_zhice_bff  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="零界只读 BFF 视图（不写库）")
    ap.add_argument("--view", choices=["overview", "cainiao", "zhice", "contracts"], default="overview")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.view == "overview":
        payload = api_zhice_bff.build_overview()
    elif args.view == "cainiao":
        payload = api_zhice_bff.cainiao_stats(force=True)
    elif args.view == "zhice":
        payload = api_zhice_bff.zhice_health()
    else:
        payload = api_zhice_bff.contracts()

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
