"""contacts_store.py — 商业关系库（客户/投资人/VP/文档档案）。

设计：jsonl 账本（contacts.jsonl），一条一联系人；
议会厅纪要喂入：--mine 从 council_meetings/*/minutes.md 提及的人名/文档自动补档（只建档不编造，来源标注）。
手动登记：--add '{"name":"张总","role":"客户","note":"AI平台采购意向"}'
边界：个人信息本机存储不外传；议会厅喂入仅提取已公开说的内容，不推测。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "data", "contacts.jsonl")
DOCS_DIR = os.path.join(HERE, "..", "deliverables")

# 已知商业人物关键词（喂入挖掘用；宁缺勿滥——只认明确称呼）
_PATTERNS = [
    (re.compile(r"(投资人|投资机构|基金合伙人)"), "投资人"),
    (re.compile(r"(潜在客户|客户方|采购意向)"), "客户"),
    (re.compile(r"(VP|副总裁|合作方对接)"), "合作VP"),
]
_DOC_PATTERNS = [
    (re.compile(r"(BP|商业计划书)[\s，,]*v?(\d+(?:\.\d+)?)?"), "BP·商业计划书"),
    (re.compile(r"(项目书|项目方案书)"), "项目书"),
    (re.compile(r"(研发方案)"), "研发方案"),
]


def load(n: int = 50) -> list[dict]:
    if not os.path.isfile(LEDGER):
        return []
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[::-1][:n]


def add(rec: dict) -> dict:
    rec = {
        "name": str(rec.get("name", ""))[:30],
        "role": str(rec.get("role", "联系"))[:10],
        "note": str(rec.get("note", ""))[:200],
        "since": rec.get("since") or __import__("time").strftime("%Y-%m-%d"),
        "src": rec.get("src", "manual"),
    }
    if not rec["name"]:
        raise ValueError("name required")
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    if any(r.get("name") == rec["name"] for r in load(500)):
        return {"dup": True, "rec": rec}
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"dup": False, "rec": rec}


def mine_meetings() -> list[dict]:
    """从议会厅纪要挖人物/文档提及（只登记明确说法，src=council-mined）。"""
    root = os.path.join(HERE, "data", "council_meetings")
    found: dict[str, dict] = {}
    if os.path.isdir(root):
        for month in os.listdir(root):
            mdir = os.path.join(root, month)
            if not os.path.isdir(mdir):
                continue
            for name in os.listdir(mdir):
                fp = os.path.join(mdir, name, "minutes.md")
                if not os.path.isfile(fp):
                    continue
                try:
                    text = open(fp, encoding="utf-8").read()
                except OSError:
                    continue
                for pat, role in _PATTERNS:
                    for m in pat.finditer(text):
                        ctx = text[max(0, m.start() - 20) : m.end() + 20].replace(
                            "\n", " "
                        )
                        key = "纪要提及·" + role
                        found.setdefault(key, {"note_ctx": []})["note_ctx"].append(
                            ctx[:60]
                        )
                for pat, label in _DOC_PATTERNS:
                    if pat.search(text):
                        found.setdefault("文档·" + label, {"note_ctx": []})
    return [
        {
            "name": k,
            "role": "线索",
            "note": "；".join(v["note_ctx"][:2])[:180],
            "src": "council-mined",
        }
        for k, v in found.items()
    ]


def docs_inventory() -> list[dict]:
    """deliverables 目录 BP/项目书/研发方案类文档盘点。"""
    out = []
    if os.path.isdir(DOCS_DIR):
        for f in sorted(os.listdir(DOCS_DIR)):
            if f.endswith((".md", ".html")) and any(
                k in f for k in ("BP", "商业计划", "项目", "研发方案", "产品")
            ):
                out.append({"file": f[:60], "path": os.path.join(DOCS_DIR, f)})
    return out[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", help='JSON: {"name":"..","role":"..","note":".."}')
    ap.add_argument("--mine", action="store_true", help="议会厅纪要喂入")
    a = ap.parse_args()
    if a.add:
        rec = json.loads(a.add)
        print(json.dumps(add(rec), ensure_ascii=False))
        return 0
    if a.mine:
        added = 0
        for c in mine_meetings():
            r = add(c)
            if not r.get("dup"):
                added += 1
        print(
            json.dumps(
                {"mined_added": added, "docs": docs_inventory()},
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    print(
        json.dumps(
            {"contacts": load(20), "docs": docs_inventory()},
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
