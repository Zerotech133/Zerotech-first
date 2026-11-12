# -*- coding: utf-8 -*-
"""收盘复盘 → 事件账：把「评分排序」观测自动挂到 signal_day 的既有事件。

为什么不直接复用 tools/preopen/log_review.py：
    它的 build_finding() 结论文字写死了 11-12 那天的数字（前 5 名胜率 0%、
    末 5 名 60%、少跌 0.25 个百分点）。换一天继续用，等于把昨天的结论
    贴到今天的证据上。本脚本全部按当日复盘产物动态生成。

三条口径防线（跨会话有效）：
    ① 挂到 data_day（信号日）的既有事件，不开新的一天 —— 事件账数的是
       「跑了几次盘前流程」，不是「看了几次结果」。
    ② 幂等键 (id, exec_day) 由 event_log.attach_drift 保证，重复跑不凑数。
    ③ regime 必须标口径来源。全市场广度不可得时降级为宽基等权粗分，
       并写明「不可与广度口径的观测直接比较」——口径不同不能互相代言。

用法：
    python tools/review/attach_close_review.py --exec-day 20261112 --dry
    python tools/review/attach_close_review.py --exec-day 20261113
"""

import argparse
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
# 交付目录与仓同级（<仓父目录>/deliverables），不硬编码绝对路径，
# 免得把本机目录结构写进脚本。
DELIV = os.environ.get("ZB_DELIV") or os.path.join(os.path.dirname(ROOT), "deliverables")
sys.path.insert(0, os.path.join(ROOT, "tools", "preopen"))

PATTERN = "SCORE_RANK_INVERTED"
# 全池口径取不到时另用一个 id：不同统计量的观测不能混进同一个 quorum。
PATTERN_TOP15_FALLBACK = "SCORE_RANK_TOP15_FALLBACK"
COST_PCT = 0.30  # A 股日频双边成本，与预判规范同口径


def find_review(exec_day: str | None) -> str:
    """取指定执行日（或最新）的收盘复盘产物。找不到要明说，不假装无事可做。"""
    if exec_day:
        p = os.path.join(DELIV, "close_review_%s.json" % exec_day)
        if not os.path.exists(p):
            raise SystemExit("[attach] BLOCKED 复盘产物不存在：%s（行情未就绪 / 复盘未跑）" % p)
        return p
    fs = sorted(glob.glob(os.path.join(DELIV, "close_review_*.json")))
    if not fs:
        raise SystemExit("[attach] BLOCKED 交付目录下没有任何收盘复盘产物")
    return fs[-1]


# ---------------------------------------------------------------------------
# 秩相关：纯 Python，不引 scipy。评分与收益都先取秩，避免单日极端值
# 把「排序有没有用」伪装成「数值有没有用」。
def _ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    dx = sum((a - mx) ** 2 for a in x)
    dy = sum((b - my) ** 2 for b in y)
    if dx <= 0 or dy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (dx * dy) ** 0.5


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or not x:
        return None
    return _pearson(_ranks(x), _ranks(y))


# ---------------------------------------------------------------------------
def market_breadth(exec_day: str) -> tuple[dict | None, str | None]:
    """全市场广度（与 11-12 那条观测同口径）。数据未就绪返回原因，不静默降级。"""
    try:
        import audit_preopen
        df = audit_preopen.load_day("tdx/ts_daily", exec_day,
                                    cols=["ts_code", "close", "pre_close"])
        if df is None or df.empty:
            return None, "当日行情分片 0 行"
        chg = df["close"] / df["pre_close"] - 1
        return {"n": int(len(chg)),
                "mean": round(float(chg.mean()) * 100, 4),
                "win": round(float((chg > 0).mean()) * 100, 2)}, None
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:100])


def regime_of(mkt: dict | None, index: dict) -> tuple[str, str, dict]:
    if mkt:
        w = float(mkt["win"])
        return ("普跌" if w < 30 else ("普涨" if w > 70 else "分化"),
                "market_breadth_v1", mkt)
    vals = [v for v in (index or {}).values() if isinstance(v, (int, float))]
    base = sum(vals) / len(vals) if vals else 0.0
    return ("普跌" if base < -1 else ("普涨" if base > 1 else "分化"),
            "index_equal_weight_fallback",
            {"n": len(vals), "mean": round(base, 4), "win": None,
             "note": "全市场广度未就绪，用复盘产物里的宽基等权涨跌粗分"})


def tiers_of(rows: list[dict]) -> dict:
    out = {}
    for label, seg in (("Top1-5", rows[:5]), ("Top6-10", rows[5:10]),
                       ("Top11-15", rows[10:15])):
        vals = [float(r["o2c"]) for r in seg if r.get("o2c") is not None]
        out[label] = {
            "n": len(vals),
            "mean": round(sum(vals) / len(vals), 3) if vals else None,
            "win": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1) if vals else None,
        }
    return out


