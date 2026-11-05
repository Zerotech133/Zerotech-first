# -*- coding: utf-8 -*-
"""治理对账：源登记 ↔ 数据目录 ↔ 仓物理登记，三方比对出缺口。

产出五张表（全部是工单，不是评价）：
  1 有表无源    catalog 里某表声称的 source 找不到对应登记源 → 来源不可追溯
  2 有源无表    sources 声明了 linked_tables，但 catalog 无任何表匹配 → 声明落空
  3 未声明覆盖  sources 的 linked_tables 为空 → 已登记但没落到表，或纯探测用
  4 时效欠账    catalog.freshness 超过频率 SLA → 该表数据已经不能用
  5 未登记资产  warehouse_registry 的物理表不在 catalog → 有数据但目录看不见

只读，不改任何文件。日期一律按 (年,月,日) 整数比较，不走字符串。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import registry

HERE = Path(__file__).resolve().parent
LIFECYCLE_DIR = HERE.parent / "datawarehouse"


def _lifecycle():
    """复用既有生命周期真源（planned/deprecated/archived 不参与时效判定）。

    不读它就会把已下线资产当成"断更"重复报警——实测 shibor_lpr 早已标 deprecated
    （上游 2026-08-20 停更），不读 lifecycle 时它仍出现在 115 张欠账里。
    """
    try:
        import sys

        if str(LIFECYCLE_DIR) not in sys.path:
            sys.path.insert(0, str(LIFECYCLE_DIR))
        import lifecycle  # noqa: WPS433

        return lifecycle
    except Exception:  # noqa: BLE001
        return None


def _load_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def parse_freshness(value) -> date | None:
    """catalog.freshness 形态为 'YYYYMMDD'；容错接受 'YYYY-MM-DD' 与前 10 位时间串。"""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _sla_days(data: dict, freq: str | None) -> int:
    table = data.get("freshness_sla_days") or {}
    return int(table.get(freq or "D", 3))


def _grade(lag_days: int) -> str:
    """时效欠账分级。口径写死在代码里，避免不同人算出不同结论。

    stopped  滞后 >180 天：上游已停更，应走 deprecate 而不是补采
    broken   36-180 天：采集链路断了，需排查
    lagging  SLA 至 36 天：批次漏跑或本就低频，需确认批次归属
    """
    if lag_days > 180:
        return "stopped"
    if lag_days > 36:
        return "broken"
    return "lagging"


def audit(*, today: date | None = None) -> dict:
    data = registry.load()
    catalog = _load_json(registry.CATALOG_FILE)
    wh = _load_json(registry.WAREHOUSE_REGISTRY)
    today = today or date.today()
    sources = registry.by_id(data)
    tables = catalog.get("tables") or {}
    src_map = data.get("catalog_source_map") or {}

    # 反查：渠道值 → 登记源 id 列表
    channel_to_ids: dict[str, list[str]] = {}
    for sid, src in sources.items():
        channel_to_ids.setdefault(sid, []).append(sid)
    for channel, ids in (src_map or {}).items():
        channel_to_ids[channel] = list(ids or [])

    # 1 有表无源
    by_channel: dict[str, list[str]] = {}
    for name, meta in tables.items():
        by_channel.setdefault(str((meta or {}).get("source") or "?"), []).append(name)
    orphan_tables: dict[str, dict] = {}
    for channel, names in by_channel.items():
        ids = channel_to_ids.get(channel)
        if not ids:
            orphan_tables[channel] = {
                "count": len(names),
                "sample": sorted(names)[:8],
            }

    # 2 有源无表 / 3 未声明覆盖
    declared_empty: list[str] = []
    matched_none: list[dict] = []
    for sid, src in sources.items():
        prefixes = registry.table_prefixes(src)
        if not prefixes:
            declared_empty.append(sid)
            continue
        hit = registry.match_tables(src, tables)
        if not hit:
            matched_none.append({"source": sid, "prefixes": list(prefixes)})

    # 4 时效欠账（生命周期非 active 的表不参与；允许按表覆盖 SLA）
    stale: list[dict] = []
    undated = 0
    lc = _lifecycle()
    exempt = set(data.get("freshness_exempt") or [])
    overrides = data.get("freshness_sla_overrides") or {}
    skipped_lifecycle: list[dict] = []
    for name, meta in tables.items():
        meta = meta or {}
        if meta.get("static_no_date"):
            continue
        if name in exempt:
            continue
        if lc is not None and lc.skipped(name):
            skipped_lifecycle.append({"table": name, "lifecycle": lc.status_for(name),
                                      "reason": lc.reason_for(name)[:120]})
            continue
        d = parse_freshness(meta.get("freshness"))
        if d is None:
            undated += 1
            continue
        lag = (today - d).days
        sla = int(overrides.get(name) or _sla_days(data, meta.get("freq")))
        if lag > sla:
            stale.append(
                {
                    "table": name,
                    "source": meta.get("source"),
                    "freq": meta.get("freq"),
                    "freshness": d.isoformat(),
                    "lag_days": lag,
                    "sla_days": sla,
                    "grade": _grade(lag),
                }
            )
    stale.sort(key=lambda x: -x["lag_days"])

    # 批次画像：同一 (source, freshness) 的表大概率属于同一采集批次。
    # 注意口径（2026-11-05 实证修正）：同一天止步**不等于**"该批次没跑"。
    # 实测 xtcloud 11-02~11-04 每天仍在写 21-30 个 parquet，说明每日批次在跑，
    # 只是只覆盖约 30 张表；其余表属于低频/历史快照，未被最近批次纳入。
    # 因此本表用于回答"哪些表没被最近批次覆盖"，不能读成"采集挂了"。
    profile: dict[tuple[str, str], list[str]] = {}
    for row in stale:
        profile.setdefault((row["source"], row["freshness"]), []).append(row["table"])
    batch_profile = [
        {"source": k[0], "freshness": k[1], "count": len(v), "sample": sorted(v)[:6]}
        for k, v in sorted(profile.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(v) >= 3
    ]

    # 5 未登记资产
    physical = set()
    for row in wh.get("physical_tables") or []:
        for t in row.get("physical_tables") or []:
            physical.add(str(t))
    unregistered = sorted(physical - set(tables))

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "scale": {
            "sources": len(sources),
            "catalog_tables": len(tables),
            "physical_tables": len(physical),
            "undated_tables": undated,
        },
        "orphan_tables": orphan_tables,
        "matched_none": matched_none,
        "declared_empty": declared_empty,
        "stale": stale,
        "batch_profile": batch_profile,
        "skipped_by_lifecycle": skipped_lifecycle,
        "unregistered": unregistered,
    }


def _md(rep: dict) -> str:
    s = rep["scale"]
    lines = [
        f"# 数据治理对账 · {rep['generated_at']}",
        "",
        f"源登记 {s['sources']} · 目录表 {s['catalog_tables']} · 物理表 {s['physical_tables']}"
        f" · 无日期表 {s['undated_tables']}",
        "",
        "## 一、有表无源（来源不可追溯）",
        "",
    ]
    if rep["orphan_tables"]:
        lines += ["| 渠道 | 表数 | 样例 |", "|---|---|---|"]
        for ch, info in sorted(rep["orphan_tables"].items(), key=lambda kv: -kv[1]["count"]):
            lines.append(f"| {ch} | {info['count']} | {', '.join(info['sample'])} |")
    else:
        lines.append("无。")

    lines += ["", "## 二、有源无表（声明落空）", ""]
    if rep["matched_none"]:
        for item in rep["matched_none"]:
            lines.append(f"- {item['source']}：前缀 {item['prefixes']} 在目录中无匹配表")
    else:
        lines.append("无。")

    lines += ["", "## 三、时效欠账（超频率 SLA）", ""]
    if rep["stale"]:
        by_grade: dict[str, list[dict]] = {}
        for row in rep["stale"]:
            by_grade.setdefault(row.get("grade", "lagging"), []).append(row)
        title = {
            "stopped": "A 停产（>180 天，应 deprecate 而非补采）",
            "broken": "B 断更（36-180 天，采集链路断了）",
            "lagging": "C 滞后（SLA-36 天，漏跑或本就低频）",
        }
        lines.append("分档：" + "，".join(f"{k} {len(v)}" for k, v in by_grade.items()))
        for grade in ("stopped", "broken", "lagging"):
            rows = by_grade.get(grade) or []
            if not rows:
                continue
            lines += ["", f"### {title[grade]}", "",
                      "| 表 | 源 | 频率 | 最新数据 | 滞后 | SLA |", "|---|---|---|---|---|---|"]
            for row in rows[:30]:
                lines.append(
                    f"| {row['table']} | {row['source']} | {row['freq']} | {row['freshness']} "
                    f"| {row['lag_days']}d | {row['sla_days']}d |"
                )
            if len(rows) > 30:
                lines.append(f"| … | 其余 {len(rows) - 30} 张省略 | | | | |")
    else:
        lines.append("无。")

    lc_skip = rep.get("skipped_by_lifecycle") or []
    if lc_skip:
        lines += ["", f"（另有 {len(lc_skip)} 张表生命周期为 planned/deprecated/archived，按契约不参与时效判定）", ""]
        for row in lc_skip[:10]:
            lines.append(f"- {row['table']}（{row['lifecycle']}）：{row['reason']}")

    profile = rep.get("batch_profile") or []
    if profile:
        lines += ["", "### 批次画像（同源同日期 ≥3 张：未被最近批次覆盖的表群）", "",
                  "| 源 | 最新数据止于 | 受影响表数 | 样例 |", "|---|---|---|---|"]
        for row in profile[:12]:
            lines.append(f"| {row['source']} | {row['freshness']} | {row['count']} | {', '.join(row['sample'])} |")

    lines += ["", "## 四、未登记物理表（有数据但目录看不见）", ""]
    if rep["unregistered"]:
        lines.append("共 %d 张，样例：%s" % (len(rep["unregistered"]), ", ".join(rep["unregistered"][:20])))
    else:
        lines.append("无。")

    lines += ["", "## 五、已登记但未声明表的源（探测型或待接）", "", ", ".join(rep["declared_empty"]) or "无。", ""]
    return "\n".join(lines)


def write_report(rep: dict, out_dir: Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir or (HERE.parent.parent / "reports" / "data_hub"))
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    js = out / f"govern_{stamp}.json"
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = out / f"govern_{stamp}.md"
    md.write_text(_md(rep), encoding="utf-8")
    return js, md
