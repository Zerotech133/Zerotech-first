# -*- coding: utf-8 -*-
"""Semantica 事件图谱（第7步）：股票-事件 二部图 + 多空倾向 + 语义检索。

学习来源：数据强"Semantica 实战系列"——实体-事件图谱 + 检索增强。
结合本项目数据源：
  · news.db 已判定行（jev 列 bias/conf/scope/reason，含 announcement 源）
    → jev 偏性直接采用；无个股的市场级事件也入库（供语义检索，不挂股边）
  · datamart.read('notice')（anns_d，带 ts_code/notice_date/标题，约 600 条/日）
    → 声明式规则表 RULES 判偏性（瞬时、确定性强）；规则未命中 → 未判
    （已判定公告由 news.db seed 路径自动吸收，jev_feed 每日增量会判）
  · report_events.jsonl（研报 AI 语义抽取，report_ai.py 维护）
    → kind=report 行（bias/conf 为本地 Ollama 判断），随 build 幂等并入

图模型（自管，借 semantica 数据模型）：
  节点：股票(code) / 事件(event)；边：股票-[涉及]->事件，事件-[倾向]->bull/bear
  存储：data/semantica/events.jsonl（事件行，原子写）+ events_vec.npy
  （对齐行序的 512 维向量矩阵）+ event_state.json（水位，原子写）

水位增量（参考 jev_feed/jev_feed.py）：
  notice 按日期水位回退 1 天重叠拉取；news.db 经情报总线 since_id 水位
  模式续读（rowid 游标，进程内 bus_query，读层 v1 补链 2026-10-19）；
  事件 id 幂等去重（AN 公告号跨源去重：jev 判定行优先于规则行）。

用法：
  python event_graph.py build [--notice-days 30]
  python event_graph.py stock 688411.SH [days]     # 个股事件流+多空统计
  python event_graph.py related "储能 中标 订单" [k]  # 语义检索相关事件
  python event_graph.py summary 688411.SH [days]   # 飞书摘要文本
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import datamart as dm  # noqa: E402
import logging
# semantica_mvp 惰性导入（embed/topk 仅在建图/语义检索时需要）——
# 这样轻量调用方（门户 events_for_stock、飞书 summary_for_stock）
# 导入本模块不会触发 fastembed ONNX 加载。

SNAP = os.path.join(HERE, "data", "semantica")
EVENT_F = os.path.join(SNAP, "events.jsonl")
VEC_F = os.path.join(SNAP, "events_vec.npy")
STATE_F = os.path.join(SNAP, "event_state.json")

KEEP_DAYS = 180  # 事件滚动保留窗口（超出丢弃，含向量）
NOTICE_DAYS = 30  # 首跑公告回看天数
TEXT_MAX = 200  # 嵌入文本截断长度

# ── 声明式规则表：公告标题 → 偏性（顺序敏感，先命中先赢；先空后多）──
# 铁律：宁可 None（未判）不可错判；中性类（质押/理财/常规披露）不设规则。
RULES = [
    # ── bear（先查强利空复合词，防"终止收购"误中"收购"）──
    # 2026-10-08 修：删「向下修正」（转债转股价下修误中，真业绩下修有专词）、
    # 删「问询函」（审核问询回复系正常流程，非处罚；真处罚有立案/处罚/警示函）、
    # 删「摘牌」（债券兑付摘牌误中；真退市有"退市/终止上市"）——铁律：宁 None 不错判
    ("业绩预亏", "bear", ["预亏", "预减", "业绩下修"]),
    ("终止重组", "bear", ["终止重大资产", "终止收购", "终止筹划", "终止发行股份"]),
    (
        "监管处罚",
        "bear",
        [
            "立案",
            "处罚",
            "警示函",
            "监管函",
            "纪律处分",
            "违规",
            "行政监管措施",
            "公开谴责",
        ],
    ),
    ("诉讼冻结", "bear", ["诉讼", "仲裁", "冻结", "查封"]),
    ("退市风险", "bear", ["退市", "终止上市", "终止挂牌", "破产清算"]),
    ("股东减持", "bear", ["减持"]),
    # ── bull ──
    ("业绩预增", "bull", ["预增", "预盈", "扭亏"]),
    ("中标合同", "bull", ["中标", "重大合同", "框架协议", "框架合作"]),
    ("回购增持", "bull", ["回购", "增持"]),
    ("分红派息", "bull", ["分红", "派息", "权益分派", "利润分配"]),
    ("股权激励", "bull", ["股权激励", "员工持股"]),
    ("政府补助", "bull", ["补助", "补贴"]),
    ("资产收购", "bull", ["收购", "并购"]),
    ("合作协议", "bull", ["战略合作", "许可协议", "签订合作"]),
]


def _d8(v):
    """任意日期形态 → 'YYYYMMDD'（无效返回 None）。"""
    s = str(v or "").strip().replace("-", "")[:8]
    return s if len(s) == 8 and s.isdigit() else None


def _an_id(url, title):
    """从公告 url 提取 AN 公告号（跨源去重键）。"""
    m = re.search(r"AN\d+", str(url or ""))
    if m:
        return m.group(0)
    if str(title or "").startswith("ann:"):
        return None
    m = re.search(r"ann:(AN\d+)", str(title or ""))
    return m.group(1) if m else None


def _rule_judge(title):
    """公告标题规则判偏性 → (bias, tag) 或 (None, None)。"""
    for tag, bias, kws in RULES:
        if any(k in title for k in kws):
            return bias, tag
    return None, None


def _norm_ts(v):
    """news.db ts / 公告日期 → 'YYYY-MM-DD HH:MM'（尽力归一）。"""
    s = str(v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:16]
    d = _d8(s)
    if d:
        t = s[8:].strip()[:5]
        return "%s-%s-%s %s" % (d[:4], d[4:6], d[6:8], t if ":" in t else "00:00")
    # '09-28 00:00' 缺年形态：补当前年
    m = re.match(r"^(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s %s" % (
            datetime.now().year,
            m.group(1),
            m.group(2),
            s[6:11] if len(s) >= 11 else "00:00",
        )
    return None


def _code6(v):
    """6 位裸代码 → 尽力补交易所后缀（6→SH，0/3→SZ，4/8→BJ）。"""
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) != 6:
        return None
    if s[0] == "6":
        return s + ".SH"
    if s[0] in "03":
        return s + ".SZ"
    if s[0] in "48":
        return s + ".BJ"
    return None


# ── 持久化（原子写） ─────────────────────────────────────────
def _replace_retry(tmp, path, retries=3, wait=5):
    """os.replace 遇 WinError 5(文件锁) 短退避重试。

    2026-11-10 判例：evt_build 656s 嵌入重算完成后，落盘 os.replace 被
    杀软/索引器/并发读者短暂锁挡，PermissionError 直接 ABORT 整链。
    短退避可让持锁方释放；重试耗尽仍抛原异常（行为不变差）。
    回滚：恢复 C:\\项目\\backups\\event_graph.py.bak-20261112-P123
    """
    import time as _t

    for i in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == retries - 1:
                raise
            _t.sleep(wait)


def _load_events():
    if not os.path.exists(EVENT_F):
        return []
    out = []
    with open(EVENT_F, encoding="utf-8") as f:
        for ln in f:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


def _atomic_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    _replace_retry(tmp, path)


def _atomic_npy(path, mat):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        np.save(f, mat)
    _replace_retry(tmp, path)


def _load_state():
    if os.path.exists(STATE_F):
        try:
            return json.load(open(STATE_F, encoding="utf-8"))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/event_graph.py", exc)
    return {}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_F), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE_F), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_F)


def _event_text(ev):
    """嵌入/展示文本：ts + 标题（截断）。"""
    return ("%s %s" % (ev.get("ts") or "", ev.get("text") or "")).strip()[:TEXT_MAX]


# ── seed 数据源 ──────────────────────────────────────────────
def _seed_newsdb(wm_rowid):
    """news.db 已判定行 → 事件行。返回 (new_events, new_wm_rowid)。

    读点收拢至情报总线（2026-10-19，读层 v1 未路由缺口补链）：
    bus_query(domain=jev, since_id=水位) 进程内调用——不走 HTTP 零端口
    依赖，SQL 级「jev IS NOT NULL AND jev!='' AND rowid>? ORDER BY rowid」
    与原直读 1:1；bus 的 max_rowid 游标含坏 jev 行（原直读「坏行也推
    水位」判例语义），分页 limit=100 循环至 count<limit 终点。
    bus 读失败 → RuntimeError fail-visible（原 sqlite 异常同型，水位不推）。

    含 announcement 源（id=ann:AN...，从标题 [code name] 提取个股）；
    无 stocks 的市场级事件也入库（kind=news，不挂股边，供语义检索）。
    """
    root = os.path.abspath(os.path.join(HERE, ".."))
    os.environ.setdefault("AIOS_ROOT", root)  # 与 NEWS_DIR 同根，部署态可覆盖
    if root not in sys.path:
        sys.path.insert(0, os.path.join(root, "portal"))
    import api_intel_bus as bus  # 惰性：轻调用方不背 portal 依赖

    out, wm = [], int(wm_rowid)
    while True:
        env, status = bus.bus_query(
            {"domain": "jev", "since_id": str(wm), "limit": "100"}
        )
        if status != 200 or not env.get("ok"):
            raise RuntimeError("intel bus jev 水位读失败: %r" % (env.get("errors"),))
        for it in env["items"]:
            j = it.get("jev")
            if not isinstance(j, dict):  # 坏行（bus 已计 bad_rows），
                continue  # 水位由 max_rowid 兜住不滞留
            title = (it.get("title") or "").strip()
            if not title:
                continue
            ts = _norm_ts(it.get("ts"))
            if not ts:
                continue
            # bus stocks=原始解析 list（str 化），此处沿用原 ts_code 过滤
            stocks = [
                c
                for c in (it.get("stocks") or [])
                if isinstance(c, str) and re.match(r"^\d{6}\.(SH|SZ|BJ)$", c)
            ]
            if not stocks:
                # announcement 源：标题形如 '[301716 鸿富诚] 鸿富诚:...'
                m = re.match(r"\[(\d{6})\s", title)
                c6 = _code6(m.group(1)) if m else None
                if c6:
                    stocks = [c6]
            out.append(
                dict(
                    id=str(it["id"]),
                    kind="news",
                    ts=ts,
                    text=title[:TEXT_MAX],
                    source=str(it.get("source") or ""),
                    stocks=stocks,
                    bias=j.get("bias"),
                    conf=j.get("conf"),
                    reason=j.get("reason") or "",
                    tag="jev",
                )
            )
        errs = env.get("errors") or {}
        if errs.get("max_rowid") is not None:
            wm = max(wm, int(errs["max_rowid"]))
        if (env.get("count") or 0) < 100:  # 末页（count=本页扫过行数）
            break
    return out, wm


def _seed_notice(wm_date, notice_days):
    """datamart notice（anns_d）→ 规则判偏事件行。

    返回 (new_events, new_wm_date)。偏性口径：规则命中 → bull/bear(conf=0.7)，
    未命中 → None（展示为未判）——不在此处跑远程 JEV（逐条 2~10s 太慢），
    已判定公告由 news.db seed 路径自动吸收（jev_feed 每日增量会判）。
    """
    start = None
    if wm_date:
        start = (datetime.strptime(wm_date, "%Y%m%d") - timedelta(days=1)).strftime(
            "%Y%m%d"
        )
    else:
        start = (datetime.now() - timedelta(days=notice_days)).strftime("%Y%m%d")
    df = dm.read("notice", start=start, limit=100000)
    if df is None or df.empty:
        return [], wm_date
    recs = df.to_dict("records")
    rows = []
    max_date = wm_date
    for r in recs:
        code = str(r.get("ts_code") or "").strip()
        nd = _d8(r.get("notice_date"))
        title = str(r.get("title") or "").strip()
        if not code or not nd or not title:
            continue
        if max_date is None or nd > max_date:
            max_date = nd
        an = _an_id(r.get("url"), title)
        eid = "ev:nt:%s:%s" % (
            an or hashlib.md5((nd + title).encode("utf-8"), usedforsecurity=False).hexdigest()[:12],
            code,
        )
        bias, tag = _rule_judge(title)
        rows.append(
            dict(
                id=eid,
                kind="notice",
                ts="%s-%s-%s 00:00" % (nd[:4], nd[4:6], nd[6:8]),
                text=title[:TEXT_MAX],
                source="anns_d",
                stocks=[code],
                bias=bias,
                conf=0.7 if bias else None,
                reason=("规则命中:" + tag) if tag else "",
                tag=tag,
            )
        )
    return rows, max_date


def _seed_reports():
    """研报 AI 抽取事件（report_events.jsonl，report_ai.py 维护全量重写）。

    行格式与本库同构（id=rpt:sha1 稳定幂等 / kind=report /
    bias/conf 来自本地 Ollama 语义抽取）。无水位——每次全读，
    seen 集合（events.jsonl 现有 id）天然去重。
    """
    f = os.path.join(SNAP, "report_events.jsonl")
    if not os.path.exists(f):
        return []
    out = []
    with open(f, encoding="utf-8") as fo:
        for ln in fo:
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("id") and e.get("bias"):
                out.append(e)
    return out


# ── build（水位增量 + 向量矩阵重建） ─────────────────────────
def build(notice_days=NOTICE_DAYS, dryrun=False, rebuild=False):
    from semantica_mvp import embed  # 惰性：建图才加载 fastembed

    st = _load_state()
    seen = {e["id"] for e in _load_events()}
    if rebuild:
        # 全量重建：忽略 seen 过滤（孤儿事件救治通道——事件已入 jsonl
        # 但向量缺失时，正常 build 会误判「无新增」直接推水位，向量永缺）
        seen = set()
        print("=== event_graph REBUILD 全量重建 ===", flush=True)
    print(
        "=== event_graph build 水位=%s 现有=%d ==="
        % ({k: v for k, v in st.items() if k != "updated_at"}, len(_load_events())),
        flush=True,
    )

    # 1) news.db 已判定行（jev 偏性优先）
    new1, wm_rowid = _seed_newsdb(int(st.get("wm_news_rowid") or 0))
    kept1 = []
    for e in new1:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        kept1.append(e)
    print("  news.db: %d 新事件（水位 rowid→%s）" % (len(kept1), wm_rowid))

    # 2) datamart notice 规则判偏（AN 号与 jev 行跨源去重）
    if dryrun:
        print("  DRY notice skip")
        kept2, wm_notice = [], st.get("wm_notice")
    else:
        new2, wm_notice = _seed_notice(st.get("wm_notice"), notice_days)
        an_seen = {
            e["id"].split(":")[2] if e["id"].startswith("ev:nt:") else e["id"]
            for e in kept1
        }
        # jev 行的 AN 号（ann:AN... 形态）
        for e in kept1:
            m = re.match(r"ann:(AN\d+)", e["id"])
            if m:
                an_seen.add(m.group(1))
        kept2 = []
        for e in new2:
            an = e["id"].split(":")[2] if e["id"].startswith("ev:nt:") else None
            if an and an in an_seen:
                continue  # jev 判定行已收录同一公告
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            kept2.append(e)
        print("  notice: %d 新事件（水位→%s）" % (len(kept2), wm_notice))

    # 3) 研报 AI 事件（report_ai.py 语义抽取，id 幂等）
    kept3 = []
    for e in _seed_reports():
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        kept3.append(e)
    print("  reports: %d 新事件（AI 语义抽取）" % len(kept3))

    if dryrun:
        print("DRYRUN 结束，未写盘。")
        return

    events = kept1 + kept2 + kept3
    if not events:
        print("无新增事件。")
        st.update(wm_news_rowid=wm_rowid, updated_at=_now())
        if wm_notice:
            st["wm_notice"] = wm_notice
        _save_state(st)
        return

    # 3) 向量矩阵重建（批量嵌入，进度打印）
    #    一致性纪律（2026-10-17 判例）：必须先嵌入成功、再写 events.jsonl——
    #    若先写事件后嵌失败，下轮会因 id 幂等判「无新增」直接推进水位，
    #    导致这批事件进了图但向量永久缺失（检索不可见）。
    all_rows = _load_events() + events
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    all_rows = [e for e in all_rows if (e.get("ts") or "")[:10] >= cutoff]
    texts = [_event_text(e) for e in all_rows]
    print("  嵌入 %d 条事件…" % len(texts), flush=True)
    t0 = time.time()
    mats = []
    B = 1024
    for i in range(0, len(texts), B):
        mats.append(embed(texts[i : i + B]))
        print("    embed %d/%d" % (min(i + B, len(texts)), len(texts)), flush=True)
    mat = np.vstack(mats) if len(mats) > 1 else mats[0]
    _atomic_npy(VEC_F, mat)
    _atomic_jsonl(EVENT_F, all_rows)

    # 4) 水位落盘
    st.update(wm_news_rowid=wm_rowid, updated_at=_now())
    if wm_notice:
        st["wm_notice"] = wm_notice
    _save_state(st)
    print(
        "BUILD 完成: 共 %d 事件 向量%s %.1fs"
        % (len(all_rows), mat.shape, time.time() - t0)
    )
    _print_bias_overview(all_rows)


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _print_bias_overview(rows):
    c = Counter((e.get("bias") or "unknown") for e in rows)
    ck = Counter(e.get("tag") or "-" for e in rows)
    print("  偏性分布: %s" % dict(c))
    print("  判定来源: %s" % dict(ck))


# ── 查询层 ───────────────────────────────────────────────────
_VEC = None  # (events, mat) 进程缓存


def _vec_cache():
    global _VEC
    if _VEC is None:
        evs = _load_events()
        mat = None
        if os.path.exists(VEC_F) and evs:
            mat = np.load(VEC_F)
            if len(mat) != len(evs):
                mat = None  # 行序错位（半途写坏）→ 放弃缓存走重算
        _VEC = (evs, mat)
    return _VEC


def events_for_stock(code, days=30, half_life=14):
    """某股近期事件流 + 多空统计（纯 jsonl 扫描，不加载向量，门户可安全调）。

    net 为时衰共振净分：Σ sign(bias)×conf×0.5^(age天/half_life)——
    新闻/公告/研报 AI 事件同池，越新的事件（含研报高 conf 看多）权重越高，
    默认半衰期 14 天；ts 缺失/坏格式按窗口边缘降权。

    Returns: {code, n, bias_stat:{bull,bear,neutral,unknown,net,n},
              events:[{ts,text,bias,conf,tag,reason,source}]} 按时间倒序
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now().date()
    evs = [
        e
        for e in _load_events()
        if code in (e.get("stocks") or []) and (e.get("ts") or "")[:10] >= cutoff
    ]
    evs.sort(key=lambda e: e.get("ts") or "", reverse=True)
    bull = [e for e in evs if e.get("bias") == "bull"]
    bear = [e for e in evs if e.get("bias") == "bear"]

    def _decay(e):
        ts = (e.get("ts") or "")[:10]
        try:
            d = (
                datetime.strptime(ts, "%Y-%m-%d")
                if "-" in ts
                else datetime.strptime(ts, "%Y%m%d")
            ).date()
            age = max((today - d).days, 0)
        except Exception:
            age = days
        return 0.5 ** (age / half_life)

    def _net(evs_):
        return sum(
            (e.get("conf") or 0.5) * (1 if e.get("bias") == "bull" else -1) * _decay(e)
            for e in evs_
        )

    stat = {
        "bull": len(bull),
        "bear": len(bear),
        "neutral": sum(1 for e in evs if e.get("bias") == "neutral"),
        "unknown": sum(1 for e in evs if not e.get("bias")),
        "net": round(_net(bull) + _net(bear), 2),
        "n": len(evs),
    }
    return {"code": code, "n": len(evs), "bias_stat": stat, "events": evs}


