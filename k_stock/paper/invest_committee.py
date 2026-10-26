# -*- coding: utf-8 -*-
"""G3 投研会审三分析师席 + 确定性聚合引擎二次筛选。

建单：WO-QT-20261020-083（三席 + 聚合 + v2 账本）。
命名修订单：WO-QT-20261021-091——聚合席去人格化（归属前提经 2026-10-27 判例更正）。

  原实现把人名「鲁肃」挂在聚合席上（organizer/host/纪要行）。12:56 老田
  裁决：聚合席去人格化——聚合本就是加权纯函数（Σ席分×席权），不是一个人，
  也不需要 persona。13:01 复核名册更正归属前提：鲁肃=D1 量化交易部最终
  决策官，通信与客户服务部主官=阚泽（agent_id LUSU1 保留）。**聚合席保持
  无名是设计使然，不随岗位归属变化**；聚合逻辑与数值口径零变化——只改
  命名与表述；席权/精选线/一票否决全部原值保留。定名判例见
  docs/audit/2026-10-27_鲁肃与阚泽定名判例.md。

★影子期红线（WO-QT-20261020-083）★
本模块是投研意见产出与留痕层：只读股票池 v1、只落本工单新立的 v2 会审
账本（append-only JSONL）。**不 import 不调用任何执行链**——
engine.exec_run / signal_consumer / signal_pool / combo_factory / 下单
通道一律零接触；combo persona_neutral（G4 第三例）同期只注册留痕，
combo 执行解锁（影子期 D14=2026-11-04 评审 + 老田签准）前绝不进执行/交易链。

──────────────────────────────────────────────────────────────────────
领域语义（deliverables/2026-10-19_量化板块三国命名体系 v1.2 §二）：
  投顾团队组织多种分析师对股票池 v1 分析判断 → 二次筛选
  → 股票池 v2（精选）+ 投研会结论进 minutes。每日选股清单（todays_picks）
  是输入。策略 Agent 向郭嘉汇报，不归本模块管。
  （WO-091：会审组织者不挂人名，聚合由无名确定性引擎执行；鲁肃岗位
  归属以 D1 量化交易部最终决策官为准，见 docs/audit/2026-10-27_鲁肃与
  阚泽定名判例.md。）

实现裁定（不另造框架）：
  - 三席分析师按命名体系人格化（persona 只影响权重/风格参数配置），
    判定输入 = 股票池 v1 单票证据（sources 标签/tier/n_hit/风控影子
    标记），判定逻辑 = 确定性规则引擎（纯标准库，不接 LLM 不联网）——
    同输入必同输出，聚合结论可手工复算；
  - 三席人格对齐 portal/persona_registry.json 风格（纯人格化层登记，
    数值属性不构成策略评价）；席名取未被数字军团占用的三国谋士：
      技术面席 刘晔·子扬（ Chanlun/qpzm 信号证据 ），
      价值面席 满宠·伯宁（ 九维出分/tier 证据 ），
      风控席   蒋济·子通（ weak/panic/q_toxic 证据，风控席持一票谨慎权 ）；
  - v2 账本落点：k_stock/data/invest_committee/pool_v2_sessions.jsonl
    （本工单新立独立文件，append-only，一行一会审场次，不 update 不
    delete——与 signal_pool/k_stock paper 拒单留痕同款铁律）；每行含
    三席意见 + 聚合结论 + minutes 纪要字段（会议结论行/边界声明），
    供 wall_adapter committee_line 同款下游按需消费（本单不接展示层）；
  - session_id 轮次化：poolv2.{scan_run_date}.r{n}，n=账本内同
    scan_run_date 已有场次+1（与 signal_pool 轮次口径同款，确定性幂等）；
  - 失败语义 closed on failure：v1 池缺失/坏 JSON/缺 stocks → 显式抛
    InvestCommitteeError，禁静默降级。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

WO = "WO-QT-20261020-083"
NAMING_WO = "WO-QT-20261021-091"       # 聚合席去人格化修订单

LEDGER_SCHEMA_VERSION = 1
POOL_ID = "todays_picks"

# v2 账本默认落点（新立独立文件；生产未写——测试注入 tmp_path）
DEFAULT_LEDGER = os.path.join("k_stock", "data", "invest_committee",
                              "pool_v2_sessions.jsonl")

# 评级阈值（三席共用口径，persona 可覆盖）
RATING_BUY = 0.65
RATING_AVOID = 0.40
# 聚合引擎：v2 精选线（稳健联判）
V2_FLOOR = 0.62

# 会审组织者（WO-091 起为无名确定性引擎：聚合是加权纯函数，不是一个人；
# 不带 zi/字号等人格字段。席权配置即会审章程，与人格无关）
ORGANIZER = {"id": "anonymous_engine", "name": "确定性聚合引擎",
             "kind": "deterministic_engine", "org": "投顾团队"}
SEAT_WEIGHTS = {"seat_jishu": 0.40, "seat_jiazhi": 0.30, "seat_fengkong": 0.30}
RISK_SEAT_ID = "seat_fengkong"

# 三分析师席配置（persona 各异：阈值/风格参数即人格；纯人格化层）
SEATS = [
    {"seat_id": "seat_jishu", "name": "刘晔", "zi": "子扬",
     "title": "技术面席", "org": "投顾团队",
     "style": {"evidence": "chanlun 买卖点 + qpzm 环境闸门信号",
               "buy_base": 0.50, "buy_step": 0.25, "sell_step": -0.25,
               "qpzm_buy": 0.15, "qpzm_avoid": -0.25,
               "hit_bonus": 0.05, "hit_cap": 3},
     "thresholds": {"buy": RATING_BUY, "avoid": RATING_AVOID}},
    {"seat_id": "seat_jiazhi", "name": "满宠", "zi": "伯宁",
     "title": "价值面席", "org": "投顾团队",
     "style": {"evidence": "九维出分 + 池层 Tier + 多源共振",
               "buy_base": 0.50, "scored": 0.25, "shadow_unscored": -0.10,
               "tier2": 0.15, "multihit": 0.10},
     "thresholds": {"buy": RATING_BUY, "avoid": RATING_AVOID}},
    {"seat_id": "seat_fengkong", "name": "蒋济", "zi": "子通",
     "title": "风控席", "org": "投顾团队",
     "style": {"evidence": "弱势标签 + 恐慌/毒性影子标记 + 异动",
               "buy_base": 0.60, "weak": -0.20, "panic_buy": -0.15,
               "q_toxic": -0.30, "big_move": -0.10, "big_move_pct": 5.0,
               "calm_bonus": 0.05},
     "thresholds": {"buy": RATING_BUY, "avoid": RATING_AVOID}},
]

_SEAT_BY_ID = {s["seat_id"]: s for s in SEATS}


class InvestCommitteeError(Exception):
    """投研会审契约违例（closed on failure，禁静默降级）。"""


# ---------- 股票池 v1 读入（只读，无源不伪造） ----------

def load_picks(path):
    """读股票池 v1（todays_picks.json）。缺文件/坏 JSON/缺 stocks → 抛错。"""
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except FileNotFoundError as e:
        raise InvestCommitteeError(f"股票池 v1 不存在: {path}") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise InvestCommitteeError(f"股票池 v1 不是合法 UTF-8 JSON: {path} ({e})") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("stocks"), list):
        raise InvestCommitteeError(f"股票池 v1 缺 stocks 数组: {path}")
    return raw


# ---------- 确定性证据提取（v1 单票证据 → 布尔事实） ----------

def _facts(stock):
    srcs = stock.get("sources") or []
    return {
        "chanlun_buys": sum(1 for s in srcs
                            if s.get("s") == "chanlun" and str(s.get("tag", "")).endswith("买")),
        "chanlun_sells": sum(1 for s in srcs
                             if s.get("s") == "chanlun" and str(s.get("tag", "")).endswith("卖")),
        "qpzm_buy": any(s.get("s") == "qpzm" and s.get("tag") != "回避" for s in srcs),
        "qpzm_avoid": any(s.get("s") == "qpzm" and s.get("tag") == "回避" for s in srcs),
        "ninedim_scored": any(s.get("s") == "ninedim" and s.get("tag") == "九维出分"
                              for s in srcs),
        "ninedim_shadow": any(s.get("s") == "ninedim" and "影子" in str(s.get("tag", ""))
                              for s in srcs),
        "weak": any(s.get("s") == "weak" for s in srcs),
        "tier2": stock.get("tier") == "T2",
        "n_hit": int(stock.get("n_hit") or 1),
        "panic_buy": bool(stock.get("panic_buy")),
        "q_toxic": bool(stock.get("q_toxic")),
        "env_diff": bool(stock.get("env_diff")),
        "pct_chg": float(stock.get("pct_chg") or 0.0),
    }


# ---------- 三分析师席：确定性评级规则引擎 ----------

def _clamp01(x):
    return round(max(0.0, min(1.0, x)), 4)


def rate_stock(seat, stock):
    """单席对单票出具评级意见（确定性规则引擎，persona=seat 配置）。

    返回 {seat_id, name, zi, title, score, rating, reasons}。
    score ∈ [0,1]；rating：>=buy 阈 BUY / <=avoid 阈 AVOID / 其余 HOLD。
    """
    st = seat["style"]
    f = _facts(stock)
    score = float(st["buy_base"])
    reasons = []

    if seat["seat_id"] == "seat_jishu":
        if f["chanlun_buys"]:
            score += st["buy_step"] * f["chanlun_buys"]
            reasons.append(f"缠论买点×{f['chanlun_buys']}（{st['evidence']}）")
        if f["chanlun_sells"]:
            score += st["sell_step"] * f["chanlun_sells"]
            reasons.append(f"缠论卖点×{f['chanlun_sells']}")
        if f["qpzm_buy"]:
            score += st["qpzm_buy"]
            reasons.append("青萍之末买入向信号")
        if f["qpzm_avoid"]:
            score += st["qpzm_avoid"]
            reasons.append("青萍之末环境闸门回避")
        hit = min(f["n_hit"], st["hit_cap"]) - 1
        if hit > 0:
            score += st["hit_bonus"] * hit
            reasons.append(f"多源命中 n_hit={f['n_hit']}")
    elif seat["seat_id"] == "seat_jiazhi":
        if f["ninedim_scored"]:
            score += st["scored"]
            reasons.append("九维出分（主模型确认）")
        elif f["ninedim_shadow"]:
            score += st["shadow_unscored"]
            reasons.append("九维影子未出分（候选口径）")
        if f["tier2"]:
            score += st["tier2"]
            reasons.append("池层 T2 高价值")
        if f["n_hit"] >= 2:
            score += st["multihit"]
            reasons.append(f"多策略共振 n_hit={f['n_hit']}")
    elif seat["seat_id"] == "seat_fengkong":
        if f["weak"]:
            score += st["weak"]
            reasons.append("弱势标签（深度垫底/强失速）")
        if f["panic_buy"]:
            score += st["panic_buy"]
            reasons.append("恐慌期检出标记")
        if f["q_toxic"]:
            score += st["q_toxic"]
            reasons.append("毒性信号影子标记")
        if abs(f["pct_chg"]) > st["big_move_pct"]:
            score += st["big_move"]
            reasons.append(f"单日异动 pct_chg={f['pct_chg']:.2f}%")
        if not f["env_diff"]:
            score += st["calm_bonus"]
            reasons.append("无环境差标记")
    else:  # pragma: no cover — 席位配置受控，未知席显式拒绝
        raise InvestCommitteeError(f"未注册分析师席: {seat['seat_id']}")

    score = _clamp01(score)
    th = seat["thresholds"]
    rating = "BUY" if score >= th["buy"] else (
        "AVOID" if score <= th["avoid"] else "HOLD")
    if not reasons:
        reasons.append("无强证据，按席基准分中性处理")
    return {"seat_id": seat["seat_id"], "name": seat["name"], "zi": seat["zi"],
            "title": seat["title"], "score": score, "rating": rating,
            "reasons": reasons}


# ---------- 确定性聚合引擎（无名纯函数，可复算） ----------

def aggregate(opinions, seat_weights=None, v2_floor=V2_FLOOR):
    """三席意见加权聚合：score=Σ(席分×席权)；精选=过 v2 线且风控席非 AVOID。

    纯函数：同 opinions 输入必同输出（验收②可复算口径）。
    """
    w = seat_weights or SEAT_WEIGHTS
    score = round(sum(o["score"] * w[o["seat_id"]] for o in opinions), 4)
    risk = [o for o in opinions if o["seat_id"] == RISK_SEAT_ID]
    risk_ok = bool(risk) and risk[0]["rating"] != "AVOID"
    return {"score": score,
            "rating": "SELECT" if (score >= v2_floor and risk_ok) else "PASS",
            "selected": bool(score >= v2_floor and risk_ok)}


# ---------- 会审场次构造与落账 ----------

def run_session(picks, now=None, ledger_path=None):
    """对股票池 v1 跑一场投研会审（三席意见→确定性聚合→v2 精选+minutes）。

    picks: 已 load 的 v1 dict（或路径 str，内部 load）。
    now: ISO 时刻文本（缺省取当前时刻；测试注入固定值保确定性）。
    ledger_path: 仅用于轮次计数读取既有场次（不写盘；写盘走 append_session）。
    """
    if isinstance(picks, str):
        picks = load_picks(picks)
    now = now or datetime.now().isoformat(timespec="seconds")
    scan_run_date = picks.get("scan_run_date") or ""
    if not scan_run_date:
        raise InvestCommitteeError("股票池 v1 缺 scan_run_date（无源不伪造）")

    reviews = []
    for stock in picks["stocks"]:
        ops = [rate_stock(seat, stock) for seat in SEATS]
        agg = aggregate(ops)
        reviews.append({"code": stock.get("code"), "name": stock.get("name"),
                        "tier": stock.get("tier"),
                        "opinions": ops, "aggregate": agg})

    selected = [r["code"] for r in reviews if r["aggregate"]["selected"]]
    n_round = _count_rounds(scan_run_date, ledger_path)
    mm_dd = scan_run_date[5:10] if len(scan_run_date) >= 10 else scan_run_date
    return {
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "wo": WO,
        "naming_revision": NAMING_WO,      # WO-091 聚合席去人格化（字段只增不改）
        "session_id": f"poolv2.{scan_run_date}.r{n_round}",
        "kind": "invest_committee_v2",
        "ts": now,
        "picks_source": POOL_ID,
        "scan_run_date": scan_run_date,
        "generated_at_source": picks.get("generated_at", ""),
        "organizer": dict(ORGANIZER),
        "seats": [dict(s) for s in SEATS],
        "seat_weights": dict(SEAT_WEIGHTS),
        "v2_floor": V2_FLOOR,
        "reviews": reviews,
        "summary": {"n_reviewed": len(reviews), "n_selected": len(selected),
                    "codes_selected": selected},
        "minutes": {
            "kind": "invest_committee",
            "title": f"投研会审 {mm_dd}",
            "host": "确定性聚合引擎",
            "conclusion": (f"{mm_dd} 投研会审｜确定性聚合引擎 汇总三席意见｜"
                           f"审 {len(reviews)} 只精选 {len(selected)} 只"
                           f"（v2 线 {V2_FLOOR}，风控席一票谨慎权）"),
            "boundary": ("本纪要仅用于模拟盘决策辅助，不涉及真实资金；"
                         "影子期内本账不接 combo 执行链"
                         "（D14=2026-11-04 解锁评审须老田签准）"),
        },
    }


def _count_rounds(scan_run_date, ledger_path):
    """同 scan_run_date 已有场次数 + 1（轮次口径，确定性）。"""
    return len([s for s in read_sessions(ledger_path=ledger_path)
                if s.get("scan_run_date") == scan_run_date]) + 1


def append_session(session, ledger_path=None):
    """会审场次 append-only 落账（一行一场，不 update 不 delete）。"""
    path = ledger_path or DEFAULT_LEDGER
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(session, ensure_ascii=False) + "\n")
    return session


def read_sessions(ledger_path=None):
    """读 v2 会审账（只读）。缺文件=空账（合法初态）；坏行跳过留痕容忍。"""
    path = ledger_path or DEFAULT_LEDGER
    if not os.path.isfile(path):
        return []
    out = []
    for line in open(path, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 尾行截断容忍（账本域常见态），不炸
    return out
