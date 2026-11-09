#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jev ⇄ QuantDinger 桥：把 Jev 复核判定同步到 QuantDinger，反向复核其 watchlist。

数据流（遵循"Jev 只判断，代码执行"铁律）：

  正向 sync（zhice 候选池 → QD watchlist）：
    读 triples.jsonl 中 decision=FOCUS 的候选（Jev 判定的临界加强信号），
    按市场映射推送到 QuantDinger 自选股，实盘可见。
    市场映射：HK→HKStock / US→USStock / CRYPTO→Crypto；
    CN 不映射（QD 无 A 股市场），仅记数——A 股闭环仍在 zhice + 影子期。

  反向 review（QD watchlist → Jev）：
    拉取 QuantDinger 自选股，逐票组装事实快照（持仓语境 + 已知判定），
    交 Jev 复核"现在是否仍值得留在观察名单"，判定落本地 jsonl（影子，不删除）。
    反向数据同样进 triples 校准闭环（N 日收益回填复用 review_gate 逻辑口径）。

用法：
  python jev_qd_bridge.py --sync           # 正向：FOCUS → QD watchlist
  python jev_qd_bridge.py --review         # 反向：QD watchlist → Jev 复核
  python jev_qd_bridge.py --loop           # 常驻：每小时 sync + review
环境变量：JEV_API_KEY（review 需要）；QD_BASE_URL / QD_USER / QD_PASSWORD 可覆盖
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import jev_common as jc
from jev_common import (
    C_JEV,
    atomic_write_text,
    db_rows,
    install_stop,
    log,
    set_module,
    with_retry,
)
import logging

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
DATA = HERE / "review_data"
# 2026-10-16 审计：review_data 缺失时 QD_REVIEW.open("a") 直接
# FileNotFoundError 炸进程——启动即确保目录存在
DATA.mkdir(exist_ok=True)
TRIPLES = DATA / "triples.jsonl"  # 复核门三元组（正向数据源）
QD_REVIEW = DATA / "qd_review.jsonl"  # 反向复核判定流
QD_PUSHED = DATA / "qd_pushed.json"  # 已推送 QD 的 (market,symbol) 集合
QD_FAILED = DATA / "qd_failed.json"  # QD 目录无此标的 → 永久跳过集合

API_KEY = os.environ.get("JEV_API_KEY", "")
JEV_BASE = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/")
QD_BASE = os.environ.get("QD_BASE_URL", "http://localhost:15000").rstrip("/")
QD_USER = os.environ.get("QD_USER", "admin")
QD_PASSWORD = os.environ.get(
    "QD_PASSWORD", ""
)  # 凭据移到 .env.local，空则 qd_login 抛 RuntimeError

# zhice market → QD market（CN 无对应，跳过）
MARKET_MAP = {"HK": "HKStock", "US": "USStock", "CRYPTO": "Crypto"}
SYNC_DECISIONS = ("FOCUS",)  # 正向同步的判定集合
# P2-2（A3 2026-10-07）：阈值 env 化（默认=历史值，影子期行为不变；调优改 jev-gate.env.ps1）
REVIEW_KEEP_THRESHOLD = float(
    os.environ.get("QD_REVIEW_KEEP_THRESHOLD", "0.35")
)  # keep_p 低于此值 → flag 警示
QD_CROWD_THRESHOLD = float(
    os.environ.get("QD_CROWD_THRESHOLD", "0.7")
)  # crowding=hot 置信达此值 → flag 警示
METRICS_PORT = 9103  # P1-6 Prometheus /metrics 端点

_token_cache = {"token": "", "exp": 0.0}

# 优雅退出 / 原子写 / 重试 / 日志（P1-1：收敛到 jev_common）
_STOP = install_stop()


def _normalize_symbol(qd_market: str, symbol: str) -> str:
    """Tushare 用下划线（BRK_B），QD 美股目录用点号（BRK.B）。
    仅对 USStock 做转换；Crypto 用 '/' 分隔符、HK 无下划线，原样返回。"""
    if qd_market == "USStock" and "_" in symbol:
        return symbol.replace("_", ".")
    return symbol


