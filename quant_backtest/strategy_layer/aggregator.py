# -*- coding: utf-8 -*-
r"""L4 组合裁决层：SignalAggregator 多策略加权融合（多策略链路重设计 §三.2）。

职责：
  · 并行调各 StrategyAdapter.score(trade_date) 收集标准 Signal；
    单策略异常 → 捕获并打印降级日志继续（降级≠失败，该策略本轮 0 贡献）
  · 同 symbol 聚合：
      - 组合分   = Σ weight_i × score_i（权重按本轮贡献策略归一化）
      - action   = 权重多数决（各 action 的权重和取最大；并列时取信号数多者，
                   再并列按 buy>sell>hold>watch 固定序取首）
      - confidence = max(各源 confidence) × 平均权重系数
                   （平均权重系数 = mean(w_i / Σw) = 1/n，n 为该 symbol 的
                    贡献策略数；单策略贡献时系数=1，不折损）
      - contributions = {strategy_id: {score, weight, action}}
      - divergence   = 同 symbol 同时出现 buy 与 sell 对立信号 → True
  · 分歧标的（combined 行 + 明细）进 divergences 清单，combined 照常保留
  · 融合结果写 PG strategy.signals_combined（psycopg2 优先，docker exec
    psql \copy 兜底）；写入失败只打印告警不中断，内存结果照常返回
  · enabled 集为空 → 返回空 AggResult

上游契约（adapter-dev 交付，本模块只 duck-type 消费，不做硬 import，
避免 signal.py/adapter.py 落盘时序耦合）：
  Signal:  symbol/action/score/confidence/rationale/strategy/trade_date
  Registry: .enabled_ids() -> list[str] / .get(id) -> adapter（weights 由
            Registry 自行从 strategy_registry.json 读出，暴露 .weights）
  Adapter: .strategy_id: str / .score(trade_date) -> list[Signal]

环境约定（同 xmap_sync.py 判例）：PG 凭据读 XMAP_PG_*（env 或
quant_backtest/.env.local），容器 quant-pg 主机口 5442、容器内 5432。
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

# ── 常量 ──────────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_LOCAL = os.path.join(PROJECT_ROOT, ".env.local")

ACTION_TIEBREAK_ORDER = ("buy", "sell", "hold", "watch")  # 全并列时的固定序
OPPOSITE_ACTION = {"buy": "sell", "sell": "buy"}          # 对立方向判定
DEFAULT_WEIGHT = 1.0                                       # 权重缺失兜底
PSQL_TIMEOUT = 10                                          # docker exec 秒数上限
CONNECT_TIMEOUT = 10                                       # psycopg2 秒数上限

COMBINED_TABLE = "strategy.signals_combined"
STAGING_TABLE = "strategy._stg_combined"  # \copy 兜底的中转暂存表
COMBINED_COLS = ("trade_date", "symbol", "action", "score", "confidence",
                 "contributions", "divergence", "rationale")
DOCKER_PSQL_BASE = ("docker", "exec", "-i", "quant-pg", "psql",
                    "-U", "quant_user", "-d", "quant_system",
                    "-v", "ON_ERROR_STOP=1")


def _load_env_local() -> None:
    """quant_backtest/.env.local 的 XMAP_PG_* 装载进进程 env（setdefault 不覆盖）。"""
    if not os.path.isfile(ENV_LOCAL):
        return
    try:
        with open(ENV_LOCAL, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("XMAP_PG_"):
                    os.environ.setdefault(key, val.strip())
    except OSError:
        pass


def _pg_connect():
    """psycopg2 连接（判例同 xmap_sync.pg_connect，超时 10s）。"""
    import psycopg2  # 延迟 import：无 psycopg2 环境走 docker exec 兜底

    _load_env_local()
    pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError("XMAP_PG_PASSWORD 未配置（env 或 quant_backtest/.env.local）")
    return psycopg2.connect(
        host=os.environ.get("XMAP_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("XMAP_PG_PORT", "5442")),
        user=os.environ.get("XMAP_PG_USER", "quant_user"),
        password=pwd,
        dbname=os.environ.get("XMAP_PG_DB", "quant_system"),
        connect_timeout=CONNECT_TIMEOUT,
    )


# ── 输出结构 ──────────────────────────────────────────────────────────
@dataclass
class AggResult:
    """聚合裁决结果：combined 为写库行，divergences 为分歧标的明细。"""

    combined: list = field(default_factory=list)     # list[dict]
    divergences: list = field(default_factory=list)  # list[dict]


# ── 工具函数 ──────────────────────────────────────────────────────────
def _sig_get(sig: Any, key: str, default: Any = None) -> Any:
    """Signal 兼容读取：dataclass 对象与 dict 双形态统一取值。"""
    if isinstance(sig, dict):
        return sig.get(key, default)
    return getattr(sig, key, default)


def _registry_weights(registry: Any) -> dict:
    """从 Registry 尽力取权重表；无 .weights 属性/不可用时返回空表（走默认权重）。"""
    try:
        w = registry.weights
    except AttributeError:
        return {}
    if callable(w):
        try:
            w = w()
        except Exception:
            return {}
    return w if isinstance(w, dict) else {}


def _persist_psycopg2(rows: list) -> bool:
    """psycopg2 execute_values 批量 upsert strategy.signals_combined。

    P2-20261017：原 INSERT 同日重跑撞 PK 整批失败（QA 实证），改
    ON CONFLICT DO UPDATE——同 (trade_date, symbol) 以新值覆盖。
    """
    import psycopg2.extras

    sql = ("INSERT INTO %s (%s) VALUES %%s "
           "ON CONFLICT (trade_date, symbol) DO UPDATE SET "
           "action = EXCLUDED.action, score = EXCLUDED.score, "
           "confidence = EXCLUDED.confidence, "
           "contributions = EXCLUDED.contributions, "
           "divergence = EXCLUDED.divergence, rationale = EXCLUDED.rationale, "
           "created_at = now()"
           % (COMBINED_TABLE, ",".join(COMBINED_COLS)))
    tuples = [
        (r["trade_date"], r["symbol"], r["action"], r["score"],
         r["confidence"], json.dumps(r["contributions"], ensure_ascii=False),
         bool(r["divergence"]), r["rationale"])
        for r in rows
    ]
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, sql, tuples,
                                               page_size=200)
    finally:
        conn.close()
    return True


def _persist_docker_exec(rows: list) -> bool:
    """docker exec psql \\\\copy 兜底 upsert（容器内 5432，无需口令）。

    P2-20261017：\\\\copy 无 upsert 语义，改三段式中转——清空暂存表 →
    \\\\copy 灌入 → INSERT..ON CONFLICT..DO UPDATE 合并后清暂存。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    for r in rows:
        writer.writerow([
            r["trade_date"], r["symbol"], r["action"], r["score"],
            r["confidence"],
            json.dumps(r["contributions"], ensure_ascii=False),
            "t" if r["divergence"] else "f",
            r["rationale"],
        ])
    stg = STAGING_TABLE
    merge_sql = (
        "INSERT INTO %s (%s) SELECT %s FROM %s "
        "ON CONFLICT (trade_date, symbol) DO UPDATE SET "
        "action = EXCLUDED.action, score = EXCLUDED.score, "
        "confidence = EXCLUDED.confidence, "
        "contributions = EXCLUDED.contributions, "
        "divergence = EXCLUDED.divergence, rationale = EXCLUDED.rationale, "
        "created_at = now(); TRUNCATE %s"
        % (COMBINED_TABLE, ",".join(COMBINED_COLS),
           ",".join(COMBINED_COLS), stg, stg))
    steps = ["TRUNCATE %s" % stg,
             "\\copy %s(%s) FROM STDIN WITH (FORMAT csv)" % (
                 stg, ",".join(COMBINED_COLS)),
             merge_sql]
    for step in steps:
        proc = subprocess.run(
            list(DOCKER_PSQL_BASE) + ["-c", step],
            input=buf.getvalue().encode("utf-8")
            if step.startswith("\\copy") else None,
            capture_output=True, timeout=PSQL_TIMEOUT,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "docker exec psql 失败: %s" % proc.stderr.decode(
                    "utf-8", "replace")[:200])
    return True


