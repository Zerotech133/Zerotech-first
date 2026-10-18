# -*- coding: utf-8 -*-
"""list_rejects：codex_bridge 拒档只读浏览工具（list_rejects）。

## 为什么要它

adapters/codex_bridge.py 受理管线会把未过审提交落档（write_reject，
stage=限频/静态校验/沙箱试跑），归档路径按日组织：

    rejects/<YYYYMMDD>/<submit_id>.md        # 拒绝原因 md 档（write_reject）
    rejects/<YYYYMMDD>/<submit_id>/          # 静态门/沙箱拒绝时 move 来的 inbox 目录

目前没有工具能快速浏览「哪些提交被拒、为什么」——被拒方（Codex 工单
作者）复查、老田抽审都不方便。本工具补齐只读浏览面。

## 用法

    python tools/list_rejects.py                        # 按日分组摘要表
    python tools/list_rejects.py --show <submit_id>     # 打印该提交完整 md 档
    python tools/list_rejects.py --stats                # 按 stage 汇总计数
    python tools/list_rejects.py --rejects-root <dir>   # 显式覆盖归档根

默认自动定位 agent_platform/data/codex_bridge/rejects；兼容旧版扁平
布局（rejects 根下直接是 <submit_id>.md / <submit_id>/，无日期层）。

## 退出码

0=正常（浏览工具恒 0：无拒档=中性输出，对齐 scan_secrets 报告口径）；
2=参数/环境错误（--show 与 --stats 互斥等）。

红线：**零写面**。不创建目录、不移动文件、不写任何内容——连 mkdir
都不做（scan_secrets / verify_strategy_ledger 只读观测面判例平移）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# bridge 实际拒档落点 = _bridge_inbox_root()（adapters/codex_bridge.py:422-423）
# = adapter 上两级 / bridge_inbox / rejects；write_reject 三处调用（:476/486/493）
# 全部传它。默认根必须对齐该实现，否则不带 --rejects-root 时看不到真拒档
# （2026-10-18 L 路工单作者指南取证发现的默认根错位，lead 修正）。
DEFAULT_REJECTS_ROOT = (Path(__file__).resolve().parents[1]
                        / "bridge_inbox" / "rejects")
REASON_MAX_CHARS = 60  # 摘要表拒绝原因截断长度
STAGE_UNKNOWN = "未知"
DATE_NEVER = "(扁平)"  # 旧版无日期层归档的日期列占位


def parse_reject_md(path: Path):
    """解析拒档 md，返回 (stage, 首条原因)。

    write_reject 的档案结构（adapters/codex_bridge.py::write_reject）：
        # bridge 拒绝档：<submit_id>
        - **阶段**：<stage>
        - **命中规则**：
        1. <原因>
    容错：字段行缺失时回退「未知」/「(无命中规则行)」，不抛异常
    （浏览工具对内容变体 fail-open，只读展示不拦截）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return STAGE_UNKNOWN, "(md 档不可读)"
    stage = STAGE_UNKNOWN
    first_reason = None
    in_rules = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- **阶段**："):
            stage = stripped.replace("- **阶段**：", "").strip() or STAGE_UNKNOWN
            continue
        if stripped.startswith("- **命中规则**"):
            in_rules = True
            continue
        if in_rules and stripped and first_reason is None:
            first_reason = stripped
    if first_reason is None:
        first_reason = "(无命中规则行)"
    return stage, first_reason


