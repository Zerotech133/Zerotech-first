#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化「更新频率」与「时间区间」口径待确认/可补齐的记录。

发现过程：
    抽 3 条 P0 记录核页面，发现页面自己的两个字段口径不同——
    `更新频率` 写「年度」，同一页 `时间区间` 却标「（实时更新）」。
    档案忠实抄了「年度」，抄录没错。取证后定性为「待确认」：多为同一系列的不同交付版本
    （批量历史库按年度滚动、实时/增量走 API 另售），不等于抄错；但排期必须分两档。

分档，不要混为一谈（混在一起就是噪音）：
    频率低频但区间标实时  页面两个口径多指不同交付版本 —— 待确认，排期要分两档
    档案与页面频率不一致  两边都有值且不同 —— 需人判谁对
    档案未填可补齐        档案是「未标注」、页面有值 —— 机会，不是故障
                          实测这一档占 96/102，混进"待确认"里会把真问题淹掉
    抽取可疑需重抓        抽出来的不是频率值（历史宽松校验混进正文）—— 证据不可用
    未取到证据需重抓      页面抓到了但没抽到「更新频率」—— **没有证据，不能当"一致"**

只读：GET 详情页，不改任何数据。

用法：
    python freq_audit.py --limit 25    # 抽样
    python freq_audit.py               # 全量，约 5 分钟
    python freq_audit.py --report-only # 从 _freq_audit.json 重出，不联网
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alias_probe import extract, fetch, to_text  # 同一抽取口径，不另写一套

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
OUT_MD = BASE / "频率口径审计.md"
OUT_JSON = BASE / "_freq_audit.json"
OUT_LOCK = BASE / "_freq_audit.lock.json"

LOW_FREQ = ("年度", "不定期", "未标注", "季度", "月")
REALTIME_HINTS = ("实时", "日更", "每日", "按日")
KIND_ORDER = ["抽取可疑需重抓", "频率低频但区间标实时", "档案与页面频率不一致", "档案未填可补齐",
              "未取到证据需重抓"]
# 没有页面原文可挂的档：只出现在审计 JSON 与报告里，不进 registry。
# 挂了会触发「频率标记缺页面原文」不变量，反而让闸门失效。
NO_EVIDENCE_KIND = "未取到证据需重抓"


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def looks_like_freq(val: str) -> bool:
    """页面频率字段是否真像频率值。与 alias_probe.valid_value 同口径。

    历史结果（_freq_audit.json）是用旧的宽松校验抓的，混进了正文段落。
    重出报告时不重爬，用这条把它挑出来单列，而不是当成"频率不一致"去误导人。
    """
    v = str(val or "").strip()
    # 合法频率词全都要在这里，漏一个就把整类记录误判成"抽取可疑"（实测漏了「按需」，
    # 8 条 ETF/港股记录全被误报）
    ALLOWED = LOW_FREQ + ("实时", "日", "周", "按需", "每年", "每日")
    return 0 < len(v) <= 10 and any(v[:8].startswith(w) for w in ALLOWED)


def classify_row(freq_record: str, freq_page: str, cov_page: str) -> str:
    """五档判定。顺序有意义：先剔除"没抓到证据"，再剔除抽取错位，再判口径待确认，
    再判缺填，最后判不一致。

    第一句必须是「没抽到页面频率」而不是「返回空」：
        返回空等于宣称"页面与档案一致"，可实际上这一条根本没有证据。
        （修复前）403 条空 conflict 里有 74 条 `freq_page` 为空（其中 38 条连 `cov_page`
        都有值），把它们算成"一致"会让"核对 506 条"虚高，真实一致只有 329 条，
        同时让「抽取可疑需重抓」这档恒为 0 成了死档。
    """
    fp = str(freq_page or "")
    rec = str(freq_record or "")
    if not fp.strip():
        return NO_EVIDENCE_KIND
    if not looks_like_freq(fp):
        return "抽取可疑需重抓"
    if any(k in fp for k in LOW_FREQ) and any(h in str(cov_page or "") for h in REALTIME_HINTS):
        return "频率低频但区间标实时"
    if rec in ("", "未标注"):
        return "档案未填可补齐"
    if fp.split()[0] not in rec and rec not in fp:
        return "档案与页面频率不一致"
    return ""


