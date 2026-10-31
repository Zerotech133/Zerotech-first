# -*- coding: utf-8 -*-
"""acad_d4_freeze_check.py — ACAD1/DATA1 D4 冻结对账（只读，2026-10-31）。

把 P0-4 报告 `docs/audit/2026-10-31_W3_ACAD1冻结与Go条件_v1.md` §2.3/§2.5
的「D4 11-03 最小补齐清单」变成可复跑只读工具，供 DATA1/ACAD1 在出冻结回执前
逐项核对；**不写数据、不写回执、不代签、不改任何账本**。

检查项
------
1) 交易日缺口：D4 两表 distinct trade_date vs `ts_trade_cal`（默认 SSE 开市日）；
2) manifest 行数 vs parquet 实际：`quant_backtest/data/tdx/_manifest.json`；
3) 同键多版本/冲突：
     top_list 键 = (trade_date, ts_code)
     top_inst  键 = (trade_date, ts_code, exalter, side)
   区分「完全一致重复」与「payload 冲突」；并统计冲突键中 max(_fetched_at)
   是否并列（并列=择新规则无法唯一裁决，冻结口径必须写死 tie-break）；
4) 候选票行数：默认 600519.SH 在两表各多少行（0 行 → L3 恒 unknown）；
5) 去重 dry-run：同键按 `_fetched_at` 择新（并列时按 part/行序取最后，仅演示），
   只输出 before/after 计划，**不落盘**。

只读保证：parquet/manifest 全部只读；运行前后对相关文件做 (bytes, mtime_ns) 快照
对比，写入报告 `readonly` 字段；不创建/删除/改写任何文件。

数据根：默认主工作树 `<main>/quant_backtest/data`（worktree 内自动回落主仓，
复用 tools/_repo_paths.py）；可用 `--data-root` 或环境变量 `ACAD_D4_DATA_ROOT` 覆盖。

用法
----
    python -X utf8 tools/acad_d4_freeze_check.py            # 人类摘要
    python -X utf8 tools/acad_d4_freeze_check.py --json     # 机读 JSON
    python -X utf8 tools/acad_d4_freeze_check.py --data-root C:/tmp/fake_data --code 600519.SH

退出码：0=对账报告已产出（有缺口也返回 0，缺口在报告里如实列出）；
        1=硬错误（数据根不存在 / 关键表不可读）；
        2=用法错误（argparse）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

DEFAULT_CANDIDATE = "600519.SH"
MANIFEST_REL = "tdx/_manifest.json"
CAL_REL = "tdx/ts_trade_cal"
D4_TABLES = {
    "top_list": {"dir": "tdx/ts_top_list", "key": ("trade_date", "ts_code")},
    "top_inst": {"dir": "tdx/ts_top_inst", "key": ("trade_date", "ts_code", "exalter", "side")},
}
SCHEMA = "acad_d4_freeze_check/v1"
EXAMPLES_LIMIT = 3


# ---------------------------------------------------------------- 路径 / 只读

def _repo_root() -> Path:
    """主工作树根：普通 checkout 返回自身；linked worktree 解析 .git 回主仓。"""
    here = Path(__file__).resolve()
    try:
        sys.path.insert(0, str(here.parent))
        from _repo_paths import repo_root  # type: ignore

        return Path(repo_root(here))
    except Exception:  # noqa: BLE001 - 兜底：按本文件相对根推断
        return here.parents[1]
    finally:
        try:
            sys.path.pop(0)
        except IndexError:
            pass


def resolve_data_root(arg: str | None = None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("ACAD_D4_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cand = _repo_root() / "quant_backtest" / "data"
    if cand.is_dir():
        return cand.resolve()
    return (_repo_root() / "quant_backtest" / "data").resolve()


def _snapshot(paths: list[Path]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in paths:
        try:
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return out


def _table_files(data_root: Path, rel_dir: str) -> list[Path]:
    d = (data_root / rel_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.rglob("*.parquet") if p.is_file())


def _load_table(files: list[Path]):
    """返回 (table, error)。table 为 pyarrow.Table；error 为不可读原因。"""
    if not files:
        return None, "目录不存在或无 parquet 文件"
    try:
        import pyarrow.parquet as pq  # 延迟导入：--help/用法错误不依赖 pyarrow

        return pq.read_table([str(f) for f in files]), None
    except Exception as exc:  # noqa: BLE001 - 读不了要如实报，不吞
        return None, "parquet 读取失败：%r" % (exc,)


# ---------------------------------------------------------------- 检查项

def _truthy_open(value) -> bool:
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def check_trade_cal(data_root: Path, data_min: str, data_max: str) -> dict:
    files = _table_files(data_root, CAL_REL)
    table, err = _load_table(files)
    if err:
        return {"error": err, "files": len(files), "ok": False}
    cal = table.to_pylist()
    open_days = sorted(
        str(r.get("cal_date")) for r in cal
        if str(r.get("exchange") or "").upper() == "SSE" and _truthy_open(r.get("is_open"))
    )
    expected = [d for d in open_days if data_min and data_max and data_min <= d <= data_max]
    return {
        "exchange": "SSE",
        "files": len(files),
        "calendar_open_days": len(open_days),
        "range": [data_min, data_max],
        "expected_open_days": len(expected),
        "expected_dates": expected,
        "ok": bool(expected),
    }


def analyze_duplicates(table, key_cols: tuple[str, ...]) -> dict:
    """同键多版本/冲突统计 + 去重 dry-run（不改数据）。"""
    columns = list(table.column_names)
    payload = [c for c in columns if c not in key_cols and c != "_fetched_at"]
    data = {c: table.column(c).to_pylist() for c in list(key_cols) + payload + ["_fetched_at"]}
    rows = table.num_rows

    groups: dict[tuple, list[int]] = {}
    for i in range(rows):
        key = tuple(data[c][i] for c in key_cols)
        groups.setdefault(key, []).append(i)

    dup_keys = {k: idxs for k, idxs in groups.items() if len(idxs) > 1}
    dup_extra_rows = sum(len(idxs) - 1 for idxs in dup_keys.values())
    identical_keys = 0
    conflicting: dict[tuple, list[int]] = {}
    for key, idxs in dup_keys.items():
        payloads = {tuple(data[c][i] for c in payload) for i in idxs}
        if len(payloads) == 1:
            identical_keys += 1
        else:
            conflicting[key] = idxs

    tie_max = unique_max = 0
    for idxs in conflicting.values():
        stamps = [str(data["_fetched_at"][i] or "") for i in idxs]
        newest = max(stamps)
        if stamps.count(newest) == 1:
            unique_max += 1
        else:
            tie_max += 1

    # 去重 dry-run：同键取 _fetched_at 最大者；并列时按「part/行序靠后」兜底（仅演示）
    picked = {}
    for key, idxs in groups.items():
        picked[key] = max(idxs, key=lambda i: (str(data["_fetched_at"][i] or ""), i))
    examples = []
    for key, idxs in list(conflicting.items())[:EXAMPLES_LIMIT]:
        versions = []
        for i in idxs[:3]:
            versions.append({"_fetched_at": data["_fetched_at"][i],
                             "payload": {c: data[c][i] for c in payload}})
        examples.append({"key": list(key), "versions": versions})

    return {
        "rows": rows,
        "keys": len(groups),
        "dup_keys": len(dup_keys),
        "dup_extra_rows": dup_extra_rows,
        "identical_keys": identical_keys,
        "conflicting_keys": len(conflicting),
        "conflict_newest_unique": unique_max,
        "conflict_newest_tied": tie_max,
        "payload_cols": payload,
        "examples": examples,
    }


def count_candidate(table, code: str) -> int:
    if "ts_code" not in table.column_names:
        return 0
    return sum(1 for c in table.column("ts_code").to_pylist() if c == code)


def _read_manifest(data_root: Path) -> tuple[dict, str]:
    p = data_root / MANIFEST_REL
    try:
        return json.loads(p.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return {}, "manifest 不存在"
    except (OSError, json.JSONDecodeError) as exc:
        return {}, "manifest 不可读：%r" % (exc,)


def run_check(data_root: Path, candidate: str = DEFAULT_CANDIDATE) -> dict:
    """执行全部只读对账，返回报告 dict（不写盘）。"""
    data_root = Path(data_root)
    if not data_root.is_dir():
        raise FileNotFoundError("数据根不存在：%s" % data_root)

    manifest, manifest_err = _read_manifest(data_root)
    # 只读快照：两表 parquet + manifest + 交易日历文件
    snap_paths: list[Path] = [data_root / MANIFEST_REL]
    for spec in D4_TABLES.values():
        snap_paths.extend(_table_files(data_root, spec["dir"]))
    snap_paths.extend(_table_files(data_root, CAL_REL))
    before = _snapshot(snap_paths)

    tables: dict[str, dict] = {}
    duplicates: dict[str, dict] = {}
    dedup_tables: dict[str, dict] = {}
    loaded: dict[str, object] = {}
    for name, spec in D4_TABLES.items():
        files = _table_files(data_root, spec["dir"])
        table, err = _load_table(files)
        if err:
            tables[name] = {"dir": spec["dir"], "key": list(spec["key"]),
                            "files": len(files), "error": err}
            continue
        loaded[name] = table
        rows = table.num_rows
        man = manifest.get(spec["dir"].split("/")[-1]) if isinstance(manifest, dict) else None
        man_rows = man.get("rows") if isinstance(man, dict) else None
        tables[name] = {
            "dir": spec["dir"],
            "key": list(spec["key"]),
            "files": len(files),
            "rows": rows,
            "manifest_rows": man_rows,
            "manifest_row_diff": (rows - man_rows) if isinstance(man_rows, int) else None,
            "manifest_ok": isinstance(man_rows, int) and man_rows == rows,
        }
        duplicates[name] = analyze_duplicates(table, spec["key"])
        dedup_tables[name] = {
            "rows_before": duplicates[name]["rows"],
            "rows_after": duplicates[name]["keys"],
            "rows_dropped": duplicates[name]["rows"] - duplicates[name]["keys"],
            "conflicting_keys": duplicates[name]["conflicting_keys"],
            "newest_unique_keys": duplicates[name]["conflict_newest_unique"],
            "newest_tied_keys": duplicates[name]["conflict_newest_tied"],
        }

    # 交易日缺口（以两表覆盖的并集为准）
    data_dates: set[str] = set()
    for table in loaded.values():
        data_dates |= {str(d) for d in table.column("trade_date").to_pylist()}
    data_min = min(data_dates) if data_dates else ""
    data_max = max(data_dates) if data_dates else ""
    trade_cal = check_trade_cal(data_root, data_min, data_max)
    if "expected_dates" in trade_cal:
        expected = trade_cal.pop("expected_dates")
        covered = sorted(d for d in data_dates if data_min <= d <= data_max)
        trade_cal.update({
            "covered_days": len(covered),
            "missing_days": [d for d in expected if d not in data_dates],
            "extra_days": [d for d in covered if d not in set(expected)],
        })
        trade_cal["coverage_ok"] = (not trade_cal["missing_days"]) and trade_cal["expected_open_days"] > 0

    candidate_counts = {
        "code": candidate,
        "top_list_rows": count_candidate(loaded["top_list"], candidate) if "top_list" in loaded else None,
        "top_inst_rows": count_candidate(loaded["top_inst"], candidate) if "top_inst" in loaded else None,
    }
    both_zero = candidate_counts["top_list_rows"] == 0 and candidate_counts["top_inst_rows"] == 0
    hard_tables = [n for n, t in tables.items() if "error" in t]
    cal_error = "error" in trade_cal
    if hard_tables or cal_error:
        candidate_counts["l3"] = "unreadable"
    else:
        candidate_counts["l3"] = "unknown" if both_zero else "present"

    after = _snapshot(snap_paths)
    readonly = {
        "files": len(before),
        "unchanged": before == after,
        "note": "parquet/manifest/交易日历均只读；(bytes, mtime_ns) 前后一致" if before == after else
                "检测到文件元数据变化（异常，请复核）",
    }

    gaps: dict = {}
    findings: dict = {}
    if cal_error:
        gaps["trade_cal_error"] = trade_cal["error"]
    elif trade_cal.get("ok") and trade_cal.get("missing_days"):
        gaps["trade_cal_missing_days"] = trade_cal["missing_days"]
    mismatched = [n for n, t in tables.items()
                  if "error" in t or (t.get("manifest_ok") is False)]
    if mismatched:
        gaps["manifest_mismatched_tables"] = mismatched
    conflicts = {n: d["conflicting_keys"] for n, d in duplicates.items() if d["conflicting_keys"]}
    if conflicts:
        gaps["duplicate_conflicting_keys"] = conflicts
    tied = {n: d["conflict_newest_tied"] for n, d in duplicates.items() if d["conflict_newest_tied"]}
    if tied:
        gaps["dedup_ambiguous_keys"] = tied
    if manifest_err:
        gaps["manifest_error"] = manifest_err
    # 非阻塞观察：候选 0 行属候选选择问题（W3 §5-D），不改变 D4 数据对账闭合判定
    if both_zero and not (hard_tables or cal_error):
        findings["candidate_zero_rows"] = candidate
    no_hard = not hard_tables and not cal_error
    closed = no_hard and not gaps
    if hard_tables:
        verdict = "D4 对账失败：%s 不可读" % "/".join(hard_tables)
    elif cal_error:
        verdict = "D4 对账失败：交易日历不可读（%s）" % trade_cal["error"]
    elif closed and both_zero:
        verdict = "D4 数据对账通过（但候选 %s 两表 0 行 → L3 恒 unknown，候选问题待 ACAD1 决定）" % candidate
    elif closed:
        verdict = "D4 对账通过（可进入冻结回执四字段登记）"
    else:
        verdict = "D4 未闭合：%s" % "；".join(
            "%s=%s" % (k, v if not isinstance(v, (list, dict)) else len(v))
            for k, v in gaps.items())

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_root": str(data_root),
        "candidate_code": candidate,
        "tables": tables,
        "trade_cal": trade_cal,
        "duplicates": duplicates,
        "candidate": candidate_counts,
        "dedup_plan": {
            "mode": "dry-run（不写数据）",
            "rule": "同键取 max(_fetched_at)；并列时按 part/行序靠后（演示用，需 DATA1 冻结前写死口径）",
            "tables": dedup_tables,
        },
        "gaps": gaps,
        "gaps_count": len(gaps),
        "findings": findings,
        "findings_count": len(findings),
        "readonly": readonly,
        "verdict": verdict,
    }


# ---------------------------------------------------------------- 输出 / CLI

def _fmt(n) -> str:
    return "-" if n is None else "{:,}".format(n)


def format_human(report: dict) -> str:
    lines = [
        "D4 冻结对账（只读）｜数据根：%s" % report.get("data_root"),
        "  top_list : %s 行 / %s 文件；manifest %s（差 %s）"
        % (_fmt(report["tables"].get("top_list", {}).get("rows")),
           report["tables"].get("top_list", {}).get("files"),
           _fmt(report["tables"].get("top_list", {}).get("manifest_rows")),
           _fmt(report["tables"].get("top_list", {}).get("manifest_row_diff"))),
        "  top_inst : %s 行 / %s 文件；manifest %s（差 %s）"
        % (_fmt(report["tables"].get("top_inst", {}).get("rows")),
           report["tables"].get("top_inst", {}).get("files"),
           _fmt(report["tables"].get("top_inst", {}).get("manifest_rows")),
           _fmt(report["tables"].get("top_inst", {}).get("manifest_row_diff"))),
    ]
    tc = report.get("trade_cal") or {}
    if "error" in tc:
        lines.append("  交易日: 交易日历不可读（%s）" % tc["error"])
    else:
        lines.append("  交易日: D4 覆盖 %s 天（%s~%s）/ SSE 应开 %s 天；缺 %s%s"
                     % (_fmt(tc.get("covered_days")), tc.get("range", ["?", "?"])[0],
                        tc.get("range", ["?", "?"])[1], _fmt(tc.get("expected_open_days")),
                        ",".join(tc.get("missing_days") or []) or "无",
                        ("；多 %s" % ",".join(tc["extra_days"])) if tc.get("extra_days") else ""))
    for name in ("top_list", "top_inst"):
        dup = report["duplicates"].get(name) or {}
        plan = report["dedup_plan"]["tables"].get(name) or {}
        lines.append("  同键/去重: %s 重复 %s 键（额外 %s 行，conflict %s / identical %s；max(_fetched_at) 并列 %s 键）"
                     " → dry-run %s→%s（- %s）"
                     % (name, _fmt(dup.get("dup_keys")), _fmt(dup.get("dup_extra_rows")),
                        _fmt(dup.get("conflicting_keys")), _fmt(dup.get("identical_keys")),
                        _fmt(dup.get("conflict_newest_tied")),
                        _fmt(plan.get("rows_before")), _fmt(plan.get("rows_after")),
                        _fmt(plan.get("rows_dropped"))))
    cand = report.get("candidate") or {}
    lines.append("  候选 %s: top_list %s 行 / top_inst %s 行 → L3=%s"
                 % (cand.get("code"), _fmt(cand.get("top_list_rows")),
                    _fmt(cand.get("top_inst_rows")), cand.get("l3")))
    lines.append("  只读: %d 文件快照前后一致=%s" % (report["readonly"]["files"], report["readonly"]["unchanged"]))
    findings = report.get("findings") or {}
    if findings:
        lines.append("  非阻塞观察: %s"
                     % "；".join("%s=%s" % (k, v) for k, v in findings.items()))
    lines.append("  结论: %s（阻塞缺口 %d 项 / 非阻塞观察 %d 项）"
                 % (report.get("verdict"), report.get("gaps_count", 0), report.get("findings_count", 0)))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="acad_d4_freeze_check.py",
        description="ACAD1/DATA1 D4 冻结对账（只读）：交易日缺口/manifest 行数/冲突键/候选0行/去重 dry-run。",
        epilog="退出码：0=报告已产出（含缺口）；1=数据根/关键表不可读；2=用法错误。",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON（机读）")
    ap.add_argument("--data-root", default=None, help="数据根（默认 <主工作树>/quant_backtest/data）")
    ap.add_argument("--code", default=DEFAULT_CANDIDATE, help="候选票代码（默认 600519.SH）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    data_root = resolve_data_root(args.data_root)
    try:
        report = run_check(data_root, candidate=args.code)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc)
        return 1
    if not report["readonly"]["unchanged"]:
        print("WARN: 只读快照检测到文件元数据变化，请复核", file=sys.stderr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_human(report))
    hard = any("error" in t for t in report["tables"].values()) or "error" in report["trade_cal"]
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
