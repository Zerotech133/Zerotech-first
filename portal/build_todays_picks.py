# -*- coding: utf-8 -*-
"""聚合全部选股策略今日强制扫描结果 → portal/data/todays_picks.json

来源（各策略最新扫描产物，数据基准日见各 strategy meta）：
  1. strong  : quant_backtest/reports/strong_latest.json   tag 板块领涨/逆板块强势
  2. weak    : reports/weak_scan_<ymd>.csv                 强失速/深度垫底（周扫；
               数据基准落后行情湖超 MAX_LAG_DAYS 天时旧名单不入清单）
  3. chanlun : quant_backtest/data/chanlun_pool.jsonl      最新 detect_date 批次
  4. qpzm    : quant_backtest/reports/rps_latest.json      res 列表（score；仅 A 股；
               action=回避(环境闸门)的票不参与看多计数、不抬 T2）
  5. ninedim : quant_backtest/data/nine_dim_scores.json    rows（score/full/band）
  6. cross   : import cross_poll.build_cross               仅收 A 股看多共振
               （看多源白名单=qpzm买入/缠论买，与 tier_of 一致；sig4 与 qpzm
               同一份 rps_latest 快照不重复计数；九维不计入——K33 警报非看多；
               qpzm 回避（环境闸门）一票否决）
  7. linkage : 个股级工具，无全市场扫描口径（记录说明，不产生名单）

分层：T1=看多源共振(strong领涨+chanlun买+qpzm 计数>=2，或 cross)；T2=单策略高价值
(强领涨/缠论买点/qpzm分>=80)；CROWD=九维高分拥挤警报(K33 语义：
回避位非买入，不参与 T2 抬层)；T3=其余。weak/ninedim 影子/卖点不参与共振计数；
qpzm 回避票不进 T1（封顶 T2）；同批次缠论买卖点并存时买点不计入共振（降 T2）；
weak 负面信号（强失速/深度垫底）同样否决 T1（封顶 T2）；ST/*ST 票不进 T1/T2
（七审封顶 T3，CROWD 警报可并存）；五源带过期闸门——数据基准落后行情湖
weak>10 天、日扫源(strong/chanlun/qpzm/ninedim/cross)>4 天时整源不入清单。
用法: python portal/build_todays_picks.py（无参聚合；个股详情另跑
      python portal/build_pick_details.py --tier T1）
"""
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
MAX_LAG_DAYS = 10  # weak 周扫源允许的最大滞后自然日（覆盖长假+周一节奏）
IS_A = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")  # 本清单为 A 股口径（cross/qpzm 多市场源入清单前过滤）
QB = os.path.join(ROOT, "quant_backtest")
OUT = os.path.join(HERE, "data", "todays_picks.json")
STALE_LAG_DAILY = 4  # 日扫源允许的最大滞后自然日（覆盖周末+小长假；五审闸门）


def _ymd(s):
    """'2026-10-09' / '20261009' / datetime 串 → YYYYMMDD。"""
    return str(s or "").replace("-", "")[:8]


def _stale_meta(basis_day, src_day, max_lag, label, file, tail):
    """过期闸门（五审）：源数据基准落后行情湖超 max_lag 自然日 → 整源不入清单。
    二十三审 fail-closed：src_day 缺失/不可解析（生产端格式变更）不再静默
    放行——无法证新鲜=按过期拦截（stale 口径 ok=True 不停挂），旧数据不得
    借闸门盲区当新数据入场。basis_day 空（行情湖不可达）保持全放行（设计内
    降级，见 main 的 basis0 留痕）。"""
    if not basis_day:
        return None
    if not src_day:
        return {"label": label, "n": 0, "stale": True, "file": file,
                "note": "源数据缺基准日字段（生产端格式变更？），无法证新鲜，"
                        "整源不入清单%s" % tail}
    try:
        lag = (datetime.strptime(basis_day, "%Y%m%d")
               - datetime.strptime(src_day, "%Y%m%d")).days
    except ValueError:
        return {"label": label, "n": 0, "stale": True, "file": file,
                "note": "源基准日 %s 不可解析（格式变更？），无法证新鲜，"
                        "整源不入清单%s" % (src_day, tail)}
    if lag > max_lag:
        return {"label": label, "n": 0, "stale": True, "file": file,
                "note": "数据基准 %s 落后行情湖 %s %d 天(>%d)，旧信号不入清单%s"
                        % (src_day, basis_day, lag, max_lag, tail)}
    return None


