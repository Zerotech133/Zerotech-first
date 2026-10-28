# -*- coding: utf-8 -*-
"""ui_ux_audit.py — 静态 UI/UX 审计（设计令牌/可访问性/状态/动效/内联样式）。

不是视觉回归，也不替代人工评审；用于快速发现「设计系统漂移」：
- HTML 是否带 lang / viewport / tokens.css；
- 是否直接硬编码 hex（tokens.css 之外）；
- 是否使用 CSS 变量 var(--...)；
- 是否有 aria-/role/focus-visible/aria-live/tabindex；
- 是否有空/加载/错误状态与 skeleton；
- 是否有 prefers-reduced-motion；
- 内联 style 数量（维护性/主题切换风险）。

用法：
  python tools/ui_ux_audit.py portal/os_entry_v9_prod.html portal/static/nav.js --markdown
  python tools/ui_ux_audit.py portal/static/tokens.css --json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
INLINE_STYLE_RE = re.compile(r"style\s*=\s*[\"']")
VAR_RE = re.compile(r"var\(\s*--")


def scan_text(path: str, text: str) -> dict:
    name = Path(path).name.lower()
    is_tokens = name == "tokens.css"
    raw_hex = len(HEX_RE.findall(text))
    inline_style = len(INLINE_STYLE_RE.findall(text))
    metrics = {
        "path": str(path),
        "bytes": len(text.encode("utf-8")),
        "lines": text.count("\n") + 1,
        "raw_hex": raw_hex,
        "css_vars": len(VAR_RE.findall(text)),
        "inline_style": inline_style,
        "aria": text.count("aria-"),
        "role": text.count("role="),
        "focus_visible": text.count("focus-visible"),
        "aria_live": text.count("aria-live"),
        "tabindex": text.count("tabindex"),
        "reduced_motion": text.count("prefers-reduced-motion"),
        "skeleton": text.lower().count("skeleton"),
        "loading": text.lower().count("loading"),
        "empty_state": text.lower().count("empty"),
        "lang": bool(re.search(r"<html[^>]*\blang\s*=", text, re.I)),
        "viewport": bool(re.search(r'name\s*=\s*["\']viewport', text, re.I)),
        "tokens_link": bool(re.search(r"tokens\.css", text, re.I)),
        "is_tokens": is_tokens,
    }
    findings = []

    def add(code, level, msg):
        findings.append({"code": code, "level": level, "message": msg})

    if not is_tokens and raw_hex > 0:
        add("raw_hex", "warn",
            "硬编码颜色 %d 处：改用 tokens.css 语义变量（--color-*/--gray-*）" % raw_hex)
    if is_tokens and raw_hex > 0:
        add("token_primitives", "info",
            "tokens 文件含 %d 个原始色值（作为单一事实源允许）" % raw_hex)
    if inline_style > 0:
        add("inline_style", "warn",
            "内联 style %d 处：主题切换/维护性风险，迁到 class 或 tokens" % inline_style)
    if path.endswith(".html"):
        if not metrics["lang"]:
            add("html_lang", "warn", "缺少 <html lang>：影响读屏与断词")
        if not metrics["viewport"]:
            add("viewport", "warn", "缺少 viewport meta：移动端缩放异常")
        if not metrics["tokens_link"]:
            add("tokens_link", "warn", "未引用 tokens.css：违反全站设计令牌单一事实源")
        if metrics["aria"] == 0 and metrics["role"] == 0:
            add("a11y", "warn", "未见 aria-/role：交互组件对辅助技术语义不足")
        if metrics["focus_visible"] == 0:
            add("focus", "warn", "未见 focus-visible：键盘用户焦点不可见")
        if metrics["aria_live"] == 0:
            add("live", "info", "未见 aria-live：异步加载/错误状态可能无播报")
        if metrics["skeleton"] == 0 and metrics["loading"] > 0:
            add("skeleton", "info", "有 loading 文案但无 skeleton：首屏/刷新体验可提升")
        if metrics["reduced_motion"] == 0:
            add("motion", "info", "未处理 prefers-reduced-motion：动效无障碍缺口")
    levels = {"warn": 0, "info": 0}
    for f in findings:
        levels[f["level"]] += 1
    return {"metrics": metrics, "findings": findings, "warn": levels["warn"],
            "info": levels["info"]}


def scan_file(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"path": str(p), "error": "not found"}
    return scan_text(str(p), p.read_text(encoding="utf-8", errors="replace"))


def to_markdown(results: list[dict]) -> str:
    lines = ["# UI/UX 静态审计", ""]
    for r in results:
        if r.get("error"):
            lines.append("- `%s` — %s" % (r["path"], r["error"]))
            continue
        m = r["metrics"]
        lines.append("## %s" % m["path"])
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        for k in ("bytes", "raw_hex", "css_vars", "inline_style", "aria", "role",
                  "focus_visible", "aria_live", "skeleton", "reduced_motion"):
            lines.append("| %s | %s |" % (k, m[k]))
        lines.append("")
        lines.append("发现：")
        if not r["findings"]:
            lines.append("- 无")
        for f in r["findings"]:
            lines.append("- [%s] %s — %s" % (f["level"], f["code"], f["message"]))
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="UI/UX static audit")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args(argv)
    results = [scan_file(f) for f in args.files]
    if args.markdown:
        print(to_markdown(results))
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
