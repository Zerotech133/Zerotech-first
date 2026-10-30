# -*- coding: utf-8 -*-
"""缠论专属学习环 L1-L4（K-ORG-190，设计 K-ORG-185）。

给缠论引擎装「只看不动手的脑子」：影子买卖点 → 记忆 → T+N 对账 →
结构归因 → 灰度权重草案 + 署名理论笔记。设计定稿：
docs/plans/2026-10-18_缠论专属学习环设计_K-ORG-185.md（K-ORG-187/188 修订）。

四阶段（CLI 子命令）：
  l1 观察   —— 重放 daily_ledger 影子信号流（seen-ever 逐日复刻），
               灰度 enrich + 结构指纹 + regime → ledger.jsonl（幂等 sig_id）
               + agent/ST1/observation 记忆（MemoryStore RBAC）。
  l2 对账   —— 每条 L1 行按 T+5/10/20 补后验（hfq 收盘→收盘，卖点反向
               口径）→ verify.jsonl（perf_key 范式：同键同值零副作用 /
               同键异值拒绝 / 坏行 fail-open）。
  l3 归因   —— 买卖点类型 × regime × conf 档 × 结构指纹 维度矩阵聚合
               → attribution.jsonl（显式阈值判读：n<30 只观察；n≥30 且
               avg<0 且 p10≤-10% → 降置信候选；n≥30 且 win 超全域基线
               +10pp → 升置信候选；verdict 必带 evidence 与 n）。
  l4 修正   —— 出口 A：降/升置信候选 → drafts/*.json 灰度权重草案
               （单次调幅 ≤±0.05，shadow_until ≥20 交易日，**本模块不
               提供任何直改生产参数的函数**）；出口 B：左尾/连败/归因
               失效 → agent/ST1/thesis 与 dept/invest/thesis 记忆 + 周记。

影子铁律：全链只读行情、只写记忆与草案，不产生任何交易指令；
草案 draft ≠ adopt，采纳永远人工凭据。

口径定案（设计未明处的施工裁量，均已注释）：
  · regime 采信 daily_ledger JSON days[].regime（池指数 MA20/MA60 D-1
    判定，K-STOCK-269 同源，不自算不核对——避免双口径漂移）。
  · entry_px = 信号锚点日（dt）当日 hfq 收盘（K-STOCK-263a pct_chg
    因子链，尾追加安全；除权回填致 ret 漂移会被同键异值拒绝亮牌）。
  · level 现役只有笔级别（引擎 bis 产信号），统一记 "bi"；段级别留
    远期多市场扩展（设计 §7）。
  · 幂等比对域：ledger 行比全部语义字段（排除 declared_at，保证回放
    逐字节一致）；verify 行循 perf_ledger 判例只比 ret_pct。
  · 归因 horizon 三档各出行（行内 horizon 字段），L4 草案触发取 T+10
    主口径（5 太噪、20 样本摊薄）。
  · conf 分档：low<0.55 / mid<0.65 / high≥0.65 / na（gray conf 实际
    离散值域 {0.425,0.50,0.51,0.553,0.60,0.638,0.65,0.75}）。
  · npv 二值化：le2（≤2 枢）/ 3p（≥3 枢，multi_hub 衰减生效区）/ na。
  · sig_id = sha1(code|type|dt)[:16]（设计 §3）；verify_key =
    sha1(sig_id|horizon)[:16]；attr_key = sha1(cell|horizon|统计指纹)[:16]。

用法：
  python chanlun_learn.py l1 [--d0 20251013 --d1 20261012]
  python chanlun_learn.py l2
  python chanlun_learn.py l3
  python chanlun_learn.py l4 drafts|thesis|weekly
  python chanlun_learn.py all          # L1→L2→L3→L4(drafts+thesis) 一键

测试注入点：_analyze/_enrich/_fingerprint/_load_close 可替换（tmp 根
全链单测不依赖真实缠论引擎与 parquet）。
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BASE = os.path.dirname(HERE)  # 仓根（C:/项目/老田的AI助手）
DATA_DIR = os.path.join(HERE, "data", "ashare")  # hfq parquet 源
LEDGER_JSON = os.path.join(
    HERE, "data", "daily_ledger", "ledger_20251013_20261012.json"
)
LEARN_DIR = os.path.join(HERE, "data", "chanlun_learn")
MEMORY_DIR = os.path.join(BASE, "agent_platform", "data", "memory")
ORG_JSON = os.path.join(BASE, "agent_platform", "registry", "org.json")

AGENT_ID = "ST1"
WARMUP = 250  # analyze 窗口（chanlun_daily_ledger 同款）
MIN_SEG = WARMUP // 2  # run_day 门槛：len(seg) < 125 跳过
BUY_TYPES = ("1买", "2买", "3买")
SELL_TYPES = ("1卖", "2卖", "3卖")
HORIZONS = (5, 10, 20)
MAIN_HORIZON = 10  # 归因/草案主口径（施工裁量，见 docstring）
N_MIN = 30  # 小样本不凭据（设计 §2 红线 4）
P10_FLOOR = -10.0  # 降置信候选：p10 ≤ -10%
WIN_EDGE_PP = 10.0  # 升置信候选：win 超全域基线 +10pp
TAIL_FLAG_N_MIN = 20  # E-01 S2 左尾观察标注 n 门槛（<N_MIN：先可见后凭据）
TAIL_RET_FLOOR = -10.0  # E-01 S1 左尾哨兵：T+20 深亏案例门槛
DRAFT_MAX_STEP = 0.05  # 单次调幅上限（设计 §2 红线 4）
SHADOW_NATURAL_DAYS = 28  # 影子期 ≥20 交易日 ≈ 28 自然日（设计 §3 L4A）
CONF_BANDS = ((0.65, "high"), (0.55, "mid"), (0.0, "low"))  # 降序取首命中

# L3 维度组合（固定顺序；cell = "|".join(f"{k}={v}")）
DIM_1 = ("type", "regime", "conf_band", "bis6", "amp", "pv", "npv")
DIM_2 = (
    ("type", "regime"),
    ("type", "conf_band"),
    ("type", "npv"),
    ("type", "amp"),
    ("type", "pv"),
    ("type", "bis6"),
    ("regime", "npv"),
    ("regime", "conf_band"),
)
DIM_3 = (("type", "regime", "npv"), ("type", "regime", "conf_band"))

# L4 可学习权重位映射（chanlun_gray._conf 三参数之一；其余 cell 无参数
# 位，verdict 留档人工判读——不硬造映射）
PARAM_MAP = {
    "npv=3p": {"param": "multi_hub_decay", "from": 0.85, "lo": 0.70, "hi": 1.00}
}


# ── 注入点（生产默认真实现；测试替换） ──────────────────────
def _analyze(bars):
    from chanlun_signal import analyze

    return analyze(bars)


def _enrich(bars, st):
    from chanlun_gray import enrich

    return enrich(bars, st)


def _fingerprint(res, sig):
    from chanlun_tgraph import _fp

    return _fp(res, sig)


def _load_bars(code, data_dir=DATA_DIR):
    """hfq 完整 bars（o/h/l/c）+ 收盘映射（K-STOCK-263a pct_chg 因子链，
    chanlun_daily_ledger 同款口径；首日 r=0，adj 以首日为基）。

    L1 重放必须用真 o/h/l（与 daily_ledger 逐位同口径）——分型/笔的
    K 线包含关系处理对 high/low 敏感，任何近似都会使重放信号结构与
    账本 JSON 错位（fallback 泛滥 → 幂等不收敛，已实证）。缺文件返回
    (None, None)。"""
    import pandas as pd

    f = os.path.join(data_dir, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        return None, None
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    bars = [
        {
            "dt": int(d),
            "o": float(o * k),
            "h": float(h * k),
            "l": float(lo * k),
            "c": float(c * k),
        }
        for d, o, h, lo, c, k in zip(
            b["trade_date"], b["open"], b["high"], b["low"], b["close"], kfac
        )
    ]
    closes = {int(d): v["c"] for d, v in ((x["dt"], x) for x in bars)}
    return bars, closes


def _load_close(code, data_dir=DATA_DIR):
    """L2 后验用收盘映射（_load_bars 的轻量封装）。"""
    return _load_bars(code, data_dir)[1]


# ── 账本 IO（perf_ledger 范式） ─────────────────────────────
def _sha1(s):
    return hashlib.sha1(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _sig_id(code, sig_type, dt):
    return _sha1("%s|%s|%s" % (code, sig_type, dt))


def _read_jsonl(path):
    """坏行 fail-open 跳过（不拖账）。"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


