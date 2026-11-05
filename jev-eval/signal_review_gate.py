#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jev 信号复核门（阶段 1 · 影子模式）：对候选池信号做语义复核，只标注不拦截。

架构（学自 buberlo/jev-trader 的"Jev 只拥有判断，不拥有系统"）：
  快照组装（纯代码，<400 token） → 一次 Jev 调用五个原子判断 → 影子决策（只落盘）
  → N 日真实收益回填（校准闭环） → 周报：Jev 否决组 vs 放行组收益差

数据源（零侵入，psql 直查 zhice-db）：
  v2_candidate_pool / v2_candidate_asset  当日候选信号（score + reason）
  v2_concept_membership                    候选与持仓的概念重叠
  v2_position                              当前持仓
  公告：Tushare anns_d（token 已配）→ 403/失败时降级为"无公告数据"模式

五个原子判断（全部 noul/choice——评测 100% 题型；score 75% 不用）：
  news_risk       noul   近期公告是否含未消化利空
  regime_fit      noul   当前大盘环境是否适合建仓（每日一次，缓存）
  concept_overlap noul   是否与现有持仓高度同概念
  signal_quality  choice 缠论结构完整度 高/中/低（独立复核）
  crowding        choice 该票当前是否过热拥挤

影子模式铁律：
  - 所有判定只写入本地 jsonl，不碰 zhice 任何表，不影响任何信号；
  - Jev 失败 → 标记 unreviewed，信号照常（绝不静默吞信号）；
  - 校准：官方校准针对 TypeSafe 分布，不针对本系统——必须用自己的 (快照,判定,收益)
    三元组重新验证后，才允许进入阶段 2 半自动。

用法：
  python signal_review_gate.py --once          # 复核最近交易日候选
  python signal_review_gate.py --backfill      # 回填已到期的 N 日收益
  python signal_review_gate.py --retry         # 补判 unreviewed（451 等，隔天快照滚动后重试）
  python signal_review_gate.py --report        # 影子期收益差报告
  python signal_review_gate.py --loop          # 常驻（盘中每 10 分钟查新候选）
环境变量：JEV_API_KEY（必需）；TUSHARE_TOKEN 可选（zhice .env.local 有则可复用）
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import jev_common as jc
from jev_common import C_JEV, atomic_write_text, db_rows, install_stop, log, set_module
import logging

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
DATA = HERE / "review_data"
DATA.mkdir(exist_ok=True)
TRIPLES = DATA / "triples.jsonl"  # (快照, 判定) 三元组流
ARCHIVE = DATA / "triples_archive.jsonl"  # R12：终端记录归档（毕业统计仍全量读取）
REGIME_CACHE = DATA / "regime.json"  # regime_fit 每日一判缓存
SEEN_FILE = DATA / "seen.json"  # 已复核 candidate_id

API_KEY = os.environ.get("JEV_API_KEY", "")
BASE_URL = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/")
# K-STOCK-366（084 判例）：CF 对大陆出口 geo-block → typesafe 全端点 451（edge
# 层、key 校验之前）。出站统一走 TS_PROXY（默认本机 7890 SG 出口，news_ai 同款）。
TS_PROXY = os.environ.get("TS_PROXY", "http://127.0.0.1:7890")
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
METRICS_PORT = 9102  # P1-6 Prometheus /metrics 端点

N_OUTCOME_DAYS = 5  # 校准用 N 日收益窗口
LOOKBACK_POOLS = 3  # 每次最多看最近 3 个候选池
ANN_LOOKBACK_DAYS = 14  # 公告回看窗口
# P1-1（A3 2026-10-07）：回填覆盖率守卫阈值 + 告警收件人（jev-gate.env.ps1 注入）
COVERAGE_MIN = float(os.environ.get("JEV_BACKFILL_COVERAGE_MIN", "0.5"))
LARK_ALERT_OPEN_ID = os.environ.get("LARK_ALERT_OPEN_ID", "")
# P2-2（A3 2026-10-07）：影子决策阈值 env 化（默认=历史值，影子期行为不变）
REJECT_NEWS_P = float(os.environ.get("REVIEW_NEWS_THRESHOLD", "0.7"))
REJECT_CROWD_P = float(os.environ.get("REVIEW_CROWD_THRESHOLD", "0.7"))
FOCUS_QUALITY_P = float(os.environ.get("REVIEW_FOCUS_THRESHOLD", "0.7"))
WATCH_OVERLAP_P = float(os.environ.get("REVIEW_WATCH_THRESHOLD", "0.7"))
# K-STOCK-365 P0-B 后续（2026-10-16）：CN 域 FOCUS 入口缺位修复——影子组 FOCUS_CN。
# 数据真相：CN 候选 quality 从未判 low（0/494，mid 93% 独占），非代码 bug 是模型行为：
# quality 题测的是上游信号理由充实度，CN（缠论/RPS）理由全充实，US/HK/CRYPTO（QD）简短。
# 方案：CN 用 quality_p 域内相对刻度定义"临界"（30 分位=0.53，triples.jsonl n=459 标定），
# 独立影子组 FOCUS_CN 只落 jsonl 不进生产 FOCUS 组（保持 v2 已验证基线纯净），
# 影子期攒 n_eff>=30 后以 T+5 决定转正或废弃。
CN_MID_LOW_P = float(os.environ.get("REVIEW_CN_MID_LOW_P", "0.53"))
# K-STOCK-365 P0-A：unreviewed 单条最多补判次数，超过标 jev_perm 永久放弃
RETRY_MAX = int(os.environ.get("REVIEW_RETRY_MAX", "3"))

# 优雅退出 / 原子写 / 重试 / 日志（P1-1：收敛到 jev_common）
_STOP = install_stop()


def _flush_seen() -> None:
    # run_once 末尾已 save_seen；run_backfill 信号中断由断点续传兜底（下次重新读全量）
    pass


atexit.register(_flush_seen)


