# -*- coding: utf-8 -*-
"""JEV 组合 pattern 案例库 + 演化路径验证器（P5.2 / P5.3 / P5.4）。

定位（真相源 v2 §阶段 P）：JEV 是 **OSINT 多维信号 + pattern 识别 + 演化推演引擎**。
本模块把 P5.1 的 `Hit` 接到「后续演化」上，形成可查询案例库，并输出 v2 §六
规定的三个判定指标：pattern 覆盖率 / 演化路径概率分化度 / 分化节点密度。

## 本轮（小规模可行性验证）的边界 —— 必须先看这一节

用户裁定「不等两项数据补齐，先做 ② 单日小规模可行性验证」。因此本轮：

- 只喂 **单个交易日 + T+1 单期**（后验日线只到次日，1/3/5/10 日无从计算）；
- 样本池是**涨停 / 触板票**，而社交采集器本就按涨停选票 → 天然偏样本；
- `Thresholds` 全部未校准、时间维度大面积 `unpriced`。

所以 `validate()` 的输出**恒带 `feasibility_only=True` / `gate_eligible=False`**，
严禁拿去对比判定闸 A/B 的 60% / 20pp / 5pp 门槛。代码按全量形态写，
是为了数据补齐后直接换 `--day` 列表重跑，而不是现在就下结论。

## 三条硬纪律（沿用 P5.1，并新增两条）

1. 装配层**只读本地**：日线 parquet / 既有快照，不联网、不采集。默认**不写盘**，
   只有显式 `--out` 才落 JSON（研究成果落盘 ≠ 装配层写盘）。
2. 后验交易日**从目录扫描**，不写死日历；缺哪期就记 `missing`，绝不用
   最近一日顶替、更不伪造收益。
3. 路径分类取 T+1 的 `pct_chg`（其 `pre_close` 即 T 日收盘），并用
   `close(T+1)/close(T)-1` 交叉校验；差异过大只标 `suspect`，不篡改路径。
4. 样本不足（`min_branch_n` / `min_pattern_n`）一律返回 `insufficient`，
   **绝不拿 n=2 的 100pp 当分化度**。
5. 缺失语义：`path=missing` 的样本不进分布、不算覆盖率分母之外，但留在案例里。

## 契约

    build_casebook(day, offsets=(1,), ...) -> list[Case]
    path_distribution(cases, pattern=None) -> dict
    validate(cases) -> dict        # 新验证器，恒标 feasibility_only
    CLI: python portal/jev_casebook.py --day YYYYMMDD [--offsets 1] [--limit N] [--out P] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jev_pattern as P  # noqa: E402
import jev_graph as G  # noqa: E402

# ── 路径口径（P5.3 起点的可校准阈值，集中在此） ─────────────────
PATH_FALL, PATH_RISE, PATH_FLAT, PATH_MISSING = "fall", "rise", "flat", "missing"
PATH_ORDER = (PATH_FALL, PATH_RISE, PATH_FLAT)
PATH_ZH = {PATH_FALL: "回落", PATH_RISE: "继续涨", PATH_FLAT: "横盘",
           PATH_MISSING: "无后验"}
POOL_LIMIT_UP, POOL_TOUCH, POOL_OTHER = "limit_up", "touch_only", "other"

# 「数据可用性」代理信号：看似能分叉，实则分层的是**有没有数据**。
# 典型：time_ready 等价于「该票有本地分钟 parquet」，而本地分钟只覆盖
# 中证800 一线 → 分出来的两组是大盘股 vs 小盘题材股，不是信号取值差异。
# 这类节点保留 delta_pp 供诊断，但绝不计入 v2 §六 条件③ 的分化节点数。
AVAILABILITY_PROXY_SIGNALS = frozenset({"time_ready"})


@dataclass(frozen=True)
class CaseThresholds:
    """案例库阈值；与 P.Thresholds 分开，前者管路径分类，后者管 pattern 判定。"""

    path_band: float = 3.0          # T+1 涨跌超过 ±3% 才不算横盘
    min_branch_n: int = 5           # 分化节点两侧各至少 5 例
    min_pattern_n: int = 5          # pattern 至少 5 例才算分化度
    fork_min_pp: float = 10.0       # 分层主路径概率差 ≥10pp 才记节点
    crosscheck_band: float = 0.5    # pct_chg 与 close 比值差异告警线（pp）


DEFAULT_CTH = CaseThresholds()


@dataclass
class Case:
    """一条案例 = 一只票某日的 pattern 命中 + 后续演化路径。"""

    code: str
    day: str
    pool_kind: str = POOL_OTHER
    base_pct_chg: float | None = None
    hits: list[dict] = field(default_factory=list)
    # 三维度留痕（分化节点分层要用，全部允许 None）
    delta_kind: str = "unpriced"
    delta_min: float | None = None
    main_net: float | None = None
    net_mf: float | None = None
    flow_has: bool = False
    social_covered: bool = False
    horizons: dict = field(default_factory=dict)   # {"T+1": {...}}
    path: str | None = None                        # 主路径（首个可用 horizon）

    def hit_ids(self) -> list[str]:
        return [h["pattern"] for h in self.hits]

    def as_dict(self) -> dict:
        return asdict(self)


# ── 后验交易日 / 日线（只读） ──────────────────────────────────
def next_trading_days(day: str, n: int, daily_dir: str | None = None) -> list[str]:
    """从 daily 分片目录扫描 T 日之后的 n 个交易日。

    不用交易日历、不写死节假日：目录里有分片即视为该日有行情。
    缺期由调用方记 missing，绝不回填最近一日顶替。
    """
    d = daily_dir or P.DAILY_DIR
    if not os.path.isdir(d):
        return []
    days = sorted(
        fn[2:-8] for fn in os.listdir(d)
        if fn.startswith("d_") and fn.endswith(".parquet")
        and len(fn) == len("d_") + 8 + len(".parquet")
        and fn[2:-8].isdigit() and fn[2:-8] > day
    )
    return days[:n]


def load_daily_rows(day: str, daily_dir: str | None = None) -> dict:
    """日线全列 → {c6: row}。P5.1 的 `_load_daily` 只有 pre_close/pct_chg，
    涨停判定需要 close/high，故案例库自行读全列（仍是纯本地只读）。
    """
    import pyarrow.parquet as pq

    d = daily_dir or P.DAILY_DIR
    path = os.path.join(d, "d_%s.parquet" % day)
    if not os.path.exists(path):
        return {}
    try:
        t = pq.read_table(path, columns=[
            "ts_code", "open", "high", "low", "close",
            "pre_close", "pct_chg", "vol", "amount",
        ])
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for row in t.to_pylist():
        out[str(row["ts_code"])[:6]] = row
    return out


def classify_pool(day: str, rows: dict, names: dict | None = None) -> dict:
    """T 日事件池：收盘封板 / 触板未封。复用 `jev_graph.limit_state`（判例 #25）。"""
    nm = P._load_names() if names is None else names
    pool: dict = {}
    for c6, r in rows.items():
        try:
            lu, touch, _ratio, _lp = G.limit_state(
                r.get("close"), r.get("high"), r.get("pre_close"),
                c6, nm.get(c6, ""), r.get("pct_chg"))
        except (TypeError, ValueError):
            continue
        if lu:
            pool[c6] = POOL_LIMIT_UP
        elif touch:
            pool[c6] = POOL_TOUCH
    return pool


