# -*- coding: utf-8 -*-
"""WO-OS-18 诸葛亮晨报生成器（06 的分层落地：仅生成器）。

七源只读聚合 → api_zhuge_brief.build_brief 规则引擎 → 一份 JSON 落盘。
产物：<out-dir>/zhuge_brief_YYYY-MM-DD.json（北京日期），同日重生成覆盖同名文件。

分层边界（本单只做第一层）：
  · 只生成，不外呼：不发送任何通知、不挂计划任务、不写控制面配置；
  · 七源与引擎本体零改动：七源获取复用 api_zhuge_route.build_route_payload，
    单源失败按 None 下传，由引擎 usable() 归入 gaps（缺源即待核）；
  · WO-OS-20 第七源 pushflow（当日通知投递成败）由 route 的 _pushflow_stats
    统一注入 raw，本模块只透传、不另写一份实现（禁双实现，口径单点）。
  · 产物保留 engine='rule-based' / model_invoked=False / dispatched=False
    三个诚实标记，表示这是规则归集草案，不是模型推理、也未派单。

用法：
  python zhuge_morning.py --dry-run
  python zhuge_morning.py --out-dir D:/tmp/brief --clock 2026-10-21T09:00:00+08:00

对外接口：
  generate(clock=None, out_dir=None, dry_run=False, fetchers=None) -> dict
  main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from api_zhuge_brief import BEIJING, SOURCES  # noqa: E402  引擎常量（只读复用）
import api_zhuge_route  # noqa: E402  七源聚合（与 /api/zhuge/brief 同款口径）

DEFAULT_OUT_DIR = HERE / "data" / "zhuge_brief"
FILE_PREFIX = "zhuge_brief_"


def _coerce_clock(clock: Optional[Any]) -> datetime:
    """clock: None→当前北京时间；str→带时区 ISO8601；datetime→须 tz-aware。

    坏值显式抛 ValueError（不静默回落到当前时间，避免回放口径失真）。
    """
    if clock is None:
        return datetime.now(BEIJING)
    if isinstance(clock, datetime):
        if clock.tzinfo is None:
            raise ValueError("clock 必须带时区（如 2026-10-21T09:00:00+08:00）")
        return clock
    text = str(clock).strip().replace(" ", "T")
    if not text:
        raise ValueError("clock 不能是空字符串")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("clock 不是合法 ISO8601: %r" % (clock,)) from exc
    if parsed.tzinfo is None:
        raise ValueError("clock 必须带时区（如 2026-10-21T09:00:00+08:00）")
    return parsed


def beijing_day(clock: datetime) -> str:
    return clock.astimezone(BEIJING).strftime("%Y-%m-%d")


def brief_path(out_dir, clock: datetime) -> Path:
    """产物路径：<out_dir>/zhuge_brief_YYYY-MM-DD.json（北京日期）。"""
    return Path(out_dir) / (FILE_PREFIX + beijing_day(clock) + ".json")


def summarize(brief: Dict[str, Any]) -> Dict[str, int]:
    suggestions = brief.get("suggestions") or []
    return {
        "suggestions": len(suggestions),
        "gaps": len(brief.get("gaps") or []),
        "top_priority": sum(1 for s in suggestions if s.get("priority") == 1),
    }


def generate(clock=None, out_dir=None, dry_run: bool = False,
             fetchers=None) -> Dict[str, Any]:
    """聚合七源 → 规则引擎 → （非 dry-run 时）落盘一份 JSON。

    返回 {ok, written, path, brief, summary}；dry_run 时 written=False 且不建目录。
    fetchers 为测试注入点（{source: callable}），None 走七源真实函数
    （pushflow 亦由 route 的 _pushflow_stats 提供，本模块不重复实现）。
    """
    clk = _coerce_clock(clock)
    target = brief_path(DEFAULT_OUT_DIR if out_dir is None else out_dir, clk)
    brief = api_zhuge_route.build_route_payload(fetchers=fetchers, clock=clk)

    written = False
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(brief, ensure_ascii=False, indent=2) + "\n"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)  # 同日重生成即覆盖；generated_at 随本次时钟刷新
        written = True

    return {
        "ok": True,
        "written": written,
        "path": str(target),
        "brief": brief,
        "summary": summarize(brief),
    }


def _fmt_summary(summary: Dict[str, int]) -> str:
    return ("摘要: 建议 %d 条 / gaps %d 项 / 最高优先级 P1 %d 条"
            % (summary["suggestions"], summary["gaps"],
               summary["top_priority"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="诸葛亮晨报生成器（只生成，不发通知、不挂计划任务）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印摘要与目标路径，不写盘、不建目录")
    parser.add_argument("--out-dir", default=None,
                        help="产物目录（默认 portal/data/zhuge_brief）")
    parser.add_argument("--clock", default=None,
                        help="测试注入时钟（带时区 ISO8601，如 "
                             "2026-10-21T09:00:00+08:00）")
    args = parser.parse_args(argv)

    try:
        res = generate(clock=args.clock, out_dir=args.out_dir,
                       dry_run=args.dry_run)
    except (TypeError, ValueError) as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2

    brief = res["brief"]
    if res["written"]:
        print("已写入: %s" % res["path"])
    else:
        print("[dry-run] 未写盘，目标路径: %s" % res["path"])
    print(_fmt_summary(res["summary"]))
    print("诚实标记: engine=%s model_invoked=%s dispatched=%s"
          % (brief.get("engine"), brief.get("model_invoked"),
             brief.get("dispatched")))
    print("口径: 七源 %d 个；缺口与建议同源可溯；未发送任何通知、未挂任何计划任务"
          % len(SOURCES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
