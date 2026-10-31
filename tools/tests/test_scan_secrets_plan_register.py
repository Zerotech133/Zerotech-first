# -*- coding: utf-8 -*-
"""K-ORG-20261031-PLANREG：计划台账生成物路径豁免（HIGH_ENTROPY 误报）。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import scan_secrets as S  # noqa: E402


def _exempt(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(re.search(p, norm) for p in S.EXEMPT_PATH_PATTERNS)


def test_generated_plan_register_exempt():
    assert _exempt("docs/plans/PLAN_REGISTER_ALL_20261031.json")
    assert _exempt("docs/plans/PLAN_REGISTER_ALL_20261031.md")
    assert _exempt("docs/plans/PLAN_REGISTER_20261031.md")


def test_normal_plan_not_exempt():
    assert not _exempt("docs/plans/2026-10-24_PRODUCT_BASELINE_v1.md")
    assert not _exempt("portal/nginx.conf")