def classify_path(pct_chg, cth: CaseThresholds = DEFAULT_CTH) -> str:
    """T+1 收益 → 回落 / 继续涨 / 横盘；无数据记 missing（不参与分布）。"""
    if pct_chg is None:
        return PATH_MISSING
    try:
        v = float(pct_chg)
    except (TypeError, ValueError):
        return PATH_MISSING
    if v != v:  # NaN
        return PATH_MISSING
    if v < -cth.path_band:
        return PATH_FALL
    if v > cth.path_band:
        return PATH_RISE
    return PATH_FLAT


# ── 案例库构建 ────────────────────────────────────────────────
def build_casebook(day: str, offsets=(1,), limit: int | None = None,
                   ctx: "P.DayContext | None" = None,
                   th: "P.Thresholds | None" = None,
                   cth: CaseThresholds = DEFAULT_CTH,
                   daily_dir: str | None = None) -> list[Case]:
    """跑 matcher → 关联后验演化 → 案例列表。

    `offsets` 支持多期（数据补齐后直接跑 1/3/5/10 日）；当前目录只够 T+1，
    更远的期会记 `missing`，不伪造。
    """
    offsets = tuple(sorted(set(int(x) for x in offsets)))
    max_off = max(offsets) if offsets else 0
    follow_days = next_trading_days(day, max_off, daily_dir=daily_dir)
    follow_rows: dict = {}

    base_rows = load_daily_rows(day, daily_dir=daily_dir)
    if not base_rows:
        return []
    pool = classify_pool(day, base_rows)
    # 封板在前、触板在后；其余票不进事件池（P5.2 定义即涨停样本）
    codes = [c for c in pool if pool[c] == POOL_LIMIT_UP] + \
            [c for c in pool if pool[c] == POOL_TOUCH]
    codes.sort(key=lambda c: (pool[c] != POOL_LIMIT_UP, c))
    if limit:
        codes = codes[:limit]

    ctx = ctx or P.DayContext(day)
    th = th or ctx.th
    cache_daily: dict = {}

    def _rows_for(d: str) -> dict:
        if d not in cache_daily:
            cache_daily[d] = load_daily_rows(d, daily_dir=daily_dir)
        return cache_daily[d]

    cases: list[Case] = []
    for c6 in codes:
        sig = ctx.signal(c6)
        hits = [h.as_dict() for h in P.match_signal(sig, th=th)]
        case = Case(
            code=c6, day=day, pool_kind=pool[c6],
            base_pct_chg=_f(base_rows[c6].get("pct_chg")),
            hits=hits,
            delta_kind=sig.delta_kind, delta_min=sig.delta_min,
            main_net=sig.main_net, net_mf=sig.net_mf,
            flow_has=sig.flow_has, social_covered=sig.social_covered(),
        )
        base_close = _f(base_rows[c6].get("close"))
        for off in offsets:
            key = "T+%d" % off
            fd = follow_days[off - 1] if off - 1 < len(follow_days) else None
            if not fd:
                case.horizons[key] = {"day": None, "pct_chg": None,
                                     "path": PATH_MISSING, "reason": "no_follow_day"}
                continue
            rec = _rows_for(fd).get(c6)
            if not rec:
                case.horizons[key] = {"day": fd, "pct_chg": None,
                                     "path": PATH_MISSING, "reason": "no_row"}
                continue
            pct = _f(rec.get("pct_chg"))
            nxt_close = _f(rec.get("close"))
            cross = None
            if pct is not None and nxt_close is not None and base_close:
                cross = round((nxt_close / base_close - 1.0) * 100.0, 4)
            suspect = (cross is not None and pct is not None
                       and abs(cross - pct) > cth.crosscheck_band)
            case.horizons[key] = {
                "day": fd, "pct_chg": pct, "path": classify_path(pct, cth),
                "close_ratio_pct": cross, "crosscheck_suspect": suspect,
            }
        # 主路径 = 第一个非 missing 的 horizon
        for off in offsets:
            p = case.horizons.get("T+%d" % off, {}).get("path")
            if p and p != PATH_MISSING:
                case.path = p
                break
        cases.append(case)
    return cases


