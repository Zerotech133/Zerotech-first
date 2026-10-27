# -*- coding: utf-8 -*-
"""旧模板调用审计（P3 观察期，2026-10-25）。

旧模板（card_build.build_card/build_card3、feishu_card_v2.build_v3/build_drill_card）
在调用时会把「调用方模块/函数/行号」追加到 portal/data/push_legacy_calls.jsonl；
本脚本按天聚合，区分白名单（非推送场景，如审批/台账卡）与待迁移。

用法：
  python push_legacy_audit.py --days 7
  python push_legacy_audit.py --days 1 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEGACY_LOG = os.path.join(HERE, "data", "push_legacy_calls.jsonl")
WHITELIST = os.path.join(HERE, "work", "push_legacy_whitelist.json")

# 2026-10-26 OS-FIX-W4AUDIT：迁移感知。审计日志是"近 N 天运行时记录"，代码迁移
# 之后旧调用记录仍会在窗口里挂 7 天——此前会把已迁移文件的历史调用误报成"待迁移"
# （W4 09:00 实例：news_collector.py 07:56 一条旧 push_card 记录，代码 09:01 已迁移）。
# 判定：①按文件名在常见源码目录找到当前源文件；②源文件含统一分装标记；③该组
# 全部记录时间早于源文件 mtime（即迁移前）→ 判"已迁移(历史)"，不列待迁移。
# 迁移后若再出现旧调用（记录晚于 mtime）仍会照常标红，不会漏。
MIGRATION_MARKERS = (
    "should_unify",
    "from_sections",
    "#unified",
    "push_unified_adapter",
    "feishu_push.",
)
SOURCE_SUBDIRS = (
    "",
    "portal",
    "news_collector",
    "tools",
    "jev-eval",
    "quant_backtest",
    "ops",
)

# 非「对外推送」场景：审批/控制/台账/本地播报等，允许继续用旧模板
DEFAULT_WHITELIST = [
    "approval_card",
    "tune_gate",
    "km_feishu_hub",
    "jev_card_action",
    "card_actions",
    "api_misc",
    "portal_api",
    "org_command_api",
    "feishu_console",
    "feishu_sop_runner",
    "card_audit",
    "broadcast",
]
# 内部工具/巡检脚本（非用户推送生产者；按脚本文件名豁免）
FILE_WHITELIST = {
    "devtask.py",
    "publish_watchdog.py",
    "a0_patrol.py",
    "execution_watchdog.py",
    "alert_forward.py",
    "refresh_agent_board.py",
    "health_check.py",
    "feishu_rollback_drill.py",
    "feishu_sop_runner.py",
    # 2026-10-26 OS-FIX-W4FINAL（老田批准执行）：JEV 图表卡含飞书 img_key
    # （图表快照），统一分装 feishu_push 不支持图片；按"非迁移场景"白名单，
    # 非通用推送生产者（随晨报/复盘附带的图表附件）。
    "news_jev_chart.py",
}


def load_whitelist():
    try:
        d = json.load(open(WHITELIST, encoding="utf-8"))
        items = d.get("modules") if isinstance(d, dict) else d
        if items:
            return set(items)
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/push_legacy_audit.py", exc)
    return set(DEFAULT_WHITELIST)


def _within(ts, days):
    import datetime as dt

    try:
        t = dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return False
    return (dt.datetime.now() - t).total_seconds() <= days * 86400


def _epoch(ts):
    import datetime as dt

    try:
        return dt.datetime.fromisoformat(str(ts)).timestamp()
    except ValueError:
        return 0.0


def _find_source(file_name, roots=None):
    """按文件名在常见源码目录找当前源文件；找不到返回 None（fail-open 不判迁移）。"""
    if not file_name or file_name == "?":
        return None
    for base in roots if roots is not None else SOURCE_SUBDIRS:
        p = os.path.join(
            base if os.path.isabs(base) else os.path.join(ROOT, base), file_name
        )
        if os.path.isfile(p):
            return p
    return None


def _source_has_unified(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read(2 * 1024 * 1024)
    except OSError:
        return False
    return any(m in src for m in MIGRATION_MARKERS)


def _is_migrated(file_name, latest_ts, roots=None):
    """源文件已迁移 且 该组记录全部早于源文件 mtime → 历史调用，不再算待迁移。"""
    path = _find_source(file_name, roots=roots)
    if not path or not _source_has_unified(path):
        return False
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return float(latest_ts or 0) <= mtime + 1.0


def audit(days=7, log_path=None, source_roots=None):
    """→ {total, groups:[{template,caller,count,whitelisted,migrated}], unlisted:[…]}。"""
    path = log_path or LEGACY_LOG
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if _within(row.get("ts"), days):
                    rows.append(row)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/push_legacy_audit.py", exc)
    wl = load_whitelist()
    grouped = {}
    for r in rows:
        key = (r.get("template") or "?", r.get("caller") or "?", r.get("file") or "?")
        grouped.setdefault(key, []).append(r)
    groups = []
    for k, rs in grouped.items():
        latest = max(_epoch(x.get("ts")) for x in rs)
        whitelisted = (k[1] in wl) or (k[2] in FILE_WHITELIST)
        migrated = _is_migrated(k[2], latest, roots=source_roots)
        groups.append(
            {
                "template": k[0],
                "caller": k[1],
                "file": k[2],
                "count": len(rs),
                "whitelisted": whitelisted,
                "migrated": migrated,
            }
        )
    groups.sort(key=lambda g: g["count"], reverse=True)
    unlisted = [g for g in groups if not g["whitelisted"] and not g["migrated"]]
    return {
        "days": days,
        "total": len(rows),
        "groups": groups,
        "unlisted": unlisted,
        "whitelist_size": len(wl),
    }


def render(res):
    lines = ["旧模板调用审计（近 %d 天 · 共 %d 次）" % (res["days"], res["total"])]
    if not res["groups"]:
        lines.append("  （无记录：所有推送均已走统一分装或闭环未触发）")
        return "\n".join(lines)
    lines.append("  模板                     调用方/脚本                 次数  白名单")
    for g in res["groups"]:
        who = g["caller"] if g["caller"] != "__main__" else g.get("file", "?")
        lines.append(
            "  %-24s %-24s %4d  %s"
            % (
                g["template"][:24],
                who[:24],
                g["count"],
                "✓"
                if g["whitelisted"]
                else ("✓ 已迁移(历史)" if g.get("migrated") else "✗ 待迁移"),
            )
        )
    if res["unlisted"]:
        lines.append(
            "待迁移：%s" % "、".join(sorted({g["caller"] for g in res["unlisted"]}))
        )
    else:
        lines.append("✓ 无待迁移调用（全部为白名单/非推送场景）")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = audit(a.days)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
