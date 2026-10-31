#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portal_api 路由清单/一致性基线（A5 B0，2026-10-31 关羽）。

用途：把 `portal/portal_api.py` Handler.do_GET/do_POST 内的路径分派提取为**可核对的清单**，
冻结为基线；后续拆分批次（纯移动/路由表化）先跑 `--check`，路由集合零 diff 才允许合并。

用法：
  python tools/portal_route_inventory.py --json            # 打印清单
  python tools/portal_route_inventory.py --write-baseline  # 写冻结基线
  python tools/portal_route_inventory.py --check           # 与基线比对（差异 exit 1）
  python tools/portal_route_inventory.py --md              # markdown 报告
参数：
  --source PATH    默认 portal/portal_api.py
  --baseline PATH  默认 tools/portal_route_baseline.json
退出码：0 一致/成功；1 不一致或错误。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    # 2026-10-31 P132：GBK 控制台直跑时中文/✅ 输出会 UnicodeEncodeError；统一 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "portal" / "portal_api.py"
DEFAULT_BASELINE = REPO / "tools" / "portal_route_baseline.json"

_ROUTE_RE = re.compile(
    r"\b(?:self\.path|parsed\.path|u\.path|path)\s*==\s*[\"']([^\"']+)[\"']")
_PREFIX_RE = re.compile(
    r"\b(?:self\.path|parsed\.path|u\.path|path)\.startswith\(\s*[\"']([^\"']+)[\"']")


def _handler_methods(source: str):
    """返回 {method_name: (start_line, end_line)}；找不到 Handler 返回 {}。"""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Handler":
            out = {}
            for m in node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[m.name] = (m.lineno, m.end_lineno)
            return out
    return {}


def table_exact_routes(path) -> list:
    """解析 portal/route_table.py 的 _GET_EXACT_* 字典键（B4a 表化后路由仍在）。"""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    routes = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        t = node.targets[0]
        if not (isinstance(t, ast.Name) and t.id.startswith("_GET_EXACT_")):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k in node.value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                routes.append(k.value)
    return routes


def extract_routes(source: str) -> dict:
    """纯函数：从源码提取路由清单。

    返回 {"GET": {"exact": [...], "prefix": [...]}, "POST": {...}, "total": int}；
    每条为 {"route","kind","line"}（按 (kind,route) 去重，保留首次出现行号）。
    """
    lines = source.splitlines()
    methods = _handler_methods(source)
    out = {"GET": {"exact": [], "prefix": []}, "POST": {"exact": [], "prefix": []},
           "total": 0}
    for meth in ("do_GET", "do_POST"):
        span = methods.get(meth)
        if not span:
            continue
        start, end = span
        body = lines[start - 1:end]
        seen = set()
        for idx, ln in enumerate(body, start=start):
            for kind, rx in (("exact", _ROUTE_RE), ("prefix", _PREFIX_RE)):
                for m in rx.finditer(ln):
                    route = m.group(1).strip()
                    if not route or (kind, route) in seen:
                        continue
                    seen.add((kind, route))
                    out[meth[3:]]["%s" % kind].append(
                        {"route": route, "kind": kind, "line": idx})
    for meth in ("GET", "POST"):
        out[meth]["exact"].sort(key=lambda x: x["route"])
        out[meth]["prefix"].sort(key=lambda x: x["route"])
    out["total"] = sum(len(out[m][k]) for m in ("GET", "POST") for k in ("exact", "prefix"))
    return out


def signatures(inv: dict):
    """用于比对的最小签名集合：("GET","exact","/x") …"""
    sigs = set()
    for meth in ("GET", "POST"):
        for kind in ("exact", "prefix"):
            for item in inv[meth][kind]:
                sigs.add((meth, kind, item["route"]))
    return sigs