def persist_combined(rows: list) -> bool:
    """融合结果落库：psycopg2 优先、docker exec 兜底；任一成功即 True。

    调用方（run）约定：本函数任何异常都向上抛由 run 捕获降级为告警，
    绝不中断聚合主流程。
    """
    if not rows:
        return True
    errs: list = []
    for writer in (_persist_psycopg2, _persist_docker_exec):
        try:
            return bool(writer(rows))
        except Exception as exc:  # noqa: BLE001 — 兜底链路逐级降级
            errs.append("%s: %s" % (writer.__name__, exc))
    # 全链失败：携带每一级的根因（QA-20261017：只留最后一级会吞掉
    # psycopg2 主路径的原始错误，排障时缺第一现场）
    raise RuntimeError("signals_combined 写库失败（全链）: " + " | ".join(errs))


# ── 主类 ──────────────────────────────────────────────────────────────
class SignalAggregator:
    """多策略信号加权融合裁决器（L4）。

    Parameters
    ----------
    registry : Registry
        adapter-dev 交付的注册表（.enabled_ids()/.get(id)/.weights）。
        允许为 None —— 此时必须显式传 adapters，否则返回空结果。
    """

    def __init__(self, registry: Any = None) -> None:
        self.registry = registry

    # -- adapter 获取 ---------------------------------------------------
    def _registry_adapters(self) -> list:
        """registry 驱动：enabled_ids 逐个 get，单项缺失跳过（不致命）。"""
        if self.registry is None:
            return []
        out = []
        try:
            ids = list(self.registry.enabled_ids() or [])
        except Exception as exc:  # noqa: BLE001 — 注册表不可用按空集降级
            print("[aggregator][WARN] registry.enabled_ids() 失败: %s" % exc)
            return []
        for sid in ids:
            try:
                adapter = self.registry.get(sid)
            except Exception as exc:  # noqa: BLE001
                print("[aggregator][WARN] registry.get(%s) 失败，跳过: %s"
                      % (sid, exc))
                continue
            if adapter is not None:
                out.append(adapter)
        return out

    def _weight_of(self, sid: str, adapter: Any) -> float:
        """策略权重：registry.weights 优先，回落 adapter.weight，缺省 1.0。"""
        w = _registry_weights(self.registry).get(sid)
        if w is None:
            w = getattr(adapter, "weight", None)
        try:
            w = float(w)
        except (TypeError, ValueError):
            w = DEFAULT_WEIGHT
        return w if w > 0 else DEFAULT_WEIGHT

    def _top_n_of(self, sid: str, adapter: Any):
        """单策略截断数：registry.top_n(sid) 优先，回落 adapter.top_n 属性。"""
        getter = getattr(self.registry, "top_n", None)
        if callable(getter):
            try:
                n = getter(sid)
                if n is not None:
                    return max(int(n), 1)
            except (TypeError, ValueError):
                pass
        n = getattr(adapter, "top_n", None)
        try:
            return max(int(n), 1) if n is not None else None
        except (TypeError, ValueError):
            return None

    # -- 聚合核心 -------------------------------------------------------
    def run(self, trade_date: str, adapters: Optional[list] = None) -> AggResult:
        """并行收集各策略信号并融合；全程不抛异常（逐策略/写库均降级）。

        Parameters
        ----------
        trade_date : str
            交易日 YYYYMMDD。
        adapters : list | None
            显式 adapter 列表；None 时由 registry.enabled_ids()+get() 推导。

        Returns
        -------
        AggResult
            combined 行 dict 键：trade_date/symbol/action/score/confidence/
            rationale/contributions/divergence。
        """
        if adapters is None:
            adapters = self._registry_adapters()
        if not adapters:
            return AggResult()

        # 1) 逐策略收集（异常降级 0 贡献）
        collected: list = []  # (strategy_id, weight, signal)
        for adapter in adapters:
            sid = str(getattr(adapter, "strategy_id", "") or
                      type(adapter).__name__)
            try:
                signals = adapter.score(trade_date) or []
            except Exception as exc:  # noqa: BLE001 — 降级≠失败
                print("[aggregator][DEGRADED] 策略 %s score(%s) 失败，"
                      "本轮计 0 贡献: %s" % (sid, trade_date, exc))
                continue
            weight = self._weight_of(sid, adapter)
            # P2-20261017：单策略 top-N 截断（registry.json 的 top_n 配置；
            # None/缺省 = 不截断）。按 score 降序取前 N，排序型策略
            # （如 kg_boost）防全市场灌入。
            n = self._top_n_of(sid, adapter)
            if n is not None and len(signals) > n:
                orig = len(signals)
                signals = sorted(
                    signals,
                    key=lambda s: float(_sig_get(s, "score", 0) or 0),
                    reverse=True)[:n]
                print("[aggregator] 策略 %s 截断 top-%d（原 %d 条）"
                      % (sid, n, orig))
            for sig in signals:
                collected.append((sid, weight, sig))

        # 2) 同 symbol 分组
        by_symbol: dict = {}
        for sid, weight, sig in collected:
            symbol = str(_sig_get(sig, "symbol", "") or "").strip()
            if not symbol:
                continue
            by_symbol.setdefault(symbol, []).append((sid, weight, sig))

        # 3) 逐 symbol 融合
        combined: list = []
        divergences: list = []
        for symbol in sorted(by_symbol):
            row = self._fuse_symbol(trade_date, symbol,
                                    by_symbol[symbol])
            combined.append(row)
            if row["divergence"]:
                divergences.append({
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "actions": {sid: _sig_get(sig, "action", "")
                                for sid, _, sig in by_symbol[symbol]},
                    "combined_action": row["action"],
                    "combined_score": row["score"],
                })

        # 4) 落库（失败只告警不中断）
        try:
            if not persist_combined(combined):
                print("[aggregator][WARN] signals_combined 写库返回失败，"
                      "内存结果照常返回（%d 行）" % len(combined))
        except Exception as exc:  # noqa: BLE001 — 写库失败不中断
            print("[aggregator][WARN] signals_combined 写库异常，"
                  "内存结果照常返回（%d 行）: %s" % (len(combined), exc))

        return AggResult(combined=combined, divergences=divergences)

    def _fuse_symbol(self, trade_date: str, symbol: str,
                     entries: list) -> dict:
        """单 symbol 融合：加权分 / 权重多数决 action / 置信度 / contributions。"""
        total_w = sum(w for _, w, _ in entries)
        if total_w <= 0:
            # 全部权重非正时退化为等权（防御，正常权重恒 >0）
            entries = [(sid, DEFAULT_WEIGHT, sig) for sid, _, sig in entries]
            total_w = float(len(entries))

        score = sum(w * float(_sig_get(sig, "score", 0.0) or 0.0)
                    for _, w, sig in entries) / total_w

        # 权重多数决：action → (权重和, 信号数)；全并列按固定序
        tally: dict = {}
        for _, w, sig in entries:
            act = str(_sig_get(sig, "action", "hold") or "hold")
            acc = tally.setdefault(act, {"w": 0.0, "n": 0})
            acc["w"] += w
            acc["n"] += 1
        action = sorted(
            tally.items(),
            key=lambda kv: (-kv[1]["w"], -kv[1]["n"],
                            ACTION_TIEBREAK_ORDER.index(kv[0])
                            if kv[0] in ACTION_TIEBREAK_ORDER
                            else len(ACTION_TIEBREAK_ORDER)),
        )[0][0]

        # 置信度 = max(各源 confidence) × 平均权重系数（mean(w_i/Σw)=1/n）
        n = len(entries)
        max_conf = max(float(_sig_get(sig, "confidence", 0.0) or 0.0)
                       for _, _, sig in entries)
        confidence = max_conf * (sum(w / total_w for _, w, _ in entries) / n)

        contributions = {
            sid: {"score": float(_sig_get(sig, "score", 0.0) or 0.0),
                  "weight": round(w / total_w, 6),
                  "action": str(_sig_get(sig, "action", "") or "")}
            for sid, w, sig in entries
        }
        rationale = "；".join(
            "[%s] %s" % (sid, str(_sig_get(sig, "rationale", "") or "").strip())
            for sid, _, sig in entries if str(_sig_get(sig, "rationale", "") or "").strip()
        )

        actions = {_sig_get(sig, "action", "") for _, _, sig in entries}
        divergence = bool(actions & set(OPPOSITE_ACTION)
                          == set(OPPOSITE_ACTION))

        return {
            "trade_date": trade_date,
            "symbol": symbol,
            "action": action,
            "score": round(score, 4),
            "confidence": round(min(max(confidence, 0.0), 1.0), 4),
            "contributions": contributions,
            "divergence": divergence,
            "rationale": rationale,
        }


__all__ = ["AggResult", "SignalAggregator", "persist_combined"]