def _std_code(code):
    """统一代码为 603268.SH / 301520.SZ / 920028.BJ 风格。"""
    c = (code or "").strip().lower()
    if "." in c:
        s, suf = c.split(".", 1)
        return "%s.%s" % (s.upper(), suf.upper())
    if c.startswith("sh"):
        return c[2:].upper() + ".SH"
    if c.startswith("sz"):
        return c[2:].upper() + ".SZ"
    if c.startswith("bj"):
        return c[2:].upper() + ".BJ"
    return c.upper()


def load_strong(stocks, basis_day=None):
    fp = os.path.join(QB, "reports", "strong_latest.json")
    d = json.load(open(fp, encoding="utf-8"))
    # 八审：date=扫描运行日（正常日比数据基准 +1），闸门拿它比对恒得
    # lag=-1、单日漏跑 lag=0，永远拦不住「strong 腿用了 D-2 行情」。
    # basis=数据基准日（板块锚同日校验值，10-10 起随 strong_scan 落盘），
    # 优先使用；旧文件无 basis 回退 date。
    stale = _stale_meta(basis_day,
                        _ymd(d.get("basis")
                             or d.get("date")
                             or str(d.get("ts") or "")[:10]),
                        STALE_LAG_DAILY, "强势板块领涨（599板块扫描）",
                        "quant_backtest/reports/strong_latest.json",
                        "；待板块扫描刷新")
    if stale:
        return stale
    rows = d.get("list") or []
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": None,
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["sources"].append({
            "s": "strong", "tag": r.get("tag"),
            "block": r.get("block"), "ex250": r.get("ex250"),
            "reasons": (r.get("reasons") or [])[:2]})
    n = len(rows)
    lead = len(d.get("lead") or [])
    meta = {"label": "强势板块领涨（599板块扫描）", "n": n, "lead": lead,
            "observe": n - lead, "file": "quant_backtest/reports/strong_latest.json",
            "ts": d.get("ts"), "note": "lead=板块领涨 observe=逆板块强势"}
    return meta


def load_weak(stocks, basis_day=None):
    rep = os.path.join(ROOT, "reports")
    cands = [f for f in os.listdir(rep)
             if f.startswith("weak_scan_") and f.endswith(".csv")]
    if not cands:
        return {"label": "弱势失速扫描", "n": 0, "note": "无输出"}
    cands.sort()
    fp = os.path.join(rep, cands[-1])
    # 过期闸门：WeakScan 为周扫（周一 15:35），数据基准落后行情湖超
    # MAX_LAG_DAYS 自然日（覆盖长假+周一节奏）视为过期，旧名单不入清单
    m = re.match(r"weak_scan_(\d{8})\.csv$", cands[-1])
    if m and basis_day:
        lag = (datetime.strptime(basis_day, "%Y%m%d")
               - datetime.strptime(m.group(1), "%Y%m%d")).days
        if lag > MAX_LAG_DAYS:
            return {"label": "弱势失速扫描（超额回撤监控）", "n": 0,
                    "stale": True, "file": "reports/" + cands[-1],
                    "note": "数据基准 %s 落后行情湖 %s %d 天(>%d)，旧名单不入清单；"
                            "待 WeakScan 周一 15:35 刷新"
                            % (m.group(1), basis_day, lag, MAX_LAG_DAYS)}
    rows = list(csv.DictReader(open(fp, encoding="utf-8-sig")))
    # 只取 focus 标注的重点池（深度垫底/强失速等），CSV 全量行不进清单
    rows = [r for r in rows if (r.get("focus") or "").strip()]
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": r.get("industry"), "close": None,
                                  "pct_chg": None, "sources": []})
        e["industry"] = e["industry"] or r.get("industry")
        e["sources"].append({
            "s": "weak", "tag": r.get("focus"),
            "block": r.get("block_name"), "ex": r.get("ex"),
            "rank_in": r.get("rank_in")})
    return {"label": "弱势失速扫描（超额回撤监控）", "n": len(rows),
            "file": "reports/" + os.path.basename(fp),
            "note": "focus=深度垫底/强失速等弱势标签（反向参考）"}