def _ts_code_of(qd_market: str, symbol: str) -> str:
    """反向归一化（QD symbol → zhice ts_code，2026-10-16 审计修复）。

    known 表以 triples 的 ts_code 为键（US 下划线、HK 带 .HK 后缀）；
    QD 侧 US 用点号、HK 不带后缀——原 known.get(item.symbol) 对
    BRK.B / 00700 这类标的永远 miss，反向复核一直拿不到已知信号。
    """
    if qd_market == "USStock" and "." in symbol:
        return symbol.replace(".", "_")
    if qd_market == "HKStock" and not symbol.endswith(".HK"):
        return symbol + ".HK"
    return symbol


def _flush_pushed() -> None:
    # sync 循环里每推一条已立即 save_pushed；atexit 再写一次确保最终状态落盘（安全）
    save_pushed(load_pushed())


def _flush_failed() -> None:
    # 同 pushed：每加一条立即 save_failed；atexit 兜底最终状态落盘
    save_failed(load_failed())


atexit.register(_flush_pushed)
atexit.register(_flush_failed)


# ── QuantDinger API（薄封装） ───────────────────────────────
def qd_login() -> str:
    body = json.dumps({"username": QD_USER, "password": QD_PASSWORD}).encode()
    req = urllib.request.Request(
        QD_BASE + "/api/auth/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with jc.safe_urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    tok = (data.get("data") or {}).get("token", "")
    if not tok:
        raise RuntimeError(f"QD 登录失败: {data.get('msg')}")
    _token_cache["token"] = tok
    _token_cache["exp"] = time.time() + 3600 * 6  # token 实际 7 天，保守缓存 6h
    return tok


def qd_token() -> str:
    if not _token_cache["token"] or time.time() > _token_cache["exp"]:
        return qd_login()
    return _token_cache["token"]


def qd_call(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    for _attempt in range(2):  # 正常一次 + 401 重新登录后一次
        req = urllib.request.Request(
            QD_BASE + path,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {qd_token()}",
            },
        )
        try:
            with jc.safe_urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401 and _attempt == 0:
                _token_cache["token"] = ""  # 强制重新登录
                continue
            # 读响应体获取 QD 真实原因（如 "Symbol 'SKHY' not found on USStock"）
            try:
                err_body = e.read().decode("utf-8", errors="replace")
                try:
                    err_json = json.loads(err_body)
                    err_msg = str(
                        err_json.get("msg") or err_json.get("detail") or err_body
                    )
                except Exception:  # noqa: BLE001
                    err_msg = err_body
                return {"code": -e.code, "msg": err_msg[:200]}
            except Exception:  # noqa: BLE001
                # 豁免（C2批2）：错误码与信息随返回值上抛给调用方，非静默
                return {"code": -e.code, "msg": f"HTTP {e.code}"}
    return {"code": -401, "msg": "401 重试后仍失败"}


def qd_watchlist() -> list[dict]:
    r = qd_call("GET", "/api/market/watchlist/get")
    return list((r.get("data") or [])) if r.get("code") == 1 else []


def qd_watchlist_add(market: str, symbol: str, name: str = "") -> tuple[bool, str]:
    payload = {"market": market, "symbol": symbol}
    if name:
        payload["name"] = name
    r = qd_call("POST", "/api/market/watchlist/add", payload)
    return r.get("code") == 1, str(r.get("msg", ""))


# ── 正向：FOCUS 候选 → QD watchlist ────────────────────────
def load_pushed() -> set[str]:
    if QD_PUSHED.exists():
        try:
            return set(json.loads(QD_PUSHED.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log(
                f"qd_pushed.jsonl 解析失败，去重集清零（有重复推送风险）: {str(e)[:80]}",
                level="warn",
            )  # C2批2
    return set()


def save_pushed(s: set[str]) -> None:
    atomic_write_text(QD_PUSHED, json.dumps(sorted(s), ensure_ascii=False))


def load_failed() -> set[str]:
    """QD 目录无此标的的永久跳过集合（避免每小时重试 400 浪费 API）。"""
    if QD_FAILED.exists():
        try:
            return set(json.loads(QD_FAILED.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log(
                f"qd_failed.jsonl 解析失败，永久跳过集清零（400 标的将重试）: {str(e)[:80]}",
                level="warn",
            )  # C2批2
    return set()


def save_failed(s: set[str]) -> None:
    atomic_write_text(QD_FAILED, json.dumps(sorted(s), ensure_ascii=False))


def sync_focus_to_qd() -> None:
    """读 triples.jsonl 的 FOCUS 判定 → 按市场映射推 QD 自选股。"""
    if not TRIPLES.exists():
        log("无 triples.jsonl，先跑 signal_review_gate.py --once")
        return
    pushed = load_pushed()
    failed = load_failed()
    focus: dict[str, dict] = {}  # key=(market,normalized_symbol) → 最新判定记录
    cn_focus = 0  # CN 无法映射 QD，仅记数（修复：原在已滤 CN 的集合里数 CN 恒为 0）
    for line in TRIPLES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue  # 豁免（C2批2）：triples 坏行跳过，正常容错
        if rec.get("decision") not in SYNC_DECISIONS:
            continue
        if rec.get("market") == "CN":
            cn_focus += 1
        elif rec.get("ts_code"):
            qd_mkt = MARKET_MAP.get(rec.get("market", ""))
            if qd_mkt:
                sym_norm = _normalize_symbol(qd_mkt, rec["ts_code"])
                focus[f"{qd_mkt}\x1f{sym_norm}"] = rec

    stats = {"push": 0, "skip_pushed": 0, "skip_failed": 0, "skip_cn": 0, "err": 0}
    for key, rec in focus.items():
        qd_mkt, symbol = key.split("\x1f")
        cache_key = f"{qd_mkt}\x1f{symbol}"
        if cache_key in failed:
            stats["skip_failed"] += 1
            continue
        if cache_key in pushed:
            stats["skip_pushed"] += 1
            continue
        ok, msg = qd_watchlist_add(qd_mkt, symbol, rec.get("ts_code", ""))
        if ok:
            pushed.add(cache_key)
            stats["push"] += 1
            log(f"  推送 {qd_mkt} {symbol} ← FOCUS({rec.get('pool_date', '')})")
            save_pushed(pushed)  # 每推一条立即落盘，防中断丢已推集合
        else:
            msg_low = str(msg).lower()
            if "not found" in msg_low:
                # QD 目录无此标的 → 永久跳过，避免每小时 400 浪费 API
                failed.add(cache_key)
                stats["skip_failed"] += 1
                log(
                    f"  永久跳过 {qd_mkt} {symbol}: QD 目录无此标的（原始 ts_code={rec.get('ts_code', '')}）"
                )
                save_failed(failed)
            else:
                stats["err"] += 1
                log(f"  失败 {qd_mkt} {symbol}: {msg}")
    save_pushed(pushed)
    save_failed(failed)
    log(
        f"正向同步完成: 推送 {stats['push']} / 已推 {stats['skip_pushed']} / "
        f"永久跳过 {stats['skip_failed']} / 失败 {stats['err']}"
        f"（CN 池内 FOCUS {cn_focus} 只无法映射，留 zhice 闭环）"
    )


# ── 反向：QD watchlist → Jev 复核 ──────────────────────────
REVIEW_QUESTIONS = {
    "keep": {
        "type": "noul",
        "instructions": "综合该标的已知信号判定（Jev 复核 FOCUS=临界加强）与当前"
        "组合状态，该标的当前仍值得保留在观察名单中"
        "（无未被消化的利空、无显著过热迹象）。",
    },
    "crowding": {
        "type": "choice",
        "instructions": "该标的当前市场状态。",
        "criteria": {
            "normal": "热度正常，无拥挤迹象",
            "hot": "已明显过热/高位放量/连续大涨后追高",
        },
    },
}


# ── 行情上下文（zhice-db v2_bar_daily，QD market → zhice market） ──
ZS_MARKET_MAP = {"USStock": "US", "HKStock": "HK", "Crypto": "CRYPTO"}


def fetch_market_context(qd_market: str, symbol: str) -> str:
    """近 6 个交易日收盘 → 累计涨跌幅文本。无数据返回空串（快照退化为旧形态）。
    P1-7：psycopg2 直连参数化查询（symbol 来自 QD API 外部输入，必须参数绑定），
    直连失败自动降级 docker exec psql 变量绑定。"""
    zs_mkt = ZS_MARKET_MAP.get(qd_market)
    if not zs_mkt:
        return ""
    try:
        rows = db_rows(
            "SELECT trade_date, close FROM v2_bar_daily "
            "WHERE symbol = %s AND market = %s "
            "ORDER BY trade_date DESC LIMIT 6",
            (symbol, zs_mkt),
        )
        if len(rows) < 2:
            return ""
        closes = [(str(r[0]), float(r[1])) for r in rows]
        chg = closes[0][1] / closes[-1][1] - 1
        seq = "; ".join(f"{d} 收 {c:g}" for d, c in reversed(closes))
        return f"近{len(closes) - 1}个交易日累计 {chg:+.1%}。逐日收盘（旧→新）: {seq}"
    except Exception as e:  # noqa: BLE001
        log("行情查询失败", symbol=symbol, err=str(e)[:80], level="warn")
        return ""


def build_review_state(item: dict, known: dict | None) -> str:
    """组装反向复核快照（<400 token）：代码组装事实，Jev 只判断。"""
    parts = [
        f"标的: {item.get('symbol', '?')} 市场 {item.get('market', '?')}"
        f" 名称 {item.get('name', '') or '未知'}",
        "来源: QuantDinger 观察名单（人工/AI 加入）",
    ]
    mkt_ctx = fetch_market_context(item.get("market", ""), item.get("symbol", ""))
    if mkt_ctx:
        parts.append(f"行情: {mkt_ctx}")
    if known:
        parts.append(
            f"已知信号: zhice 候选池 {known.get('pool_date', '')} 评分 "
            f"{known.get('score', 0):.3f}，Jev 复核判定 {known.get('decision', '?')}"
            f"（{known.get('why', '')}）"
        )
    else:
        parts.append("已知信号: 无（仅 QD 观察名单，未经 zhice 候选池复核）")
    return "\n".join(parts)


def load_known_decisions() -> dict[str, dict]:
    """triples.jsonl 里每个 (market,symbol) 的最新判定（反向复核语境用）。"""
    known: dict[str, dict] = {}
    if not TRIPLES.exists():
        return known
    for line in TRIPLES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue  # 豁免（C2批2）：triples 坏行跳过，正常容错
        if rec.get("ts_code"):
            known[rec["ts_code"]] = rec  # 顺序读，后者覆盖 → 最新
    return known


def review_qd_watchlist(force: bool = False) -> None:
    """拉 QD 自选股 → 逐票 Jev 复核 → 落 qd_review.jsonl（影子，不动 QD 数据）。
    force=True 跳过 24h 去重（规则升级后重跑用）。"""
    if not API_KEY:
        log("反向复核需要 JEV_API_KEY，跳过")
        return
    items = qd_watchlist()
    if not items:
        log("QD 观察名单为空，跳过")
        return
    known = load_known_decisions()
    # 上次复核时间（避免每轮全量打 Jev：间隔 <24h 的跳过）
    last: dict[str, str] = {}
    if QD_REVIEW.exists():
        for line in QD_REVIEW.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                last[f"{rec.get('market', '')}:{rec.get('symbol', '')}"] = rec.get(
                    "ts", ""
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_qd_bridge.py", exc)
    now = datetime.now()
    n_new, n_skip, n_err = 0, 0, 0
    with QD_REVIEW.open("a", encoding="utf-8") as f:
        for item in items:
            key = f"{item.get('market', '')}:{item.get('symbol', '')}"
            prev = last.get(key, "")
            if (
                prev
                and not force
                and (now - datetime.fromisoformat(prev)).total_seconds() < 86400
            ):
                n_skip += 1
                continue
            # 2026-10-16 审计：QD symbol → ts_code 反向归一化后查 known
            # （原 BRK.B/00700 永远 miss）；原样直查保留为回退。
            sym = item.get("symbol", "")
            rec_known = known.get(
                _ts_code_of(item.get("market", ""), sym)
            ) or known.get(sym)
            state = build_review_state(item, rec_known)
            record = {
                "ts": now.isoformat(timespec="seconds"),
                "market": item.get("market", "?"),
                "symbol": item.get("symbol", "?"),
                "name": item.get("name", ""),
                "state_len": len(state),
            }
            try:
                try:
                    # 2026-10-16: 后端分发收敛到 jev_common.jev_ask_backend
                    # （JEV_BACKEND=ollama 走本地模型，typesafe.ai 451 屏蔽）
                    ans = with_retry(
                        lambda: jc.jev_ask_backend(state, REVIEW_QUESTIONS),
                        retries=3,
                        base_sec=2.0,
                        label="jev ",
                    )
                    C_JEV.labels("qdbridge", "1").inc()
                except Exception:
                    C_JEV.labels("qdbridge", "0").inc()
                    raise
                keep_p = float(ans.get("keep", {}).get("noul", 1.0))
                crowd = ans.get("crowding", {}).get("choice", "unknown")
                crowd_p = (ans.get("crowding", {}).get("probabilities") or {}).get(
                    crowd, 0.0
                )
                record.update(
                    {
                        "keep_p": keep_p,
                        "crowding": crowd,
                        "crowding_p": crowd_p,
                        # 阈值 env 化（P2-2）：QD_REVIEW_KEEP_THRESHOLD / QD_CROWD_THRESHOLD
                        "flag": "WARN"
                        if (
                            keep_p < REVIEW_KEEP_THRESHOLD
                            or (crowd == "hot" and crowd_p >= QD_CROWD_THRESHOLD)
                        )
                        else "OK",
                    }
                )
                n_new += 1
                log(
                    f"  {key}: keep={keep_p:.2f} crowd={crowd}({crowd_p:.2f}) "
                    f"→ {record['flag']}"
                )
            except Exception as e:  # noqa: BLE001
                record.update({"status": "jev_error", "err": str(e)[:120]})
                n_err += 1
                log(f"  {key}: Jev 失败 {str(e)[:80]}")
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    log(f"反向复核完成: 新复核 {n_new} / 24h内跳过 {n_skip} / 失败 {n_err}")


# ── 主入口 ──────────────────────────────────────────────────
def main() -> None:
    set_module("qdbridge")
    args = sys.argv[1:]
    if "--sync" in args:
        sync_focus_to_qd()
    elif "--review" in args:
        review_qd_watchlist(force="--force" in args)
    elif "--loop" in args:
        jc.start_metrics(METRICS_PORT)
        log("桥常驻启动：每 3600s 一轮 sync + review")
        while not _STOP["flag"]:
            try:
                sync_focus_to_qd()
            except Exception as e:  # noqa: BLE001
                log(f"sync 异常: {str(e)[:120]}")
            try:
                review_qd_watchlist()
            except Exception as e:  # noqa: BLE001
                log(f"review 异常: {str(e)[:120]}")
            # 分段 sleep 以便信号到达时能尽快退出
            for _ in range(3600):
                if _STOP["flag"]:
                    break
                time.sleep(1)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
