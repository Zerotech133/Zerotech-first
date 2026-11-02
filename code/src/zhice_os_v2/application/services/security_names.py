# -*- coding: utf-8 -*-
"""证券代码归一与名称解析（全站统一口径）。"""
from __future__ import annotations

from typing import Mapping


def code_variants(market: str, code: str) -> list[str]:
    c = str(code or "").strip()
    out = {c, c.upper(), c.lower()}
    base = c.split(".")[0]
    out |= {base, base.upper(), base.lower()}
    if market == "CN":
        if base.upper().startswith(("SH", "SZ", "BJ")) and base[2:].isdigit():
            num = base[2:]
            pre = base.upper()[:2]
            out |= {num + "." + pre, num, pre.lower() + num}
        elif base.isdigit() and len(base) == 6:
            suffix = ".SH" if base.startswith(("6", "68")) else (".BJ" if base.startswith(("4", "8", "92")) else ".SZ")
            out |= {base + suffix, "sh" + base, "sz" + base, "bj" + base}
    elif market == "HK":
        if base.isdigit():
            out |= {base.zfill(5), base.zfill(5) + ".HK", base + ".HK"}
    elif market == "US":
        out |= {base.upper()}
    return sorted(x for x in out if x)


def resolve_name(names: Mapping[tuple[str, str], str], market: str, code: str) -> str | None:
    for v in code_variants(market, code):
        if (market, v) in names:
            return names[(market, v)]
    return None
