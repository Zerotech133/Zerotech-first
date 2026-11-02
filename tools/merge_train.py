#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge train 门（W1/UF-09，ADR-015/ADGS v1.1 §9.8）——独立工具，纯 stdlib、无副作用。

规则（时区 Asia/Shanghai = 固定 UTC+8，中国无夏令时）：
  · 主合并窗口：16:30–17:00（左闭右开）；
  · 次合并窗口：21:40–22:10（左闭右开）；
  · 禁并期：09:15–15:05、17:10–18:40、20:00–20:20、21:20–21:40（左闭右开）；
  · 文档/组合声明旁路：仅改动 docs/**、deliverables/**、compositions/** 或任意 *.md 时
    随时允许合并（优先级最高：禁并期也可合）；
  · 窗口外只提交不合；紧急事故按 L2/ADGS 例外分级处理，本工具只提供 --force 显式放行
    （打印警告，要求工单回单留痕），不做任何写操作。

子命令：
  status                现在是否可合、原因、当前/下一窗口、禁并期
  check [--files a,b]   allowed/blocked 判定；退出码 0=allowed / 1=blocked
公共开关：
  --json                stdout 输出可解析 JSON（人类提示走 stderr）
  --now ISO             指定“现在”（测试用；如 2026-11-02T16:45:00+08:00，裸时间按 Asia/Shanghai）
  --force               紧急放行：allowed=true + 醒目警告（须工单留痕）

退出码：0 allowed / status 正常；1 blocked（仅 check）；2 参数/环境错误。
用法：
  python -X utf8 tools/merge_train.py status
  python -X utf8 tools/merge_train.py status --json
  python -X utf8 tools/merge_train.py check --files portal/api_kg.py
  python -X utf8 tools/merge_train.py check --files docs/a.md,compositions/x/manifest.yaml --json
  python -X utf8 tools/merge_train.py check --now 2026-11-02T16:45:00+08:00
  python -X utf8 tools/merge_train.py check --force --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timedelta, timezone

SCHEMA = "zerobound/merge-train/v1"
TZ = timezone(timedelta(hours=8), "Asia/Shanghai")  # 固定 UTC+8（无 DST）

# 窗口/禁并期定义：仅时分，左闭右开；跨午夜不支持（当前规则均为同日区间）
WINDOWS = (
    {"name": "main", "label": "主窗口", "start": time(16, 30), "end": time(17, 0)},
    {"name": "secondary", "label": "次窗口", "start": time(21, 40), "end": time(22, 10)},
)
FORBIDDEN = (
    {"label": "业务时段", "start": time(9, 15), "end": time(15, 5)},
    {"label": "收盘后时段", "start": time(17, 10), "end": time(18, 40)},
    {"label": "晚餐/交接时段", "start": time(20, 0), "end": time(20, 20)},
    {"label": "次窗口前缓冲", "start": time(21, 20), "end": time(21, 40)},
)
# 文档/组合声明旁路前缀（+ 任意 *.md）；与 ADGS §9.8“文档/组合声明随时合并”同口径
DOC_PREFIXES = ("docs/", "deliverables/", "compositions/")
DOC_SUFFIX_MD = ".md"

FORCE_WARNING = ("紧急放行（--force）：仅限回滚/紧急事故（ADGS §9.10 L2 例外）；"
                 "须在工单回单写明五要素与批准人，24h 内复盘；禁止日常绕过。")


# ---------- 时间工具 ----------
def now_local() -> datetime:
    return datetime.now(TZ)


def parse_now(text: str | None) -> datetime:
    """解析 --now（ISO 8601；无时区按 Asia/Shanghai；也兼容 'YYYY-MM-DD HH:MM[:SS]'）。"""
    if not text:
        return now_local()
    raw = str(text).strip()
    dt = None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError("无法解析 --now：%r（示例 2026-11-02T16:45:00+08:00）" % text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _at(now: datetime, t: time) -> datetime:
    return datetime.combine(now.date(), t, tzinfo=TZ)


def _fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------- 规则判定 ----------
def window_for(now: datetime) -> dict | None:
    """当前所处合并窗口（左闭右开）；不在窗口返回 None。"""
    for w in WINDOWS:
        if _at(now, w["start"]) <= now < _at(now, w["end"]):
            return w
    return None


def forbidden_for(now: datetime) -> dict | None:
    """当前所处禁并期（左闭右开）；不在禁并期返回 None。"""
    for f in FORBIDDEN:
        if _at(now, f["start"]) <= now < _at(now, f["end"]):
            return f
    return None


def next_window(now: datetime) -> dict:
    """下一个“开始时间严格晚于 now”的窗口（含跨日）。"""
    best = None
    for w in WINDOWS:
        cand = _at(now, w["start"])
        if cand <= now:
            cand = cand + timedelta(days=1)
        if best is None or cand < best[0]:
            best = (cand, w)
    cand, w = best
    return {"name": w["name"], "label": w["label"],
            "start": _iso(cand), "start_hm": _fmt_time(w["start"]),
            "end_hm": _fmt_time(w["end"])}


def _window_info(w: dict | None) -> dict | None:
    if not w:
        return None
    return {"name": w.get("name"), "label": w["label"],
            "start_hm": _fmt_time(w["start"]), "end_hm": _fmt_time(w["end"])}


def classify_files(files) -> tuple[bool, list, list]:
    """返回 (是否全部走文档旁路, 旁路文件, 非旁路文件)。空列表不算旁路。"""
    docs, code = [], []
    for raw in files or []:
        p = str(raw or "").replace("\\", "/").strip()
        while p.startswith("./"):
            p = p[2:]
        p = p.lstrip("/")
        if not p:
            continue
        if p.startswith(DOC_PREFIXES) or p.lower().endswith(DOC_SUFFIX_MD):
            docs.append(p)
        else:
            code.append(p)
    return (bool(docs) and not code, docs, code)


def _next_after_forbidden(now: datetime, f: dict) -> dict:
    """当前禁并期的结束时间（用于 forbidden_until 输出）。"""
    return {"label": f["label"], "until": _iso(_at(now, f["end"]))}


def evaluate(now: datetime, files=None, force: bool = False) -> dict:
    """产出 merge train 判定（纯函数，无副作用）。"""
    files = [str(f) for f in (files or []) if str(f).strip()]
    bypass, bypass_files, code_files = classify_files(files)
    cur_window = window_for(now)
    cur_forbidden = forbidden_for(now)
    nxt = next_window(now)

    allowed = False
    mode = "outside_window"
    reason = ""
    if force:
        allowed, mode = True, "force"
        reason = FORCE_WARNING
    elif bypass:
        allowed, mode = True, "docs_bypass"
        reason = ("文档/组合声明旁路：改动仅含 docs/**、deliverables/**、compositions/** "
                  "或 *.md（%d 个文件），随时允许合并" % len(bypass_files))
    elif cur_window is not None:
        allowed, mode = True, "window"
        reason = ("%s %s–%s（Asia/Shanghai）内，允许合并"
                  % (cur_window["label"], _fmt_time(cur_window["start"]),
                     _fmt_time(cur_window["end"])))
    elif cur_forbidden is not None:
        mode = "forbidden"
        reason = ("禁并期 %s–%s（%s）：窗口外只提交不合，禁止合并"
                  % (_fmt_time(cur_forbidden["start"]), _fmt_time(cur_forbidden["end"]),
                     cur_forbidden["label"]))
    else:
        reason = ("当前不在合并窗口（窗口外只提交不合）；下一%s %s"
                  % (nxt["label"], nxt["start"]))

    payload = {
        "schema": SCHEMA,
        "tool": "merge_train",
        "now": _iso(now),
        "timezone": "Asia/Shanghai (UTC+8, no DST)",
        "allowed": allowed,
        "mode": mode,
        "reason": reason,
        "window": _window_info(cur_window),
        "next_window": nxt,
        "forbidden_until": (_next_after_forbidden(now, cur_forbidden)["until"]
                            if cur_forbidden else None),
        "forbidden": _window_info(cur_forbidden) if cur_forbidden else None,
        "force": bool(force),
        "force_warning": FORCE_WARNING if force else None,
        "files": files,
        "bypass_files": bypass_files,
        "non_bypass_files": code_files,
        "windows": [{"name": w["name"], "label": w["label"],
                     "start_hm": _fmt_time(w["start"]),
                     "end_hm": _fmt_time(w["end"])} for w in WINDOWS],
        "forbidden_periods": [{"label": f["label"],
                               "start_hm": _fmt_time(f["start"]),
                               "end_hm": _fmt_time(f["end"])} for f in FORBIDDEN],
        "doc_bypass": {"prefixes": list(DOC_PREFIXES), "suffixes": [DOC_SUFFIX_MD]},
    }
    return payload


# ---------- 输出 ----------
def _print_status_text(p: dict) -> None:
    print("== merge train 状态（%s）==" % p["timezone"])
    print("  现在时间 : %s" % p["now"])
    print("  是否可合 : %s" % ("YES" if p["allowed"] else "NO"))
    print("  判定模式 : %s" % p["mode"])
    print("  原因     : %s" % p["reason"])
    w = p["window"]
    print("  当前窗口 : %s" % (("%s %s–%s" % (w["label"], w["start_hm"], w["end_hm"]))
                              if w else "（不在窗口内）"))
    n = p["next_window"]
    print("  下一窗口 : %s %s（%s–%s）" % (n["label"], n["start"], n["start_hm"], n["end_hm"]))
    print("  禁并期   : %s" % (("是，%s 至 %s" % (p["forbidden"]["label"], p["forbidden_until"]))
                              if p["forbidden_until"] else "否（当前不在禁并期）"))
    print("  窗口表   : " + "；".join("%s %s–%s" % (w["label"], w["start_hm"], w["end_hm"])
                                     for w in p["windows"]))
    print("  禁并期表 : " + "；".join("%s–%s" % (f["start_hm"], f["end_hm"])
                                     for f in p["forbidden_periods"]))
    print("  文档旁路 : docs/**、deliverables/**、compositions/**、*.md 随时允许")
    if p["force"]:
        print("  ⚠ %s" % FORCE_WARNING)


def _print_check_text(p: dict) -> None:
    mark = "ALLOWED" if p["allowed"] else "BLOCKED"
    print("[%s] merge train（%s）" % (mark, p["timezone"]))
    print("  现在时间 : %s" % p["now"])
    print("  原因     : %s" % p["reason"])
    n = p["next_window"]
    if not p["allowed"]:
        print("  下一窗口 : %s %s（%s–%s）" % (n["label"], n["start"], n["start_hm"], n["end_hm"]))
    if p["files"]:
        print("  改动文件 : %d（旁路 %d / 非旁路 %d）"
              % (len(p["files"]), len(p["bypass_files"]), len(p["non_bypass_files"])))
    if p["forbidden_until"]:
        print("  禁并期至 : %s" % p["forbidden_until"])
    if p["force"]:
        print("  ⚠ %s" % FORCE_WARNING)


def cmd_status(args) -> int:
    payload = evaluate(parse_now(args.now), files=None, force=bool(args.force))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print_status_text(payload)
    return 0


def cmd_check(args) -> int:
    files = []
    if args.files:
        files = [x for x in str(args.files).split(",") if x.strip()]
    payload = evaluate(parse_now(args.now), files=files, force=bool(args.force))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print_check_text(payload)
    return 0 if payload["allowed"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="merge train 门（W1/UF-09）：窗口/禁并期/文档组合旁路判定（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("status", help="现在是否可合、当前/下一窗口、禁并期")
    st.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    st.add_argument("--now", default=None, help="指定现在（ISO 8601，测试用）")
    st.add_argument("--force", action="store_true", help="紧急放行（须留痕）")

    ck = sub.add_parser("check", help="allowed/blocked 判定（rc=0/1）")
    ck.add_argument("--files", default=None, help="逗号分隔的改动文件（用于文档/组合旁路）")
    ck.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    ck.add_argument("--now", default=None, help="指定现在（ISO 8601，测试用）")
    ck.add_argument("--force", action="store_true", help="紧急放行（须留痕）")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "check":
            return cmd_check(args)
    except ValueError as exc:
        print("[merge_train] ERROR: %s" % exc, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
