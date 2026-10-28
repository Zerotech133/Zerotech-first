#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计所有含「至今」的时间覆盖记载。

为什么单挑「至今」：
    它直接决定"这份数据能不能拿来追新"。已核实的 4 条证券从业人员记录都写「2003 至今」，
    页面原文却是 `2003.08-2023.5.30` —— 数据早停更了，档案却说过更新中。
    这类错误不会让链接失效、不会让标题不符，只会在选票日让你算错新鲜度。

抽取逻辑复用 alias_probe（同一口径只实现一次，避免两处各写一套必漂移）。

只读：GET 详情页，不改任何数据。

用法：
    python coverage_audit.py            # 核全部含「至今」的记录
    python coverage_audit.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alias_probe import extract, fetch, norm, to_text  # noqa: E402

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
OUT_MD = BASE / "至今覆盖审计.md"
OUT_JSON = BASE / "_coverage_audit.json"
OUT_LOCK = BASE / "_coverage_audit.lock.json"

def tz_now() -> datetime:
    return datetime.now(tz=timezone(timedelta(hours=8)))

DATE_TOKEN = re.compile(
    r"(?:19|20)\d{2}(?:\s*[.\-/年]\s*\d{1,2}(?!\d)(?:\s*[.\-/日]\s*\d{1,2}(?!\d))?)?")


def last_ym(text: str) -> tuple[int, int] | None:
    r"""取文本里最后一个日期 token 的年月。

    必须按 token 匹配并加 (?!\d) 边界：早先的写法把 `2004-2025年` 里的 `20`
    当成月份吃掉，算出 262 个月这种离谱值，整份报告的严重程度全是错的。
    """
    toks = DATE_TOKEN.findall(text or "")
    if not toks:
        return None
    last = toks[-1]
    year = int(last[:4])
    parts = [int(x) for x in re.findall(r"\d{1,2}", last[4:])]
    month = parts[0] if parts and 1 <= parts[0] <= 12 else 12
    return year, month


def classify(rec_cov: str, page_cov: str) -> str:
    """档案写「至今」时，页面止期到底有多新。

    分档按"能不能拿来追新"来定，不是按有没有止期：
      - 页面自己就写"至今/实时" → 一致
      - 止期在 6 个月内 → 一致（年度数据本就有发布延迟）
      - 7~18 个月 → 轻微滞后
      - 超过 18 个月 → 至少漏了一整个年度周期，属实质高估
    """
    if "至今" in page_cov:
        return "一致（页面写到至今）"
    if "实时" in page_cov:
        return "一致（页面标实时更新）"
    ym = last_ym(page_cov)
    if ym is None:
        return "不一致"
    now = tz_now()
    months = (now.year - ym[0]) * 12 + (now.month - ym[1])
    if months <= 6:
        return f"一致（止期距当前 {months} 个月）"
    if months <= 18:
        return f"轻微滞后（止期距今 {months} 个月）"
    return f"档案高估新鲜度（止期距今 {months} 个月）"


ORDER = ["档案高估新鲜度", "轻微滞后", "不一致", "页面未写时间区间", "取页失败", "一致"]


def bucket(verdict: str) -> str:
    """把带数字的判定归到固定档，便于统计。"""
    for key in ("档案高估新鲜度", "轻微滞后", "页面未写时间区间", "取页失败", "不一致"):
        if verdict.startswith(key):
            return key
    if verdict.startswith("一致"):
        return "一致"
    return "其他"


def write_lock() -> dict:
    """给审计结果写一把锁（条数 + sha256）。

    为什么需要：证据文件被删除或改写时，所有"新鲜度不变量"都会真空成立——
    没有记录就没有违规，检查全绿但证据已经没了。锁是外部锚点，能把这种情况打红。
    """
    import hashlib

    raw = OUT_JSON.read_bytes()
    lock = {"sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(json.loads(raw).get("rows", [])),
            "locked_at": tz_now().isoformat(timespec="seconds")}
    OUT_LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    return lock


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--lock-only", action="store_true", help="只按现有结果补写锁，不联网")
    args = ap.parse_args()

    if args.lock_only:
        if not OUT_JSON.is_file():
            print(f"没有历史结果 {OUT_JSON}")
            return 2
        print("已写锁：", write_lock())
        return 0

    datasets = json.loads(REGISTRY.read_text(encoding="utf-8"))["datasets"]
    targets = [d for d in datasets if "至今" in str(d.get("coverage", ""))]
    if args.limit:
        targets = targets[: args.limit]
    print(f"含「至今」的记载 {len(targets)} 条")

    rows = []
    for i, d in enumerate(targets, 1):
        html = fetch(d["url"])
        page_cov = ""
        if html:
            page_cov = extract(to_text(html), "时间区间", "coverage")
        rec_cov = str(d.get("coverage", ""))
        if not html:
            verdict = "取页失败"
        elif not page_cov:
            verdict = "页面未写时间区间"
        elif norm(page_cov) == norm(rec_cov):
            verdict = "一致"
        else:
            verdict = classify(rec_cov, page_cov)
        rows.append({"id": d["id"], "name": d["name"], "shard": d.get("_shard"),
                     "record": rec_cov, "page": page_cov, "url": d["url"], "verdict": verdict})
        if i % 10 == 0:
            print(f"  {i}/{len(targets)}")
        time.sleep(args.delay)

    rows.sort(key=lambda r: (ORDER.index(bucket(r["verdict"])) if bucket(r["verdict"]) in ORDER else 9,
                             r["name"]))
    counts = {k: sum(1 for r in rows if bucket(r["verdict"]) == k) for k in ORDER}

    now = tz_now()
    lines = ["# 「至今」时间覆盖审计", "",
             f"> 审计时间：{now.isoformat(timespec='seconds')}　｜　对象：coverage 含「至今」的记录 "
             f"{len(rows)} 条　｜　方法：取详情页 `时间区间` 原文比对", "",
             "> 判定分档：**档案高估新鲜度**＝页面给了明确止期，档案却写「至今」；",
             "> `页面未写时间区间` 不算记录的错；`一致` 表示页面确实写到至今。", "",
             "| 判定 | 条数 |", "|---|---|"]
    for k in ORDER:
        lines.append(f"| {k} | {counts[k]} |")
    lines.append("")

    suspect = [r for r in rows if bucket(r["verdict"]) in ("档案高估新鲜度", "轻微滞后", "不一致")]
    if suspect:
        lines += ["## 需修正的记载", "", "| 分片 | 名称 | 档案记载 | 页面原文 | 判定 | 链接 |",
                  "|---|---|---|---|---|---|"]
        for r in suspect:
            lines.append(f"| {r['shard']} | {r['name']} | {r['record']} | {r['page'] or '（未取到）'}"
                         f" | {r['verdict']} | {r['url']} |")
        lines.append("")
        lines.append("按分片汇总：")
        lines.append("")
        by_shard: dict[str, int] = {}
        for r in suspect:
            by_shard[str(r["shard"])] = by_shard.get(str(r["shard"]), 0) + 1
        for sh, c in sorted(by_shard.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{sh}`：{c} 条")
        lines.append("")

    lines += ["## 全部结果", "", "| 判定 | 分片 | 名称 | 档案记载 | 页面原文 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['verdict']} | {r['shard']} | {r['name']} | {r['record']} | {r['page'] or '（未取到）'} |")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"checked_at": now.isoformat(timespec="seconds"),
                                    "counts": counts, "rows": rows}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    write_lock()
    print("\n判定分布：", counts)
    print(f"报告：{OUT_MD}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
