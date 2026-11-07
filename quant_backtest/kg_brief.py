# -*- coding: utf-8 -*-
"""KG 每日情报简报（每日链第 10 步，G1-1：情报周期"分发"环闭环）。

六段式（纯模板聚合，无 LLM，定时可靠性优先）：
  ① 结构信号   d 日缠论信号 by type + 明细 top8（vv/bei 标注）
  ② 盘口异动   d 日盘口事件 by type + 焦点 top6（封板/炸板/尾盘）
  ③ 资金动向   d 日龙虎榜席位净买 top5（席位→标的数）
  ④ 传导链     d 日新 DRIVE 边 top5（p 降序）
  ⑤ 席位突变   最近 20 交易日共现突变（KG-V7 口径：新结盟/突增 top5）
  ⑥ 数据健康   graph_check 结果 + 六域覆盖 + 来源断流/catalog 滞后
               （G7 评级产物，与门户红点同源）+ 盲区声明
  ⑦ 舆情内参   F2-20261107：当日①②标的 × guba 词云（flag 默认关；
               独立来源计数=近7日不同 user 帖子数去重，N≥2 才命中）
               F2-P2-a：xq 内容层并列接入（flag F2_XQ_SECTION 默认关；
               读 xueqiu_info.db xq_status 带 /S/ 标的映射帖）

日期锚：默认 max(HAS_VOL_EVENT.validStart)（最活域，T+1 链后=昨日）。
幂等：data/kg_brief_state.json 记 last_date（--force 忽略水位）。
发送复用 news_collector/send_feishu.py（临时文件+子进程，SEND_OK 口径，
与 morning_report 同机制）。

用法：
  python kg_brief.py --dry      # 只打印不发送不写水位
  python kg_brief.py [--d 20260928] [--force]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kg_client import KgClient  # noqa: E402

SHADOW_PARQUET = os.path.join(HERE, "data", "semantica", "notice_llm.parquet")

STATE = os.path.join(HERE, "data", "kg_brief_state.json")
CHECK_LATEST = os.path.join(HERE, "data", "graph_check", "latest.json")
RATING_LATEST = os.path.join(HERE, "data", "kg_source_rating.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
SEND_PY = os.path.join(NEWS_DIR, "send_feishu.py")
DISCLAIMER = "个人研究·知识图谱情报简报·非投资建议"

CANDLE_CN = {
    "early_seal": "早盘封板",
    "seal": "封板",
    "late_seal": "尾盘封板",
    "broken": "炸板",
    "late_surge": "尾盘抢筹",
    "late_dump": "尾盘砸盘",
}
CANDLE_ORDER = ["early_seal", "seal", "late_seal", "broken", "late_surge", "late_dump"]
BLIND_NOTE = (
    "盲区: 盘口事件仅覆盖分钟库890标的(约15%非全市场); "
    "龙虎榜仅上榜日; 缠论信号确认滞后5-9交易日"
)


def _today8():
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")


def _load_state():
    try:
        with open(STATE, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(obj):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def _wan(v):
    if v is None:
        return "-"
    return "%.1f亿" % (v / 1e8) if abs(v) >= 1e8 else "%.0f万" % (v / 1e4)


def _short_seat(n):
    return n if len(n) <= 14 else n[:13] + "…"


def _surge_lines():
    """⑤ 席位突变（KG-V7 同口径）：复用 kg_portal.map_graph 边日序列，
    最近 20 个交易日——新结盟=首现落窗内且窗口共现≥5；
    突增=历史≥10日且≥2次，窗口日均≥历史日均 3 倍。"""
    try:
        import kg_portal

        mg = kg_portal.map_graph()
    except Exception as e:
        return ["⑤ 席位突变: 画布数据不可用(%s)" % str(e)[:60]]
    if not mg.get("ok"):
        return ["⑤ 席位突变: 画布数据不可用(%s)" % str(mg.get("error", "?"))[:60]]
    edges = mg.get("edges") or []
    dset = sorted({seg[0] for e in edges for seg in e["s"]})
    if not dset:
        return ["⑤ 席位突变: 近 %d 日无共现数据" % mg.get("days", 365)]
    W = 20
    dIdx = {dd: i for i, dd in enumerate(dset)}
    TI = len(dset) - 1
    lo = max(0, TI - W + 1)
    sigs = []
    for e in edges:
        winW = oldW = 0
        firstIdx = None
        for seg in e["s"]:
            di = dIdx.get(seg[0])
            if di is None or di > TI:
                continue
            if firstIdx is None:
                firstIdx = di
            if di >= lo:
                winW += seg[1]
            else:
                oldW += seg[1]
        if firstIdx is None or winW < 5:
            continue
        oldDays = max(0, lo - firstIdx)
        typ = ratio = None
        if firstIdx >= lo:
            typ, ratio = "新结盟", float(winW)
        elif oldDays >= 10 and oldW >= 2:
            r = (winW / W) / (oldW / oldDays)
            if r >= 3:
                typ, ratio = "突增", r
        if typ:
            sigs.append((winW, typ, e["a"], e["b"], ratio, e["s"][0][0]))
    head = "⑤ 席位突变(截至%s·近%d日): " % (dset[-1], W)
    if not sigs:
        return [head + "无突变信号"]
    sigs.sort(key=lambda x: -x[0])
    nNew = sum(1 for s in sigs if s[1] == "新结盟")
    out = [head + "%d 条（新结盟×%d · 突增×%d）" % (len(sigs), nNew, len(sigs) - nNew)]
    for winW, typ, a, b, ratio, first in sigs[:5]:
        tail = ("首现%s" % first) if typ == "新结盟" else "%.1fx" % ratio
        out.append(
            "  · %s ↔ %s %s %d次 %s" % (_short_seat(a), _short_seat(b), typ, winW, tail)
        )
    if len(sigs) > 5:
        out.append("  …等 %d 条（/kg/map ⚡ 突变 查看）" % (len(sigs) - 5))
    return out


F2_GUBA_SECTION = os.environ.get("F2_GUBA_SECTION", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


F2_XQ_SECTION = os.environ.get("F2_XQ_SECTION", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
F2_XQ_DB = os.path.join(HERE, "data", "xueqiu", "xueqiu_info.db")


def _f2_tset_add(lst, rows):
    """F2-20261107：①② 段标的去重保序暂存 helper。"""
    seen = {tc for tc, _ in lst}
    for r in rows:
        if r["tc"] not in seen:
            lst.append((r["tc"], r["nm"]))
            seen.add(r["tc"])


def _f2_guba_lines(targets, days=7, top_n=40, xq_counts=None):
    """⑦ 舆情内参（F2-20261107，feature flag 默认关）。

    对当日 ①② 段标的（上限 8 只）逐只调用 guba_sentiment.guba_summary
    真实读取 data/guba/{code6}.jsonl 内容（NOISE_PAT 过滤）；
    「独立来源」= 同一标的近 days 日内不同 user 帖子数（去重），
    热点词命中 ≥3 帖且独立来源 ≥2 才输出命中行。
    任何异常降级为单行提示，绝不抛出（日报可靠性优先）。
    """
    if not F2_GUBA_SECTION:
        return []
    if not targets:
        return ["⑦ 舆情内参(guba, 近7日): flag 开启但当日无①②标的"]
    out, hits = [], []
    try:
        import guba_sentiment
    except Exception as e:
        return ["⑦ 舆情内参: guba_sentiment 不可用(%s)" % str(e)[:50]]
    for tc, nm in targets[:8]:
        try:
            sm = guba_sentiment.guba_summary(tc, days=days, top_n=top_n)
        except Exception as e:
            continue  # 单只失败不阻塞整段
        if not sm.get("ok"):
            continue  # 无采集数据 / 解析失败 → 优雅降级：不输出该标的
        # 独立来源计数：近 days 日不同 user 去重（复用 _load_posts 过滤口径）
        try:
            posts = guba_sentiment._load_posts(tc, days)
            users = {str(r.get("user") or "") for r in posts}
            users.discard("")
        except Exception:
            users = set()
        n_src = len(users)
        if n_src < 2:
            continue
        for w in sm.get("words", [])[:3]:
            if w.get("n", 0) >= 3:
                _xq_n = (xq_counts or {}).get(tc)
                _tail = (
                    " / xq %d 家" % _xq_n if _xq_n else ""
                )
                out.append(
                    "  · %s %s: %d 家独立来源在说【%s】%s"
                    % (tc, nm, n_src, w.get("w"), _tail)
                )
                hits.append(tc)
                break  # 每标的只取第一个达标热点词
    head = "⑦ 舆情内参(guba, 近7日): 扫描%d只 · 命中%d只" % (
        min(len(targets), 8),
        len(set(hits)),
    )
    if out:
        return [head] + out
    return [head + "（无达标标的：独立来源≥2 且热点词≥3帖）"]


def _f2_xq_lines(targets, days=7):
    """⑦ 舆情内参 xq 侧（F2-P2-a，flag F2_XQ_SECTION 默认关）。

    读 xueqiu_info.db::xq_status 中 target 形如 /S/{EX}{code6}/ 的
    帖子（带标的映射）；「独立来源」= 近 days 日不同 user_id 去重；
    ≥2 源即命中，输出「xq N 家独立来源在说」。与 guba 侧并列，
    双源行形如「guba N1 家 / xq N2 家」。异常降级不抛出。
    """
    if not F2_XQ_SECTION:
        return {}
    out = {}
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % F2_XQ_DB.replace("\\", "/"), uri=True)
        cur = con.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        for tc, nm in targets[:8]:
            ex = tc[-2:]
            c6 = tc.split(".")[0]
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM xq_status "
                "WHERE target LIKE ? AND created_at >= ?",
                ("/S/%s%s/%%" % (ex, c6), cutoff),
            )
            n, u = cur.fetchone()
            if u and u >= 2:
                out[tc] = u
        con.close()
    except Exception:
        return out
    return out


def build(d):
    """六段聚合，返回 (text, meta)。"""
    cli = KgClient(retry=1)
    L = []
    f2_targets = []  # F2-20261107：①② 段标的暂存（去重保序，⑦ 段消费）
    with cli._session() as s:
        # ① 结构信号
        rows = s.run(
            "MATCH (sec:Security)-[:HAS_STRUCTURE]->(e:MarketEvent "
            "{kind:'chanlun'}) WHERE e.date=$d "
            "RETURN sec.ts_code AS tc, sec.name AS nm, e.type AS t, "
            "e.vv AS vv, e.bei AS bei, e.price AS px "
            "ORDER BY tc",
            d=d,
        ).data()
        if not rows:
            L.append("① 结构信号(%s): 无（含确认窗口内滞后，见⑤）" % d)
        else:
            _f2_tset_add(f2_targets, rows[:8])
            byt = {}
            for r in rows:
                byt[r["t"]] = byt.get(r["t"], 0) + 1
            L.append(
                "① 结构信号(%s): %d 个（%s）"
                % (d, len(rows), " · ".join("%s×%d" % kv for kv in sorted(byt.items())))
            )
            for r in rows[:8]:
                tags = ("趋✓" if r["vv"] else "") + ("背" if r["bei"] else "")
                L.append(
                    "  · %s %s %s%s px=%s" % (r["tc"], r["nm"], r["t"], tags, r["px"])
                )
            if len(rows) > 8:
                L.append("  …等 %d 个" % (len(rows) - 8))

        # ② 盘口异动
        rows = s.run(
            "MATCH (sec:Security)-[:HAS_CANDLE]->(e:MarketEvent "
            "{kind:'candle'}) WHERE e.date=$d "
            "RETURN sec.ts_code AS tc, sec.name AS nm, e.type AS t, "
            "e.mtime AS mt, e.pct AS pct, e.n_touch AS nt, "
            "e.tail_vol AS tv "
            "ORDER BY tc",
            d=d,
        ).data()
        if not rows:
            L.append("② 盘口异动(%s): 无（或分钟库滞后）" % d)
        else:
            byt = {}
            for r in rows:
                byt[r["t"]] = byt.get(r["t"], 0) + 1
            L.append(
                "② 盘口异动(%s): %d 起（%s）"
                % (
                    d,
                    len(rows),
                    " · ".join(
                        "%s×%d" % (CANDLE_CN.get(k, k), v)
                        for k, v in sorted(byt.items(), key=lambda x: -x[1])
                    ),
                )
            )
            pri = sorted(
                rows,
                key=lambda r: (
                    CANDLE_ORDER.index(r["t"]) if r["t"] in CANDLE_ORDER else 9,
                    -(r["nt"] or 0),
                ),
            )
            _f2_tset_add(f2_targets, pri[:6])
            for r in pri[:6]:
                extra = (
                    "触%d分" % r["nt"]
                    if r["nt"]
                    else ("尾量%d%%" % round(r["tv"] * 100) if r["tv"] else "")
                )
                L.append(
                    "  · %s %s %s %s %+.1f%% %s"
                    % (
                        r["tc"],
                        r["nm"],
                        CANDLE_CN.get(r["t"], r["t"]),
                        r["mt"] or "-",
                        r["pct"] if r["pct"] is not None else 0,
                        extra,
                    )
                )

        # ③ 资金动向（席位口径，当日净买）
        rows = s.run(
            "MATCH (st:Seat)-[t:TRADED_ON]->(s:Security) "
            "WHERE t.validStart=$d "
            "WITH st, sum(t.net_buy) AS nb, count(*) AS ns "
            "WHERE nb IS NOT NULL "
            "RETURN st.name AS nm, nb, ns ORDER BY nb DESC LIMIT 5",
            d=d,
        ).data()
        if not rows:
            L.append("③ 资金动向(%s): 龙虎榜当日无席位数据（披露滞后或未上榜）" % d)
        else:
            L.append("③ 资金动向(%s): 席位净买 top5" % d)
            for r in rows:
                L.append("  · %s %s / %d 标的" % (r["nm"], _wan(r["nb"]), r["ns"]))

        # ④ 传导链（Event.text -> MarketEvent.type(date)）
        rows = s.run(
            "MATCH (a:Event)-[r:DRIVE]->(b:MarketEvent) "
            "WHERE r.validStart=$d "
            "RETURN left(a.text, 18) AS an, "
            "b.type + '(' + b.date + ')' AS bn, r.p AS p, "
            "r.lag_days AS lg "
            "ORDER BY r.p DESC LIMIT 5",
            d=d,
        ).data()
        if not rows:
            L.append("④ 传导链(%s): 无新 DRIVE 边" % d)
        else:
            L.append(
                "④ 传导链(%s): 新增 %d 组（top5）"
                % (
                    d,
                    s.run(
                        "MATCH ()-[r:DRIVE]->() WHERE r.validStart=$d "
                        "RETURN count(r) AS n",
                        d=d,
                    ).data()[0]["n"],
                )
            )
            for r in rows:
                L.append(
                    "  · %s → %s (p=%.2f lag=%s)"
                    % (r["an"], r["bn"], r["p"] or 0, r["lg"])
                )
    cli.close()

    # ⑤ 席位突变（KG-V7 口径，画布数据自洽锚最新共现日）
    L.extend(_surge_lines())

    # ⑦ 舆情内参（F2-20261107：flag 默认关；关=输出与现状逐字节一致）
    _f2_targets_list = list(f2_targets)
    _xq_counts = _f2_xq_lines(_f2_targets_list)
    L.extend(_f2_guba_lines(_f2_targets_list, xq_counts=_xq_counts))

    # ⑥ 数据健康（读 graph_check 产物）
    try:
        with open(CHECK_LATEST, encoding="utf-8-sig") as f:
            chk = json.load(f)
        parts = []
        for t, c in chk.get("coverage", {}).items():
            parts.append("%s=%s" % (c.get("cn", t), (c.get("max") or "-")[-4:]))
        qa = (
            "OK"
            if chk.get("ok")
            else "WARN "
            + json.dumps(
                {k: v for k, v in chk.get("issues", {}).items() if not v.get("ok")},
                ensure_ascii=False,
            )
        )
        ts = (chk.get("ts") or "")[:16]
        L.append("⑥ 数据健康: QA %s（%s）" % (qa, ts))
        L.append("  覆盖: " + " | ".join(parts))
    except Exception as e:
        L.append("⑥ 数据健康: graph_check 产物不可读(%s)" % e)
    # 来源评级（G8：断流/catalog 滞后，与门户情报卡红点同一产物；
    # 每日链 graph_check→kg_brief 顺序保证此处读到当日值）
    try:
        with open(RATING_LATEST, encoding="utf-8-sig") as f:
            st = json.load(f).get("staleness") or {}
        stale = st.get("stale_tables") or []
        if stale:
            byf = st.get("stale_by_family") or {}
            fam = " · ".join(
                "%s:%s" % (k, "/".join(v[:2])) for k, v in byf.items()
            ) or " / ".join(stale[:4])
            L.append(
                "  ⚠ 源断流 %d 表: %s（/kg 面板·恢复即消）" % (len(stale), fam[:80])
            )
        if st.get("catalog_stale"):
            L.append(
                "  ⚠ catalog 滞后 %s 天（freshness 判据失真）"
                % st.get("catalog_age_days")
            )
        if not stale and not st.get("catalog_stale"):
            L.append("  源健康: 无断流 · catalog 当日")
    except Exception as e:
        L.append("  源健康: rating 产物不可读(%s)" % str(e)[:50])
    # 影子层 fail 率（G6b：judge 两连败兜底 conf=0 占比；>2% 告警，
    # 已证 fail=裸 bull/bear 输出被设计拒绝，属样本分布非故障）
    try:
        import pandas as pd

        sd = pd.read_parquet(SHADOW_PARQUET, columns=["conf", "judged_at"])
        n = len(sd)
        nf = int((sd["conf"] == 0).sum())
        last = str(sd["judged_at"].max())[:16]
        if n:
            pct = 100.0 * nf / n
            if pct > 2:
                L.append(
                    "  ⚠ 影子层 fail %.1f%%（%d/%d，>2%% 阈值·查 "
                    "Ollama/裸标签率）" % (pct, nf, n)
                )
            else:
                L.append(
                    "  影子层健康: fail %.2f%%（%d/%d · 判定至 %s）"
                    % (pct, nf, n, last)
                )
    except Exception as e:
        L.append("  影子层: notice_llm.parquet 不可读(%s)" % str(e)[:50])
    L.append("  " + BLIND_NOTE)
    L.append("· " + DISCLAIMER)

    head = "🕸 KG 每日情报简报 %s" % d
    text = "\n".join([head] + L)
    # K-STOCK-360 六段分区（按段落序号聚合缩进行），供卡片渲染
    secs, cur = [], [head]
    for line in L:
        if line.startswith("· " + DISCLAIMER):
            cur.append(line)
            break
        if len(line) > 1 and line[0] in "①②③④⑤⑥⑦" + "·" and line[1] in " ":
            if cur:
                secs.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        secs.append("\n".join(cur))
    # KGBRIEF-STATE-20261107：当日①②段标的随 meta 带出（P2-c 焦点池数据源；
    # 水位文件字段，不受 F2_* flag 控制）
    return text, {
        "d": d,
        "n_lines": len(L),
        "secs": secs,
        "last_secs": [{"tc": tc, "nm": nm} for tc, nm in f2_targets],
    }


def _send(text):
    """复用 send_feishu.py；SEND_OK 口径。

    截断护栏（2026-10-02 判例）：send_feishu.py 以 --content 命令行
    参数传全文，Windows CreateProcess 命令行 32K 上限——简报带
    QA drift 全量明细时超限报 WinError 206（今晨链 ABORT 根因）。
    飞书 text 消息上限亦约 150KB，截断到 12K 字符保发送不截断主内容
    （正文摘要在前，drift 明细等长尾在后）。"""
    if len(text) > 12000:
        n0 = len(text)
        text = text[:12000] + "\n…(发送截断，全文见 /kg 面板)"
        print("send: text truncated %d -> 12000" % n0)
    if not os.path.isfile(SEND_PY):
        print("send skip: %s not found" % SEND_PY)
        return False
    fd, tmp = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        r = subprocess.run(
            [sys.executable, SEND_PY, tmp], capture_output=True, text=True, timeout=90
        )
        ok = "SEND_OK" in (r.stdout or "")
        print(
            "send ->", "SEND_OK" if ok else (r.stdout or r.stderr or "?").strip()[:200]
        )
        return ok
    except Exception as e:
        print("send err:", e)
        return False
    finally:
        os.unlink(tmp)


# K-STOCK-360 群同步目标（判例同 K-STOCK-357/358 晨报）
# K-STOCK-403：id 改由 push_targets 单一事实源按名解析
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "news_collector")))
import push_targets  # noqa: E402

SYNC_GROUPS = [(n, push_targets.target_id(n) or "") for n in ("内部测试", "量化群")]


def _build_card_payload(secs):
    """K-STOCK-360 六段 → v1 互动卡（首行加粗分区 + 台账/看板双按钮）。

    v1 模式（不传 use_schema2）：K-STOCK-354 判例——按钮卡勿用 v2；
    按钮域名/访问码单点收敛 portal/feishu_card_v2（K-STOCK-305 判例）。"""
    blocks = []
    for s in secs:
        lines = s.split("\n")
        lines[0] = "**%s**" % lines[0]
        blocks.append("\n".join(lines))
    buttons = None
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))
        from feishu_card_v2 import PORTAL_BASE, PORTAL_KEY, m_url

        # 2026-10-25：优先 /m H5 链接（账本/选股），未启用回退旧公网链
        _ledger = m_url("ledger") or "%s/ledger.html?key=%s" % (PORTAL_BASE, PORTAL_KEY)
        _picks = m_url("picks") or "%s/picks.html?key=%s" % (PORTAL_BASE, PORTAL_KEY)
        buttons = [
            ("💼 交易台账", _ledger, "primary"),
            ("📈 选股看板", _picks, "default"),
        ]
    except Exception:
        pass  # 按钮可选组件：域名模块不可用→纯分区卡照发
    sys.path.insert(0, NEWS_DIR)
    from card_build import build_card

    return build_card(
        "🕸 KG 每日情报简报",
        "结构 × 盘口 × 资金 × 传导 × 席位 × 健康",
        "turquoise",
        blocks,
        footer="个人研究·知识图谱情报简报·非投资建议",
        buttons=buttons,
    )


def _send_card(card):
    """复用 card_build.push_card 发互动卡。"""
    try:
        sys.path.insert(0, NEWS_DIR)
        from card_build import push_card

        return push_card(card)
    except Exception as e:
        print("card send error: %s" % str(e)[:120])
        return False


def _sync_groups(card, text):
    """K-STOCK-360 群同步：卡优先降级文本，单群失败不影响其他。"""
    try:
        sys.path.insert(0, NEWS_DIR)
        from send_feishu import send_content_to

        card_str = json.dumps(card, ensure_ascii=False)
        if len(text) > 12000:  # 群文本同护栏（WinError 206 判例）
            text = text[:12000] + "\n…(发送截断，全文见 /kg 面板)"
        text_str = json.dumps({"text": text}, ensure_ascii=False)
        for gname, gid in SYNC_GROUPS:
            g_ok = False
            try:
                g_ok = send_content_to(gid, "chat_id", card_str, "interactive")
                if not g_ok:
                    g_ok = send_content_to(gid, "chat_id", text_str, "text")
            except Exception as e:
                print("sync %s error: %s" % (gname, str(e)[:100]))
            print("sync %s: %s" % (gname, "SEND_OK" if g_ok else "FAIL"))
    except Exception as e:
        print("group sync import error: %s" % str(e)[:100])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--d", default=None, help="yyyymmdd，默认最新活跃信号日")
    ap.add_argument("--dry", action="store_true", help="只打印不发送")
    ap.add_argument("--force", action="store_true", help="忽略幂等水位")
    a = ap.parse_args()

    d = a.d
    if not d:
        cli = KgClient(retry=1)
        with cli._session() as s:
            d = s.run(
                "MATCH ()-[r:HAS_VOL_EVENT]->() RETURN max(r.validStart) AS d"
            ).data()[0]["d"]
        cli.close()

    if not a.force and not a.dry:
        st = _load_state()
        if st.get("last_date") == d:
            print("brief %s already sent (state), skip" % d)
            return 0

    text, meta = build(d)
    print(text)
    print("─" * 40)
    if a.dry:
        print("[dry] meta=%s" % meta)
        return 0
    # K-STOCK-360 卡优先 → 文本降级保底 → 群同步（水位只看个人送达）
    card = None
    try:
        card = _build_card_payload(meta.get("secs") or [])
    except Exception as e:
        print("card build error: %s" % str(e)[:120])
    ok = _send_card(card) if card is not None else False
    if not ok:
        print("card FAIL -> text fallback")
        ok = _send(text)
    if card is not None:
        _sync_groups(card, text)
    if ok:
        _save_state(
            {
                "last_date": d,
                "sent_at": datetime.now().isoformat(timespec="seconds"),
                "last_secs": meta.get("last_secs") or [],  # KGBRIEF-STATE-20261107
            }
        )
        print("brief %s sent + state saved" % d)
    else:
        print("brief %s send FAILED (state untouched)" % d)
        return 1
    return 0


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "kg_brief", lambda: sys.exit(main()))
