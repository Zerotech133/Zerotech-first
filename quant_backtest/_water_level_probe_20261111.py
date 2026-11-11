"""只读: 全表水位盘点。不写任何数据文件。

对 tushare_curated / tushare / tdx 三处的 parquet 表, 读出各自的"最新日期",
用于判断哪些源落后、哪些已齐。只打印 + 落一份 md 报告到 quant_backtest/_reports/。

基准: 最后一交易日 LAST_TD。日频表 max==LAST_TD 即 OK。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "quant_backtest" / "data"
OUT = ROOT / "quant_backtest" / "_reports" / "water_level_20261111.md"
LAST_TD = "20261110"

DATE_COLS = (
    "trade_date", "cal_date", "trade_day", "date", "end_date", "ann_date",
    "f_ann_date", "report_date", "upd_date", "created_at", "in_date",
    "list_date", "delist_date", "month", "period", "publish_date", "dt",
)


def norm(v) -> str:
    s = str(v).strip()
    s = s.replace("-", "").replace("/", "").replace(":", "").replace(" ", "")
    return s[:8]


def probe_file(p: Path) -> tuple[str, str, int]:
    """返回 (日期列名, 最大日期, 行数)。失败返回 ('', '', -1)。"""
    try:
        md = pq.ParquetFile(p).metadata
        rows = md.num_rows
        cols = [f.name for f in pq.ParquetFile(p).schema_arrow]
    except Exception:
        return ("", "", -1)
    hit = [c for c in DATE_COLS if c in cols]
    if not hit:
        return ("", "", rows)
    best = ("", "")
    for c in hit[:3]:
        try:
            arr = pq.read_table(p, columns=[c]).column(c).to_pylist()
        except Exception:
            continue
        vals = [norm(v) for v in arr if v is not None]
        vals = [v for v in vals if v[:4].isdigit() and len(v) >= 8]
        if vals:
            m = max(vals)
            if m > best[1]:
                best = (c, m)
    return (best[0], best[1], rows)


def status_of(col: str, latest: str) -> str:
    if not col:
        return "NODATE"
    if not latest:
        return "EMPTY"
    if col in ("list_date", "delist_date"):
        return "STATIC"  # 静态属性列, 不代表表的新鲜度
    if latest == LAST_TD:
        return "OK"
    if latest > LAST_TD:
        return "FUTURE"
    if col in ("ann_date", "f_ann_date", "end_date", "report_date", "publish_date"):
        return "SLOW"  # 财报类, 天然不逐日
    return "BEHIND"


def scan_dir(base: Path, tag: str, rows_out: list) -> None:
    if not base.exists():
        return
    for p in sorted(base.glob("*.parquet")):
        c, d, n = probe_file(p)
        rows_out.append((status_of(c, d), tag, p.stem, c, d, n))
        print(f"{status_of(c,d):<7} {tag:<16} {p.stem:<28} {d or '-':<10} {n:>9,}", flush=True)
    for sub in sorted([x for x in base.iterdir() if x.is_dir()]):
        parts = sorted(sub.glob("part_*.parquet"))
        ds = sorted(sub.glob("d_*.parquet"))
        # 逐码表(irm_qa_sh / bak_basic 等)同时存在历史 part_ 快照与 d_ 日增量,
        # 真实水位取二者较大者; 只看 part_ 会把停在旧快照日误判成落后。
        best = ("", "", -1)
        if ds:
            last = ds[-1].stem.replace("d_", "")
            _, _, n = probe_file(ds[-1])
            best = ("d_文件名", last, n)
        if parts:
            c, d, n = probe_file(parts[-1])
            if d and (not best[1] or d > best[1]):
                best = (c, d, n)
            if not parts:
                pass
        if best[1]:
            tot = f"{len(parts)} 分区/{len(ds)} 日片" if ds else f"{len(parts)} 分区"
            rows_out.append((status_of("x", best[1]), tag, sub.name, best[0] or "-", best[1], best[2]))
            print(f"{status_of('x',best[1]):<7} {tag:<16} {sub.name:<28} {best[1]:<10} {best[2]:>9,}  ({tot})", flush=True)


def main() -> int:
    rows: list = []
    print(f"基准最后一交易日 = {LAST_TD}\n")
    print(f"{'状态':<7} {'源':<16} {'表':<28} {'最新':<10} {'行数':>9}")
    print("-" * 78)
    scan_dir(DATA / "tushare_curated", "tushare_curated", rows)
    scan_dir(DATA / "tushare", "tushare", rows)
    scan_dir(DATA / "tdx", "tdx", rows)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    agg: dict[str, int] = {}
    for r in rows:
        agg[r[0]] = agg.get(r[0], 0) + 1
    lines = ["# 全表水位盘点 2026-11-11", "", f"基准最后一交易日 `{LAST_TD}`", "",
             "状态口径：OK=齐到基准日 / BEHIND=日频但落后 / SLOW=公告财报类天然不逐日 /"
             " STATIC=静态属性列(上市退市日)不代表新鲜度 / NODATE=无日期列 / FUTURE=含未来日期",
             "", "## 汇总", "", "| 状态 | 表数 |", "|---|---|"]
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 明细", "", "| 状态 | 源 | 表 | 日期列 | 最新日期 | 行数 |", "|---|---|---|---|---|---|"]
    for st, tag, name, col, d, n in rows:
        lines.append(f"| {st} | {tag} | {name} | {col or '-'} | {d or '-'} | {n:,} |")
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== 汇总 ===")
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {k:<10} {v}")
    print(f"\n报告: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
