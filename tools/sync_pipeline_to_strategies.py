# -*- coding: utf-8 -*-
"""E5 卡墙↔收编流水线回写工具。

把 quant_backtest/data/shadow_onboarding/daily_shadow_summary_YYYYMMDD.json 中
各策略的影子进度，按映射表回写到 portal/strategies.json 已存在条目的
pipeline 字段（契约 docs/dashboard_contracts.md §6 strategies 条目）。

规则（E5 设计稿）：
- 只更新**已存在**条目的 pipeline 字段，不增删条目、不动 _meta、不改 ts；
- 幂等：同 as_of 重跑零 diff；--dry-run 打印计划 diff 不写盘；
- fail-visible：summary 缺失 / JSON 解析失败 / 字段缺失 → 非零退出 + stderr；
- 原子写：.tmp → os.replace；
- 无匹配条目 → 打印提示退出 0（S5 上架后自动接入，预期行为非错误）。
"""
import argparse
import glob
import json
import os
import re
import sys
import logging

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STRATEGIES = os.path.join(REPO_ROOT, "portal", "strategies.json")
DEFAULT_SUMMARY_DIR = os.path.join(
    REPO_ROOT, "quant_backtest", "data", "shadow_onboarding")

SOURCE_TAG = "strategy_layer/onboarding"
VALID_STAGES = {"S1", "S2", "S3", "S4", "S5"}
SUMMARY_GLOB = "daily_shadow_summary_*.json"

# 收编侧策略 id ↔ portal/strategies.json 条目 id 映射表（随收编推进维护）。
# 当前收编批次四策略（ma_cross/trend_follow/rsi_reversal/boll_breakout）尚未
# 上架卡墙（S5 上架时补位，条目 id 待定）；li122/qpzm 在 S3 过闸上架后补位。
STRATEGY_MAP = {
    # "ma_cross":      {"entry": "<S5上架后填条目id>", "stage": "S4", "stage_cn": "影子期"},
    # "trend_follow":  {"entry": "<S5上架后填条目id>", "stage": "S4", "stage_cn": "影子期"},
    # "rsi_reversal":  {"entry": "<S5上架后填条目id>", "stage": "S4", "stage_cn": "影子期"},
    # "boll_breakout": {"entry": "<S5上架后填条目id>", "stage": "S4", "stage_cn": "影子期"},
    # "li122":         {"entry": "<S3过闸上架后填条目id>", "stage": "S3", "stage_cn": "过闸待上"},
    # "qpzm":          {"entry": "<S3过闸上架后填条目id>", "stage": "S3", "stage_cn": "过闸待上"},
}


def _fail(msg):
    """fail-visible：stderr 说明 + 非零退出，禁静默跳过。"""
    print("[sync_pipeline] 错误：%s" % msg, file=sys.stderr)
    sys.exit(1)


def find_latest_summary(summary_dir):
    """目录里取日期最新一份 daily_shadow_summary_YYYYMMDD.json。"""
    paths = glob.glob(os.path.join(summary_dir, SUMMARY_GLOB))
    dated = []
    for p in paths:
        m = re.search(r"(\d{8})\.json$", os.path.basename(p))
        if m:
            dated.append((m.group(1), p))
    if not dated:
        _fail("目录 %s 下未找到 %s" % (summary_dir, SUMMARY_GLOB))
    dated.sort(reverse=True)
    return dated[0][1], dated[0][0]