def pool_ic(rev: dict, exec_day: str) -> tuple[dict | None, str | None]:
    """复算**全共识池**的评分秩相关，与 11-12 台账同口径。

    11-12 台账的 rank_ic(n=82) 是「82 只共识池」上的 Spearman，不是 15 只
    选票内部排序。用 15 只算出来的 rho 差了 2~3 倍（实测 -0.250 vs -0.083），
    两者不是同一个统计量，不能当同类观测攒 quorum。

    数据源：选票产物的 full_rated（82 只带 score）+ 复盘落盘的行情证据
    q_<exec_day>.json（82 只 open/pre/last）。11-12 回填验证：
    本函数复算 -0.0834 / -0.1454，台账记录 -0.083 / -0.145。
    """
    try:
        ep = (rev.get("evidence") or {}).get("path")
        if not ep:
            return None, "复盘产物缺 evidence.path（拿不到全池行情证据）"
        qp = ep if os.path.isabs(ep) else os.path.join(ROOT, ep.replace("/", os.sep))
        with io.open(qp, "r", encoding="utf-8") as fh:
            quotes = (json.load(fh).get("quotes") or {})

        pk_path = None
        bp = os.path.join(DELIV, "preopen_basket_%s.json" % exec_day)
        if os.path.exists(bp):
            with io.open(bp, "r", encoding="utf-8") as fh:
                pk_path = json.load(fh).get("pick_json")
        if not pk_path or not os.path.exists(str(pk_path)):
            # 没有篮子时按数据日推选票产物名（精排日 = data_day）。
            pk_path = os.path.join(DELIV, "preopen_pick15_v2_adj_check_%s.json"
                                   % str(rev.get("data_day")))
        if not os.path.exists(str(pk_path)):
            return None, "全池评分明细缺失：%s" % pk_path
        with io.open(str(pk_path), "r", encoding="utf-8") as fh:
            rated = json.load(fh).get("full_rated") or []

        sc, bb, aa = [], [], []
        for it in rated:
            v = quotes.get(it.get("ts_code"))
            if not v or not v.get("open") or not v.get("pre") or v.get("last") is None:
                continue
            sc.append(float(it["score"]))
            bb.append((float(v["last"]) / float(v["open"]) - 1) * 100)
            aa.append((float(v["last"]) / float(v["pre"]) - 1) * 100)
        if len(sc) < 10:
            return None, "全池可用样本仅 %d 只，不足以算秩相关" % len(sc)
        return {"n": len(sc), "rho_b": spearman(sc, bb), "rho_a": spearman(sc, aa),
                "quotes": os.path.basename(qp), "pick": os.path.basename(str(pk_path))}, None
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])