# ── DB 查询（P1-7：psycopg2 直连参数化，失败自动降级 docker exec） ──
def psql(sql: str, params=None) -> list[list[str]]:
    """统一转字符串：直连返回真实类型（Decimal/date/uuid），降级路径本就是 str；
    转换后两条路径口径一致，下游解析代码无需感知。含外部输入必须带 params。"""
    rows = db_rows(sql, params)
    return [[str(v) if v is not None else "" for v in r] for r in rows]


# ── 数据组装 ────────────────────────────────────────────────
def fetch_recent_pools() -> list[dict]:
    """最近 LOOKBACK_POOLS 个候选池（pool_date 降序）。"""
    rows = psql(
        "SELECT pool_id, pool_date, pool_type, candidate_count FROM v2_candidate_pool "
        "ORDER BY pool_date DESC, id DESC LIMIT %s",
        (LOOKBACK_POOLS,),
    )
    return [
        {"pool_id": r[0], "pool_date": r[1], "pool_type": r[2], "count": r[3]}
        for r in rows
        if len(r) == 4
    ]


def fetch_candidates(pool_id: str) -> list[dict]:
    """池内候选（ts_code / market / score / reason）。"""
    rows = psql(
        "SELECT candidate_id, ts_code, market, coalesce(candidate_score,0), "
        "coalesce(candidate_reason,'') FROM v2_candidate_asset "
        "WHERE pool_id = %s ORDER BY candidate_score DESC NULLS LAST LIMIT 20",
        (pool_id,),
    )
    return [
        {
            "candidate_id": r[0],
            "ts_code": r[1],
            "market": r[2],
            "score": float(r[3] or 0),
            "reason": r[4],
        }
        for r in rows
        if len(r) == 5
    ]


def fetch_positions() -> list[str]:
    """当前持仓代码（含 OPEN/部分平仓）。"""
    rows = psql(
        "SELECT DISTINCT ts_code FROM v2_position "
        "WHERE status IN ('OPEN','PARTIAL') AND market='CN'"
    )
    return [r[0] for r in rows if r]


def fetch_concepts(ts_code: str) -> list[str]:
    """个股所属概念名（经 v2_concept_membership → v2_concept）。"""
    rows = psql(
        "SELECT c.concept_name FROM v2_concept_membership m "
        "JOIN v2_concept c ON c.id = m.concept_id "
        "WHERE m.symbol = %s LIMIT 10",
        (ts_code,),
    )
    return [r[0] for r in rows if r]


def fetch_concept_overlap(cand_concepts: list[str], held: list[str]) -> list[str]:
    """候选概念 ∩ 持仓概念 → 重叠概念名。"""
    if not cand_concepts or not held:
        return []
    rows = psql(
        "SELECT DISTINCT c.concept_name FROM v2_concept_membership m "
        "JOIN v2_concept c ON c.id = m.concept_id "
        "WHERE m.symbol = ANY(%s)",
        (held,),
    )
    held_set = {r[0] for r in rows if r}
    return [c for c in cand_concepts if c in held_set]


# ── 公告：Tushare anns_d → 失败降级 ────────────────────────
_ANN_SRC = {"mode": "unknown"}  # tushare / none


def probe_ann_source() -> None:
    """探测公告源可用性（每进程一次）。无权限/无 SDK → none 模式（问题改问"无数据"）。"""
    global _ANN_SRC
    if _ANN_SRC["mode"] != "unknown":
        return
    if not TUSHARE_TOKEN:
        _ANN_SRC = {"mode": "none"}
        return
    try:
        rows = tushare_anns("000001.SZ", lookback=3)
        _ANN_SRC = {"mode": "tushare", "ok": bool(rows)}
    except Exception as e:  # noqa: BLE001
        log(f"公告源探测失败（降级 none 模式）: {str(e)[:120]}")
        _ANN_SRC = {"mode": "none"}


