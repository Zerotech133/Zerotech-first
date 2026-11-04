# -*- coding: utf-8 -*-
"""DEPRECATED：薄封装，转发到 knowledge_governance.py --all（保持 PS1/旧调用兼容）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import knowledge_governance as kg  # noqa: E402


def main() -> int:
    rep = kg.run_all(write=True)
    print(json.dumps(rep.get("aggregate") or {}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
