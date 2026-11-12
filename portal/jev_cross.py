# -*- coding: utf-8 -*-
"""涨停事件交叉卡 v2（J-03，2026-11-13 定案两维卡）

一个涨停事件 → 一张卡，证据分**主维**和**附表**两层铺开。**不产出结论**：卡上
不写"建议""预期""看涨"，只写"哪一维有数据、精度到哪、原始值多少"。结论由人下。

## 为什么是两维卡（2026-11-13 裁定 A 案）

四维平行铺开在实践中不成立：时间维依赖小票分钟，覆盖不到是小票的结构性事实，
不是本线能补的（见 `docs/需求单_小票分钟覆盖_采集工位_v1.md`）。四维平等摆着，
会让一张卡「四格空两格」看起来像半成品，而实际上资金与席位两维是**完整可用**的。
所以改为主副两层：

| 层 | 维 | 是否参与验收 |
|---|---|---|
| **主维** | `flow`（收盘终值）+ `seat`（T+1 席位） | **是**，`acceptance.verdict` 只看这两维 |
| 附表 | `time`（分钟）+ `narrative`（日期级） | 否，只陈述、不判定 |

附表不是"不重要"，而是**精度天然低于主维**：叙事维的公告本身没有时分（精度就是天），
时间维在本地覆盖不到时是空的。把它们拉进验收等于用不可得的维拖累可得的维。

## 四维与各自的**时点精度**（这是本模块最重要的字段）

| 维 | 取数 | 精度 | 状态语义 |
|---|---|---|---|
| `time` | 本地 `stk_mins` parquet | 分钟 | 无该票分钟 → `unavailable` |
| `flow` | `_jev_snap/flow_*.json` | 收盘终值 | 无快照 → `unavailable` |
| `seat` | `data/hm_detail/*.jsonl` | **T+1** | 当日无快照 → `pending`（正常滞后，不是缺数据） |
| `narrative` | `_jev_snap/disc_*.json` | 日期级 | 公告/研报本身无时分，精度就是天 |

`pending` 与 `unavailable` 必须分开：前者是"按规则还没到"（龙虎榜 T+1 早间才全），
后者是"按规则不会有"或"本地没采"。混成一个词，第二天就无法判断该等还是该补。

## 三条硬纪律

1. **席位维不许拿前一日冒充当日**。当日卡必须写 `seat: pending(T+1)`，
   次日回填后重算——这是第五辑之外最容易犯的自欺（用昨天的主力给今天的涨停当证据）。
2. **零联网**。四个维全部只读本地。要补数据是采集工位的事，不在渲染链路里做。
3. **不调 LLM**。卡面规则拼装，不消耗云端额度，也不引入"模型看多"这种不可复算的东西。

## 验收口径：已知率，但已知率**不得单独用**（裁定 C 案）

`known = ok + none`。`none`（确认无记录）算已知是有理由的：「涨停但当天没公告」本身
就是判据，不是缺数据。但**单独报一个 100% 会自我安慰**，因为它把三件事压成了零：
`pending`（按规则没到）、`partial`（拿到的不可采信）、`unavailable`（取不到）。

所以本模块的验收结论是 `acceptance.verdict` 三档，不看百分比：

| 档 | 条件 | 含义 |
|---|---|---|
| `pass` | 主维全部 `ok`/`none` | 两维都拿到了，可以拿去用 |
| `wait` | 无 unavailable/partial，但有 `pending` | 数据按规则还没到（龙虎榜 T+1），等 |
| `fail` | 主维出现 `unavailable` 或 `partial` | 取不到或不可采信，这张卡这两维作废 |

百分比（`primary_known_pct` / `all_dims_known_pct`）只作为**附注**出现在同一行，
必须与四态计数同时出现，禁止单独引用。

## 与 `jev_graph.minute_profile` 的关系

时间维**直接复用** `minute_profile` 的口径（首次异动 / 一字板 at_open / 封板时刻），
只把取数换成只读本地（`loader=_mins_local_ro`）。判定逻辑不复制第二份——
复制必然漂移，两页会给出不同结论（判例 #25）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUANT = os.path.join(STATION, "quant_backtest")
MINS_DIR = os.path.join(QUANT, "data", "tushare", "stk_mins")
DAILY_DIR = os.path.join(QUANT, "data", "tushare", "daily")
SNAP_DIR = os.path.join(QUANT, "data", "tushare", "_jev_snap")
SEAT_DIR = os.path.join(STATION, "data", "hm_detail")
TOP_DIR = os.path.join(STATION, "data", "top_list")

# 主维 / 附表（2026-11-13 裁定 A 案）。顺序即展示顺序，主维在前。
PRIMARY_DIMS = ("flow", "seat")
AUX_DIMS = ("time", "narrative")

_DIM_LABEL = {"flow": "资金", "seat": "席位", "time": "时间", "narrative": "叙事"}

_STATUS_ZH = {"ok": "有", "partial": "部分（不可采信）", "pending": "待补（T+1）",
              "none": "无该票记录", "unavailable": "取不到"}


def _portal_path():
    p = os.path.dirname(os.path.abspath(__file__))
    if p not in sys.path:
        sys.path.insert(0, p)


def _d8(day) -> str:
    return "".join(ch for ch in str(day or "") if ch.isdigit())[:8]


def _dash(day) -> str:
    d = _d8(day)
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


# ── 只读取数 ───────────────────────────────────────────────
def _mins_local_ro(ts_code: str, day: str):
    """只读本地分钟（不回落远端）。给 `minute_profile` 当 loader 用。

    返回与 `jev_graph._mins` 同形状的 DataFrame，`attrs["source"]="local_ro"`。
    """
    try:
        import pandas as pd
    except Exception:  # noqa: BLE001
        return None
    d = _d8(day)
    path = os.path.join(MINS_DIR, "%s_%s.parquet"
                        % (str(ts_code).replace(".", "_"), d[:6]))
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    if df is None or not len(df) or "trade_time" not in df.columns:
        return None
    key = _dash(d)
    sub = df[df["trade_time"].astype(str).str[:10] == key]
    if not len(sub):
        return None
    sub = sub.sort_values("trade_time").reset_index(drop=True)
    sub.attrs["source"] = "local_ro"
    sub.attrs["tbl"] = None
    return sub


def _session_open(day, now=None) -> bool:
    """当日是否**尚未收盘**——用于把「按规则未到」与「取不到」分开。

    收盘终值类数据（`ts_moneyflow`）盘中必然没有，这不是缺数据，是精度定义：
    它只有在收盘那一刻才成立。若盘中把 `unavailable` 判成 fail，11-16 接盘中
    时每一张卡都会 fail，而实际上它们只是"还没到"。

    只认"当天 + 未过 15:30"。查历史日一律 False：历史日已收盘，再没有就是真缺。
    """
    d = _d8(day)
    now = now or datetime.now()
    if d != now.strftime("%Y%m%d"):
        return False
    return now.strftime("%H%M") < "1530"


def _daily_of(day: str, code: str):
    """当日日线一行 → dict / None。日线按日一个 parquet，全市场 5,500+ 只。"""
    try:
        import pandas as pd
    except Exception:  # noqa: BLE001
        return None
    path = os.path.join(DAILY_DIR, "d_%s.parquet" % _d8(day))
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001
        return None
    hit = df[df["ts_code"] == code]
    if not len(hit):
        return None
    return hit.iloc[0].to_dict()


def _json_snapshot(kind: str, day: str):
    path = os.path.join(SNAP_DIR, "%s_%s.json" % (kind, _d8(day)))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _jsonl_rows(folder: str, day: str) -> list | None:
    path = os.path.join(folder, "%s.jsonl" % _d8(day))
    if not os.path.exists(path):
        return None
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    return out


# ── 四维 ──────────────────────────────────────────────────
def _dim_time(code, day, limit_price, pre_close, name, remote=None):
    """分钟维。口径全交给 `jev_graph.minute_profile`，这里只选 loader。

    `remote=None` 时读环境 `JEV_CROSS_MINS_REMOTE`（默认 on）：**on 走
    jev_graph 既有链路**（本地优先 → ts_stk_mins → ts_rt_min → 写回本地），
    off 只读本地 parquet。这条与 `jev_graph` 的分钟层完全一致，不是本模块
    自己放宽——本地 stk_mins 只覆盖中证800×近1年，涨停小票多在小盘，纯本地
    会把时间维打成 0（11-10 实测 2/4 维，时间维全空）。

    但**来源必须写进卡面**：local / ts_stk_mins / ts_rt_min 的完整度不同
    （rt_min 只有 237 根、缺收盘那根），混成一个「有分钟」会掩盖精度差异。
    """
    _portal_path()
    if remote is None:
        remote = os.environ.get("JEV_CROSS_MINS_REMOTE", "on").lower() not in (
            "0", "off", "false")
    loader = None if remote else _mins_local_ro
    try:
        from jev_graph import minute_profile
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "precision": "minute",
                "source": "stk_mins", "data": None,
                "note": "分钟口径模块不可用: %s" % exc}
    p = minute_profile(code, day, limit_price=limit_price,
                       pre_close=pre_close, loader=loader)
    if not p.get("ok"):
        return {"status": "unavailable", "precision": "minute",
                "source": "stk_mins", "data": None,
                "note": ("分钟取不到（本地未覆盖且远端未命中）；"
                         "补法：tools/jev_mins_fetch.py --day %s" % _d8(day))}
    tbl = p.get("tbl") or ("local" if p.get("source") == "local_ro" else "unknown")
    bars = p.get("bars") or 0
    fb = str(p.get("first_bar") or "")
    hhmm = fb[11:16] if len(fb) >= 16 else None
    # 完整交易日 = 241 根、首根 09:30。两者缺一，`times[0]` 就不是开盘，
    # `at_open` / `first_move` / `seal_time` 全部失去基准（实测 300264 的
    # rt_min 只有 85 根、从 13:35 起，minute_profile 会把 13:35 报成
    # first_move 且 at_open=True，看着像「一字板」——这是数据缺陷冒充结论）。
    complete = bars >= 200 and hhmm in ("09:25", "09:30", "09:31")
    data = {"first_move": p.get("first_move"),
            "first_move_pct": p.get("first_move_pct"),
            "at_open": p.get("at_open"), "gap_pct": p.get("gap_pct"),
            "seal_time": p.get("seal_time"),
            "bars": bars, "first_bar": fb, "last_bar": p.get("last_bar"),
            "spike": p.get("spike"),
            "high_pct": p.get("high_pct"),
            "close_pct": p.get("close_pct")}
    if not complete:
        for k in ("first_move", "first_move_pct", "at_open", "gap_pct", "seal_time"):
            data[k] = None
        return {"status": "partial", "precision": "minute", "source": tbl,
                "data": data,
                "note": ("分钟只有 %d 根、起点 %s（非完整交易日）：首次异动与封板"
                         "时刻**不可采信**，已置空；仅根数/区间可看"
                         % (bars, hhmm or "?"))}
    if tbl == "ts_rt_min":
        # 与 tools/jev_mins_fetch.py 同一口径：rt_min 只有 237 根、缺 15:00
        # 收盘集合竞价那根，落盘会被固化成「已补齐」，所以那边拒写；这边
        # 允许看，但必须降级标注——**尾盘封板的票会因此判成没封板**。
        return {"status": "partial", "precision": "minute", "source": tbl,
                "data": data,
                "note": ("来源 ts_rt_min：只有 237 根、缺 15:00 收盘那根，"
                         "尾盘封板可能漏判；要定论需 ts_stk_mins 或 tushare 完整源")}
    return {"status": "ok", "precision": "minute", "source": tbl,
            "data": data, "note": None}


def _dim_flow(day, c6, now=None):
    snap = _json_snapshot("flow", day)
    if snap is None:
        if _session_open(day, now=now):
            # 盘中：收盘终值还没生成。这是**精度定义**，不是缺数据——
            # 判成 unavailable 会让每张盘中卡都 fail（11-16 接盘中时的必修）。
            return {"status": "pending", "precision": "eod",
                    "source": "ts_moneyflow", "data": None,
                    "note": ("当日未收盘：资金流是收盘终值，盘中不可用。"
                             "收盘后同一张卡重跑即有（不需要补采）")}
        return {"status": "unavailable", "precision": "eod",
                "source": "ts_moneyflow", "data": None,
                "note": ("该日已收盘但无资金流快照；补法：一次 build 会落 "
                         "_jev_snap/flow_%s.json" % _d8(day))}
    v = snap.get(c6)
    if not v:
        return {"status": "none", "precision": "eod",
                "source": "ts_moneyflow", "data": None,
                "note": "该票当日无资金流记录"}
    main_net = v.get("main_net")
    net_mf = v.get("net_mf")
    # divergence：主力（大单+特大单）净买、但全档净额流出 —— 11-06 新型电力
    # 三只涨停全背离，是「大单接、散户跑」的判据（jev_flow 同口径）。
    #
    # **三态，不是布尔**：北交所等无资金流覆盖的票 net_mf 落 0.0（实测 11-10
    # 362/5580 只），那是「没数据」不是「净额为零」。判成 False 会把
    # 「不知道」写成「没背离」——同一句话两种含义，正是判例 #38 那类错误。
    if main_net is None or net_mf is None or net_mf == 0.0 or main_net == 0.0:
        divergence = None
    else:
        divergence = bool(main_net > 0 and net_mf < 0)
    return {"status": "ok", "precision": "eod", "source": "ts_moneyflow",
            "data": {"main_net": main_net, "net_mf": net_mf,
                     "main_buy": v.get("main_buy"),
                     "main_sell": v.get("main_sell"),
                     "divergence": divergence},
            "note": ("单位万元；主力=大单+特大单，收盘终值，盘中不可用"
                     + ("；net_mf=0 视为无覆盖，背离判不了" if divergence is None else ""))}


def _dim_seat(day, code, c6):
    rows = _jsonl_rows(SEAT_DIR, day)
    if rows is None:
        return {"status": "pending", "precision": "T+1",
                "source": "ts_hm_detail", "data": None,
                "note": ("龙虎榜 T+1 早间才全，当日查不到是正常现象；"
                         "补法：tools/top_list_fetch.py --day %s（次日）" % _d8(day))}
    mine = [x for x in rows if str(x.get("ts_code") or "") == code]
    if not mine:
        return {"status": "none", "precision": "T+1",
                "source": "ts_hm_detail", "data": None,
                "note": "该票当日未上龙虎榜（席位明细只覆盖上榜股）"}
    _portal_path()
    try:
        import jev_seat
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "precision": "T+1",
                "source": "ts_hm_detail", "data": None,
                "note": "席位分层模块不可用: %s" % exc}
    seats = jev_seat.load_seats(day)
    s = jev_seat.seat_of(seats, c6)
    if not s:
        return {"status": "none", "precision": "T+1",
                "source": "ts_hm_detail", "data": None,
                "note": "席位聚合未命中（原始行存在但解析失败）"}
    return {"status": "ok", "precision": "T+1", "source": "ts_hm_detail",
            "data": {"n_seats": s.get("n_seats"),
                     "seat_net": s.get("seat_net"),
                     "by_kind": {k: v.get("net") for k, v in (s.get("by_kind") or {}).items()},
                     "by_group": {k: v.get("net") for k, v in (s.get("by_group") or {}).items()},
                     "top_buyer": (s.get("top_buyer") or {}).get("display"),
                     "top_buyer_amt": (s.get("top_buyer") or {}).get("buy"),
                     "top_seller": (s.get("top_seller") or {}).get("display")},
            "note": "席位净额≠龙虎榜净额（只披露部分营业部）；persona 已匿名化"}


def _dim_narrative(day, c6):
    snap = _json_snapshot("disc", day)
    if snap is None:
        return {"status": "unavailable", "precision": "date",
                "source": "stock_notice/stock_report", "data": None,
                "note": ("无当日披露快照；补法：_jev_snap/disc_%s.json "
                         "需采集侧落盘" % _d8(day))}
    v = snap.get(str(c6)[:6]) or {}
    reports, notices = v.get("reports") or [], v.get("notices") or []
    if not reports and not notices:
        return {"status": "none", "precision": "date",
                "source": "stock_notice/stock_report", "data": None,
                "note": "当日无公告/研报"}
    return {"status": "ok", "precision": "date",
            "source": "stock_notice/stock_report",
            "data": {"n_reports": len(reports), "n_notices": len(notices),
                     "ratings": [r.get("rating") for r in reports if r.get("rating")],
                     "notice_types": [n.get("type") for n in notices if n.get("type")],
                     "titles": [(n.get("title") or "")[:40] for n in notices[:3]]},
            "note": "公告/研报无时分，精度到天；不得用于 Δmin"}


# ── 组卡 ──────────────────────────────────────────────────
def build_card(code: str, day: str, remote_mins: bool | None = None,
               now=None) -> dict:
    """单票单日四维卡。→ dict（见模块头四维表）。

    `remote_mins` 控制时间维要不要走远端兜底（`None` = 读环境）。**批量出卡
    必须传 False**：本地未覆盖的票每只都要发 ts_stk_mins + ts_rt_min 两次请求，
    单票最坏 48s（timeout 12s × retries 1 × 2 表），100 只就是半小时——实测
    批量跑 3 分钟 0 张卡出来。批量场景的正确做法是先出本地卡（时间维标
    unavailable），再对需要深看的票单只补远端。
    """
    _portal_path()
    d = _d8(day)
    code = str(code)
    c6 = code.split(".")[0]

    daily = _daily_of(d, code)
    name = None
    top_rows = _jsonl_rows(TOP_DIR, d) or []
    for x in top_rows:
        if str(x.get("ts_code") or "") == code:
            name = x.get("name")
            break

    card = {}
    card["code"] = code
    card["c6"] = c6
    card["day"] = d
    card["name"] = name
    card["close"] = None
    card["pre_close"] = None
    card["pct"] = None
    card["limit"] = {"is_limit_up": None, "touch_only": None,
                     "ratio": None, "price": None}
    card["dims"] = {}
    card["completeness"] = {"n_ok": 0, "n_total": 4, "pct": 0.0}
    card["primary"] = {}
    card["aux"] = {}
    card["acceptance"] = {}
    card["warnings"] = []

    if daily is None:
        card["warnings"].append("无当日本地日线，无法判定涨停档位")
        limit_price = pre_close = None
    else:
        card["close"] = float(daily.get("close") or 0) or None
        card["pre_close"] = float(daily.get("pre_close") or 0) or None
        card["pct"] = round(float(daily.get("pct_chg") or 0), 2) or None
        if name is None:
            # ST 5% 档靠名称识别；拿不到名称只能用涨幅反推，且必须留痕
            card["warnings"].append("未取到股票名称，ST 档位按涨幅反推，可能误判")
        try:
            from jev_graph import limit_state
            lu, touch, ratio, lp = limit_state(
                card["close"], float(daily.get("high") or 0), card["pre_close"],
                c6, name or "", pct=card["pct"])
            card["limit"] = {"is_limit_up": lu, "touch_only": touch,
                             "ratio": ratio, "price": lp}
            limit_price = lp
            pre_close = card["pre_close"]
        except Exception as exc:  # noqa: BLE001
            card["warnings"].append("涨停判定不可用: %s" % exc)
            limit_price = pre_close = None

    card["dims"]["time"] = _dim_time(code, d, limit_price, pre_close, name,
                                     remote=remote_mins)
    card["dims"]["flow"] = _dim_flow(d, c6, now=now)
    card["dims"]["seat"] = _dim_seat(d, code, c6)
    card["dims"]["narrative"] = _dim_narrative(d, c6)

    # 五态分列：`ok` 有值、`none` 确认无记录（也是有信息量的「空」，比如「涨停但
    # 当天没公告」本身就是判据）、`partial` 拿了但不可采信、`pending` 按规则未到、
    # `unavailable` 取不到。**不能合并成「完整度」一个数**：把「确认没有」和
    # 「不知道」算成同一格，等于把判例 #38 那条错误换个地方重犯。
    cnt = {k: 0 for k in ("ok", "none", "partial", "pending", "unavailable")}
    for v in card["dims"].values():
        cnt[v["status"]] = cnt.get(v["status"], 0) + 1
    cnt["n_known"] = cnt["ok"] + cnt["none"]
    cnt["n_total"] = 4
    cnt["pct_known"] = round(cnt["n_known"] / 4 * 100, 1)
    card["completeness"] = cnt

    # ── 两维主卡（裁定 A）：验收只看 flow + seat ──
    p = {"known": 0, "unavailable": 0, "pending": 0, "partial": 0,
         "none": 0, "ok": 0, "dims": {}}
    for k in PRIMARY_DIMS:
        s = card["dims"][k]["status"]
        p["dims"][k] = s
        if s in ("ok", "none"):
            p["known"] += 1
            p[s] += 1
        elif s in p:
            p[s] += 1
    p["n_total"] = len(PRIMARY_DIMS)
    p["pct_known"] = round(p["known"] / p["n_total"] * 100, 1)
    card["primary"] = p
    card["aux"] = {k: card["dims"][k]["status"] for k in AUX_DIMS}

    # ── 验收（裁定 C）：三档判定，不拿百分比当结论 ──
    card["acceptance"] = {
        "metric": "primary_known_rate",
        "known": p["known"], "n_total": p["n_total"], "pct": p["pct_known"],
        "verdict": _verdict_primary(p),
        "breakdown": {k: p[k] for k in
                      ("ok", "none", "pending", "partial", "unavailable")},
        "reference_all_dims_pct": cnt["pct_known"],
        "guard": ("已知率不得单独引用：必须与四态计数同行出现；"
                  "验收看 verdict，不看百分比"),
    }
    return card


def _verdict_primary(p: dict) -> str:
    """主维三档判定。**`pending` 一律不归入 pass**——「还没到」不等于「没问题」。

    pass  主维全部 ok/none，两维拿到了
    wait  无 unavailable/partial，但有 pending（龙虎榜 T+1，等就有）
    fail  主维出现 unavailable 或 partial，这两维对本案作废
    """
    if p["unavailable"] or p["partial"]:
        return "fail"
    if p["pending"] or p["known"] < p["n_total"]:
        return "wait"
    return "pass"


def acceptance_summary(cards: list) -> dict:
    """批量验收汇总。`pct_pass` 是主指标；两个 known 百分比只作附注。

    为什么不用「已知率」当主指标（裁定 C 的反面）：已知率把 `pending` 之外的三态
    合成一个分母，一张卡「资金有、席位取不到」和「资金有、席位没赶上」在百分比上
    可能看起来差不多，但一张该等、一张该修。`pass/wait/fail` 分档才分得开。
    """
    n = len(cards)
    if not n:
        return {"n": 0, "pass": 0, "wait": 0, "fail": 0, "pct_pass": 0.0,
                "guard": "无卡，不出验收结论"}
    v = {"pass": 0, "wait": 0, "fail": 0}
    for c in cards:
        v[c["acceptance"]["verdict"]] = v.get(c["acceptance"]["verdict"], 0) + 1
    pk = sum(c["primary"]["known"] for c in cards)
    ak = sum(c["completeness"]["n_known"] for c in cards)
    return {"n": n, "pass": v["pass"], "wait": v["wait"], "fail": v["fail"],
            "pct_pass": round(v["pass"] / n * 100, 1),
            "primary_known_pct": round(pk / (n * len(PRIMARY_DIMS)) * 100, 1),
            "all_dims_known_pct": round(ak / (n * 4) * 100, 1),
            "guard": ("all_dims_known_pct 只作参考（含附表两维，受分钟覆盖拖累）；"
                      "验收主指标是 pct_pass")}


def candidate_codes(day: str, limit_up_only: bool = True) -> list:
    """当日出卡票池 —— **涨停/触板事件池**，不是选股池。

    两步，顺序不能反：
    1. 日线粗筛 `pct_chg >= 9.5`（只为不漏掉 20%/30% 档的票，不做判定）；
    2. 用 `limit_state` 按档位定死判定，只留 `is_limit_up` 或 `touch_only`。

    为什么要第 2 步：龙虎榜票不等于涨停票（换手 20%、跌 7% 也会上榜）。首版
    直接拿龙虎榜当池，前 3 张卡全是 -0.32% / +1.72% 的非涨停票——「涨停事件
    交叉卡」里混进没涨停的票，是**把对象换了还不自知**（判例 #28 同族）。

    `limit_up_only=False` 时保留全部粗筛票（调试用，不用于出卡）。
    """
    d = _d8(day)
    names = {}
    for x in (_jsonl_rows(TOP_DIR, d) or []):
        c = str(x.get("ts_code") or "")
        if c and x.get("name"):
            names[c] = x.get("name")

    rows = []
    try:
        import pandas as pd
        path = os.path.join(DAILY_DIR, "d_%s.parquet" % d)
        if os.path.exists(path):
            df = pd.read_parquet(path, columns=["ts_code", "pct_chg", "close",
                                                "high", "pre_close"])
            for r in df.itertuples(index=False):
                if r.pct_chg is None:
                    continue
                # 粗筛两条腿，缺一不可：
                #   pct>=9.5    收盘接近涨停（漏不掉 20%/30% 档的票）
                #   high>=4.5%  盘中摸过板（炸板回落收 5% 的票，只看涨幅会漏；
                #               4.5% 是为了覆盖主板 ST 的 5% 档）
                hp = ((r.high / r.pre_close - 1) * 100
                      if r.pre_close else 0.0)
                if float(r.pct_chg) < 9.5 and hp < 4.5:
                    continue
                rows.append({"code": r.ts_code, "pct": float(r.pct_chg),
                             "close": float(r.close or 0),
                             "high": float(r.high or 0),
                             "pre_close": float(r.pre_close or 0)})
    except Exception:  # noqa: BLE001
        # 日线读不到就退化成龙虎榜池，并且只保留判定得出的部分
        return list(names) if not limit_up_only else []

    if not limit_up_only:
        return sorted(names) + [r["code"] for r in rows if r["code"] not in names]

    _portal_path()
    try:
        from jev_graph import limit_state
    except Exception:  # noqa: BLE001
        return [r["code"] for r in sorted(rows, key=lambda z: -z["pct"])]

    out = []
    for r in rows:
        code = r["code"]
        c6 = code.split(".")[0]
        try:
            lu, touch, _ratio, _lp = limit_state(
                r["close"], r["high"], r["pre_close"], c6,
                names.get(code) or "", pct=r["pct"])
        except Exception:  # noqa: BLE001
            lu = touch = False
        if lu or touch:
            out.append(code)
    out.sort(key=lambda c: -next((r["pct"] for r in rows if r["code"] == c), 0))
    return out


def _verdict_zh(v) -> str:
    return {"pass": "通过", "wait": "等数据（T+1）", "fail": "不通过"}.get(v, str(v))


def render_markdown(card: dict) -> str:
    """一张卡的文本面。**只陈述，不结论**。

    主维在前、附表在后，且显式标注「附表不参与验收」——不标的话，读者会把
    「时间维取不到」当成这张卡的质量问题，而它其实是采集层的覆盖问题。
    """
    L = []
    lim = card.get("limit") or {}
    tag = "涨停" if lim.get("is_limit_up") else ("触板未封" if lim.get("touch_only") else "非涨停")
    L.append("## %s %s · %s · %s" % (card["code"], card.get("name") or "-",
                                     card["day"], tag))
    L.append("")
    ac = card.get("acceptance") or {}
    pr = card.get("primary") or {}
    cp = card["completeness"]
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 收盘 / 昨收 | %s / %s |" % (card.get("close"), card.get("pre_close")))
    L.append("| 涨幅 | %s%% |" % card.get("pct"))
    L.append("| 涨停价（档位 %s） | %s |"
             % (lim.get("ratio"), lim.get("price")))
    L.append("| 验收（主维：资金+席位） | **%s**｜已知 %d/%d（%s%%） |"
             % (_verdict_zh(ac.get("verdict")), pr.get("known", 0),
                pr.get("n_total", len(PRIMARY_DIMS)), pr.get("pct_known")))
    L.append("| 四态明细 | 有值 %d · 确认无 %d · 部分 %d · 待补 %d · 取不到 %d"
             "（全四维，含附表） |"
             % (cp["ok"], cp["none"], cp["partial"], cp["pending"],
                cp["unavailable"]))
    L.append("")
    L.append("**主维**（参与验收）")
    L.append("")
    L.append("| 维 | 状态 | 精度 | 来源 | 数据 |")
    L.append("|---|---|---|---|---|")
    for k in PRIMARY_DIMS:
        d = card["dims"].get(k) or {}
        L.append("| %s %s | %s | %s | %s | %s |"
                 % (_DIM_LABEL.get(k, k), k,
                    _STATUS_ZH.get(d.get("status"), d.get("status")),
                    d.get("precision"), d.get("source"),
                    _compact(d.get("data"))))
    L.append("")
    L.append("附表（**不参与验收**：精度天然低于主维）")
    L.append("")
    L.append("| 维 | 状态 | 精度 | 来源 | 数据 |")
    L.append("|---|---|---|---|---|")
    for k in AUX_DIMS:
        d = card["dims"].get(k) or {}
        L.append("| %s %s | %s | %s | %s | %s |"
                 % (_DIM_LABEL.get(k, k), k,
                    _STATUS_ZH.get(d.get("status"), d.get("status")),
                    d.get("precision"), d.get("source"),
                    _compact(d.get("data"))))
    notes = [d.get("note") for k, d in card["dims"].items() if d.get("note")]
    if card.get("warnings"):
        notes = list(card["warnings"]) + notes
    if notes:
        L.append("")
        for n in notes:
            L.append("- %s" % n)
    return "\n".join(L)


def _compact(data) -> str:
    if not data:
        return "—"
    if isinstance(data, dict):
        keep = []
        for k, v in data.items():
            if v is None or v == [] or v == {}:
                continue
            if isinstance(v, dict):
                v = ",".join("%s=%s" % (kk, vv) for kk, vv in v.items()
                             if vv not in (None, 0, 0.0))
                if not v:
                    continue
                v = "{%s}" % v
            elif isinstance(v, list):
                v = "[%s]" % ",".join(str(x) for x in v[:3])
            keep.append("%s=%s" % (k, v))
        return "; ".join(keep) or "—"
    return str(data)


# ── CLI ───────────────────────────────────────────────────
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="涨停事件交叉卡 v1（只读，不联网）")
    ap.add_argument("--day", required=True)
    ap.add_argument("--code", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--any", action="store_true",
                    help="票池放宽到全部粗筛票（调试用，含未涨停）")
    ap.add_argument("--remote", action="store_true",
                    help="批量出卡也走远端分钟兜底（慢，慎用；单票查询默认已开）")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    codes = a.code or (candidate_codes(a.day, limit_up_only=not a.any)[:a.limit]
                       if a.all else [])
    if not codes:
        print("没有票可出卡：给 --code，或 --all（票池=龙虎榜∪涨停粗筛）")
        return 2
    # 批量一律只读本地：单票走远端兜底会拖到分钟级，100 只跑不完（实测 3 分钟
    # 0 产出）。要深看的时间维，用 --code 单只补。
    remote = True if a.code else bool(a.remote)
    cards = [build_card(c, a.day, remote_mins=remote) for c in codes]
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    for c in cards:
        if a.json:
            txt = json.dumps(c, ensure_ascii=False, indent=2)
        else:
            txt = render_markdown(c)
        print(txt)
        print()
        if a.out:
            fn = "%s_%s.%s" % (c["day"], c["c6"], "json" if a.json else "md")
            with open(os.path.join(a.out, fn), "w", encoding="utf-8") as f:
                f.write(txt + "\n")
    s = acceptance_summary(cards)
    print("== %d 张卡｜主维验收：通过 %d · 等数据 %d · 不通过 %d（通过率 %s%%）"
          % (s["n"], s["pass"], s["wait"], s["fail"], s["pct_pass"]))
    print("   参考（不得单独引用）：主维已知率 %s%%｜全四维已知率 %s%%（含附表，"
          "受小票分钟覆盖拖累）" % (s["primary_known_pct"], s["all_dims_known_pct"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