def load_summary(path):
    """读 summary 并校验必需字段，任何缺失/坏 JSON → fail-visible。"""
    if not os.path.isfile(path):
        _fail("summary 不存在：%s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        _fail("summary 读取/解析失败 %s：%s" % (path, e))
    if not isinstance(data, dict):
        _fail("summary 顶层不是对象：%s" % path)
    trade_date = data.get("trade_date")
    if not (isinstance(trade_date, str) and re.fullmatch(r"\d{8}", trade_date)):
        _fail("summary 缺 trade_date（8 位日期）：%s" % path)
    strategies = data.get("strategies")
    if not isinstance(strategies, dict) or not strategies:
        _fail("summary 缺 strategies 字典：%s" % path)
    return data


def build_pipeline(summary, sid, info, conf):
    """按契约 §6 组装 pipeline 字段。"""
    stats = (summary.get("stats") or {}).get(sid) or {}
    n_total = stats.get("n_total", info.get("recorded", 0))
    pool_n = len(summary.get("pool") or [])
    return {
        "stage": conf["stage"],
        "stage_cn": conf.get("stage_cn", ""),
        "as_of": summary["trade_date"],
        "note": "影子 %s 条 · 池 %d 只" % (n_total, pool_n),
        "source": SOURCE_TAG,
    }


def atomic_write_json(path, data):
    """原子写：同目录 .tmp → os.replace，失败清理 tmp。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/sync_pipeline_to_strategies.py", exc)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="E5：daily_shadow_summary → portal/strategies.json pipeline 回写")
    ap.add_argument("--file", default=DEFAULT_STRATEGIES,
                    help="strategies.json 路径（默认 portal/strategies.json）")
    ap.add_argument("--summary", default=None,
                    help="指定 summary json 路径（默认取 summary-dir 最新一份）")
    ap.add_argument("--summary-dir", default=DEFAULT_SUMMARY_DIR,
                    help="summary 搜索目录")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划 diff，不写盘")
    args = ap.parse_args(argv)

    # 1) summary
    if args.summary:
        summary_path = args.summary
    else:
        summary_path, _ = find_latest_summary(args.summary_dir)
    summary = load_summary(summary_path)

    # 2) 匹配映射表
    targets = []
    for sid, info in summary["strategies"].items():
        conf = STRATEGY_MAP.get(sid)
        if not conf:
            continue
        if conf.get("stage") not in VALID_STAGES:
            _fail("映射表 %s 的 stage 非法（须 S1..S5）：%r" % (sid, conf.get("stage")))
        if not isinstance(conf.get("entry"), str) or not conf["entry"]:
            _fail("映射表 %s 缺 entry（strategies.json 条目 id）" % sid)
        if not isinstance(info, dict):
            _fail("summary.strategies.%s 不是对象" % sid)
        targets.append((conf["entry"], build_pipeline(summary, sid, info, conf)))

    if not targets:
        print("无匹配条目（S5 上架后自动接入）")
        return 0

    # 3) 读 strategies.json，只更新已存在条目
    if not os.path.isfile(args.file):
        _fail("strategies.json 不存在：%s" % args.file)
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        _fail("strategies.json 读取/解析失败 %s：%s" % (args.file, e))
    entries = data.get("strategies")
    if not isinstance(entries, list):
        _fail("strategies.json 缺 strategies 数组：%s" % args.file)
    by_id = {e.get("id"): e for e in entries if isinstance(e, dict)}

    plans = []
    for entry_id, pipeline in targets:
        entry = by_id.get(entry_id)
        if entry is None:
            _fail("映射表条目 %s 不在 strategies.json（映射与注册表漂移）" % entry_id)
        old = entry.get("pipeline")
        if old == pipeline:
            continue  # 幂等：同 as_of 重跑零 diff
        plans.append((entry, old, pipeline))

    if not plans:
        print("无变更（幂等零 diff）· as_of=%s" % summary["trade_date"])
        return 0

    # 4) 打印计划 diff（--dry-run 到此为止，不写盘）
    for entry, old, new in plans:
        print("[计划] %s.pipeline：%s → %s" % (
            entry.get("id"),
            json.dumps(old, ensure_ascii=False) if old is not None else "（缺省）",
            json.dumps(new, ensure_ascii=False)))
    if args.dry_run:
        print("--dry-run：未写盘")
        return 0

    # 5) 应用 + 原子写（不增删条目、不动 _meta、不改 ts）
    for entry, _old, new in plans:
        entry["pipeline"] = new
    atomic_write_json(args.file, data)
    print("已回写 %d 条 · as_of=%s → %s" % (
        len(plans), summary["trade_date"], args.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
