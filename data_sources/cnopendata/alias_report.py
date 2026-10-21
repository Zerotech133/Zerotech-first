#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把重复挂载的同名记录整理成核对清单，供人拍板。

为什么不能只列个"20 组重复"：
    两组记录如果描述/覆盖/量级互相矛盾，那不是"同一数据集的两条路径"，
    而是**至少有一条写错了**。机器能做的只是把差异摆出来，
    判断哪条对必须靠人（可能两个类目下的口径真的不同）。

输出分三档：
    A 结构字段全一致  → 纯重复，合并风险低
    B 有字段冲突      → 两个席位记录了不同事实，必须先查页面原文再定
    C 分类不同但一致  → 保留正本，另一条留别名即可

用法：
    python alias_report.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
REGISTRY = BASE / "registry.json"
TITLES = BASE / "_title_health.json"
OUT = BASE / "同名数据集核对清单.md"
GROUPS = BASE / "_alias_groups.json"

# 内容字段：两条记录不一致说明至少一条写错了（用 fact_key 归一后比）
CONTENT_FIELDS = [("coverage", "时间覆盖"), ("volume", "量级"),
                  ("frequency", "更新频率"), ("access_mode", "获取方式")]
# 分诊字段：数据集身份不变，只是两个席位分诊结论不同
TRIAGE_FIELDS = [("layer", "圈层"), ("priority", "优先级"),
                 ("auth_status", "授权状态"), ("api_available", "有无 API")]


def fact_key(value) -> tuple:
    """把同一事实的不同写法归一。

    不归一会怎样：`1993 至 2023` 与 `1993-2023年` 被判成冲突，
    19/20 组全报警，真正的问题（一条写 2023 年止、一条写至今）反而被淹掉。
    这就是告警疲劳——报出来的东西里大部分是噪音，人就整份不看了。
    """
    s = "" if value is None else str(value).strip()
    if not s:
        return ("empty",)
    if s.startswith("未标注"):
        return ("unlabeled",)
    digits = re.findall(r"\d+", s)
    if digits:
        # 必须转 int：`2014-05` 与 `2014年5月` 提取出的是 ("2014","05") 与 ("2014","5")，
        # 按字符串比会判成冲突，前导零把同一事实拆成两条。
        return ("num", tuple(int(x) for x in digits))
    return ("txt", re.sub(r"\s+", "", s).lower())