def _load_baseline(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(tuple(x) for x in data.get("signatures") or [])


def _family(route: str) -> str:
    parts = [p for p in route.split("/") if p]
    if not parts:
        return "/"
    if parts[0] == "api" and len(parts) > 1:
        return "/api/" + parts[1]
    return "/" + parts[0]


def render_md(inv: dict) -> str:
    fam = Counter()
    for meth in ("GET", "POST"):
        for kind in ("exact", "prefix"):
            for item in inv[meth][kind]:
                fam[_family(item["route"])] += 1
    lines = ["# portal_api 路由清单（A5 B0）", "",
             "> 自动生成：`python tools/portal_route_inventory.py --md`",
             "> 生成时间：%s" % time.strftime("%Y-%m-%dT%H:%M:%S"), "",
             "## 汇总", "",
             "| 方法 | exact | prefix | 小计 |", "|---|---:|---:|---:|"]
    for meth in ("GET", "POST"):
        e = len(inv[meth]["exact"])
        p = len(inv[meth]["prefix"])
        lines.append("| %s | %d | %d | %d |" % (meth, e, p, e + p))
    lines.append("| **合计** | | | **%d** |" % inv["total"])
    lines += ["", "## 路由族（前二段）", "", "| 族 | 条数 |", "|---|---:|"]
    for k, v in fam.most_common(30):
        lines.append("| `%s` | %d |" % (k, v))
    lines += ["", "## exact 路由（全部）", ""]
    for meth in ("GET", "POST"):
        lines.append("### %s" % meth)
        for item in inv[meth]["exact"]:
            lines.append("- `%s` (L%d)" % (item["route"], item["line"]))
        lines.append("")
    lines += ["## prefix 路由（全部）", ""]
    for meth in ("GET", "POST"):
        for item in inv[meth]["prefix"]:
            lines.append("- %s `%s*` (L%d)" % (meth, item["route"], item["line"]))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="portal_api 路由清单/一致性基线")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--md-out", default="", help="把 markdown 报告写入指定路径")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    src_path = Path(args.source)
    if not src_path.is_file():
        print("source 不存在: %s" % src_path, file=sys.stderr)
        return 1
    inv = extract_routes(src_path.read_text(encoding="utf-8"))
    # B4a：把 route_table.py 中已表化的 GET exact 路由并入清单（否则会误报"丢失路由"）
    _tbl = src_path.parent / "route_table.py"
    if _tbl.is_file():
        have = {x["route"] for x in inv["GET"]["exact"]}
        for route in table_exact_routes(_tbl):
            if route not in have:
                inv["GET"]["exact"].append({"route": route, "kind": "exact", "line": 0})
                have.add(route)
        inv["GET"]["exact"].sort(key=lambda x: x["route"])
        inv["total"] = sum(len(inv[m][k]) for m in ("GET", "POST") for k in ("exact", "prefix"))
    sigs = signatures(inv)

    if args.write_baseline:
        out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "source": str(src_path), "total": inv["total"],
               "signatures": sorted([list(s) for s in sigs])}
        Path(args.baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.baseline).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                       encoding="utf-8")
        print("baseline written: %d routes -> %s" % (len(sigs), args.baseline))
        return 0

    if args.check:
        bp = Path(args.baseline)
        if not bp.is_file():
            print("baseline 不存在: %s（先跑 --write-baseline）" % bp, file=sys.stderr)
            return 1
        base = _load_baseline(bp)
        lost, added = base - sigs, sigs - base
        if not lost and not added:
            print("✅ ROUTE INVENTORY OK: %d 条与基线零 diff" % len(sigs))
            return 0
        if lost:
            print("❌ 丢失路由 %d:" % len(lost), sorted(lost)[:10])
        if added:
            print("❌ 新增路由 %d:" % len(added), sorted(added)[:10])
        return 1

    if args.md or args.md_out:
        md = render_md(inv)
        if args.md_out:
            out = Path(args.md_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8", newline="\n")
            print("md -> %s (%d routes)" % (out, inv["total"]))
        if args.md:
            print(md)
        return 0

    if args.json:
        print(json.dumps(inv, ensure_ascii=False, indent=1))
        return 0

    fam = Counter(_family(i["route"]) for m in ("GET", "POST")
                  for k in ("exact", "prefix") for i in inv[m][k])
    print("portal_route_inventory: total=%d GET=%d POST=%d families=%d"
          % (inv["total"],
             len(inv["GET"]["exact"]) + len(inv["GET"]["prefix"]),
             len(inv["POST"]["exact"]) + len(inv["POST"]["prefix"]), len(fam)))
    for k, v in fam.most_common(12):
        print("  %-28s %d" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
