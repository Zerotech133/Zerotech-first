#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并 CnOpenData 数据源档案分片 -> registry.json + INDEX.md

用法:
    python build_registry.py

只读取 _fragments/frag_*.json，按 id 去重，输出：
    registry.json   机器可读全量档案
    INDEX.md        人读索引（按分类组分组，带统计）
不修改任何分片，不联网。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
FRAG_DIR = BASE / "_fragments"
REGISTRY = BASE / "registry.json"
INDEX = BASE / "INDEX.md"

REQUIRED = [
    "id", "layer", "category_group", "category", "name", "url", "desc",
    "coverage", "volume", "frequency", "access_mode", "api_available",
    "tags", "maps_to", "priority", "auth_status",
]
DEFAULTS = {
    "layer": "core",
    "desc": "未标注",
    "coverage": "未标注",
    "volume": "未标注",
    "frequency": "未标注",
    "access_mode": "direct_sale",
    "api_available": False,
    "tags": [],
    "maps_to": [],
    "priority": "P3",
    "auth_status": "未授权-需采购",
    "notes": "",
}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
LAYER_ORDER = {"core": 0, "factor": 1, "context": 2}


def load_fragments() -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    problems: list[str] = []
    if not FRAG_DIR.is_dir():
        return records, [f"分片目录不存在: {FRAG_DIR}"]

    for path in sorted(FRAG_DIR.glob("frag_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path.name}: JSON 解析失败 ({exc})")
            continue

        items = payload.get("records") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            problems.append(f"{path.name}: 无 records 数组")
            continue

        shard = payload.get("shard", path.stem) if isinstance(payload, dict) else path.stem
        for idx, raw in enumerate(items):
            if not isinstance(raw, dict):
                problems.append(f"{path.name}[{idx}]: 非对象，已跳过")
                continue
            rec = dict(DEFAULTS)
            rec.update({k: v for k, v in raw.items() if v not in (None, "")})
            rec["_shard"] = shard
            rec["frequency_raw"] = rec.get("frequency", "")
            rec["frequency"] = normalize_frequency(rec.get("frequency", ""))
            missing = [k for k in REQUIRED if k not in rec]
            if missing:
                problems.append(f"{path.name}[{idx}] {rec.get('name', '?')}: 缺字段 {missing}")
            if not rec.get("id") or not rec.get("name"):
                problems.append(f"{path.name}[{idx}]: 缺 id 或 name，已跳过")
                continue
            records.append(rec)
    return records, problems


def normalize_id(raw_id: str, url: str) -> str:
    """统一 id 口径：小写、非字母数字转 '-'、压缩连续 '-'。

    各采集席对 URL 末段的处理不一致（有的保留大小写与 %20），
    统一在此归一，避免同一数据集出现两条记录。
    """
    import re

    base = str(raw_id or "").strip()
    if not base and url:
        base = str(url).rstrip("/").split("/")[-1]
    base = base.replace("%20", "-").replace("_", "-")
    base = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", base)
    base = re.sub(r"-{2,}", "-", base).strip("-").lower()
    return base or "unknown"


def normalize_frequency(raw: str) -> str:
    """把采集席写下的自由文本频率归并到统一枚举。

    各席按页面原文抄写，出现「日度」「按需」「不定时」「年度（原始月度）」等
    十余种变体；归一后可统计、可筛选，原文保留在 frequency_raw。
    """
    s = str(raw or "").strip()
    if not s:
        return "未标注"
    if "实时" in s:
        return "实时"
    if "按需" in s or "特殊需求" in s or "另议" in s or "另行联系" in s:
        return "按需"
    if "年度" in s or "每年" in s or "年更" in s:
        return "年度"
    if "季度" in s or "季报" in s:
        return "季度"
    if "月" in s:
        return "月"
    if "周" in s:
        return "周"
    if "日" in s:
        return "日"
    if "不定期" in s or "不定时" in s:
        return "不定期"
    if "不更新" in s or "停更" in s or "不支持" in s:
        return "不支持更新"
    return "未标注"


def _curation_rank(rec: dict) -> int:
    """分诊权重：人工/工单产出 > 刷新脚本自动收录。

    自动收录的记录一律带 status=new_untriaged，字段多为占位（P3、tags=["待分类"]）。
    若按「描述更长者胜」去重，自动记录的长描述会把人工分诊过的 P0/P1 顶掉，
    实测曾把 P0 从 17 条压到 10 条。故显式让已分诊记录优先。
    """
    return 0 if str(rec.get("status", "")) == "new_untriaged" else 1


def _prefer(new: dict, old: dict) -> bool:
    """是否用 new 替换 old。先比分诊权重，再比描述完整度。"""
    rn, ro = _curation_rank(new), _curation_rank(old)
    if rn != ro:
        return rn > ro
    return len(str(new.get("desc", ""))) > len(str(old.get("desc", "")))


def dedupe(records: list[dict]) -> tuple[list[dict], int]:
    """按 id 去重，再按 url 兜底；冲突时保留分诊权重更高的一条。"""
    seen: dict[str, dict] = {}
    dropped = 0
    for rec in records:
        rec["id"] = normalize_id(rec.get("id"), rec.get("url", ""))
        rid = rec["id"]
        if rid in seen:
            dropped += 1
            if _prefer(rec, seen[rid]):
                seen[rid] = rec
        else:
            seen[rid] = rec

    by_url: dict[str, dict] = {}
    url_dropped = 0
    for rec in seen.values():
        url = str(rec.get("url", "")).strip().rstrip("/")
        if url and url in by_url:
            url_dropped += 1
            if _prefer(rec, by_url[url]):
                by_url[url] = rec
            continue
        by_url[url or f"__nourl__{rec['id']}"] = rec
    return list(by_url.values()), dropped + url_dropped




def _alias_rank(rec: dict) -> tuple:
    """同名记录里挑正本的排序键（越小越优先）。

    刻意**不把描述长度算进去**：描述会随再抓取变长变短，正本会跟着翻转，
    外部按 id 引用的地方就漂了。只用人判断相关的稳定信号：
    已分诊 > 优先级高 > id 字典序（纯兜底，保证每次都一样）。
    """
    return (
        -_curation_rank(rec),
        PRIORITY_ORDER.get(rec.get("priority", "P3"), 9),
        0 if str(rec.get("notes", "")).strip() else 1,   # 有告诫/备注的更可能是人工核过的
        str(rec.get("id", "")),
    )


def mark_aliases(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """同名不同路径的记录标为别名，**不删除**。

    背景：上游把同一数据集挂在多个类目下（如「中国债券行情数据」同时出现在
    「股票和债券交易数据」与「债券数据」），两条 URL 都有效、内容同一份，
    但 id 因带类目前缀而不同，按 id 去重抓不住。
    不处理的话档案虚增（实测 20 组 / 40 条），下游按名字取数据集会拿到两个候选。

    处置原则：保留两条，给非正本打 `alias_of` 并写明正本 id。
    删记录要人判断（可能两个类目下的口径真的不同），机器只做标记。
    """
    import re

    def key(name: str) -> str:
        return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(name or "")).lower()

    groups: dict[str, list[dict]] = {}
    for rec in records:
        k = key(rec.get("name", ""))
        if k:
            groups.setdefault(k, []).append(rec)

    marked: list[dict] = []
    for k, items in groups.items():
        if len(items) < 2:
            continue
        ranked = sorted(items, key=_alias_rank)
        canon = ranked[0]
        for rec in ranked[1:]:
            rec["alias_of"] = canon["id"]
            rec["alias_reason"] = "同名数据集在另一类目下重复挂载"
            rec["notes"] = (
                str(rec.get("notes", "")).rstrip()
                + f" ｜ 与 {canon['id']} 同名同源，本条为别名（正本见 alias_of）。"
                "build_registry 自动标记，未删除，待人工确认是否合并。"
            ).strip(" ｜")
            marked.append({"alias": rec["id"], "canonical": canon["id"],
                           "name": rec.get("name", "")})
        # 回写正本：让按正本 id 取数的人知道还有另一个挂载点，别漏
        extra = "；".join(f"{r['id']} ({r['url']})" for r in ranked[1:])
        canon["notes"] = (
            str(canon.get("notes", "")).rstrip() + f" ｜ 另有同类目挂载：{extra}"
        ).strip(" ｜")
    return records, marked


def sort_key(rec: dict):
    return (
        PRIORITY_ORDER.get(rec.get("priority", "P3"), 9),
        LAYER_ORDER.get(rec.get("layer", "core"), 9),
        rec.get("category_group", ""),
        rec.get("category", ""),
        rec.get("name", ""),
    )


def counter(records: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for rec in records:
        val = rec.get(key)
        if isinstance(val, list):
            for v in val:
                out[v] = out.get(v, 0) + 1
        else:
            out[str(val)] = out.get(str(val), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def apply_freshness_overlay(records: list[dict]) -> tuple[list[dict], list[str]]:
    """把「至今」审计结果作为**证据层**挂到记录上，不改动原有 coverage 记载。

    为什么是叠加而不是改写：
        coverage 是采集席抄录的原文，改它就是改写事实来源；而页面止期是另一份证据。
        两份都留着，下游才能判断该信谁、以及为什么。
        实测 38 条含「至今」的记载里 24 条与页面矛盾（12 条止期距今超过 18 个月）。

    字段：
        coverage_page        页面「时间区间」原文（审计时抓的）
        freshness_flag       ok / lag / stale / page_unlabeled / conflict
        freshness_lag_months 止期距审计时点月数（页面写至今/实时则无）
        coverage_checked_at  审计时间
    未审计的记录不打任何标记，保持"未审计"状态（缺字段即未审计，不编造 ok）。
    """
    import re

    ev = BASE / "_coverage_audit.json"
    if not ev.is_file():
        return records, ["未找到 _coverage_audit.json，本次不挂新鲜度证据"]

    payload = json.loads(ev.read_text(encoding="utf-8"))
    checked_at = payload.get("checked_at", "")
    by_id = {r["id"]: r for r in records}
    applied = 0
    skipped = 0
    for row in payload.get("rows", []):
        rec = by_id.get(row.get("id", ""))
        if rec is None:
            continue
        verdict = str(row.get("verdict", ""))
        if verdict.startswith("档案高估新鲜度"):
            flag = "stale"
        elif verdict.startswith("轻微滞后"):
            flag = "lag"
        elif verdict.startswith("一致"):
            flag = "ok"
        elif verdict.startswith("页面未写时间区间"):
            flag = "page_unlabeled"
        elif verdict.startswith("取页失败"):
            # 取页失败 = 这次没拿到证据，不是"两源冲突"。留空即"未审计"，
            # 否则下游会看到一堆假 conflict，久了整份标记都没人信。
            skipped += 1
            continue
        else:
            flag = "conflict"
        rec["freshness_flag"] = flag
        rec["coverage_page"] = row.get("page") or ""
        rec["coverage_checked_at"] = checked_at
        m = re.search(r"(\d+)\s*个月", verdict)
        if m:
            rec["freshness_lag_months"] = int(m.group(1))
        applied += 1
    note = f"新鲜度证据已挂 {applied} 条"
    if skipped:
        note += f"（另有 {skipped} 条取页失败，留作未审计，未标记）"
    return records, [note] if applied else []


FREQ_CONFLICT_KINDS = ("频率低频但区间标实时", "档案与页面频率不一致",
                       "档案未填可补齐", "抽取可疑需重抓")


def apply_freq_overlay(records: list[dict]) -> tuple[list[dict], list[str]]:
    """把「更新频率 × 时间区间」审计结果作为证据挂上，不改动 `frequency`。

    为什么只挂有差异的那些：
        审计 506 条，其中 403 条无 conflict。（修复前）403 里有 74 条根本没抽到页面频率
        （`freq_page` 为空），把这 74 条当"一致"是错的——它们没有证据。
        现在这 74 条已单独归到「未取到证据需重抓」，403 拆成 329 条真一致 + 74 条无证据，
        所以真实一致的只有 329 条。只挂"页面说法与档案不同"的，读者一眼看到该看的。

    「未取到证据需重抓」这一档**不挂**：没有页面原文可挂，挂了会触发
    verify_all 的「频率标记缺页面原文」不变量，反而把闸门弄坏。它只存在于
    _freq_audit.json 与报告里。

    字段：
        freq_page      页面「更新频率」原文
        freq_conflict  分档（见 FREQ_CONFLICT_KINDS）；空字符串表示页面与档案一致
        freq_checked_at 审计时间
    未审计的记录不打标记（缺字段 = 未审计，不编造"一致"）。
    """
    ev = BASE / "_freq_audit.json"
    if not ev.is_file():
        return records, ["未找到 _freq_audit.json，本次不挂频率证据"]

    payload = json.loads(ev.read_text(encoding="utf-8"))
    checked_at = payload.get("checked_at", "")
    by_id = {r["id"]: r for r in records}
    applied = 0
    for row in payload.get("rows", []):
        rec = by_id.get(row.get("id", ""))
        kind = str(row.get("conflict") or "")
        page = str(row.get("freq_page") or "")
        if rec is None or not page or kind not in FREQ_CONFLICT_KINDS:
            continue
        rec["freq_page"] = page
        rec["freq_conflict"] = kind
        rec["freq_checked_at"] = checked_at
        if kind == "频率低频但区间标实时":
            # 这类记录档案与页面的"更新频率"其实一致（都写年度），口径差异在页面自己的
            # "时间区间"上。不把那段原文带上，读者只看到两个相同的"年度"，看不出问题。
            cov = str(row.get("cov_page") or "").strip()
            if cov:
                # 只描述事实，不写"自相矛盾"：多数是同一系列的不同交付版本（批量库按年度、
                # 实时走 API），定性为"抄错"会误导下游。
                rec["freq_note"] = (f"页面时间区间写「{cov}」，与更新频率「{page}」口径不一致"
                                    "（待确认，多为不同交付版本）")
        applied += 1
    # 证据文件里的结论条数，必须与实际挂上的对上，否则说明 id 改名后静默丢了
    expected = sum(1 for r in payload.get("rows", []) if r.get("conflict") in FREQ_CONFLICT_KINDS)
    notes = []
    if applied:
        notes.append(f"频率证据已挂 {applied} 条（来源 _freq_audit.json，其中与审计结论差额 "
                     f"{expected - applied} 条）")
    elif expected:
        # 一条都没挂上却什么都不说 = 静默失败：审计明明有结论，构建看起来一切正常。
        print(f"警告：频率证据一条都没挂上（审计有 {expected} 条结论），可能 id 已改名",
              file=sys.stderr)
    return records, notes


def main() -> int:
    records, problems = load_fragments()
    if not records:
        print("没有可用分片，未生成档案。", file=sys.stderr)
        for p in problems:
            print(" -", p, file=sys.stderr)
        return 2

    records, dropped = dedupe(records)
    records, aliases = mark_aliases(records)
    records, freshness_notes = apply_freshness_overlay(records)
    records, freq_notes = apply_freq_overlay(records)
    records.sort(key=sort_key)
    canon_only = [r for r in records if not r.get("alias_of")]
    flagged = [r for r in records if r.get("freshness_flag")]
    freq_flagged = [r for r in records if r.get("freq_conflict")]

    now = datetime.now(timezone(timedelta(hours=8)))
    meta = {
        "source": "CnOpenData",
        "source_operator": "河南汉数信息技术有限公司",
        "generated_at": now.isoformat(timespec="seconds"),
        "scope": "股票/金融相关数据集与 API（含因子与背景层）",
        "boundary": "仅登记公开目录页元信息，不含任何数据内容；未采购、未调用接口",
        "total_records": len(records),
        "unique_datasets": len(canon_only),
        "alias_records": len(aliases),
        "duplicates_dropped": dropped,
        "by_priority": counter(records, "priority"),
        "by_priority_canonical": counter(canon_only, "priority"),
        "by_layer": counter(records, "layer"),
        "by_frequency": counter(records, "frequency"),
        "by_access_mode": counter(records, "access_mode"),
        "by_category_group": counter(records, "category_group"),
        "freshness_checked": len(flagged),
        "by_freshness": counter(flagged, "freshness_flag"),
        # 条目数不等于数据集数：12 条 stale 里有 5 条是别名，正本新鲜度正常。
        # 只给条目口径会让下游高估"有多少份数据停更"。
        "by_freshness_canonical": counter([r for r in flagged if not r.get("alias_of")], "freshness_flag"),
        "freq_checked": len(freq_flagged),
        "by_freq_conflict": counter(freq_flagged, "freq_conflict"),
        # 同 by_freshness_canonical 的道理：6 条「频率口径待确认」里可能有别名，
        # 只给条目口径会让下游高估"有多少份数据口径待确认"。
        "by_freq_conflict_canonical": counter([r for r in freq_flagged if not r.get("alias_of")],
                                             "freq_conflict"),
    }
    info_notes = list(freshness_notes) + list(freq_notes)   # 信息性提示单独走，不混进"分片告警"
    REGISTRY.write_text(
        json.dumps({"meta": meta, "datasets": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append("# CnOpenData 数据源档案（股票 / 金融）")
    lines.append("")
    lines.append(f"> 生成时间：{meta['generated_at']}　｜　条目总数：**{len(records)}**　｜　唯一数据集：**{meta['unique_datasets']}**　｜　别名：{meta['alias_records']}　｜　去重丢弃：{dropped}")
    lines.append(">")
    lines.append("> **条目数 ≠ 数据集数**：上游把同一数据集挂在多个类目下（两条 URL 都有效、内容同一份），")
    lines.append("> 这些副本保留但标了 `alias_of`，统计覆盖时请用「唯一数据集」那个数。")
    lines.append(">")
    lines.append("> 档案只登记公开目录页元信息（名称、覆盖、量级、更新频率、获取方式、链接），**不含任何数据内容**；未采购、未调用接口。")
    lines.append("> 获取方式一栏：`open` 可免费下载 / `trial` 高校试用 / `direct_sale` 需采购 / `api` 需 API Key。")
    lines.append("")

    lines.append("## 总览")
    lines.append("")
    lines.append("| 维度 | 分布 |")
    lines.append("|---|---|")
    for label, key in (("优先级", "by_priority"), ("圈层", "by_layer"),
                       ("更新频率", "by_frequency"), ("获取方式", "by_access_mode")):
        dist = meta[key]
        lines.append(f"| {label} | " + "、".join(f"{k} {v}" for k, v in dist.items()) + " |")
    lines.append("")
    lines.append("| 分类组 | 条目数 |")
    lines.append("|---|---|")
    for k, v in meta["by_category_group"].items():
        lines.append(f"| {k} | {v} |")
    lines.append("")

    p0 = [r for r in records if r.get("priority") == "P0"]
    lines.append(f"## P0 优先接入候选（{len(p0)} 条）")
    lines.append("")
    if p0:
        lines.append("| 名称 | 分类 | 覆盖 | 更新 | 获取 | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for r in p0:
            note = str(r.get("notes", "")).replace("|", "／")[:70]
            lines.append(
                f"| [{r['name']}]({r['url']}) | {r.get('category','')} | {r.get('coverage','')} "
                f"| {r.get('frequency','')} | {r.get('access_mode','')} | {note} |"
            )
    else:
        lines.append("_暂无 P0 条目_")
    lines.append("")

    if aliases:
        lines.append(f"## 重复挂载别名（{len(aliases)} 条，未删除）")
        lines.append("")
        lines.append("同一数据集在多个类目下重复挂载，两条 URL 都可用。下表列出非正本的一条；")
        lines.append("正本选择规则：已分诊 > 优先级高 > id 字典序（不随描述变化，避免再抓取后正本漂移）。")
        lines.append("")
        lines.append("| 别名 id | 正本 id | 名称 |")
        lines.append("|---|---|---|")
        for a in sorted(aliases, key=lambda x: x["name"]):
            lines.append(f"| {a['alias']} | {a['canonical']} | {a['name']} |")
        lines.append("")

    stale = [r for r in records if r.get("freshness_flag") == "stale"]
    if stale:
        stale_canon = [r for r in stale if not r.get("alias_of")]
        lines.append(f"## 新鲜度存疑（{len(stale_canon)} 个数据集 / {len(stale)} 条记录，止期距今超过 18 个月）")
        if len(stale_canon) != len(stale):
            lines.append("")
            lines.append(f"> 其中 {len(stale) - len(stale_canon)} 条是别名副本，正本另有记录。"
                         "按「有多少份数据停更」估算时请用数据集数。")
        lines.append("")
        lines.append("这些记录写着「至今」，但页面给了明确止期且已过去一年半以上。"
                     "**原 coverage 记载未改动**，页面原文见 `coverage_page`。")
        lines.append("")
        lines.append("| 名称 | 档案记载 | 页面原文 | 滞后 | 优先级 |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(stale, key=lambda x: -(x.get("freshness_lag_months") or 0)):
            mark = " `别名`" if r.get("alias_of") else ""
            lines.append(f"| [{r['name']}]({r['url']}){mark} | {r.get('coverage','')} "
                         f"| {r.get('coverage_page','') or '—'} "
                         f"| {r.get('freshness_lag_months','—')} 个月 | {r.get('priority','')} |")
        lines.append("")

    freq_conf = [r for r in records if r.get("freq_conflict") == "频率低频但区间标实时"]
    if freq_conf:
        lines.append(f"## 频率口径待确认（{len(freq_conf)} 条，页面两个口径多指不同交付版本）")
        lines.append("")
        lines.append("页面「更新频率」写低频（多为年度），同一页「时间区间」却标「实时更新」。")
        lines.append("**档案的 `frequency` 未改动**（原样抄录），证据见 `freq_note`。")
        lines.append("取证结论：这多半不是互斥事实，而是同一系列下的不同交付版本——")
        lines.append("批量历史库按年度滚动更新，实时/增量通道单独售卖（如股吧 API 每 30 分钟增量抓取）。")
        lines.append("所以**排期应分两档**：批量库按年度，实时需求走 API；不能一律按年度，也不能一律改日频。")
        lines.append("")
        # 绝对路径：INDEX.md 落盘在 data_sources/cnopendata/，取证件实际在 C:/项目/deliverables/，
        # 写相对路径按本目录解析就是悬空的（指向一个不存在的文件等于没指）。
        lines.append("> 逐条取证见 C:/项目/deliverables/数据源档案_CnOpenData_20261021/频率口径取证_待批.md"
                     "（归档副本，非本目录内文件）")
        lines.append("")
        lines.append("| 名称 | 优先级 | 档案频率 | 页面口径证据 |")
        lines.append("|---|---|---|---|")
        for r in sorted(freq_conf, key=lambda x: str(x.get("priority"))):
            lines.append(f"| [{r['name']}]({r['url']}) | {r.get('priority','')} "
                         f"| {r.get('frequency','')} | {str(r.get('freq_note',''))[:80]} |")
        lines.append("")

    lines.append("## 全量索引（按分类组）")
    lines.append("")
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r.get("category_group", "未分类"), []).append(r)
    for group, items in groups.items():
        lines.append(f"### {group}（{len(items)} 条）")
        lines.append("")
        lines.append("| 名称 | 分类 | 覆盖 | 量级 | 更新 | 优先级 | 获取 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in items:
            mark = " `别名`" if r.get("alias_of") else ""
            lines.append(
                f"| [{r['name']}]({r['url']}){mark} | {r.get('category','')} | {r.get('coverage','')} "
                f"| {str(r.get('volume',''))[:28]} | {r.get('frequency','')} "
                f"| {r.get('priority','')} | {r.get('access_mode','')} |"
            )
        lines.append("")

    if info_notes:
        lines.append("## 构建提示")
        lines.append("")
        for n in info_notes:
            lines.append(f"- {n}")
        lines.append("")

    if problems:
        lines.append("## 分片告警")
        lines.append("")
        for p in problems:
            lines.append(f"- {p}")
        lines.append("")

    INDEX.write_text("\n".join(lines), encoding="utf-8")

    print(f"OK 条目 {len(records)}（丢弃重复 {dropped}）")
    print(f"   {REGISTRY}")
    print(f"   {INDEX}")
    if problems:
        print(f"   告警 {len(problems)} 条，见 INDEX.md 末尾", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
