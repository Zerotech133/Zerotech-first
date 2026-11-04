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

    # 4 时效欠账
    stale: list[dict] = []
    undated = 0
    for name, meta in tables.items():
        meta = meta or {}
        if meta.get("static_no_date"):
            continue
        d = parse_freshness(meta.get("freshness"))
        if d is None:
            undated += 1
            continue
        lag = (today - d).days
        sla = _sla_days(data, meta.get("freq"))
        if lag > sla:
            stale.append(
                {
                    "table": name,
                    "source": meta.get("source"),
                    "freq": meta.get("freq"),
                    "freshness": d.isoformat(),
                    "lag_days": lag,
                    "sla_days": sla,
                }
            )
    stale.sort(key=lambda x: -x["lag_days"])

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
        lines += ["| 表 | 源 | 频率 | 最新数据 | 滞后 | SLA |", "|---|---|---|---|---|---|"]
        for row in rep["stale"][:40]:
            lines.append(
                f"| {row['table']} | {row['source']} | {row['freq']} | {row['freshness']} "
                f"| {row['lag_days']}d | {row['sla_days']}d |"
            )
        if len(rep["stale"]) > 40:
            lines.append(f"| … | 其余 {len(rep['stale']) - 40} 张省略 | | | | |")
    else:
        lines.append("无。")

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
