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
环境闸门差异化·影子（P3 2026-10-11）：env_diff(闸门收紧日×缠论买点)/
panic_buy(买点信号reg=down) 两级票级标注只随档归档供后验分层，不参与
tier 判定不拦截——调研依据 chanlun_quality_layering.py（reg=down 期买点
T+10 中位 +0.32 vs flat -2.91）；qpzm stale/异常日 gate 类标注不打（fail-
closed）。P4 豁免已启用（2026-10-11 用户拍板）：收紧日×缠论1买×qpzm回避
全为环境闸门型 → qpzm_avoid 不再否决 T1（_env_diff_exempt；bull>=2 共振
把关不变，收紧日实际生效通道=strong领涨∩1买；个股型回避/action 缺失仍
否决，weak/ST/CROWD/同批卖点闸门不动；cross 旁路不同步、维持保守）。
P5 质量分层（2026-10-11 用户拍板）：毒层买点（3买bei=False：T+10 中位
-4.03 n=289；2买range：T+5 胜率 8.3% n=46）不计 T1 共振票也不计 cross
看多源（TOXIC_EXCLUDE 开关一键回退；票仍走 T2 通道）；q_toxic/q_first_buy
影子标注随档归档供后验分层（pick_layer_verdicts.py），P5c「T1 腿 1买独占」
待后验达标再议。
用法: python portal/build_todays_picks.py（无参聚合；个股详情另跑
      python portal/build_pick_details.py --tier T1）