def collect_records(root: Path):
    """收集全部拒档记录，返回 [(date, submit_id, stage, reason, md_path)]。

    以 md 档为准（一提交一档，adapters/codex_bridge.py 限频口径）；
    无 md 档的落档目录（异常/手放）也列出，stage 记「未知」。
    兼容两层布局：日期层 <YYYYMMDD>/ 与旧版扁平（根下直接落档）。
    """
    records = []
    if not root.is_dir():
        return records
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            if not (len(entry.name) == 8 and entry.name.isdigit()):
                # 旧版扁平：<submit_id>/ 直接在根下
                records.append((DATE_NEVER, entry.name, STAGE_UNKNOWN,
                                "(无 md 档，仅目录)", None))
                continue
            for sub in sorted(entry.iterdir(), key=lambda p: p.name):
                if sub.is_file() and sub.suffix == ".md":
                    stage, reason = parse_reject_md(sub)
                    records.append((entry.name, sub.stem, stage, reason, sub))
                elif sub.is_dir() and (entry / (sub.name + ".md")).is_file():
                    continue  # md + 目录同框：以 md 档为准，一条记录
                else:
                    records.append((entry.name, sub.name, STAGE_UNKNOWN,
                                    "(无 md 档，仅目录)", None))
        elif entry.suffix == ".md":
            # 旧版扁平：<submit_id>.md 直接在根下
            stage, reason = parse_reject_md(entry)
            records.append((DATE_NEVER, entry.stem, stage, reason, entry))
    return records


def format_summary(records):
    """摘要表：日期 | submit_id | stage | 拒绝原因首行（截 60 字符）。"""
    lines = []
    for date, submit_id, stage, reason, _md in records:
        shown = reason[:REASON_MAX_CHARS]
        if len(reason) > REASON_MAX_CHARS:
            shown += "…"
        lines.append("%s  %s  %s  %s" % (date, submit_id, stage, shown))
    return lines


def stats_by_stage(records):
    """按 stage 汇总计数，返回 [(stage, count)]，计数降序。"""
    counts = {}
    for _d, _s, stage, _r, _md in records:
        counts[stage] = counts.get(stage, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def find_reject_md(root: Path, submit_id: Path):
    """按 submit_id 查找 md 档（先日期层后扁平层），返回 Path 或 None。"""
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            candidate = entry / ("%s.md" % submit_id.name)
            if candidate.is_file():
                return candidate
    candidate = root / ("%s.md" % submit_id.name)
    if candidate.is_file():
        return candidate
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="codex_bridge 拒档只读浏览工具（绝不写任何内容）"
    )
    parser.add_argument(
        "--rejects-root", default=None,
        help="rejects 归档根目录；缺省自动定位 data/codex_bridge/rejects",
    )
    parser.add_argument(
        "--show", default=None, metavar="SUBMIT_ID",
        help="打印该 submit_id 的完整 md 档内容（只读展示）",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="按 stage 汇总拒档计数",
    )
    args = parser.parse_args(argv)

    if args.show and args.stats:
        print("[错误] --show 与 --stats 互斥，请分开执行。", file=sys.stderr)
        return 2

    root = Path(args.rejects_root) if args.rejects_root else DEFAULT_REJECTS_ROOT

    if args.show:
        md = find_reject_md(root, Path(args.show))
        if md is None:
            # 目录落档但无 md 档的情况也提示（fail-visible 只读）
            dir_hit = None
            if root.is_dir():
                for entry in sorted(root.iterdir(), key=lambda p: p.name):
                    cand_dir = (entry / args.show) if entry.is_dir() else None
                    if cand_dir is not None and cand_dir.is_dir():
                        dir_hit = cand_dir
                        break
            if dir_hit is not None:
                print("(submit_id=%s 只有落档目录、无 md 档：%s)"
                      % (args.show, dir_hit))
            else:
                print("无拒档：未找到 submit_id=%s 的拒档记录。" % args.show)
            return 0
        print("# 档案路径：%s" % md)
        print(md.read_text(encoding="utf-8", errors="replace"), end="")
        return 0

    records = collect_records(root)
    if not records:
        print("无拒档（rejects 目录缺失或为空：%s）" % root)
        return 0

    if args.stats:
        print("stage 汇总（共 %d 档）：" % len(records))
        for stage, count in stats_by_stage(records):
            print("  %-8s %d" % (stage, count))
        return 0

    print("日期        submit_id          stage      拒绝原因首行（截 %d 字符）"
          % REASON_MAX_CHARS)
    for line in format_summary(records):
        print(line)
    print("[汇总] 共 %d 条拒档记录。" % len(records))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
