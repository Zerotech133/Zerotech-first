# -*- coding: utf-8 -*-
"""上游健康度盘点（2026-11-05）。

回答一个 govern 回答不了的问题：报告里的「时效欠账」，到底是
**上游停了**、**上游下架了**、还是**我们自己没拉**？
三者处置完全不同：换源 / 下线 / 补链路。散在各次报告里就只能靠记忆。

四类证据，可信度从高到低：

  1 远端内容水位  xtcloud `/api/data/status` 的 `latest`（181 张表里 124 张有值）。
  2 远端抽样水位  `latest` 为空的表，按 offset 分层抽 6 个点读日期列最大值。
                  **不能用「尾页 = 最新」**：实测 tdx_sector_daily 的
                  offset=rows-300 取回的是 2005-06-08，ts_stk_rewards 是 1993 年。
                  该库不按时间序存储，只有 ts_news 这类才恰好有序。
                  抽样只能给出"上游至少到过某日"的下界，证不出"上游没有更新"，
                  所以抽不到更新时判 STALLED_WEAK（疑似停更），不判 STALLED。
  3 远端存活信号  status 里存在该表 + `all_dt`（该表最后一次被远端重写）。
                  `all_dt` 只证明"远端管线还在跑这张表"，不证明内容更新：
                  ts_slb_sec 的 all_dt 是 11-04，内容停在 2024-09-30。
  4 本地水位      catalog.freshness（已按 date_col 取 max）。

判定（verdict）：

  OK            上游活跃 · 本地已同步（含"远端领先 ≤ 容差"的批次时点差）
  BEHIND        远端领先超容差 · 本地确实没拉到          → 补采
  STALLED       远端 latest == 本地且本地超 SLA           → 上游停更（硬证据）
  STALLED_WEAK  远端 latest 为空，抽样未见更新            → 疑似停更（软证据）
  GONE          远端根本没有这张表                        → 上游下架
  CALIBER       本地/远端口径不同（公告日 vs 报告期）     → 不改数，改口径
  FUTURE        水位落在未来（交易日历/预测表）           → 滞后无意义
  NO_LINK       上游有表、本地无数据（planned）           → 待接采集
  STATIC        静态参照表（list_date 当水位无意义）      → 豁免
  UNDECIDABLE   两端都取不到可比水位                      → 先补目录/日期列
  NO_REMOTE     该源没有远端水位接口                      → 只能看本地 SLA

用法：
  python upstream_health.py                     # 复用状态缓存，只做本地对账
  python upstream_health.py --live              # 重取 /api/data/status + 抽样探针
  python upstream_health.py --live --no-probe   # 只取状态，不做抽样
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools" / "datawarehouse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASE = "http://rookie.xtcloud-tech.com:48088"
HERE = Path(__file__).resolve().parent
OUT_DIR = ROOT / "reports" / "data_hub"
STATUS_CACHE = OUT_DIR / "xt_status_raw.json"
PROBE_CACHE = OUT_DIR / "xt_probe_cache.json"

SAMPLES = 6  # 每表抽样点数（含首尾），1 请求/点
SAMPLE_LIMIT = 200  # 每点取多少行读日期列
MAX_PROBE = 40  # 抽样探针表数上限（结果进 xt_probe_cache.json，重跑几乎零成本）

# 远端列名探测顺序（实测 xtcloud 各表日期列名不统一）
DATE_CANDIDATES = (
    "trade_date", "ann_date", "cal_date", "week_date", "publish_date", "pub_time",
    "report_date", "end_date", "surv_date", "in_date", "ipo_date", "issue_date",
    "res_ann_date", "imp_ann_date", "actual_date", "date", "datetime", "list_date",
    "month", "week", "prd",
)
PERIOD_COLS = {"month", "week", "prd", "year", "年份", "年", "quarter"}

# 公告日类列：与远端"报告期" latest 不同口径，不能直接相减
PUB_COLS = {"ann_date", "surv_date", "pub_time", "publish_date", "pub_date",
            "notice_date", "f_ann_date", "actual_date", "res_ann_date", "imp_ann_date"}

# 各频率的"领先容差"（天）。本地批次每日 17:35 跑、远端 17:00 刷新，
# 所以 D 频表白天看永远是"远端领先 1 天"，那是时点差不是欠账。
TOLERANCE = {"1min": 1, "RT": 1, "D": 3, "W": 7, "M": 35, "Q": 95, "A": 366}

# catalog 里 source=xtcloud 但实际不在 xtcloud 远端的表（存放于 tdx 仓、由
# global_fut_sync 维护）。不排除就会被判"上游下架"。
NOT_XTCLOUD = {"ts_global_fut"}

VERDICT_CN = {
    "OK": "上游活跃·本地同步",
    "BEHIND": "上游领先·本地未采",
    "STALLED": "上游停更（确认）",
    "STALLED_WEAK": "疑似停更（抽样未见更新）",
    "GONE": "上游下架",
    "CALIBER": "口径不同（公告日/报告期）",
    "FUTURE": "水位含未来日期",
    "NO_LINK": "未接采集",
    "STATIC": "静态参照表",
    "UNDECIDABLE": "无法判定",
    "NO_REMOTE": "该源无远端水位接口",
    "LOCAL_NEWER": "本地反超远端",
}

# 非 xtcloud 源的旁证：这些源没有"远端水位"接口，只能靠本地证据 + 已知事实。
SOURCE_NOTES = {
    "tushare": "TushareSync 每日 23:30（2026-11-05 实测 6695s/exit=0）；长尾 8 表由 "
               "TushareTailSync 02:10 兜底。无逐表水位接口，只能按本地 SLA 判。",
    "xueqiu": "雪球 API 2026-10 起 403 IP Blacklisted，xq_* 多为 planned 空表；属源级阻断。",
    "cnopendata": "静态历史包（年度/年鉴），一次性入库，无增量语义。",
    "guba": "东方财富股吧，按 watchlist 采集；帖子流用日期水位判停更不成立。",
    "pg": "v2_bar_daily 属外部 PG 库（EXTERNAL_TABLES 登记），采集不在本仓。",
    "legacy": "us_legacy 为美股历史遗留快照，已 deprecated。",
    "crypto": "crypto_daily 依赖本地 7890 代理，代理未开则当日无数据（历史遗留未解）。",
    "factor_library": "静态因子清单（272 因子），快照式，无时序语义。",
}

# 停更/下架表的换源建议。只写有依据的；写"无替代"也是一种结论。
REPLACEMENTS = {
    "ts_index_weekly": "ts_index_daily（远端 20261104 活跃）自聚合周线，口径可复现；"
                       "该表上游停在 10-09，缺 3 根周线，不再等。",
    "ts_tdx_member": "ts_ths_member（同花顺成分，382,731 行）+ ts_index_member_all 覆盖；"
                     "通达信板块成分上游停在 10-29。",
    "tdx_sector_daily": "ts_ths_daily / ts_dc_daily 替代；通达信板块指数日线上游止于 10-22，"
                        "同族 tdx_sector_min 远端 latest 同为 20261022。",
    "tdx_sector_min": "无替代（板块分钟线只有通达信）；按需一次性回补，不进日更。",
    "ts_rt_min": "无替代；71.7M 行实时分钟，远端 latest 仍在动（2026-11-05 11:29），"
                 "属「上游活跃但我们按设计不日更」，需要时按区间补。",
    "ts_slb_sec": "无需替代：转融通 2024 年后业务收紧，上游停在 2024-09-30。",
    "ts_slb_len": "无需替代：同上，转融通期限表已无增量。",
    "ts_kpl_concept": "ts_ths_index / ts_kpl_list 覆盖概念板块；开盘啦概念停在 2024-11-17。",
    "ts_tdx_index": "ts_ths_index / ts_dc_index 覆盖板块指数；通达信板块指数停在 2025-04-17。",
    "dknife_snap": "无替代：远端已无此表（status 181 张里查不到），本地为只读快照。",
    "tdx_sector_min_tail": "无替代：远端已下架，分钟深表由 tdx_sector_min 单独承载。",
    "ts_emotion_daily": "tdx_sector_daily 衍生重建（涨停/连板统计）；远端已下架。",
    "ts_emotion_ladder": "同 ts_emotion_daily，情绪阶梯可由涨停池重建。",
}


def _norm_date(value) -> dt.date | None:
    """'20261104' / '2026-11-04' / '2026-11-04 17:05:18' / NA → date|None。

    远端 latest 有四种形态（纯数字、横杠、带时分秒、null），本地 freshness 是
    YYYY-MM-DD。统一"取前 10 位里的数字"再校验，不做字符串比较。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in {"nan", "none", "nat", "null"}:
        return None
    digits = "".join(ch for ch in raw[:10] if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return dt.date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _norm_period(value) -> dt.date | None:
    """周期列：'202610'（月）→ 2026-10-01，'2026'（年）→ 2026-01-01。

    月/季/年频表的日期列不是 8 位日期，_norm_date 会一律返回 None，
    于是这些表永远"无水位"——catalog 里 26 张无日期表的成因之一。
    """
    if value is None:
        return None
    digits = "".join(ch for ch in str(value).strip()[:10] if ch.isdigit())
    if len(digits) >= 8:
        return _norm_date(digits)
    if len(digits) == 6:
        try:
            return dt.date(int(digits[:4]), int(digits[4:6]), 1)
        except ValueError:
            return None
    if len(digits) == 4:
        try:
            return dt.date(int(digits), 1, 1)
        except ValueError:
            return None
    return None


def _tolerance(freq: str | None) -> int:
    return TOLERANCE.get(str(freq or "D"), 3)


def classify(
    *,
    local: dt.date | None,
    remote: dt.date | None,
    present: bool = True,
    freq: str | None = "D",
    sla_days: int = 3,
    lag_days: int | None = None,
    lifecycle: str = "active",
    static_no_date: bool = False,
    date_col: str | None = None,
    rows: int = 0,
    sampled: bool = False,
    today: dt.date | None = None,
) -> tuple[str, str]:
    """纯函数：给定本地/远端水位 → (verdict, reason)。不做 IO，便于测。"""
    today = today or dt.date.today()
    if static_no_date:
        return "STATIC", "list_date/静态参照，无增量语义"
    if not present:
        return ("GONE", "远端 status 里无此表") if rows else ("NO_LINK", "远端无此表且本地空")
    if local is None and (lifecycle == "planned" or rows == 0):
        return "NO_LINK", "catalog 标 planned 且本地无数据"
    if local is None and remote is None:
        return "UNDECIDABLE", "两端都无水位"
    if local is None:
        return "UNDECIDABLE", "本地无水位（date_col=%s 未解析）" % (date_col or "-")
    if local > today:
        return "FUTURE", "本地水位 %s 落在未来（交易日历/预测类），滞后无意义" % local.isoformat()
    if remote is None:
        # 远端取不到水位不代表表有问题：SLA 内就是可用。首版把它一律判
        # UNDECIDABLE，43 张里绝大多数其实是"远端没暴露水位 + 本地在 SLA 内"。
        lag = lag_days if lag_days is not None else (today - local).days
        if lag <= sla_days:
            return "OK", "本地 %s 在 SLA %dd 内（远端未暴露水位）" % (
                local.isoformat(), sla_days)
        return "UNDECIDABLE", "远端无可用日期水位（latest 空/无日期列），本地滞后 %dd" % lag
    if remote > today:
        return "FUTURE", "远端 latest %s 落在未来（报告期/日历口径），不可比" % remote.isoformat()
    if remote > local:
        ahead = (remote - local).days
        if ahead <= _tolerance(freq):
            return "OK", "远端领先 %dd（≤容差 %dd，批次时点差）" % (ahead, _tolerance(freq))
        return "BEHIND", "远端领先 %dd（超容差 %dd）" % (ahead, _tolerance(freq))
    if remote == local:
        lag = lag_days if lag_days is not None else (today - local).days
        if lag > sla_days:
            if sampled:
                return "STALLED_WEAK", "抽样 %d 点最大 %s，未见更新，滞后 %dd > SLA %dd" % (
                    SAMPLES, local.isoformat(), lag, sla_days)
            return "STALLED", "两端同日止步 %s，滞后 %dd > SLA %dd" % (
                local.isoformat(), lag, sla_days)
        return "OK", "两端同步 %s（滞后 %dd ≤ SLA）" % (local.isoformat(), lag)
    # remote < local
    if date_col in PUB_COLS:
        return "CALIBER", "本地 %s 取 %s（公告日），远端 latest 是报告期，不可比" % (
            local.isoformat(), date_col)
    return "LOCAL_NEWER", "本地 %s 反而比远端 %s 新 %dd" % (
        local.isoformat(), remote.isoformat(), (local - remote).days)


def classify_local_only(
    *,
    local: dt.date | None,
    lag_days: int | None,
    sla_days: int,
    lifecycle: str = "active",
    static_no_date: bool = False,
    rows: int = 0,
    today: dt.date | None = None,
) -> tuple[str, str]:
    """非 xtcloud 源：没有"远端水位"接口，只能看本地是否在 SLA 内。

    不能把这类表判 GONE——xtcloud 的 status 里当然没有 tushare 的表，
    首版就是这样把 54 张 tushare 表算成"上游下架"的。
    """
    today = today or dt.date.today()
    if static_no_date:
        return "STATIC", "静态参照/清单表，无增量语义"
    if local is None and (lifecycle == "planned" or rows == 0):
        return "NO_LINK", "catalog 标 planned 且本地无数据"
    if local is None:
        return "UNDECIDABLE", "本地无水位（目录缺日期列）"
    if local > today:
        return "FUTURE", "本地水位 %s 落在未来（日历/预测类）" % local.isoformat()
    if lag_days is not None and lag_days <= sla_days:
        return "OK", "本地 %s 在 SLA %dd 内（该源无远端水位接口）" % (
            local.isoformat(), sla_days)
    return "NO_REMOTE", "滞后 %dd，但该源无远端水位接口，分不清停更还是漏采" % (lag_days or 0)


def _session():
    import requests

    s = requests.Session()
    # xtcloud 是直连可达的公网域名；trust_env=True 会继承宿主代理 → 403/60s 超时
    s.trust_env = False
    return s


def fetch_status(session=None, timeout: int = 90) -> dict:
    s = session or _session()
    r = s.get(BASE + "/api/data/status", timeout=timeout)
    r.raise_for_status()
    return r.json()


def load_status_cache(path: Path = STATUS_CACHE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_probe_cache(path: Path = PROBE_CACHE) -> dict:
    """抽样结果缓存：抽样 190 次请求约 10 分钟，重跑不该再付一次。

    只在 (表, 远端 rows) 未变时复用；rows 变了说明上游重写过表，必须重抽。
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_probe_cache(cache: dict, path: Path = PROBE_CACHE) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def _sample_offsets(rows: int, samples: int = SAMPLES) -> list[int]:
    """分层抽样点位：首、尾、以及中间等距若干点。

    注意"尾"只是 offset=rows-1，不代表最新数据——该库不按时间序存储。
    """
    if rows <= 0:
        return [0]
    if rows <= SAMPLE_LIMIT:
        return [0]
    step = max(1, rows // max(1, samples))
    pts = {0, rows - 1}
    for i in range(1, samples):
        pts.add(min(rows - 1, i * step))
    return sorted(pts)


def probe_watermark(session, table: str, rows: int, date_col: str | None = None,
                    samples: int = SAMPLES, limit: int = SAMPLE_LIMIT
                    ) -> tuple[dt.date | None, str | None, int]:
    """分层抽样读日期列最大值 → (抽样最大日期, 实际用的列, 读到的行数)。

    返回的是**下界**：证得了"上游至少到过这天"，证不出"上游没有更新"。
    """
    offsets = _sample_offsets(rows, samples)
    col = date_col
    best = None
    seen = 0
    for off in offsets:
        r = session.get(BASE + "/api/data/table/" + table,
                        params={"offset": off, "limit": limit}, timeout=90)
        r.raise_for_status()
        d = r.json()
        cols = d.get("columns") or []
        data = d.get("rows") or []
        if not cols or not data:
            continue
        if col not in cols:
            col = next((c for c in DATE_CANDIDATES if c in cols), None)
        if col is None:
            col = next((c for c in cols if "date" in str(c).lower()), None)
        if col is None:
            return None, None, seen
        idx = cols.index(col)
        parse = _norm_period if col in PERIOD_COLS else _norm_date
        for row in data:
            try:
                v = parse(row[idx])
            except (IndexError, TypeError):
                continue
            seen += 1
            if v and (best is None or v > best):
                best = v
    return best, col, seen


def _local_state(tables: dict, data: dict, today: dt.date) -> dict:
    """本地口径：freshness / lag / sla / lifecycle / static，与 govern 完全一致。"""
    from tools.data_hub import govern  # noqa: WPS433

    try:
        import lifecycle  # noqa: WPS433
    except Exception:  # noqa: BLE001
        lifecycle = None
    exempt = set(data.get("freshness_exempt") or [])
    overrides = data.get("freshness_sla_overrides") or {}
    out = {}
    for name, meta in tables.items():
        meta = meta or {}
        freq = meta.get("freq")
        local = govern.parse_freshness(meta.get("freshness"))
        lag = (today - local).days if local else None
        sla = int(overrides.get(name) or govern._sla_days(data, freq))
        out[name] = {
            "source": meta.get("source") or "?",
            "freq": freq,
            "date_col": meta.get("date_col"),
            "rows": int(meta.get("rows") or 0),
            "local": local,
            "lag": lag,
            "sla": sla,
            "static": bool(meta.get("static_no_date")),
            "exempt": name in exempt,
            "lifecycle": (lifecycle.status_for(name) if lifecycle else "active"),
        }
    return out


def audit(
    *,
    today: dt.date | None = None,
    status: dict | None = None,
    catalog: dict | None = None,
    sources: dict | None = None,
    probe: bool = False,
    max_probe: int = MAX_PROBE,
    session=None,
    probe_cache: dict | None = None,
    refresh_probe: bool = False,
) -> dict:
    """跑一次盘点。全部输入可注入（测试离线跑，不碰网络）。"""
    from tools.data_hub import registry  # noqa: WPS433

    today = today or dt.date.today()
    catalog = catalog if catalog is not None else json.loads(
        Path(registry.CATALOG_FILE).read_text(encoding="utf-8"))
    data = sources if sources is not None else registry.load()
    tables = catalog.get("tables") or {}
    local = _local_state(tables, data, today)

    remote: dict[str, dict] = {}
    if status is not None:
        for item in status.get("tables") or []:
            remote[str(item.get("table"))] = item

    probed = 0
    hits = 0
    cache = dict(probe_cache or {})
    errors: list[dict] = []
    rows: list[dict] = []
    for name in sorted(local):
        st = local[name]
        # 只有 xtcloud 有"远端水位"接口；其余源不走 present/latest 判定，
        # 否则 tushare 的 67 张表会因为"xtcloud status 里没有"被判成下架。
        is_xt = st["source"] == "xtcloud" and name not in NOT_XTCLOUD
        rmeta = remote.get(name) if is_xt else None
        present = ((status is None) or (name in remote)) if is_xt else True
        # 抽样探针只打"远端 latest 为空 + 本地有数据 + 需要判定"的表
        # 判据用**远端** rows，不用 catalog rows：catalog 的 rows 字段大量缺失，
        # 首版用它当条件，19 张远端有数、本地落后的表因此一次也没被抽样。
        need_probe = (
            probe and rmeta is not None and probed < max_probe
            and rmeta.get("latest") in (None, "")
            and int(rmeta.get("rows") or 0) > 0
            and (st["lag"] is None or st["lag"] > st["sla"]
                 or st["lifecycle"] != "active")
        )
        # latest 也可能是周期值（如 ts_broker_recommend 的 "202611"），
        # 只按 8 位日期解析会把它当"无水位"。
        r_raw = (rmeta or {}).get("latest")
        r_water = _norm_date(r_raw) or _norm_period(r_raw)
        r_col = st["date_col"]
        sampled = False
        if need_probe:
            r_rows = int(rmeta.get("rows") or 0)
            hit = cache.get(name)
            if (hit and not refresh_probe
                    and int(hit.get("rows") or -1) == r_rows):
                r_water = _norm_date(hit.get("water"))
                r_col = hit.get("col") or st["date_col"]
                sampled = r_water is not None
                hits += 1
            else:
                try:
                    r_water, r_col, _seen = probe_watermark(
                        session or _session(), name, r_rows, st["date_col"])
                    probed += 1
                    sampled = r_water is not None
                    cache[name] = {
                        "rows": r_rows,
                        "col": r_col,
                        "water": r_water.isoformat() if r_water else None,
                        "probed_at": dt.datetime.now().astimezone().isoformat(
                            timespec="seconds"),
                    }
                except Exception as exc:  # noqa: BLE001 - 单表失败不能拖垮盘点
                    errors.append({"table": name, "error": str(exc)[:200]})
        if is_xt:
            verdict, reason = classify(
                local=st["local"], remote=r_water, present=present,
                freq=st["freq"], sla_days=st["sla"], lag_days=st["lag"],
                lifecycle=st["lifecycle"], static_no_date=st["static"],
                date_col=r_col or st["date_col"], rows=st["rows"],
                sampled=sampled, today=today,
            )
        else:
            verdict, reason = classify_local_only(
                local=st["local"], lag_days=st["lag"], sla_days=st["sla"],
                lifecycle=st["lifecycle"], static_no_date=st["static"],
                rows=st["rows"], today=today,
            )
        rows.append({
            "table": name,
            "source": st["source"],
            "freq": st["freq"],
            "date_col": st["date_col"],
            "lifecycle": st["lifecycle"],
            "local": st["local"].isoformat() if st["local"] else None,
            "lag_days": st["lag"],
            "sla_days": st["sla"],
            "remote_latest": r_water.isoformat() if r_water else None,
            "remote_raw": (rmeta or {}).get("latest"),
            "remote_rows": (rmeta or {}).get("rows"),
            "remote_all_dt": (rmeta or {}).get("all_dt"),
            "sampled": sampled,
            "present": present,
            "verdict": verdict,
            "reason": reason,
            "replacement": REPLACEMENTS.get(name),
        })

    # 台账失真：lifecycle 说 planned，但表里已有可用数据
    lifecycle_conflict = [
        r["table"] for r in rows
        if r["lifecycle"] == "planned"
        and r["verdict"] in {"OK", "BEHIND", "CALIBER", "FUTURE"}
    ]
    return {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "probed": probed,
        "probe_hits": hits,
        "probe_cache": cache,
        "errors": errors,
        "rows": rows,
        "lifecycle_conflict": lifecycle_conflict,
        "summary": _summarize(rows, data),
    }


def _summarize(rows: list[dict], data: dict) -> dict:
    by_verdict: dict[str, int] = {}
    for r in rows:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    per_source: dict[str, dict] = {}
    for r in rows:
        s = per_source.setdefault(r["source"], {})
        s[r["verdict"]] = s.get(r["verdict"], 0) + 1
    src_meta = {s.get("id"): s for s in data.get("sources") or []}
    for sid, counts in per_source.items():
        meta = src_meta.get(sid) or {}
        counts["_name"] = meta.get("name") or sid
        counts["_status"] = meta.get("status")
        counts["_total"] = sum(v for k, v in counts.items() if not k.startswith("_"))
        counts["_conclusion"] = _source_conclusion(counts)
    return {"by_verdict": by_verdict, "per_source": per_source,
            "tables": len(rows), "sources": len(per_source)}


def _source_conclusion(counts: dict) -> str:
    """源级结论：以「还能不能继续用」为唯一标准，不评价质量。"""
    gone = counts.get("GONE", 0)
    stalled = counts.get("STALLED", 0)
    weak = counts.get("STALLED_WEAK", 0)
    behind = counts.get("BEHIND", 0)
    ok = counts.get("OK", 0)
    if gone + stalled:
        return "部分失效：%d 张需换源/下线" % (gone + stalled)
    if behind:
        return "可用，%d 张待补采" % behind
    if weak:
        return "可用（%d 张疑似停更待确认）" % weak
    no_remote = counts.get("NO_REMOTE", 0)
    if no_remote:
        return "可用性未知：%d 张无远端水位" % no_remote
    if ok:
        return "可用"
    return "无可用证据（待接或无法判）"


def _md(rep: dict) -> str:
    s = rep["summary"]
    bv = s["by_verdict"]
    rows = rep["rows"]
    lines = [
        "# 上游健康度盘点 · %s" % rep["generated_at"],
        "",
        "目录 %d 张表 / %d 个源 · 抽样探针 %d 张（失败 %d）。判定口径见文末。"
        % (s["tables"], s["sources"], rep["probed"], len(rep["errors"])),
        "",
        "| 判定 | 含义 | 张数 |",
        "|---|---|---|",
    ]
    for key in ("OK", "BEHIND", "STALLED", "STALLED_WEAK", "GONE", "CALIBER",
                "FUTURE", "NO_LINK", "NO_REMOTE", "STATIC", "UNDECIDABLE",
                "LOCAL_NEWER"):
        if bv.get(key):
            lines.append("| %s | %s | %d |" % (key, VERDICT_CN.get(key, key), bv[key]))

    lines += ["", "## 源级台账", "",
              "| 源 | 名称 | 表数 | 活跃 | 领先 | 停更(确证) | 疑似停更 | 下架 | 结论 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for sid, c in sorted(s["per_source"].items(), key=lambda kv: -kv[1]["_total"]):
        lines.append("| %s | %s | %d | %d | %d | %d | %d | %d | %s |" % (
            sid, c.get("_name", ""), c["_total"], c.get("OK", 0), c.get("BEHIND", 0),
            c.get("STALLED", 0), c.get("STALLED_WEAK", 0), c.get("GONE", 0),
            c["_conclusion"]))
    notes = [(sid, SOURCE_NOTES[sid]) for sid in s["per_source"] if sid in SOURCE_NOTES]
    if notes:
        lines += ["", "**源级旁证**（无逐表水位接口的源，靠这些证据判可用性）：", ""]
        for sid, note in notes:
            lines.append("- **%s**：%s" % (sid, note))

    def block(title, verdicts, note=""):
        picked = [r for r in rows if r["verdict"] in verdicts]
        picked.sort(key=lambda r: -(r["lag_days"] or 0))
        out = ["", "## %s" % title, ""]
        if note:
            out += [note, ""]
        if not picked:
            return out + ["无。"]
        out += ["| 表 | 源 | 频率 | 本地 | 远端 | 滞后 | 判定依据 | 换源建议 |",
                "|---|---|---|---|---|---|---|---|"]
        for r in picked:
            out.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["table"], r["source"], r["freq"] or "-", r["local"] or "-",
                r["remote_latest"] or "-",
                ("%dd" % r["lag_days"]) if r["lag_days"] is not None else "-",
                r["reason"], r["replacement"] or "-"))
        return out

    lines += block(
        "上游停更（确认：两端同水位）", {"STALLED"},
        "远端 latest 与本地同日止步且本地超 SLA。**补采无意义**——上游没有新数据。")
    lines += block(
        "疑似停更（抽样未见更新）", {"STALLED_WEAK"},
        "远端 latest 为空，只能按 6 点抽样判。抽样证不出「上游没有更新」，"
        "这一组要么找上游要水位接口，要么按业务重要性逐张人工确认。")
    lines += block("上游下架（远端已无此表）", {"GONE"},
                   "远端 status 的 181 张表里查不到。本地数据可留作只读快照。")
    lines += block("上游领先·本地未采（补采决策）", {"BEHIND"},
                   "远端有新数据。要区分「按设计不日更」和「链路漏跑」。")
    lines += block("口径不可比 / 未来水位（不改数，改口径）", {"CALIBER", "FUTURE"},
                   "本地取公告日、远端 latest 取报告期，或水位本身落在未来；"
                   "相减得到的「本地超前/滞后」都是假异常。")
    lines += block("未接采集（planned 且本地空）", {"NO_LINK"},
                   "上游有表、本地无数据。属「该接没接」，不是上游问题。")
    lines += block("该源无远端水位接口", {"NO_REMOTE"},
                   "tushare 等源没有逐表水位接口，只能看本地是否在 SLA 内。"
                   "**这类表「滞后」不必然是上游停更**，需按源另判。")
    lines += block("无法判定", {"UNDECIDABLE", "LOCAL_NEWER"},
                   "至少一端取不到可比水位。这一组的数量本身就是目录质量的指标。")
    lines += block("静态参照表（豁免）", {"STATIC"},
                   "date_col=list_date，新上市才推进；判「停更」语义错误。")

    conflict = rep.get("lifecycle_conflict") or []
    if conflict:
        lines += ["", "## 台账失真：lifecycle 标 planned 但已有数据", "",
                  "以下 %d 张表在 lifecycle 里标 planned，实际已有最新数据并可判定。"
                  "台账不改，治理报告会继续把它们当「未接」：" % len(conflict), "",
                  ", ".join(conflict), ""]

    lines += ["", "## 口径与局限", "",
              "1. **`all_dt` 不是内容水位**：它是表被远端重写的时间。ts_slb_sec 的 "
              "all_dt=2026-11-04，内容停在 2024-09-30。只作「远端管线还在跑」的旁证。",
              "2. **`rows` 不是行数**：ts_ths_member 声称 15,593,536，二分实测边界 "
              "382,731。远端行数只用于抽样点位计算。",
              "3. **远端不按时间序存储**：tdx_sector_daily 的 offset=rows-300 取回 2005 年，"
              "ts_stk_rewards 取回 1993 年。所以抽样只给下界，不能反证「没有更新」，"
              "对应 STALLED_WEAK。",
              "4. **领先容差**：本地批次每日 17:35 跑、远端 17:00 刷新，白天看 D 频表"
              "永远「远端领先 1 天」，那是时点差；容差按频率取 1/3/7/35/95/366 天。",
              "5. **非 xtcloud 源无逐表水位**：tushare 靠夜批 exit=0 与长尾兜底批作证；"
              "雪球 403、crypto 依赖本地代理，属源级阻断而非表级停更。",
              "6. 本次为**只读盘点**，不触发任何补采，不改 catalog。", ""]

    if rep["errors"]:
        lines += ["## 抽样失败", "", "| 表 | 错误 |", "|---|---|"]
        for e in rep["errors"]:
            lines.append("| %s | %s |" % (e["table"], e["error"]))
        lines.append("")

    lines += ["## 复跑", "",
              "```bash", "cd quant_backtest",
              "python upstream_health.py --live          # 重取状态 + 抽样探针",
              "python upstream_health.py                 # 复用缓存，只做本地对账",
              "```", ""]
    return "\n".join(lines)


def write(rep: dict, out_dir: Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir or OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    js = out / ("upstream_health_%s.json" % stamp)
    md = out / ("upstream_health_%s.md" % stamp)
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(_md(rep), encoding="utf-8")
    return js, md


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="上游健康度盘点（只读）")
    ap.add_argument("--live", action="store_true", help="重取 /api/data/status")
    ap.add_argument("--no-probe", action="store_true", help="只取状态，不做抽样探针")
    ap.add_argument("--cache", default=str(STATUS_CACHE), help="状态缓存路径")
    ap.add_argument("--probe-cache", default=str(PROBE_CACHE), help="抽样结果缓存路径")
    ap.add_argument("--refresh-probe", action="store_true", help="忽略抽样缓存重抽")
    ap.add_argument("--max-probe", type=int, default=MAX_PROBE)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--no-write", action="store_true", help="只打印摘要，不落盘")
    args = ap.parse_args(argv)

    status = None
    if args.live:
        status = fetch_status()
        Path(args.cache).parent.mkdir(parents=True, exist_ok=True)
        Path(args.cache).write_text(json.dumps(status, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    elif os.path.exists(args.cache):
        status = load_status_cache(Path(args.cache))

    cache = load_probe_cache(Path(args.probe_cache))
    rep = audit(status=status, probe=args.live and not args.no_probe,
                max_probe=args.max_probe, probe_cache=cache,
                refresh_probe=args.refresh_probe)
    if args.live and not args.no_probe:
        save_probe_cache(rep["probe_cache"], Path(args.probe_cache))
    bv = rep["summary"]["by_verdict"]
    print("表 %d · 源 %d · 抽样 %d（命中缓存 %d）· 失败 %d" % (
        rep["summary"]["tables"], rep["summary"]["sources"], rep["probed"],
        rep.get("probe_hits", 0), len(rep["errors"])))
    print("判定：" + " ".join("%s=%d" % (k, v) for k, v in sorted(bv.items())))
    for sid, c in sorted(rep["summary"]["per_source"].items(), key=lambda kv: -kv[1]["_total"]):
        print("  %-14s %3d 张  %s" % (sid, c["_total"], c["_conclusion"]))
    if not args.no_write:
        js, md = write(rep, Path(args.out_dir))
        print("明细：", js)
        print("报告：", md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