def tz_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def main() -> int:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    datasets = payload["datasets"]
    title_ok: dict[str, str] = {}
    if TITLES.is_file():
        for row in json.loads(TITLES.read_text(encoding="utf-8")).get("rows", []):
            title_ok[row["id"]] = row.get("verdict", "?")

    groups: dict[str, list[dict]] = {}
    for d in datasets:
        if d.get("alias_of"):
            groups.setdefault(d["alias_of"], []).append(d)
    by_id = {d["id"]: d for d in datasets}

    bucket_a, bucket_b, bucket_c = [], [], []
    for canon_id, alist in sorted(groups.items()):
        canon = by_id.get(canon_id)
        if canon is None:
            continue
        for alias in alist:
            content = [(label, str(canon.get(f)), str(alias.get(f)))
                       for f, label in CONTENT_FIELDS
                       if fact_key(canon.get(f)) != fact_key(alias.get(f))]
            triage = [(label, str(canon.get(f)), str(alias.get(f)))
                      for f, label in TRIAGE_FIELDS
                      if canon.get(f) != alias.get(f)]
            tags_a, tags_b = set(canon.get("tags") or []), set(alias.get("tags") or [])
            entry = {"canon": canon, "alias": alias, "content": content, "triage": triage,
                     "tags_shared": len(tags_a & tags_b),
                     "tags_union": len(tags_a | tags_b)}
            if content:
                bucket_b.append(entry)
            elif triage:
                bucket_c.append(entry)
            else:
                bucket_a.append(entry)

    now = tz_now()
    lines = [
        "# 同名数据集核对清单（重复挂载）", "",
        f"> 生成时间：{now.isoformat(timespec='seconds')}　｜　档案 {payload['meta']['total_records']} 条"
        f" / 唯一数据集 {payload['meta']['unique_datasets']} 个　｜　待核对 **{len(groups)} 组**", "",
        "> **处置状态**：系统已给非正本打 `alias_of` 指向正本，**未删除任何记录**。",
        "> 本清单只整理证据，合不合并需人定；判断依据是下面「字段冲突」栏——",
        "> 有冲突说明两个席位对同一数据集记录了不同事实，至少一条是错的。", "",
        "## 汇总", "",
        "| 分类 | 组数 | 含义 |",
        "|---|---|---|",
        f"| B 内容字段冲突 | {len(bucket_b)} | 同一字段两条写得不一样，至少一条错，必须先核原文 |",
        f"| C 仅分诊不一致 | {len(bucket_c)} | 数据集同一份，只是两个席位分诊结论不同（优先级/圈层） |",
        f"| A 内容与分诊都一致 | {len(bucket_a)} | 纯重复，合并风险最低 |",
        "",
        "> 已归一化再比：`1993 至 2023` 与 `1993-2023年` 算一致（提取数字序列相同），",
        "> 否则 19/20 组全会报警，真问题反而被淹。", "",
    ]

    def render(entries: list[dict], title: str, note: str) -> None:
        lines.append(f"## {title}（{len(entries)} 组）")
        lines.append("")
        if note:
            lines.append(f"> {note}")
            lines.append("")
        if not entries:
            lines.append("_无_")
            lines.append("")
            return
        for i, e in enumerate(entries, 1):
            c, a = e["canon"], e["alias"]
            lines.append(f"### {i}. {c.get('name','')}")
            lines.append("")
            lines.append("| 角色 | id | 类目 | 链接 | 标题校验 |")
            lines.append("|---|---|---|---|---|")
            for role, rec in (("正本", c), ("别名", a)):
                verdict = title_ok.get(rec["id"], "未校验")
                lines.append(f"| {role} | `{rec['id']}` | {rec.get('category','')} | {rec['url']} | {verdict} |")
            lines.append("")
            for label, rows, gap in (("内容字段冲突", e["content"], "至少一条写错了"),
                                     ("分诊不一致", e["triage"], "数据集同一份，分诊结论不同")):
                if not rows:
                    continue
                lines.append(f"**{label}**（{gap}）：")
                lines.append("")
                lines.append("| 字段 | 正本 | 别名 |")
                lines.append("|---|---|---|")
                for name, va, vb in rows:
                    lines.append(f"| {name} | {va} | {vb} |")
                lines.append("")
            lines.append(f"标签重合：{e['tags_shared']}/{e['tags_union']}")
            lines.append("")
            dc = (c.get("desc") or "").strip()
            da = (a.get("desc") or "").strip()
            lines.append(f"- 正本描述（{len(dc)} 字）：{dc}")
            lines.append(f"- 别名描述（{len(da)} 字）：{da}")
            if (c.get("notes") or "").strip() or (a.get("notes") or "").strip():
                lines.append(f"- 正本备注：{(c.get('notes') or '').strip() or '（空）'}")
                lines.append(f"- 别名备注：{(a.get('notes') or '').strip() or '（空）'}")
            lines.append("")

    render(bucket_b, "B 内容字段冲突（优先核）",
           "同一字段两条写得不一样。请打开两条链接对照页面原文，确认哪条写错；"
           "若两条其实是不同产品（如一个是 API、一个是数据集下载），则不该合并，应分别登记。")
    render(bucket_c, "C 仅分诊不一致",
           "数据集同一份，只是两个席位给的优先级/圈层不同。合并前需定一个口径，否则统计会双重计入。")
    render(bucket_a, "A 内容与分诊都一致",
           "纯重复挂载，确认后可按「保留正本、别名留痕」处理。")

    lines += [
        "## 处置选项（需老田定）", "",
        "1. **保持现状**：两条都留，`alias_of` 已标，下游按 `unique_datasets` 统计。零风险，但页面有同名列。",
        "2. **合并保留一条**：从分片里删掉别名记录，重跑 `build_registry.py`。会减少 20 条记录，需接受不可回退（分片仍在 git/备份里）。",
        "3. **分组处理**：B 类先核原文再定，A 类直接合并。", "",
        "> 我不会替你做这个决定：删记录是不可逆动作，且 B 类冲突可能意味着某条记录的关键口径（时间覆盖/量级）是错的，",
        "> 采信哪条会直接影响接入方案。", "",
    ]

    OUT.write_text("\n".join(lines), encoding="utf-8")

    # 机读版分组：给 alias_probe.py 用，避免它自己再算一套分类（同口径两处实现必漂移）
    machine = {
        "generated_at": now.isoformat(timespec="seconds"),
        "b_content_conflict": [{"canonical": e["canon"]["id"], "alias": e["alias"]["id"],
                                "name": e["canon"].get("name", ""),
                                "fields": [c[0] for c in e["content"]]} for e in bucket_b],
        "c_triage_only": [{"canonical": e["canon"]["id"], "alias": e["alias"]["id"],
                           "name": e["canon"].get("name", "")} for e in bucket_c],
        "a_identical": [{"canonical": e["canon"]["id"], "alias": e["alias"]["id"],
                         "name": e["canon"].get("name", "")} for e in bucket_a],
    }
    GROUPS.write_text(json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK {OUT}")
    print(f"   B 内容冲突 {len(bucket_b)} / C 仅分诊 {len(bucket_c)} / A 全一致 {len(bucket_a)}")
    for e in bucket_b:
        names = "；".join(f"{label}({va} vs {vb})" for label, va, vb in e["content"][:3])
        print(f"   ! {e['canon']['name']}: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
