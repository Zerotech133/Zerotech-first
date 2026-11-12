# -*- coding: utf-8 -*-
"""多策略共振聚合器（策略集市基础设施层 · 只读聚合，不改任何策略口径）。

设计原则（对齐项目纪律）：
· 声明式策略注册表 STRATEGIES——新增策略=加一条注册（产物路径/取数/
  方向语义/徽标/详情页 URL），聚合与页面自动跟随，零改框架；
· 展示层聚合：不修改任何策略的入池/推送/拦截规则，影子策略保持影子；
· 方向语义三分：long（看多）/ short（看空或回避）/ neutral（中性观察），
  冲突=同票同时存在 long 与 short；
· 代码归一化：以 tushare 格式为基准（600519.SH / PLTR / 00700 / BTC/USDT），
  watchlist 前缀（sh/sz/bj/rt_hk/gb_）在入口归一。

产物消费（全部只读）：
· RPS 共振    reports/rps_latest.json     res[].code/name/market/score/action/sigs
· 四策略信号  同上 res[].sigs（trend_follow/ma_cross/rsi_reversal/boll_breakout）
· 缠论预警池  data/chanlun_pool.jsonl     近 90 天最新信号/票 + 侧面标签
· 九维评分    data/nine_dim_scores.json   rows[].code/name/score/cov（观察工具）

W3-4 韧性改造（20261014 cross-resilience-dev）：
· 源状态显式化：build_cross 产物带 src_state（每源 ok/missing/error）与
  degraded 标注——单源失败按"该源缺席"继续聚合，不再静默空集；
· sig4 解耦：longs 字段缺失时从 res[].sigs（四策略 0/1）现算持仓数，
  不再与 qpzm 完全共命运（同一 rps_latest.json，字段级互备）；
· k=3 门槛降档（W3-4 网格结论 k=3 为硬门槛，不自动降 k 防放水）：
  正常模式 k=3；degraded 且可用源=3 保持 k=3；可用源<3 → topn 产空
  结果 + 醒目 DEGRADED 说明（blocked 模式）；
· 源健康检查 source_health()：四源 ok/stale/missing 三态 + 数据锚日期，
  stale/missing 时可经 runner.push_feishu 发告警（config cross 节
  source_health_alert 开关，测试走 dry 模式不真推）。

W5 净向分排序（20261014 cross-netdev，v1.1 计划滚动项）：
· 排序键切换 sort_key（config.json cross 节，缺省 "net"；设 "raw" 一键
  回退旧长票计数排序）：net_score = Σ(源方向分×源权重)/Σ(可用源权重)，
  方向分 long=+1/short=-1/neutral=0，归一化使口径在降级日保持 [-1,1]；
· 9dim 反指显式处理（W3-4 网格结论 + W5 批准口径）：方向取反 × 权重
  0.5（贡献 -0.5）——改前聚合把 9dim 当普通 long 计入 n_long，未体现
  反指语义，本版在净向分中显式处理并留痕（W3-4 网格 down0.5 为 +0.5、
  reverse 为 -1.0，W5 批准的 -0.5 为两者折中的保守口径）；
· 单源缺失（src_state 非 ok）时该源不计入分子、权重同步剔除出分母
  （归一化），与韧性降级语义共存；k 门槛/买入观望分级阈值不动。
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPORTS = os.path.join(HERE, "reports")

# 北京时间（生产者完成时间戳统一 +08:00，2026-10-26 OS-FIX-MEASURE2）
CST = timezone(timedelta(hours=8))

CHANLUN_LOOKBACK_DAYS = 90  # 缠论信号有效窗口（检出日起）
NINE_DIM_LONG = 80  # 九维展示阈值（观察工具口径，非买入建议）
SIG4_LONG = 3  # 四策略持仓数 ≥3 记 long
K_RES = 3  # W3-4 网格结论：k=3 共振硬门槛（degraded 不降档防放水）
TOPN_DEFAULT = 10  # k 门槛后 TopN 条数（对齐 cross_grid_backtest.TOPN）
CACHE_TTL = 60  # 门户请求级缓存（秒）
_CACHE = {"t": 0, "d": None}  # 门户请求级缓存状态（test_ui_drilldown mock 点）
CONFIG_FP = os.path.join(HERE, "config.json")

# ── W5 净向分排序参数（公式对齐 cross_grid_backtest：Σ dir×w） ──
SORT_KEY_DEFAULT = "net"  # 排序键缺省：net（W5 直上）；"raw" 回退旧行为
SRC_W = {  # 源权重（W3-4 网格口径：qpzm/sig4/chanlun=1.0）
    "qpzm": 1.0,
    "sig4": 1.0,
    "chanlun": 1.0,
    "9dim": 0.5,  # 9dim 降权 0.5（共振内削弱单源噪声）
}
SRC_DIR_SIGN = {"9dim": -1.0}  # 9dim 反指：方向取反（long 计 -1），
# 贡献 = -1×0.5 = -0.5（W5 批准口径，见模块头注释）
_DIR_VAL = {"long": 1.0, "short": -1.0, "neutral": 0.0}

# 源健康过期阈值（日历日近似；交易日精确判定见文档遗留风险）
STALE_DAYS = {
    "qpzm": 3,  # 日批缺失（容一个周末）
    "sig4": 5,  # ≈3 交易日未更新（W3-4 口径）
    "chanlun": 4,  # 扫描断档（容周末+批处理延迟）
    "9dim": 8,  # 锚超 8 日（周频口径）
}


def norm_code(raw):
    """watchlist/tushare 代码 → 统一格式（sh600519→600519.SH、
    gb_pltr→PLTR、rt_hk00700→00700；其余原样）。
    2026-10-16 章程 §4.3 收编：A 股判定（含裸 6 位补后缀）委托
    codes.normalize_code 唯一权威；港美股分支本模块自有。"""
    from codes import normalize_code

    c = str(raw or "").strip()
    n = normalize_code(c)
    if n is not None:
        return n
    if c.startswith("rt_hk"):
        try:
            return "%05d" % int(c[5:])
        except ValueError:
            return c
    if c.startswith("gb_"):
        return c[3:].upper().replace(".", "_")
    return c


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _load_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/cross_poll.py", exc)
    return out


# ── 生产者时间戳（2026-10-26 OS-FIX-MEASURE2）──────────────────────────────
# 背景：api_common 的源健康只认"生产者完成时间"；此前 cross 聚合器只有
# generated_at（聚合器墙钟），无法证明"当期跑完"→ 永远 unproven。本版给
# build_cross 产物补两个字段：
#   completed_at = 本次聚合完成时刻（北京时间 ISO8601 +08:00）
#   trade_date   = 各源数据锚（信号日 dt）的最大值，取不到 None
# 口径边界：trade_date 是"信号覆盖到哪一天"，**不是**聚合器运行日；拿不到
# 任何源锚就不猜（None），由消费方维持 unproven。排序/过滤/门槛逻辑零改动。
def _now_cst_iso():
    """北京时间 ISO8601（+08:00）——生产者完成时间戳的唯一格式。"""
    return datetime.now(CST).isoformat(timespec="seconds")


def _signal_day(value):
    """信号日期归一 → YYYY-MM-DD；8 位数字/带时间/坏值 → ''（不猜）。"""
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = "%s-%s-%s" % (text[:4], text[4:6], text[6:])
    day = text[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return ""
    return day


def _source_anchor(hits):
    """单源数据锚 = 该源全部命中信号日 dt 的最大值（YYYY-MM-DD）；无则 ''。"""
    days = [d for d in (_signal_day(h.get("dt")) for h in (hits or {}).values()) if d]
    return max(days) if days else ""


# ── 策略取数器：各自返回 {code: hit}，hit={dir,label,extra} ──
def _src_qpzm():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for r in snap.get("res", []):
        act = str(r.get("action", ""))
        base = act.replace("（环境闸门）", "")
        d = "long" if base == "买入" else "short" if base == "回避" else "neutral"
        out[norm_code(r.get("code"))] = {
            "dir": d,
            "label": "共振%d·%s" % (r.get("score") or 0, base),
            "name": r.get("name") or "",
            "market": r.get("market") or "",
            # 数据锚：rps 快照 ts（日批），供 build_cross 汇总 trade_date
            "dt": str(snap.get("ts") or ""),
        }
    return out


def _sig4_count(r):
    """sig4 解耦取数（W3-4）：优先 res[].longs，缺失时从 res[].sigs
    （四策略 0/1 dict）现算持仓数——字段级互备，不再与 qpzm 完全共命运。
    两路皆缺 → None（该票无四策略信号，跳过，不装作 neutral）。"""
    n = r.get("longs")
    if n is None:
        sigs = r.get("sigs") or {}
        if not sigs:
            return None
        n = sum(1 for v in sigs.values() if v)
    try:
        return int(n)
    except (TypeError, ValueError):
        return None


def _src_sig4():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for r in snap.get("res", []):
        n = _sig4_count(r)
        if n is None:
            continue
        out[norm_code(r.get("code"))] = {
            "dir": "long" if n >= SIG4_LONG else "neutral",
            "label": "4策略%d/4" % n,
            "name": r.get("name") or "",
            "market": r.get("market") or "",
            # 数据锚：同 rps 快照 ts
            "dt": str(snap.get("ts") or ""),
        }
    return out


_DD_FALLBACK = {"n": 0}


def _dd_of(r):
    """池行「最新检出日」——优先 detect_date，缺失才回退 dt。

    为什么保留回退而不改成断言（v7 §1 结论，2026-11-05）：
    detect_date 是确认日、dt 是结构端点日，回退会让该行看起来更旧，只把
    结果推向「更保守（更可能判 stale）」一侧，不会掩盖断档；而断言会在数据
    缺列时让整个轮询崩掉，代价远大于收益。当前全量 fill rate 100%，回退不
    触发。此处加一次性告警，使「回退真的发生了」可观测而不是静默。
    """
    dd = r.get("detect_date")
    if not dd:
        if _DD_FALLBACK["n"] == 0:
            logging.getLogger("cross_poll").warning(
                "chanlun 池存在缺 detect_date 的行，已回退 dt：code=%s",
                r.get("code"),
            )
        _DD_FALLBACK["n"] += 1
        dd = r.get("dt") or ""
    return str(dd)


def _src_chanlun():
    # 数据源：优先 SQLite，fallback JSONL（双写期过渡）
    pool = _src_chanlun_load()
    cutoff = (datetime.now() - timedelta(days=CHANLUN_LOOKBACK_DAYS)).strftime(
        "%Y-%m-%d"
    )
    latest = {}
    for r in pool:
        dd = _dd_of(r)
        if dd < cutoff:
            continue
        code = norm_code(r.get("code"))
        if code not in latest or dd > latest[code][0]:
            latest[code] = (dd, r)
    out = {}
    for code, (_, r) in latest.items():
        t = str(r.get("type") or "")
        d = "long" if "买" in t else "short" if "卖" in t else "neutral"
        tags = "".join(
            lb
            for lb, k in (("M", "mbei"), ("盘", "bei2"), ("量", "vv"), ("趋", "tbe"))
            if r.get(k) is True
        )
        out[code] = {
            "dir": d,
            "label": "缠论%s%s" % (t, ("·" + tags) if tags else ""),
            "name": r.get("name") or "",
            "market": r.get("market") or "CN",
            "dt": str(r.get("dt") or ""),
        }
    return out


def _src_chanlun_load():
    """优先从 SQLite 读取近 CHANLUN_LOOKBACK_DAYS 天信号，失败回退 JSONL。

    SQLite 路径走 db.pool_query_recent（已按 code 保留最新）；JSONL 路径
    全量加载后由 _src_chanlun 下游过滤。两条路径输出结构一致。
    """
    try:
        import db as _db

        conn = _db.get_conn()
        try:
            rows = _db.pool_query_recent(conn, days=CHANLUN_LOOKBACK_DAYS)
            if rows:
                return rows
        finally:
            conn.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/cross_poll.py", exc)
    return _load_jsonl(os.path.join(DATA, "chanlun_pool.jsonl"))


def _src_9dim():
    fp = os.path.join(DATA, "nine_dim_scores.json")
    if not os.path.exists(fp):
        # 判例式：文件缺=源缺失（missing），不许静默当合法空集
        raise FileNotFoundError(fp)
    snap = _load_json(fp)
    out = {}
    for r in snap.get("rows", []):
        sc = r.get("score")
        if sc is None or sc < NINE_DIM_LONG:
            continue
        out[norm_code(r.get("code"))] = {
            "dir": "long",
            "label": "九维%.0f" % sc,
            "name": r.get("name") or "",
            "market": "CN",
            # 数据锚：nine_dim_scores.json trade_date（周频锚），供 trade_date 汇总
            "dt": str(snap.get("trade_date") or snap.get("generated_at") or ""),
        }
    return out


# ── 策略注册表：新增策略在此加一条即可 ─────────────────────
STRATEGIES = [
    {
        "id": "qpzm",
        "name": "RPS共振",
        "badge": "共振",
        "color": "#3fb950",
        "icon": "🌊",
        "url": "/bt-qpzm",
        "markets": "CN/US/HK/CRYPTO",
        "loader": _src_qpzm,
    },
    {
        "id": "sig4",
        "name": "四策略信号",
        "badge": "4策略",
        "color": "#58a6ff",
        "icon": "📊",
        "url": "/bt-signals",
        "markets": "自选池",
        "loader": _src_sig4,
    },
    {
        "id": "chanlun",
        "name": "缠论结构",
        "badge": "缠论",
        "color": "#f778ba",
        "icon": "🧭",
        "url": "/bt-chanlun-pool",
        "markets": "CN",
        "loader": _src_chanlun,
    },
    {
        "id": "9dim",
        "name": "九维评分",
        "badge": "九维",
        "color": "#d29922",
        "icon": "🧮",
        "url": "/bt-9dim",
        "markets": "CN",
        "loader": _src_9dim,
        # 阈值过滤型源：score≥80 无票是合法结果，空集不算源缺失
        "allow_empty": True,
    },
]


def _sort_key_cfg():
    """读 config cross.sort_key（缺省 net；非法值按缺省处理不炸）。"""
    cfg = _load_json(CONFIG_FP, default={}).get("cross") or {}
    sk = str(cfg.get("sort_key") or SORT_KEY_DEFAULT).strip().lower()
    return sk if sk in ("net", "raw") else SORT_KEY_DEFAULT


def net_score_of(st, src_state):
    """单票净向分：Σ(源方向分×源权重×源方向符号) / Σ(可用源权重)。

    · 方向分：long=+1 / short=-1 / neutral=0（未命中的可用源贡献 0）；
    · 9dim 反指：方向取反 × 权重 0.5 → 命中贡献 -0.5（SRC_DIR_SIGN）；
    · 单源缺失（src_state 非 ok）：该源不进分子、权重剔出分母（归一化，
      使降级日分数口径保持 [-1,1] 可比）；分母为 0（极端：全源缺失）→ 0.0。
    """
    num, den = 0.0, 0.0
    for sid, w in SRC_W.items():
        if (src_state or {}).get(sid, {}).get("status") != "ok":
            continue  # 缺失源：分子分母同步剔除
        den += w
        for h in st.get("hits", ()):
            if h["sid"] == sid:
                num += SRC_DIR_SIGN.get(sid, 1.0) * _DIR_VAL[h["dir"]] * w
    return round(num / den, 4) if den > 0 else 0.0


def _sort_stocks(stocks, sort_key):
    """按排序键排序（降序）。net=净向分（平票按 code 稳定序，对齐网格
    lexsort 口径）；raw=旧长票计数（-n_hit,-n_long,code），一键回退。"""
    if sort_key == "net":
        return sorted(stocks, key=lambda x: (-x["net_score"], x["code"]))
    return sorted(stocks, key=lambda x: (-x["n_hit"], -x["n_long"], x["code"]))


def build_cross(force=False):
    """聚合全部注册策略 → {generated_at, strategies, stocks, matrix, ...}。
    stocks[]: code/name/market/hits[{sid,dir,label,url,badge,color}]/n_hit/
    n_long/n_short/state(long=全多 short=全空 mixed=冲突 solo=单策略)

    W3-4 韧性字段：
    · src_state:  {sid: {status: ok/missing/error, n: 票数, detail}}
      ——ok=有数据；missing=产物缺/空集；error=取数器抛异常（判例式：
      加载失败≠市场无信号，必须显式区分，不许静默当空集）；
    · degraded:   任一源非 ok 即 True；degrade_note 为中文降级说明；
    · k_gate:     {k, n_sources_ok, mode, note}——mode∈normal（4 源全在，
      k=3）/ degraded_k3（可用源=3，仍 k=3）/ blocked（可用源<3，k=3
      数学不可达，topn 产空 + 醒目 DEGRADED，不自动降 k 防放水）；
    · topn:       n_hit≥k 的票按排序键（cross.sort_key，缺省 net 净向分）
      降序取前 TOPN_DEFAULT（blocked 时 []）；net 模式下每票带 net_score
      字段（[-1,1] 归一化净向分），输出带 sort_key 口径标注供下游识别。
    · 生产者时间戳（2026-10-26 OS-FIX-MEASURE2，供 api_common 源健康判"当期
      跑完"）：completed_at = 本轮聚合完成时刻（北京时间 ISO8601 +08:00）；
      trade_date = 各源数据锚的最大日（src_state[sid].anchor，取不到 None）；
      src_state = {sid: {status, n, detail, anchor}}（anchor 为新增字段，向后兼容）。
      排序/过滤/k 门槛逻辑零改动。
    """
    if not force and _CACHE["d"] and time.time() - _CACHE["t"] < CACHE_TTL:
        return _CACHE["d"]
    per = {}
    src_state = {}
    for s in STRATEGIES:
        sid = s["id"]
        try:
            per[sid] = s["loader"]()
            # 空集语义分流：allow_empty 源（9dim 阈值过滤）空=合法 ok；
            # 其余源空集=missing；loader 抛异常=error（显式三态，
            # 加载失败≠市场无信号，不许静默当空集）
            if per[sid]:
                st = "ok"
            elif s.get("allow_empty"):
                st = "ok"
            else:
                st = "missing"
            src_state[sid] = {
                "status": st,
                "n": len(per[sid]),
                "detail": "",
                "anchor": _source_anchor(per[sid]),
            }
        except Exception as ex:
            per[sid] = {}
            src_state[sid] = {
                "status": "error",
                "n": 0,
                "detail": str(ex)[:120],
                "anchor": "",
            }
    stocks = {}
    for s in STRATEGIES:
        sid = s["id"]
        for code, hit in per[sid].items():
            st = stocks.setdefault(
                code,
                {
                    "code": code,
                    "name": hit.get("name") or "",
                    "market": hit.get("market") or "",
                    "hits": [],
                },
            )
            if not st["name"] and hit.get("name"):
                st["name"] = hit["name"]
            st["hits"].append(
                {
                    "sid": sid,
                    "dir": hit["dir"],
                    "label": hit["label"],
                    "url": s["url"],
                    "badge": s["badge"],
                    "color": s["color"],
                    "dt": hit.get("dt", ""),
                }
            )
    for st in stocks.values():
        dirs = [h["dir"] for h in st["hits"]]
        st["n_hit"] = len(st["hits"])
        st["n_long"] = dirs.count("long")
        st["n_short"] = dirs.count("short")
        st["state"] = (
            "mixed"
            if st["n_long"] and st["n_short"]
            else "long"
            if st["n_long"]
            else "short"
            if st["n_short"]
            else "neutral"
        )
        st["hits"].sort(key=lambda h: h["sid"])
    # 策略两两交集矩阵
    matrix = {a["id"]: {b["id"]: 0 for b in STRATEGIES} for a in STRATEGIES}
    for st in stocks.values():
        sids = {h["sid"] for h in st["hits"]}
        for a in sids:
            for b in sids:
                matrix[a][b] += 1
    # k 门槛降档（不自动降 k）：可用源数决定 normal / degraded_k3 / blocked
    bad = [sid for sid, v in src_state.items() if v["status"] != "ok"]
    n_ok = len(STRATEGIES) - len(bad)
    if not bad:
        k_mode, k_note = "normal", "四源齐整，k=%d 共振正常" % K_RES
    elif n_ok >= K_RES:
        k_mode, k_note = (
            "degraded_k3",
            "源缺失[%s]，可用 %d 源仍保持 k=%d "
            "（不降门槛）" % (",".join(sorted(bad)), n_ok, K_RES),
        )
    else:
        k_mode, k_note = (
            "blocked",
            "DEGRADED：可用源 %d <%d，k=%d 共振数学"
            "不可达，TopN 产空（不降门槛放水）；缺失[%s]"
            % (n_ok, K_RES, K_RES, ",".join(sorted(bad))),
        )
    k_gate = {"k": K_RES, "n_sources_ok": n_ok, "mode": k_mode, "note": k_note}
    # W5 排序键：net=净向分（每票带 net_score 字段）；raw=旧长票计数回退
    sort_key = _sort_key_cfg()
    if sort_key == "net":
        for st in stocks.values():
            st["net_score"] = net_score_of(st, src_state)
    srt = _sort_stocks(list(stocks.values()), sort_key)
    # k 门槛过滤（判例式：排序键只改序，不放水——blocked/degraded 同此口径）
    topn = [st for st in srt if st["n_hit"] >= K_RES]
    # 生产者时间戳（2026-10-26 OS-FIX-MEASURE2）：各源数据锚取最大值 →
    # trade_date（信号覆盖到哪一天，不是聚合器运行日）；取不到任何锚 → None。
    # completed_at = 本轮聚合完成时刻（北京时间 +08:00），供 api_common 源健康
    # 的 dated() 判"当期是否跑完"——此前只有 generated_at 被拒收为 unproven。
    anchors = [v.get("anchor") for v in src_state.values() if v.get("anchor")]
    trade_date = max(anchors) if anchors else None
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "completed_at": _now_cst_iso(),
        "trade_date": trade_date,
        "strategies": [
            {k: v for k, v in s.items() if k != "loader"} for s in STRATEGIES
        ],
        "n_per": {sid: len(v) for sid, v in per.items()},
        "src_state": src_state,
        "degraded": bool(bad),
        "degrade_note": (
            "全部源正常"
            if not bad
            else "DEGRADED：源缺失/异常[%s]，%s" % (",".join(sorted(bad)), k_note)
        ),
        "k_gate": k_gate,
        "sort_key": sort_key,
        "topn": topn[:TOPN_DEFAULT],
        "stocks": srt,
        "matrix": matrix,
    }
    _CACHE["t"], _CACHE["d"] = time.time(), out
    out["orthogonal"] = build_orthogonal(stocks, src_state)
    return out


# ── P2 正交碰撞（2026-11-13，教材"三维碰撞"的工程化）────────────────
CONTRA_SOURCES = {"9dim"}   # 反指源（W5 口径）：不进正交看多集


def build_orthogonal(stocks, src_state):
    """各看多源独立判定 → 交集/单源/差集三类集合（只读标注，不拦截）。

    教材出处：《公安大数据挖掘与分析教程》长歌案 5.1.3"三维碰撞"——
    各数据块独立研判出嫌疑目标，最后碰撞整合。交集=高置信，差集=待查证。

    与 n_hit 计数的本质区别（n_hit 教训的工程化，见 JUDGMENT_AUDIT #2）：
    n_hit 数的是"源出现次数"，任何源（含反向标记）都凑数；
    正交碰撞只看【看多源】（dir=long），且必须源状态 ok——
    error/missing 的源不算"没票"，算"未知"，不进差集。

    输出（随 build_cross 产物归档，供差集后验）：
      sets.long{n源: [codes]}  —— 每档独立看多源数的票集
      pairs[A_not_B]: [codes]  —— 差集（A long 而 B 无观点），按源对归档，
                                   这是度量各源独立信息量的天然对照组
      audit: 三字段（同台账纪律）
    """
    ok_srcs = {sid for sid, v in src_state.items() if v.get("status") == "ok"}
    # 反指源（W5 口径：9dim 方向取反×0.5）不进正交碰撞的看多集——
    # 它的 long 语义与正向源相反，混入会把差集口径搅浑。
    contra = {sid for sid in ok_srcs if CONTRA_SOURCES and sid in CONTRA_SOURCES}
    ok_srcs -= contra
    bull_sets = {}       # sid -> {code}
    for sid in ok_srcs:
        bull_sets[sid] = {
            code for code, s in stocks.items()
            if any(h["sid"] == sid and h["dir"] == "long" for h in s["hits"])
        }
    # 交集分档
    tiers = {}
    for code, s in stocks.items():
        n = sum(1 for sid in ok_srcs if code in bull_sets.get(sid, ()))
        if n:
            tiers.setdefault(n, []).append(code)
    # 差集（源对）
    pairs = {}
    sids = sorted(bull_sets)
    for i, a in enumerate(sids):
        for b in sids[i + 1:]:
            pairs["%s_not_%s" % (a, b)] = sorted(bull_sets[a] - bull_sets[b])
            pairs["%s_not_%s" % (b, a)] = sorted(bull_sets[b] - bull_sets[a])
    return {
        "ok_sources": sorted(ok_srcs),
        "bull_n_tiers": {str(k): sorted(v) for k, v in sorted(tiers.items())},
        "diff_pairs": pairs,
        "n_diff_total": sum(len(v) for v in pairs.values()),
        "audit": {
            "assumptions": "看多源=dir long；源 ok 才参与（error/missing 不进差集）",
            "falsifier": "反向源（short）混入 long 集；差集票后验与交集无差"
                         "（各源无独立信息量）",
            "cost_if_wrong": "差集若被当'待查证线索'使用而无独立信息，"
                             "只是噪声放大——仅归档后验，不进名单",
        },
    }


# ── W3-4 源健康检查（三态 ok/stale/missing + 数据锚日期） ──────
def _anchor_age_days(anchor):
    """锚日期（YYYY-MM-DD 或 YYYYMMDD）距今天数；解析失败返回 None。"""
    a = str(anchor or "").strip()
    if len(a) == 8 and a.isdigit():
        a = "%s-%s-%s" % (a[:4], a[4:6], a[6:])
    try:
        d = datetime.strptime(a[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (datetime.now() - d).total_seconds() / 86400.0


def _health_status(anchor, stale_days):
    """三态判定：锚缺 → missing；锚龄 > stale_days → stale；否则 ok。"""
    age = _anchor_age_days(anchor)
    if age is None:
        return "missing", None
    if age > stale_days:
        return "stale", round(age, 1)
    return "ok", round(age, 1)


def source_health(now=None):
    """四源健康检查 → {sid: {status: ok/stale/missing, anchor, age_days,
    stale_after, detail}}（合成数据可测：只读产物，不碰网络/数据库）。

    判例式规则（STALE_DAYS）：
    · qpzm   rps_latest.json ts 日批；>3 日历日 stale（容一个周末）
    · sig4   同文件四策略信号（longs/sigs 任一在即算有数据）；
             >5 日历日 stale（≈W3-4 口径 3 交易日）
    · chanlun 池内最新 detect_date/dt；>4 日历日 stale（扫描断档）
    · 9dim   nine_dim_scores.json trade_date；>8 日历日 stale（周频锚）
    """
    del now  # 预留：测试注入时钟用；当前以 datetime.now() 为准
    h = {}
    # qpzm / sig4 同源 rps_latest.json，字段级分别判（解耦后互不连坐）
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    res = snap.get("res") or []
    ts = str(snap.get("ts") or "")[:10]
    q_ok = bool(res and any(r.get("action") for r in res))
    h["qpzm"] = dict(
        zip(
            ("status", "age_days"),
            _health_status(ts if q_ok else "", STALE_DAYS["qpzm"]),
        )
    )
    h["qpzm"].update(
        {
            "anchor": ts if q_ok else "",
            "stale_after": STALE_DAYS["qpzm"],
            "detail": "" if q_ok else "res 空/无 action",
        }
    )
    s_ok = bool(res and any(_sig4_count(r) is not None for r in res))
    h["sig4"] = dict(
        zip(
            ("status", "age_days"),
            _health_status(ts if s_ok else "", STALE_DAYS["sig4"]),
        )
    )
    h["sig4"].update(
        {
            "anchor": ts if s_ok else "",
            "stale_after": STALE_DAYS["sig4"],
            "detail": "" if s_ok else "res 无 longs/sigs",
        }
    )
    # chanlun：池内最新检出日
    ch_anchor = ""
    try:
        pool = _src_chanlun_load()
        for r in pool:
            dd = _dd_of(r)
            if dd > ch_anchor:
                ch_anchor = dd[:10] if len(dd) >= 10 else dd
    except Exception as ex:
        h["chanlun"] = {
            "status": "missing",
            "anchor": "",
            "age_days": None,
            "stale_after": STALE_DAYS["chanlun"],
            "detail": "error: %s" % str(ex)[:80],
        }
    else:
        st, age = _health_status(ch_anchor, STALE_DAYS["chanlun"])
        h["chanlun"] = {
            "status": st,
            "anchor": ch_anchor,
            "age_days": age,
            "stale_after": STALE_DAYS["chanlun"],
            "detail": "" if ch_anchor else "信号池空",
        }
    # 9dim：最新锚 trade_date
    n9 = _load_json(os.path.join(DATA, "nine_dim_scores.json"))
    n_anchor = str(n9.get("trade_date") or n9.get("generated_at") or "")[:10]
    st, age = _health_status(n_anchor, STALE_DAYS["9dim"])
    h["9dim"] = {
        "status": st,
        "anchor": n_anchor,
        "age_days": age,
        "stale_after": STALE_DAYS["9dim"],
        "detail": "" if n9.get("rows") else "rows 空/文件缺",
    }
    return h


def health_summary_line(health=None):
    """每日 cross 扫描汇总输出的"源健康"一行：
    源健康: qpzm ok(2026-10-13) | sig4 stale(2026-10-08) | ... | 可用源 2/4"""
    h = health or source_health()
    parts = [
        "%s %s%s" % (sid, v["status"], "(%s)" % v["anchor"] if v["anchor"] else "")
        for sid, v in h.items()
    ]
    n_ok = sum(1 for v in h.values() if v["status"] == "ok")
    return "源健康: %s | 可用源 %d/%d" % (" | ".join(parts), n_ok, len(h))


def push_source_health_alert(health=None, send=None):
    """源缺失/过期告警（复用 cross 现有推送通道 runner.push_feishu）。

    · 开关：config.json cross.source_health_alert（默认关——防未配置环境
      误推；判例式：告警必须显式开启）；
    · send=None 按开关；send=True 强推；send=False dry 模式（测试路径，
      不写 outbox 不真推）；
    · 全源 ok → 不告警，返回 (False, "")。
    返回 (是否发送, 文本)。
    """
    h = health or source_health()
    bad = {sid: v for sid, v in h.items() if v["status"] != "ok"}
    n_ok = len(h) - len(bad)
    lines = [
        "⚠️ cross 源健康告警 · %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
        health_summary_line(h),
    ]
    for sid, v in h.items():
        lines.append(
            "· %-8s %-8s 锚=%-10s %s"
            % (
                sid,
                v["status"],
                v["anchor"] or "无",
                v["detail"]
                or (
                    "数据龄 %.1f 天" % v["age_days"]
                    if v["age_days"] is not None
                    else ""
                ),
            )
        )
    if n_ok < K_RES:
        lines.append(
            "→ 可用源 %d <%d，k=%d 共振不可达，cross 降级（TopN 空）"
            % (n_ok, K_RES, K_RES)
        )
    text = "\n".join(lines)
    if not bad:
        return False, ""
    if send is None:
        cfg = _load_json(CONFIG_FP, default={}).get("cross") or {}
        send = bool(cfg.get("source_health_alert"))
    if not send:
        return False, text  # 开关关 / dry：只返回文本不推
    try:
        import runner

        return bool(runner.push_feishu(text)), text
    except Exception as ex:
        return False, text + "\n[push error: %s]" % str(ex)[:80]


if __name__ == "__main__":
    d = build_cross(force=True)
    multi = [s for s in d["stocks"] if s["n_hit"] >= 2]
    mixed = [s for s in d["stocks"] if s["state"] == "mixed"]
    print(
        "stocks=%d multi(>=2)=%d mixed=%d" % (len(d["stocks"]), len(multi), len(mixed))
    )
    for s in multi[:15]:
        print(
            " ",
            s["code"],
            s["name"],
            s["state"],
            " | ".join(h["label"] for h in s["hits"]),
        )
    # W3-4：每日汇总输出带源健康行 + k 门槛状态
    print(health_summary_line())
    kg = d["k_gate"]
    print(
        "k 门槛: mode=%s k=%d 可用源=%d | %s"
        % (kg["mode"], kg["k"], kg["n_sources_ok"], kg["note"])
    )
    # W5：排序键口径标注（下游/推送卡可见）
    sk_note = (
        "净向分 net=Σ(dir×w)/Σw（9dim 反向×0.5）"
        if d["sort_key"] == "net"
        else "长票计数（旧行为）"
    )
    print("排序键: sort_key=%s（%s）" % (d["sort_key"], sk_note))
    if d["degraded"]:
        print("[DEGRADED] %s" % d["degrade_note"])
