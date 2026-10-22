# -*- coding: utf-8 -*-
"""生成 tushare_staging 清理清单（只读，不删任何东西）。

规则（按「批次是否真的落地」而不是「过期天数」）：
  KEEP      在跑管线的目录（代码引用中）
  CANDIDATE 批次已完成 **且** 能证明「删了不丢数」：summary 里
            production_write / global_activation 不为 False，且批次日期
            在原始层 daily 里能查到（缺失比例 ≤10%）
  UNKNOWN   没有 summary / 未完成 / 只写了暂存层 / 原始层缺日期 —— 一律保留

为什么加这三条反向断言（2026-10-22 代码审查 F8）
  1. `complete_count == entry_count` 只证明「抓齐了」，不证明「写出去了」。
     staging 里 49 份 annual 摘要实测 production_write 全为 false，18 份
     global_activation 也是 false —— 这批数据只落在暂存层，删掉就是唯一
     副本，旧规则却按「已完成」把它们全判成 CANDIDATE。
  2. `.bak` 规则写着「原名仍在」却从不校验原名；原名若已没了，.bak 就是
     唯一副本，删了就真没了。
  3. 最终判据是「数据已在原始层」：用 summary.dates 抽样核对
     <raw>/daily/d_<yyyymmdd>.parquet，缺失比例 >10% 的批次降 UNKNOWN。

输出：C:\\项目\\_archive\\2026-10-22\\staging_cleanup_plan.json + 人读表格
（--stage / --raw-daily / --out 供夹具演练，默认值就是真实路径）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

STAGE = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_staging")
# 原始层：<data>/tushare/daily/d_<yyyymmdd>.parquet（STAGE 的上级就是数据根）
RAW_DAILY = STAGE.parent / "tushare" / "daily"
OUT_JSON = Path(r"C:\项目\_archive\2026-10-22\staging_cleanup_plan.json")

# 明确在跑 / 明确要保留的（有代码在读，或管线活跃）
ALWAYS_KEEP = {"tail_fill", "held_tail_fill", "history", "reconciliation", "explicit_gaps"}

# 原始层交叉校验：缺失比例 >10% 就不许判「可删」；每个批次最多抽样这么多天。
RAW_MISSING_LIMIT = 0.10
RAW_SAMPLE_MAX = 40

# .bak / .bak_k116 / .bak-20261016 这类备份后缀；`.bak` 后面必须是结尾或分隔符，
# 免得把 `foo.backup` 误当备份文件剥成 `foo`。
BAK_RE = re.compile(r"\.bak(?:$|[._-])")
ANNUAL_RE = re.compile(r"^annual_.*_batch_\d+$")


def dir_size(p: Path) -> tuple[int, int, float]:
    total = 0
    n = 0
    newest = 0.0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                st = f.stat()
                total += st.st_size
                n += 1
                newest = max(newest, st.st_mtime)
        except OSError:
            continue
    return total, n, newest


def load_summary(path: Path) -> dict | None:
    """读一份批次摘要；读不到 / 不是 JSON 对象 → None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def summary_status(data: dict) -> str | None:
    """判定批次完成度（只看「抓没抓齐」，不看「有没有写出去」）。

    这个 staging 的 annual 摘要**没有 status 字段**，只有每张表的
    entry_count / complete_count / source_error_count。所以真正的判据是：
        sum(complete_count) == sum(entry_count) 且 sum(source_error_count) == 0
    只看 status 会把 49 个已完成的批次全判成"状态不明"（2026-10-22 实测）。
    """
    for key in ("status", "result", "state", "ok", "verdict"):
        if key in data:
            return f"{key}={data[key]}"
    tables = data.get("tables")
    if isinstance(tables, dict) and tables:
        try:
            entry = sum(int(t.get("entry_count") or 0) for t in tables.values())
            comp = sum(int(t.get("complete_count") or 0) for t in tables.values())
            err = sum(int(t.get("source_error_count") or 0) for t in tables.values())
        except (TypeError, ValueError, AttributeError):
            return None
        if entry == 0:
            return "empty"
        if comp == entry and err == 0:
            return f"complete({comp}/{entry},err=0)"
        return f"incomplete({comp}/{entry},err={err})"
    return "no-status-field"