"""

import csv
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date as _date
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
NEWS_DIR = os.path.join(
    ROOT, "news_collector"
)  # P2-2 源级降级推送（outbox+send_feishu）
MAX_LAG_DAYS = 10  # weak 周扫源允许的最大滞后自然日（覆盖长假+周一节奏）
IS_A = re.compile(
    r"^\d{6}\.(SH|SZ|BJ)$"
)  # 本清单为 A 股口径（cross/qpzm 多市场源入清单前过滤）
QB = os.path.join(ROOT, "quant_backtest")
ASHARE_DIR = os.path.join(QB, "data", "ashare")  # 收盘行情主库（TdpSync 16:0x 落盘）
OUT = os.path.join(HERE, "data", "todays_picks.json")
STALE_LAG_DAILY = 4  # 日扫源允许的最大滞后自然日（覆盖周末+小长假；五审闸门）

# ── 四十一审三闸（2026-11-12 盘后复盘落地，用户拍板）────────────────────
# 当日实证（普跌日，全市场均值 -1.65%）：名单 597 只整体 -2.09% 跑输 0.44pp。
# ① 弱势日不缩名单：盘前 score=12 / 下跌概率 79 时清单照发 597 只——
#   盘面判断与选股名单之间没有任何联动。
# ② 共振保护层缺位：T2 里 bull（看多源数）=1 的 38 只、=0 的 10 只混在一起，
#   后验分层从未在 T2 内部生效。
# ③ 信号龄无过期：T2 里挂着 78 天前的 2买（亿华通 08-25）；当日实测
#   bull=1 且龄>30交易日 -1.64% vs 龄内 -1.27%，旧信号更差还没人清。
# 三条闸门（对齐当日数据预演：T2 48→约 13 只；T1 bull>=2 口径不动——
# 当日 T1 停挂是数据问题，不是口径问题）：
WEAK_DAY = {"score_max": 30, "prob_min": 70}   # premarket_latest 的 overall/prob
SIG_MAX_TRADE_DAYS = 30                        # T2 信号龄硬过期（交易日）
BASIS_FAIL_RC = 4  # 基准新鲜度闸门失败退出码（与 0=正常 / 3=T1停挂 区分）
# P5b 毒层过滤开关（2026-10-11 启用，用户拍板）：毒层买点不计 T1 共振。
# 回退=改 False（cl_buy/载荷计数即刻恢复同权口径）。
TOXIC_EXCLUDE = True


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
        return {
            "label": label,
            "n": 0,
            "stale": True,
            "file": file,
            "note": "源数据缺基准日字段（生产端格式变更？），无法证新鲜，"
            "整源不入清单%s" % tail,
        }
    try:
        lag = (
            datetime.strptime(basis_day, "%Y%m%d")
            - datetime.strptime(src_day, "%Y%m%d")
        ).days
    except ValueError:
        return {
            "label": label,
            "n": 0,
            "stale": True,
            "file": file,
            "note": "源基准日 %s 不可解析（格式变更？），无法证新鲜，"
            "整源不入清单%s" % (src_day, tail),
        }
    if lag > max_lag:
        return {
            "label": label,
            "n": 0,
            "stale": True,
            "file": file,
            "note": "数据基准 %s 落后行情湖 %s %d 天(>%d)，旧信号不入清单%s"
            % (src_day, basis_day, lag, max_lag, tail),
        }
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
    stale = _stale_meta(
        basis_day,
        _ymd(d.get("basis") or d.get("date") or str(d.get("ts") or "")[:10]),
        STALE_LAG_DAILY,
        "强势板块领涨（599板块扫描）",
        "quant_backtest/reports/strong_latest.json",
        "；待板块扫描刷新",
    )
    if stale:
        return stale
    rows = d.get("list") or []
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": r.get("name"),
                "industry": None,
                "close": None,
                "pct_chg": None,
                "sources": [],
            },
        )
        e["name"] = e["name"] or r.get("name")
        e["sources"].append(
            {
                "s": "strong",
                "tag": r.get("tag"),
                "block": r.get("block"),
                "ex250": r.get("ex250"),
                "reasons": (r.get("reasons") or [])[:2],
            }
        )
    n = len(rows)
    lead = len(d.get("lead") or [])
    meta = {
        "label": "强势板块领涨（599板块扫描）",
        "n": n,
        "lead": lead,
        "observe": n - lead,
        "file": "quant_backtest/reports/strong_latest.json",
        "ts": d.get("ts"),
        "note": "lead=板块领涨 observe=逆板块强势",
    }
    return meta


def load_weak(stocks, basis_day=None):
    rep = os.path.join(ROOT, "reports")
    cands = [
        f for f in os.listdir(rep) if f.startswith("weak_scan_") and f.endswith(".csv")
    ]
    if not cands:
        return {"label": "弱势失速扫描", "n": 0, "note": "无输出"}
    cands.sort()
    fp = os.path.join(rep, cands[-1])
    # 过期闸门：WeakScan 为周扫（周一 15:35），数据基准落后行情湖超
    # MAX_LAG_DAYS 自然日（覆盖长假+周一节奏）视为过期，旧名单不入清单
    m = re.match(r"weak_scan_(\d{8})\.csv$", cands[-1])
    if not m and basis_day:
        # 四十一审 fail-closed（对齐二十三审 cross 探针）：文件名解析不出
        # 数据基准日（命名变更？）不再静默放行——旧名单可能借闸门盲区
        # 入场。整源不入清单；湖不可达（basis_day 空）时保持放行（设计
        # 内降级，同 _stale_meta）。
        return {
            "label": "弱势失速扫描（超额回撤监控）",
            "n": 0,
            "stale": True,
            "file": "reports/" + cands[-1],
            "note": "文件名无法解析数据基准日（%s，命名变更？），"
            "闸门 fail-closed 不放行；待 WeakScan 刷新或人工核名" % cands[-1],
        }
    if m and basis_day:
        lag = (
            datetime.strptime(basis_day, "%Y%m%d")
            - datetime.strptime(m.group(1), "%Y%m%d")
        ).days
        if lag > MAX_LAG_DAYS:
            return {
                "label": "弱势失速扫描（超额回撤监控）",
                "n": 0,
                "stale": True,
                "file": "reports/" + cands[-1],
                "note": "数据基准 %s 落后行情湖 %s %d 天(>%d)，旧名单不入清单；"
                "待 WeakScan 周一 15:35 刷新"
                % (m.group(1), basis_day, lag, MAX_LAG_DAYS),
            }
    rows = list(csv.DictReader(open(fp, encoding="utf-8-sig")))
    # 只取 focus 标注的重点池（深度垫底/强失速等），CSV 全量行不进清单
    rows = [r for r in rows if (r.get("focus") or "").strip()]
    for r in rows:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": r.get("name"),
                "industry": r.get("industry"),
                "close": None,
                "pct_chg": None,
                "sources": [],
            },
        )
        e["industry"] = e["industry"] or r.get("industry")
        e["sources"].append(
            {
                "s": "weak",
                "tag": r.get("focus"),
                "block": r.get("block_name"),
                "ex": r.get("ex"),
                "rank_in": r.get("rank_in"),
            }
        )
    return {
        "label": "弱势失速扫描（超额回撤监控）",
        "n": len(rows),
        "file": "reports/" + os.path.basename(fp),
        "note": "focus=深度垫底/强失速等弱势标签（反向参考）",
    }


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
    stale = _stale_meta(
        basis_day,
        _ymd(latest),
        STALE_LAG_DAILY,
        "缠论信号池（三类买卖点）",
        "quant_backtest/data/chanlun_pool.jsonl",
        "；待缠论扫描刷新",
    )
    if stale:
        return stale
    batch = [r for r in rows if r.get("detect_date") == latest]
    for r in batch:
        c = _std_code(r.get("code"))
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": r.get("name"),
                "industry": None,
                "close": r.get("price"),
                "pct_chg": None,
                "sources": [],
            },
        )
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("price")
        e["sources"].append(
            {
                "s": "chanlun",
                "tag": r.get("type"),
                "dt": r.get("dt"),
                "trend": r.get("trend"),
                "tbe": r.get("tbe"),
                "entry_px": r.get("entry_px"),
                # P3 环境闸门差异化（2026-10-11）：reg=信号时大盘 regime
                # （P8 附标，down/flat/up）随源带出，供影子标注与后验分层。
                # P5b 毒层过滤（2026-10-11）：bei=盘整背驰标注（3类适用，
                # True/False/None 不可判），供 3买bei=False 毒层判定。
                "reg": r.get("reg"),
                "bei": r.get("bei"),
            }
        )
    return {
        "label": "缠论信号池（三类买卖点）",
        "n": len(batch),
        "batch_date": latest,
        "file": "quant_backtest/data/chanlun_pool.jsonl",
        "note": "取最新扫描批次；type=1买/2买/3买/1卖/2卖/3卖",
    }


def load_qpzm(stocks, basis_day=None):
    fp = os.path.join(QB, "reports", "rps_latest.json")
    d = json.load(open(fp, encoding="utf-8"))
    stale = _stale_meta(
        basis_day,
        _ymd(d.get("latest")),
        STALE_LAG_DAILY,
        "青萍之末（RPS+技术+环境闸门）",
        "quant_backtest/reports/rps_latest.json",
        "；待 qpzm 扫描刷新",
    )
    if stale:
        return stale
    rows = d.get("res") or []
    n_in = 0
    for r in rows:
        c = _std_code(r.get("code"))
        if not IS_A.match(c):
            continue  # qpzm 池含美股/加密，本清单仅 A 股
        n_in += 1
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": r.get("name"),
                "industry": None,
                "close": r.get("px"),
                "pct_chg": None,
                "sources": [],
            },
        )
        e["name"] = e["name"] or r.get("name")
        e["close"] = e["close"] or r.get("px")
        e["sources"].append(
            {
                "s": "qpzm",
                "tag": (r.get("action") or "").split("（")[0] or "观察池",
                "action": r.get("action"),
                "score": r.get("score"),
                "rps": r.get("rps"),
                "tech": r.get("tech"),
                "env": r.get("env"),
                "added": r.get("added"),
            }
        )
    return {
        "label": "青萍之末（RPS+技术+环境闸门）",
        "n": n_in,
        "trade_date": d.get("latest"),
        "file": "quant_backtest/reports/rps_latest.json",
        "ts": d.get("ts"),
        "note": "action 含 回避（环境闸门）标记",
        "env_gate": _a_share_env(d.get("envs")),
    }


def _a_share_env(envs):
    """A股环境闸门状态提取（P1 T1 功效评估 2026-10-11）：qpzm envs 数组
    中 label 以「A股」开头的一条=全市场宽度闸门（safe=False 时 qpzm 腿
    全回避、T1 只剩 strong∩chanlun 通道）。附进 meta 随 strategies 归档，
    后验可按闸门开合分层统计 T1（收紧日/放行日供给与构成不同；湖回算
    序列已验证与源头一致，但归档固化免回算、可追溯）。stale 拦截时本
    meta 不产出（过期闸门状态同样不可信，fail-closed）。"""
    for e in envs or []:
        if str(e.get("label") or "").startswith("A股"):
            return {
                "safe": e.get("safe"),
                "state": e.get("state"),
                "score": e.get("score"),
                "breadth": e.get("breadth"),
            }
    return None


def _env_diff_shadow(e, gate_tight):
    """环境闸门差异化·影子标注（P3 2026-10-11，chanlun 买点质量分层
    调研落地；调研脚本 chanlun_quality_layering.py，3438 事件可成交口径）。

    调研发现：大盘 reg=down 期检出的缠论买点 T+10 中位 +0.32，显著优于
    flat 期 -2.91——恐慌期买点质量反而高，环境闸门一刀切压制 chanlun 腿
    损失的不全是噪音。本函数打两级影子标注（只标注不拦截不抬层，tier
    判定不读本标注；启用门槛=影子样本 n≥10 且后验显著优于对照，届时
    另提方案用户拍板）：
      · env_diff  = A股闸门收紧日(safe=False) × 票有缠论买点——差异化
        对象（收紧期 qpzm 腿全回避、T1 只剩 strong∩chanlun 通道，此层
        即「环境差但结构信号在」的候选豁免人群）
      · panic_buy = 缠论买点信号自带 reg=down（信号级恐慌标注，不依赖
        qpzm 闸门状态；chanlun 源 stale 拦截时源不入场，天然 fail-closed）
    证据字段 env_diff_why 随票归档。宽口径说明：买卖点并存的票也标
    （标注对象=环境压制下的买点证据，多空并存的信息留给后验自行分层；
    tier 侧 cl_buy 严格口径不受影响）。qpzm stale/异常时 env_gate 缺失
    → gate_tight=False（闸门状态不可信不打日级标注，与 _a_share_env
    fail-closed 哲学一致）。返回 (env_diff, panic_buy, why)。
    """
    cl_buys = [
        s for s in e["sources"] if s["s"] == "chanlun" and "买" in (s.get("tag") or "")
    ]
    if not cl_buys:
        return False, False, None
    panic = any(s.get("reg") == "down" for s in cl_buys)
    diff = bool(gate_tight)
    why = None
    if diff or panic:
        bits = []
        if diff:
            bits.append("A股闸门收紧日买点")
        if panic:
            bits.append("信号reg=down(恐慌期检出)")
        why = "×".join(bits)
    return diff, panic, why


def _env_diff_exempt(e, gate_tight):
    """环境闸门差异化·豁免判定（P4 启用 2026-10-11，用户拍板）。

    条件（全部满足才豁免 qpzm 回避对 T1 的否决）：
      1. gate_tight：A股闸门收紧日（env_gate.safe=False；qpzm stale/异常
         时 env_gate 缺失 → 不豁免，闸门状态不可信 fail-closed）
      2. 票有缠论 1买——质量分层唯一正 alpha 层（T+10 中位 +0.43 vs
         全池 -1.14；进 T1 仍需 bull>=2 共振把关，收紧日实际即
         strong领涨∩1买，供给增量天然受 strong 交集约束不泛滥）
      3. qpzm 回避全部为「环境闸门」型（action 含 环境闸门 字样）——
         个股型回避仍否决；action 缺失=型别不可判，维持否决（保守）
    哲学：环境闸门型回避传递的是「市场差」信息，而调研（可成交口径
    3438 事件）显示市场差恰是 1买 质量最高的环境（reg=down T+10 中位
    +0.32 vs flat -2.91；收紧期一刀切否决 1买 共振票自相矛盾）。豁免
    仅作用于 qpzm_avoid 一处；weak 否决/ST 封顶/CROWD/同批卖点不计
    共振等其余闸门全部不动。cross 通道（load_cross 侧过滤）本次不同步
    ——豁免票走 strong+chanlun 主通道即可，cross 旁路维持保守。
    """
    if not gate_tight:
        return False
    if not any(s["s"] == "chanlun" and s.get("tag") == "1买" for s in e["sources"]):
        return False
    avoids = [s for s in e["sources"] if s["s"] == "qpzm" and s.get("tag") == "回避"]
    return bool(avoids) and all("环境闸门" in (s.get("action") or "") for s in avoids)


def _chanlun_toxic(srcs):
    """毒层买点判定（P5b 启用 2026-10-11，用户拍板；调研依据
    chanlun_quality_layering.py 可成交口径 3438 事件）。

    毒层 = 以下任一买点（T1 共振不计票，票本身仍走 T2 单策略通道）：
      · 3买 ∧ bei=False（盘整背驰明确不成立）：T+10 中位 -4.03 vs
        全池 -1.14（n=289，超 skill 达标线 n≥10 且反向显著）
      · 2买 ∧ trend=range：T+5 胜率 8.3%（n=46，小样本——故整体
        挂 TOXIC_EXCLUDE 开关可一键回退）
    bei=None（不可判）/ trend 缺失不判毒——保守方向，与 P4 fail-closed
    哲学一致。卖点票不进本判定（cl_buy 口径已排除买卖并存）。
    """
    for s in srcs:
        if s.get("s") != "chanlun":
            continue
        tag = s.get("tag") or ""
        if "买" not in tag:
            continue
        if tag == "3买" and s.get("bei") is False:
            return True
        if tag == "2买" and s.get("trend") == "range":
            return True
    return False


def _q_layer_marks(e):
    """chanlun 买点质量分层·影子标注（P5a 2026-10-11，只标注不拦截）。

    两级标注随档归档供 P5d 后验分层（pick_layer_verdicts.py）：
      · q_toxic    = 毒层买点票（口径同 _chanlun_toxic；P5b 已启用过滤，
        本标注继续记录毒层人群构成，跟踪其 T2 通道表现是否同样劣势）
      · q_first_buy= 缠论 1买票（唯一正 alpha 层：T+10 中位 +0.43 vs
        全池 -1.14，分半稳健；P5c「T1 腿 1买独占」是否启用由后验说话）
    返回 (q_toxic, q_first_buy)。
    """
    cl_buys = [
        s for s in e["sources"] if s["s"] == "chanlun" and "买" in (s.get("tag") or "")
    ]
    if not cl_buys:
        return False, False
    return (_chanlun_toxic(e["sources"]), any(s.get("tag") == "1买" for s in cl_buys))


def load_ninedim(stocks, basis_day=None):
    fp = os.path.join(QB, "data", "nine_dim_scores.json")
    d = json.load(open(fp, encoding="utf-8"))
    stale = _stale_meta(
        basis_day,
        _ymd(d.get("trade_date")),
        STALE_LAG_DAILY,
        "九维评分（多维因子合成）",
        "quant_backtest/data/nine_dim_scores.json",
        "；待九维评分刷新",
    )
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
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": r.get("name"),
                "industry": r.get("industry"),
                "close": r.get("close"),
                "pct_chg": r.get("pct_chg"),
                "sources": [],
            },
        )
        e["industry"] = e["industry"] or r.get("industry")
        e["close"] = e["close"] if e["close"] is not None else r.get("close")
        e["sources"].append(
            {
                "s": "ninedim",
                "tag": (
                    "九维出分"
                    if scored
                    else (
                        "九维候选·满权重" if r.get("full") else "九维候选(影子未出分)"
                    )
                ),
                "score": r.get("score"),
                "cov": r.get("cov"),
                "band": r.get("band"),
                "rps50": r.get("rps50"),
            }
        )
    return {
        "label": "九维评分（多维因子合成）",
        "n": n_cand,
        "trade_date": d.get("trade_date"),
        "file": "quant_backtest/data/nine_dim_scores.json",
        "ts": d.get("generated_at"),
        "note": "候选池口径（RPS50>80+额>2亿+动能）；K33：高分=拥挤警报非买入；"
        "cov<0.75 不出分；本批 %d/%d 只出分" % (n_scored, n_cand),
    }


def load_cross(stocks, basis_day=None):
    sys.path.insert(0, QB)
    import cross_poll

    # 过期闸门（五审）：cross 白名单输入 = qpzm + chanlun，取两者数据基准较旧者
    # 与行情湖比对（chanlun 以 JSONL 批次日期为代理——SQLite/JSONL 双写同批）。
    days = []
    try:
        days.append(
            _ymd(
                json.load(
                    open(
                        os.path.join(QB, "reports", "rps_latest.json"), encoding="utf-8"
                    )
                ).get("latest")
            )
        )
    except Exception as ex:  # A5: 新鲜度探针失败静默=staleness 口径缺角——留痕
        print("[picks] rps_latest 读取失败：%s" % str(ex)[:60])
    try:
        days.append(
            _ymd(
                max(
                    json.loads(ln).get("detect_date") or ""
                    for ln in open(
                        os.path.join(QB, "data", "chanlun_pool.jsonl"), encoding="utf-8"
                    )
                    if ln.strip()
                )
            )
        )
    except Exception as ex:  # A5: 同上——留痕
        print("[picks] chanlun_pool 读取失败：%s" % str(ex)[:60])
    days = [x for x in days if x]
    # 二十三审 fail-closed：探针全空（两源日期字段均缺失）不再跳过闸门——
    # cross_poll 内部按每票最新取信号且 detect_date 有 dt 回退，旧数据可
    # 绕道进 T1（且此时 cl_sell 守卫因 chanlun 源 n=0 查空失效）。
    if not days:
        return {
            "label": "交叉共振（多策略同命中）",
            "n": 0,
            "stale": True,
            "file": "quant_backtest/cross_poll.py(内存聚合)",
            "note": "qpzm/chanlun 新鲜度探针均无基准日（格式变更？），"
            "整源不入清单；待上游扫描刷新",
        }
    stale = _stale_meta(
        basis_day,
        min(days),
        STALE_LAG_DAILY,
        "交叉共振（多策略同命中）",
        "quant_backtest/cross_poll.py(内存聚合)",
        "；待上游 qpzm/chanlun 扫描刷新",
    )
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
        cl_sell = any(
            src["s"] == "chanlun" and "卖" in (src.get("tag") or "")
            for src in (stocks.get(c) or {}).get("sources") or []
        )
        # P5b 毒层同步（2026-10-11）：毒层买点（_chanlun_toxic 口径）
        # 不计入 cross 看多源——与 tier_of cl_buy 收紧同口径，堵旁路
        # （依赖 chanlun 先于 cross 加载；chanlun 源失败守卫自动失效降级）。
        cl_toxic = _chanlun_toxic((stocks.get(c) or {}).get("sources") or [])
        # weak 否决（五审）：强失速/深度垫底 = 显式负面信号，与 qpzm 回避同哲学，
        # 不挂「多策略共振」标签（依赖 weak 先于 cross 加载，顺序见 main）。
        weak_bad = any(
            src["s"] == "weak" for src in (stocks.get(c) or {}).get("sources") or []
        )
        # ST 封顶（七审）：ST/*ST 不挂「多策略共振」标签（与 tier_of 封顶同口径）
        nm = s.get("name") or (stocks.get(c) or {}).get("name") or ""
        is_st = bool(re.match(r"^\*?ST", nm.upper()))  # B1: 前缀口径防误伤
        n_long = sum(
            1
            for h in hits
            if h.get("dir") == "long"
            and h.get("sid") in ("qpzm", "chanlun")
            and not (cl_sell and h.get("sid") == "chanlun")
            and not (TOXIC_EXCLUDE and cl_toxic and h.get("sid") == "chanlun")
        )
        avoid = any(h.get("sid") == "qpzm" and h.get("dir") == "short" for h in hits)
        if n_long < 2 or avoid or weak_bad or is_st:
            if len(hits) >= 2:
                n_skip += 1  # 仅统计旧口径(n_hit>=2)本会入选、被新口径拦下的
            continue
        n += 1
        e = stocks.setdefault(
            c,
            {
                "code": c,
                "name": s.get("name"),
                "industry": None,
                "close": None,
                "pct_chg": None,
                "sources": [],
            },
        )
        e["name"] = e["name"] or s.get("name")
        # label@日期=信号检出日（缠论为 90 天有效窗内取最新，旧信号显式可见）
        hit_labels = [
            "%s|%s%s"
            % (
                h.get("sid"),
                h.get("label"),
                ("@" + h["dt"][:10]) if h.get("dt") else "",
            )
            for h in hits
        ]
        e["sources"].append(
            {
                "s": "cross",
                "tag": "多策略共振",
                "state": s.get("state"),
                "hits": hit_labels,
            }
        )
    return {
        "label": "交叉共振（多策略同命中）",
        "n": n,
        "file": "quant_backtest/cross_poll.py(内存聚合)",
        "note": "仅收 A 股看多共振（白名单=qpzm买入/缠论买>=2；sig4/九维不计入；"
        "qpzm 回避否决）；本批过滤 n_hit>=2 但口径不符的 %d 只；"
        "label@日期=信号检出日" % n_skip,
    }


def _pick_sort_key(x):
    """清单排序键（P5c 形态 C 2026-10-11 启用）：tier → tier 内缠论 1买
    （q_first_buy，唯一正中位层）置顶 → n_hit 降序 → code。纯展示层
    优先级，不影响 tier 判定/拦截；形态 B（strong∩chanlun 通道 1买
    独占）待影子达标另议，数据工具 t1_channel_verdicts.py。
    四十一审补：弱势日被缩掉的 T3（weak_day_trim）沉到同 tier 最底。"""
    order = {"T1": 0, "T2": 1, "CROWD": 2, "T3": 3}
    return (order[x["tier"]], not x.get("q_first_buy"), -x["n_hit"],
            bool(x.get("weak_day_trim")), x["code"])


def _sig_age_days(e, basis_day):
    """票内最新缠论买点距基准日的自然日数（供交易日折算）。

    只看买点（'买' in tag）——卖点是离场依据不是入场信号，龄再长也不该
    把一只票从看多层清出去。无 chanlun 日期的票（strong/qpzm 单源）返回
    None：这类源的时效由五源 stale 闸门把关，这里不重复设卡。
    """
    days = []
    for s in e["sources"]:
        if s.get("s") != "chanlun" or "买" not in (s.get("tag") or ""):
            continue
        d = s.get("dt") or ""
        if len(d) >= 10 and d[4] == "-" and basis_day:
            try:
                y1, m1, d1 = int(d[:4]), int(d[5:7]), int(d[8:10])
                y2, m2, d2 = int(basis_day[:4]), int(basis_day[4:6]), int(basis_day[6:8])
                days.append((_date(y2, m2, d2) - _date(y1, m1, d1)).days)
            except Exception:
                continue
    return min(days) if days else None


def _bull_count(srcs):
    """看多源计数（与 tier_of 的 bull 同口径，抽出供闸门复用）。

    与 n_hit（源总数）不同：weak/ninedim/卖点/回避都不算看多。2026-11-12
    复盘教训：把源总数当共振，会把 weak:深度垫底（超跌反弹标记）误读成
    「共振保护」——方向反了的信号凑数比单信号更危险。
    """
    tags = [s.get("tag") or "" for s in srcs if s["s"] == "chanlun"]
    cl_buy = any("买" in t for t in tags) and not any("卖" in t for t in tags)
    if TOXIC_EXCLUDE and cl_buy and _chanlun_toxic(srcs):
        cl_buy = False
    return (
        any(s["s"] == "qpzm" and s.get("tag") == "买入" for s in srcs)
        + cl_buy
        + any(s["s"] == "strong" and s.get("tag") == "板块领涨" for s in srcs)
    )


def _weak_market_state(basis_day):
    """读盘前情绪快照，判今日是否弱势日（fail-open：读不到=不缩名单）。

    联动点：premarket_latest.json 的 overall（五维总分，0~100）与 prob
    （下跌概率 %）。11-12 实证：overall=12、prob=79，名单照发 597 只、
    当日 -2.09% vs 基准 -1.65%——盘面判断和名单之间没有联动，这是当日
    最大的口径缺口。缺文件/字段缺失/非交易快照（生成日≠基准日）都按
    「判不了」处理：宁可漏报弱势，不可用昨天的情绪缩今天的名单。
    """
    fp = os.path.join(QB, "reports", "premarket_latest.json")
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        return None
    snap_day = _ymd((d.get("ts") or "")[:10]) or _ymd(str(d.get("trade_date") or ""))
    if snap_day != basis_day:
        return None                     # 快照不是今天的，情绪不可外推
    score, prob = d.get("overall"), d.get("prob")
    if not isinstance(score, (int, float)) or not isinstance(prob, (int, float)):
        return None
    return {"score": score, "prob": prob,
            "weak": score <= WEAK_DAY["score_max"] or prob >= WEAK_DAY["prob_min"]}


def tier_of(e, veto_ok=True, gate_tight=False):
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
    cl_buy = any("买" in t for t in cl_tags) and not any("卖" in t for t in cl_tags)
    # P5b 毒层过滤（2026-10-11 启用）：毒层买点（3买bei=False/2买range）
    # 不计 T1 共振票——票仍走 T2 单策略通道；回退=TOXIC_EXCLUDE=False。
    if TOXIC_EXCLUDE and cl_buy and _chanlun_toxic(srcs):
        cl_buy = False
    bull = (
        any(s["s"] == "qpzm" and s.get("tag") == "买入" for s in srcs)
        + cl_buy
        + any(s["s"] == "strong" and s.get("tag") == "板块领涨" for s in srcs)
    )
    qpzm_avoid = any(s["s"] == "qpzm" and s.get("tag") == "回避" for s in srcs)
    # 环境闸门差异化豁免（P4 启用 2026-10-11）：收紧日 × 缠论1买 × 回避
    # 全为环境闸门型 → qpzm_avoid 不再否决 T1（市场差恰是 1买 质量最高
    # 环境，reg=down T+10 中位 +0.32 vs flat -2.91；详见 _env_diff_exempt）。
    # bull>=2 共振把关不变，收紧日实际生效通道=strong领涨∩1买。
    if qpzm_avoid and _env_diff_exempt(e, gate_tight):
        qpzm_avoid = False
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
    nd_alert = any(
        s["s"] == "ninedim" and s.get("band") in ("alert", "alert2") for s in srcs
    )
    if not (is_st or nd_alert):
        if (
            veto_ok
            and (bull >= 2 or "cross" in ss)
            and not qpzm_avoid
            and "weak" not in ss
        ):
            return "T1"
        if "chanlun" in ss and any(
            s["s"] == "chanlun" and "买" in (s.get("tag") or "") for s in srcs
        ):
            return "T2"
        if "strong" in ss and any(
            s["s"] == "strong" and s.get("tag") == "板块领涨" for s in srcs
        ):
            return "T2"
        if "qpzm" in ss and any(
            s["s"] == "qpzm" and s.get("tag") == "买入" and (s.get("score") or 0) >= 80
            for s in srcs
        ):
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

    三十七审（2026-10-10）：湖权威覆盖（原实现只补 None）。loader 占位价
    是各自快照时点——chanlun 用信号批次 price（可能数周前）、qpzm 用
    rps px、ninedim 用九维快照 close——与湖 pct 不同源不同日，「close=
    旧价 + pct_chg=最新日」混搭曾致 76/766 票现价错误（1009 档实证：
    T1 亚邦 603188 显示信号批次价 4.32 vs 湖 1009 收盘 5.07，错 14.8%，
    9/29-30 两连板拉起后；最重经纬辉开错 33%）。湖行 close/pct 同日
    自洽，命中即无条件覆盖。
    湖不可达/无 pandas 时整体降级（保持 loader 占位，页面已有占位显示），
    不拖垮聚合。返回 (基准日YYYYMMDD, 覆盖数)。
    """
    try:
        sys.path.insert(0, QB)
        from tdx_read import read_day, latest_date  # noqa: E402

        day = latest_date("ts_daily")
        if not day:
            return None, 0
        df = read_day("ts_daily", day, columns=["ts_code", "close", "pct_chg"])
        q = {
            r.ts_code: (float(r.close), float(r.pct_chg))
            for r in df.itertuples()
            if r.ts_code
        }
        n = 0
        for c, e in stocks.items():
            hit = q.get(c)
            if not hit:
                continue  # 湖无行（停牌/退市）→ 保留 loader 占位
            if e.get("close") != hit[0] or e.get("pct_chg") != hit[1]:
                e["close"], e["pct_chg"] = hit[0], hit[1]
                n += 1
        return day, n
    except Exception as ex:  # noqa: BLE001
        print("[backfill] 行情回填降级：%s" % str(ex)[:80])
        return None, 0


