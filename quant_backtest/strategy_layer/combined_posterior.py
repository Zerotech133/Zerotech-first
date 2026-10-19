# -*- coding: utf-8 -*-
"""B3 后验引擎扩展：signals_combined 分层后验统计（M0 2026-10-19）。

把 picks 链后验口径扩展到融合信号链（signals_combined 历史行），按
策略 / 权重组 / 分歧票分层统计信号后收益。只读统计工具：本模块自身
绝不连 PG（PG 读取仅发生在调用方显式注入 loader 时），不写任何生产
数据，产出档原子写（tmp + os.replace，K-STOCK-393 判例）。

  数据输入：默认经 combined_verdicts.load_rows_file 注入路径回放；
      可传 rows 直接注入；trade_dates 过滤交易日子集。
  行情输入：quote_fetcher 由调用方注入（测试用假数据），返回
      {"close": 信号日收盘, "t1": T+1 收盘, "t5": T+5 收盘}；
      无行情的票标记 missing，不计入统计分母但显式计数。
  收益口径：T+N 收盘对信号日收盘（ret = close(T+N)/close(sig) - 1，
      百分数保留 2 位）；win_rate 按 T+1 收益 > 0 计。
  分层统计（strategy_registry.json weights 手工 0.5/0.5 口径）：
      · per_strategy：按 contributions 键（贡献策略）分层，多源事件
        跨桶计入
      · per_weight_group：按行内最小贡献权重分带——weight>=0.5=full，
        0<w<0.5=shadow，w=0（含无贡献权重）=off
      · divergence_group：divergence=true 的票单独一组，不混入上述
        分层分母
  输出兼容：产出 JSON 顶层含 status/asof/total_rows/per_strategy/
      per_weight_group/divergence_group/missing_total，并镜像一份
      summary{asof,windows,by_strategy,by_weight_group,by_shadow}
      供 xunyou.read_posterior 直接读（status=OK 不降级）。

用法：
  python combined_posterior.py --rows-file f.json [--out o.json] [--limit N]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

try:
    from strategy_layer.combined_verdicts import load_rows_file, normalize_rows
except ImportError:  # 直接脚本运行时的路径兜底
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategy_layer.combined_verdicts import load_rows_file, normalize_rows

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "strategy_registry.json")

WEIGHT_FULL_MIN = 0.5          # weight>=0.5 → full
WEIGHT_GROUPS = ("full", "shadow", "off")
WIN_RATE_WINDOW = "t1"         # win_rate 口径窗：T+1 收益 > 0


# ── 权重分层口径 ───────────────────────────────────────────────────────
def load_registry_weights(path: str | None = None) -> dict:
    """读 strategy_registry.json weights（手工 0.5/0.5）；不可得返回 {}。"""
    try:
        doc = json.load(open(path or REGISTRY_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    w = doc.get("weights")
    return w if isinstance(w, dict) else {}


def _contrib_weight(c, registry_weights, sid):
    """贡献条目权重：优先条目自带 weight，回退 registry 口径。"""
    if isinstance(c, dict) and c.get("weight") is not None:
        try:
            w = float(c["weight"])
        except (TypeError, ValueError):
            w = None
        if w is not None:
            return w
    try:
        return float(registry_weights[sid])
    except (KeyError, TypeError, ValueError):
        return None


def row_weight(row: dict, registry_weights: dict):
    """行权重 = 各贡献策略权重的最小值（缺任一源权重则记 None → off）。

    取最小值：任一贡献源权重未到位（<0.5）时信号只算部分支撑，
    不应计入 full 组。
    """
    contribs = row.get("contributions") or {}
    ws = [_contrib_weight(c, registry_weights, sid)
          for sid, c in contribs.items() if isinstance(c, dict)]
    if not ws or any(w is None for w in ws):
        return None
    return min(ws)


def weight_group_of(w) -> str:
    """weight>=0.5=full，0<w<0.5=shadow，0/None=off。"""
    if w is None:
        return "off"
    w = float(w)
    if w >= WEIGHT_FULL_MIN:
        return "full"
    if w > 0:
        return "shadow"
    return "off"


# ── 统计块 ─────────────────────────────────────────────────────────────
def _new_state() -> dict:
    return {"n": 0, "miss": 0, "t1": [], "t5": []}


def _ret(base: float, close) -> float | None:
    """T+N 收盘对信号日收盘（百分数 2 位）；收盘缺失/非法返回 None。"""
    try:
        c = float(close)
    except (TypeError, ValueError):
        return None
    if base <= 0 or c <= 0:
        return None
    return round(100.0 * (c / base - 1.0), 2)


def _block(st: dict) -> dict:
    t1, t5 = st["t1"], st["t5"]
    return {
        "count": st["n"],
        "win_rate": (round(100.0 * sum(1 for v in t1 if v > 0) / len(t1), 1)
                     if t1 else None),
        "avg_ret_t1": round(sum(t1) / len(t1), 2) if t1 else None,
        "avg_ret_t5": round(sum(t5) / len(t5), 2) if t5 else None,
        "missing": st["miss"],
    }


# ── 原子写（K-STOCK-393 判例：tmp + os.replace，失败不留半文件） ────────
def _write_json_atomic(doc: dict, path: str) -> str:
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
    return path


# ── 后验主流程 ─────────────────────────────────────────────────────────
def build_combined_posterior(trade_dates=None, rows=None, quote_fetcher=None,
                             out_path=None, rows_file=None, loader=None,
                             registry_path=None) -> dict:
    """signals_combined 分层后验统计（只读计算 + 可选原子落盘）。

    Parameters
    ----------
    trade_dates : list[str] | None  只统计这些交易日（YYYYMMDD 子集）
    rows : list[dict] | None        直接注入历史行（原始或已规整均可）
    quote_fetcher : f(symbol, trade_date) -> {"close","t1","t5"} | None
                    行情注入口；None=无行情（全部记 missing）
    out_path : str | None           给出则原子写产出档
    rows_file : str | None          经 combined_verdicts.load_rows_file 回放
    loader : f(trade_dates) -> rows | None  调用方显式注入的读取器
                    （如 PG 读取；本模块自身绝不连 PG）
    registry_path : str | None      权重口径文件（默认 strategy_registry.json）
    """
    if rows is None:
        if rows_file:
            rows = load_rows_file(rows_file)
        elif loader is not None:
            rows = loader(trade_dates)
        else:
            rows = []
    rows = normalize_rows(rows)
    if trade_dates:
        want = {str(d) for d in trade_dates}
        rows = [r for r in rows if r["trade_date"] in want]

    registry_weights = load_registry_weights(registry_path)
    overall = _new_state()
    strat_states = {}
    group_states = {g: _new_state() for g in WEIGHT_GROUPS}
    div_state = _new_state()
    missing_total = 0

    for row in rows:
        td, sym = row["trade_date"], row["symbol"]
        contribs = row["contributions"]
        divergence = bool(row.get("divergence"))

        # 行归属组：overall 必入；分歧票只进 divergence 组（单独一组，
        # 不混入策略/权重组分母），普通票进各贡献策略 + 权重组
        sids = sorted(sid for sid, c in contribs.items()
                      if isinstance(c, dict))
        gw = weight_group_of(row_weight(row, registry_weights))
        groups = [overall]
        if divergence:
            groups.append(div_state)
        else:
            groups += [strat_states.setdefault(s, _new_state())
                       for s in sids]
            groups.append(group_states[gw])

        # 行情后验（单票异常降级：该票记 missing，整体继续）
        quote = None
        if quote_fetcher is not None:
            try:
                quote = quote_fetcher(sym, td)
            except Exception:  # noqa: BLE001 — 单票行情异常不阻断整体
                quote = None
        base = None
        if isinstance(quote, dict):
            try:
                base = float(quote["close"]) if quote.get("close") is not None else None
            except (TypeError, ValueError):
                base = None
        if base is None or base <= 0:
            for g in groups:
                g["miss"] += 1
            missing_total += 1
            continue

        r1, r5 = _ret(base, quote.get("t1")), _ret(base, quote.get("t5"))
        for g in groups:
            g["n"] += 1
            if r1 is not None:
                g["t1"].append(r1)
            if r5 is not None:
                g["t5"].append(r5)

    doc = {
        "status": "OK",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": max((r["trade_date"] for r in rows), default=""),
        "total_rows": len(rows),
        "missing_total": missing_total,
        "per_strategy": {s: _block(b) for s, b in sorted(strat_states.items())},
        "per_weight_group": {g: _block(group_states[g]) for g in WEIGHT_GROUPS},
        "divergence_group": _block(div_state),
        "summary": {
            "asof": max((r["trade_date"] for r in rows), default=""),
            "caliber": {
                "return": "T+N 收盘对信号日收盘（百分数），quote_fetcher 注入",
                "win_rate": "T+1 收益 > 0 占比（%），分母=T+1 收益可算的票",
                "missing": "行情不可得票数（不计入统计分母，显式计数）",
                "layering": "per_strategy=contributions 键（多源跨桶）；"
                            "per_weight_group=行内最小贡献权重分带"
                            "(>=0.5 full / 0-0.5 shadow / 0 off)；"
                            "divergence=true 单独一组，不混入其他分层分母",
            },
            "windows": {"t1": _block(overall), "t5": _block(overall)},
            "by_strategy": {s: _block(b)
                            for s, b in sorted(strat_states.items())},
            "by_weight_group": {g: _block(group_states[g])
                                for g in WEIGHT_GROUPS},
            "by_shadow": {"divergence": _block(div_state)},
        },
    }
    if out_path:
        doc["out_path"] = _write_json_atomic(doc, out_path)
    return doc


# ── CLI（--rows-file / --out / --limit；只读回放，绝不连 PG） ───────────
def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    rows_file = out = limit = None
    for i, a in enumerate(argv):
        if a == "--rows-file" and i + 1 < len(argv):
            rows_file = argv[i + 1]
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
        elif a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])

    rows = load_rows_file(rows_file) if rows_file else []
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    doc = build_combined_posterior(rows=rows, out_path=out)

    if quote_fetcher_not_provided(doc):
        print("[combined_posterior] 未注入 quote_fetcher，全部票记 missing"
              "（行情由调用方注入，本模块不自行取数）")
    s = doc["summary"]
    print("[combined_posterior] rows=%d missing=%d asof=%s → %s"
          % (doc["total_rows"], doc["missing_total"], doc["asof"],
             doc.get("out_path") or "(未落盘)"))
    for name, blk in [("overall", s["windows"]["t1"]),
                      ("divergence", s["by_shadow"]["divergence"])]:
        print("  [%s] count=%s win_rate=%s%% avg_ret_t1=%s avg_ret_t5=%s"
              % (name, blk["count"], blk["win_rate"],
                 blk["avg_ret_t1"], blk["avg_ret_t5"]))
    for g, blk in s["by_weight_group"].items():
        print("  [w:%s] count=%s missing=%s" % (g, blk["count"], blk["missing"]))
    return 0


def quote_fetcher_not_provided(doc: dict) -> bool:
    """CLI 提示用：本次统计是否因未注入行情而全部记 missing。"""
    return doc["total_rows"] > 0 and doc["missing_total"] == doc["total_rows"]


if __name__ == "__main__":
    raise SystemExit(main())