def build_finding(rev: dict, src: str) -> dict:
    exec_day = str(rev["exec_day"])
    # 挂到「信号日」的既有事件。写成 exec_day 会新开一天流程账，
    # 把「看了几次结果」算成「跑了几次流程」。
    signal_day = str(rev["data_day"])

    rows = sorted(
        [r for r in (rev.get("rows") or [])
         if not r.get("miss") and r.get("score") is not None and r.get("o2c") is not None],
        key=lambda r: -float(r["score"]))
    score = [float(r["score"]) for r in rows]
    o2c = [float(r["o2c"]) for r in rows]
    pair_c = [r for r in rows if r.get("c2c") is not None]
    top15_b = spearman(score, o2c)
    top15_a = spearman([float(r["score"]) for r in pair_c],
                       [float(r["c2c"]) for r in pair_c])
    tiers = tiers_of(rows)

    # 主口径必须是全池（与 11-12 台账可比）；取不到才降级，并换 id。
    ic, ic_why = pool_ic(rev, exec_day)
    if ic:
        rho_b, rho_a = ic["rho_b"], ic["rho_a"]
        rho_scope, pattern, n_pool = "consensus_pool_v1", PATTERN, ic["n"]
    else:
        rho_b, rho_a = top15_b, top15_a
        rho_scope, pattern, n_pool = "top15_only_fallback", PATTERN_TOP15_FALLBACK, len(rows)

    mkt, why = market_breadth(exec_day)
    regime, basis, mkt_out = regime_of(mkt, rev.get("index") or {})
    if why:
        mkt_out = dict(mkt_out, breadth_unavailable=why)

    def fmt(v):
        if isinstance(v, bool) or v is None:
            return "n/a"
        if isinstance(v, int):
            return "%d" % v
        return "%.3f" % v if isinstance(v, float) else "n/a"

    def tier_txt(k):
        t = tiers[k]
        return "%s 均值%+.3f%% / 胜率%s%% / n=%d" % (
            k, t["mean"] or 0, fmt(t["win"]), t["n"])

    evidence = (
        "%s 执行（%s 信号）。全池 %d 只评分秩相关（%s，Spearman）："
        "评分 vs 开盘买入当日收盘 rho=%s，vs 信号日收盘→执行日收盘 rho=%s。"
        "分层（15 只选票）%s、%s、%s。组合开→收 %+.3f%%，同池 %+.3f%%，"
        "市场 %s（%s，等权均值 %s%%，上涨占比 %s%%，n=%s）。"
        "双边成本 %.2f%%。行情源 %s。证据 %s / %s。"
        % (exec_day, signal_day, n_pool, rho_scope, fmt(rho_b), fmt(rho_a),
           tier_txt("Top1-5"), tier_txt("Top6-10"), tier_txt("Top11-15"),
           float(rev.get("top_o2c") or 0), float(rev.get("pool_o2c") or 0),
           basis, regime, fmt(mkt_out.get("mean")), fmt(mkt_out.get("win")),
           fmt(mkt_out.get("n")), COST_PCT, rev.get("quote_source") or "unknown",
           (ic or {}).get("quotes", "n/a"), (ic or {}).get("pick", "n/a"))
    )
    if why:
        evidence += " 注意：全市场广度未就绪（%s），本条 regime 与广度口径的观测不可直接比较。" % why
    if ic_why:
        evidence += (" 注意：全池口径不可用（%s），本条降级为 15 只内部排序，"
                     "与台账里 n=82 的 rho 不是同一统计量，已换 id，不并入同类观测计数。"
                     % ic_why)

    return {
        "id": pattern,
        "severity": "low",
        "title": "评分排序与实现收益的秩相关（单次观测，方向未定）",
        "sample_kind": "single_day",
        # 一天只能算一个独立观测。写大于 1 会让 quorum 被人为凑够。
        "independent_observations": 1,
        "signal_day": signal_day,
        "exec_day": exec_day,
        "regime": regime,
        "regime_basis": basis,
        "n_pool": n_pool,
        "rho_scope": rho_scope,
        "comparable_with_prior": bool(ic),
        "rho_open_to_close": rho_b,
        "rho_prev_close_to_close": rho_a,
        "rho_top15_open_to_close": top15_b,
        "rho_top15_prev_close_to_close": top15_a,
        "ic_sources": ic,
        "tiers": tiers,
        "cost_pct": COST_PCT,
        "market": mkt_out,
        "evidence": evidence,
        "so_what": (
            "单日、单一行情状态、全池 %d 只，属弱证据，不构成「评分方向已反转」"
            "或「评分有效」的结论。本条只登记观测，不改任何权重、因子定义或阈值；"
            "选票相对同池的差额还需扣掉 %.2f%% 双边成本才能谈净额。"
            % (n_pool, COST_PCT)
        ),
        "next": (
            "①不据此调整评分权重；②同类观测必须跨行情状态才算独立样本——"
            "3 次都在普跌日是同一个 beta 的 3 次采样，且比对前须确认 regime_basis "
            "与 rho_scope 都一致（全池 n=82 与 15 只内部排序不能混算）；"
            "③真要验证须先按预判规范交 precheck（信息上限 = 分位多空毛 spread "
            "减成本），上限不足即弃。"
        ),
        "source": os.path.relpath(src, ROOT).replace("\\", "/"),
        "appended_by": "houfeng/close_review:attach_close_review",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="收盘复盘观测 → 事件账")
    ap.add_argument("--exec-day", default=None, help="执行日 YYYYMMDD，缺省取最新复盘")
    ap.add_argument("--review", default=None, help="复盘 JSON 路径，覆盖 --exec-day 的查找")
    ap.add_argument("--dry", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()

    src = os.path.abspath(a.review) if a.review else find_review(a.exec_day)
    with io.open(src, "r", encoding="utf-8") as fh:
        rev = json.load(fh)
    if a.exec_day and str(rev.get("exec_day")) != a.exec_day:
        raise SystemExit("[attach] BLOCKED 产物 exec_day=%s 与指定 %s 不符，拒绝挂错天"
                         % (rev.get("exec_day"), a.exec_day))

    finding = build_finding(rev, src)
    day = finding["signal_day"]

    if a.dry:
        print(json.dumps({"attach_to": day, "finding": finding},
                         ensure_ascii=False, indent=1))
        return 0

    import event_log
    if not event_log.load(day):
        raise SystemExit("[attach] BLOCKED 事件 %s 不存在（盘前流程账未建立）；"
                         "事后观测不能凭空开一天流程账" % day)
    ev, added = event_log.attach_drift(day, finding)
    print("[attach] 复盘 %s → 事件 %s（%s 信号）"
          % (os.path.basename(src), day, finding["signal_day"]))
    print("[attach] %s  id=%s  exec_day=%s  regime=%s（%s）  rho_scope=%s"
          % ("新增" if added else "已存在，未重复累加", finding["id"],
             finding["exec_day"], finding["regime"], finding["regime_basis"],
             finding["rho_scope"]))
    print("[attach] 该事件现有 drift 数 = %d，事件账天数不增加"
          % len(ev.get("drift") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