def _ashare_latest_day(sample="000001_SZ.parquet"):
    """读 ashare 单票 parquet 的最新 trade_date（收盘库落盘哨兵）。

    只读一只样本，毫秒级；失败/缺文件返回 None（探测失败不阻断主链，
    由调用方按「无法证明新鲜」处理——但当前只在拿到 ashare_day 时比较）。
    """
    try:
        import pandas as pd  # noqa: PLC0415（延迟导入，与 _backfill_quotes 同口径）

        fp = os.path.join(ASHARE_DIR, sample)
        if not os.path.exists(fp):
            return None
        df = pd.read_parquet(fp, columns=["trade_date"])
        if df.empty:
            return None
        return str(df["trade_date"].max())
    except Exception as ex:  # noqa: BLE001
        print("[picks] ashare 基准探测降级：%s" % str(ex)[:80])
        return None


def _basis_guard(basis_day, ashare_day=None):
    """基准新鲜度闸门：行情湖基准日落后 ashare 收盘库 → 返回错误文案。

    fail-closed（2026-11-04 立；2026-11-05 审计加固）：TodaysPicks 原 15:40
    跑在 TdxSync_A_高频 17:35 之前，basis 恒为 D-1；2026-11-04 首次实测
    17:35 同步后才写入当日 ts_daily。本闸门比较两个只读基准日：
    - ashare 最新交易日 > 湖基准日 → 拒绝写盘（rc=4）；
    - ashare 哨兵探测失败 → 无法证明基准新鲜，同样拒绝写盘（rc=4）；
      PICKS_BASIS_ALLOW_PROBE_FAIL=1 可临时放行（仅回滚/调试）。
    - 两库同为最后交易日（周末/节假日/尚未开盘）→ 放行；若需进一步识别
      「工作日双 D-1」，用 PICKS_BASIS_STRICT=1（需交易日历，默认关）。
    PICKS_BASIS_GUARD=0 可整体关闭（仅回滚/调试，生产不设）。
    """
    if os.environ.get("PICKS_BASIS_GUARD", "1") == "0":
        return None
    if not basis_day:
        return None  # 湖不可达保持设计内降级（原因已在 backfill 留痕）
    ad = ashare_day if ashare_day is not None else _ashare_latest_day()
    if not ad:
        if os.environ.get("PICKS_BASIS_ALLOW_PROBE_FAIL", "0") == "1":
            return None
        return (
            "ashare 收盘库基准探测失败（%s 不可读/为空），无法证明行情湖基准 %s 新鲜"
            % (ASHARE_DIR, _ymd(basis_day) or basis_day)
        )
    b = _ymd(basis_day)
    a = _ymd(ad)
    if a and b and b < a:
        return (
            "行情湖基准 %s 落后 ashare 收盘库最新交易日 %s"
            "（TdxSync_A_高频 17:35 未同步/失败）" % (b, a)
        )
    if os.environ.get("PICKS_BASIS_STRICT", "0") == "1" and a and b and b == a:
        today = _date.today()
        t = today.strftime("%Y%m%d")
        if today.weekday() < 5 and t > b:
            return (
                "行情湖与 ashare 收盘库同为 %s，但今日 %s 为工作日（疑似双双未更新）"
                % (b, t)
            )
    return None


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


