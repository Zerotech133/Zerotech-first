# -*- coding: utf-8 -*-
"""校验数据仓原型 v2 产物：结构/数据契约/依赖/无外部地址/大小。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTML = REPO / "portal" / "static" / "dw_v2.html"
DATA = REPO / "portal" / "static" / "dw_v2_data.json"
REQUIRED_IDS = [
    "page-title", "hero-chips", "kpi-grid", "freshness-banner", "domain-grid",
    "tasks-body", "map-search", "map-domain", "map-status", "map-freq",
    "map-body", "map-total", "query-form", "q-dataset", "q-output",
    "quality-bars", "fresh-body", "accept-body", "lineage-flow",
    "governance-principles", "governance-pending", "wip-body", "drawer",
]
REQUIRED_REFS = ['/static/tokens.css', '/static/nav.js']
VIEWS = ["overview", "map", "query", "quality", "lineage", "governance"]


def extract_data(html: str) -> dict:
    m = re.search(r"const DW_DATA = (\{.*?\});\s*\(function", html, re.S)
    if not m:
        m = re.search(r"const DW_DATA = (\{.*?\});", html, re.S)
    if not m:
        raise ValueError("未找到内嵌 DW_DATA")
    return json.loads(m.group(1))


def check(html_path: Path = HTML, data_path: Path = DATA) -> dict:
    out = {"ok": True, "issues": [], "warnings": [], "facts": {}}
    if not html_path.exists():
        out["ok"] = False
        out["issues"].append("HTML 缺失: %s" % html_path)
        return out
    html = html_path.read_text(encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    out["facts"]["html_kb"] = round(size_kb, 1)
    if size_kb > 400:
        out["issues"].append("HTML 体积 %.1fKB 超过 400KB 上限" % size_kb)
    if html_path.stat().st_size > 0 and not data_path.exists():
        out["warnings"].append("data JSON 缺失（HTML 内嵌数据仍可预览）")

    for i in REQUIRED_IDS:
        if ('id="%s"' % i) not in html:
            out["issues"].append("缺少必需元素 id=%s" % i)
    for r in REQUIRED_REFS:
        if r not in html:
            out["issues"].append("缺少设计系统依赖: %s" % r)
    if "__DW_DATA_JSON__" in html:
        out["issues"].append("模板占位 __DW_DATA_JSON__ 未替换")
    if not re.search(r"<title>[^<]*数据仓[^<]*</title>", html):
        out["issues"].append("title 不含「数据仓」")
    if 'name="prototype" content="dw-v2"' not in html:
        out["warnings"].append("缺少 prototype meta")

    tabs = re.findall(r'class="tab"[^>]*data-view="([a-z]+)"', html)
    out["facts"]["tabs"] = tabs
    if tabs != VIEWS:
        out["issues"].append("视图集合不符: %r != %r" % (tabs, VIEWS))
    panels = re.findall(r'id="view-([a-z]+)"', html)
    if sorted(panels) != sorted(VIEWS):
        out["issues"].append("panel 集合不符: %r" % (panels,))

    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    var_ext = re.findall(r'fetch\(\s*"(https?://[^"]*)"', html)
    out["facts"]["external_refs"] = len(ext) + len(var_ext)
    if ext or var_ext:
        out["issues"].append("存在外部 URL 依赖: %r" % (ext + var_ext)[:4])
    if "<script src=\"http" in html:
        out["issues"].append("外部 script 依赖")

    try:
        data = extract_data(html)
    except (ValueError, json.JSONDecodeError) as exc:
        out["issues"].append("内嵌 DW_DATA 解析失败: %s" % exc)
        data = {}
    if data:
        for key, minimum in (("datasets", 200), ("domains", 5), ("kpis", 6),
                             ("query_examples", 3), ("governance", 1)):
            v = data.get(key)
            n = len(v) if hasattr(v, "__len__") else (1 if v else 0)
            out["facts"]["count_%s" % key] = n
            if n < minimum:
                out["issues"].append("数据字段 %s=%d < %d" % (key, n, minimum))
        meta = data.get("meta") or {}
        out["facts"]["catalog_version"] = meta.get("catalog_version")
        out["facts"]["physical"] = meta.get("physical")
        if not meta.get("physical"):
            out["issues"].append("meta.physical 缺失/为 0")
        ds = data.get("datasets") or []
        if not any(x.get("status") == "unknown" for x in ds):
            out["warnings"].append("数据集中无 unknown 样本（校验覆盖面偏窄）")
    out["ok"] = not out["issues"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验数据仓原型 v2")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = check()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print("dw_v2 prototype check: %s" % ("PASS" if r["ok"] else "FAIL"))
        for i in r["issues"]:
            print("  ISSUE", i)
        for w in r["warnings"]:
            print("  WARN ", w)
        print("  facts", json.dumps(r["facts"], ensure_ascii=False))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