def write_lock() -> dict:
    """给频率审计结果加锁（条数 + sha256），防止证据被静默删除/改写后不变量真空成立。"""
    import hashlib

    raw = OUT_JSON.read_bytes()
    lock = {"sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(json.loads(raw).get("rows", [])),
            "locked_at": tz_now().isoformat(timespec="seconds")}
    OUT_LOCK.write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    return lock


def render(rows: list[dict]) -> int:
    for r in rows:
        if r.get("conflict") != "取页失败":
            r["conflict"] = classify_row(r.get("freq_record", ""),
                                         r.get("freq_page", ""),
                                         r.get("cov_page", ""))

    failed = [r for r in rows if r.get("conflict") == "取页失败"]
    hits = [r for r in rows if r.get("conflict") and r.get("conflict") != "取页失败"]
    by_kind = {k: sum(1 for r in hits if r["conflict"] == k) for k in KIND_ORDER}
    no_ev = by_kind[NO_EVIDENCE_KIND]
    # 有效核对 = 真拿到页面频率的条数。取页失败与"抓到了但没抽到频率"都不算。
    valid = len(rows) - len(failed) - no_ev
    pri: dict[str, int] = {}
    for r in hits:
        if r["conflict"] == NO_EVIDENCE_KIND:
            continue                      # 没有证据的记录不参与"需处理"优先级分布
        pri[str(r.get("priority"))] = pri.get(str(r.get("priority")), 0) + 1
    need = (len(hits) - by_kind["档案未填可补齐"] - by_kind["抽取可疑需重抓"] - no_ev)

    now = tz_now()
    lines = [
        "# 更新频率 × 时间区间 口径审计", "",
        f"> 审计时间：{now.isoformat(timespec='seconds')}　｜　核对 {len(rows)} 条 / "
        f"有效核对 {valid} 条 / 未取到证据 {no_ev} 条"
        f"　｜　**需处理 {need} 条**　｜　可补齐 {by_kind['档案未填可补齐']} 条"
        f"　｜　取页失败 {len(failed)}", "",
        f"> 「核对 {len(rows)} 条」不等于「{len(rows)} 条都核过了」：其中一批页面抓到了却没抽到更新频率，",
        "> 那部分**没有证据**，既不能算一致也不能算不一致，单独列一档并要求重抓。", "",
        "> 各档分开算，因为处置方式完全不同：", "",
        "| 分档 | 条数 | 含义与处置 |", "|---|---|---|",
        f"| 频率低频但区间标实时 | {by_kind['频率低频但区间标实时']} "
        "| 页面两个口径多指不同交付版本（批量库按年度、实时走 API）。**待确认，排期分两档** |",
        f"| 档案与页面频率不一致 | {by_kind['档案与页面频率不一致']} "
        "| 两边都有值且不同，需核页面确认哪次抄错 |",
        f"| 档案未填可补齐 | {by_kind['档案未填可补齐']} "
        "| 档案是「未标注」、页面写了值，属**可补齐**，不是故障 |",
        f"| 抽取可疑需重抓 | {by_kind['抽取可疑需重抓']} "
        "| 抽出来的不是频率值（混进正文），证据不可用，需重抓 |",
        f"| 未取到证据需重抓 | {no_ev} "
        "| 页面有、但没抽到「更新频率」。**没有证据，不能当一致看**，需重抓 |",
        "",
        "| 优先级 | 条数（需处理 + 可补齐） |", "|---|---|",
    ]
    for k in ("P0", "P1", "P2", "P3"):
        if k in pri:
            lines.append(f"| {k} | {pri[k]} |")
    lines.append("")

    for kind in KIND_ORDER[:2]:
        group = sorted([r for r in hits if r["conflict"] == kind],
                       key=lambda x: (str(x.get("priority")), x.get("name", "")))
        if not group:
            continue
        lines += [f"## {kind}（{len(group)} 条）", "",
                  "| 优先级 | 名称 | 档案频率 | 页面频率 | 页面时间区间 |", "|---|---|---|---|---|"]
        for r in group:
            lines.append(f"| {r.get('priority')} | {r.get('name')} | {r.get('freq_record')}"
                         f" | {str(r.get('freq_page'))[:24]} | {str(r.get('cov_page'))[:40]} |")
        lines.append("")

    fill = sorted([r for r in hits if r["conflict"] == "档案未填可补齐"],
                  key=lambda x: str(x.get("priority")))
    if fill:
        lines += [f"## 档案未填可补齐（{len(fill)} 条，非故障）", "",
                  "这些记录 `frequency=未标注`，页面其实写了更新频率。补进去能提高可筛选性。", "",
                  "| 优先级 | 名称 | 页面写的频率 |", "|---|---|---|"]
        for r in fill[:40]:
            lines.append(f"| {r.get('priority')} | {r.get('name')} | {str(r.get('freq_page'))[:20]} |")
        if len(fill) > 40:
            lines.append(f"| … | 其余 {len(fill) - 40} 条见 _freq_audit.json | |")
        lines.append("")

    noev = sorted([r for r in hits if r["conflict"] == NO_EVIDENCE_KIND],
                  key=lambda x: (str(x.get("priority")), x.get("name", "")))
    if noev:
        with_cov = sum(1 for r in noev if str(r.get("cov_page") or "").strip())
        lines += [f"## {NO_EVIDENCE_KIND}（{len(noev)} 条）", "",
                  f"这些记录页面抓到了，但没抽到「更新频率」（其中 {with_cov} 条仍抽到了「时间区间」）。",
                  "**这类记录没有页面证据，不能当「一致」看，需重抓。**", "",
                  "| 优先级 | 名称 | 有无 cov_page |", "|---|---|---|"]
        for r in noev[:40]:
            has_cov = "有" if str(r.get("cov_page") or "").strip() else "无"
            lines.append(f"| {r.get('priority')} | {r.get('name')} | {has_cov} |")
        if len(noev) > 40:
            lines.append(f"| … | 其余 {len(noev) - 40} 条见 _freq_audit.json | |")
        lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n核对 {len(rows)} / 有效核对 {valid} / 需处理 {need} / "
          f"可补齐 {by_kind['档案未填可补齐']} / 未取到证据 {no_ev} / 取页失败 {len(failed)}")
    print(f"分档：{by_kind}")
    print(f"优先级：{pri}")
    # 必须在分类**之后**落盘：先前写在 render 之前，导致 JSON 里 conflict 全是空，
    # 下游拿到的是一份"没有任何结论"的文件，而报告仍显示 6 条待确认——两处口径打架。
    OUT_JSON.write_text(json.dumps({"checked_at": tz_now().isoformat(timespec="seconds"),
                                    "checked": len(rows), "rows": rows},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    write_lock()
    print(f"报告：{OUT_MD}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--report-only", action="store_true", help="从已有 JSON 重出报告，不联网")
    ap.add_argument("--lock-only", action="store_true", help="只按现有结果补写锁，不联网")
    args = ap.parse_args()

    if args.lock_only:
        if not OUT_JSON.is_file():
            print(f"没有历史结果 {OUT_JSON}")
            return 2
        print("已写锁：", write_lock())
        return 0

    if args.report_only:
        if not OUT_JSON.is_file():
            print(f"没有历史结果 {OUT_JSON}")
            return 2
        return render(json.loads(OUT_JSON.read_text(encoding="utf-8"))["rows"])

    datasets = json.loads(REGISTRY.read_text(encoding="utf-8"))["datasets"]
    targets = datasets[: args.limit] if args.limit else datasets
    print(f"待核 {len(targets)} 条", flush=True)

    rows = []
    for i, d in enumerate(targets, 1):
        html = fetch(d["url"])
        if html is None:
            rows.append({"id": d["id"], "name": d["name"], "priority": d.get("priority"),
                         "freq_record": d.get("frequency"), "freq_page": "", "cov_page": "",
                         "url": d["url"], "conflict": "取页失败"})
        else:
            t = to_text(html)
            rows.append({"id": d["id"], "name": d["name"], "priority": d.get("priority"),
                         "freq_record": str(d.get("frequency") or ""),
                         "freq_page": extract(t, "更新频率", "frequency"),
                         "cov_page": extract(t, "时间区间", "coverage"),
                         "url": d["url"], "conflict": ""})
        if i % 50 == 0:
            print(f"  {i}/{len(targets)}", flush=True)
        time.sleep(args.delay)

    return render(rows)


if __name__ == "__main__":
    raise SystemExit(main())