def _append_line(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def ledger_path(learn_dir=LEARN_DIR):
    return os.path.join(learn_dir, "ledger.jsonl")


def verify_path(learn_dir=LEARN_DIR):
    return os.path.join(learn_dir, "verify.jsonl")


def attribution_path(learn_dir=LEARN_DIR):
    return os.path.join(learn_dir, "attribution.jsonl")


def drafts_dir(learn_dir=LEARN_DIR):
    return os.path.join(learn_dir, "drafts")


# ── 记忆层（MemoryStore RBAC；缺 agent_platform 时降级 finding） ──
def memory_store(memory_dir=MEMORY_DIR, org_path=ORG_JSON):
    """从 org.json 读 ST1 memory_grants 构造 enforce=True 的 MemoryStore。"""
    ep = os.path.join(BASE, "agent_platform", "engine")
    if ep not in sys.path:
        sys.path.insert(0, ep)
    from memory import MemoryStore

    grants = {}
    try:
        org = json.load(open(org_path, encoding="utf-8"))
        for dep in org.get("departments", []):
            for p in dep.get("agent_profiles", []):
                if p.get("agent_id") == AGENT_ID:
                    grants[AGENT_ID] = p.get("memory_grants") or {}
    except Exception:
        grants = {}
    return MemoryStore(memory_dir, grants_by_agent=grants, enforce=True)


def _memory_write(ms, gns, payload, fp, findings):
    """append-only + 指纹判重（回放幂等）：同 fingerprint 已存在则跳过。"""
    if ms is None:
        findings.append("memory 降级：MemoryStore 不可达，跳过 %s" % gns)
        return False
    try:
        for rec in ms.read(AGENT_ID, gns, limit=10000):
            if (rec.get("payload") or {}).get("fingerprint") == fp:
                return False
        ms.write(AGENT_ID, gns, dict(payload, fingerprint=fp))
        return True
    except Exception as e:  # 越权等异常：留 finding 不拖账
        findings.append("memory 写入失败 %s: %s" % (gns, e))
        return False


# ── L1 观察 ─────────────────────────────────────────────────
def _conf_parts(sig):
    """conf 三分解（gray._conf 同判据）：beichi/vv 布尔，multi_hub=n_prior≥3。"""
    n_prior = sig.get("n_prior")
    return {
        "beichi": any(sig.get(x) is True for x in ("mbei", "div_area", "div_dif")),
        "vv": sig.get("vv") is True,
        "multi_hub": bool(n_prior is not None and n_prior >= 3),
    }


def conf_band(conf):
    if conf is None:
        return "na"
    for th, name in CONF_BANDS:
        if conf >= th:
            return name
    return "low"


def npv_band(npv):
    if npv is None:
        return "na"
    return "3p" if npv >= 3 else "le2"


def _found_stamp(day):
    """确定性 declared_at（回放逐字节一致）：记账日 17:10 影子窗口。"""
    return "%sT17:10:00+08:00" % day


def l1_run(
    ledger_json=LEDGER_JSON,
    d0=None,
    d1=None,
    learn_dir=LEARN_DIR,
    data_dir=DATA_DIR,
    ms=None,
    limit_stocks=0,
    findings=None,
):
    """重放 daily_ledger seen-ever 信号流 → ledger.jsonl + ST1 observation。

    逐日复刻 chanlun_daily_ledger.run_day 口径（tail(WARMUP)、MIN_SEG
    门槛、seen-ever 按 (code,type) 累积），JSON 大类为事实源核对重放。
    返回 {recorded, replayed, findings, n_signals}。
    """
    findings = findings if findings is not None else []
    L = json.load(open(ledger_json, encoding="utf-8"))
    days = L.get("days") or {}
    all_days = sorted(days)
    if d0:
        all_days = [d for d in all_days if d >= str(d0)]
    if d1:
        all_days = [d for d in all_days if d <= str(d1)]
    # 事实源 fresh 事件：(day, code) → 大类集合；regime 采信 JSON
    ev = {}
    regime_of = {}
    for day in all_days:
        regime_of[day] = (days[day] or {}).get("regime")
        for a in (days[day] or {}).get("actions") or []:
            t = a.get("type")
            if t in ("sig_buy", "sig_sell"):
                ev.setdefault((day, a.get("stock")), set()).add(
                    "buy" if t == "sig_buy" else "sell"
                )
    codes = sorted({c for (_, c) in ev})
    if limit_stocks:
        codes = codes[:limit_stocks]
    by_code_events = {}
    for (day, code), cats in ev.items():
        if code in codes:
            by_code_events.setdefault(code, {}).setdefault(day, set()).update(cats)

    lp = ledger_path(learn_dir)
    index = {r.get("sig_id"): r for r in _read_jsonl(lp) if r.get("sig_id")}
    # 已记账 (code, 大类) → 信号 dt 集合（从 index 重建）：fallback 挑选
    # 只看此集合、与 index 无关 → 跨 run 选择收敛一致（回放幂等）
    taken = {}
    for r in index.values():
        cat = "sell" if str(r.get("type", "")).endswith("卖") else "buy"
        taken.setdefault((r.get("code"), cat), set()).add(r.get("dt"))
    # 事件位固化索引：(found_day, code, 大类) → 已记账行。fallback 的
    # taken 排除会随记账漂移（第二遍排除首遍挑中的 dt 后顶上另一个信号
    # → 选择漂移不幂等，已实证），故事件位首见记账即固化信号，后续 run
    # 直接复核固化信号不再重挑。
    pos = {}
    for r in index.values():
        cat = "sell" if str(r.get("type", "")).endswith("卖") else "buy"
        pos.setdefault((str(r.get("found_day")), r.get("code"), cat), r)
    recorded = replayed = n_signals = 0

    for code in codes:
        bars_all, closes = _load_bars(code, data_dir)
        if not bars_all:
            findings.append("L1 缺行情 %s，跳过（顺延）" % code)
            continue
        tdays = sorted(closes)
        win_days = [d for d in tdays if all_days[0] <= str(d) <= all_days[-1]]
        if not win_days:
            continue
        # 重放 seen-ever：init = 窗口前一交易日（daily_ledger 同款）
        pre = [d for d in tdays if str(d) < all_days[0]]
        seen = set()
        if pre:
            _st0, s0 = _analyze_sig(_bars_upto(bars_all, pre[-1]))
            seen = {(s.get("type"), s.get("dt")) for s in s0}
        day_ev = by_code_events.get(code, {})
        for day in win_days:
            ds = str(day)
            bars_day = _bars_upto(bars_all, day)
            if len(bars_day) < MIN_SEG:
                continue
            st, sigs = _analyze_sig(bars_day[-WARMUP:])
            fresh = [s for s in sigs if (s.get("type"), s.get("dt")) not in seen]
            seen |= {(s.get("type"), s.get("dt")) for s in sigs}
            cats_json = day_ev.get(ds)
            if not cats_json:
                continue  # 当日无 JSON 记账 → 只推进 seen
            cats_replay = {
                ("buy" if s.get("type") in BUY_TYPES else "sell") for s in fresh
            }
            for cat in sorted(cats_json):
                want = BUY_TYPES if cat == "buy" else SELL_TYPES
                pinned = pos.get((ds, code, cat))
                if pinned is not None:
                    # 事件位已固化：复核固化信号而非重挑（重挑随 taken
                    # 漂移不幂等，已实证）。固化信号在重放中缺失属口径
                    # 漂移 → finding 亮牌不记账。
                    m = [
                        s
                        for s in sigs
                        if s.get("type") == pinned.get("type")
                        and s.get("dt") == pinned.get("dt")
                    ]
                    if not m:
                        findings.append(
                            "L1 %s %s 固化信号重放缺失（口径漂移亮牌）" % (ds, code)
                        )
                        continue
                    row = _l1_row(
                        code, m[0], st, day, regime_of.get(ds), closes, findings
                    )
                    if row is None:
                        continue
                    n_signals += 1
                    old = index.get(row["sig_id"])
                    if old is not None:
                        if _sig_core(old) != _sig_core(row):
                            raise ValueError(
                                "ledger 冲突：sig_id=%s 已存在但内容不一致"
                                "（同键异值拒绝；口径漂移须人工判读）" % row["sig_id"]
                            )
                        replayed += 1
                    else:
                        index[row["sig_id"]] = row
                        _append_line(lp, row)
                        recorded += 1
                        _l1_memory(ms, row, findings)
                    continue
                cand = [s for s in fresh if s.get("type") in want]
                if cat not in cats_replay:
                    # JSON 记账但重放未 fresh（迟到/口径漂移）→ fallback
                    # 取当日可见同类中 dt 最大且未被本大类记账者 + finding
                    # （taken 按 (code,大类) 收敛，跨 run 选择一致 → 幂等）
                    got = taken.get((code, cat), set())
                    cand = [
                        s
                        for s in sigs
                        if s.get("type") in want and s.get("dt") not in got
                    ]
                    findings.append(
                        "L1 %s %s 重放未 fresh（fallback 取最近未记账同类）"
                        % (ds, code)
                    )
                extra = cats_replay - cats_json
                if extra:
                    findings.append(
                        "L1 %s %s 重放多出大类 %s（以账本 JSON 为准丢弃）"
                        % (ds, code, sorted(extra))
                    )
                if not cand:
                    findings.append("L1 %s %s 无未记账同类信号可记账" % (ds, code))
                    continue
                sig = max(cand, key=lambda s: s.get("dt") or 0)
                taken.setdefault((code, cat), set()).add(sig.get("dt"))
                row = _l1_row(code, sig, st, day, regime_of.get(ds), closes, findings)
                if row is None:
                    continue
                pos.setdefault((ds, code, cat), row)  # 事件位首见固化
                n_signals += 1
                old = index.get(row["sig_id"])
                if old is not None:
                    if _sig_core(old) != _sig_core(row):
                        raise ValueError(
                            "ledger 冲突：sig_id=%s 已存在但内容不一致"
                            "（同键异值拒绝；口径漂移须人工判读）" % row["sig_id"]
                        )
                    replayed += 1
                    continue
                index[row["sig_id"]] = row
                _append_line(lp, row)
                recorded += 1
                _l1_memory(ms, row, findings)
    return {
        "recorded": recorded,
        "replayed": replayed,
        "findings": findings,
        "n_signals": n_signals,
    }


def _sig_core(row):
    """幂等比对域：sig_id=sha1(code|type|dt) 是信号身份，found_day/
    declared_at 是记账时戳（fallback 场景同信号可在多个记账日出现），
    均不参与同键异值判定——首见固化，重放零副作用。"""
    return {k: v for k, v in row.items() if k not in ("declared_at", "found_day")}


def _bars_upto(bars, upto_day):
    """bars 截至某交易日（含；调用方自行取 tail(WARMUP)）。"""
    return [b for b in bars if b["dt"] <= int(upto_day)]


def _analyze_sig(bars):
    """analyze + gray enrich → (st, signals)（注入点封装）。

    enrich 失败不阻断记账（灰度失败不拖账，gray 同款纪律）。"""
    st = _analyze(bars)
    if not st:
        return {}, []
    try:
        _enrich(bars, st)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/chanlun_learn.py", exc)
    return st, st.get("signals") or []


def _l1_row(code, sig, st, found_day, regime, closes, findings):
    """信号 dict → ledger 行（设计 §3 字段表）。"""
    try:
        dt = int(sig.get("dt"))
        sig_type = sig.get("type")
        fp = _fingerprint(st or {}, sig) or {}
    except Exception:
        fp = {}
    conf = sig.get("conf")
    row = {
        "sig_id": _sig_id(code, sig_type, dt),
        "dt": dt,
        "found_day": str(found_day),
        "code": code,
        "type": sig_type,
        "level": "bi",
        "regime": regime,
        "fingerprint": {k: fp.get(k) for k in ("bis6", "amp", "pv", "npv")},
        "fp": fp.get("fp"),
        "conf": conf,
        "conf_parts": _conf_parts(sig),
        "n_prior": sig.get("n_prior"),
        "entry_px": closes.get(dt),
        "declared_at": _found_stamp(found_day),
    }
    if row["entry_px"] is None:
        findings.append("L1 %s dt=%s 缺收盘价（entry_px=None）" % (code, dt))
    return row


def _l1_memory(ms, row, findings):
    """agent/ST1/observation 自述版（一句观察语 + 全字段溯源）。"""
    if ms is None:
        return
    cp = row["conf_parts"]
    marks = "%s/%s/%s" % (
        "背驰✓" if cp["beichi"] else "背驰✗",
        "共振✓" if cp["vv"] else "共振✗",
        "多枢" if cp["multi_hub"] else "少枢",
    )
    note = "%s %s（%s）：regime=%s，%s，conf=%s——影子记账，T+5/10/20 待对账" % (
        row["type"],
        row["code"],
        row["level"],
        row["regime"],
        marks,
        row["conf"] if row["conf"] is not None else "不可判",
    )
    _memory_write(
        ms,
        "agent/%s/observation" % AGENT_ID,
        {
            "kind": "shadow_signal",
            "sig_id": row["sig_id"],
            "code": row["code"],
            "type": row["type"],
            "dt": row["dt"],
            "regime": row["regime"],
            "conf": row["conf"],
            "conf_parts": row["conf_parts"],
            "fingerprint": row["fingerprint"],
            "note": note,
            "declared_at": row["declared_at"],
        },
        "l1|%s" % row["sig_id"],
        findings,
    )


# ── L2 验证对账 ─────────────────────────────────────────────
def l2_run(
    learn_dir=LEARN_DIR,
    data_dir=DATA_DIR,
    horizons=HORIZONS,
    close_map=None,
    findings=None,
):
    """T+5/10/20 后验 → verify.jsonl。卖点反向口径（看跌是否兑现）。

    行情未入库（交易日不足 N 档）自动顺延——pending 不落行，下次重跑
    幂等补齐，不重试轰炸。同键同值零副作用 / 同键异值 ValueError。
    返回 {recorded, replayed, pending, findings}。
    """
    findings = findings if findings is not None else []
    rows = _read_jsonl(verify_path(learn_dir))
    index = {r.get("verify_key"): r for r in rows if r.get("verify_key")}
    ledger = _read_jsonl(ledger_path(learn_dir))
    if close_map is None:
        close_map = {}
        for code in sorted({r["code"] for r in ledger}):
            c = _load_close(code, data_dir)
            if c:
                close_map[code] = c
    recorded = replayed = pending = 0
    for r in ledger:
        code = r.get("code")
        closes = close_map.get(code)
        if not closes:
            findings.append("L2 缺行情 %s，全部 horizon 顺延" % code)
            pending += len(horizons)
            continue
        dts = sorted(closes)
        try:
            i = dts.index(int(r["dt"]))
        except ValueError:
            findings.append("L2 %s dt=%s 不在交易日历，顺延" % (code, r["dt"]))
            pending += len(horizons)
            continue
        for h in horizons:
            j = i + h
            if j >= len(dts):
                pending += 1
                continue
            v_dt = dts[j]
            ret = (closes[v_dt] / closes[dts[i]] - 1) * 100.0
            if str(r.get("type", "")).endswith("卖"):
                ret = -ret  # 卖点反向口径
            ret = round(ret, 4)
            key = _sha1("%s|%s" % (r["sig_id"], h))
            old = index.get(key)
            if old is not None:
                if old.get("ret_pct") != ret:  # perf_ledger 判例：只比 ret_pct
                    raise ValueError(
                        "verify 冲突：verify_key=%s 已存在但 ret 不一致 "
                        "(existing=%s new=%s；M0 契约：同键异值拒绝，"
                        "后验漂移须人工判读)" % (key, old.get("ret_pct"), ret)
                    )
                replayed += 1
                continue
            row = {
                "verify_key": key,
                "sig_id": r["sig_id"],
                "code": code,
                "type": r.get("type"),
                "horizon": h,
                "dt": int(r["dt"]),
                "verify_dt": v_dt,
                "ret_pct": ret,
                "declared_at": _found_stamp(str(v_dt)),
            }
            index[key] = row
            _append_line(verify_path(learn_dir), row)
            recorded += 1
    return {
        "recorded": recorded,
        "replayed": replayed,
        "pending": pending,
        "findings": findings,
    }


def _tail_stats(rets):
    """分布摘要（设计 §3 L2：不止单一均值）。"""
    if not rets:
        return None
    s = sorted(rets)
    n = len(s)
    i10 = max(0, -(-int(n * 10) // 100) - 1)  # ceil(n*0.10)-1
    return {
        "n": n,
        "win": round(sum(1 for v in s if v > 0) * 100.0 / n, 1),
        "avg": round(sum(s) / n, 2),
        "p10": round(s[i10], 2),
        "worst": round(s[0], 2),
    }


def l2_summary(learn_dir=LEARN_DIR, horizons=HORIZONS):
    """按 type × horizon 分布摘要（读侧聚合，供 L3 与人读日报）。"""
    by = {}
    for r in _read_jsonl(verify_path(learn_dir)):
        h = r.get("horizon")
        if r.get("ret_pct") is None:
            continue
        by.setdefault((r.get("type"), h), []).append(r["ret_pct"])
    out = {}
    for (t, h), rets in sorted(by.items()):
        out.setdefault("by_type", {})[t] = dict(
            out.get("by_type", {}).get(t, {}), **{str(h): _tail_stats(rets)}
        )
    for h in horizons:
        allr = [v for (t, hh), vs in by.items() for v in vs if hh == h]
        out["all_%d" % h] = _tail_stats(allr)
    return out


# ── L3 结构归因 ─────────────────────────────────────────────
def _dims_of(row, vrow):
    """一行信号 → 维度取值 dict（L3 维度矩阵原子）。"""
    fp = row.get("fingerprint") or {}
    return {
        "type": row.get("type"),
        "regime": row.get("regime") or "na",
        "conf_band": conf_band(row.get("conf")),
        "bis6": fp.get("bis6") or "na",
        "amp": fp.get("amp") or "na",
        "pv": fp.get("pv") or "na",
        "npv": npv_band(fp.get("npv")),
    }


def _cell_key(dims, combo):
    return "|".join("%s=%s" % (k, dims.get(k, "na")) for k in combo)


def l3_run(learn_dir=LEARN_DIR, as_of=None, horizons=HORIZONS, findings=None):
    """维度矩阵聚合 → attribution.jsonl（append-only，统计指纹幂等）。

    判读规则（显式阈值、可回放，设计 §3）：
      n<30                    → verdict="observation_only"（小样本不凭据）
      n≥30 且 avg<0 且 p10≤-10 → verdict="降置信候选"
      n≥30 且 win≥基线+10pp    → verdict="升置信候选"
      其余                     → verdict="正常"
    verdict 行必带 evidence（基线/差值/三档分布/样本 sig_id 引用）与 n。
    观察标注（E-01 S2，只读不判读）：horizon=20 且 p10≤-10 且 n≥20 →
    tail_flag="左尾观察"（不进 verdict 阈值体系、不触发 L4 出口 A/B）。
    """
    findings = findings if findings is not None else []
    as_of = as_of or datetime.now().strftime("%Y%m%d")
    ledger = {
        r["sig_id"]: r for r in _read_jsonl(ledger_path(learn_dir)) if r.get("sig_id")
    }
    vrows = [
        v
        for v in _read_jsonl(verify_path(learn_dir))
        if v.get("sig_id") in ledger and v.get("ret_pct") is not None
    ]
    # 全域基线（每 horizon）
    base = {}
    for h in horizons:
        base[h] = _tail_stats([v["ret_pct"] for v in vrows if v["horizon"] == h])
    # 维度组合 → cell → rets
    combos = [(k,) for k in DIM_1] + [c for c in DIM_2] + [c for c in DIM_3]
    cells = {}
    for v in vrows:
        row = ledger[v["sig_id"]]
        dims = _dims_of(row, v)
        for combo in combos:
            cells.setdefault((_cell_key(dims, combo), tuple(combo)), []).append(v)
    ap = attribution_path(learn_dir)
    known = {_sha1_json(r) for r in _read_jsonl(ap)}
    recorded = 0
    for (cell, combo), vs in sorted(cells.items()):
        for h in horizons:
            rets = [v["ret_pct"] for v in vs if v["horizon"] == h]
            st = _tail_stats(rets)
            if st is None:
                continue
            b = base.get(h) or {}
            baseline_win = b.get("win")
            dims_now = {k: v for k, v in (kv.split("=", 1) for kv in cell.split("|"))}
            verdict, delta = _verdict(st, baseline_win)
            ev = {
                "baseline_win": baseline_win,
                "delta_pp": delta,
                "dist": {
                    str(hh): _tail_stats(
                        [x["ret_pct"] for x in vs if x["horizon"] == hh]
                    )
                    for hh in horizons
                },
                "samples": sorted({x["sig_id"] for x in vs})[:5],
                "rule": (
                    "n<30 只观察"
                    if st["n"] < N_MIN
                    else "avg<0 且 p10<=%.0f%%" % P10_FLOOR
                    if verdict == "降置信候选"
                    else "win≥基线+%.0fpp" % WIN_EDGE_PP
                    if verdict == "升置信候选"
                    else "无阈值命中"
                ),
            }
            row = {
                "attr_key": _sha1(
                    "%s|%s|%s"
                    % (
                        cell,
                        h,
                        json.dumps(
                            [st["n"], st["win"], st["avg"], st["p10"]], sort_keys=True
                        ),
                    )
                ),
                "as_of": as_of,
                "cell": cell,
                "dims": {k: dims_now[k] for k in combo},
                "horizon": h,
                "n": st["n"],
                "win": st["win"],
                "avg": st["avg"],
                "p10": st["p10"],
                "verdict": verdict,
                "evidence": ev,
            }
            if h == 20 and st["p10"] <= P10_FLOOR and st["n"] >= TAIL_FLAG_N_MIN:
                row["tail_flag"] = "左尾观察"  # E-01 S2：只读标注，不参与判读
            if _sha1_json(row) in known:
                continue  # 同统计指纹重放零副作用
            known.add(_sha1_json(row))
            _append_line(ap, row)
            recorded += 1
    return {"recorded": recorded, "cells": len(cells), "findings": findings}


def _sha1_json(row):
    return hashlib.sha1(
        json.dumps(
            {k: v for k, v in row.items() if k not in ("as_of",)},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    , usedforsecurity=False).hexdigest()[:16]


def _verdict(st, baseline_win):
    """显式阈值判读（设计 §3；返回 (verdict, delta_pp)）。"""
    if st["n"] < N_MIN:
        return "observation_only", None
    if st["avg"] < 0 and st["p10"] <= P10_FLOOR:
        return "降置信候选", (
            baseline_win if baseline_win is None else round(st["win"] - baseline_win, 1)
        )
    if baseline_win is not None and st["win"] >= baseline_win + WIN_EDGE_PP:
        return "升置信候选", round(st["win"] - baseline_win, 1)
    return "正常", (
        round(st["win"] - baseline_win, 1) if baseline_win is not None else None
    )


# ── L4 修正（两个出口，都是建议权） ─────────────────────────
def l4_drafts(learn_dir=LEARN_DIR, now=None, findings=None):
    """出口 A：降/升置信候选 → 灰度权重草案 drafts/*.json（建议权）。

    映射（唯一自动映射，其余留档人工判读——不硬造参数位）：
      cell 含 npv=3p（多枢）→ param=multi_hub_decay（gray._conf 衰减系数，
      现役 0.85）；降置信候选 → to=from-0.05；升置信候选 → to=from+0.05。
    校验：单次幅度 ≤±0.05（构造保证）；param 夹取 [lo,hi]；shadow_until
    必填（created+28 自然日 ≈ 20 交易日）；同 cell+param+direction 未过
    期草案已存在 → 幂等跳过。**不提供任何直改生产参数的函数**。
    """
    findings = findings if findings is not None else []
    now = now or datetime.now()
    os.makedirs(drafts_dir(learn_dir), exist_ok=True)
    existing = []
    for f in sorted(os.listdir(drafts_dir(learn_dir))):
        if f.endswith(".json"):
            try:
                existing.append(
                    json.load(
                        open(os.path.join(drafts_dir(learn_dir), f), encoding="utf-8")
                    )
                )
            except Exception:
                continue
    drafted = skipped = 0
    manual = []
    for row in _read_jsonl(attribution_path(learn_dir)):
        if row.get("horizon") != MAIN_HORIZON:
            continue
        v = row.get("verdict")
        if v not in ("降置信候选", "升置信候选"):
            continue
        cell = row.get("cell") or ""
        m = PARAM_MAP.get(cell.split("|")[-1]) if cell else None
        if m is None:
            manual.append(
                {
                    "cell": cell,
                    "verdict": v,
                    "n": row.get("n"),
                    "why": "无对应可学习权重位，留人工判读",
                }
            )
            continue
        direction = "down" if v == "降置信候选" else "up"
        step = -DRAFT_MAX_STEP if direction == "down" else DRAFT_MAX_STEP
        to = round(min(max(m["from"] + step, m["lo"]), m["hi"]), 3)
        if to == m["from"]:
            manual.append(
                {"cell": cell, "verdict": v, "why": "参数已到边界 %s" % m["lo"]}
            )
            continue
        fp = "l4a|%s|%s|%s" % (cell, m["param"], direction)
        if any(
            (
                e.get("fingerprint") == fp
                and str(e.get("shadow_until", "")) >= now.strftime("%Y-%m-%d")
            )
            for e in existing
        ):
            skipped += 1
            continue
        shadow_until = (now + timedelta(days=SHADOW_NATURAL_DAYS)).strftime("%Y-%m-%d")
        draft = {
            "param": m["param"],
            "from": m["from"],
            "to": to,
            "cell": cell,
            "n": row.get("n"),
            "verdict": v,
            "rationale": "L3 归因 %s（n=%s, win=%s%%, avg=%s%%, "
            "p10=%s%%，基线 win=%s%%）→ 建议灰度参数 "
            "%s %s→%s；draft 建议权，adopt 须人工凭据，"
            "影子对照 ≥20 交易日后呈老田拍板"
            % (
                v,
                row.get("n"),
                row.get("win"),
                row.get("avg"),
                row.get("p10"),
                (row.get("evidence") or {}).get("baseline_win"),
                m["param"],
                m["from"],
                to,
            ),
            "shadow_until": shadow_until,
            "created": now.strftime("%Y-%m-%d"),
            "fingerprint": fp,
            "source": "chanlun_learn L4A (K-ORG-190)",
        }
        name = "%s_%s.json" % (now.strftime("%Y%m%d%H%M%S"), _sha1(fp))
        path = os.path.join(drafts_dir(learn_dir), name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=1)
        drafted += 1
    return {
        "drafted": drafted,
        "skipped": skipped,
        "manual": manual,
        "findings": findings,
    }


def l4_thesis(learn_dir=LEARN_DIR, ms=None, findings=None):
    """出口 B：显著事件 → agent/ST1/thesis + 理论演进 → dept/invest/thesis。

    显著事件（设计 §3：conf 与先例背离 / 左尾触发 / 同型连败连胜）：
      · 左尾触发：T+10 ret ≤ -10% → ST1 thesis（带 sig_id 证据）；
      · 同型连败：同 type 按 dt 序连续 3 笔 T+10 亏损 → ST1 thesis；
      · 理论演进：L3 降置信候选（n≥30 硬结论）→ dept/invest/thesis
        （K70 来源标签全链贯穿，供 DEV3/STRAT1 与 council 复盘引用）。
    全部指纹判重（回放幂等）；越权/缺失降级 finding 不拖账。
    """
    findings = findings if findings is not None else []
    if ms is None:
        findings.append("thesis 降级：MemoryStore 不可达")
        return {"st_thesis": 0, "dept_thesis": 0, "findings": findings}
    ledger = {
        r["sig_id"]: r for r in _read_jsonl(ledger_path(learn_dir)) if r.get("sig_id")
    }
    v10 = [
        v
        for v in _read_jsonl(verify_path(learn_dir))
        if v.get("horizon") == MAIN_HORIZON and v.get("sig_id") in ledger
    ]
    st_n = dept_n = 0
    # ① 左尾触发
    for v in v10:
        if (v.get("ret_pct") or 0) > P10_FLOOR:
            continue
        row = ledger[v["sig_id"]]
        ok = _memory_write(
            ms,
            "agent/%s/thesis" % AGENT_ID,
            {
                "kind": "left_tail",
                "sig_id": v["sig_id"],
                "code": v["code"],
                "type": v["type"],
                "dt": v["dt"],
                "ret_pct": v["ret_pct"],
                "note": "左尾触发：%s %s T+%d 后验 %.1f%%（≤%.0f%% 阈）——"
                "该信号结构判读需复核（影子，不涉持仓）"
                % (v["type"], v["code"], MAIN_HORIZON, v["ret_pct"], P10_FLOOR),
                "declared_at": v.get("declared_at"),
            },
            "left_tail|%s" % v["sig_id"],
            findings,
        )
        st_n += 1 if ok else 0
    # ② 同型连败（负段 ≥3 笔结束时报一次，避免连环重复）
    by_type = {}
    for v in v10:
        by_type.setdefault(v.get("type"), []).append(v)
    for t, vs in sorted(by_type.items()):
        vs.sort(key=lambda x: x.get("dt") or 0)
        segs, run = [], []
        for v in vs + [None]:  # sentinel 收段
            if v is not None and (v.get("ret_pct") or 0) < 0:
                run.append(v)
                continue
            if len(run) >= 3:
                segs.append(run)
            run = []
        for run in segs:
            last = run[-1]
            ok = _memory_write(
                ms,
                "agent/%s/thesis" % AGENT_ID,
                {
                    "kind": "streak_loss",
                    "sig_type": t,
                    "n": len(run),
                    "sig_ids": [x["sig_id"] for x in run],
                    "note": "同型连败：%s 连续 %d 笔 T+%d 后验为负"
                    "（末笔 %s %.1f%%）——该类型在本阶段市场状态下"
                    "判读可信度下降（影子观察）"
                    % (t, len(run), MAIN_HORIZON, last["code"], last["ret_pct"]),
                    "declared_at": last.get("declared_at"),
                },
                "streak|%s|%s" % (t, last["sig_id"]),
                findings,
            )
            st_n += 1 if ok else 0
    # ③ 理论演进（dept/invest/thesis）
    for row in _read_jsonl(attribution_path(learn_dir)):
        if row.get("horizon") != MAIN_HORIZON or row.get("verdict") != "降置信候选":
            continue
        ok = _memory_write(
            ms,
            "dept/invest/thesis",
            {
                "kind": "chan_regime_failure",
                "source": "agent/%s (chanlun_learn K-ORG-190)" % AGENT_ID,
                "cell": row.get("cell"),
                "n": row.get("n"),
                "win": row.get("win"),
                "avg": row.get("avg"),
                "p10": row.get("p10"),
                "note": "缠论结构失效观察：cell=%s 在 T+%d 口径 n=%d、"
                "win=%s%%（基线 %s%%）、avg=%s%%、p10=%s%%——"
                "降置信候选，建议权重草案待人工 adopt"
                % (
                    row.get("cell"),
                    MAIN_HORIZON,
                    row.get("n"),
                    row.get("win"),
                    (row.get("evidence") or {}).get("baseline_win"),
                    row.get("avg"),
                    row.get("p10"),
                ),
                "declared_at": row.get("as_of"),
            },
            "attr_fail|%s" % row.get("attr_key"),
            findings,
        )
        dept_n += 1 if ok else 0
    return {"st_thesis": st_n, "dept_thesis": dept_n, "findings": findings}


def l4_weekly(learn_dir=LEARN_DIR, out=None, as_of=None):
    """周记《缠论结构观察》聚合（署名产出；stdout 或 --out 文件）。"""
    as_of = as_of or datetime.now()
    if isinstance(as_of, str):  # CLI --as-of 字符串锚点
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                as_of = datetime.strptime(as_of, fmt)
                break
            except ValueError:
                continue
    week_ago = (as_of - timedelta(days=7)).strftime("%Y%m%d")
    ledger = [
        r
        for r in _read_jsonl(ledger_path(learn_dir))
        if str(r.get("found_day", "")) >= week_ago
    ]
    v10 = {
        (v.get("sig_id")): v
        for v in _read_jsonl(verify_path(learn_dir))
        if v.get("horizon") == MAIN_HORIZON
    }
    rets = [v["ret_pct"] for s in ledger for v in [v10.get(s.get("sig_id"))] if v]
    st_all = _tail_stats(rets)
    lines = [
        "# 缠论结构观察（%s 周）" % as_of.strftime("%Y-%m-%d"),
        "",
        "署名：缠论分析师 ST1（量化交易部·策略部）· 影子铁律：只判读不执行",
        "",
        "## 本周影子信号",
        "- 记账 %d 条（%s 起）" % (len(ledger), week_ago),
        "- T+%d 对账：%s"
        % (
            MAIN_HORIZON,
            "n=%d，win=%s%%，avg=%s%%" % (st_all["n"], st_all["win"], st_all["avg"])
            if st_all
            else "无已对账样本",
        ),
        "",
        "## 归因候选（T+%d 主口径）" % MAIN_HORIZON,
    ]
    for row in _read_jsonl(attribution_path(learn_dir)):
        if row.get("horizon") == MAIN_HORIZON and row.get("verdict") in (
            "降置信候选",
            "升置信候选",
        ):
            lines.append(
                "- [%s] %s（n=%s，win=%s%%，p10=%s%%）"
                % (row["verdict"], row["cell"], row["n"], row["win"], row["p10"])
            )
    # ── E-01 S1：T+20 左尾哨兵（只读 verify.jsonl，观察增强不判读） ──
    v20 = [
        v
        for v in _read_jsonl(verify_path(learn_dir))
        if v.get("horizon") == 20 and v.get("ret_pct") is not None
    ]
    deep = sorted(
        (
            v
            for v in v20
            if v["ret_pct"] <= TAIL_RET_FLOOR
            and str(v.get("verify_dt", "")) >= week_ago
        ),
        key=lambda x: x["ret_pct"],
    )
    lines += [
        "",
        "## T+20 左尾哨兵（E-01 观察增强）",
        "- 本周新对账 ret≤%.0f%% 案例：%d 条" % (TAIL_RET_FLOOR, len(deep)),
    ]
    for v in deep:
        r10 = v10.get(v.get("sig_id"))
        lines.append(
            "  - %s %s ret20=%+.2f%% ret10=%s"
            % (
                v.get("code"),
                v.get("type"),
                v["ret_pct"],
                "n/a" if not r10 else "%+.2f%%" % r10["ret_pct"],
            )
        )
    lines.append("- 卖向 T+20 滚动读数（全量窗口）：")
    for t in SELL_TYPES:
        st_t = _tail_stats([v["ret_pct"] for v in v20 if v.get("type") == t])
        if st_t:
            lines.append(
                "  - %s：n=%d，win=%s%%，avg=%s%%，p10=%s%%"
                % (t, st_t["n"], st_t["win"], st_t["avg"], st_t["p10"])
            )
    lines += [
        "",
        "## 下周关注",
        "- 候选 cell 的影子对照与左尾跟踪（草案 adopt 永远人工凭据）",
        "",
    ]
    text = "\n".join(lines)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    return text


# ── CLI ─────────────────────────────────────────────────────
def _print(tag, rep):
    print(
        "[%s] %s"
        % (
            tag,
            json.dumps(
                {k: v for k, v in rep.items() if k != "findings"}, ensure_ascii=False
            ),
        )
    )
    for f in rep.get("findings") or []:
        print("  [finding] %s" % f)


def main(argv=None):
    ap = argparse.ArgumentParser(description="缠论专属学习环 L1-L4")
    ap.add_argument("cmd", choices=["l1", "l2", "l3", "l4", "all"])
    ap.add_argument("--ledger", default=LEDGER_JSON)
    ap.add_argument("--d0", default=None)
    ap.add_argument("--d1", default=None)
    ap.add_argument("--learn-dir", default=LEARN_DIR)
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--memory-dir", default=MEMORY_DIR)
    ap.add_argument("--org", default=ORG_JSON)
    ap.add_argument("--limit-stocks", type=int, default=0)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--l4-cmd", default="drafts", choices=["drafts", "thesis", "weekly"]
    )
    ap.add_argument("--no-memory", action="store_true")
    a = ap.parse_args(argv)
    ms = None
    if not a.no_memory and a.cmd in ("l1", "l4", "all"):
        try:
            ms = memory_store(a.memory_dir, a.org)
        except Exception as e:
            print("[warn] MemoryStore 不可达（记忆腿降级）: %s" % e)
    try:
        if a.cmd in ("l1", "all"):
            _print(
                "L1",
                l1_run(
                    a.ledger, a.d0, a.d1, a.learn_dir, a.data_dir, ms, a.limit_stocks
                ),
            )
        if a.cmd in ("l2", "all"):
            _print("L2", l2_run(a.learn_dir, a.data_dir))
        if a.cmd in ("l3", "all"):
            _print("L3", l3_run(a.learn_dir, a.as_of))
        if a.cmd in ("l4", "all"):
            if a.cmd == "l4" and a.l4_cmd == "weekly":
                print(l4_weekly(a.learn_dir, a.out, a.as_of))
                return 0
            if a.cmd == "l4" and a.l4_cmd == "thesis":
                _print("L4B", l4_thesis(a.learn_dir, ms))
                return 0
            _print("L4A", l4_drafts(a.learn_dir))
            if a.cmd == "all":
                _print("L4B", l4_thesis(a.learn_dir, ms))
        return 0
    except ValueError as e:  # 同键异值拒绝 → 非零退出
        print("[ERROR] %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