def load_chanlun(stocks, basis_day=None):
    fp = os.path.join(QB, "data", "chanlun_pool.jsonl")
    # 坏行跳过（十一审 2026-10-10）：对齐 cross_poll._load_jsonl 的 per-line
    # 容错——此前列表推导一行坏数据即整源丢失，而 cross 走 SQLite 路径存活，
    # cl_sell 同批卖点守卫随之查空失效，带卖点票可经 cross 旁路挂 T1。
    rows = []
    with open(fp, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    latest = max((r.get("detect_date") or "") for r in rows)
    stale = _stale_meta(basis_day, _ymd(latest), STALE_LAG_DAILY,
                        "缠论信号池（三类买卖点）",
                        "quant_backtest/data/chanlun_pool.jsonl",
                        "；待缠论扫描刷新")
    if stale:
        return stale
    batch = [r for r in rows if r.get("detect_date") == latest]
    for r in batch:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": r.get("price"),
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("price")
        e["sources"].append({
            "s": "chanlun", "tag": r.get("type"),
            "dt": r.get("dt"), "trend": r.get("trend"),
            "tbe": r.get("tbe"), "entry_px": r.get("entry_px")})
    return {"label": "缠论信号池（三类买卖点）", "n": len(batch),
            "batch_date": latest, "file": "quant_backtest/data/chanlun_pool.jsonl",
            "note": "取最新扫描批次；type=1买/2买/3买/1卖/2卖/3卖"}


def load_qpzm(stocks, basis_day=None):
    fp = os.path.join(QB, "reports", "rps_latest.json")
    d = json.load(open(fp, encoding="utf-8"))
    stale = _stale_meta(basis_day, _ymd(d.get("latest")), STALE_LAG_DAILY,
                        "青萍之末（RPS+技术+环境闸门）",
                        "quant_backtest/reports/rps_latest.json",
                        "；待 qpzm 扫描刷新")
    if stale:
        return stale
    rows = d.get("res") or []
    n_in = 0
    for r in rows:
        c = _std_code(r.get("code"))
        if not IS_A.match(c):
            continue  # qpzm 池含美股/加密，本清单仅 A 股
        n_in += 1
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": None, "close": r.get("px"),
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("px")
        e["sources"].append({
            "s": "qpzm", "tag": (r.get("action") or "").split("（")[0] or "观察池",
            "action": r.get("action"), "score": r.get("score"),
            "rps": r.get("rps"), "tech": r.get("tech"), "env": r.get("env"),
            "added": r.get("added")})
    return {"label": "青萍之末（RPS+技术+环境闸门）", "n": n_in,
            "trade_date": d.get("latest"), "file": "quant_backtest/reports/rps_latest.json",
            "ts": d.get("ts"), "note": "action 含 回避（环境闸门）标记"}


def load_ninedim(stocks, basis_day=None):
    fp = os.path.join(QB, "data", "nine_dim_scores.json")
    d = json.load(open(fp, encoding="utf-8"))
    stale = _stale_meta(basis_day, _ymd(d.get("trade_date")), STALE_LAG_DAILY,
                        "九维评分（多维因子合成）",
                        "quant_backtest/data/nine_dim_scores.json",
                        "；待九维评分刷新")
    if stale:
        return stale
    rows = d.get("rows") or []
    n_cand = 0
    n_scored = 0
    for r in rows:
        n_cand += 1
        if r.get("score") is not None:
            n_scored += 1
        c = _std_code(r.get("code"))
        scored = r.get("score") is not None
        e = stocks.setdefault(c, {"code": c, "name": r.get("name"),
                                  "industry": r.get("industry"),
                                  "close": r.get("close"),
                                  "pct_chg": r.get("pct_chg"), "sources": []})
        e["industry"] = e["industry"] or r.get("industry")
        e["close"] = e["close"] if e["close"] is not None else r.get("close")
        e["sources"].append({
            "s": "ninedim",
            "tag": ("九维出分" if scored else
                    ("九维候选·满权重" if r.get("full") else "九维候选(影子未出分)")),
            "score": r.get("score"), "cov": r.get("cov"),
            "band": r.get("band"), "rps50": r.get("rps50")})
    return {"label": "九维评分（多维因子合成）", "n": n_cand,
            "trade_date": d.get("trade_date"),
            "file": "quant_backtest/data/nine_dim_scores.json",
            "ts": d.get("generated_at"),
            "note": "候选池口径（RPS50>80+额>2亿+动能）；K33：高分=拥挤警报非买入；"
                    "cov<0.75 不出分；本批 %d/%d 只出分" % (n_scored, n_cand)}


def load_cross(stocks, basis_day=None):
    sys.path.insert(0, QB)
    import cross_poll
    # 过期闸门（五审）：cross 白名单输入 = qpzm + chanlun，取两者数据基准较旧者
    # 与行情湖比对（chanlun 以 JSONL 批次日期为代理——SQLite/JSONL 双写同批）。
    days = []
    try:
        days.append(_ymd(json.load(open(
            os.path.join(QB, "reports", "rps_latest.json"),
            encoding="utf-8")).get("latest")))
    except Exception as ex:  # A5: 新鲜度探针失败静默=staleness 口径缺角——留痕
        print("[picks] rps_latest 读取失败：%s" % str(ex)[:60])
    try:
        days.append(_ymd(max(json.loads(ln).get("detect_date") or ""
                             for ln in open(os.path.join(
                                 QB, "data", "chanlun_pool.jsonl"),
                                 encoding="utf-8") if ln.strip())))
    except Exception as ex:  # A5: 同上——留痕
        print("[picks] chanlun_pool 读取失败：%s" % str(ex)[:60])
    days = [x for x in days if x]
    # 二十三审 fail-closed：探针全空（两源日期字段均缺失）不再跳过闸门——
    # cross_poll 内部按每票最新取信号且 detect_date 有 dt 回退，旧数据可
    # 绕道进 T1（且此时 cl_sell 守卫因 chanlun 源 n=0 查空失效）。
    if not days:
        return {"label": "交叉共振（多策略同命中）", "n": 0, "stale": True,
                "file": "quant_backtest/cross_poll.py(内存聚合)",
                "note": "qpzm/chanlun 新鲜度探针均无基准日（格式变更？），"
                        "整源不入清单；待上游扫描刷新"}
    stale = _stale_meta(basis_day, min(days), STALE_LAG_DAILY,
                        "交叉共振（多策略同命中）",
                        "quant_backtest/cross_poll.py(内存聚合)",
                        "；待上游 qpzm/chanlun 扫描刷新")
    if stale:
        return stale
    d = cross_poll.build_cross(force=True)
    n = 0
    n_skip = 0
    for s in d.get("stocks") or []:
        c = _std_code(s.get("code"))
        if not IS_A.match(c):
            continue  # cross 面板含美股/加密，本清单仅 A 股
        # 看多共振口径（2026-10-10 三审+四审修正）：三审把 n_hit>=2 收紧为
        # n_long>=2（九维不计入）；四审进一步把看多源白名单对齐 tier_of 的
        # bull 源——仅 qpzm 买入 / 缠论买；sig4 与 qpzm 出自同一份 rps_latest
        # 快照（重复计数，曾致「回避+旧缠论买+4策略」凑成 T1）不计入；
        # qpzm 回避（环境闸门）一票否决——K33：回避位非买入，不得借 cross 抬 T1。
        hits = s.get("hits") or []
        # 同批卖点守卫（四审补漏）：cross_poll 每票只留最新一条缠论信号，
        # 同检出日买卖并列时按序任取（实据：水羊 2买+1卖 取到 2买→long）；
        # 该票若在最新缠论批次自带卖点，缠论不计入看多源——与 tier_of
        # 「同批买卖并存不计共振」同口径，堵住经 cross 绕过降级的旁路。
        # （依赖 chanlun 先于 cross 加载；chanlun 源失败时守卫自动失效降级。）
        cl_sell = any(src["s"] == "chanlun" and "卖" in (src.get("tag") or "")
                      for src in (stocks.get(c) or {}).get("sources") or [])
        # weak 否决（五审）：强失速/深度垫底 = 显式负面信号，与 qpzm 回避同哲学，
        # 不挂「多策略共振」标签（依赖 weak 先于 cross 加载，顺序见 main）。
        weak_bad = any(src["s"] == "weak"
                       for src in (stocks.get(c) or {}).get("sources") or [])
        # ST 封顶（七审）：ST/*ST 不挂「多策略共振」标签（与 tier_of 封顶同口径）
        nm = (s.get("name")
              or (stocks.get(c) or {}).get("name") or "")
        is_st = bool(re.match(r"^\*?ST", nm.upper()))  # B1: 前缀口径防误伤
        n_long = sum(1 for h in hits
                     if h.get("dir") == "long"
                     and h.get("sid") in ("qpzm", "chanlun")
                     and not (cl_sell and h.get("sid") == "chanlun"))
        avoid = any(h.get("sid") == "qpzm" and h.get("dir") == "short"
                    for h in hits)
        if n_long < 2 or avoid or weak_bad or is_st:
            if len(hits) >= 2:
                n_skip += 1  # 仅统计旧口径(n_hit>=2)本会入选、被新口径拦下的
            continue
        n += 1
        e = stocks.setdefault(c, {"code": c, "name": s.get("name"),
                                  "industry": None, "close": None,
                                  "pct_chg": None, "sources": []})
        e["name"] = e["name"] or s.get("name")
        # label@日期=信号检出日（缠论为 90 天有效窗内取最新，旧信号显式可见）
        hit_labels = ["%s|%s%s" % (h.get("sid"), h.get("label"),
                                   ("@" + h["dt"][:10]) if h.get("dt") else "")
                      for h in hits]
        e["sources"].append({
            "s": "cross", "tag": "多策略共振",
            "state": s.get("state"), "hits": hit_labels})
    return {"label": "交叉共振（多策略同命中）", "n": n,
            "file": "quant_backtest/cross_poll.py(内存聚合)",
            "note": "仅收 A 股看多共振（白名单=qpzm买入/缠论买>=2；sig4/九维不计入；"
                    "qpzm 回避否决）；本批过滤 n_hit>=2 但口径不符的 %d 只；"
                    "label@日期=信号检出日" % n_skip}


def tier_of(e, veto_ok=True):
    # T1 否决链守卫（十一审 2026-10-10）：chanlun/weak/qpzm 之一 err 失败
    # （非 stale 闸门拦截——stale 是设计内"旧负面不误杀"）时否决闸门静默
    # 失效，T1 可混入带同批卖点/强失速/qpzm回避的票 → veto_ok=False 当日
    # T1 判定整体停挂，共振票自然落 T2/T3（单策略通道不受影响）。
    # K33 复盘（2026-10-08 产品评审 P0）：九维高分=拥挤警报而非买入信号，
    # band 票不再抬进 T2；alert 级入独立 CROWD 警报层（回避标记），其余落 T3。
    # 2026-10-10 复审修正：T1 共振只数看多源（strong 板块领涨 / chanlun 买点 /
    # qpzm），weak(反向回避)、ninedim 影子(未出分无观点)、chanlun 卖点不再
    # 参与凑对——此前 60% 的 T1 靠非看多源凑成；cross(n_hit>=2 派生)仍直接 T1。
    # 2026-10-10 四审修正：①同批次缠论买卖点并存（如 2买+1卖）时买点不计入
    # 共振——多空并存非纯看多，降级走 T2 单策略通道（卖点留 sources 复核）；
    # ②qpzm 回避（环境闸门）票不进 T1、封顶 T2——与 load_cross 否决口径一致，
    # 防止回避票绕开 cross 通道经 strong+chanlun 直抬 T1。
    srcs = e["sources"]
    ss = {s["s"] for s in srcs}
    cl_tags = [s.get("tag") or "" for s in srcs if s["s"] == "chanlun"]
    cl_buy = (any("买" in t for t in cl_tags)
              and not any("卖" in t for t in cl_tags))
    bull = (any(s["s"] == "qpzm" and s.get("tag") == "买入" for s in srcs)
            + cl_buy
            + any(s["s"] == "strong" and s.get("tag") == "板块领涨"
                  for s in srcs))
    qpzm_avoid = any(s["s"] == "qpzm" and s.get("tag") == "回避" for s in srcs)
    # weak 否决（五审）：强失速/深度垫底 = 显式负面信号，与 qpzm 回避同哲学，
    # T1 封顶降 T2；weak 源自身带过期闸门，过期旧负面数据不入场（不误杀）。
    # 2026-10-10 七审修正：ST/*ST 票（5% 涨跌幅+退市风险警示）不进 T1/T2
    # 看多层——全管线此前无 ST 过滤（当日 T2 即混入 7 只 *ST），落 T3 反向
    # 参考；九维拥挤警报与 ST 可并存，CROWD 判定不受影响。
    # B1 修复(077e 选股审计): 子串 "ST" in name 会误伤名字含 ST 字母的
    # 非 ST 票——收紧为前缀正则(*ST/ST 开头才是风险警示口径)
    is_st = bool(re.match(r"^\*?ST", (e.get("name") or "").upper()))
    # 二十九审（K33 延伸 2026-10-10，用户拍板"封顶 T1"）：九维 alert/alert2
    # （score≥75 拥挤警报；K33 实证 75+ 胜率 9%、85+ 全亏）与 ST 同权封顶
    # T1/T2——动量型共振票恰是九维高分人群，此前 T1 分支先于 CROWD 判定
    # return，拥挤标记被最优标签吞掉、crowd_alert 计数漏记；alert 票直接
    # 落独立 CROWD 警报层（low/na 九维不影响 T1/T2 判定）。
    nd_alert = any(s["s"] == "ninedim" and s.get("band") in ("alert", "alert2")
                   for s in srcs)
    if not (is_st or nd_alert):
        if (veto_ok and (bull >= 2 or "cross" in ss)
                and not qpzm_avoid and "weak" not in ss):
            return "T1"
        if "chanlun" in ss and any(s["s"] == "chanlun"
                                   and "买" in (s.get("tag") or "")
                                   for s in srcs):
            return "T2"
        if "strong" in ss and any(s["s"] == "strong"
                                  and s.get("tag") == "板块领涨"
                                  for s in srcs):
            return "T2"
        if "qpzm" in ss and any(s["s"] == "qpzm"
                                and s.get("tag") == "买入"
                                and (s.get("score") or 0) >= 80
                                for s in srcs):
            return "T2"
    if nd_alert:
        return "CROWD"
    return "T3"


def _latest_history(hist_dir, exclude_day=None):
    """归档目录里最近一份（排除今日自身），供轮动 diff。"""
    best = None
    for fp in glob.glob(os.path.join(hist_dir, "todays_*.json")):
        if exclude_day and exclude_day in fp:
            continue
        if best is None or os.path.getmtime(fp) > os.path.getmtime(best):
            best = fp
    return best


def _backfill_quotes(stocks):
    """A股 close/pct_chg 回填：从 tdx_read 行情湖最新日一次取全市场。

    六策略源里仅 weak 带 pct_chg，其余 null → 列表页涨跌幅大面积「—」。
    湖不可达/无 pandas 时整体降级（保持 null，页面已有占位显示），不拖垮聚合。
    返回 (基准日YYYYMMDD, 回填数)。
    """
    try:
        sys.path.insert(0, QB)
        from tdx_read import read_day, latest_date  # noqa: E402
        day = latest_date("ts_daily")
        if not day:
            return None, 0
        df = read_day("ts_daily", day, columns=["ts_code", "close", "pct_chg"])
        q = {r.ts_code: (float(r.close), float(r.pct_chg))
             for r in df.itertuples() if r.ts_code}
        n = 0
        for c, e in stocks.items():
            hit = q.get(c)
            if hit and e.get("close") is None:
                e["close"], e["pct_chg"] = hit[0], hit[1]
                n += 1
            elif hit and e.get("pct_chg") is None:
                e["pct_chg"] = hit[1]
                n += 1
        return day, n
    except Exception as ex:  # noqa: BLE001 — 回填失败降级不拖垮
        print("[backfill] 行情回填降级：%s" % str(ex)[:80])
        return None, 0


def _archive_once(hist_dir, basis_day, doc):
    """归档首写锁定（二十六审 2026-10-10）。

    TodaysPicks 每日 15:10 照跑（含周末），周六/周日重跑时 basis_day 仍=
    上个交易日，同名覆写会把首跑后才变化的"事后清单"混进后验地基（picks_
    history 是 verdicts/后验工具的样本源，且周五首版快照被冲掉不可恢复）。
    history 取首次产出快照（first-write-wins）；重跑只刷新 todays_picks.json。
    返回 (arch, wrote)：wrote=False 表示同名归档已存在、保留首次快照；
    确需重写先手工删除该归档再跑。
    """
    arch = os.path.join(hist_dir, "todays_%s.json" % basis_day)
    if os.path.exists(arch):
        return arch, False
    tmp = arch + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    os.replace(tmp, arch)
    return arch, True


def main():
    stocks = {}
    metas = {}
    # 行情湖基准日提前取一次（weak 过期闸门用；backfill 内部会再取，轻查询幂等）
    try:
        sys.path.insert(0, QB)
        from tdx_read import latest_date
        basis0 = latest_date("ts_daily")
    except Exception as ex:  # A5: 湖基准日失败会让 basis_day 回退——留痕
        print("[picks] 行情湖 latest_date 失败 basis0=None：%s" % str(ex)[:60])
    for sid, fn in (("strong", load_strong), ("weak", load_weak),
                    ("chanlun", load_chanlun), ("qpzm", load_qpzm),
                    ("ninedim", load_ninedim), ("cross", load_cross)):
        try:
            metas[sid] = fn(stocks, basis0)
            metas[sid]["ok"] = True
        except Exception as ex:  # noqa: BLE001 — 单源失败不拖垮聚合
            metas[sid] = {"label": sid, "ok": False, "err": str(ex)[:120]}
    metas["linkage"] = {
        "label": "板块联动信号", "ok": True, "n": None,
        "note": "个股级工具（linkage_signals.py 需指定代码），无全市场扫描口径，今日不产生名单"}
    # T1 否决链状态（十一审）：ok=False 的源含 err（异常失败）；stale 闸门
    # 拦截的源 ok=True（设计内放行），不触发停挂。
    veto_failed = [s for s in ("chanlun", "weak", "qpzm")
                   if not metas.get(s, {}).get("ok")]
    veto_ok = not veto_failed

    out_rows = []
    for c, e in stocks.items():
        e["n_hit"] = len({s["s"] for s in e["sources"]} - {"cross"})
        e["tier"] = tier_of(e, veto_ok)
        out_rows.append(e)
    order = {"T1": 0, "T2": 1, "CROWD": 2, "T3": 3}
    out_rows.sort(key=lambda x: (order[x["tier"]], -x["n_hit"], x["code"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    basis_day, n_backfill = _backfill_quotes(stocks)
    # 基准日动态推导：行情湖最新日（硬编码 2026-10-07 时代已废——每天跑也是旧文案）
    if basis_day:
        basis_s = "%s-%s-%s" % (basis_day[:4], basis_day[4:6], basis_day[6:])
        trade_basis = "%s 收盘（行情湖 ts_daily 最新可得交易日）" % basis_s
    else:
        basis_s = None
        trade_basis = "行情湖不可达，涨跌幅以策略源自带为准"
    # ── 池子轮动：与上一份归档 diff（新进/退出/留存），随归档积累成轮动视图 ──
    hist_dir = os.path.join(HERE, "data", "picks_history")
    os.makedirs(hist_dir, exist_ok=True)
    cur_codes = {r["code"] for r in out_rows}
    prev_path = _latest_history(hist_dir, exclude_day=basis_day)
    rotation = {"new_in": None, "dropped": None, "retained": None,
                "vs": os.path.basename(prev_path) if prev_path else None}
    if prev_path:
        try:
            prev = json.load(open(prev_path, encoding="utf-8"))
            prev_codes = {r["code"] for r in prev.get("stocks") or []}
            rotation["new_in"] = len(cur_codes - prev_codes)
            rotation["dropped"] = len(prev_codes - cur_codes)
            rotation["retained"] = len(cur_codes & prev_codes)
        except Exception as ex:  # noqa: BLE001
            print("[rotation] 上份归档不可读：%s" % str(ex)[:60])
    doc = {
        "generated_at": now,
        "scan_run_date": basis_s or now[:10],
        "trade_basis": trade_basis,
        "strategies": metas,
        "t1_gate": {"ok": veto_ok, "failed": veto_failed,
                    "note": ("" if veto_ok else
                             "否决源异常失败，T1 当日停挂（共振票降 T2/T3）")},
        "counts": {
            "total": len(out_rows),
            "T1_resonance": sum(1 for r in out_rows if r["tier"] == "T1"),
            "T2_high_value": sum(1 for r in out_rows if r["tier"] == "T2"),
            "T3_single": sum(1 for r in out_rows if r["tier"] == "T3"),
            "crowd_alert": sum(1 for r in out_rows if r["tier"] == "CROWD"),
            "rotation": rotation,
        },
        "stocks": out_rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # 二十三审：原子落盘——portal http.server 随时在读 OUT，直写会让读端
    # 拿到半截 JSON（fetch parse error）；tmp+os.replace 同盘原子换名。
    # 归档同理（verdicts/后验工具随时读 picks_history）。
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    # 每日归档（轮动视图与后验计算的数据地基）——二十六审：首写锁定
    if basis_day:
        arch, wrote = _archive_once(hist_dir, basis_day, doc)
        if not wrote:
            print("  归档 %s 已存在：保留首次快照（重跑不覆写；如需重写先删该文件）"
                  % os.path.basename(arch))
    print("  轮动(vs %s)：新进 %s / 退出 %s / 留存 %s；归档 %s"
          % (rotation["vs"] or "无", rotation["new_in"], rotation["dropped"],
             rotation["retained"],
             "todays_%s.json" % basis_day if basis_day else "跳过(无基准日)"))
    print("todays_picks: total=%d T1=%d T2=%d CROWD=%d T3=%d -> %s"
          % (doc["counts"]["total"], doc["counts"]["T1_resonance"],
             doc["counts"]["T2_high_value"], doc["counts"]["crowd_alert"],
             doc["counts"]["T3_single"], OUT))
    if not veto_ok:
        # 双通道告警（十二审）：stdout 给运维日志；stderr 给 alert_fail——
        # start_todays_picks.ps1 只把 err.log 传给 alert_fail，stdout 警告
        # 收报人看不到，exit=3 推送将无法区分"停挂"与"崩溃"。
        _hold = ("[T1停挂] 否决源异常失败: %s —— 同批卖点/weak/qpzm回避否决"
                 "失效，今日不产 T1（共振票降 T2/T3）；exit=3 触发 alert_fail 推送"
                 % ",".join(veto_failed))
        print("  " + _hold)
        print(_hold, file=sys.stderr)
    print("  行情回填：基准 %s，回填 %d 只" % (basis_day or "无", n_backfill))
    for sid, m in metas.items():
        print("  %-8s ok=%s n=%s %s" % (sid, m.get("ok"), m.get("n"),
                                        (m.get("err") or "")[:60]))
    return veto_ok  # False → __main__ 以退出码 3 结束（ran-but-degraded 警报）


if __name__ == "__main__":
    # argparse 化：此前无参解析，--help 会直接触发真实聚合写盘（副作用 bug）
    import argparse
    _ap = argparse.ArgumentParser(
        description="聚合六策略选股产物 → portal/data/todays_picks.json"
                    "（个股详情见 build_pick_details.py）")
    _ap.parse_args()
    # 退出码口径（十一审）：0=正常；3=跑完但 T1 停挂（否决源 err，清单已
    # 落盘仅缺 T1 层）——start_todays_picks.ps1 透传给 alert_fail 推送。
    sys.exit(0 if main() else 3)
