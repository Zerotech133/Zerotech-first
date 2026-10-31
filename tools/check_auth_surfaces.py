# -*- coding: utf-8 -*-
"""身份入口守卫：只允许 config/auth_surfaces.json 中的 5 个凭证面。

用法：
  python tools/check_auth_surfaces.py          # 校验注册表（失败 exit 1）
  python tools/check_auth_surfaces.py --json   # 输出报告

规则：
- 恰好 5 个 surface；
- id/name/owner/source 必填且不重复；
- 出现第 6 个（max_surfaces 被改大）必须 L2 并更新本检查；
- 源码中的已知入口必须能指向 source 文件，避免“表里有、实现没有”。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "config" / "auth_surfaces.json"
REQUIRED = ("id", "kind", "name", "owner", "entry", "purpose", "source")


def check() -> dict:
    try:
        data = json.loads(REG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "registry_unreadable", "detail": str(exc)}
    surfaces = data.get("surfaces") or []
    problems = []
    if data.get("max_surfaces") != 5:
        problems.append("max_surfaces 必须为 5")
    if len(surfaces) != 5:
        problems.append("surface 数量必须为 5，实际 %d" % len(surfaces))
    ids, names = set(), set()
    for s in surfaces:
        if not isinstance(s, dict):
            problems.append("surface 非对象")
            continue
        for k in REQUIRED:
            if not str(s.get(k) or "").strip():
                problems.append("%s 缺字段 %s" % (s.get("id") or "?", k))
        if s.get("id") in ids:
            problems.append("重复 id: %s" % s.get("id"))
        if s.get("name") in names:
            problems.append("重复 name: %s" % s.get("name"))
        ids.add(s.get("id"))
        names.add(s.get("name"))
    return {"ok": not problems, "count": len(surfaces), "problems": problems,
            "decision": data.get("decision"), "sso": data.get("sso"),
            "surfaces": [s.get("id") for s in surfaces if isinstance(s, dict)]}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