def staged_only(data: dict) -> str | None:
    """production_write / global_activation 显式为 False → 只写了暂存层。

    这两个字段是「批次到底落地没有」的反向证据：抓齐（complete）不等于写出。
    返回形如 `staged_only(production_write=False)`；两个都是 False 时一并列出。
    字段缺失 ≠ False，不降级（无法证伪就不扣帽子）。
    """
    flags = [k for k in ("production_write", "global_activation") if data.get(k) is False]
    if not flags:
        return None
    return "staged_only(%s)" % ",".join("%s=False" % k for k in flags)


def summary_dates(data: dict) -> list[str]:
    """取摘要里的 yyyymmdd 日期列表（脏值一律丢掉）。"""
    raw = data.get("dates")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, str) and re.fullmatch(r"\d{8}", d)]


def raw_daily_missing(
    dates: list[str], raw_daily: Path, sample_max: int
) -> tuple[int, int] | None:
    """抽样核对批次日期是否已在原始层 daily 落地。

    返回 (缺失数, 样本数)；没有 dates 或原始层不可读时返回 None。
    抽样是确定性的（等步长取点），同一份摘要每次得到同一结果。
    """
    if not dates or not raw_daily.is_dir():
        return None
    if len(dates) <= sample_max:
        sample = list(dates)
    else:
        step = max(1, len(dates) // sample_max)
        sample = list(dates)[::step][:sample_max]
    missing = sum(
        1 for d in sample if not (raw_daily / ("d_%s.parquet" % d)).is_file()
    )
    return missing, len(sample)


def original_name(name: str) -> str | None:
    """`x.db.bak_k116` → `x.db`；没有 .bak* 后缀 → None。"""
    m = BAK_RE.search(name)
    if m is None or m.start() == 0:
        return None
    return name[: m.start()]


def _classify_annual(
    stage: Path,
    name: str,
    age_h: float,
    raw_daily: Path,
    sample_max: int,
) -> tuple[str, str]:
    """annual_<year>_batch_<n> 目录的 verdict/reason（判据最重的一条）。"""
    sib = stage / f"{name}_summary.json"
    data = load_summary(sib) if sib.is_file() else None
    if data is None:
        if age_h > 48:
            return "UNKNOWN", f"批次摘要状态不明（无 summary 或读不出），{age_h/24:.1f} 天未写"
        return "KEEP", "批次较新且没有可用摘要，保留"

    st = summary_status(data)
    if st is None or not st.startswith("complete"):
        if st is not None and st.startswith("incomplete"):
            return "UNKNOWN", f"批次未完成（{st}），保留待查"
        return "UNKNOWN", f"批次摘要状态不明（{st or '无状态字段'}），{age_h/24:.1f} 天未写"

    # 1) 抓齐 ≠ 写出：摘要自己承认只写暂存层，就不能当"已完成可删"。
    flags = staged_only(data)
    if flags:
        return "UNKNOWN", f"{flags}；摘要证明只写暂存层（{st}），暂存副本可能是唯一副本"

    # 2) 最终判据：数据已在原始层 = 暂存副本可删。
    dates = summary_dates(data)
    if not dates:
        return "UNKNOWN", f"摘要没有 dates 字段（{st}），无法交叉校验原始层，不能证明可删"
    miss = raw_daily_missing(dates, raw_daily, sample_max)
    if miss is None:
        return "UNKNOWN", f"原始层 daily 不可读（{raw_daily}），无法交叉校验，不能证明可删"
    missing_n, sample_n = miss
    ratio = missing_n / sample_n
    if ratio > RAW_MISSING_LIMIT:
        return (
            "UNKNOWN",
            "批次日期在原始层缺失 %d/%d（>%g%%），暂存副本不能删"
            % (missing_n, sample_n, RAW_MISSING_LIMIT * 100),
        )
    return (
        "CANDIDATE",
        "批次已完成（%s: %s），无 staged_only 标记，原始层缺失 %d/%d（≤%g%%）"
        % (sib.name, st, missing_n, sample_n, RAW_MISSING_LIMIT * 100),
    )


def build_plan(
    stage: Path, raw_daily: Path, sample_max: int
) -> list[dict]:
    now = time.time()
    plan: list[dict] = []

    # ---- 目录类 ----
    for d in sorted(p for p in stage.iterdir() if p.is_dir()):
        name = d.name
        size, files, newest = dir_size(d)
        age_h = (now - newest) / 3600 if newest else 9999
        verdict = "KEEP"
        reason = ""
        if name in ALWAYS_KEEP:
            reason = "在跑管线目录（代码引用中）"
        elif ANNUAL_RE.match(name):
            verdict, reason = _classify_annual(stage, name, age_h, raw_daily, sample_max)
        elif name == "history_3y":
            if age_h > 48:
                verdict, reason = "CANDIDATE", f"历史回填暂存批次，{age_h/24:.1f} 天未写"
            else:
                reason = "较新"
        else:
            verdict, reason = "UNKNOWN", f"未识别目录，{age_h/24:.1f} 天未写"
        plan.append({"kind": "dir", "name": name, "bytes": size, "files": files,
                     "age_hours": round(age_h, 1),
                     "newest": datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M") if newest else None,
                     "verdict": verdict, "reason": reason})

    # ---- 文件类 ----
    root_files = sorted(p for p in stage.iterdir() if p.is_file())
    payloads = [p for p in root_files if p.name.startswith("nine_dim_source_payload_full_")]
    keep_payload = payloads[-1].name if payloads else None
    for p in root_files:
        st = p.stat()
        age_h = (now - st.st_mtime) / 3600
        verdict, reason = "KEEP", ""
        if p.name.startswith("nine_dim_source_payload_full_"):
            if p.name == keep_payload:
                reason = "同一 payload 的最新版本，保留"
            else:
                verdict, reason = "CANDIDATE", "同一 payload 的重复版本（只保留最新一版）"
        elif p.name.endswith(".json") and "_summary.json" in p.name:
            reason = "批次摘要，用于判定批次状态，保留"
        elif BAK_RE.search(p.name):
            # 反向校验：旧规则写「原名仍在」却从不看原名，这里真看。
            orig = original_name(p.name)
            if orig and (stage / orig).exists():
                verdict, reason = "CANDIDATE", f"备份文件（原名 {orig} 仍在）"
            else:
                verdict, reason = (
                    "UNKNOWN",
                    "原名已缺失，这是唯一副本（%s 不在本目录）" % (orig or "原名未知"),
                )
        elif p.suffix in (".sqlite3", ".sqlite3-wal", ".sqlite3-shm"):
            reason = "运行库，保留"
        elif p.suffix == ".lock":
            reason = "锁文件，保留"
        elif p.suffix == ".json" and age_h > 72:
            verdict, reason = "UNKNOWN", f"历史报表 JSON，{age_h/24:.1f} 天未更新"
        else:
            verdict, reason = "KEEP", "未识别但体积极小或近期更新"
        plan.append({"kind": "file", "name": p.name, "bytes": st.st_size, "files": 1,
                     "age_hours": round(age_h, 1),
                     "newest": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "verdict": verdict, "reason": reason})
    return plan


def unknown_breakdown(plan: list[dict]) -> dict[str, int]:
    """UNKNOWN 按原因归类（给报告看「为什么不能删」）。"""
    buckets = {
        "staged_only": 0,
        "原始层缺日期": 0,
        ".bak 原名缺失": 0,
        "其它（无摘要/未完成/未识别）": 0,
    }
    for item in plan:
        if item["verdict"] != "UNKNOWN":
            continue
        reason = item["reason"]
        if reason.startswith("staged_only("):
            buckets["staged_only"] += 1
        elif "批次日期在原始层缺失" in reason:
            buckets["原始层缺日期"] += 1
        elif reason.startswith("原名已缺失"):
            buckets[".bak 原名缺失"] += 1
        else:
            buckets["其它（无摘要/未完成/未识别）"] += 1
    return buckets


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="plan_staging_cleanup.py",
        description=(
            "只读生成 tushare_staging 清理清单：按批次完成度 + "
            "production_write/global_activation + 原始层日期交叉校验判定可回收项"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--stage", default=str(STAGE), help="暂存根（默认 %(default)s）")
    ap.add_argument("--out", default=str(OUT_JSON), help="清单 JSON 输出路径（默认 %(default)s）")
    ap.add_argument(
        "--raw-daily", default=str(RAW_DAILY), help="原始层 daily 目录（默认 %(default)s）"
    )
    ap.add_argument(
        "--max-date-sample",
        type=int,
        default=RAW_SAMPLE_MAX,
        metavar="N",
        help="每个批次最多抽样多少个日期做原始层核对（默认 %(default)s）",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse: --help=0，参数错误=2
        return int(exc.code or 0)

    stage = Path(args.stage)
    out_json = Path(args.out)
    raw_daily = Path(args.raw_daily)
    sample_max = max(1, args.max_date_sample)

    if not stage.is_dir():
        print("ERROR: 暂存根不存在或不是目录: %s" % stage, file=sys.stderr)
        return 2
    if not raw_daily.is_dir():
        print(
            "WARN: 原始层 daily 不可读（%s）—— annual 批次一律不判 CANDIDATE（无法证明可删）"
            % raw_daily,
            file=sys.stderr,
        )

    plan = build_plan(stage, raw_daily, sample_max)

    # ---- 汇总 ----
    def total(v: str) -> int:
        return sum(x["bytes"] for x in plan if x["verdict"] == v)

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage_root": str(stage),
        "raw_daily": str(raw_daily),
        "raw_missing_limit": RAW_MISSING_LIMIT,
        "rule": (
            "按批次是否真的落地而非过期天数：完成且非 staged_only 且原始层有日期才可清；"
            "重复只留最新；.bak 校验原名；状态不明一律保留"
        ),
        "counts": {v: sum(1 for x in plan if x["verdict"] == v) for v in ("KEEP", "CANDIDATE", "UNKNOWN")},
        "bytes": {v: total(v) for v in ("KEEP", "CANDIDATE", "UNKNOWN")},
        "reclaimable_gib": round(total("CANDIDATE") / 1024**3, 2),
        "unknown_breakdown": unknown_breakdown(plan),
        "items": plan,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"扫描 {stage}")
    print(f"原始层 {raw_daily}（缺失阈值 >{RAW_MISSING_LIMIT:.0%}，每批最多抽样 {sample_max} 天）")
    print(f"条目 {len(plan)} 个")
    print()
    for v in ("KEEP", "CANDIDATE", "UNKNOWN"):
        print(f"  {v:10s} {summary['counts'][v]:4d} 项  {summary['bytes'][v]/1024**3:6.2f} GiB")
    print()
    print(f"可回收（CANDIDATE）: {summary['reclaimable_gib']} GiB")
    print()
    if summary["counts"]["UNKNOWN"]:
        print("=== UNKNOWN 归因 ===")
        for tag, n in summary["unknown_breakdown"].items():
            print(f"  {tag:28s} {n:4d}")
        print()
    print("=== CANDIDATE 明细（前 25）===")
    cands = sorted((x for x in plan if x["verdict"] == "CANDIDATE"),
                   key=lambda x: -x["bytes"])
    for x in cands[:25]:
        print("  %-46s %8.1f MB  %s" % (x["name"][:46], x["bytes"] / 1024**2, x["reason"]))
    if len(cands) > 25:
        print(f"  …… 另有 {len(cands)-25} 项，见 JSON")
    print()
    print(f"清单已写 {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