def related_events(query, k=10, kind=None):
    """语义检索相关事件（全库向量余弦 top-k；kind 先过滤后检索）。"""
    from semantica_mvp import embed, topk  # 惰性：仅语义检索加载 fastembed

    evs, mat = _vec_cache()
    if not evs or mat is None:
        return []
    idx = [i for i, e in enumerate(evs) if kind is None or e.get("kind") == kind]
    if not idx:
        return []
    qv = embed([query[:TEXT_MAX]])[0]
    hits = topk(qv, mat[idx], k)
    out = []
    for j, s in hits:
        e = evs[idx[int(j)]]
        out.append(
            {
                "score": round(s, 3),
                "ts": e.get("ts"),
                "text": e.get("text"),
                "bias": e.get("bias"),
                "conf": e.get("conf"),
                "stocks": e.get("stocks"),
                "kind": e.get("kind"),
            }
        )
    return out


def recent_strong(n=8, days=7):
    """近 N 天全市场强事件（bull/bear 各取前 n/2，按 conf 降序）。

    供门户预警池页展示市场事件面，纯 jsonl 扫描不加载向量。
    Returns: [{ts,text,bias,conf,tag,stocks,reason}]
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    evs = [
        e
        for e in _load_events()
        if e.get("bias") in ("bull", "bear") and (e.get("ts") or "")[:10] >= cutoff
    ]
    bull = sorted(
        [e for e in evs if e["bias"] == "bull"],
        key=lambda e: e.get("conf") or 0,
        reverse=True,
    )[: n // 2]
    bear = sorted(
        [e for e in evs if e["bias"] == "bear"],
        key=lambda e: e.get("conf") or 0,
        reverse=True,
    )[: n - n // 2]
    out = bull + bear
    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return out[:n]


def summary_for_stock(code, days=7, topn=4):
    """个股事件摘要（飞书推送/门户下钻用文本）。"""
    r = events_for_stock(code, days)
    if r["n"] == 0:
        return "🗞 事件图谱 %s: 近 %d 天无事件" % (code, days)
    s = r["bias_stat"]
    head = "🗞 事件图谱 %s: 近%d天 %d条 多空%s净%+.2f(时衰加权)" % (
        code,
        days,
        s["n"],
        "牛%d/熊%d" % (s["bull"], s["bear"]),
        s["net"],
    )
    lines = []
    for e in r["events"][:topn]:
        # K-STOCK-359 A股口径红涨绿跌（对齐 premarket 判例）
        mark = {"bull": "🔴", "bear": "🟢"}.get(e.get("bias"), "⚪")
        lines.append(
            "  %s %s %s" % (mark, (e.get("ts") or "")[:10], (e.get("text") or "")[:40])
        )
    return head + "\n" + "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--notice-days", type=int, default=NOTICE_DAYS)
    b.add_argument("--dryrun", action="store_true")
    b.add_argument(
        "--rebuild",
        action="store_true",
        help="全量重建：忽略 id 幂等，重嵌全部事件（孤儿救治）",
    )
    s = sub.add_parser("stock")
    s.add_argument("code")
    s.add_argument("days", nargs="?", type=int, default=30)
    s.add_argument("--topn", type=int, default=10)
    r = sub.add_parser("related")
    r.add_argument("query")
    r.add_argument("k", nargs="?", type=int, default=10)
    sm = sub.add_parser("summary")
    sm.add_argument("code")
    sm.add_argument("days", nargs="?", type=int, default=7)
    args = ap.parse_args()

    if args.cmd == "build" or args.cmd is None:
        build(
            getattr(args, "notice_days", NOTICE_DAYS),
            getattr(args, "dryrun", False),
            getattr(args, "rebuild", False),
        )
    elif args.cmd == "stock":
        r_ = events_for_stock(args.code, args.days)
        s = r_["bias_stat"]
        print("=== %s 近 %d 天事件 %d 条 ===" % (args.code, args.days, r_["n"]))
        print(
            "  bull=%d bear=%d neutral=%d unknown=%d net=%+.2f"
            % (s["bull"], s["bear"], s["neutral"], s["unknown"], s["net"])
        )
        for e in r_["events"][: args.topn]:
            # K-STOCK-359 A股口径红涨绿跌（对齐 premarket 判例）
            mark = {"bull": "🔴", "bear": "🟢"}.get(e.get("bias"), "⚪")
            print(
                "  %s [%s] %s %s (conf=%s %s)"
                % (
                    mark,
                    (e.get("ts") or "")[:10],
                    (e.get("text") or "")[:46],
                    e.get("tag"),
                    e.get("conf"),
                    (e.get("reason") or "")[:24],
                )
            )
    elif args.cmd == "related":
        for h in related_events(args.query, args.k):
            print(
                "  [%.3f] %s %s bias=%s %s"
                % (
                    h["score"],
                    h["ts"],
                    (h["text"] or "")[:44],
                    h["bias"],
                    ",".join(h["stocks"] or [])[:20],
                )
            )
    elif args.cmd == "summary":
        print(summary_for_stock(args.code, args.days))


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "evt_build", main)