def tushare_anns(ts_code: str, lookback: int = ANN_LOOKBACK_DAYS) -> list[str]:
    """Tushare anns_d 拉个股公告标题（近 lookback 天）。需要 2000+ 积分权限。

    2026-10-30 R5 修复：改用裸 HTTP 调用。tushare 包 `ts.pro_api(token)` 会把
    「40203 无接口权限」误报为「您的token不对」（判例见 quant_backtest/fetch_stock_names.py），
    导致公告源被误判为 token 失效；裸调同 token 可拿到真实错误码（如 40203 权限不足）。
    """
    import requests

    end = date.today()
    start = end - timedelta(days=lookback)
    body = {
        "api_name": "anns_d",
        "token": TUSHARE_TOKEN,
        "params": {
            "ts_code": ts_code,
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "src": "沪深京",
        },
        "fields": "ts_code,ann_date,title",
    }
    r = requests.post(
        "https://api.tushare.pro", json=body, timeout=20,
        proxies={"http": None, "https": None},
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(
            "anns_d 失败 code=%s: %s" % (d.get("code"), str(d.get("msg"))[:100])
        )
    data = d.get("data") or {}
    items = data.get("items") or []
    fields = data.get("fields") or []
    idx = fields.index("title") if "title" in fields else 2
    return [str(row[idx]) for row in items][:15]


def build_state(cand: dict, held: list[str], anns: list[str] | None) -> str:
    """组装 <400 token 状态快照。anns=None 表示无公告数据源。"""
    concepts = fetch_concepts(cand["ts_code"]) if cand.get("market") == "CN" else []
    overlap = fetch_concept_overlap(concepts, held)
    parts = [
        f"候选: {cand['ts_code']} 市场 {cand.get('market', '?')} (策略评分 {cand['score']:.3f})",
        f"信号理由: {cand['reason'][:200]}",
        f"所属概念: {', '.join(concepts[:6]) or ('仅A股提供' if cand.get('market') != 'CN' else '未知')}",
        f"现有持仓 {len(held)} 只: {', '.join(held[:8])}",
        f"与持仓重叠概念: {', '.join(overlap[:4]) or '无'}",
    ]
    if anns is None:
        parts.append("近期公告: 数据源不可用（无法核验）")
    else:
        parts.append(
            "近期公告标题: " + ("; ".join(anns[:8]) if anns else "近14天无公告")
        )
    return "\n".join(parts)


# ── Jev 五判断 ──────────────────────────────────────────────
QUESTIONS = {
    "news_risk": {
        "type": "noul",
        "instructions": "该股近期公告中存在尚未被股价消化的实质利空"
        "（立案调查/减持/业绩暴雷/重大诉讼等）。",
    },
    "concept_overlap": {
        "type": "noul",
        "instructions": "该候选与现有持仓的概念重叠构成显著集中风险（同一主线暴露过高）。",
    },
    "signal_quality": {
        "type": "choice",
        "instructions": "基于信号理由文本，独立评估该买入信号的质量。"
        "注意：理由可能来自缠论/趋势动量/事件等不同策略，按各自逻辑评估；"
        "若理由只有入场标记而无实质依据，判 low。",
        "criteria": {
            "high": "理由具体充分：结构/趋势/事件依据清晰且自洽",
            "mid": "理由基本成立但有瑕疵（依据单薄或缺少确认）",
            "low": "理由含糊、无实质内容或前后矛盾",
        },
    },
    "crowding": {
        "type": "choice",
        "instructions": "该股当前市场状态。",
        "criteria": {
            "normal": "热度正常，无拥挤迹象",
            "hot": "已明显过热/高位放量/连续涨停后追高",
        },
    },
}

REGIME_QUESTION = {
    "regime_fit": {
        "type": "noul",
        "instructions": "当前 A 股大盘环境整体适合执行新买入信号"
        "（无系统性风险事件主导、消息面未显著恶化）。",
    },
}


def jev_ask(state: str, questions: dict) -> dict:
    def _call() -> dict:
        # 2026-10-16: 后端分发收敛到 jev_common.jev_ask_backend
        # （JEV_BACKEND=ollama 走本地模型直连；typesafe.ai 451 地区屏蔽）。
        # ollama 分支不经 TS_PROXY（本地 127.0.0.1 直连，urllib 实测无劫持）。
        return jc.jev_ask_backend(state, questions)

    # K-STOCK-365 P0-A：401/403/451 是内容级确定性拒绝（TypeSafe 合规过滤——
    # 2026-10-09 起生产日志证实：同池候选部分成功部分 451，与限流/网络无关），
    # 指数退避重试必然同错，白烧 6s/条与 API 配额 → 立即上抛交由 unreviewed +
    # --retry 补判通道隔天换快照再战。其余异常保持 with_retry 语义（3 次 0/2/4s）。
    # jev_common.with_retry 无豁免机制且为公共库（gate/bridge 在用）不扩，
    # 故本文件内联等价循环，退避参数与日志格式对齐原版。
    try:
        last: Exception | None = None
        for i in range(3):
            try:
                ans = _call()
                C_JEV.labels("review", "1").inc()
                return ans
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 451):
                    raise
                last = e
            except Exception as e:  # noqa: BLE001
                last = e
            if i < 2:
                wait = 2.0 * (2**i)
                log(
                    f"jev 重试 {i + 1}/2（{wait:.0f}s）: {str(last)[:80]}", level="warn"
                )
                time.sleep(wait)
        assert last is not None
        raise last
    except Exception:
        C_JEV.labels("review", "0").inc()
        raise


def regime_ok_today() -> float:
    """大盘判断每日一次（缓存）。失败/无 key → 1.0（降级：不因环境问题漏复核）。"""
    key = date.today().isoformat()
    if REGIME_CACHE.exists():
        try:
            cache = json.loads(REGIME_CACHE.read_text(encoding="utf-8"))
            if cache.get("date") == key:
                return float(cache.get("regime_p", 1.0))
        except Exception as e:  # noqa: BLE001
            log(
                f"regime.json 缓存解析失败，忽略缓存重新计算: {str(e)[:80]}",
                level="warn",
            )  # C2批2
    p = 1.0
    if API_KEY:
        try:
            # 喂真实数据而非空问（原则：代码组装事实，Jev 只做判断）
            idx = psql(
                "SELECT trade_date, close FROM v2_bar_daily "
                "WHERE symbol IN ('000001.SH','sh000001') AND market='CN' "
                "ORDER BY trade_date DESC LIMIT 5"
            )
            idx_txt = (
                "; ".join(f"{r[0]} 收盘 {float(r[1]):.0f}" for r in idx) or "无数据"
            )
            chg = ""
            if len(idx) >= 2:
                chg = f"（近{len(idx) - 1}个交易日累计 {(float(idx[0][1]) / float(idx[-1][1]) - 1):+.1%}）"
            state = f"日期 {key}。上证指数近5个交易日: {idx_txt}{chg}。\n当前持仓 {len(fetch_positions())} 只。"
            ans = jev_ask(state, REGIME_QUESTION)
            p = float(ans.get("regime_fit", {}).get("noul", 1.0))
        except Exception as e:  # noqa: BLE001
            log(f"regime 判定失败（降级放行）: {str(e)[:100]}")
    atomic_write_text(
        REGIME_CACHE, json.dumps({"date": key, "regime_p": p}, ensure_ascii=False)
    )
    return p


