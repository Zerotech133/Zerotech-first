# -*- coding: utf-8 -*-
"""数据中台 news/notice 别名读路径 → JEV 新闻分流接线。

从 quant_backtest 读层 datamart.read('news'/'notice') 按水位增量拉取
（news→tdx stock_news；notice→stock_notice 真源命中、anns_d 降级），
映射为 news.db news 表新行（id=xd:<kind>:md5(url) 幂等，source=xd_news/
xd_notice，stocks=[ts_code]），逐条过 JEV 本地模型判定写回 jev 列——
接入与分流一步完成。下游 pushed/digested/摘要管线无需改动。

水位存 runs 表（kind=jev_feed，detail 记各源日期水位），每轮回退 1 天
重叠拉取 + INSERT OR IGNORE 去重（与 tushare_sync 增量同口径）。
单轮截断（拉满 cap）时水位不动，下轮重叠窗口重拉。

失败可见（2026-10-26 JEV 审计 §四-⑤ 修复：此前 runs.ok 恒 1、失败计成功）：
源失败（dm.read 抛异常/返回 None）或判定阶段整批失败 → runs.ok=0、
detail.errors 带 {source, stage, error} 明细、打印 FEED FAILED、进程 rc=1；
失败源水位不推进（下次从上次成功水位重拉，防丢数被"成功"掩盖）。
真实空数据（空 DataFrame）仍记成功（重拉同窗口幂等，不算失败）。

用法：cd news_collector && python jev_feed.py [--days 2] [--cap 500]
      [--dryrun]
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-11-07：HERE 必须插在 quant_backtest 之前——两目录存在同名
# quant_backtest/jev_backfill.py，qb 在前会遮蔽 news_collector 的
# jev_backfill（缺 SCOPE_ALERT_ENV 等，测试间按导入顺序踩雷）。
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))
sys.path.insert(0, HERE)

import datamart as dm  # noqa: E402
import news_ai  # noqa: E402

DB = os.path.join(HERE, "news.db")
FEED_KIND = "jev_feed"

# ── 2026-11-07 P0 RFC 方案②（D1 批）：连续水位 + 稳定游标 ──────────────
# 开关 JEV_WM_V2=1 才启用 v2 拉取路径；缺省关闭＝v1 行为逐字不变。
# 切换前置（RFC §6）：公告过滤真实轮次生效验证（21:00 首轮 + 次日 09:00 快照
# notice_skip>0）通过后，才在 start_jev_feed.ps1 落 env 开关。
# 回滚（RFC §7）：撤 env 即回 v1；v1 对 v2 dict 水位降级读 .get("date")。
WM_V2_ENV = "JEV_WM_V2"
WM_V2_MAX_PAGES = 40  # 单源单轮分页安全阀（cap=500 → 单轮至多 2 万行/源）

# 2026-10-26 OS-FIX-JEVFEEDNIGHT：夜批独占窗口守卫。
# 背景：21:00 JevFeed 与 21:50 JevNight 同时本地 judge，抢同一 Ollama
# （NUM_PARALLEL=1）→ 夜批速率从 1,300/h 掉到 612/h（10-26 实锤）。
# 规则：21:45–07:05 或 M3 暂停旗/夜锁存在时，jev_feed 只拉数不判读；
# 待判行留给 JevNight（按 jev IS NULL 取全量）处理，不丢数据。
# 应急开关：JEV_FEED_FORCE_JUDGE=1 强制判读。
NIGHT_GUARD_START_MIN = 21 * 60 + 45  # 21:45
NIGHT_GUARD_END_MIN = 7 * 60 + 5  # 07:05（不含）

# ── 2026-11-13 A 案（老田裁定）：白天让位，jev_feed 不在门禁/盘中窗口判读 ──
# 背景：单并发 ollama（NUM_PARALLEL=1）下，jev_feed 自 07:05 起持续判读会把队列占满，
# 门禁 jev_gate 的判定请求排不上 → 11-13 07:21–07:23 连续三次 60s 超时、regime 降级放行。
# 修法：盘前门禁窗口 + 交易时段（默认工作日 07:05–15:05）jev_feed 只拉数不判读，
# 未判行留 jev IS NULL 由 JevNight 夜间统一判（与夜窗守卫同一语义：数据不丢）。
# 开关：JEV_FEED_DAY_GUARD=0 关闭；JEV_FEED_DAY_GUARD_WINDOW="0705-1505" 改窗口；
# 应急：JEV_FEED_FORCE_JUDGE=1 强制判读（与夜窗守卫共用同一应急开关）。
DAY_GUARD_ENV = "JEV_FEED_DAY_GUARD"
DAY_GUARD_WINDOW_ENV = "JEV_FEED_DAY_GUARD_WINDOW"
DAY_GUARD_DEFAULT_WINDOW = "0705-1505"
NIGHT_PAUSE_FLAG = r"C:\Temp\k266_m3.pause"
NIGHT_LOCK = os.environ.get(
    "JEV_NIGHT_LOCK_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "locks",
        "jev_night.lock",
    ),
)


def _in_night_window(now=None):
    """是否处于 JevNight 独占窗口（时间窗 + pause/lock 强证据）。"""
    if os.environ.get("JEV_FEED_FORCE_JUDGE") == "1":
        return False
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    if minutes >= NIGHT_GUARD_START_MIN or minutes < NIGHT_GUARD_END_MIN:
        return True
    return os.path.exists(NIGHT_PAUSE_FLAG) or os.path.exists(NIGHT_LOCK)


def _day_guard_window():
    """解析 JEV_FEED_DAY_GUARD_WINDOW（HHMM-HHMM），非法回默认窗口。"""
    raw = (os.environ.get(DAY_GUARD_WINDOW_ENV) or DAY_GUARD_DEFAULT_WINDOW).strip()
    m = re.match(r"^(\d{3,4})-(\d{3,4})$", raw)
    if not m:
        raw = DAY_GUARD_DEFAULT_WINDOW
        m = re.match(r"^(\d{3,4})-(\d{3,4})$", raw)
    a, b = int(m.group(1)), int(m.group(2))
    return (a // 100) * 60 + a % 100, (b // 100) * 60 + b % 100


def _in_day_guard(now=None):
    """是否处于白天让位窗口（默认工作日 07:05–15:05）。

    True = 本轮只拉数不判读，未判行留 jev IS NULL 给 JevNight（与夜窗守卫同语义）。
    默认开启；JEV_FEED_DAY_GUARD=0 关闭；JEV_FEED_FORCE_JUDGE=1 强制判读。
    """
    if os.environ.get("JEV_FEED_FORCE_JUDGE") == "1":
        return False
    if os.environ.get(DAY_GUARD_ENV) == "0":
        return False
    now = now or datetime.now()
    if now.weekday() >= 5:  # 周末门禁/盘中链路不跑，不设让位
        return False
    start, end = _day_guard_window()
    minutes = now.hour * 60 + now.minute
    return start <= minutes < end


def _xd_id(kind, url, title):
    """幂等主键：xd:<kind>:md5(url|title) 前 12 位（对齐 sina:5115033 风格）。"""
    h = hashlib.md5((url or title or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return "xd:%s:%s" % (kind, h)


def _norm_ts(pub_date, pub_time=""):
    """YYYYMMDD(+HH:MM:SS) → 'YYYY-MM-DD HH:MM'（对齐 news.ts 口径）。"""
    d = str(pub_date or "").replace("-", "")[:8]
    if len(d) != 8 or not d.isdigit():
        return None
    t = str(pub_time or "")[:5]
    return "%s-%s-%s %s" % (d[:4], d[4:6], d[6:8], t if len(t) == 5 else "00:00")


def load_watermark():
    """读 runs 表最近一次成功 feed 的各源水位 {kind: YYYYMMDD}。"""
    db = sqlite3.connect(DB)
    try:
        row = db.execute(
            "SELECT detail FROM runs WHERE kind=? AND ok=1 ORDER BY id DESC LIMIT 1",
            (FEED_KIND,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        return {}
    try:
        return json.loads(row[0]).get("wm", {})
    except Exception as e:
        news_ai.log(
            "jev_feed: runs.detail 解析失败，feed 降级为空: %s" % str(e)[:100]
        )  # C2批2
        return {}


def _pick(row, *names):
    """dict 取首个非空字段。"""
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return v
    return ""


# ── 2026-11-07 S2 / V4 根因修复：公告结构化前缀兜底 ────────────────────
# 背景：xd_notice 上游常缺 ts_code/symbol，历史 6,016 条公告里 5,978 条
# 标题自带 [600095 湘财股份] 形式的结构化前缀，此前未解析 → stocks=[] →
# 次日 07:10 夜跑判为 no_codes，hit 恒 NULL。正则与 tools/jev_r3_probe.py
# 的 PREFIX_RE 对齐；6 位裸码按 tools/jev_audit.py::norm_ts_code 口径归一
# 成带后缀 TS 码，保证下游夜跑可消费。只影响新入库行，不写历史、不依赖
# 外部证券主数据文件（名称/行情有效性仍由下游判定）。
ANN_PREFIX_RE = re.compile(r"\[\s*(\d{6})\s+([^\]]+?)\s*\]")


def _ann_prefix_ts(title) -> str:
    """从公告标题结构化前缀提取代码 → 带后缀 TS 码；无/非法则返回空串。"""
    m = ANN_PREFIX_RE.search(str(title or ""))
    if not m:
        return ""
    code = m.group(1)
    if not re.fullmatch(r"\d{6}", code):
        return ""
    if code[0] == "6":
        return code + ".SH"
    if code[0] in ("0", "3"):
        return code + ".SZ"
    if code[0] in ("8", "4") or code.startswith("920"):
        return code + ".BJ"
    return ""


def _row_stocks(row, title, content="", source_kind=""):
    """新行 stocks：上游 ts_code/symbol 优先 → 公告前缀兜底 → 名称抽取兜底（P5.4）。

    返回 (codes, tag_conf)：
      tag_conf=None   上游结构化码 / 公告结构化前缀 / 无命中（历史行为，最高可信）
      tag_conf='high' 标题点名（P5.4 名称抽取，强相关）
      tag_conf='low'  仅正文提及（P5.4）——下游只作「有信号」判定，不得作 Δmin 锚点
                      （防污染 P-01/P-05/P-06/P-09 的分钟级锚点，见 docs/jev_gate_a2_spec.md）
    名称抽取失败时静默退化为旧行为（[]），不阻断采集主流程。
    """
    code = str(_pick(row, "ts_code") or _pick(row, "symbol") or "")
    if code:
        return [code], None
    code = _ann_prefix_ts(title)
    if code:
        return [code], None
    try:
        import _stock_tagger
        return _stock_tagger.tag_stocks(title, content, source_kind)
    except Exception as _e:
        sys.stderr.write("[jev_feed] stock tagger degraded: %r\n" % (_e,))
        return [], None


def feed_source(alias, kind, wm_date, days, cap, dryrun=False):
    """拉一个别名 → 映射插入 news.db → 返回 (插入数, 拉取数, max_date, error)。

    「真实空数据」与「源失败」严格区分（2026-10-26 JEV 审计 §四-⑤：
    runs.ok 恒 1、失败被计成功）：
    - `datamart.read` 返回空 DataFrame = 真实无新数据 → error=None（成功）；
    - 抛异常 / 返回 None = 源不可用/未就绪 → error=明细（fail-visible，
      调用方据此把 runs.ok 记 0、水位不推进，避免丢数被"成功"掩盖）。
    """
    if wm_date:
        start = (datetime.strptime(wm_date, "%Y%m%d") - timedelta(days=1)).strftime(
            "%Y%m%d"
        )
    else:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    if dryrun:
        print("  DRY %s <- %s start=%s cap=%d" % (kind, alias, start, cap))
        return 0, 0, None, None
    try:
        df = dm.read(alias, start=start, limit=cap)
    except Exception as e:  # noqa: BLE001
        return 0, 0, None, "dm.read(%r) 异常: %s" % (alias, str(e)[:200])
    if df is None:
        return 0, 0, None, "dm.read(%r) 返回 None（源不可用/未就绪）" % alias
    if df.empty:
        return 0, 0, None, None
    if len(df) >= cap:
        print(
            "  WARN %s 拉满 cap=%d（截断；水位推进策略见主循环 ADV 行）" % (kind, cap)
        )
    db = sqlite3.connect(DB)
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        max_date, n_ins = None, 0
        for row in df.to_dict("records"):
            title = str(_pick(row, "title"))
            if not title:
                continue
            nid = _xd_id(kind, _pick(row, "url"), title)
            ts = _norm_ts(
                _pick(row, "pub_date", "notice_date", "ann_date"),
                _pick(row, "pub_time"),
            )
            if ts is None:
                continue
            # max_date 统计全部可解析行（含已存在重复）：修复活锁——
            # 若仅统计已插入行，截断轮 inserted=0 时 max_date=None，
            # 水位永不前进，同一窗口无限重拉（2026-10-16 实证两轮
            # fetched=500/inserted=0 卡死，日增量无法进窗）。
            d = ts[:10].replace("-", "")
            if max_date is None or d > max_date:
                max_date = d
            cur = db.execute("SELECT 1 FROM news WHERE id=?", (nid,)).fetchone()
            if cur:
                continue
            stocks, tag_conf = _row_stocks(row, title, str(_pick(row, "content")), kind)
            db.execute(
                "INSERT INTO news(id, source, ts, title, content, hit, "
                "pushed, digested, created_at, stocks, jev, tag_conf) "
                "VALUES (?,?,?,?,?,0,0,0,?,?,NULL,?)",
                (
                    nid,
                    "xd_%s" % kind,
                    ts,
                    title,
                    str(_pick(row, "content")),
                    now,
                    json.dumps(stocks, ensure_ascii=False),
                    tag_conf,
                ),
            )
            n_ins += 1
        db.commit()
    finally:
        db.close()
    return n_ins, len(df), max_date, None


# ── 2026-11-07 P0 RFC 方案②（R2/R3）：游标分页拉取（JEV_WM_V2=1 门控）────
def _wm_norm(raw):
    """水位归一：v1 str 'YYYYMMDD' → {'v':1,...}（零游标）；v2 dict 原样。
    非法输入返回 None（按首跑处理）。"""
    if isinstance(raw, dict) and raw.get("date"):
        return {"v": 2, "date": str(raw.get("date"))[:8], "cur": raw.get("cur")}
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        return {"v": 1, "date": raw, "cur": None}
    return None


def _zero_cursor(start):
    """零游标 = start 前一日：首页即走游标分支（rowid 三键防并列丢行）。"""
    d = (datetime.strptime(start, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    return {"date": d, "sub": "", "rid": 0}


def feed_source_v2(alias, kind, wm_raw, days, cap, dryrun=False):
    """方案②：连续水位 + 稳定游标分页，消除 --cap 截断丢数。

    - 水位 v2 格式：{"v":2,"date":"YYYYMMDD","cur":{"date","sub","rid"}|None}
      （runs.detail.wm；v1 str 由 _wm_norm 兼容读，零游标从当日整日重读，
      幂等去重顺势补齐 v1 截断损失的本日行）；
    - 分页：dm.read(after=游标) 拉满 cap 续拉，直到不满页（源耗尽）或
      WM_V2_MAX_PAGES 安全阀（游标随水位保存、下轮续走，不丢数）；
    - 耗尽：date=本轮最大日期、cur=None（下轮重叠该日重读，幂等去重）；
    - 源失败：error 非空，调用方水位不推进（同 v1 fail-visible 语义）。
    返回 (插入数, 拉取数, 新水位dict|None, error)；None=无新数据不写水位。
    """
    wm = _wm_norm(wm_raw)
    if wm and wm.get("date"):
        start = wm["date"]  # v2 不回退 1 天：游标已保证不重不漏
    else:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    # v2 路径永不传 after=None（那会回落 v1 的 DESC LIMIT 截断语义）：
    # 游标缺失（首跑/耗尽重叠日）一律落零游标 = start 前一日升序整走。
    cursor = (wm.get("cur") if wm and wm.get("date") else None) or _zero_cursor(start)
    if dryrun:
        print(
            "  DRY %s <- %s start=%s cap=%d cur=%s (wm_v2)"
            % (kind, alias, start, cap, "有" if cursor else "无")
        )
        return 0, 0, None, None

    db = sqlite3.connect(DB)
    n_ins = n_fetched = 0
    max_date = None
    exhausted = False
    err = None
    try:
        for _page in range(WM_V2_MAX_PAGES):
            try:
                df = dm.read(alias, start=start, limit=cap, after=cursor)
            except Exception as e:  # noqa: BLE001
                err = "dm.read(%r, after) 异常: %s" % (alias, str(e)[:200])
                break
            if df is None:
                err = "dm.read(%r, after) 返回 None（源不可用/未就绪）" % alias
                break
            if df.empty:
                exhausted = True
                break
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            dcol = next(
                (c for c in ("pub_date", "notice_date", "ann_date") if c in df.columns),
                None,
            )
            for row in df.to_dict("records"):
                title = str(_pick(row, "title"))
                if not title:
                    continue
                nid = _xd_id(kind, _pick(row, "url"), title)
                ts = _norm_ts(
                    _pick(row, "pub_date", "notice_date", "ann_date"),
                    _pick(row, "pub_time"),
                )
                if ts is None:
                    continue
                d = ts[:10].replace("-", "")
                if max_date is None or d > max_date:
                    max_date = d
                if db.execute("SELECT 1 FROM news WHERE id=?", (nid,)).fetchone():
                    continue
                stocks, tag_conf = _row_stocks(row, title, str(_pick(row, "content")), kind)
                db.execute(
                    "INSERT INTO news(id, source, ts, title, content, hit, "
                    "pushed, digested, created_at, stocks, jev, tag_conf) "
                    "VALUES (?,?,?,?,?,0,0,0,?,?,NULL,?)",
                    (
                        nid,
                        "xd_%s" % kind,
                        ts,
                        title,
                        str(_pick(row, "content")),
                        now,
                        json.dumps(stocks, ensure_ascii=False),
                        tag_conf,
                    ),
                )
                n_ins += 1
            db.commit()
            n_fetched += len(df)
            last = df.iloc[-1]
            cursor = {
                "date": str(last[dcol])[:10].replace("-", "") if dcol else (max_date or start),
                "sub": str(last.get("_fetched_at") or ""),
                "rid": int(last["_src_rowid"]) if "_src_rowid" in df.columns else 0,
            }
            if len(df) < cap:
                exhausted = True
                break
        else:
            # for 未 break = 打满安全阀：受控续走（游标已保存），非丢数
            print(
                "  WARN %s 打满 WM_V2_MAX_PAGES=%d（受控分页续走，下轮从游标继续）"
                % (kind, WM_V2_MAX_PAGES)
            )
    finally:
        db.close()

    if err:
        return 0, n_fetched, None, err
    if max_date is None:
        return 0, 0, None, None  # 无可解析新行：不写水位（同 v1 语义）
    new_wm = {
        "v": 2,
        "date": max_date,
        "cur": None if exhausted else cursor,
    }
    return n_ins, n_fetched, new_wm, None


# ── 2026-11-07 RFC-20261107 §11-A-FIX：夜窗守卫内补纯规则粗筛 ──────────
# 根因：judge_pending() 命中 _in_night_window() 时直接 return，21:45–07:05
# （约 16/24 轮）完全不经过 prefilter → 方案 A 公告白名单在生产
# notice_skip=0、new_code_effective_in_prod=False，一周影子观测拿不到样本。
# 修法：夜窗仍严格不调 Ollama（保住 OS-FIX-JEVFEEDNIGHT 的独占语义），
# 只做零算力规则粗筛，命中写 src=prefilter 的 neutral；判读仍归 JevNight。
# 开关关闭或任何异常都静默回退，绝不新增告警噪声、绝不改变夜批行为。
_PREFILTER_ON_VALUES = ("1", "safe", "on", "true", "yes")


def _night_prefilter(kind, cap):
    """夜窗专属第一段粗筛：只跑规则、只写 neutral，不碰 Ollama。

    UPDATE 带 AND (jev IS NULL OR jev='')，保证不覆盖 JevNight 已判结果；
    src=prefilter / pref.reason 证据可按 RFC §7 SQL 精确回退。
    """
    if str(os.environ.get("JEV_PREFILTER", "0")).strip().lower() not in _PREFILTER_ON_VALUES:
        return 0
    source = "xd_%s" % kind
    db = None
    n = 0
    try:
        import jev_prefilter

        db = sqlite3.connect(DB, timeout=5.0)
        rows = db.execute(
            "SELECT id, title FROM news "
            "WHERE source=? AND (jev IS NULL OR jev='') "
            "ORDER BY ts DESC LIMIT ?",
            (source, cap),
        ).fetchall()
        skipped = []
        for nid, title in rows:
            j = jev_prefilter.prefilter_jev(title or "", source, mode="safe")
            if j is not None:
                skipped.append((nid, j))
        for nid, j in skipped:
            cur = db.execute(
                "UPDATE news SET jev=? WHERE id=? AND (jev IS NULL OR jev='')",
                (json.dumps(j, ensure_ascii=False), nid),
            )
            n += int(cur.rowcount or 0)
        if n:
            db.commit()
    except Exception as e:  # noqa: BLE001 —— 纯增益逻辑，失败一律静默回退给夜批
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        print("  NIGHT-PREFILTER FAIL %r（留待 JevNight，不告警）" % (e,), file=sys.stderr)
        n = 0
    finally:
        if db is not None:
            db.close()
    if n:
        print("  NIGHT-PREFILTER %s skip=%d（不调 Ollama）" % (kind, n))
    return n


def judge_pending(kind, cap, dryrun=False):
    """对本轮 xd_ 来源未判行过 JEV 模型（每批 8 条，内部 0.8s 防限流）。

    返回 {"judged": 实际判成条数, "missing": 判定失败条数, "error": 阶段错误或 None}。
    夜批独占窗口（21:45–07:05 / 暂停旗 / 夜锁）内跳过判读，返回
    {"judged":0,...,"skipped_night":True}——数据留待 JevNight 判，不丢。
    2026-11-07：夜窗仍不调 Ollama，但先跑零算力规则粗筛（见 _night_prefilter）。
    """
    res = {"judged": 0, "missing": 0, "error": None, "pref_skipped": 0}
    if not dryrun and _in_night_window():
        res["skipped_night"] = True
        print(
            "  SKIP judge %s: JevNight 独占窗口（21:45–07:05 / 暂停旗）；"
            "留待夜批判读" % kind
        )
        res["pref_skipped"] += _night_prefilter(kind, cap)
        return res
    if not dryrun and _in_day_guard():
        # 2026-11-13 A 案：白天让位。只拉数不判读，不跑 _night_prefilter——
        # 规则粗筛写的是 neutral，白天不该用它顶替夜间 LLM 判读（降质）。
        res["skipped_day"] = True
        print(
            "  SKIP judge %s: 白天让位窗口（%s，ollama 让给门禁/盘中链路）；"
            "留待 JevNight 判读"
            % (kind, os.environ.get(DAY_GUARD_WINDOW_ENV) or DAY_GUARD_DEFAULT_WINDOW)
        )
        return res
    db = sqlite3.connect(DB)
    try:
        # ── 2026-11-13 老田裁定 B 案：公告类不进 LLM ────────────────────
        # 必须在 SELECT 之前拦：否则下面的 LIMIT cap 照样会把 500 条公告
        # 排进批次、照样跑 9B。采集到就地标口径豁免，jev 写 "na" 不写 neutral。
        _src_ex = "xd_%s" % kind
        if not dryrun:
            try:
                import jev_prefilter as _jp_exempt

                if _src_ex in _jp_exempt.exempt_sources():
                    _n_ex = _jp_exempt.apply_notice_exempt(db, (_src_ex,))
                    if _n_ex:
                        res["exempted"] = _n_ex
                        print(
                            "  EXEMPT judge %s: %d 条按口径豁免，不进 LLM"
                            % (kind, _n_ex)
                        )
                    return res
            except Exception as e:  # noqa: BLE001
                print("  EXEMPT judge %s FAIL: %s" % (kind, str(e)[:120]))
        rows = db.execute(
            "SELECT id, title, stocks FROM news "
            "WHERE source=? AND (jev IS NULL OR jev='') "
            "ORDER BY ts DESC LIMIT ?",
            ("xd_%s" % kind, cap),
        ).fetchall()
        if not rows:
            return res
        if dryrun:
            print("  DRY judge %s: %d pending" % (kind, len(rows)))
            return res
        for i in range(0, len(rows), 8):
            items = []
            for nid, title, stocks in rows[i : i + 8]:
                try:
                    st = json.loads(stocks) if stocks else []
                except Exception:
                    st = []
                items.append({
                    "id": nid, "title": title, "stocks": st,
                    "source": "xd_%s" % kind,
                })
            # 2026-11-07 RFC-20261107 方案 A：三入口统一第一段规则粗筛。
            # 与 jev_backfill.py 的 prefilter 段同口径（JEV_PREFILTER=safe 才启用），
            # skip 直接写带 src=prefilter / pref.reason 证据的 neutral，可 SQL 回退。
            _mode = str(os.environ.get("JEV_PREFILTER", "0")).strip().lower()
            if _mode in _PREFILTER_ON_VALUES:
                import jev_prefilter

                kept, skipped = [], []
                for it in items:
                    j = jev_prefilter.prefilter_jev(
                        it.get("title") or "", it.get("source") or "", mode="safe"
                    )
                    if j is None:
                        kept.append(it)
                    else:
                        skipped.append((it["id"], j))
                for nid, j in skipped:
                    db.execute(
                        "UPDATE news SET jev=? WHERE id=? AND (jev IS NULL OR jev='')",
                        (json.dumps(j, ensure_ascii=False), nid),
                    )
                if skipped:
                    db.commit()
                    res["pref_skipped"] += len(skipped)
                    print("  PREFILTER %s skip=%d keep=%d" % (kind, len(skipped), len(kept)))
                items = kept
                if not items:
                    continue
            try:
                got = news_ai.jev_judge_batch(items) or {}
            except Exception as e:  # noqa: BLE001
                res["missing"] += len(items)
                res["error"] = res["error"] or (
                    "jev_judge_batch(%s) 异常: %s" % (kind, str(e)[:200])
                )
                continue
            for nid, j in got.items():
                db.execute(
                    "UPDATE news SET jev=? WHERE id=?",
                    (json.dumps(j, ensure_ascii=False), nid),
                )
            db.commit()
            res["judged"] += len(got)
            res["missing"] += len(items) - len(got)
            if items and not got and res["error"] is None:
                res["error"] = "jev_judge_batch(%s) 全部失败（0/%d 条判成）" % (
                    kind,
                    len(items),
                )
            time.sleep(0.5)
        return res
    finally:
        db.close()


CAP_HIT_ALERT_ENV = "JEV_CAP_HIT_ALERT"  # 缺省开；=0 关告警（排障用）


def _cap_hit_handle(summary, kind, n_ins, n_fetched, cap, dryrun=False):
    """P0 RFC 方案①（修订版）R5 止血：截断轮显式化，只做信号、不动水位策略。

    字面「截断轮不推水位」会回归 2026-10-16 活锁修复（见主循环 ADV 注释与
    feed_source 内注释），故水位策略修正归方案②（连续水位+游标）。
    信号三路：runs.detail.cap_hit（观测）、stdout/stderr（留痕）、
    飞书 push_message（失败静默，不影响退出码）。feed 每日一轮，
    告警上限天然 ≤1 条/源/日，无需冷却文件。
    """
    if dryrun or n_fetched < cap:
        return None
    summary["src"][kind]["cap_hit"] = True
    msg = (
        "【JEV 采集截断告警】\n"
        "源=%s fetched=%d cap=%d 插入=%d\n"
        "上游真实量 600-3,400 条/日，截断可能永久跳过中间日期"
        "（P0 丢数，RFC 20261107_jev_cap_data_loss_p0）。\n"
        "处置：批 D1 落方案②（连续水位+游标）；临时缓解需评估判读压力后调大 --cap。"
        % (kind, n_fetched, cap, n_ins)
    )
    print(msg)
    print(
        "FEED CAP_HIT %s fetched=%d cap=%d inserted=%d"
        % (kind, n_fetched, cap, n_ins),
        file=sys.stderr,
    )
    try:
        if str(os.environ.get(CAP_HIT_ALERT_ENV, "1")).strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        ):
            news_ai.push_message(msg)
    except Exception:  # noqa: BLE001 —— 告警失败静默，绝不影响采集退出码
        pass
    return msg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="无水位（首跑）回看天数")
    ap.add_argument("--cap", type=int, default=500, help="每源单轮拉取/判定上限")
    ap.add_argument("--dryrun", action="store_true")
    args = ap.parse_args(argv)

    wm = load_watermark()
    wm_v2 = (
        str(os.environ.get(WM_V2_ENV, "0")).strip().lower() in _PREFILTER_ON_VALUES
    )
    print(
        "=== jev_feed 水位=%s days=%d cap=%d dryrun=%s wm_v2=%s ==="
        % (wm or "无(首跑)", args.days, args.cap, args.dryrun, wm_v2)
    )
    summary = {"wm": {}, "src": {}, "errors": []}
    errors = summary["errors"]
    t0 = time.time()
    total_ins = 0
    page_limit_kinds = set()

    def _fail(kind, stage, msg):
        """阶段失败留痕：detail.errors + stdout/stderr 双写（err log 供告警）。"""
        errors.append({"source": kind, "stage": stage, "error": msg})
        line = "FEED FAILED %s stage=%s: %s" % (kind, stage, msg)
        print(line)
        print(line, file=sys.stderr)

    for alias, kind in [("news", "news"), ("notice", "notice")]:
        max_date = None  # 展示口径（v2 用 new_wm["date"]，见下）
        if wm_v2:
            n_ins, n_fetched, new_wm, err = feed_source_v2(
                alias, kind, wm.get(kind), args.days, args.cap, dryrun=args.dryrun
            )
        else:
            # v1 路径（缺省）：对 v2 dict 水位降级读 .get("date")（RFC §7 回滚口径）
            wm_date = wm.get(kind)
            if isinstance(wm_date, dict):
                wm_date = wm_date.get("date")
            n_ins, n_fetched, max_date, err = feed_source(
                alias, kind, wm_date, args.days, args.cap, dryrun=args.dryrun
            )
        if wm_v2:
            # v2 水位策略：耗尽推进（cur=None）；安全阀续走（cur=游标）；
            # 源失败/无新行不写水位（同 v1 fail-visible）。游标分页下
            # 「拉满 cap」是正常分页，不再触发 v1 的 cap_hit 截断告警。
            if err:
                _fail(kind, "feed", err)
            elif not args.dryrun and new_wm is not None:
                max_date = new_wm["date"]
                summary["wm"][kind] = new_wm
                if new_wm.get("cur") is not None:
                    page_limit_kinds.add(kind)
                    print(
                        "  ADV %s 安全阀续走：游标=%s（下轮继续，不丢数）"
                        % (kind, json.dumps(new_wm["cur"], ensure_ascii=False))
                    )
                else:
                    print(
                        "  ADV %s v2 水位推进→%s（源耗尽，插入=%d）"
                        % (kind, new_wm["date"], n_ins)
                    )
        else:
            # 水位推进策略（2026-10-16 活锁修复 + 2026-10-26 源失败不推进）：
            #   源失败 → 不写 wm（下次从上次成功水位重拉，避免丢数被"成功"掩盖）；
            #   未截断 → 推进到本轮 max_date（原行为）；
            #   截断且有新插入 → 推进（存量积压排水：INSERT OR IGNORE +
            #     1 天重叠兜底回看）；
            #   截断且零插入 → 也推进（活锁破除：整窗全为重复/坏日期行，
            #     不推进则永远卡死，实证 21:24/23:55 两轮 fetched=500
            #     inserted=0 无限重拉）。
            if err:
                _fail(kind, "feed", err)
            elif not args.dryrun and max_date:
                if n_fetched >= args.cap:
                    print(
                        "  ADV %s 截断轮推进水位→%s（插入=%d，%s）"
                        % (kind, max_date, n_ins, "排水" if n_ins else "活锁破除")
                    )
                summary["wm"][kind] = max_date
        summary["src"][kind] = {"inserted": n_ins, "fetched": n_fetched}
        if kind in page_limit_kinds:
            summary["src"][kind]["page_limit"] = True
        if not wm_v2:
            # P0 RFC 方案①（修订版）：截断轮显式化——仅 v1 路径；
            # v2 游标分页无截断语义，cap_hit 信号随之退役（安全阀走 page_limit）。
            _cap_hit_handle(summary, kind, n_ins, n_fetched, args.cap, dryrun=args.dryrun)
        total_ins += n_ins
        print(
            "  %s <- %s: inserted=%d fetched=%d max=%s"
            % (kind, alias, n_ins, n_fetched, max_date or "-")
        )
        jr = judge_pending(kind, args.cap, dryrun=args.dryrun)
        if jr.get("skipped_night"):
            summary["src"][kind]["judge_skipped_night"] = True
        if jr["judged"]:
            summary["src"][kind]["judged"] = jr["judged"]
            print("  %s judged=%d" % (kind, jr["judged"]))
        if jr.get("pref_skipped"):
            summary["src"][kind]["pref_skipped"] = jr["pref_skipped"]
        if jr["missing"]:
            # 单条判定失败不硬失败（避免个别坏条让整轮红），但留明细可追
            summary["src"][kind]["judge_missing"] = jr["missing"]
            print(
                "  WARN %s judge_missing=%d（下轮仍会被重判）" % (kind, jr["missing"])
            )
        if jr["error"]:
            _fail(kind, "judge", jr["error"])
    if args.dryrun:
        print("DRYRUN 结束，未写库。")
        return 0
    ok = not errors
    db = sqlite3.connect(DB)
    try:
        db.execute(
            "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
            "VALUES (?,?,?,?,0,?)",
            (
                FEED_KIND,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                1 if ok else 0,
                total_ins,
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        db.commit()
    finally:
        db.close()
    if ok:
        print(
            "FEED done inserted=%d wm=%s %.1fs"
            % (total_ins, summary.get("wm", {}), time.time() - t0)
        )
        return 0
    print(
        "FEED FAILED runs.ok=0 errors=%d inserted=%d wm=%s %.1fs"
        % (len(errors), total_ins, summary.get("wm", {}), time.time() - t0)
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