def _snapshot_qpzm(hist_dir, basis_day):
    """qpzm 源逐日快照留档（K-STOCK-121，2026-10-11）。

    quant_backtest/reports/rps_latest.json 每日滚动覆写、历史不保，T1 主
    通道之一（qpzm 腿）因此无法离线重放（P5c 报告 §5 局限「qpzm 通道
    留白」的根源）。本函数把当日 rps_latest 全量（res/tops/envs 环境闸门）
    首写锁定快照进 picks_history（qpzm_YYYY-MM-DD.json，~27KB/日）：
    - 与 _archive_once 同口径 first-write-wins，重跑不覆写；
    - 独立补档：归档已存在也照补（功能上线日的既有 basis_day 可回填，
      如 2026-10-09 周末回填）；monitor/verdicts 只认 todays_*.json
      前缀，本快照对其不可见、零污染；
    - 防错位：仅当源自身 latest 与 basis_day 一致才落档（行情湖与源
      刷新错位日不误标历史）；
    - 侧线动作不反噬主链：任何异常仅打印。
    """
    try:
        src = os.path.join(QB, "reports", "rps_latest.json")
        if not os.path.exists(src):
            return
        with open(src, encoding="utf-8") as f:
            d = json.load(f)
        latest = str(d.get("latest") or "")
        if latest != basis_day.replace("-", ""):
            print(
                "  [qpzm 快照] 跳过：源 latest=%s 与 basis=%s 错位"
                "（防误标，不落档）" % (latest or "?", basis_day)
            )
            return
        out = os.path.join(hist_dir, "qpzm_%s.json" % basis_day)
        if os.path.exists(out):
            return  # 首写锁定，重跑不覆写（与 _archive_once 同口径）
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
        print(
            "  [qpzm 快照] 留档 %s（res=%s tops=%s envs=%s）"
            % (
                os.path.basename(out),
                len(d.get("res") or []),
                len(d.get("tops") or []),
                len(d.get("envs") or []),
            )
        )
    except Exception as ex:  # noqa: BLE001
        print("  [qpzm 快照] 失败（不反噬主链）：%s" % str(ex)[:80])