def review_candidate(cand: dict, held: list[str], regime_p: float) -> dict:
    """单候选复核：快照 → 五判断 → 影子决策。返回三元组记录。"""
    anns: list[str] | None = None
    if _ANN_SRC["mode"] == "tushare" and cand.get("market") == "CN":
        try:
            anns = tushare_anns(cand["ts_code"])
        except Exception:  # noqa: BLE001
            anns = None
    state = build_state(cand, held, anns)

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "candidate_id": cand["candidate_id"],
        "ts_code": cand["ts_code"],
        "market": cand.get("market", "?"),
        "pool_date": cand.get("pool_date", ""),
        "score": cand["score"],
        "ann_mode": _ANN_SRC["mode"],
        "ann_count": len(anns) if anns is not None else -1,
        "state_len": len(state),
    }

    if not API_KEY:
        record.update({"status": "unreviewed", "reason": "no_key"})
        return record
    try:
        ans = jev_ask(state, QUESTIONS)
    except Exception as e:  # noqa: BLE001
        log(f"  {cand['ts_code']} Jev 失败: {str(e)[:100]}")
        record.update({"status": "unreviewed", "reason": "jev_error"})
        return record

    news_p = float(ans.get("news_risk", {}).get("noul", 0.0))
    overlap_p = float(ans.get("concept_overlap", {}).get("noul", 0.0))
    quality = ans.get("signal_quality", {}).get("choice", "unknown")
    quality_p = (ans.get("signal_quality", {}).get("probabilities") or {}).get(
        quality, 0.0
    )
    crowd = ans.get("crowding", {}).get("choice", "unknown")
    crowd_p = (ans.get("crowding", {}).get("probabilities") or {}).get(crowd, 0.0)

    # 影子决策（阈值 env 化 P2-2——学 buberlo：策略在代码/配置里，不在提示词里）
    # 影子期不拦截任何东西，decision 仅供校准对照。
    # 影子决策 v2（2026-09-22 回放反转，依据 replay-20260922-030434.jsonl 1633 样本）：
    # 旧规则 PASS -基线 = -0.57%，因 quality/regime 判定与 5 日收益负相关。
    # 数据真相：quality=low 组 +2.92%（最强组）、hot -1.54%（否决有效）、news 高分略差。
    # 新规则：low=临界信号加分（FOCUS），hot/news 高置信否决，regime 不做门（反向），
    # overlap 降为标注（样本内未验证）。阈值仍在配置，影子期继续验证。
    # 2026-10-07 实时三元组首验：FOCUS-PASS 收益差 +1.95%（n=194/235），v2 方向成立。
    if news_p >= REJECT_NEWS_P:
        decision, why = "REJECT", f"news_risk={news_p:.2f}"
    elif crowd == "hot" and crowd_p >= REJECT_CROWD_P:
        decision, why = "REJECT", f"crowding=hot({crowd_p:.2f})"
    elif quality == "low" and quality_p >= FOCUS_QUALITY_P:
        decision, why = "FOCUS", f"quality=low({quality_p:.2f}) 临界信号加分"
    elif quality == "low":
        decision, why = "FOCUS", f"quality=low({quality_p:.2f})"
    elif cand.get("market") == "CN" and quality == "mid" and quality_p <= CN_MID_LOW_P:
        # CN 相对临界影子组（K-STOCK-365 P0-B 后续）：不进生产 FOCUS 组，
        # 独立 FOCUS_CN 档位仅供毕业统计对照，影子期不参与任何拦截/推送决策
        decision, why = "FOCUS_CN", f"CN相对临界 mid({quality_p:.2f})"
    elif overlap_p >= WATCH_OVERLAP_P:
        decision, why = "WATCH", f"overlap={overlap_p:.2f} 概念集中标注"
    else:
        decision, why = "PASS", "regular"

    record.update(
        {
            "status": "reviewed",
            "news_p": news_p,
            "overlap_p": overlap_p,
            "quality": quality,
            "quality_p": quality_p,
            "crowd": crowd,
            "crowd_p": crowd_p,
            "regime_p": regime_p,
            "decision": decision,
            "decision_why": why,
        }
    )
    return record


# ── 持久化 ──────────────────────────────────────────────────
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log(
                f"seen.json 解析失败，已复核集清零（候选将重复复核）: {str(e)[:80]}",
                level="warn",
            )  # C2批2
            return set()
    return set()


def save_seen(seen: set) -> None:
    atomic_write_text(SEEN_FILE, json.dumps(sorted(seen)[-5000:]))


def append_triples(records: list[dict]) -> None:
    # 追加模式单写者场景相对安全；写完整个 records 列表后 flush+fsync 保证刷盘，
    # 崩溃前已落盘的行不会丢，未写完的行由下次调用恢复（不破坏流式语义）。
    with TRIPLES.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def dedup_triples(lines: list[str]) -> list[str]:
    """按 candidate_id 去重（保留末次行，位置取首现次序）。

    K-STOCK-402（四轮审计 BUG-3）：run_once 落盘顺序 append_triples →
    save_seen 之间存在崩溃窗口——triples 已写而 seen 未写，下轮对同
    candidate 重新复核并再次追加；报告/毕业条件/覆盖率按行计数不查重
    → 样本虚增可提前满足毕业门槛（②FOCUS n≥300）。消费侧统一过本
    函数；run_backfill 全量重写文件时顺带自愈物理重复行。
    （不反转落盘顺序——seen 先写会变成「triples 永丢失」，比重复更糟。）
    """
    keep: dict[str, str] = {}
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            cid = json.loads(line).get("candidate_id") or f"__idx{i}"
        except Exception:  # noqa: BLE001
            cid = f"__idx{i}"  # 坏行保留不丢（历史数据卫生问题不扩大化）
        keep[cid] = line
    return list(keep.values())


def weekdays_after(d: date, today: date) -> int:
    """d 之后（不含）到 today（含）的工作日数——交易日近似。

    K-STOCK-402（四轮审计 BUG-4）：覆盖率守卫/回填预滤原先按日历日判
    到期，而收益回填需 N_OUTCOME_DAYS 根**交易日** bar——周五池周三即
    计入 due 分母（实际仅 3 个交易日、必然未回填），长假后首日整批
    「假欠账」触发误报告警。周末口径足以消除系统性误报；法定假日仅使
    due 判定更晚（安全侧：宁可晚报不误报）。
    """
    if today <= d:
        return 0
    days = (today - d).days
    full_weeks, rem = divmod(days, 7)
    n = full_weeks * 5
    wd = d.weekday()
    for i in range(1, rem + 1):
        if (wd + i) % 7 < 5:
            n += 1
    return n


# ── 三元组轮转归档（R12：防活文件无界增长） ──────────────────
_ARCHIVE_TTL_DAYS = 30  # 终端记录保留 30 天后归档
_last_archive = ""  # 最近归档日（按日节流）


