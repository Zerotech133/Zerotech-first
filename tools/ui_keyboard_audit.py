# -*- coding: utf-8 -*-
"""ui_keyboard_audit.py — 静态键盘焦点顺序审计（无浏览器依赖）。

按 DOM 源码顺序列出可聚焦元素（a[href]/button/input/select/textarea/summary/
[tabindex!=-1]），用于检查：
- skip link 是否第一个；
- Tab 顺序是否与视觉/信息层级一致；
- 是否存在 tabindex>0 的反模式。

注意：这是静态近似；真实 Tab 顺序仍建议浏览器人工抽查。
"""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser


FOCUS_TAGS = {"a", "button", "input", "select", "textarea", "summary"}


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self._hidden = 0
        self._cur = None
        self._tabindex_warn = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hidden = ("hidden" in a) or a.get("aria-hidden") == "true"
        if hidden:
            self._hidden += 1
            return
        if self._hidden:
            return
        tabindex = a.get("tabindex")
        if tabindex and tabindex.lstrip("-").isdigit() and int(tabindex) > 0:
            self._tabindex_warn.append(tag)
        focusable = tag in FOCUS_TAGS
        if tag == "a" and not a.get("href"):
            focusable = False
        if tag == "input" and a.get("type", "").lower() == "hidden":
            focusable = False
        if tabindex not in (None, "", "-1"):
            focusable = True
        if focusable:
            self._cur = {
                "tag": tag,
                "label": a.get("aria-label") or a.get("title") or a.get("value") or "",
                "text": "",
                "tabindex": tabindex or "",
            }
            self.items.append(self._cur)

    def handle_endtag(self, tag):
        if self._hidden:
            self._hidden -= 1
            return
        self._cur = None

    def handle_data(self, data):
        if self._cur is not None and data.strip():
            self._cur["text"] = (self._cur["text"] + " " + data.strip()).strip()


def audit(path: str) -> dict:
    p = _Parser()
    with open(path, encoding="utf-8", errors="replace") as f:
        p.feed(f.read())
    order = []
    for i, it in enumerate(p.items, 1):
        label = it["label"] or it["text"] or "(无文本)"
        order.append({
            "order": i, "tag": it["tag"], "label": label[:80],
            "tabindex": it["tabindex"],
        })
    return {
        "path": path,
        "count": len(order),
        "order": order,
        "positive_tabindex": p._tabindex_warn,
        "skip_first": bool(order and order[0]["tag"] == "a"
                           and ("跳到" in order[0]["label"]
                                or "跳过" in order[0]["label"])),
    }


def to_markdown(report: dict) -> str:
    lines = ["# 键盘焦点顺序审计", "", "文件：`%s`（可聚焦 %d 个）" %
             (report["path"], report["count"]), ""]
    lines.append("| # | 标签 | 文本/aria-label | tabindex |")
    lines.append("|---|---|---|---|")
    for it in report["order"]:
        lines.append("| %d | %s | %s | %s |" % (
            it["order"], it["tag"], it["label"].replace("|", "\\|"), it["tabindex"]))
    if report["positive_tabindex"]:
        lines.append("")
        lines.append("⚠ tabindex>0 反模式：%s" % ", ".join(report["positive_tabindex"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="static keyboard focus-order audit")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    rep = audit(args.file)
    if args.markdown:
        print(to_markdown(rep))
    else:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
