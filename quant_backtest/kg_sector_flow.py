# -*- coding: utf-8 -*-
"""板块异动工单流（六步数据流自动化 · v1 骨架）。

六步映射（方案第八章 → 本系统现状）：
  ① 源    图内日频数据（P1-P3 已全齐）：topgap 事件 × Security.industry
  ② 清洗  行业空值剔除、口径统一（ts_code / yyyymmdd）
  ③ 规则  v1 触发口径（阈值待 --replay 历史定标）：
            同一行业同日 >= N_MIN 只股上龙虎榜
            且游资净买同向（yz_net 同号）>= M_MIN 只
          交叉验证字段只做展示不做门槛：
            pen 穿透数 / 缠论结构共振数 / 席位共现对
  ④ 存储  reports/sector_flow_latest.json（当日快照，沿 rps_latest 范式）
          + sector_flow_hist.jsonl（历史追加，回放定标用）
  ⑤ 通知  飞书文本（复用 news_collector/send_feishu.py 通道）：
          有单=逐单摘要（席位跨票/共振/明细前5），无单=安静日简报；
          同 date 幂等（sector_flow_pushed.json），config 可关
  ⑥ 看板  portal /kg/flow 页（读 latest.json + hist.jsonl，只读不重算）

挂载：start_kg_sync.ps1 步骤 sector_flow（kg_features 后、链尾），
纯只读图 + 写 reports，不写图——挂载无"图写入先于重算"约束。

用法：
  python kg_sector_flow.py                     # 默认跑最近一个交易日
  python kg_sector_flow.py --date 20260929
  python kg_sector_flow.py --replay 250        # 历史回放定标（频次+行业/月份两维分布）
  python kg_sector_flow.py --replay 250 --dryrun   # 只打印，不写 calib 档案
  python kg_sector_flow.py --nmin 5 --mmin 3   # 临时阈值
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

import kg_portal as KP
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
PUSHED_F = os.path.join(REPORTS, "sector_flow_pushed.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

# ── 规则 v1 阈值（2026-10-02 250 日回放定标，20250917~20260929）──
# 5/4 → 88 单/日均 0.35（约每周 1.5 单）：行业集中真实主题（电气设备 31/
# 元器件 15/软件服务 10/化工原料 9），月均 ~7 单随行情放大（202603=17），
# 平均每单 9.9 股；对照 5/3 日均 0.87 偏噪、3/2 日均 3.28 纯噪声。
N_MIN = 5  # 同行业同日上榜股数下限
M_MIN = 4  # 游资净买同向股数下限
DEDUP_DAYS = 7  # 同行业工单去重窗口（自然日）


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def last_trade_date():
    """最近一个有 topgap 事件的交易日（图自洽，不依赖日历表）。"""
    r = KP._q("MATCH (m:MarketEvent {kind:'topgap'}) RETURN max(m.date) AS d")
    return r[0]["d"] if r and r[0]["d"] else None


# ────────────────────────────────────────────────
# ① 源 + ② 清洗
# ────────────────────────────────────────────────
def load_day(d):
    """当日 topgap 事件 → 行（industry 空/未分类剔除）。"""
    rows = KP._q(
        "MATCH (s:Security)-[:HAS_TOPGAP]->(m:MarketEvent {date:$d}) "
        "WHERE s.industry IS NOT NULL AND s.industry <> '' "
        "RETURN s.ts_code AS tc, s.name AS nm, s.industry AS ind, "
        "s.area AS area, m.gap AS gap, m.yz_net AS yz, m.pen AS pen, "
        "m.dom_nature AS nat, m.quant_ratio AS qr, m.reason AS reason",
        d=d,
    )
    return rows


# ────────────────────────────────────────────────
# ③ 规则
# ────────────────────────────────────────────────
def _d0(d, back=5):
    """触发日往前 back 个交易日（TradeDate 链，图自洽）。"""
    r = KP._q(
        "MATCH (t:TradeDate) WHERE t.date <= $d "
        "RETURN t.date AS d ORDER BY d DESC LIMIT $n",
        d=d,
        n=back,
    )
    return r[-1]["d"] if r else d


def cross_check(d, codes, back=5, back_evt=10, back_struct=20):
    """交叉验证（展示不门槛）。

    ① 席位共现（全覆盖，主指标）：同一席位当日做了工单内 ≥2 只票
       （TRADED_ON validStart=触发日）——板块协同操盘证据；
    ② 板块连续性（全覆盖）：工单票中此前 back 交易日内已上过榜的
       股数（HAS_TOPGAP 早于触发日）——区分行情首日 vs 接力日；
    ③ 技术事件共振：2026-10-02 复核修正首版"扫描池不相交"误判——
       池实为全市场相交（ashare 5595 只，榜票 100% 覆盖），稀疏根因
       是缠论信号为慢变量（确认滞后约 1 周）+ 首版窗口过窄：
       · 结构（HAS_STRUCTURE）：coalesce(detect_date, date) 口径
         ——detect_date=规则引擎最早可检出日，观察者当时可见、无前视；
         20 交易日宽窗，买卖分开计（卖点共振=板块虽热但成员已现
         衰竭信号，谨慎佐证；对照 6 个工单日实测 0/0 → 8买/10卖）；
       · 量能/手法（HAS_VOL_EVENT/HAS_CANDLE）：事件日即当日可知
         （date 口径），10 交易日中窗。
    """
    if not codes:
        return {"seat_x": [], "cont_n": 0, "tech_n": 0, "tech_breakdown": {}}
    d0 = _d0(d, back)
    # ① 席位共现（同日跨票）
    sx = KP._q(
        "MATCH (st:Seat)-[t:TRADED_ON]->(sec:Security) "
        "WHERE t.validStart = $d AND sec.ts_code IN $cs "
        "WITH st, count(DISTINCT sec) AS ns, "
        "collect(DISTINCT sec.name)[..3] AS nms "
        "WHERE ns >= 2 RETURN st.name AS seat, ns, nms "
        "ORDER BY ns DESC, seat LIMIT 10",
        d=d,
        cs=codes,
    )
    # ② 板块连续性：窗口内更早已有 topgap 的工单票数
    cont = KP._q(
        "MATCH (s:Security)-[:HAS_TOPGAP]->(m:MarketEvent) "
        "WHERE s.ts_code IN $cs AND m.date >= $d0 AND m.date < $d "
        "RETURN count(DISTINCT s.ts_code) AS n",
        cs=codes,
        d0=d0,
        d=d,
    )[0]["n"]
    # ③-1 缠论结构共振（宽窗，双口径）：
    #   主口径=已确认（coalesce(detect_date, date)）：规则引擎最早可
    #   检出日，观察者当时可见、无前视；
    #   复盘口径=信号日（date）：结构在 K 线上完成的时点——确认滞后
    #   是算法成本非信息不可得，对"左侧布局前置期"语义更实质
    #   （例 0914 元器件：0903 结构完成 → 0916+ 确认，工单日已确认 0、
    #   信号日口径 3 买）。
    d0_st = _d0(d, back_struct)
    bd = {}
    for key, col in (
        ("结构买", "coalesce(m.detect_date, m.date)"),
        ("结构卖", "coalesce(m.detect_date, m.date)"),
        ("结构买dt", "m.date"),
        ("结构卖dt", "m.date"),
    ):
        bd[key] = KP._q(
            "MATCH (s:Security)-[:HAS_STRUCTURE]->(m:MarketEvent) "
            "WHERE s.ts_code IN $cs AND %s >= $w0 AND %s <= $d "
            "AND m.type ENDS WITH $bs "
            "RETURN count(DISTINCT s.ts_code) AS n" % (col, col),
            cs=codes,
            w0=d0_st,
            d=d,
            bs=("买" if "买" in key else "卖"),
        )[0]["n"]
    # ③-2 量能/手法共振（中窗 + 事件日口径，当日可知无滞后）
    d0_ev = _d0(d, back_evt)
    for rel, key in (("HAS_VOL_EVENT", "量能"), ("HAS_CANDLE", "手法")):
        bd[key] = KP._q(
            "MATCH (s:Security)-[:%s]->(m:MarketEvent) "
            "WHERE s.ts_code IN $cs AND m.date >= $d0 AND m.date <= $d "
            "RETURN count(DISTINCT s.ts_code) AS n" % rel,
            cs=codes,
            d0=d0_ev,
            d=d,
        )[0]["n"]
    return {
        "seat_x": [{"seat": r["seat"], "n": r["ns"], "stocks": r["nms"]} for r in sx],
        "cont_n": cont,
        "tech_n": max(bd.values()) if bd else 0,
        "tech_breakdown": bd,
    }


def apply_rules(rows, n_min=N_MIN, m_min=M_MIN):
    """行业分组 → 触发工单列表。"""
    by_ind = {}
    for r in rows:
        by_ind.setdefault(r["ind"], []).append(r)
    tickets = []
    for ind, rs in sorted(by_ind.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < n_min:
            continue
        # 游资净买同向：yz_net 同号的票数（正/负取多的一方）
        yz_pos = [r for r in rs if (r.get("yz") or 0) > 0]
        yz_neg = [r for r in rs if (r.get("yz") or 0) < 0]
        same = max(len(yz_pos), len(yz_neg))
        if same < m_min:
            continue
        [r["tc"] for r in rs]
        pens = [r for r in rs if r.get("pen") == 1]
        tickets.append(
            {
                "industry": ind,
                "n_listed": len(rs),  # 上榜股数
                "n_yz_same": same,  # 游资同向数
                "yz_dir": "+" if len(yz_pos) >= len(yz_neg) else "-",
                "n_pen": len(pens),  # 次日穿透数（展示）
                "gap_sum": round(sum(r.get("gap") or 0 for r in rs) / 1e8, 3),
                # ↑ 亿元，行业合计差额（展示）
                "stocks": [
                    {
                        "tc": r["tc"],
                        "nm": r["nm"],
                        "yz_w": round((r.get("yz") or 0) / 1e4, 1),
                        "pen": r.get("pen"),
                        "reason": (r.get("reason") or "")[:24],
                    }
                    for r in sorted(rs, key=lambda x: -(abs(x.get("yz") or 0)))
                ],
                "cross": None,  # cross_check 结果挂载点
            }
        )
    return tickets


# ────────────────────────────────────────────────
# ④ 存储
# ────────────────────────────────────────────────
def save(tickets, d, dryrun=False):
    os.makedirs(REPORTS, exist_ok=True)
    doc = {
        "date": d,
        "generated_at": _now(),
        "rule": {"n_min": N_MIN, "m_min": M_MIN},
        "n_tickets": len(tickets),
        "tickets": tickets,
    }
    if dryrun:
        return doc
    with open(
        os.path.join(REPORTS, "sector_flow_latest.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    # hist 追加（同 date 幂等：链补跑场景去重，保留最新一行口径）
    hp = os.path.join(REPORTS, "sector_flow_hist.jsonl")
    rows = []
    if os.path.exists(hp):
        with open(hp, encoding="utf-8") as f:
            rows = [ln for ln in f if ln.strip() and json.loads(ln).get("date") != d]
    rows.append(
        json.dumps(
            {"date": d, "n": len(tickets), "inds": [t["industry"] for t in tickets]},
            ensure_ascii=False,
        )
        + "\n"
    )
    with open(hp, "w", encoding="utf-8") as f:
        f.writelines(rows)
    return doc


# ────────────────────────────────────────────────
# ⑤ 通知（飞书文本卡片，复用 chanlun 强预警推送通道）
# ────────────────────────────────────────────────
def _fmt_bd(bd):
    """技术共振 dict → '买0/卖0·dt3/0·量5·法1' 紧凑串。"""
    if not bd:
        return "—"
    g = lambda k: bd.get(k) or 0  # noqa: E731
    return "买%d/卖%d·dt%d/%d·量%d·法%d" % (
        g("结构买"),
        g("结构卖"),
        g("结构买dt"),
        g("结构卖dt"),
        g("量能"),
        g("手法"),
    )


def _notify_text(doc):
    """工单文档 → 飞书文本（模板：头部汇总 + 逐单摘要 + 口径脚注）。"""
    lines = [
        "🧨 板块异动工单 · %s" % doc.get("date"),
        "触发 %d 单（n_min=%s m_min=%s，游资同向口径）"
        % (
            doc.get("n_tickets") or 0,
            (doc.get("rule") or {}).get("n_min"),
            (doc.get("rule") or {}).get("m_min"),
        ),
        "",
    ]
    for t in doc.get("tickets") or []:
        cx = t.get("cross") or {}
        lines.append(
            "[%s] %d 股 · 游资%s同向 %d · 穿透 %d/%d"
            % (
                t["industry"],
                t["n_listed"],
                t["yz_dir"],
                t["n_yz_same"],
                t.get("n_pen") or 0,
                t["n_listed"],
            )
        )
        lines.append(
            "  连续 %d · 跨票席位 %d · 共振 %s · 差额 %+.2f亿"
            % (
                cx.get("cont_n") or 0,
                len(cx.get("seat_x") or []),
                _fmt_bd(cx.get("tech_breakdown")),
                t.get("gap_sum") or 0,
            )
        )
        for sx in (cx.get("seat_x") or [])[:2]:
            lines.append(
                "  跨票：%s ×%d（%s）" % (sx["seat"], sx["n"], "、".join(sx["stocks"]))
            )
        ss = t.get("stocks") or []
        det = " · ".join(
            "%s%+.0f万%s"
            % (s["nm"], s.get("yz_w") or 0, "·pen" if s.get("pen") else "")
            for s in ss[:5]
        )
        more = " …等%d股" % len(ss) if len(ss) > 5 else ""
        lines.append("  明细：%s%s" % (det, more))
        lines.append("")
    cfg = {}
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_sector_flow.py", exc)
    lines.append(
        "口径：同行业同日≥%d股上榜+游资净买同向≥%d（250日定标 "
        "日均0.35单）；共振买/卖=已确认口径 dt=信号日复盘口径" % (N_MIN, M_MIN)
    )
    portal = (cfg.get("report") or {}).get("portal_url") or ""
    if portal:
        lines.append("看板：%skg/flow" % portal)
    return "\n".join(lines)


def _push_feishu(text):
    """写临时文件 → 子进程调 news_collector/send_feishu.py（与
    chanlun_scan._push_feishu_text 同通道同口径）。"""
    if not os.path.isdir(NEWS_DIR):
        print("push skip: news_collector not found")
        return False
    tmp = os.path.join(NEWS_DIR, "outbox", "pending_sector_flow.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        return (r.stdout or "").strip().startswith("SEND_OK")
    except Exception as e:
        print("push error: %s" % str(e)[:120])
        return False


def notify(doc):
    """飞书工单推送：同 date 幂等（sector_flow_pushed.json 记已发日），
    config.json sector_flow.push=false 或 feishu.push=false 可关。
    零工单也发一条简报（夜链无触发=安静确认，与"漏跑"区分）。"""
    cfg = {}
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_sector_flow.py", exc)
    if (cfg.get("sector_flow") or {}).get("push") is False:
        print("notify: disabled by config")
        return False
    if not (cfg.get("feishu") or {}).get("push", True):
        print("notify: master feishu.push off")
        return False
    d = doc.get("date")
    pushed = set()
    try:
        with open(PUSHED_F, encoding="utf-8") as f:
            pushed = set(json.load(f))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_sector_flow.py", exc)
    if d in pushed:
        print("notify: %s already pushed" % d)
        return False
    if not (doc.get("tickets")):
        text = "🧨 板块异动工单 · %s\n无触发（n_min=%s m_min=%s）——安静日" % (
            d,
            (doc.get("rule") or {}).get("n_min"),
            (doc.get("rule") or {}).get("m_min"),
        )
    else:
        text = _notify_text(doc)
    ok = _push_feishu(text)
    if ok:
        pushed.add(d)
        os.makedirs(REPORTS, exist_ok=True)
        with open(PUSHED_F, "w", encoding="utf-8") as f:
            json.dump(sorted(pushed), f, ensure_ascii=False)
        print("notify: PUSHED %s（%d 单）" % (d, doc.get("n_tickets") or 0))
    else:
        print("notify: SEND_FAILED（下轮重试）")
    return ok


# ────────────────────────────────────────────────
# 阈值定标：历史回放
# ────────────────────────────────────────────────
CALIB_F = os.path.join(REPORTS, "sector_flow_calib.json")


def ym_of(d):
    """'20260929' → '202609'。非 yyyymmdd（缺省/异常/短串）归 'unknown'，
    不做猜测性截断——截断会把 '2026' 当成月份，静默污染分布。"""
    s = str(d or "")
    return s[:6] if (len(s) == 8 and s.isdigit()) else "unknown"


def dist_of(pairs, topk=10):
    """[(date, industry)] → 两维分布。

    行业维：按触发单数降序取 topk，其余合并为「其他」（industry_tail_n
    记被合并的行业个数，不丢量级信息）；
    月份维：yyyymm 全量计数，按月升序（时间序，不用频次序——频次序会让
    「哪个月热」难以按月对比）。
    口径说明：一律按**触发单数**计（一单一行），不是按上榜股数；
    去重行业日已在 hit/ind_days 里单独给，两者不可混读。
    """
    ic, mc = {}, {}
    for d, ind in pairs:
        ic[ind] = ic.get(ind, 0) + 1
        k = ym_of(d)
        mc[k] = mc.get(k, 0) + 1
    order = sorted(ic.items(), key=lambda kv: (-kv[1], kv[0]))
    head = order[:topk]
    tail = order[topk:]
    return {
        "industry_dist": {
            "top": [{"industry": k, "n": v} for k, v in head],
            "other_n": sum(v for _, v in tail),
            "industry_tail_n": len(tail),
            "industry_total_n": len(ic),
            "total": sum(ic.values()),
        },
        "month_dist": [{"ym": k, "n": v} for k, v in sorted(mc.items())],
    }


def replay(n_days=250, topk=10, dryrun=False):
    """近 n_days 个交易日回放：不同 (n_min, m_min) 组合的触发频次，
    外加默认阈值口径的**行业/月份两维分布**（L-08）。

    输出：stdout 明细 + reports/sector_flow_calib.json（定标档案，机读；
    dryrun=True 只打印不落盘）。该文件与 sector_flow_latest.json 是不同
    生产者、不同用途，不互相覆盖。
    """
    ds = [
        r["d"]
        for r in KP._q(
            "MATCH (m:MarketEvent {kind:'topgap'}) "
            "RETURN DISTINCT m.date AS d ORDER BY d DESC LIMIT $n",
            n=n_days,
        )
    ]
    print(
        "[replay] %d 个交易日（%s~%s）"
        % (len(ds), ds[-1] if ds else "-", ds[0] if ds else "-")
    )
    grid = [(n, m) for n in (3, 4, 5, 6) for m in (2, 3, 4)]
    hit = {(n, m): 0 for n, m in grid}
    ind_days = {(n, m): set() for n, m in grid}  # 去重口径备用
    pairs = {(n, m): [] for n, m in grid}  # 两维分布的原始 (date, industry)
    for d in ds:
        rows = load_day(d)
        for n, m in grid:
            ts = apply_rules(rows, n, m)
            hit[(n, m)] += len(ts)
            for t in ts:
                ind_days[(n, m)].add((d, t["industry"]))
                pairs[(n, m)].append((d, t["industry"]))
    for n, m in sorted(grid):
        # 2026-11-07 审计修复：原 `len(ind_days[(n, m)])` 结果被丢弃（无副作用），
        # 读者会误以为已用上"去重行业日"口径；改为随同输出，口径可见。
        print(
            "  n_min=%d m_min=%d → 触发 %d 单（日均 %.2f，去重行业日 %d）"
            % (n, m, hit[(n, m)], hit[(n, m)] / max(1, len(ds)),
               len(ind_days[(n, m)]))
        )
    # 两维明细只给默认口径（当前生产阈值），避免 12 组合刷屏；
    # 全组合分布全部写进 calib 档案。
    cur = (N_MIN, M_MIN)
    dists = {("%d/%d" % k): dist_of(v, topk) for k, v in pairs.items()}
    cd = dists.get("%d/%d" % cur) or dist_of([], topk)
    ind = cd["industry_dist"]
    if ind["top"]:
        print(
            "  行业 top%d（n_min=%d m_min=%d）：%s（共 %d 行业，其他 %d 单/%d 行业）"
            % (
                min(topk, len(ind["top"])),
                cur[0],
                cur[1],
                " · ".join("%s %d" % (x["industry"], x["n"]) for x in ind["top"]),
                ind["industry_total_n"],
                ind["other_n"],
                ind["industry_tail_n"],
            )
        )
    if cd["month_dist"]:
        peak = max(cd["month_dist"], key=lambda x: x["n"])
        print(
            "  月份分布：%s ｜ 峰 %s %d 单"
            % (
                " · ".join("%s %d" % (x["ym"], x["n"]) for x in cd["month_dist"]),
                peak["ym"],
                peak["n"],
            )
        )
    doc = {
        "generated_at": _now(),
        "n_days": len(ds),
        "window": [ds[-1], ds[0]] if ds else [],
        "rule": {"n_min": N_MIN, "m_min": M_MIN},
        "topk": topk,
        "grid": [
            {
                "n_min": n,
                "m_min": m,
                "n_tickets": hit[(n, m)],
                "per_day": round(hit[(n, m)] / max(1, len(ds)), 4),
                "n_ind_days": len(ind_days[(n, m)]),
            }
            for n, m in sorted(grid)
        ],
        "dist_by_rule": dists,
        "industry_dist": ind,  # 默认口径两维，置顶方便消费方直取
        "month_dist": cd["month_dist"],
        "count_basis": "tickets（一单一行；非上榜股数）",
    }
    if not dryrun:
        os.makedirs(REPORTS, exist_ok=True)
        with open(CALIB_F, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print("  已写 reports/sector_flow_calib.json")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="yyyymmdd，缺省最近交易日")
    ap.add_argument("--replay", type=int, metavar="N", help="近 N 交易日回放定标")
    ap.add_argument("--nmin", type=int, default=N_MIN)
    ap.add_argument("--mmin", type=int, default=M_MIN)
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    if a.replay:
        replay(a.replay, dryrun=a.dryrun)
        return
    d = a.date or last_trade_date()
    if not d:
        raise SystemExit("图内无 topgap 事件，先跑夜链 dt_gap")
    print("[sector_flow] %s（n_min=%d m_min=%d）" % (d, a.nmin, a.mmin))
    rows = load_day(d)
    tickets = apply_rules(rows, a.nmin, a.mmin)
    for t in tickets:
        t["cross"] = cross_check(d, [s["tc"] for s in t["stocks"]])
    doc = save(tickets, d, dryrun=a.dryrun)
    print("  当日上榜 %d 票 → 工单 %d 单" % (len(rows), doc["n_tickets"]))
    for t in doc["tickets"]:
        cx = t.get("cross") or {}
        print(
            "    [%s] %s · %d 股 · 游资%s同向 %d · 穿透 %d · "
            "连续 %d · 跨票席位 %d · 技术共振 %s · 差额 %.2f亿"
            % (
                "触发",
                t["industry"],
                t["n_listed"],
                t["yz_dir"],
                t["n_yz_same"],
                t["n_pen"],
                cx.get("cont_n") or 0,
                len(cx.get("seat_x") or []),
                cx.get("tech_breakdown") or {},
                t["gap_sum"],
            )
        )
        for sx in (cx.get("seat_x") or [])[:3]:
            print(
                "      跨票席位：%s ×%d（%s）"
                % (sx["seat"], sx["n"], "、".join(sx["stocks"]))
            )
    if not a.dryrun:
        notify(doc)
        print("  已写 reports/sector_flow_latest.json")


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "sector_flow", main)