def _f(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# ── 演化路径统计（P5.3） ──────────────────────────────────────
def path_distribution(cases: list[Case], pattern: str | None = None,
                      cth: CaseThresholds = DEFAULT_CTH) -> dict:
    """某组样本的路径分布 + 分化度（最大分支 − 次大分支，pp）。

    样本不足时 `divergence_pp=None` 且 `reason=insufficient`，
    绝不把小样本的极端比例当分化度。
    """
    sel = [c for c in cases if c.path and c.path != PATH_MISSING]
    if pattern:
        sel = [c for c in sel if pattern in c.hit_ids()]
    n = len(sel)
    counts = {k: 0 for k in PATH_ORDER}
    for c in sel:
        if c.path in counts:
            counts[c.path] += 1
    probs = {k: (round(v / n, 4) if n else 0.0) for k, v in counts.items()}
    ordered = sorted(probs.values(), reverse=True)
    div = round((ordered[0] - ordered[1]) * 100, 2) if len(ordered) >= 2 else None
    out = {
        "n": n, "counts": counts, "probs": probs,
        "dominant": max(probs, key=probs.get) if n else None,
        "divergence_pp": div,
    }
    if n < cth.min_pattern_n:
        out["divergence_pp"] = None
        out["reason"] = "insufficient"
    return out


# ── 分化节点（P5.3） ──────────────────────────────────────────
def _fork_flags(c: Case) -> dict:
    """候选分化信号；None 表示该信号对本票无数据（不计入任一侧）。"""
    mn, nf = c.main_net, c.net_mf
    divergence = None if (mn is None or nf is None) else (mn > 0 and nf < 0)
    time_ready = c.delta_kind != "unpriced" and c.delta_min is not None
    return {
        "time_ready": time_ready,
        "price_lead": (c.delta_kind == "price_lead") if time_ready else None,
        "fund_divergence": divergence,
        "limit_up": c.pool_kind == POOL_LIMIT_UP,
        "social_covered": c.social_covered,
    }


def fork_nodes(cases: list[Case], pattern: str,
               cth: CaseThresholds = DEFAULT_CTH) -> list[dict]:
    """按候选信号二分，看「主路径概率」是否发生 ≥10pp 迁移（v2 §六 分化节点）。

    两侧各需 ≥ min_branch_n；不满足记 `insufficient` 并留下两侧样本数，
    便于直接看出是哪个维度的数据缺口卡住了推演。
    """
    sel = [c for c in cases if c.path and c.path != PATH_MISSING
           and pattern in c.hit_ids()]
    overall = path_distribution(sel, cth=cth)
    dominant = overall["dominant"]
    nodes: list[dict] = []
    if not dominant:
        return nodes
    for flag in ("time_ready", "price_lead", "fund_divergence",
                 "limit_up", "social_covered"):
        a = [c for c in sel if _fork_flags(c)[flag] is True]
        b = [c for c in sel if _fork_flags(c)[flag] is False]
        pa = _path_prob(a, dominant)
        pb = _path_prob(b, dominant)
        node = {"signal": flag, "dominant_path": dominant,
                "n_true": len(a), "n_false": len(b),
                "p_true": pa, "p_false": pb,
                "node_class": ("availability_proxy"
                               if flag in AVAILABILITY_PROXY_SIGNALS
                               else "signal_value"),
                "degenerate": (not a or not b)}
        if len(a) < cth.min_branch_n or len(b) < cth.min_branch_n:
            node["status"] = "insufficient"
            node["delta_pp"] = None
        else:
            delta = round(abs(pa - pb) * 100, 2)
            node["delta_pp"] = delta
            node["status"] = "fork" if delta >= cth.fork_min_pp else "no_fork"
        # 只有「信号取值」类且样本充足、差值达标，才算条件③ 的分化节点
        node["countable"] = (node["node_class"] == "signal_value"
                             and node["status"] == "fork"
                             and not node["degenerate"])
        nodes.append(node)
    return nodes


def _path_prob(cases: list[Case], path: str) -> float:
    if not cases:
        return 0.0
    return round(sum(1 for c in cases if c.path == path) / len(cases), 4)


# ── 新验证器（P5.4） ──────────────────────────────────────────
def validate(cases: list[Case], cth: CaseThresholds = DEFAULT_CTH,
             day: str = "", horizons: tuple = (1,)) -> dict:
    """输出 v2 §六 三指标；**恒为 feasibility_only，禁止进判定闸**。"""
    with_path = [c for c in cases if c.path and c.path != PATH_MISSING]
    n_hit = sum(1 for c in cases if c.hits)
    by_pattern: dict = {}
    for pid, name, *_ in P.PATTERNS:
        dist = path_distribution(cases, pattern=pid, cth=cth)
        dist["name"] = name
        dist["nodes"] = fork_nodes(cases, pid, cth=cth)
        dist["n_fork"] = sum(1 for x in dist["nodes"] if x["countable"])
        dist["n_proxy_fork"] = sum(
            1 for x in dist["nodes"]
            if x["node_class"] == "availability_proxy" and x["status"] == "fork")
        by_pattern[pid] = dist

    divs = [v["divergence_pp"] for v in by_pattern.values()
            if v["divergence_pp"] is not None]
    return {
        "day": day,
        "horizons": ["T+%d" % h for h in horizons],
        "n_cases": len(cases),
        "n_with_path": len(with_path),
        "coverage": round(n_hit / len(cases), 4) if cases else 0.0,
        "n_hit_any": n_hit,
        "baseline_dist": path_distribution(cases, cth=cth),
        "by_pattern": by_pattern,
        "n_pattern_with_valid_div": len(divs),
        "max_divergence_pp": max(divs) if divs else None,
        "n_pattern_with_fork": sum(1 for v in by_pattern.values() if v["n_fork"]),
        # 诊断：有多少「看起来分化」其实只是数据覆盖偏倚（数据补齐后应消失）
        "n_proxy_fork_total": sum(v["n_proxy_fork"] for v in by_pattern.values()),
        # ── 闸位护栏：本轮结果一律不得用于 A/B 判定 ──
        "feasibility_only": True,
        "gate_eligible": False,
        "calibrated": False,
        "gate_block_reasons": [
            "单交易日样本，非多日累积 5000+ 涨停样本",
            "后验仅 T+1 单期，1/3/5/10 日演化无法计算",
            "样本池按涨停/触板选票，与社交采集选票同源，存在选择偏倚",
            "Thresholds / CaseThresholds 均未校准",
            "时间维度大面积 unpriced（双锚点数据缺口未补）",
        ],
    }


def write_casebook(path: str, payload: dict) -> None:
    """显式落盘（原子替换）。默认不写，CLI 需 --out 才调用。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── CLI ───────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="JEV P5.2 案例库 + 演化路径验证器（离线；默认不写盘）")
    ap.add_argument("--day", required=True, help="基准交易日 YYYYMMDD")
    ap.add_argument("--offsets", default="1", help="后验期，逗号分隔，如 1,3,5,10")
    ap.add_argument("--limit", type=int, default=None, help="事件池上限（调试用）")
    ap.add_argument("--out", default=None, help="显式落盘 JSON 路径")
    ap.add_argument("--json", action="store_true", help="stdout 输出完整 JSON")
    args = ap.parse_args(argv)

    offsets = tuple(int(x) for x in args.offsets.split(",") if x.strip())
    cases = build_casebook(args.day, offsets=offsets, limit=args.limit)
    report = validate(cases, day=args.day, horizons=offsets)
    payload = {
        "report": report,
        "follow_days": next_trading_days(args.day, max(offsets) if offsets else 1),
        "cases": [c.as_dict() for c in cases],
    }
    if args.out:
        write_casebook(args.out, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("day=%s  案例=%d  有后验=%d  命中≥1 pattern=%d  coverage=%.1f%%"
              % (args.day, report["n_cases"], report["n_with_path"],
                 report["n_hit_any"], 100 * report["coverage"]))
        print("后验交易日: %s" % (", ".join(payload["follow_days"]) or "(无)"))
        print("全池路径分布: %s" % report["baseline_dist"]["counts"])
        print("各 pattern（n / 分化度 / 分化节点数）:")
        for pid, v in report["by_pattern"].items():
            print("  %s %-8s n=%-4d div=%-8s fork=%d  %s"
                  % (pid, v["name"], v["n"], str(v["divergence_pp"]),
                     v["n_fork"], v.get("reason", "")))
        print("⚠ feasibility_only=True / gate_eligible=False：结果不得进判定闸 A/B")
        if args.out:
            print("已写出: %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