def _maybe_archive_triples() -> int:
    """终端记录（status=reviewed 且 ret_nd 已回填）超 30 天移到归档文件。

    分工：毕业统计 run_report 改读归档+活文件（样本不丢）；run_backfill 保持
    只读活文件（10min 热路径全量读改写，内存受界）；coverage_guard 只看活文件
    （近窗数据源健康，未回填记录永不归档→真实断档继续告警）。按日节流；
    缺/坏 ts 保守保留不归档。返回归档条数。
    """
    global _last_archive
    today = date.today()
    if _last_archive == today.isoformat() or not TRIPLES.exists():
        return 0
    _last_archive = today.isoformat()
    cutoff = today - timedelta(days=_ARCHIVE_TTL_DAYS)
    arch, keep = [], []
    for line in dedup_triples(TRIPLES.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get("status") == "reviewed" and r.get("ret_nd") is not None:
                ts = r.get("ts", "")
                if ts and datetime.fromisoformat(ts).date() < cutoff:
                    arch.append(line)
                    continue
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/signal_review_gate.py", exc)
        keep.append(line)
    if arch:
        with ARCHIVE.open("a", encoding="utf-8") as f:
            f.write("\n".join(arch) + "\n")
            f.flush()
            os.fsync(f.fileno())
        atomic_write_text(TRIPLES, "\n".join(keep) + "\n")
        log(
            f"三元组归档 {len(arch)} 条（活文件 {len(keep)} 条，TTL {_ARCHIVE_TTL_DAYS} 天）"
        )
    return len(arch)


def _iter_triples() -> list:
    """活文件+归档合并去重（归档在前，活文件在后→同 candidate 活文件胜出）。

    R12：run_report 毕业统计需要全量终端样本，归档后样本不丢失。
    """
    lines = []
    if ARCHIVE.exists():
        lines += ARCHIVE.read_text(encoding="utf-8").splitlines()
    if TRIPLES.exists():
        lines += TRIPLES.read_text(encoding="utf-8").splitlines()
    return dedup_triples(lines)


# ── 三种运行模式 ────────────────────────────────────────────
def run_once(dry: bool = False) -> int:
    """复核最近候选池未处理候选。返回本轮复核数。"""
    probe_ann_source()
    pools = fetch_recent_pools()
    if not pools:
        log("无候选池（等待每日选股写入 v2_candidate_pool）")
        return 0
    seen = load_seen()
    held = fetch_positions()
    regime_p = regime_ok_today()

    new_records: list[dict] = []
    for pool in pools:
        cands = fetch_candidates(pool["pool_id"])
        todo = [c for c in cands if c["candidate_id"] not in seen]
        if not todo:
            continue
        log(f"池 {pool['pool_date']} {pool['pool_type']}: {len(todo)} 待复核")
        for c in todo:
            c["pool_date"] = pool["pool_date"]
            r = (
                review_candidate(c, held, regime_p)
                if not dry
                else {
                    "candidate_id": c["candidate_id"],
                    "ts_code": c["ts_code"],
                    "status": "dry",
                    "state_len": 0,
                }
            )
            new_records.append(r)
            tag = r.get("decision") or r.get("status")
            log(
                f"  {c['ts_code']} → {tag}"
                + (f" ({r['decision_why']})" if r.get("decision_why") else "")
            )
            seen.add(c["candidate_id"])
            if not dry:
                time.sleep(0.3)  # 温和限速
    if new_records and not dry:
        append_triples(new_records)
        save_seen(seen)
    log(
        f"本轮 {len(new_records)} 条（{'dry' if dry else '已落盘'}）"
        f" regime={regime_p:.2f} 公告源={_ANN_SRC['mode']}"
    )
    return len(new_records)


def run_backfill() -> int:
    """对 pool_date 已满 N_OUTCOME_DAYS 的记录回填真实收益（v2_bar_daily 收盘价）。"""
    if _STOP["flag"]:
        return 0  # 信号已到达，跳过本轮回填（断点续传由下次调用恢复）
    if not TRIPLES.exists():
        log("无三元组记录")
        return 0
    _maybe_archive_triples()  # R12：每日归档后重新读活文件（终端记录已出活文件）
    cutoff_today = date.today()
    lines = dedup_triples(  # K-STOCK-402：崩溃窗口重复行自愈（重写即去重）
        TRIPLES.read_text(encoding="utf-8").splitlines()
    )
    out, filled, pending = [], 0, 0
    for line in lines:
        if _STOP["flag"]:
            break  # 信号到达，跳出（已读 lines 未写入，下次重新读全量）
        # 2026-10-16 审计：单条坏行/坏日期原炸整轮回填（全量三元组滞留
        # 无法出结果）——坏行原样保留，跳过继续。
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            out.append(line)
            continue
        if r.get("outcome") or r.get("status") != "reviewed":
            out.append(line)
            continue
        pd_ = r.get("pool_date", "")
        if not pd_:
            out.append(line)
            continue
        try:
            pool_d = date.fromisoformat(pd_)
        except ValueError:
            out.append(line)
            continue
        # K-STOCK-402（BUG-4）：交易日口径预滤（原日历日，周末/长假必然
        # 查不到 N+1 根 bar 白打一次 DB）；最终判定仍是下方 bar 计数
        if weekdays_after(pool_d, cutoff_today) < N_OUTCOME_DAYS:
            pending += 1
            out.append(line)
            continue
        # 入场=pool_date 收盘；出场=第 N_OUTCOME_DAYS 个交易日收盘（不足则等）
        # market 用记录自身市场（v2 修复：原硬编码 'CN'，HK/US/CRYPTO 候选永远回填不上）
        mkt = r.get("market", "CN")
        rows = psql(
            "SELECT trade_date, close FROM v2_bar_daily "
            "WHERE symbol = %s AND market = %s AND trade_date >= %s "
            "ORDER BY trade_date ASC LIMIT %s",
            (r["ts_code"], mkt, pd_, N_OUTCOME_DAYS + 1),
        )
        if len(rows) < N_OUTCOME_DAYS + 1:
            pending += 1
            out.append(line)
            continue
        entry = float(rows[0][1])
        exit_ = float(rows[N_OUTCOME_DAYS][1])
        r["outcome"] = "win" if exit_ > entry else "loss"
        r["ret_nd"] = round((exit_ - entry) / entry, 6)
        r["entry_px"], r["exit_px"] = entry, exit_
        out.append(json.dumps(r, ensure_ascii=False))
        filled += 1
    atomic_write_text(TRIPLES, "\n".join(out) + "\n")
    log(f"回填 {filled} 条，未到期 {pending} 条")
    return filled


def fetch_candidates_by_ids(ids: list[str]) -> dict[str, dict]:
    """按 candidate_id 批量回查候选（含 pool_date）——补判通道专用。"""
    if not ids:
        return {}
    ph = ", ".join(["%s"] * len(ids))  # 纯占位拼接，值全参数化（P1-7 纪律）
    rows = psql(
        "SELECT a.candidate_id, a.ts_code, a.market, "
        "coalesce(a.candidate_score,0), coalesce(a.candidate_reason,''), p.pool_date "
        "FROM v2_candidate_asset a JOIN v2_candidate_pool p ON a.pool_id = p.pool_id "
        "WHERE a.candidate_id IN (%s)" % ph,
        tuple(ids),
    )
    return {
        r[0]: {
            "candidate_id": r[0],
            "ts_code": r[1],
            "market": r[2],
            "score": float(r[3] or 0),
            "reason": r[4],
            "pool_date": r[5],
        }
        for r in rows
        if len(r) == 6
    }


def run_retry(dry: bool = False) -> int:
    """K-STOCK-365 P0-A 补判通道：unreviewed（jev_error/no_key）重建快照重新判定。

    背景：2026-10-09 起 TypeSafe 对部分候选快照稳定返回 451（内容级合规过滤），
    10-12~14 每天丢 20~60 条判定（影子期样本流失）。451 虽确定性，但快照本身
    时变（公告窗口 14 天滚动、持仓变化），隔天重建后重试有真实解除概率。
    策略：复用 review_candidate（公告探测/快照/五判断/影子决策全套同款）；
    成功 → 原记录就地更新判定字段并标 retried=1（保留原 ts 与 retry 轨迹）；
    失败 → retry_count+1，超 RETRY_MAX 标 reason=jev_perm 永久放弃。
    近似口径：held/regime 取当前值（原判定时点不可精确复原），retried 字段
    已区分两轮判定，报告消费侧可识别。
    """
    if not TRIPLES.exists():
        return 0
    lines = TRIPLES.read_text(encoding="utf-8").splitlines()
    targets: list[tuple[int, dict]] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if (
            r.get("status") == "unreviewed"
            and r.get("reason") in ("jev_error", "no_key")
            and r.get("retry_count", 0) < RETRY_MAX
            and r.get("candidate_id")
        ):
            targets.append((i, r))
    log(f"补判候选 {len(targets)} 条" + ("（dry 跳过）" if dry else ""))
    if not targets or dry:
        return 0
    if not API_KEY:
        log("无 JEV_API_KEY，补判跳过", level="warn")
        return 0
    cand_map = fetch_candidates_by_ids([r["candidate_id"] for _, r in targets])
    probe_ann_source()
    held = fetch_positions()
    regime_p = regime_ok_today()
    ok = fail = perm = miss = 0
    for i, r in targets:
        if _STOP["flag"]:
            break  # 信号到达：已处理行随末尾原子写保留，未处理下次再来
        c = cand_map.get(r["candidate_id"])
        if not c:
            miss += 1  # 候选已被池清理：保留原记录不动
            continue
        try:
            new = review_candidate(c, held, regime_p)
        except Exception as e:  # noqa: BLE001
            log(f"补判 {r.get('ts_code')} 异常: {str(e)[:80]}", level="warn")
            new = {"status": "unreviewed", "reason": "jev_error"}
        r["retry_count"] = r.get("retry_count", 0) + 1
        if new.get("status") == "reviewed":
            ok += 1
            for k in (
                "status",
                "news_p",
                "overlap_p",
                "quality",
                "quality_p",
                "crowd",
                "crowd_p",
                "regime_p",
                "decision",
                "decision_why",
            ):
                if k in new:
                    r[k] = new[k]
            r["retried"] = 1
            r["retried_at"] = datetime.now().isoformat(timespec="seconds")
        elif r["retry_count"] >= RETRY_MAX:
            perm += 1
            r["reason"] = "jev_perm"
        else:
            fail += 1
            r["reason"] = new.get("reason", "jev_error")
        lines[i] = json.dumps(r, ensure_ascii=False)
        time.sleep(0.3)  # 温和限速（对齐 run_once）
    atomic_write_text(TRIPLES, "\n".join(lines) + "\n")
    log(f"补判完成：成功 {ok} 失败 {fail} 永久放弃 {perm} 候选缺失 {miss}")
    return ok


def coverage_guard() -> float:
    """P1-1 覆盖率守卫：到期记录中已回填占比 < COVERAGE_MIN → 飞书提醒。

    分母=已满 N_OUTCOME_DAYS 窗口的 reviewed 记录（含 bar 断档未回填——这正是要暴露的欠账）。
    """
    if not TRIPLES.exists():
        return 1.0
    today = date.today()
    due = filled = 0
    for line in dedup_triples(  # K-STOCK-402 BUG-3：重复行不重复计数
        TRIPLES.read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if r.get("status") != "reviewed" or not r.get("pool_date"):
            continue
        try:
            pool_d = date.fromisoformat(r["pool_date"])
        except ValueError:
            continue  # 2026-10-16 审计：坏日期并入豁免（原炸整个守卫）
        # K-STOCK-402 BUG-4：due 判定改交易日口径（原日历日 cutoff，
        # 周五池周三即入分母但实际仅 3 个交易日无法回填 → 系统性误报）
        if weekdays_after(pool_d, today) < N_OUTCOME_DAYS:
            continue
        due += 1
        if r.get("ret_nd") is not None:
            filled += 1
    pct = filled / due if due else 1.0
    log(f"回填覆盖率 {filled}/{due} = {pct:.1%}（阈值 {COVERAGE_MIN:.0%}）")
    if due and pct < COVERAGE_MIN:
        # 2026-10-16（K-STOCK-401 判例对齐 jev_gate.push_feishu）：lark-cli
        # 注入凭证发其他 app 签发 open_id 必 99992361——改 OpenAPI 直连优先
        # （apps[0] + push_targets("个人") 自洽配对），lark-cli 降为回退。
        _text = json.dumps(
            {
                "text": (
                    f"[Jev复核门] 回填覆盖率告警：{filled}/{due} = {pct:.0%}"
                    f"（阈值 {COVERAGE_MIN:.0%}）。到期未回填多为 bar 断档，"
                    f"请核查 v2_bar_daily 数据源覆盖。"
                )
            },
            ensure_ascii=False,
        )
        sent = False
        try:
            _root = os.path.dirname(os.path.abspath(__file__))
            _nc = os.path.join(os.path.dirname(_root), "news_collector")
            if _nc not in sys.path:
                sys.path.insert(0, _nc)
            import push_targets
            from feishu_client import send_msg

            _oid = push_targets.target_id("个人")
            if _oid:
                sent, _why = send_msg(_text, "text", receive_id=_oid)
                if not sent:
                    log(f"覆盖率告警 OpenAPI 直连失败: {str(_why)[:120]}", level="warn")
        except Exception as e:  # noqa: BLE001
            log(f"覆盖率告警 OpenAPI 直连异常: {str(e)[:150]}", level="warn")
        if not sent and LARK_ALERT_OPEN_ID:
            try:
                jc.lark_cli_json(
                    [
                        "im",
                        "+messages-send",
                        "--as",
                        "bot",
                        "--user-id",
                        LARK_ALERT_OPEN_ID,
                        "--msg-type",
                        "text",
                        "--content",
                        _text,
                    ],
                    timeout=20,
                    kind="push",
                )
                sent = True
            except Exception as e:  # noqa: BLE001
                log(f"覆盖率告警 lark-cli 回退失败: {str(e)[:150]}", level="warn")
    return pct


def run_report() -> None:
    """影子期报告：Jev 决策分组 vs 真实 N 日收益——阶段 2 的唯一放行依据。"""
    if not TRIPLES.exists() and not ARCHIVE.exists():
        log("无三元组记录")
        return
    groups: dict[str, list[dict]] = {}
    for line in _iter_triples():  # R12：归档+活文件全量（毕业统计样本不丢）
        try:
            r = json.loads(line)
        except Exception:  # noqa: BLE001
            continue  # 2026-10-16 审计：坏行不炸整份报告
        if r.get("ret_nd") is not None:
            groups.setdefault(r.get("decision", "?"), []).append(r)
    if not any(groups.values()):
        log("尚无已回填收益的记录（先 --backfill，且需等待 N 日）")
        return
    print("\n===== Jev 信号复核门 · 影子期报告 =====")
    print(f"{'决策':<8}{'样本':<6}{'胜率':<8}{'均值N日收益':<12}{'中位数':<10}")
    # v2 修复：组名与影子决策 v2 对齐（FOCUS/WATCH 为 v2 新组，HUMAN 是 v1 遗留仅兼容老数据）
    # FOCUS_CN 为 2026-10-16 新增 CN 相对临界影子组，单列统计不并入 FOCUS
    for d in ("FOCUS", "FOCUS_CN", "PASS", "WATCH", "REJECT", "HUMAN"):
        rs = groups.get(d, [])
        if not rs:
            continue
        rets = [x["ret_nd"] for x in rs]
        rets.sort()
        win = sum(1 for x in rs if x["outcome"] == "win")
        med = rets[len(rets) // 2]
        print(
            f"{d:<8}{len(rs):<6}{win / len(rs):<8.1%}{sum(rets) / len(rs):<12.2%}{med:<10.2%}"
        )
    pass_ = groups.get("PASS", [])
    focus = groups.get("FOCUS", [])
    rej = groups.get("REJECT", [])
    if pass_ and rej:
        diff = (sum(x["ret_nd"] for x in pass_) / len(pass_)) - (
            sum(x["ret_nd"] for x in rej) / len(rej)
        )
        print(f"\n放行-否决 收益差: {diff:+.2%}")
    # JEV 毕业三条件（老田 2026-10-07 晚 B3 面谈拍板，riskguard-jev-calib-brief §五 #4）：
    # ① FOCUS-PASS 收益差 ≥+1% 且持续 2 周（连续两份周报达标，人工核对）
    # ② FOCUS 样本 n≥300   ③ REJECT 样本 n≥30（均按已回填 ret_nd 口径）
    # 纪律：REJECT 触发率不因凑样本放宽（否决阈值不得下调）。
    if focus and pass_:
        diff = (sum(x["ret_nd"] for x in focus) / len(focus)) - (
            sum(x["ret_nd"] for x in pass_) / len(pass_)
        )
        print(f"FOCUS-PASS 收益差: {diff:+.2%}（全量混合口径，含市场构成偏差）")

        def _mkt(x):
            tc = str(x.get("ts_code") or "")
            if tc.endswith((".SH", ".SZ", ".BJ")) or x.get("market") == "CN":
                return "CN"
            if "/USDT" in tc:
                return "CRYPTO"
            return x.get("market") or "?"

        pe = [x for x in pass_ if _mkt(x) != "CN"]
        fe = [x for x in focus if _mkt(x) != "CN"]
        dd_ex = (
            (
                (sum(x["ret_nd"] for x in fe) / len(fe))
                - (sum(x["ret_nd"] for x in pe) / len(pe))
            )
            if pe and fe
            else None
        )
        n_rej = len(groups.get("REJECT", []))
        # K-STOCK-365 P0-B：毕业①勾只认「同市场域(剔CN)」口径（2026-10-07 辛普森
        # 悖论判例：全量混合口径的收益差主要由市场构成贡献，PASS 含 CN 大跌票而
        # FOCUS CN=0，不可比）。此前判定行按全量打勾与脚注口径打架，本修统一。
        ok1 = dd_ex is not None and dd_ex >= 0.01
        d1 = f"{dd_ex:+.2%}" if dd_ex is not None else "-"
        print(
            f"毕业① 收益差≥+1%: {'✓' if ok1 else '✗'}"
            f"（剔CN同市场域 {d1} 为勾判口径；全量 {diff:+.2%} 仅参考；"
            "持续性需连续两份周报达标）"
        )
        print(
            f"毕业② FOCUS n≥300: {'✓' if len(focus) >= 300 else '✗'}"
            f"（当前 {len(focus)}）"
        )
        # 毕业③口径（2026-10-07 晚拍板，brief §八 #1）：原「REJECT n≥30」攒样
        # 需 ≈464 交易日实质不可达（触发率 0.11%，news_risk 语义猎极端尾部）
        # → 改「高危组负收益验证」：news_p≥0.5 且 CN（news 通道有效域）的
        # 已回填样本 n≥10 且均值 < PASS 同域均值。REJECT 生产阈值 0.70 不动
        # （「触发率不放宽」拍板仍有效，仅毕业考核换证据口径）。
        hi = [
            x
            for rs in groups.values()
            for x in rs
            if _mkt(x) == "CN" and (x.get("news_p") or 0) >= 0.5
        ]
        cn_pass = [x for x in pass_ if _mkt(x) == "CN"]
        hi_avg = (sum(x["ret_nd"] for x in hi) / len(hi)) if hi else None
        cp_avg = (sum(x["ret_nd"] for x in cn_pass) / len(cn_pass)) if cn_pass else None
        ok3 = len(hi) >= 10 and cp_avg is not None and hi_avg < cp_avg
        h = f"{hi_avg:+.2%}" if hi_avg is not None else "-"
        c = f"{cp_avg:+.2%}" if cp_avg is not None else "-"
        print(
            f"毕业③ 高危组负收益验证: {'✓' if ok3 else '✗'}"
            f"（news_p≥0.5 CN n={len(hi)} 均 {h} vs CN-PASS 均 {c}；"
            f"REJECT 生产 n={n_rej} 照旧拦截）"
        )
        print("三条件齐 → 阶段 2 放行评审；缺任一 → 继续影子观察")
        # 分市场拆解（2026-10-07 晚研究发现辛普森悖论，brief §7.2：混合口径
        # +1.95pp 主要由市场构成贡献——PASS 含 CN 大跌票而 FOCUS CN=0，
        # 同市场域仅 +0.67pp。条件①判读以同市场域口径为准，10-08 回补重验）
        print("分市场拆解（PASS vs FOCUS 均值N日收益）：")
        for m in ("CN", "CRYPTO", "US", "HK"):
            pm = [x for x in pass_ if _mkt(x) == m]
            fm = [x for x in focus if _mkt(x) == m]
            if not pm and not fm:
                continue
            ps = f"{sum(x['ret_nd'] for x in pm) / len(pm):+.2%}" if pm else "  -  "
            fs = f"{sum(x['ret_nd'] for x in fm) / len(fm):+.2%}" if fm else "  -  "
            print(f"  {m:<7} PASS {len(pm):>3} {ps} | FOCUS {len(fm):>3} {fs}")
        if pe and fe:
            print(f"  同市场域(剔CN) FOCUS-PASS: {dd_ex:+.2%}（= 毕业①勾判口径）")
        if not any(_mkt(x) == "CN" for x in focus):
            # K-STOCK-365 P0-B 结构性标注：CN 候选 quality 历史分布 mid/high
            # 双占（low=0），FOCUS 唯一入口 quality=low 从未在 CN 触发——
            # FOCUS 组全在 US/HK/CRYPTO，剔CN口径是被迫而非可选；Jev 对 CN
            # 候选的 quality 判定倾向值得人工抽检（非代码 bug，模型行为）。
            print(
                "⚠ 结构性缺位：CN 组 quality 从未判 low（FOCUS 唯一入口），"
                "FOCUS 全部来自 US/HK/CRYPTO——剔CN口径为被迫；"
                "FOCUS 在 A 股的价值主张尚无样本支撑"
            )
    else:
        print("判读：FOCUS/PASS 组数据不足，继续影子观察")


def main() -> None:
    set_module("review")
    jc.log_db_channel()  # JEV-DSN-20261105：启动自检（脱敏 DSN + 实际通道，不连库）
    args = sys.argv[1:]
    if "--backfill" in args:
        run_backfill()
        coverage_guard()
    elif "--retry" in args:
        run_retry(dry="--dry" in args)
    elif "--report" in args:
        run_report()
    elif "--loop" in args:
        if not API_KEY:
            sys.exit("常驻模式需要 JEV_API_KEY")
        jc.start_metrics(METRICS_PORT)
        log("复核门常驻启动（影子模式，10 分钟轮询；每日 20:05 自动回填+补判）")
        last_backfill = ""
        while not _STOP["flag"]:
            try:
                run_once()
            except Exception as e:  # noqa: BLE001
                log(f"轮询异常: {e}")
            # 每日一次自动回填+补判（收盘后数据已入库的晚间时段）
            today = date.today().isoformat()
            if datetime.now().hour >= 20 and last_backfill != today:
                last_backfill = today
                try:
                    run_backfill()
                    coverage_guard()
                    run_retry()
                except Exception as e:  # noqa: BLE001
                    log(f"自动回填/补判异常: {e}")
            # 分段 sleep 以便信号到达时能尽快退出
            for _ in range(600):
                if _STOP["flag"]:
                    break
                time.sleep(1)
    else:
        run_once(dry="--dry" in args)


if __name__ == "__main__":
    main()