def gate_failed_sources(metas):
    """五源全景闸停挂名单（三十九审 2026-10-10，用户拍板）。

    停挂名单=否决三源（chanlun/weak/qpzm）+strong（bull 证据腿缺席=
    T1 近空档、板块领涨 T2 缺失——保守方向但无披露）+ninedim（封顶源
    缺席=拥挤警报失明，K33 实证 alert 带胜率 9% 的票可直接混入 T1，
    档被首写锁定不可恢复——激进方向）。语义：最高层只在全景可信时开放；
    异常日 T1 诚实空档，T2/T3 照常，档内 t1_gate 留痕供后验按档识别。
    cross 不入名单：旁路缺失设计内可容忍（bull 判定照常），且其数据源
    与 qpzm/chanlun 命运绑定（其降级走 _push_source_alert 推送）。
    P2-3（2026-10-11）从 main 内联抽出为可测函数；源键整体缺失
    （loader 未注册）按停挂计——`not metas.get(s, {}).get("ok")`。
    """
    return [
        s
        for s in ("chanlun", "weak", "qpzm", "strong", "ninedim")
        if not metas.get(s, {}).get("ok")
    ]


def _push_source_alert(metas, veto_failed, basis_day):
    """P2-2（2026-10-11）：rc=0 路径下的源级降级飞书推送。

    背景：停挂路径（五源任一 ok=False）rc=3 已走 alert_fail 飞书推送
    （ps1 Invoke-Stage 的 code -ne 0 分支，十二审双通道），但 rc=0 路径
    存在两类「页面有显示、无人被通知」的盲区——
      a) 非停挂源失败：cross 探针异常（不在五源停挂名单，二十三审
         fail-closed 不产名单）→ T1 旁路当日缺失，无任何推送；
      b) stale 闸门拦截：上游任务未跑/停摆（如 WeakScan 坏了）导致
         源过期不入清单——stale 口径 ok=True 不停挂，但源长期缺席
         无人知晓（同 TdxSync 连续失败告警族问题）。
    正常日（无失败源、无 stale 源）零推送（噪音控制）；veto_failed
    非空时本函数不推（rc=3 链路已推，防双推）。复用 news_collector
    outbox+send_feishu（同 alert_fail 口径）；推送异常整体吞掉——
    告警器不能反噬聚合主流程 rc（同 alert_fail 语义）。
    """
    if veto_failed:
        return
    if os.environ.get("PICKS_SKIP_PUSH") == "1":
        print("  [PICKS 源级降级] PICKS_SKIP_PUSH=1 跳过飞书推送（验证/回放用）")
        return
    lines = []
    for sid, m in metas.items():
        if sid == "linkage":
            continue  # 纯记录源恒 ok=True，无名单语义
        if not m.get("ok"):
            lines.append("- %s 异常失败: %s" % (sid, (m.get("err") or "")[:60]))
        elif m.get("stale"):
            lines.append("- %s 过期拦截: %s" % (sid, (m.get("note") or "")[:60]))
    if not lines:
        return
    msg = (
        "[PICKS 源级降级] todays_picks 聚合完成（T1 闸正常，rc=0），"
        "但以下源降级未入清单（数据基准 %s）：\n%s\n"
        "页面策略卡有显示；请检查对应上游任务是否停摆。"
        % (basis_day or "?", "\n".join(lines))
    )
    print("  [PICKS 源级降级] " + "；".join(l[2:] for l in lines))
    try:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_picks_sources.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("  [picks] 源级降级推送：%s" % (r.stdout or "").strip()[:60])
    except Exception as ex:  # noqa: BLE001
        print("  [picks] 源级降级推送失败（不影响聚合）：%s" % str(ex)[:80])


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
    for sid, fn in (
        ("strong", load_strong),
        ("weak", load_weak),
        ("chanlun", load_chanlun),
        ("qpzm", load_qpzm),
        ("ninedim", load_ninedim),
        ("cross", load_cross),
    ):
        try:
            metas[sid] = fn(stocks, basis0)
            metas[sid]["ok"] = True
        except Exception as ex:  # noqa: BLE001
            metas[sid] = {"label": sid, "ok": False, "err": str(ex)[:120]}
    metas["linkage"] = {
        "label": "板块联动信号",
        "ok": True,
        "n": None,
        "note": "个股级工具（linkage_signals.py 需指定代码），无全市场扫描口径，今日不产生名单",
    }
    # T1 否决链状态（十一审）：ok=False 的源含 err（异常失败）；stale 闸门
    # 拦截的源 ok=True（设计内放行）不触发停挂——五源名单语义见
    # gate_failed_sources docstring（三十九审；P2-3 抽出）。
    veto_failed = gate_failed_sources(metas)
    veto_ok = not veto_failed

    out_rows = []
    # P3 环境闸门差异化·影子标注（2026-10-11）：qpzm stale/异常时
    # env_gate 缺失 → gate_tight=False（fail-closed，不打日级标注）。
    env_gate = (metas.get("qpzm") or {}).get("env_gate")
    gate_tight = bool(env_gate and env_gate.get("safe") is False)
    # 四十一审①：弱势日联动（fail-open）。盘前 score/prob 判弱时，
    # 名单缩到 T1 + 「T2 干净层」——T3 单信号层整体降为影子标注
    # （保留在 stocks 里带 weak_day_trim=true，页面与后验仍可见）。
    # 11-12 实证：普跌日 T3 五百多只平均比市场多跌 0.5pp，最大的负超额
    # 来源就是这一层；缩掉它是当日复盘最直接的止血。
    wm = _weak_market_state(basis0)
    weak_day = bool(wm and wm.get("weak"))
    n_sig_stale = 0
    n_env_diff = n_panic_buy = n_exempt = n_q_toxic = n_q_first = 0
    for c, e in stocks.items():
        e["n_hit"] = len({s["s"] for s in e["sources"]} - {"cross"})
        e["tier"] = tier_of(e, veto_ok, gate_tight)
        # 四十一审②③：T2 收口 —— bull 恰 1（单看多源，与 T1 的 bull>=2
        # 同一序列）+ 信号龄 <=30 交易日。bull=0 的 T2 是被否决/毒层压下来
        # 的边缘票（当日 -1.44%，且无独立验证价值），龄超的旧买点当日
        # -1.64% vs 龄内 -1.27%，都清回 T3。归因写进 sources 供页面显示。
        if e["tier"] == "T2":
            if _bull_count(e["sources"]) == 0:
                e["tier"] = "T3"
                e["t2_demote_why"] = "无看多源（否决/毒层压降的边缘票）"
            else:
                age = _sig_age_days(e, basis0)
                # 自然日 42 ≈ 交易日 30（含约 6 周末 + 假期余量）
                if age is not None and age > 42:
                    e["tier"] = "T3"
                    e["t2_demote_why"] = "信号龄 %d 天（>30 交易日）" % age
                    n_sig_stale += 1
        if weak_day and e["tier"] == "T3":
            e["weak_day_trim"] = True
        d, p, why = _env_diff_shadow(e, gate_tight)
        if d:
            e["env_diff"] = True
            n_env_diff += 1
        if p:
            e["panic_buy"] = True
            n_panic_buy += 1
        if why:
            e["env_diff_why"] = why
        # P4 豁免标注：满足豁免条件的票（无论最终 tier）随档归档，
        # 后验可统计「豁免人群」与收紧日对照层的窗口收益差。
        if _env_diff_exempt(e, gate_tight):
            e["env_diff_exempt"] = True
            n_exempt += 1
        # P5a 质量分层影子标注：q_toxic（毒层构成跟踪）/q_first_buy
        # （1买层，P5c 是否启用由后验说话）——只标注不拦截。
        qt, qf = _q_layer_marks(e)
        if qt:
            e["q_toxic"] = True
            n_q_toxic += 1
        if qf:
            e["q_first_buy"] = True
            n_q_first += 1
        out_rows.append(e)
    out_rows.sort(key=_pick_sort_key)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    basis_day, n_backfill = _backfill_quotes(stocks)
    # 基准新鲜度闸门（2026-11-04 立，fail-closed）：ashare 已有当日收盘而
    # 行情湖仍停在 D-1 时拒绝写盘，避免旧基准清单冒充当日盘后清单。
    guard_err = _basis_guard(basis_day)
    if guard_err:
        print(
            "[picks] FAIL-CLOSED 基准新鲜度闸门：%s\n"
            "  → 拒绝写入 %s（rc=%d）；检查 \\ZeroBound\\TdxSync_A_高频 17:35 任务"
            % (guard_err, OUT, BASIS_FAIL_RC),
            file=sys.stderr,
        )
        return BASIS_FAIL_RC
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
    rotation = {
        "new_in": None,
        "dropped": None,
        "retained": None,
        "vs": os.path.basename(prev_path) if prev_path else None,
    }
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
        "t1_gate": {
            "ok": veto_ok,
            "failed": veto_failed,
            "note": (
                ""
                if veto_ok
                else "关键源（否决/证据/封顶）异常失败，T1 当日停挂"
                "（共振票降 T2/T3）——三十九审五源全景闸"
            ),
        },
        # 四十一审①②③（2026-11-12）：弱势日联动 + T2 收口 + 信号龄硬过期
        "risk_gate": {
            "weak_day": weak_day,
            "weak_day_ref": wm,
            "t2_sig_stale_demoted": n_sig_stale,
            "sig_max_trade_days": SIG_MAX_TRADE_DAYS,
            "note": (
                "盘前 score<=%d 或 prob>=%d → 名单缩至 T1+T2；"
                "T2 收口为 bull=1 且信号龄<=%d 交易日"
                % (WEAK_DAY["score_max"], WEAK_DAY["prob_min"],
                   SIG_MAX_TRADE_DAYS)
            ),
        },
        "counts": {
            "total": len(out_rows),
            "T1_resonance": sum(1 for r in out_rows if r["tier"] == "T1"),
            "T2_high_value": sum(1 for r in out_rows if r["tier"] == "T2"),
            "T3_single": sum(1 for r in out_rows if r["tier"] == "T3"),
            "crowd_alert": sum(1 for r in out_rows if r["tier"] == "CROWD"),
            "rotation": rotation,
            # P3 环境闸门差异化·影子（2026-10-11）：只统计不拦截——
            # 收紧日买点/恐慌期买点两层，达标启用另议（函数 docstring）
            "env_diff_shadow": n_env_diff,
            "panic_buy_shadow": n_panic_buy,
            # P4 豁免启用（2026-10-11）：收紧日×1买×环境闸门型回避 →
            # qpzm_avoid 不再否决 T1（bull>=2 共振把关仍在）
            "env_diff_exempt": n_exempt,
            # P5a 质量分层影子（2026-10-11）：q_toxic 毒层构成跟踪（P5b
            # 已过滤其 T1 共振票，标注记录 T2 通道表现）；q_first_buy 1买
            # 层（P5c「T1 腿 1买独占」后验达标再议启用）
            "q_toxic_shadow": n_q_toxic,
            "q_first_buy_shadow": n_q_first,
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
            print(
                "  归档 %s 已存在：保留首次快照（重跑不覆写；如需重写先删该文件）"
                % os.path.basename(arch)
            )
        # qpzm 源快照独立补档（K-STOCK-121）：归档在不在都照补，
        # 回填既有 basis_day；与归档同日随 TodaysPicks 自然积累
        _snapshot_qpzm(hist_dir, basis_day)
    print(
        "  轮动(vs %s)：新进 %s / 退出 %s / 留存 %s；归档 %s"
        % (
            rotation["vs"] or "无",
            rotation["new_in"],
            rotation["dropped"],
            rotation["retained"],
            "todays_%s.json" % basis_day if basis_day else "跳过(无基准日)",
        )
    )
    print(
        "todays_picks: total=%d T1=%d T2=%d CROWD=%d T3=%d -> %s"
        % (
            doc["counts"]["total"],
            doc["counts"]["T1_resonance"],
            doc["counts"]["T2_high_value"],
            doc["counts"]["crowd_alert"],
            doc["counts"]["T3_single"],
            OUT,
        )
    )
    if not veto_ok:
        # 双通道告警（十二审）：stdout 给运维日志；stderr 给 alert_fail——
        # start_todays_picks.ps1 只把 err.log 传给 alert_fail，stdout 警告
        # 收报人看不到，exit=3 推送将无法区分"停挂"与"崩溃"。
        _hold = (
            "[T1停挂] 否决源异常失败: %s —— 同批卖点/weak/qpzm回避否决"
            "失效，今日不产 T1（共振票降 T2/T3）；exit=3 触发 alert_fail 推送"
            % ",".join(veto_failed)
        )
        print("  " + _hold)
        print(_hold, file=sys.stderr)
    print("  行情回填：基准 %s，回填 %d 只" % (basis_day or "无", n_backfill))
    for sid, m in metas.items():
        print(
            "  %-8s ok=%s n=%s %s"
            % (sid, m.get("ok"), m.get("n"), (m.get("err") or "")[:60])
        )
    # P2-2（2026-10-11）：rc=0 路径的源级降级（cross 失败/stale 拦截）
    # 推送飞书——此前页面有卡、无人被通知；停挂路径 rc=3 已走 alert_fail。
    _push_source_alert(metas, veto_failed, basis_day)
    return veto_ok  # False → __main__ 以退出码 3 结束（ran-but-degraded 警报）


if __name__ == "__main__":
    # argparse 化：此前无参解析，--help 会直接触发真实聚合写盘（副作用 bug）
    import argparse

    _ap = argparse.ArgumentParser(
        description="聚合六策略选股产物 → portal/data/todays_picks.json"
        "（个股详情见 build_pick_details.py）"
    )
    _ap.parse_args()
    # 退出码口径（十一审）：0=正常；3=跑完但 T1 停挂（否决源 err，清单已
    # 落盘仅缺 T1 层）——start_todays_picks.ps1 透传给 alert_fail 推送。
    # 4=基准新鲜度闸门拒绝写盘（2026-11-04 立，见 _basis_guard）。
    _res = main()
    sys.exit(0 if _res is True else (3 if _res is False else _res))
