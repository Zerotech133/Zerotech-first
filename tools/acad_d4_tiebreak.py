# -*- coding: utf-8 -*-
"""acad_d4_tiebreak.py — D4 冲突键 tie-break 策略模拟（只读，2026-10-31）。

背景：`tools/acad_d4_freeze_check.py` 已合并，D4 对账发现
  · top_list：13 个 (trade_date, ts_code) 重复键（13 冲突）；
  · top_inst：121 个 (trade_date, ts_code, exalter, side) 重复键（83 冲突、38 一致）；
  · 冲突键的 max(_fetched_at) **全部并列**（同批抓取）→「按时间择新」无法裁决。
本工具把「tie-break 到底怎么定」变成可复跑、可量化的模拟：对每个重复键列出候选行，
按多种策略各选一行，输出 采用/丢弃 行、对行数/manifest 的影响、以及策略间的
汇总字段差异（如 top_list 净额合计、top_inst 买卖额合计），**默认 dry-run、零写盘**。

策略（同一 `_fetched_at` 并列场景）
----------------------------------
  keep-first          按 part/行序保留第一行（最早出现）
  keep-last           按 part/行序保留最后一行（基准策略）
  keep-newest-fetched 保留 max(_fetched_at)，并列时按 keep-last（并报并列键数）
  prefer-side         top_inst：side=0 取 buy 最大行；side=1 取 sell 最大行；top_list 不适用
  keep-max-net        业务字段最大：top_list=net_amount，top_inst=net_buy
  keep-min-net        业务字段最小（同一字段；用于量化口径敏感度）
另含**键定义诊断**：top_list 若把 `reason` 纳入键（trade_date, ts_code, reason），
重复键是否归零——用于判断「13 冲突」是数据缺陷还是键定义过粗。

只读保证：复用 acad_d4_freeze_check 的只读加载；运行前后 (bytes, mtime_ns) 快照对比；
不写 parquet/manifest、不写回执、不代签。

用法
----
    python -X utf8 tools/acad_d4_tiebreak.py                 # 人类摘要
    python -X utf8 tools/acad_d4_tiebreak.py --json          # JSON（含逐键明细）
    python -X utf8 tools/acad_d4_tiebreak.py --data-root C:/tmp/fake --json

退出码：0=报告已产出；1=数据根/关键表不可读；2=用法错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# 本环境 python 以 safe_path 模式启动（脚本目录不进 sys.path），显式补 tools 目录
sys.path.insert(0, str(Path(__file__).resolve().parent))
import acad_d4_freeze_check as d4

SCHEMA = "acad_d4_tiebreak/v1"
STRATEGIES = ("keep-first", "keep-last", "keep-newest-fetched", "prefer-side",
              "keep-max-net", "keep-min-net")
BASELINE = "keep-last"
BUSINESS_FIELD = {"top_list": "net_amount", "top_inst": "net_buy"}
# 汇总口径（每个策略去重后全表合计，用于量化策略差异）
AGG_FIELDS = {"top_list": ("net_amount", "amount"), "top_inst": ("buy", "sell", "net_buy")}
# 键定义诊断：是否把某列纳入键后重复归零
ALT_KEY = {"top_list": ("trade_date", "ts_code", "reason")}


def _load_rows(data_root: Path, rel_dir: str) -> tuple[list[dict], list[str], str]:
    """返回 (rows, payload_cols, error)；rows 顺序 = 文件排序 + 行序（part/行序）。"""
    files = d4._table_files(data_root, rel_dir)
    table, err = d4._load_table(files)
    if err:
        return [], [], err
    return table.to_pylist(), list(table.column_names), ""


def _group(rows: list[dict], key_cols: tuple[str, ...]) -> dict[tuple, list[int]]:
    groups: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        key = tuple(r.get(c) for c in key_cols)
        groups.setdefault(key, []).append(i)
    return groups


def _payload(r: dict, cols: list[str], exclude: set[str]) -> dict:
    return {c: r.get(c) for c in cols if c not in exclude}


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _pick(strategy: str, rows: list[dict], idxs: list[int], business_field: str) -> int:
    """在同一个重复键的候选行中选择采用行；返回 rows 索引。"""
    if strategy == "keep-first":
        return idxs[0]
    if strategy == "keep-last":
        return idxs[-1]
    if strategy == "keep-newest-fetched":
        newest = max(str(r.get("_fetched_at") or "") for r in (rows[i] for i in idxs))
        cand = [i for i in idxs if str(rows[i].get("_fetched_at") or "") == newest]
        return cand[-1]  # 并列 → 按 part/行序靠后（确定性）
    if strategy == "prefer-side":
        if "side" not in rows[idxs[0]]:
            return idxs[-1]
        side = str(rows[idxs[0]].get("side") or "")
        if side == "0":
            field = "buy"
        elif side == "1":
            field = "sell"
        else:
            return idxs[-1]
        return max(idxs, key=lambda i: (_num(rows[i].get(field)), i))
    if strategy == "keep-max-net":
        return max(idxs, key=lambda i: (_num(rows[i].get(business_field)), i))
    if strategy == "keep-min-net":
        return min(idxs, key=lambda i: (_num(rows[i].get(business_field)), i))
    raise ValueError("未知策略：%s" % strategy)


def _aggregate(rows: list[dict], groups: dict[tuple, list[int]], strategy: str,
               business_field: str, agg_fields: tuple[str, ...]) -> dict:
    sums = {f: 0.0 for f in agg_fields}
    for key, idxs in groups.items():
        chosen = idxs[0] if len(idxs) == 1 else _pick(strategy, rows, idxs, business_field)
        r = rows[chosen]
        for f in agg_fields:
            v = r.get(f)
            try:
                sums[f] += float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                pass
    return {f: round(v, 4) for f, v in sums.items()}


def _analyse_table(rows: list[dict], cols: list[str], key_cols: tuple[str, ...],
                   name: str) -> dict:
    business_field = BUSINESS_FIELD[name]
    groups = _group(rows, key_cols)
    dup_keys = {k: idxs for k, idxs in groups.items() if len(idxs) > 1}
    extra_rows = sum(len(idxs) - 1 for idxs in dup_keys.values())
    payload_cols = [c for c in cols if c not in key_cols and c != "_fetched_at"]

    # 逐键：候选行 + 各策略采用/丢弃行
    key_records = []
    changed_counts: dict[str, int] = {s: 0 for s in STRATEGIES
                                      if not (s == "prefer-side" and name != "top_inst"
                                              and "side" not in cols)}
    identical_keys = 0
    for key, idxs in dup_keys.items():
        payloads = [tuple(rows[i].get(c) for c in payload_cols) for i in idxs]
        is_identical = len(set(payloads)) == 1
        if is_identical:
            identical_keys += 1
        selected = {}
        dropped = {}
        for s in changed_counts:
            chosen = _pick(s, rows, idxs, business_field)
            selected[s] = idxs.index(chosen)
        for s in changed_counts:
            dropped[s] = [j for j in range(len(idxs)) if j != selected[s]]
        if BASELINE in selected:
            base_payload = payloads[selected[BASELINE]]
            for s in changed_counts:
                # 语义差异按 payload 判定：完全一致重复键不因行序不同算「改选」
                if s != BASELINE and payloads[selected[s]] != base_payload:
                    changed_counts[s] += 1
        versions = []
        for j, i in enumerate(idxs):
            versions.append({
                "id": j,
                "_fetched_at": rows[i].get("_fetched_at"),
                "payload": _payload(rows[i], payload_cols, set()),
            })
        key_records.append({
            "key": list(key),
            "versions": versions,
            "identical": is_identical,
            "selected": selected,
            "dropped": dropped,
        })

    # 冲突键带来的额外行数（用于 safe_dedup_rows：只折叠 identical 重复）
    identical_extra = conflict_extra = 0
    for rec in key_records:
        extra = len(rec["versions"]) - 1
        if rec["identical"]:
            identical_extra += extra
        else:
            conflict_extra += extra

    # 各策略：行数/manifest 投影/汇总字段/与基准差异
    manifest_rows = None
    strategies: dict[str, dict] = {}
    for s in changed_counts:
        agg = _aggregate(rows, groups, s, business_field, AGG_FIELDS[name])
        strategies[s] = {
            "rows_before": len(rows),
            "rows_after": len(groups),
            "rows_dropped": len(rows) - len(groups),
            "keys_changed_vs_baseline": changed_counts.get(s, 0) if s != BASELINE else 0,
            "aggregates": agg,
        }
    base_agg = strategies[BASELINE]["aggregates"]
    for s, info in strategies.items():
        info["aggregate_delta_vs_baseline"] = {
            f: round(info["aggregates"][f] - base_agg[f], 4) for f in info["aggregates"]
        }

    # 键定义诊断
    alt = {}
    alt_cols = ALT_KEY.get(name)
    if alt_cols:
        alt_groups = _group(rows, alt_cols)
        alt_dup = sum(1 for idxs in alt_groups.values() if len(idxs) > 1)
        alt = {"key": list(alt_cols), "dup_keys": alt_dup,
               "note": "把该列纳入键后重复键归零=原「冲突」为合法多行；否则为真重复"}

    return {
        "rows": len(rows),
        "keys": len(groups),
        "dup_keys": len(dup_keys),
        "dup_extra_rows": extra_rows,
        "identical_keys": identical_keys,
        "conflicting_keys": len(dup_keys) - identical_keys,
        "identical_extra_rows": identical_extra,
        "conflict_extra_rows": conflict_extra,
        "fields_missing": [f for f in AGG_FIELDS[name] + (business_field,) if f not in cols],
        "key_cols": list(key_cols),
        "business_field": business_field,
        "manifest_rows": manifest_rows,
        "strategies": strategies,
        "alt_key_analysis": alt,
        "keys_detail": key_records,
    }


def run_sim(data_root: Path) -> dict:
    data_root = Path(data_root)
    if not data_root.is_dir():
        raise FileNotFoundError("数据根不存在：%s" % data_root)
    manifest, manifest_err = d4._read_manifest(data_root)

    # 只读快照
    snap_paths = [data_root / d4.MANIFEST_REL]
    for spec in d4.D4_TABLES.values():
        snap_paths.extend(d4._table_files(data_root, spec["dir"]))
    snap_paths.extend(d4._table_files(data_root, d4.CAL_REL))
    before = d4._snapshot(snap_paths)

    tables: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for name, spec in d4.D4_TABLES.items():
        rows, cols, err = _load_rows(data_root, spec["dir"])
        if err:
            errors[name] = err
            continue
        info = _analyse_table(rows, cols, spec["key"], name)
        man = manifest.get(spec["dir"].split("/")[-1]) if isinstance(manifest, dict) else None
        man_rows = man.get("rows") if isinstance(man, dict) else None
        info["manifest_rows"] = man_rows
        info["manifest_projection"] = {
            "manifest_rows": man_rows,
            "rows_after_all_dedup": info["keys"],
            "rows_after_safe_dedup": info["rows"] - info["identical_extra_rows"],
            "projected_diff_all_dedup": (info["keys"] - man_rows) if isinstance(man_rows, int) else None,
            "projected_diff_safe_dedup": (info["rows"] - info["identical_extra_rows"] - man_rows)
            if isinstance(man_rows, int) else None,
            "note": "manifest 应对齐实际行数：先折叠 identical 重复；冲突键是否保留/重采由 DATA1 定，"
                    "不要在数据层静默择一",
        }
        tables[name] = info

    after = d4._snapshot(snap_paths)
    readonly = {
        "files": len(before),
        "unchanged": before == after,
        "note": "只读 dry-run：(bytes, mtime_ns) 前后一致" if before == after else
                "检测到文件元数据变化（异常，请复核）",
    }

    # 推荐口径（基于模拟结论，非代签）
    recommendations = []
    tl = tables.get("top_list")
    ti = tables.get("top_inst")
    if tl:
        alt = tl.get("alt_key_analysis") or {}
        if alt.get("dup_keys") == 0:
            recommendations.append(
                "top_list：按 (trade_date,ts_code,reason) 键重复归零 → 13 键为「同股同日多上榜原因」合法多行，"
                "**不应按 (trade_date,ts_code) 去重**；冻结键定义应含 reason（或保留多行），否则会丢失上榜原因维度并篡改净额合计。")
        else:
            recommendations.append(
                "top_list：reason 纳入键后仍有 %s 个重复键 → 属真重复，需 DATA1 定 tie-break。" % alt.get("dup_keys"))
    if ti:
        tied_conflict = sum(
            1 for k in ti["keys_detail"]
            if not k["identical"] and len({str(v["_fetched_at"]) for v in k["versions"]}) == 1)
        n_conflict = ti["conflicting_keys"]
        recommendations.append(
            "top_inst：%d 冲突键中 %d 个的 _fetched_at 全部并列，且全部落在同一 part 同批 → "
            "时间/顺序类规则均无业务依据；建议 ① %d 个「完全一致」键安全折叠（可减 %d 行）；"
            "② %d 冲突键**保留并标 conflict**（不静默择一），或重采 20260930 上游数据后按新批次 sha 重算；"
            "③ 若必须择一，模拟量化了各策略差异（见 JSON strategies/aggregate_delta_vs_baseline），"
            "但任何择一都属业务假设，需 DATA1/ACAD1 书面拍板后才可进冻结回执。"
            % (n_conflict, tied_conflict, ti["identical_keys"], ti["identical_extra_rows"], n_conflict))
    recommendations.append(
        "建议 DATA1 在 11-03 冻结前：① top_list 改键口径（含 reason）；② top_inst 先折叠 identical 38 键，"
        "冲突 83 键重采或标 conflict；③ 之后重算 manifest 并对齐实际行数（当前投影差见 manifest_projection）。")

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_root": str(data_root),
        "dry_run": True,
        "strategies": list(STRATEGIES),
        "baseline": BASELINE,
        "tables": tables,
        "errors": errors,
        "manifest_error": manifest_err,
        "readonly": readonly,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------- 输出 / CLI

def format_human(report: dict) -> str:
    lines = ["D4 tie-break 策略模拟（只读 dry-run）｜数据根：%s" % report["data_root"]]
    for name, info in report["tables"].items():
        lines.append("  [%s] 行 %d → 全键去重后 %d / 仅折叠 identical 后 %d（额外 %d；一致 %d 键 / 冲突 %d 键）"
                     % (name, info["rows"], info["keys"],
                        info["rows"] - info["identical_extra_rows"],
                        info["dup_extra_rows"], info["identical_keys"], info["conflicting_keys"]))
        for s in report["strategies"]:
            if s not in info["strategies"]:
                continue
            st = info["strategies"][s]
            delta = st["aggregate_delta_vs_baseline"]
            nz = {f: v for f, v in delta.items() if abs(v) > 1e-9}
            lines.append("     %-20s payload 改选 %s 键 | 汇总差异%s"
                         % (s, st["keys_changed_vs_baseline"],
                            (" " + ", ".join("%s%+.2f" % (f, v) for f, v in nz.items())) if nz else " 无"))
        alt = info.get("alt_key_analysis") or {}
        if alt:
            lines.append("     键诊断：%s → 重复键 %s（%s）"
                         % ("+".join(alt["key"]), alt["dup_keys"], "合法多行" if alt["dup_keys"] == 0 else "仍有重复"))
        mp = info.get("manifest_projection") or {}
        lines.append("     manifest=%s；全键去重差=%s；仅折叠 identical 差=%s"
                     % (mp.get("manifest_rows"), mp.get("projected_diff_all_dedup"),
                        mp.get("projected_diff_safe_dedup")))
    lines.append("  只读：%d 文件快照前后一致=%s" % (report["readonly"]["files"], report["readonly"]["unchanged"]))
    for r in report["recommendations"]:
        lines.append("  建议：%s" % r)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="acad_d4_tiebreak.py",
        description="D4 冲突键 tie-break 策略模拟（只读 dry-run）：候选行/采用/丢弃/行数与汇总影响。",
        epilog="退出码：0=报告已产出；1=数据根/关键表不可读；2=用法错误。",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON（含逐键明细）")
    ap.add_argument("--data-root", default=None, help="数据根（默认 <主工作树>/quant_backtest/data）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    data_root = d4.resolve_data_root(args.data_root)
    try:
        report = run_sim(data_root)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc)
        return 1
    if not report["readonly"]["unchanged"]:
        print("WARN: 只读快照检测到文件元数据变化，请复核", file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_human(report))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
