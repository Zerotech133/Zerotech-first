# -*- coding: utf-8 -*-
"""P4 权重进化器：双通道反馈 → EWMA 平滑 → 建议权重。

重设计 §五（多策略链路重设计_20261017.md）落地：
  显式反馈  strategy.feedback（accept=+1 / reject=-1 / mute=0）
  隐式反馈  signals_combined 每策略贡献 vs 次交易日收益
            （data/ashare/ 单票后复权 parquet，C: 真身全上下文可达）
  融合      target_i = 0.5·norm(ret_i) + 0.5·norm(fb_i)；缺数据的通道
            退化为另一通道；两通道全缺的策略保持原权重不动
  平滑      EWMA：w_new = ALPHA·w_target + (1-ALPHA)·w_old（防单周噪声）
  约束      归一化 Σw=1 后 clamp [W_MIN, W_MAX]

影子期纪律（对齐总研发方案 B4）：默认 dry-run 只输出建议表；--apply
才写 strategy.weights（PG upsert）+ Registry.set_weight（registry.json）。
计划任务 WeightUpdater（周日）以 dry-run 跑，--apply 待满窗数据后人工开启。

用法：
    python -X utf8 weight_updater.py                  # dry-run（近 2 周）
    python -X utf8 weight_updater.py --weeks 4        # 指定窗口
    python -X utf8 weight_updater.py --apply          # 真实写回（影子期禁用）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_QUANT_BT = os.path.dirname(_HERE)
if _QUANT_BT not in sys.path:
    sys.path.insert(0, _QUANT_BT)

from strategy_layer import Registry, bootstrap_default_adapters  # noqa: E402
import logging

# ── 常量（影子期参数，判据预注册防事后调参）────────────────────────────
ALPHA = 0.3  # EWMA 新数据权重
W_MIN, W_MAX = 0.1, 0.7
FB_ACCEPT, FB_REJECT = 1.0, -1.0  # mute 记 0
RET_CLAMP = 0.05  # 单信号次日收益截断 ±5%（防一字板极端值绑架）
NORM_FLOOR = 1e-9
ASHARE_DIR = os.path.join(_QUANT_BT, "data", "ashare")
PSQL_TIMEOUT = 10
CONNECT_TIMEOUT = 10
DOCKER_PSQL_BASE = (
    "docker",
    "exec",
    "quant-pg",
    "psql",
    "-U",
    "quant_user",
    "-d",
    "quant_system",
)

__all__ = [
    "fb_score",
    "norm01",
    "ewma",
    "normalize_clamp",
    "next_day_return",
    "compute_targets",
    "main",
]


# ── 纯函数（单测覆盖核心）─────────────────────────────────────────────
def fb_score(feedback_rows: List[Dict[str, str]], strategy_id: str) -> Optional[float]:
    """显式反馈分：Σ(verdict 分) / max(该策略总条数, 1)；无反馈返回 None。

    accept 记 +1、reject 记 -1、mute 记 0；分母含 mute（沉默不奖励）。
    """
    total, acc = 0, 0.0
    for r in feedback_rows:
        if str(r.get("strategy", "")) != strategy_id:
            continue
        total += 1
        v = str(r.get("verdict", ""))
        if v == "accept":
            acc += FB_ACCEPT
        elif v == "reject":
            acc += FB_REJECT
    if total == 0:
        return None
    return acc / total


def norm01(x: float, lo: float, hi: float) -> float:
    """线性归一到 [0,1]；hi==lo（全窗口同值）时回 0.5（无信息不偏置）。"""
    if hi - lo < NORM_FLOOR:
        return 0.5
    v = (x - lo) / (hi - lo)
    return min(max(v, 0.0), 1.0)


def ewma(w_old: float, w_target: float, alpha: float = ALPHA) -> float:
    """EWMA 平滑。"""
    return alpha * w_target + (1.0 - alpha) * w_old


def normalize_clamp(weights: Dict[str, float]) -> Dict[str, float]:
    """归一化 Σ=1 且逐项 clamp [W_MIN, W_MAX]（注水法）。

    先比例归一；越界项钉在边界并把预算让给界内项按比例重分，
    迭代至无新越界；结局再 clamp 兜底（浮点安全）。
    """
    if not weights:
        return {}
    total = sum(max(w, 0.0) for w in weights.values())
    if total < NORM_FLOOR:
        n = len(weights)
        return {k: 1.0 / n for k in weights}
    free = {k: max(v, 0.0) / total for k, v in weights.items()}
    fixed: Dict[str, float] = {}
    for _ in range(len(weights) + 1):
        budget = 1.0 - sum(fixed.values())
        tot_free = sum(free.values())
        if not free or tot_free < NORM_FLOOR or budget < NORM_FLOOR:
            break
        cand = {k: v / tot_free * budget for k, v in free.items()}
        over = {k: v for k, v in cand.items() if v > W_MAX}
        under = {k: v for k, v in cand.items() if v < W_MIN}
        if over:
            # 上界优先钉死：预算让给 free 项（重分只会抬升，先不钉下界）
            for k in over:
                fixed[k] = W_MAX
                del free[k]
            continue
        if under:
            # 上界处理完后仍低于下界 = 预算不足，钉 W_MIN 让位其余项
            for k in under:
                fixed[k] = W_MIN
                del free[k]
            continue
        fixed.update(cand)
        break
    out = {k: min(max(v, W_MIN), W_MAX) for k, v in fixed.items()}
    tot = sum(out.values())
    if tot > NORM_FLOOR:
        # Σ=1 是 aggregator 加权平均的硬约束，归一优先；
        # 仅当注水后仍越界（约束系无解的病态组合）时以 clamp 尽力限界，
        # 此时 Σ 可能轻微偏离 1（现实 n≤4、W_MIN=0.1/W_MAX=0.7 下不出现）
        norm = {k: v / tot for k, v in out.items()}
        clamped = {k: min(max(v, W_MIN), W_MAX) for k, v in norm.items()}
        tot2 = sum(clamped.values())
        if tot2 > NORM_FLOOR and all(abs(clamped[k] - norm[k]) < 1e-9 for k in norm):
            out = clamped
        else:
            out = norm
    return out


def next_day_return(
    symbol: str, trade_date: str, ashare_dir: str = ASHARE_DIR
) -> Optional[float]:
    """信号日→次交易日收益（后复权 close 相邻比）；数据缺失返回 None。

    symbol 形如 000001.SZ → ashare/000001_SZ.parquet；
    基准 = ≤trade_date 最近一日收盘；目标 = >trade_date 最早一日收盘。
    非 A 股后缀（无 .SZ/.SH/.BJ）返回 None。
    """
    m = re.fullmatch(r"([0-9]{6})\.(SZ|SH|BJ)", symbol or "")
    if not m:
        return None
    path = os.path.join(ashare_dir, "%s_%s.parquet" % (m.group(1), m.group(2)))
    if not os.path.isfile(path):
        return None
    try:
        import pyarrow.parquet as pq

        tbl = pq.read_table(path, columns=["trade_date", "close"])
        df = tbl.to_pandas()
    except Exception:  # noqa: BLE001
        return None
    if df.empty:
        return None
    df = df.dropna(subset=["trade_date", "close"])
    df = df[df["trade_date"].astype(str).str.fullmatch(r"[0-9]{8}")]
    df = df.astype({"trade_date": "int64", "close": "float64"})
    base = df[df["trade_date"] <= int(trade_date)].sort_values("trade_date")
    nxt = df[df["trade_date"] > int(trade_date)].sort_values("trade_date")
    if base.empty or nxt.empty:
        return None
    c0 = float(base.iloc[-1]["close"])
    c1 = float(nxt.iloc[0]["close"])
    if c0 <= 0:
        return None
    ret = c1 / c0 - 1.0
    return min(max(ret, -RET_CLAMP), RET_CLAMP)


# ── PG 访问（psycopg2 优先 / docker exec 兜底，同包判例）──────────────
def _load_env_local() -> None:
    env_local = os.path.join(_QUANT_BT, ".env.local")
    if not os.path.isfile(env_local):
        return
    try:
        with open(env_local, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    if k.startswith("XMAP_PG_"):
                        os.environ.setdefault(k, v.strip())
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/weight_updater.py", exc)


def _pg_connect():
    import psycopg2

    _load_env_local()
    pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError("XMAP_PG_PASSWORD 未配置（env 或 .env.local）")
    return psycopg2.connect(
        host=os.environ.get("XMAP_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("XMAP_PG_PORT", "5442")),
        user=os.environ.get("XMAP_PG_USER", "quant_user"),
        password=pwd,
        dbname=os.environ.get("XMAP_PG_DB", "quant_system"),
        connect_timeout=CONNECT_TIMEOUT,
    )


def _psql_exec(sql: str) -> str:
    cmd = DOCKER_PSQL_BASE + ("-At", "-F", "\t", "-c", sql)
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PSQL_TIMEOUT,
    )
    if p.returncode != 0:
        raise RuntimeError("docker psql rc=%d: %s" % (p.returncode, p.stderr[:200]))
    return p.stdout


def fetch_feedback(weeks: int) -> List[Dict[str, str]]:
    """近 N 周 feedback 行（psycopg2 优先 / docker 兜底）。"""
    sql = (
        "SELECT trade_date, symbol, strategy, verdict "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "FROM strategy.feedback WHERE created_at >= now() "
        "- interval '%d weeks'" % max(int(weeks), 1)
    )
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_date, symbol, strategy, verdict "
                    "FROM strategy.feedback WHERE created_at >= "
                    "now() - interval '%s weeks'",
                    (max(int(weeks), 1),),
                )
                rows = [
                    {
                        "trade_date": str(r[0]),
                        "symbol": str(r[1]),
                        "strategy": str(r[2]),
                        "verdict": str(r[3]),
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
        return rows
    except Exception:  # noqa: BLE001
        out = []
        for line in _psql_exec(sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                out.append(
                    {
                        "trade_date": parts[0],
                        "symbol": parts[1],
                        "strategy": parts[2],
                        "verdict": parts[3],
                    }
                )
        return out


def fetch_signals(weeks: int) -> List[Dict[str, Any]]:
    """近 N 周 signals_combined（contributions 已解析为 dict）。"""
    sql = (
        "SELECT trade_date, symbol, contributions::text "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "FROM strategy.signals_combined WHERE created_at >= now() "
        "- interval '%d weeks'" % max(int(weeks), 1)
    )
    try:
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_date, symbol, contributions "
                    "FROM strategy.signals_combined WHERE "
                    "created_at >= now() - interval '%s weeks'",
                    (max(int(weeks), 1),),
                )
                raw = [(str(r[0]), str(r[1]), r[2]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        raw = []
        for line in _psql_exec(sql).splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                raw.append((parts[0], parts[1], parts[2]))
    out: List[Dict[str, Any]] = []
    for td, sym, contrib in raw:
        if isinstance(contrib, (str, bytes)):
            try:
                contrib = json.loads(
                    contrib if isinstance(contrib, str) else contrib.decode("utf-8")
                )
            except ValueError:
                continue
        if isinstance(contrib, dict):
            out.append({"trade_date": td, "symbol": sym, "contributions": contrib})
    return out


# ── 融合主流程 ────────────────────────────────────────────────────────
def compute_targets(
    registry: Registry, weeks: int = 2, log: Optional[callable] = print
) -> Dict[str, Dict]:
    """双通道打分 → 目标权重 → EWMA → 归一化。返回逐策略明细（dry-run 展示与单测用）。

    返回 {sid: {"w_old","fb","ret_mean","ret_n","target","w_new"}}。
    """
    sids = registry.enabled_ids()
    w_old = {sid: registry.get_weight(sid) for sid in sids}
    fb_rows = fetch_feedback(weeks)
    sig_rows = fetch_signals(weeks)

    # 隐式通道：每策略逐信号次日收益
    rets: Dict[str, List[float]] = {sid: [] for sid in sids}
    for row in sig_rows:
        contrib = row.get("contributions") or {}
        for sid in sids:
            it = contrib.get(sid)
            if not isinstance(it, dict):
                continue
            r = next_day_return(row["symbol"], row["trade_date"])
            if r is not None:
                rets[sid].append(r)
    ret_mean = {sid: (sum(v) / len(v) if v else None) for sid, v in rets.items()}

    # 归一（全窗口 min-max；None 通道退化到另一通道）
    ret_vals = [v for v in ret_mean.values() if v is not None]
    fb_vals = [v for v in (fb_score(fb_rows, sid) for sid in sids) if v is not None]
    detail: Dict[str, Dict] = {}
    for sid in sids:
        has_ret = ret_mean.get(sid) is not None
        fb = fb_score(fb_rows, sid)
        has_fb = fb is not None
        if has_ret and has_fb:
            t = 0.5 * norm01(
                ret_mean[sid], min(ret_vals), max(ret_vals)
            ) + 0.5 * norm01(fb, min(fb_vals), max(fb_vals))
        elif has_ret:
            t = norm01(ret_mean[sid], min(ret_vals), max(ret_vals))
        elif has_fb:
            t = norm01(fb, min(fb_vals), max(fb_vals))
        else:  # 两通道全缺 → 保持原权重（w_target=w_old，EWMA 后不变）
            t = w_old[sid]
        w_new = ewma(w_old[sid], t)
        detail[sid] = {
            "w_old": round(w_old[sid], 4),
            "fb": None if fb is None else round(fb, 4),
            "ret_mean": (
                None if ret_mean.get(sid) is None else round(ret_mean[sid], 6)
            ),
            "ret_n": len(rets[sid]),
            "target": round(t, 4),
            "w_new": round(w_new, 4),
        }
    final = normalize_clamp({sid: detail[sid]["w_new"] for sid in sids})
    for sid in sids:
        detail[sid]["w_final"] = round(final.get(sid, w_old[sid]), 4)
    return detail


def apply_weights(detail: Dict[str, Dict]) -> None:
    """--apply 路径：PG strategy.weights upsert + registry.json 同步。"""
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for sid, d in detail.items():
                    cur.execute(
                        "INSERT INTO strategy.weights (strategy_id, weight) "
                        "VALUES (%s, %s) ON CONFLICT (strategy_id) "
                        "DO UPDATE SET weight = EXCLUDED.weight, "
                        "updated_at = now()",
                        (sid, d["w_final"]),
                    )
    finally:
        conn.close()
    registry = bootstrap_default_adapters(Registry())
    for sid, d in detail.items():
        if registry.is_enabled(sid) or sid in registry.list_ids():
            registry.set_weight(sid, d["w_final"])


# ── CLI ───────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="策略权重进化器（P4）")
    ap.add_argument("--weeks", type=int, default=2, help="回看窗口（周），默认 2")
    ap.add_argument(
        "--apply", action="store_true", help="真实写回（影子期禁用；默认 dry-run）"
    )
    args = ap.parse_args(argv)
    try:
        registry = bootstrap_default_adapters(Registry())
    except Exception as exc:  # noqa: BLE001
        print("[weight_updater][FATAL] 注册引导失败: %s" % exc)
        return 1
    detail = compute_targets(registry, weeks=args.weeks)
    print(
        "[weight_updater] %s 窗口=%d周 模式=%s"
        % (
            time.strftime("%Y-%m-%d %H:%M"),
            args.weeks,
            "APPLY" if args.apply else "dry-run",
        )
    )
    hdr = "%-14s %8s %8s %10s %6s %8s %8s" % (
        "strategy",
        "w_old",
        "fb",
        "ret_mean",
        "ret_n",
        "target",
        "w_final",
    )
    print(hdr)
    for sid, d in sorted(detail.items(), key=lambda kv: -kv[1]["w_final"]):
        print(
            "%-14s %8.3f %8s %10s %6d %8.3f %8.3f"
            % (
                sid,
                d["w_old"],
                "-" if d["fb"] is None else "%.2f" % d["fb"],
                "-" if d["ret_mean"] is None else "%.3f%%" % (d["ret_mean"] * 100),
                d["ret_n"],
                d["target"],
                d["w_final"],
            )
        )
    n_data = sum(1 for d in detail.values() if d["fb"] is not None or d["ret_n"] > 0)
    if n_data < len(detail):
        print(
            "[weight_updater] 有策略两通道全缺 → 权重保持不变（影子期正常，"
            "攒满数据后自动参与进化）"
        )
    if args.apply:
        apply_weights(detail)
        print("[weight_updater] 已写回 strategy.weights + registry.json")
    else:
        print("[weight_updater] dry-run 结束（--apply 才写回；影子期纪律）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
