# -*- coding: utf-8 -*-
r"""B3 后验引擎扩展：signals_combined（融合信号链）后验统计（M0 2026-10-19）。

把 pick_verdicts（picks 链）的后验口径复刻到融合信号链：
  数据源适配层：strategy.signals_combined（PG，psycopg2 优先 / docker exec
      psql 兜底，判例同 aggregator/dashboard_api）+ 离线 rows 注入 /
      --rows-file 回放（测试与演练用，绝不写生产）。
  收益口径：复用 picks 链可成交口径（信号日+2 开盘买入 → T+1/3/5 收盘，
      pct_between 直接复用 portal/build_pick_verdicts，复权/exdiv void
      同判例），stats 块同构 n/hit/avg/med。
  分层统计：
      · by_strategy：按贡献策略（contributions 键）分层，多源事件跨桶
        计入（二十五/三十六审同判例）；仅 action=buy 入桶（卖/持不是成绩样本）
      · by_weight_group：信号时点各源权重分带（lt025/b025_050/b050_075/
        ge075）×策略双维分层，供权重进化器（G7）后验输入
      · by_shadow：影子标注组（Δ5 P3：env_diff/panic_buy）——只标不拦：
        被标注事件照常进全部基础分层，影子层只是加法视图，不剔除样本、
        不改裁决
      · excess：超额对照——回收 picks 链 pick_verdicts 的 window_mid
        （全市场窗口中位）作 benchmark 表 {(exec_day, w): mid}，事件级
        win 带 base/ex，summary 出 excess 分窗 blk；数据不可得显式跳过
输出：portal/data/combined_verdicts.json（原子写 tmp+os.replace，
K-STOCK-393 判例）。只读工具，默认不写任何 PG/生产数据。
用法：
  python combined_verdicts.py [--date YYYYMMDD] [--rows-file f.json]
                              [--out o.json] [--limit N]
                              [--bench-file f.json]
                              [--view shadow] [--in o.json]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QB_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(QB_ROOT)
PORTAL_DIR = os.path.join(ROOT, "portal")
OUT_DEFAULT = os.path.join(PORTAL_DIR, "data", "combined_verdicts.json")
PICK_VERDICTS_DEFAULT = os.path.join(PORTAL_DIR, "data", "pick_verdicts.json")

WINDOWS = (1, 3, 5)
EXEC_LAG = 2  # 首次可操作日 = 信号日 + 2 交易日（九审口径）
WEIGHT_BANDS = ("lt025", "b025_050", "b050_075", "ge075")
SHADOW_TAGS = ("env_diff", "panic_buy")
PG_LIMIT_DEFAULT = 60  # 不指定 --date 时默认回看最近 N 个交易日


# ── 数据源适配层（读） ─────────────────────────────────────────────────
def normalize_rows(rows) -> list:
    """signals_combined 行规整：contributions/JSON 串双形态统一为 dict。

    剔除缺 trade_date/symbol 的残行；symbol 须含 '.'（后验口径当前只
    覆盖 A 股，与 build_pick_verdicts 同判例）。
    """
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        td = str(r.get("trade_date") or "").strip()
        sym = str(r.get("symbol") or "").strip().upper()
        if len(td) != 8 or not td.isdigit() or "." not in sym:
            continue
        contrib = r.get("contributions") or {}
        if isinstance(contrib, (str, bytes)):
            try:
                contrib = json.loads(
                    contrib if isinstance(contrib, str) else contrib.decode("utf-8")
                )
            except ValueError:
                contrib = {}
        if not isinstance(contrib, dict):
            contrib = {}
        out.append(
            {
                "trade_date": td,
                "symbol": sym,
                "action": str(r.get("action") or "hold"),
                "score": float(r.get("score") or 0.0),
                "confidence": float(r.get("confidence") or 0.0),
                "contributions": contrib,
                "divergence": bool(r.get("divergence")),
                "rationale": str(r.get("rationale") or ""),
            }
        )
    return out


def load_rows_file(path: str) -> list:
    """离线回放：--rows-file JSON（行 dict 列表，与落库行同构）。"""
    doc = json.load(open(path, encoding="utf-8"))
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ValueError("rows-file 须为行 dict 列表（或 {rows: [...]}）")
    return normalize_rows(rows)


def load_rows_pg(trade_date=None, limit=PG_LIMIT_DEFAULT) -> list:
    """读 strategy.signals_combined；psycopg2 优先 / docker exec psql 兜底。

    只读 SELECT，绝不写库。trade_date 为 None 时取最近 limit 个交易日。
    """
    from strategy_layer.aggregator import _pg_connect, DOCKER_PSQL_BASE

    where = (
        ("WHERE trade_date = '%s'" % str(trade_date).replace("'", ""))
        if trade_date
        else ""
    )
    tail = " ORDER BY trade_date DESC, score DESC LIMIT %d" % int(limit or 60)
    cols = (
        "trade_date, symbol, action, score, confidence, "
        "contributions::text, divergence::text, rationale"
    )
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                if trade_date:
                    cur.execute(
                        "SELECT %s FROM strategy.signals_combined "  # nosec B608 - 值参数化/内部标识符（已抽查）
                        "WHERE trade_date = %%s%s"
                        % (cols, tail.replace("LIMIT %d" % int(limit or 60), "")),
                        (str(trade_date),),
                    )
                else:
                    cur.execute(
                        "SELECT %s FROM strategy.signals_combined%s" % (cols, tail)  # nosec B608 - 值参数化/内部标识符（已抽查）
                    )
                raw = cur.fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        raw = _psql_rows(
            "SELECT %s FROM strategy.signals_combined %s%s" % (cols, where, tail),  # nosec B608 - 值参数化/内部标识符（已抽查）
            DOCKER_PSQL_BASE,
        )
    rows = []
    for r in raw:
        rows.append(
            {
                "trade_date": r[0],
                "symbol": r[1],
                "action": r[2],
                "score": r[3],
                "confidence": r[4],
                "contributions": r[5],
                "divergence": r[6],
                "rationale": r[7],
            }
        )
    return normalize_rows(rows)


def _psql_rows(sql: str, base) -> list:
    """docker exec psql COPY TO STDOUT 兜底读（dashboard_api 判例简化版）。"""
    proc = subprocess.run(
        list(base) + ["-c", "COPY (%s) TO STDOUT WITH (FORMAT csv, HEADER true)" % sql],
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "psql 兜底读失败: %s" % proc.stderr.decode("utf-8", "replace")[:200]
        )
    import csv as _csv
    import io as _io

    out = []
    for row in _csv.DictReader(_io.StringIO(proc.stdout.decode("utf-8", "replace"))):
        row["divergence"] = str(row.get("divergence") or "").strip() == "t"
        out.append(row)
    return out


# ── 工具（口径与 build_pick_verdicts 对齐） ───────────────────────────
def after_days(cal, sig_day: str, n: int):
    """信号日后第 n 个交易日（严格大于信号日；同 build_pick_verdicts）。"""
    try:
        i = cal.index(sig_day)
    except ValueError:
        later = [d for d in cal if d > sig_day]
        if not later:
            return None
        i = cal.index(later[0]) - 1
    j = i + n
    return cal[j] if 0 <= j < len(cal) else None


def weight_band(w) -> str:
    """权重分带（contributions 权重为 symbol 内归一化值，和≈1）。"""
    w = float(w)
    if w < 0.25:
        return "lt025"
    if w < 0.50:
        return "b025_050"
    if w < 0.75:
        return "b050_075"
    return "ge075"


def shadow_tags_of(row: dict, env=None) -> list:
    """Δ5 P3 影子标注（只标不拦）。

    panic_buy：恐慌期买入信号——env 日历该日 regime=="down"（env 缺失时
    退读 rationale 内 reg=down 标记，Δ5 现状「reg 只进 rationale」的兜底）。
    env_diff：环境闸门型差异日——env 该日为 {"diff": True}。
    env 结构：{trade_date: "down"/"up"/{"diff": True, "regime": ...}}。
    """
    tags = []
    td = str(row.get("trade_date") or "")
    ev = (env or {}).get(td)
    regime = None
    if isinstance(ev, dict):
        if ev.get("diff"):
            tags.append("env_diff")
        regime = ev.get("regime")
    elif isinstance(ev, str):
        regime = ev
    if regime is None:
        rationale = str(row.get("rationale") or "")
        if "reg=down" in rationale:
            regime = "down"
    if regime == "down" and row.get("action") == "buy":
        tags.append("panic_buy")
    return [t for t in SHADOW_TAGS if t in tags]


# ── 统计块（n/hit/avg/med，与 build_pick_verdicts 同构） ──────────────
def blk(vals):
    if not vals:
        return {"n": 0, "hit": None, "avg": None, "med": None}
    s = sorted(vals)
    return {
        "n": len(vals),
        "hit": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
        "avg": round(sum(vals) / len(vals), 2),
        "med": s[len(s) // 2],
    }


# ── 读侧视图（只读，供消费"只标不拦"的影子标注） ──────────────────────
def read_shadow_view(path: str = OUT_DEFAULT) -> dict:
    """shadow 视图读取口：从 combined_verdicts JSON 读影子标注（纯只读）。

    返回 {"asof":…, "by_shadow": 分窗统计, "shadow_codes": {sym: {date,
    action, shadow_tags}}}——dashboard/CLI 消费"只标不拦"标注的最小面。
    """
    doc = json.load(open(path, encoding="utf-8"))
    summary = doc.get("summary") or {}
    codes = {
        sym: {
            "date": rec.get("date"),
            "action": rec.get("action"),
            "shadow_tags": rec.get("shadow_tags") or [],
        }
        for sym, rec in (doc.get("per_code") or {}).items()
        if rec.get("shadow_tags")
    }
    return {
        "asof": summary.get("asof", ""),
        "by_shadow": summary.get("by_shadow") or {},
        "shadow_codes": codes,
    }


# ── benchmark 对照表（回收 picks 链 window_mid，不重算不造数） ─────────
def load_benchmark_mid(path: str = PICK_VERDICTS_DEFAULT) -> dict:
    """从 picks 链 pick_verdicts.json 回收全市场窗口中位对照表。

    picks 链 per_code[*].win[w].base 即 window_mid（K-STOCK-077 判例，
    同 (exec_d, d1) 横截面恒定）→ 回收为 {(exec_day, w): mid}，供
    signals_combined 侧超额对照。文件缺失/无数据返回 {}（显式跳过，不造数）。
    """
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    table = {}
    for rec in (doc.get("per_code") or {}).values():
        for w, v in (rec.get("win") or {}).items():
            if isinstance(v, dict) and v.get("base") is not None and v.get("exec"):
                try:
                    table[(str(v["exec"]), int(w))] = float(v["base"])
                except (TypeError, ValueError):
                    continue
    return table


# ── 后验主流程 ─────────────────────────────────────────────────────────
def build(rows, cal, return_fn, windows=WINDOWS, env=None, benchmark=None) -> dict:
    """signals_combined 后验计算（事件级四十审语义：同票跨档=独立事件）。

    Parameters
    ----------
    rows : list[dict]  signals_combined 行（normalize_rows 已规整或原始均可）
    cal : list[str]    交易日历（升序 YYYYMMDD）
    return_fn : f(symbol, exec_day, d1) -> float | "exdiv" | None
    env : dict | None  影子标注环境日历（shadow_tags_of 口径）
    benchmark : dict | None  {(exec_day, w): mid} 全市场窗口中位对照表
                （load_benchmark_mid 回收 picks 链 window_mid）；None=跳过
    """
    rows = normalize_rows(rows)
    per_code = {}
    win_buckets = {w: [] for w in windows}
    ex_buckets = {w: [] for w in windows}  # 超额 r-base（benchmark 可得时）
    strat_buckets = {}  # sid -> {w: [r]}
    band_buckets = {}  # sid -> {band: [r]}
    shadow_buckets = {}  # tag -> {w: [r]}
    n_events = 0
    for row in sorted(rows, key=lambda r: (r["trade_date"], r["symbol"])):
        sig, sym = row["trade_date"], row["symbol"]
        n_events += 1
        tags = shadow_tags_of(row, env)
        contribs = row["contributions"]
        rec = per_code.setdefault(
            sym,
            {
                "action": row["action"],
                "score": row["score"],
                "date": sig,
                "strategies": sorted(
                    sid for sid, c in contribs.items() if isinstance(c, dict)
                ),
                "shadow_tags": tags,
                "win": {},
            },
        )
        if sig < rec["date"]:
            continue  # per_code 只留每票最近一次信号；事件桶不受影响
        if sig > rec["date"]:
            rec.update(
                {
                    "action": row["action"],
                    "score": row["score"],
                    "date": sig,
                    "win": {},
                    "strategies": sorted(
                        sid for sid, c in contribs.items() if isinstance(c, dict)
                    ),
                }
            )
        rec["shadow_tags"] = tags
        exec_d = after_days(cal, sig, EXEC_LAG)
        wins = {}
        for w in windows:
            d1 = after_days(cal, exec_d, w) if exec_d else None
            if d1 is None:
                wins[w] = None  # 窗口未满 → pending（不硬凑）
                continue
            r1 = return_fn(sym, exec_d, d1)
            if r1 == "exdiv":
                wins[w] = {"r": None, "exec": exec_d, "void": "exdiv"}
                continue
            r1 = None if r1 is None else round(float(r1), 2)
            entry = {"r": r1, "exec": exec_d}
            if benchmark:
                mid = benchmark.get((exec_d, w))
                entry["base"] = mid
                entry["ex"] = (
                    round(r1 - mid, 2) if r1 is not None and mid is not None else None
                )
            wins[w] = entry
            if r1 is not None and row["action"] == "buy":
                win_buckets[w].append(r1)
                if benchmark:
                    mid = benchmark.get((exec_d, w))
                    if mid is not None:
                        ex_buckets[w].append(round(r1 - mid, 2))
                for sid, c in contribs.items():
                    if not isinstance(c, dict):
                        continue
                    strat_buckets.setdefault(sid, {}).setdefault(w, []).append(r1)
                    if c.get("weight") is not None:
                        band_buckets.setdefault(sid, {}).setdefault(
                            weight_band(c["weight"]), {}
                        ).setdefault(w, []).append(r1)
                for t in tags:
                    # 只标不拦：被标注事件照常已在 windows/by_strategy 桶，
                    # 影子桶是加法视图
                    shadow_buckets.setdefault(t, {}).setdefault(w, []).append(r1)
        rec["win"] = wins

    summary = {
        "asof": max((r["trade_date"] for r in rows), default=""),
        "events": n_events,
        "caliber": {
            "id": "exec-d2-open-20261010",
            "buy": "信号日后第2个交易日开盘（与 picks 链 build_pick_verdicts 同口径）",
            "sell": "首买日后 1/3/5 个交易日收盘",
            "return": "return_fn 注入（默认复用 build_pick_verdicts.pct_between，复权/exdiv void 同判例）",
            "layering": "by_strategy=contributions 键（多源跨桶，仅 action=buy 入桶）；"
            "by_weight_group=信号时点归一化权重分带×策略；"
            "by_shadow=影子标注加法视图，只标不拦，n 勿跨桶加总",
        },
        "excess_caliber": (
            "超额=个股窗口收益-同窗口全市场开盘-收盘中位（复用 picks 链 "
            "pick_verdicts window_mid，不重算不造数；基准不可得则空块）"
            if benchmark
            else "benchmark 对照不可得（picks 链 window_mid 缺失），跳过"
        ),
        "windows": {str(w): blk(win_buckets[w]) for w in windows},
        "excess": {str(w): blk(ex_buckets[w]) for w in windows},
        "by_strategy": {
            s: {str(w): blk(b.get(w) or []) for w in windows}
            for s, b in sorted(strat_buckets.items())
        },
        "by_weight_group": {
            s: {
                b: {str(w): blk(v.get(w) or []) for w in windows}
                for b, v in sorted(bs.items())
            }
            for s, bs in sorted(band_buckets.items())
        },
        "by_shadow": {
            t: {str(w): blk(b.get(w) or []) for w in windows}
            for t, b in sorted(shadow_buckets.items())
        },
    }
    return {"summary": summary, "per_code": per_code}


def write_out(out: dict, path: str = OUT_DEFAULT) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **out}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, path)  # K-STOCK-393 原子写判例
    return path


def default_return_fn():
    """默认收益函数：复用 picks 链 pct_between（湖读取+复权口径）。"""
    if PORTAL_DIR not in sys.path:
        sys.path.insert(0, PORTAL_DIR)
    from build_pick_verdicts import pct_between  # noqa: E402

    return pct_between


def load_calendar() -> list:
    """全市场交易日历（ts_daily trade_date 并集，升序）。"""
    if QB_ROOT not in sys.path:
        sys.path.insert(0, QB_ROOT)
    from tdx_read import read_range  # noqa: E402

    df = read_range("ts_daily", "20200101", "20991231", columns=["trade_date"])
    return sorted(
        {str(d)[:8] for d in (df["trade_date"].tolist() if df is not None else [])}
    )


def main() -> int:
    date = limit = rows_file = out = bench_file = view = in_path = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--date" and i + 1 < len(argv):
            date = argv[i + 1]
        elif a == "--rows-file" and i + 1 < len(argv):
            rows_file = argv[i + 1]
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
        elif a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif a == "--bench-file" and i + 1 < len(argv):
            bench_file = argv[i + 1]
        elif a == "--view" and i + 1 < len(argv):
            view = argv[i + 1]
        elif a == "--in" and i + 1 < len(argv):
            in_path = argv[i + 1]
    # 读侧视图：只读产出 JSON，不重算、不写任何东西
    if view:
        if view != "shadow":
            print("[combined_verdicts] 未知视图 %r（当前支持: shadow）" % view)
            return 2
        v = read_shadow_view(in_path or out or OUT_DEFAULT)
        print("[combined_verdicts] shadow 视图 asof=%s" % v["asof"])
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0
    if rows_file:
        rows = load_rows_file(rows_file)
    else:
        rows = load_rows_pg(date, limit)
    if not rows:
        print("[combined_verdicts] signals_combined 无行可算（检查融合链产出）")
        return 1
    cal = load_calendar()
    if not cal:
        print("[combined_verdicts] 行情湖不可达（交易日历为空），跳过")
        return 1
    bench = load_benchmark_mid(bench_file) if bench_file else load_benchmark_mid()
    if bench:
        print(
            "[combined_verdicts] benchmark 对照 %d 组（回收 picks 链 "
            "window_mid）" % len(bench)
        )
    else:
        print(
            "[combined_verdicts] benchmark 对照不可得"
            "（picks 链 window_mid 数据缺失），超额对照显式跳过"
        )
    res = build(rows, cal, default_return_fn(), benchmark=bench)
    path = write_out(res, out)
    s = res["summary"]
    print("[combined_verdicts] 事件 %d | asof %s → %s" % (s["events"], s["asof"], path))
    for w in WINDOWS:
        b = s["windows"][str(w)]
        print("  execT+%d: n=%s hit=%s%% avg=%s%%" % (w, b["n"], b["hit"], b["avg"]))
    for sid, bs in s["by_strategy"].items():
        print(
            "  [%s] " % sid
            + "; ".join("T+%d n=%s" % (w, bs[str(w)]["n"]) for w in WINDOWS)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
