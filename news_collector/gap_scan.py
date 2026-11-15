# -*- coding: utf-8 -*-
"""池内标的 × 数据源 缺口发现器（OSINT1 马良 · 只读发现，不做研判）

职责定位（老田 2026-11-15 明确）：
  「随时随地在发现我们的池子里面哪些票应该新增数据了、哪些票应该补充数据了。
    哪些票的新闻有欠缺，哪些票贴吧有欠缺，还有什么地方有此类型数据。」
  本脚本是这句话的可执行形态 —— 「发现与搜索」能力，不是「生产与研判」能力。
  输出止于「数据 + 数字 + 判据」，是否扩源/是否调阈值归上级（L2 马超配准）。

回答五个问题：
  1) 哪些票在**队列内却无任何数据**（MISSING / EMPTY）—— 采集侧该修的；
  2) 哪些票**在队列内、但源端内容超过阈值无新增**（STALE_Q）—— 该新增了；
  3) 哪些票**不在该源队列里**（NO_QUEUE / STALE_OFFQ / THIN_OFFQ）—— 扩源决策；
  4) 哪些票**历史太薄**（THIN_*）—— 该补历史；
  5) 哪些「缺」是**合法的**（NA_MARKET 源不覆盖该市场 / WLT 白名单已核验空）。

口径纪律（与《OSINT 方法论结合方案》、collect_sla.py 同判据）：
  - **只读**：不改任何采集产物、不触网、不启浏览器；唯一写入本脚本自己的
    `data/gap_scan.json`、`data/gap_scan.md`、`data/_gap_index.json`（索引缓存）。
  - **假缺口必须排除**（2026-11-15 首跑实测校准，三个坑）：
      a) 跨市场：irm 实测只落 00/30（479 文件全深市）、sse 只落 60/68（209 文件全沪市）、
         taoguba 无 92 前缀、qianping 不采北交所 → 一律 NA_MARKET，不计缺口；
      b) 日度快照：qianping 每码在快照里天然只有 1 条，套 `thin_n` 会把 845 只误判「太薄」→
         该源跳过条数判定，只判「在不在最新快照」；
      c) 队列外 ≠ 断供：池 887 只、日常队列仅 65 只（irm 实测 29 / sse 28 / taoguba 65 /
         guba 65 / qianping 57）→ 队列外的「陈旧」是**存量遗留**，属扩源决策，
         不能与队列内的「该新增」混报（否则 400+ 条噪音淹没真缺口）。
  - **append-on-new 语义**：各源均为「有新帖才追加」，故 `max_ts` 是**内容最后新增时间**，
    不是「上次尝试时间」。STALE 只能说「源端期间无新内容」，不能说「没去采」——
    要区分请交叉 `collect_stale_probe.py`（接口侧最新时间）或日志 `last_run_ts`。
  - **阈值不裁定**：stale_hours / thin_n 全部 DEFAULT，用 CLI 覆盖；改阈值归上级。
  - **NO_QUEUE ≠ 故障**：属扩源决策（L2 归马超配准），本脚本不自动处理。

用法：
  python gap_scan.py                          # 表格 + 落 JSON/MD
  python gap_scan.py --stale-hours 48 --thin 3 --top 30
  python gap_scan.py --json                   # 只打 JSON（供管道）
  python gap_scan.py --only 002983 600515     # 单票/多票体检
  python gap_scan.py --no-cache               # 忽略索引缓存全量重建
退出码：0=正常；2=读盘错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")

OUT_JSON = os.path.join(HERE, "data", "gap_scan.json")
OUT_MD = os.path.join(HERE, "data", "gap_scan.md")
IDX_F = os.path.join(HERE, "data", "_gap_index.json")
INTEL_LOG = os.path.join(HERE, "logs", "target_queue_intel_daily.log")

if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULTS = {
    "stale_hours": 48.0,   # DEFAULT 经验值：两个交易日无新增内容
    "thin_n": 3,           # DEFAULT：少于 3 条视为历史太薄
    "top": 25,
}

# ---------------------------------------------------------------- 池子声明
# weight 只用于缺口排序优先级（主选 > 影子/候选 > 宽池），不代表投资价值判断。
POOL_SPECS = [
    {"id": "chanlun_actionable", "file": "target_queue_chanlun.json",
     "path": "actionable", "weight": 2, "label": "缠论 actionable（每日先验池）"},
    {"id": "chanlun_all", "file": "target_queue_chanlun.json",
     "path": "all", "weight": 1, "label": "缠论全池"},
    {"id": "houfeng_pick", "file": "houfeng_watchlist.json",
     "path": "picks", "weight": 3, "label": "侯风选股30（关闸期影子池）"},
    {"id": "houfeng_cand164", "file": "houfeng_candidates_164.json",
     "path": "candidates", "weight": 1, "label": "侯风164候选池（冻结快照）"},
    {"id": "cohorts_main", "file": "cohorts_watchlist.json",
     "path": "pools.main.codes", "weight": 3, "label": "两池·主选（三条全中）"},
    {"id": "cohorts_auto", "file": "cohorts_watchlist.json",
     "path": "pools.auto.codes", "weight": 2, "label": "两池·auto 影子池"},
    {"id": "kstock_held", "file": "kstock_watchlist.json",
     "path": "held", "weight": 3, "label": "k_stock 实持仓"},
    {"id": "kstock_orders", "file": "kstock_watchlist.json",
     "path": "order_universe", "weight": 1, "label": "k_stock 订单宇宙"},
    {"id": "unified40", "file": "unified40_watchlist.json",
     "path": "all", "weight": 2, "label": "四组统一总榜40（部分种子）"},
    {"id": "s12", "file": "../research/shadow/s12/basket_20261106.json",
     "path": "basket", "weight": 2, "label": "蒋琬S12共识20"},
]

# ---------------------------------------------------------------- 源声明
# `market` 是**采集器真实覆盖范围**（实测口径，见模块 docstring），不是期望范围。
# `queue` 是该源的采集队列口径；None = 全市场自采源（按票打标，不排逐票队列）。
SOURCES = [
    {"id": "irm", "label": "互动易(官方问答)", "kind": "jsonl", "market": "sz",
     "dir": os.path.join(DATA, "irm"), "queue": "chanlun_actionable",
     "ts_fields": ["ts", "update_date"],
     "data_class": "官方问答：投资者提问与董秘回复（tier1 官方口径）"},
    {"id": "sse", "label": "上证e互动(官方问答)", "kind": "jsonl", "market": "sh",
     "dir": os.path.join(DATA, "sse"), "queue": "chanlun_actionable",
     "ts_fields": ["ts"],
     "data_class": "官方问答：沪市投资者提问与回复（tier1 官方口径）"},
    {"id": "taoguba", "label": "淘股吧(论坛)", "kind": "jsonl", "market": "a_nobj",
     "dir": os.path.join(DATA, "taoguba"), "queue": "chanlun_actionable",
     "ts_fields": ["ts"],
     "data_class": "短线游资论坛帖/题材发酵线索"},
    {"id": "guba", "label": "东财股吧(论坛)", "kind": "jsonl", "market": "all",
     "dir": os.path.join(ROOT, "quant_backtest", "data", "guba"),
     "queue": "chanlun_actionable", "ts_fields": ["ts"],
     "data_class": "散户论坛帖/情绪与异动线索（含北交所）"},
    {"id": "xq", "label": "雪球timeline(社媒)", "kind": "jsonl", "market": "all",
     "dir": os.path.join(HERE, "data", "xq_timeline"), "queue": "registry_xq",
     "ts_fields": ["ts"],
     "data_class": "社媒长文/观点与产业链拆解（含北交所）"},
    {"id": "qianping", "label": "千股千评(量化日度)", "kind": "qianping", "market": "non_bj",
     "dir": os.path.join(HERE, "data", "qianping"), "queue": "chanlun_actionable",
     "ts_fields": ["trade_day"],
     "data_class": "主力成本/资金分布/关注度（全市场日度快照）"},
    {"id": "news", "label": "新闻流(自采全市场)", "kind": "news_db", "market": "all",
     "dir": os.path.join(HERE, "news.db"), "queue": None,
     "ts_fields": ["ts"],
     "data_class": "全市场快讯/公告转载（按票打标，命中即留痕）"},
]

MARKET_FILTERS = {
    "sh": ("60", "68"),
    "sz": ("00", "30"),
    "a_nobj": ("00", "30", "60", "68"),
    "all": None,       # 不筛（含 92 北交所）
    "non_bj": "non_bj",
}

# 状态机：区分「队列内」与「队列外」是全部价值所在。
STATE_NA_MARKET = "NA_MARKET"    # 源不覆盖该市场 —— 不是缺口
STATE_NA_ASHARE = "NA_ASHARE"    # 非 A 股 —— A 股源不适用
STATE_WLT = "WLT"                # 白名单已核验的合法空
STATE_OK = "OK"
STATE_MISSING = "MISSING"        # 队列内、源支持、无任何数据
STATE_EMPTY = "EMPTY"            # 队列内、有文件但 0 条
STATE_STALE_Q = "STALE_Q"        # 队列内、有内容但超阈值无新增 → 该新增
STATE_THIN_Q = "THIN_Q"          # 队列内、条数 < thin_n → 该补历史
STATE_NO_QUEUE = "NO_QUEUE"      # 队列外、无数据 → 扩源决策
STATE_STALE_OFFQ = "STALE_OFFQ"  # 队列外、有内容但超阈值 → 存量遗留
STATE_THIN_OFFQ = "THIN_OFFQ"    # 队列外、条数薄 → 存量薄
STATE_NO_NEWS = "NO_NEWS"        # 新闻流：超阈值无命中（内容事实，非断供）

GAP_STATES = {STATE_MISSING, STATE_EMPTY, STATE_STALE_Q, STATE_THIN_Q,
              STATE_NO_QUEUE, STATE_STALE_OFFQ, STATE_THIN_OFFQ, STATE_NO_NEWS}

SEVERITY = {
    STATE_MISSING: 3.0,       # 队列内承诺了却没采到 —— 最严重
    STATE_EMPTY: 2.5,
    STATE_STALE_OFFQ: 2.0,    # 有存量但停更且不在队列 —— 真缺口但不可执行
    STATE_NO_QUEUE: 1.8,
    STATE_STALE_Q: 1.5,       # 在采、只是本期无新内容
    STATE_THIN_OFFQ: 1.4,
    STATE_THIN_Q: 1.2,
    STATE_NO_NEWS: 0.5,       # 内容事实，低权重
}

BUCKETS = [
    ("A_应采未采", (STATE_MISSING, STATE_EMPTY),
     "在队列内、源支持该票，却没有任何数据 —— 采集侧该修"),
    ("B_队列外未覆盖", (STATE_NO_QUEUE, STATE_STALE_OFFQ, STATE_THIN_OFFQ),
     "源支持该票，但不在该源采集队列（存量遗留/从未排上）—— 扩源决策，归上级配准"),
    ("C_队列内该补", (STATE_STALE_Q, STATE_THIN_Q),
     "在采队列内，但源端超阈值无新增 / 条数太薄 —— 该新增或该补历史"),
    ("D_新闻空窗", (STATE_NO_NEWS,),
     "全市场新闻流对该票超阈值无命中 —— 内容事实，非断供"),
]
STATE2BUCKET = {s: name for name, states, _ in BUCKETS for s in states}

# 候选源方向（**未接入、未核验可得性、不擅自接入**）。
# 用途：回答「还有什么地方有此类型数据」，供上级决定是否走 L2 新源接入。
# 可得性/合规/成本三列均为**待核验假设**，不是结论；接入前须实测接口与授权。
CANDIDATE_SOURCES = [
    {"class": "公告原文全文", "name": "巨潮资讯网(cninfo) 公告全文",
     "fills": "news 源只有转载标题，缺原文；官方一手、可做法核验底座",
     "access": "公开页面/公开接口，待核验", "risk": "低（法定披露渠道）"},
    {"class": "调研纪要", "name": "巨潮「投资者关系活动记录表」",
     "fills": "irm/sse 只有公开问答，缺机构调研问答；官方一手",
     "access": "公开，待核验", "risk": "低"},
    {"class": "交易行为", "name": "龙虎榜 / 大宗交易 / 融资融券",
     "fills": "现有 7 源全为文本舆情，无结构化资金面；游资席位可归因",
     "access": "交易所公开数据/东财接口，待核验", "risk": "低"},
    {"class": "卖方预期", "name": "券商研报与评级变动",
     "fills": "缺卖方一致预期与调整方向（预期差来源）",
     "access": "东财研报中心公开列表，全文多需授权", "risk": "中（全文版权）"},
    {"class": "互动问答增量", "name": "同花顺「问董秘」/ 东财「问董秘」",
     "fills": "补齐 irm/sse 未覆盖的第三方问答（口径非官方，需降级标注）",
     "access": "待核验", "risk": "中（tier3，需与官方区分）"},
    {"class": "先行指标", "name": "专利 / 招投标 / 招聘",
     "fills": "领先于财报的产能与订单信号（《OSINT 方法论》先行指标优于结果指标）",
     "access": "工商与政务公开，爬取成本中，待核验", "risk": "中"},
    {"class": "工商穿透", "name": "企查查 / 天眼查",
     "fills": "关联方、股权穿透、对外投资（公告外的关系网）",
     "access": "付费 API", "risk": "中（授权与转售限制）"},
    {"class": "短社媒", "name": "微博 / 抖音 / 知乎",
     "fills": "散户情绪扩散最快的一层，现无覆盖",
     "access": "无公开接口、需登录态，待核验", "risk": "高（授权与合规）"},
]


RE_TS_CACHE = {}


def _re_for(field):
    if field not in RE_TS_CACHE:
        RE_TS_CACHE[field] = re.compile(
            r'"%s"\s*:\s*"([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:]{4,8})"' % field)
    return RE_TS_CACHE[field]


def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    if len(s) == 10:
        s = s + " 00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _code_of(it, field="code"):
    if isinstance(it, str):
        v = it
    elif isinstance(it, dict):
        v = it.get(field) or it.get("ts_code") or it.get("symbol") or it.get("stock") or ""
    else:
        return ""
    v = str(v).split(".")[0].strip()
    return v.zfill(6) if v else ""


def _codes6(items, field="code"):
    out = set()
    for it in items or []:
        c = _code_of(it, field)
        if c:
            out.add(c)
    return out


def _dig(obj, path):
    if path == "__list__":
        return obj if isinstance(obj, list) else []
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return []
        cur = cur.get(part)
        if cur is None:
            return []
    return cur if isinstance(cur, list) else []


def load_pools():
    """返回 ({code6: {"pools": [...], "weight": n, "names": [...]}}, 统计, 异常)。"""
    codes, stats, problems = {}, {}, []
    for spec in POOL_SPECS:
        fp = os.path.join(HERE, spec["file"])
        if not os.path.exists(fp):
            problems.append("%s: 文件缺失 %s" % (spec["id"], spec["file"]))
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                obj = json.load(f)
        except Exception as e:
            problems.append("%s: 读取失败 %s" % (spec["id"], e))
            continue
        items = _dig(obj, spec["path"])
        got = _codes6(items)
        stats[spec["id"]] = {"n": len(got), "label": spec["label"], "weight": spec["weight"]}
        for c in got:
            slot = codes.setdefault(c, {"pools": [], "weight": 0, "names": []})
            slot["pools"].append(spec["id"])
            slot["weight"] = max(slot["weight"], spec["weight"])
        for it in items:
            if isinstance(it, dict):
                c = _code_of(it)
                nm = str(it.get("name", "") or "").strip()
                if c in codes and nm and nm not in codes[c]["names"]:
                    codes[c]["names"].append(nm)
    return codes, stats, problems


def _file_stat(fp, ts_fields, cache, fresh):
    """单文件 → (n_lines, max_ts, min_ts)。带 (size, mtime) 缓存。"""
    try:
        st = os.stat(fp)
    except OSError:
        return 0, None, None
    key = os.path.abspath(fp)
    sig = "%d|%d" % (st.st_size, int(st.st_mtime))
    if not fresh:
        hit = cache.get(key)
        if hit and hit.get("sig") == sig:
            return hit["n"], hit.get("max_ts"), hit.get("min_ts")
    n, mx, mn = 0, None, None
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                n += 1
                for fld in ts_fields:
                    m = _re_for(fld).search(line)
                    if not m:
                        continue
                    dt = _parse_dt(m.group(1))
                    if dt is None:
                        continue
                    if mx is None or dt > mx:
                        mx = dt
                    if mn is None or dt < mn:
                        mn = dt
    except OSError:
        return 0, None, None
    cache[key] = {"sig": sig, "n": n,
                  "max_ts": mx.strftime("%Y-%m-%d %H:%M:%S") if mx else None,
                  "min_ts": mn.strftime("%Y-%m-%d %H:%M:%S") if mn else None}
    return n, cache[key]["max_ts"], cache[key]["min_ts"]


def build_index(src, cache, fresh):
    idx, tot_bytes, empties = {}, 0, 0
    d = src["dir"]
    if not os.path.isdir(d):
        return idx, {"n_files": 0, "total_bytes": 0, "n_empty": 0}
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return idx, {"n_files": 0, "total_bytes": 0, "n_empty": 0}
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        code = name[:-6].split(".")[0].zfill(6)
        if not (len(code) == 6 and code.isdigit()):
            continue
        fp = os.path.join(d, name)
        try:
            sz = os.path.getsize(fp)
        except OSError:
            continue
        tot_bytes += sz
        n, mx, mn = _file_stat(fp, src["ts_fields"], cache, fresh)
        if n == 0:
            empties += 1
        idx[code] = {"n": n, "max_ts": mx, "min_ts": mn, "bytes": sz}
    return idx, {"n_files": len(idx), "total_bytes": tot_bytes, "n_empty": empties}


def build_qianping_index(src):
    """千股千评：最新交易日快照含哪些码（每码天然 1 条，条数不参与 thin 判定）。"""
    d = src["dir"]
    files = []
    if os.path.isdir(d):
        files = sorted(n for n in os.listdir(d) if n.startswith("qianping_") and n.endswith(".jsonl"))
    if not files:
        return {}, {"snapshot": None, "trade_day": None, "n_codes": 0}
    latest = files[-1]
    codes, trade_day = {}, None
    with open(os.path.join(d, latest), "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            c = str(o.get("code", "")).split(".")[0].zfill(6)
            if len(c) == 6:
                trade_day = trade_day or str(o.get("trade_day", "") or "") or None
                codes[c] = {"n": 1, "max_ts": str(o.get("trade_day", "") or "") or None,
                            "min_ts": None, "bytes": 0}
    return codes, {"snapshot": latest, "trade_day": trade_day, "n_codes": len(codes)}


def build_news_index(src, now=None):
    """新闻流：按 stocks 字段逐票统计命中数与最后命中时间。

    另记 `n48` / `n7d`（窗口内命中数）：48h 窗口跨周末时会被假日效应污染，
    7 日窗口是更稳的「该票有没有新闻覆盖」指标，两者并列供判读。
    """
    import sqlite3
    idx = {}
    db = src["dir"]
    now = now or datetime.now()
    d48 = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    d7 = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.exists(db):
        return idx, {"rows": 0}
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
    try:
        for ts, stocks in con.execute(
                "select ts, stocks from news where stocks is not null "
                "and stocks <> '[]' and stocks <> ''"):
            try:
                arr = json.loads(stocks)
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            for s in arr:
                c = str(s).split(".")[0].strip().zfill(6)
                if not (len(c) == 6 and c.isdigit()):
                    continue
                slot = idx.setdefault(c, {"n": 0, "max_ts": None, "min_ts": None,
                                          "bytes": 0, "n48": 0, "n7d": 0})
                slot["n"] += 1
                t = str(ts or "")
                if t >= d48:
                    slot["n48"] += 1
                if t >= d7:
                    slot["n7d"] += 1
                if ts and (slot["max_ts"] is None or t > slot["max_ts"]):
                    slot["max_ts"] = t
    finally:
        con.close()
    return idx, {"rows": len(idx)}


def _observed_queue():
    """从当日采集日志解析实测逐源只数（`→ xxx_collector.py N 只`）。

    作用：把「声明队列 65 只」与「实测 29/28/65/65 只」并列，防止用声明口径
    当实际覆盖。解析失败一律 None（fail-soft，不猜）。
    """
    out = {}
    if not os.path.exists(INTEL_LOG):
        return out
    try:
        with open(INTEL_LOG, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return out
    pat = re.compile(r"→\s*(\w+)\.py\s+(\d+)\s+只")
    seen = {}
    for m in pat.finditer(txt):
        seen.setdefault(m.group(1), []).append(int(m.group(2)))
    for k, v in seen.items():
        out[k] = sum(v) if k in ("taoguba_collector", "guba_pw") else max(v)
    return out


def _weekend_slack(now, hours=24.0):
    """过去 3 天（含今天）每出现一个周末日 +hours —— 同 collect_sla 判例。

    理由：48h 窗口跨周末时，源端本来就不产生内容，把「周末无新帖」判成
    「该新增」是假缺口。11-15（周日）实测：新闻 7 日覆盖 2575 只票，
    48h 只有 96 只命中，两者差距几乎全部来自周末效应。
    """
    extra = 0.0
    for back in range(0, 3):
        if (now - timedelta(days=back)).weekday() >= 5:
            extra += hours
    return extra


def market_ok(code, market):
    if market == "all":
        return True
    if market == "non_bj":
        return not code.startswith("92")
    pref = MARKET_FILTERS.get(market)
    return bool(pref) and code[:2] in pref


def is_ashare(code):
    if not (len(code) == 6 and code.isdigit()):
        return False
    return code[:2] in ("00", "30", "60", "68", "92", "83", "87", "43")


def _wl_lookup(slot, code):
    if not slot:
        return None
    ent = slot["exact"].get(code)
    if ent:
        return ent
    for pref, e in slot.get("prefix") or []:
        if code.startswith(pref):
            return e
    return None


def load_whitelist(now):
    """复用 collect_sla 的白名单实现（同格式、同到期语义），失败返回空。"""
    try:
        import collect_sla as sla
        return sla._load_gap_whitelist(now)[0]
    except Exception:
        return {}


def load_queue_sets():
    out = {"chanlun_actionable": set(), "registry_xq": set()}
    try:
        import collect_sla as sla
        out["chanlun_actionable"] = sla._load_chanlun(True)[0]
        out["registry_xq"] = sla._load_registry_all()[0]
    except Exception:
        pass
    return out


def judge(code, src, rec, queue_sets, wl, now, stale_h, thin_n):
    """单票单源判定。顺序即优先级：市场 → 数据 → 队列 → 新鲜度 → 厚度。"""
    det = {"n": 0, "last": None, "age_h": None, "in_queue": None}
    if not is_ashare(code):
        return STATE_NA_ASHARE, det
    if not market_ok(code, src["market"]):
        return STATE_NA_MARKET, det

    q = src["queue"]
    if q == "chanlun_actionable":
        det["in_queue"] = code in queue_sets["chanlun_actionable"]
    elif q == "registry_xq":
        det["in_queue"] = code in queue_sets["registry_xq"]

    if rec is not None:
        det["n"] = rec.get("n") or 0
        det["last"] = rec.get("max_ts")
        if det["last"]:
            dt = _parse_dt(det["last"])
            if dt:
                det["age_h"] = round((now - dt).total_seconds() / 3600.0, 1)
        if det["n"] == 0:
            return (STATE_EMPTY if det["in_queue"] else STATE_NO_QUEUE), det

        # 新闻流：全市场自采、按票打标，无命中是内容事实而非断供
        if src["kind"] == "news_db":
            if det["age_h"] is not None and det["age_h"] > stale_h:
                return STATE_NO_NEWS, det
            return STATE_OK, det

        # 日度全市场快照：命中即新鲜，条数不谈厚薄
        if src["kind"] == "qianping":
            return STATE_OK, det

        stale = det["age_h"] is not None and det["age_h"] > stale_h
        thin = det["n"] < thin_n
        if stale:
            det["also_thin"] = thin
            return (STATE_STALE_Q if det["in_queue"] else STATE_STALE_OFFQ), det
        if thin:
            return (STATE_THIN_Q if det["in_queue"] else STATE_THIN_OFFQ), det
        return STATE_OK, det

    # 无数据
    if src["kind"] == "news_db":
        # 新闻流全市场自采、按票打标，无命中即「该票期间零新闻」——不是断供、不是没排队
        return STATE_NO_NEWS, det
    if det["in_queue"] is False:
        return STATE_NO_QUEUE, det
    if det["in_queue"] is None:      # 无逐票队列的口径（不应发生，兜底归扩源）
        return STATE_NO_QUEUE, det
    ent = _wl_lookup(wl.get(src["id"]), code)
    if ent:
        det["wl_reason"] = ent.get("reason", "")
        det["wl_kind"] = ent.get("kind", "")
        return STATE_WLT, det
    return STATE_MISSING, det


def apply_only_filter(pools, only):
    """`--only` 的裁剪口径：返回 (子集, 池外代码集)。

    语义是「体检这几只票」，不是「体检这几只里恰好落在池内的那些」。
    池外票必须补一条空 ctx 并打标记，否则会被静默丢弃——那样「七个源
    一个文件都没有」会被读成「体检通过」，是比报错危险得多的失败模式
    （11-16 判例：10 只清单里 8 只池外，首版工具只吐出 2 只，其余无声消失）。
    """
    if not only:
        return pools, set()
    want = {str(x).split(".")[0].zfill(6) for x in only}
    pool_out = {c for c in want if c not in pools}
    merged = {k: v for k, v in pools.items() if k in want}
    for c in sorted(pool_out):
        merged[c] = {"pools": [], "weight": 0, "names": []}
    return merged, pool_out


def build_report(now=None, stale_h=None, thin_n=None, fresh_cache=False, only=None,
                 no_slack=False):
    global _cache_snapshot
    now = now or datetime.now()
    stale_raw = DEFAULTS["stale_hours"] if stale_h is None else stale_h
    thin_n = DEFAULTS["thin_n"] if thin_n is None else thin_n
    slack = 0.0 if no_slack else _weekend_slack(now, 24.0)
    stale_h = stale_raw + slack

    pools, pool_stats, problems = load_pools()
    queues = load_queue_sets()
    wl = load_whitelist(now)
    observed = _observed_queue()

    cache, idxs, src_stats = {}, {}, {}
    if not fresh_cache and os.path.exists(IDX_F):
        try:
            with open(IDX_F, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    for src in SOURCES:
        sid = src["id"]
        if src["kind"] == "jsonl":
            idx, st = build_index(src, cache, fresh_cache)
        elif src["kind"] == "qianping":
            idx, st = build_qianping_index(src)
        else:
            idx, st = build_news_index(src, now)
        idxs[sid] = idx
        src_stats[sid] = st
    _cache_snapshot = cache

    # --only 的语义是「体检这几只票」，不是「体检这几只里在池内的那些」。
    # 池外票必须显式补空 entry 并打 pool_out 标记（详见 apply_only_filter 注释）。
    pools, pool_out = apply_only_filter(pools, only)

    all_states = (STATE_OK, STATE_MISSING, STATE_EMPTY, STATE_STALE_Q, STATE_THIN_Q,
                  STATE_NO_QUEUE, STATE_STALE_OFFQ, STATE_THIN_OFFQ, STATE_NO_NEWS,
                  STATE_WLT, STATE_NA_MARKET, STATE_NA_ASHARE)
    by_code = {}
    agg = {s["id"]: {k: 0 for k in all_states} for s in SOURCES}

    for code, ctx in pools.items():
        slot = {"pools": ctx["pools"], "weight": ctx["weight"],
                "pool_out": code in pool_out,
                "name": (ctx["names"][0] if ctx["names"] else ""), "srcs": {}}
        score, gaps = 0.0, []
        for src in SOURCES:
            sid = src["id"]
            st, det = judge(code, src, idxs[sid].get(code), queues, wl, now, stale_h, thin_n)
            agg[sid][st] += 1
            det["state"] = st
            slot["srcs"][sid] = det
            if st in GAP_STATES:
                gaps.append(sid)
                score += SEVERITY.get(st, 1.0) * ctx["weight"]
        slot["gap_score"] = round(score, 2)
        slot["gaps"] = gaps
        by_code[code] = slot

    ranking = sorted(
        ({"code": c, "name": v["name"], "pools": v["pools"], "weight": v["weight"],
          "gap_score": v["gap_score"], "gaps": v["gaps"],
          "detail": {k: {"state": d["state"], "n": d.get("n"), "last": d.get("last"),
                         "age_h": d.get("age_h")}
                     for k, d in v["srcs"].items() if d["state"] in GAP_STATES}}
         for c, v in by_code.items() if v["gaps"] and not v.get("pool_out")),
        key=lambda r: (-r["gap_score"], r["code"]))

    n_pool = len(pools)
    src_rows = []
    for src in SOURCES:
        sid = src["id"]
        a = agg[sid]
        na = a[STATE_NA_MARKET] + a[STATE_NA_ASHARE]
        applicable = n_pool - na
        q_gap = a[STATE_STALE_Q] + a[STATE_THIN_Q]
        offq = a[STATE_NO_QUEUE] + a[STATE_STALE_OFFQ] + a[STATE_THIN_OFFQ]
        must = a[STATE_MISSING] + a[STATE_EMPTY]
        src_rows.append({
            "id": sid, "label": src["label"], "kind": src["kind"],
            "data_class": src["data_class"],
            "dir": os.path.relpath(src["dir"], ROOT).replace("\\", "/"),
            "queue": src["queue"],
            "observed_queue": observed.get(
                {"irm": "irm_collector", "sse": "sse_collector", "taoguba": "taoguba_collector",
                 "guba": "guba_pw", "qianping": "qianping_collector", "xq": "xq_stock_timeline",
                 "news": "news_collector"}.get(sid, sid)),
            "pool_n": n_pool, "applicable": applicable, "na": na,
            "ok": a[STATE_OK], "wlt": a[STATE_WLT],
            "must_n": must, "q_gap_n": q_gap, "offq_n": offq,
            "no_news_n": a[STATE_NO_NEWS],
            "missing": a[STATE_MISSING], "empty": a[STATE_EMPTY],
            "no_queue": a[STATE_NO_QUEUE], "stale_q": a[STATE_STALE_Q],
            "thin_q": a[STATE_THIN_Q], "stale_offq": a[STATE_STALE_OFFQ],
            "thin_offq": a[STATE_THIN_OFFQ],
            "gap_n": must + q_gap + offq + a[STATE_NO_NEWS],
            "gap_pct": (round(100.0 * (must + q_gap + offq) / applicable, 1) if applicable else None),
            "files": src_stats[sid].get("n_files", src_stats[sid].get("rows",
                                                                     src_stats[sid].get("n_codes"))),
            "total_mb": round(src_stats[sid].get("total_bytes", 0) / 1048576.0, 1),
            "snapshot": src_stats[sid].get("snapshot"),
            "trade_day": src_stats[sid].get("trade_day"),
        })

    buckets = {name: [] for name, _s, _d in BUCKETS}
    for r in ranking:
        for sid, d in r["detail"].items():
            b = STATE2BUCKET.get(d["state"])
            if not b:
                continue
            buckets[b].append({"code": r["code"], "name": r["name"], "src": sid,
                               "state": d["state"], "n": d["n"], "last": d["last"],
                               "age_h": d["age_h"], "pools": r["pools"],
                               "score": r["gap_score"]})
    for k in buckets:
        buckets[k].sort(key=lambda x: (-x["score"], x["code"]))

    # 按池聚合：哪条策略线的票数据最欠缺（回答「哪个池该优先补」）
    pool_rows = []
    for spec in POOL_SPECS:
        pid = spec["id"]
        members = [c for c, v in by_code.items() if pid in v["pools"]]
        if not members:
            continue
        row = {"id": pid, "label": spec["label"], "n": len(members),
               "avg_score": round(sum(by_code[c]["gap_score"] for c in members) / len(members), 2),
               "src_gap": {}}
        for src in SOURCES:
            sid = src["id"]
            appl = [c for c in members
                    if by_code[c]["srcs"][sid]["state"] not in (STATE_NA_MARKET, STATE_NA_ASHARE)]
            g = [c for c in appl if by_code[c]["srcs"][sid]["state"] in GAP_STATES]
            zero = [c for c in appl if by_code[c]["srcs"][sid]["state"]
                    in (STATE_MISSING, STATE_EMPTY, STATE_NO_QUEUE)]
            row["src_gap"][sid] = {
                "applicable": len(appl), "gap": len(g), "zero_cov": len(zero),
                "pct": (round(100.0 * len(g) / len(appl), 1) if appl else None),
                "zero_pct": (round(100.0 * len(zero) / len(appl), 1) if appl else None)}
        pool_rows.append(row)
    pool_rows.sort(key=lambda r: -r["avg_score"])

    # 新闻源覆盖口径：全市场打标票数 vs 池内命中（回答「新闻能覆盖多宽」）
    nidx = idxs.get("news") or {}
    news_cover = {
        "market_all": len(nidx),
        "market_7d": sum(1 for v in nidx.values() if v.get("n7d")),
        "market_48h": sum(1 for v in nidx.values() if v.get("n48")),
        "pool_n": len(by_code),
        "pool_7d": sum(1 for c in by_code if (nidx.get(c) or {}).get("n7d")),
        "pool_48h": sum(1 for c in by_code if (nidx.get(c) or {}).get("n48")),
        "pool_never": sum(1 for c in by_code if not (nidx.get(c) or {}).get("n")),
    }

    return {
        "asof": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": {"stale_hours": stale_h, "stale_hours_raw": stale_raw,
                   "weekend_slack_h": slack, "thin_n": thin_n,
                   "note": "阈值均为 DEFAULT 经验值，非裁定；周末日自动 +24h 松弛"},
        "news_cover": news_cover,
        "pool_stats": pool_stats,
        "pool_total": n_pool,
        "problems": problems,
        "queue_sets": {k: len(v) for k, v in queues.items()},
        "observed_queue": observed,
        "sources": src_rows,
        "pool_rows": pool_rows,
        "candidate_sources": CANDIDATE_SOURCES,
        "buckets": {k: (v if k == "B_队列外未覆盖" else v[:40]) for k, v in buckets.items()},
        "bucket_n": {k: len(v) for k, v in buckets.items()},
        "ranking": ranking[:80],
        "by_code": by_code,
        "pool_out": sorted(pool_out),
        "cache_files": len(cache),
    }


# ---------------------------------------------------------------- 渲染
STATE_LABEL = {
    STATE_OK: "有", STATE_MISSING: "队列内无数据", STATE_EMPTY: "队列内空文件",
    STATE_STALE_Q: "队列内该新增", STATE_THIN_Q: "队列内太薄",
    STATE_NO_QUEUE: "未排上队列", STATE_STALE_OFFQ: "队列外停更",
    STATE_THIN_OFFQ: "队列外太薄", STATE_NO_NEWS: "新闻空窗",
    STATE_WLT: "合法空", STATE_NA_MARKET: "源不覆盖", STATE_NA_ASHARE: "非A股",
}


def render_md(rep):
    L = []
    p = rep["params"]
    L.append("# 池内标的 × 数据源 缺口扫描（OSINT1 马良 · 只读发现）")
    L.append("")
    L.append("- 时点：`%s`　池内标的：**%d** 只" % (rep["asof"], rep["pool_total"]))
    L.append("- 阈值：`stale_hours=%s`（原始 %s%s）、`thin_n=%s`（DEFAULT 经验值，非裁定）"
             % (p["stale_hours"], p["stale_hours_raw"],
                ("，周末松弛 +%sh" % p["weekend_slack_h"]) if p.get("weekend_slack_h") else "",
                p["thin_n"]))
    L.append("- 队列口径：缠论 actionable **%d** 只、雪球 registry 并集 **%d** 只"
             % (rep["queue_sets"].get("chanlun_actionable", 0),
                rep["queue_sets"].get("registry_xq", 0)))
    L.append("")
    L.append("**判读指南（三个不可混报的区分）**")
    L.append("")
    L.append("1. `队列内` vs `队列外`：池子远大于采集队列。队列内「没数据/停更」是**可执行的缺陷**；"
             "队列外「停更/太薄」是**存量遗留**，只能靠扩源解决（L2 决策）。")
    L.append("2. `STALE` 语义：各源 append-on-new，`max_ts` 是**内容最后新增**，"
             "不等于上次尝试。STALE 只说「源端期间无新内容」。")
    L.append("3. `NA_MARKET`：irm 只落深市、sse 只落沪市、taoguba 无北交所、qianping 不采北交所 —— "
             "跨市场**不计缺口**。")
    L.append("")
    if rep["problems"]:
        L.append("**读取异常**：" + "；".join(rep["problems"]))
        L.append("")

    L.append("## 一、源级汇总")
    L.append("")
    L.append("| 源 | 数据类 | 实测每日队列 | 适用/池内 | 有数据 | 合法空 | **A 应采未采** | **B 队列外** | **C 队列内该补** | D 新闻空窗 | 缺口率 | 源端规模 | 体积MB |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rep["sources"]:
        L.append("| %s `%s` | %s | %s | %d/%d | %d | %d | **%d** | %d | %d | %d | %s | %s | %s |" % (
            r["label"], r["id"], r["data_class"],
            ("%d 只" % r["observed_queue"]) if r["observed_queue"] else "—",
            r["applicable"], r["pool_n"], r["ok"], r["wlt"],
            r["must_n"], r["offq_n"], r["q_gap_n"], r["no_news_n"],
            ("%s%%" % r["gap_pct"]) if r["gap_pct"] is not None else "—",
            r["files"], r["total_mb"]))
    L.append("")
    L.append("> 实测每日队列取自 `logs/target_queue_intel_daily.log` 的 `→ xxx.py N 只`，"
             "与声明队列（actionable %d）并列，防止用声明口径冒充实际覆盖。"
             "「源端规模」按源性质不同：jsonl 源=文件数、`qianping`=最新快照内票数、"
             "`news`=有打标关联的票数。" % rep["queue_sets"].get("chanlun_actionable", 0))
    L.append("")

    L.append("## 二、按池看：哪条线的票数据最欠缺")
    L.append("")
    L.append("| 池 | 票数 | 平均缺口分 | 缺口最重的 3 个源（缺口/适用，零覆盖/适用） |")
    L.append("|---|---|---|---|")
    for r in rep.get("pool_rows") or []:
        tops = sorted(
            ((sid, v) for sid, v in r["src_gap"].items() if v["gap"] > 0),
            key=lambda kv: -(kv[1]["pct"] or 0))[:3]
        txt = "；".join("%s %d/%d,零覆盖 %d" % (sid, v["gap"], v["applicable"],
                                              v.get("zero_cov", 0))
                       for sid, v in tops) or "—"
        L.append("| %s（`%s`） | %d | %s | %s |" % (r["label"], r["id"], r["n"], r["avg_score"], txt))
    L.append("")
    L.append("> 缺口分 = Σ（源严重度 × 池权重），只用于排序优先级，**不是**标的质量评价。")
    L.append("")

    for name, _states, desc in BUCKETS:
        items = rep["buckets"].get(name) or []
        L.append("### 三、%s　共 %d 条" % (name, rep["bucket_n"].get(name, 0)))
        L.append("")
        L.append("_%s_" % desc)
        L.append("")
        if not items:
            L.append("_无_")
            L.append("")
            continue
        if name == "B_队列外未覆盖":
            # 该桶条数 = 票 × 源，按票聚合才看得出「哪只票该补」。
            by_c = {}
            for it in items:
                e = by_c.setdefault(it["code"], {"name": it["name"], "pools": it["pools"],
                                                 "score": it["score"], "srcs": [], "zero": 0})
                e["srcs"].append("%s(%s)" % (it["src"], STATE_LABEL.get(it["state"], "")))
                if it["state"] in (STATE_MISSING, STATE_EMPTY, STATE_NO_QUEUE):
                    e["zero"] += 1
            L.append("| 代码 | 名称 | 缺口分 | 缺几个源 | 其中零覆盖 | 缺哪些源 | 所属池 |")
            L.append("|---|---|---|---|---|---|---|")
            for c, e in sorted(by_c.items(), key=lambda kv: (-kv[1]["zero"], -kv[1]["score"]))[:30]:
                L.append("| %s | %s | %s | %d | **%d** | %s | %s |" % (
                    c, e["name"] or "-", e["score"], len(e["srcs"]), e["zero"],
                    "、".join(e["srcs"]), ",".join(e["pools"])))
            if len(by_c) > 30:
                L.append("")
                L.append("_（共 %d 只票，仅列缺口分前 30，完整见 JSON）_" % len(by_c))
            L.append("")
            continue
        L.append("| 代码 | 名称 | 源 | 状态 | 条数 | 最后内容 | 距今h | 所属池 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for it in items[:25]:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                it["code"], it["name"] or "-", it["src"],
                STATE_LABEL.get(it["state"], it["state"]),
                it["n"], it["last"] or "-",
                it["age_h"] if it["age_h"] is not None else "-",
                ",".join(it["pools"])))
        if len(items) > 25:
            L.append("")
            L.append("_（仅列前 25 条，完整清单见 `data/gap_scan.json` → `buckets`）_")
        L.append("")

    nc = rep.get("news_cover") or {}
    if nc:
        L.append("## 四、新闻源覆盖口径（打标命中，非采集量）")
        L.append("")
        L.append("| 指标 | 全市场 | 池内（%s 只） |" % nc.get("pool_n", 0))
        L.append("|---|---|---|")
        L.append("| 有打标新闻（全历史） | %d 只 | %d 只 |"
                 % (nc.get("market_all", 0),
                    nc.get("pool_n", 0) - nc.get("pool_never", 0)))
        L.append("| 近 7 日有新闻 | %d 只 | %d 只 |" % (nc.get("market_7d", 0), nc.get("pool_7d", 0)))
        L.append("| 近 48h 有新闻 | %d 只 | **%d 只** |"
                 % (nc.get("market_48h", 0), nc.get("pool_48h", 0)))
        L.append("| 池内从未有过新闻 | — | %d 只 |" % nc.get("pool_never", 0))
        L.append("")
        L.append("> 新闻源是全市场自采、按票打标（37%% 的入库新闻带个股标签；"
                 "公告类 `xd_notice`/`xd_news` 打标率 100%%，宏观类 `chinanews`/`world` 约 1%% 属正常）。"
                 "「空窗」= 该票期间没有被打标的新闻，是**内容事实**，不等于新闻源断供。")
        L.append("")

    L.append("## 五、源能力地图（现有什么数据、各自能给什么）")
    L.append("")
    L.append("| 源 | 落盘路径 | 采集队列 | 提供的数据类型 |")
    L.append("|---|---|---|---|")
    for r in rep["sources"]:
        L.append("| %s | `%s` | %s | %s |" % (
            r["label"], r["dir"], r["queue"] or "全市场自采（按票打标）", r["data_class"]))
    L.append("")
    L.append("## 六、源能力空缺：还有什么地方有此类型数据")
    L.append("")
    L.append("以下为**候选方向，均未接入、均未核验可得性**。仅回答「这类数据还在哪儿」，"
             "是否接入属新源接入（L2，归马超配准），本席不擅自开采集。")
    L.append("")
    L.append("| 数据类 | 候选源 | 补什么空缺 | 可得性（待核验） | 合规风险 |")
    L.append("|---|---|---|---|---|")
    for c in rep.get("candidate_sources") or []:
        L.append("| %s | %s | %s | %s | %s |" % (
            c["class"], c["name"], c["fills"], c["access"], c["risk"]))
    L.append("")
    return "\n".join(L)


def render_table(rep):
    out = ["时点 %s　池内 %d 只　阈值 stale=%sh thin=%s" % (
        rep["asof"], rep["pool_total"], rep["params"]["stale_hours"], rep["params"]["thin_n"]), ""]
    out.append("%-20s %8s %6s %5s %6s %6s %6s %6s" % (
        "SRC", "适用/池", "有数据", "合法空", "A应采", "B队外", "C队内", "D新闻"))
    for r in rep["sources"]:
        out.append("%-20s %8s %6d %5d %6d %6d %6d %6d" % (
            r["label"][:18], "%d/%d" % (r["applicable"], r["pool_n"]),
            r["ok"], r["wlt"], r["must_n"], r["offq_n"], r["q_gap_n"], r["no_news_n"]))
    out.append("")
    out.append("行动分组：" + "　".join("%s=%d" % (k, v) for k, v in rep["bucket_n"].items()))
    return "\n".join(out)


def _atomic_write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


_cache_snapshot = {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="池内标的 × 数据源 缺口发现器（只读）")
    ap.add_argument("--stale-hours", type=float, default=DEFAULTS["stale_hours"])
    ap.add_argument("--thin", type=int, default=DEFAULTS["thin_n"])
    ap.add_argument("--top", type=int, default=DEFAULTS["top"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", default=OUT_MD)
    ap.add_argument("--no-cache", action="store_true", help="忽略索引缓存，全量重建")
    ap.add_argument("--no-write", action="store_true", help="不落盘（纯查看）")
    ap.add_argument("--no-slack", action="store_true", help="不做周末松弛（阈值原样使用）")
    ap.add_argument("--only", nargs="+", default=None, help="只体检指定代码（如 002983 600515）")
    ap.add_argument("--now", default=None)
    a = ap.parse_args(argv)

    now = _parse_dt(a.now) if a.now else datetime.now()
    t0 = time.time()
    rep = build_report(now=now, stale_h=a.stale_hours, thin_n=a.thin,
                       fresh_cache=a.no_cache, only=a.only, no_slack=a.no_slack)
    rep["elapsed_s"] = round(time.time() - t0, 1)

    if not a.no_write and not a.only:
        _atomic_write_json(OUT_JSON, rep)
        with open(IDX_F + ".tmp", "w", encoding="utf-8") as f:
            json.dump(_cache_snapshot, f, ensure_ascii=False)
        os.replace(IDX_F + ".tmp", IDX_F)
        try:
            with open(a.md, "w", encoding="utf-8") as f:
                f.write(render_md(rep))
        except OSError as e:
            print("[WARN] markdown 落盘失败：%s" % e, file=sys.stderr)

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0

    if a.only:
        # 打印**实际生效**阈值（含周末松弛），不是 CLI 传入的原始值，
        # 否则单票体检会拿 48h 去读 96h 的判定结果，读数与判据对不上。
        print("单票体检 %s（生效阈值 stale=%sh［原始 %sh + 周末松弛 %sh］、thin=%s）" % (
            ",".join(a.only), rep["params"]["stale_hours"], rep["params"]["stale_hours_raw"],
            rep["params"].get("weekend_slack_h", 0), rep["params"]["thin_n"]))
        print("")
        for code, v in rep["by_code"].items():
            pool_txt = ",".join(v["pools"]) if not v.get("pool_out") else "池外·未命中任何池"
            print("%s %s  pools=%s  gap_score=%.1f" % (
                code, v["name"] or "-", pool_txt, v["gap_score"]))
            for sid, d in v["srcs"].items():
                print("    %-8s %-12s n=%-5s last=%-19s age_h=%s" % (
                    sid, STATE_LABEL.get(d["state"], d["state"]), d.get("n"),
                    d.get("last") or "-", d.get("age_h") if d.get("age_h") is not None else "-"))
            print("")
        out = rep.get("pool_out") or []
        if out:
            print("不在任何池内（989 池 / 10 条池声明均未命中）：%s" % ",".join(out))
            print("  → 这批票的缺口不是「采不到」，是「根本没进队列」；是否纳池归上级配准。")
            print("")
        return 0

    print(render_table(rep))
    print("")
    print("Top %d 缺口票：" % min(a.top, len(rep["ranking"])))
    for r in rep["ranking"][:a.top]:
        print("  %s %-6s score=%5.1f 缺: %s" % (
            r["code"], (r["name"] or "")[:6], r["gap_score"],
            ", ".join("%s:%s" % (g, STATE_LABEL.get(r["detail"][g]["state"], ""))
                      for g in r["gaps"])))
    print("")
    print("JSON → %s" % OUT_JSON)
    print("MD   → %s" % a.md)
    print("耗时 %.1fs　索引缓存 %d 文件" % (rep["elapsed_s"], rep["cache_files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
