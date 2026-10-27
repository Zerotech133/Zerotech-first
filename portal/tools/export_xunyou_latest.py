# -*- coding: utf-8 -*-
"""导出荀攸盘后日报最新产物 → portal/data/xunyou_latest.json（G8 Agent 日报入口数据源）

来源（只读，不修改 reports/ 下任何文件）：
  quant_backtest/reports/xunyou_pool_<YYYYMMDD>.json —— 荀攸（策略研究 Agent）
  每日 17:45 盘后作业产物（RESEARCH_ONLY_NOT_PRODUCTION / BLOCK_CUTOVER，
  研究产物非生产 picks）。按文件名日期排序取最新一份。

产出字段（精简，供 portal/picks.html「荀攸盘后日报」区块 fetch 展示）：
  agent / trade_date / generated_at / scope / cutover_verdict
  pool_count / divergence_count
  top：按 score 降序前 10，每项 symbol/score/confidence/divergence/rationale
       /tier（2026-10-19 拍板分档："T1"/"T2"/""；旧池文件无 tier 字段时
       兼容为空串，不崩）
       （rationale 截断至 RATIONALE_MAX 字符，超长以 … 收尾）
  source_file：导出所用源文件名（运维溯源用）

用法: python portal/tools/export_xunyou_latest.py（无参；纯标准库）
  源缺失或结构非法时退出码 1 并打 stderr——调用方（定时任务）可据此报警，
  页面侧则安静降级显示「盘后作业未产出」占位。
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # 仓库根
REPORTS_DIR = os.path.join(ROOT, "quant_backtest", "reports")
OUT_PATH = os.path.join(HERE, "..", "data", "xunyou_latest.json")  # portal/data/
POOL_RE = re.compile(r"^xunyou_pool_(\d{8})\.json$")
TOP_N = 10
RATIONALE_MAX = 80  # 表格理由列截断长度（字符）


def latest_pool_file():
    """按文件名日期（YYYYMMDD）排序取最新的一份；无匹配返回 None。"""
    best = None  # (ymd, path)
    for path in glob.glob(os.path.join(REPORTS_DIR, "xunyou_pool_*.json")):
        m = POOL_RE.match(os.path.basename(path))
        if not m:
            continue
        ymd = m.group(1)
        if best is None or ymd > best[0]:
            best = (ymd, path)
    return best[1] if best else None


def is_divergent(item):
    """divergence 真值判定：兼容 bool 与 "true" 字符串，拒字符串 "false"。"""
    v = item.get("divergence")
    return v is True or v == "true"


def score_key(item):
    v = item.get("score")
    return (
        v if isinstance(v, (int, float)) and not isinstance(v, bool) else float("-inf")
    )


def truncate(text):
    t = "" if text is None else str(text)
    return t if len(t) <= RATIONALE_MAX else t[: RATIONALE_MAX - 1] + "…"


def build_payload(src_path, data):
    pool = data.get("pool")
    if not isinstance(pool, list):
        raise ValueError("源文件缺少 pool 数组或结构非法: %s" % src_path)
    ranked = sorted(pool, key=score_key, reverse=True)  # 稳定排序，同分保持源序
    top = []
    for item in ranked[:TOP_N]:
        if not isinstance(item, dict):
            continue
        top.append(
            {
                "symbol": item.get("symbol"),
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "divergence": is_divergent(item),
                # tier 透传（2026-10-19 拍板分档）：旧池文件无 tier 字段 → 空串
                "tier": item.get("tier", "")
                if isinstance(item.get("tier", ""), str)
                else "",
                "rationale": truncate(item.get("rationale")),
            }
        )
    div_count = sum(1 for it in pool if isinstance(it, dict) and is_divergent(it))
    stats = data.get("stats") or {}
    if (
        isinstance(stats, dict)
        and stats.get("divergence_total") is not None
        and stats["divergence_total"] != div_count
    ):
        # 以 pool 实算为准；stats 不一致仅告警（不阻断、不改源）
        print(
            "[warn] stats.divergence_total=%s 与 pool 实算 %s 不一致，取实算值"
            % (stats["divergence_total"], div_count),
            file=sys.stderr,
        )
    return {
        "agent": data.get("agent"),
        "trade_date": data.get("trade_date"),
        "generated_at": data.get("generated_at"),
        "scope": data.get("scope"),
        "cutover_verdict": data.get("cutover_verdict"),
        "pool_count": len(pool),
        "divergence_count": div_count,
        "top": top,
        "source_file": os.path.basename(src_path),
    }


def main():
    src = latest_pool_file()
    if not src:
        print(
            "[error] 未找到 %s/xunyou_pool_<YYYYMMDD>.json（盘后作业未产出）"
            % REPORTS_DIR,
            file=sys.stderr,
        )
        return 1
    try:
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("[error] 读取/解析源文件失败 %s: %s" % (src, e), file=sys.stderr)
        return 1
    try:
        payload = build_payload(src, data)
    except ValueError as e:
        print("[error] %s" % e, file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, OUT_PATH)  # 原子替换，避免半文件被页面 fetch 到
    print("source      : %s" % src)
    print("trade_date  : %s" % payload["trade_date"])
    print("generated_at: %s" % payload["generated_at"])
    print("scope       : %s" % payload["scope"])
    print("cutover     : %s" % payload["cutover_verdict"])
    print("pool_count  : %s" % payload["pool_count"])
    print("divergence  : %s" % payload["divergence_count"])
    print("top         : %s 条（score 降序）" % len(payload["top"]))
    print("output      : %s" % os.path.abspath(OUT_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
