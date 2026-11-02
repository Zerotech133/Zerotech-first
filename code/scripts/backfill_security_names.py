# -*- coding: utf-8 -*-
"""把代码名回填为中文/官方名称（v2_instrument 主数据）。

只更新 name 为空或 name=code 的行；找不到名称保留代码，不造假。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\security_name_backfill.json")


def variants(market: str, code: str) -> list[str]:
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
            suf = ".SH" if base.startswith(("6", "68")) else (".BJ" if base.startswith(("4", "8", "92")) else ".SZ")
            out |= {base + suf, "sh" + base, "sz" + base, "bj" + base}
    elif market == "HK":
        if base.isdigit():
            out |= {base.zfill(5), base.zfill(5) + ".HK", base + ".HK"}
    elif market == "US":
        out |= {base.upper()}
    return sorted(x for x in out if x)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        inst = {}
        for m, sym, name in c.execute(sa.text("SELECT market, symbol, name FROM v2_instrument WHERE name IS NOT NULL AND name <> ''")).all():
            inst[(str(m), str(sym))] = str(name)

        tables = [
            ("v2_selection_pool", "market", "code"),
            ("v2_selection_pick", "market", "code"),
            ("v2_acad1_pool", "market", "code"),
            ("v2_base_signal", "market", "code"),
            ("v2_stock_research", None, "code"),
            ("v2_strategy_observation", None, "code"),
        ]
        report = {}
        for table, mcol, ccol in tables:
            cols = f"{mcol}, {ccol}, name" if mcol else f"{ccol}, name"
            rows = c.execute(sa.text(f"SELECT {cols} FROM {table}")).mappings().all()
            updates, unresolved = [], []
            for r in rows:
                code = r[ccol]
                markets = [r[mcol]] if mcol else ["CN", "HK", "US", "CRYPTO"]
                found = None
                for m in markets:
                    for v in variants(m, code):
                        if (m, v) in inst:
                            found = inst[(m, v)]
                            break
                    if found:
                        break
                if not found:
                    unresolved.append(str(code))
                    continue
                if str(r.get('name') or '') != found:
                    updates.append({"m": (r.get(mcol) if mcol else None), "c": code, "n": found})
            if args.write and updates:
                with e.begin() as w:
                    if mcol:
                        w.execute(sa.text(f"UPDATE {table} SET name=:n WHERE {mcol}=:m AND {ccol}=:c"), updates)
                    else:
                        w.execute(sa.text(f"UPDATE {table} SET name=:n WHERE {ccol}=:c"),
                                  [{"c": x["c"], "n": x["n"]} for x in updates])
            report[table] = {"candidates": len(rows), "resolved": len(updates),
                             "unresolved": len(unresolved), "unresolved_sample": unresolved[:10]}
    OUT.write_text(json.dumps({"read_only": not args.write, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"written": args.write, "report": report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
