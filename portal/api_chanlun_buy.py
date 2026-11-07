# -*- coding: utf-8 -*-
"""🛒 买点实战台（/bt-buy）——只看买点 · K-ORG-20261107-BUYDESK。

与「缠论工作台」（/bt-chanlun，结构研究）和「预警池」（/bt-chanlun-pool，
六类信号影子观察）分家：本页只服务「今天动不动手、买哪个」这一个动作。
青萍之末（/bt-qpzm）与候风策略卡（8124 独立静态服务）是别的策略，
既不在本页，也不并进缠论页。

数据源（只读，页面不落任何状态）：
  quant_backtest/data/chanlun_pool{,_us,_hk}.jsonl  入池信号（只取 1买/2买/3买）
  quant_backtest/data/chanlun_paper{,_us,_hk}.json   模拟账本（买点仓 + 已平仓买点）
  api_common_stock._stock_sector_rows                个股→板块（板块块异步加载）

架构（自上而下 = 一次买入决策的完整链路）：
  ① 环境判定  今天动不动手（顺风 40 + 量能 30 + 持仓浮盈 30 合成分 + 结论）
  ② 今日名单  分型筛选 + 置信度 + 结构徽章（背驰/量能/中枢）
  ③ 板块聚集  买点砸在哪些板块（异步，带板块 RPS50 与当日涨跌）
  ④ 持仓盯市  买点仓实况（浮动排序 / 20 日离场倒计时 / 到期提示）
  ⑤ 分型战绩  绝对收益 vs 超额收益（alpha）双口径，诚实披露
  ⑥ 纪律卡    入场/持有/离场/仓位/取舍

关键研究结论（写进页面，不藏）：
  已平仓买点 1839 笔（截至 2026-11-05 账本）——绝对收益三类全负
  （1买 -2.33% / 2买 -5.94% / 3买 -10.11%），但超额收益分化：
  1买 +1.41%（alpha 胜率 49.9%）、2买 +0.92%（51.2%）为正，
  3买 -3.80%（33.6%）为负。即「买点赚的是相对收益，不是绝对收益」，
  3买 连相对收益都吃不到，实战中应降权。
"""
import json
import logging
import os
import time

from api_common import QBT_DIR, pill_nav
from chanlun_buy_coarse import coarse_bt_block

BUY_TYPES = ("1买", "2买", "3买")
HOLD_DAYS = 20
# 环境分硬门槛（K-RES-20261107-ENVSTRAT 回测断点，勿随手改：
# 改阈值等于改策略，必须重跑 tools/research/env_strat_backtest.py）
ENV_GATE = 75
_SFX = {"CN": "", "US": "_us", "HK": "_hk"}
_MKT_LABEL = {"CN": "🇨🇳 A股", "US": "🇺🇸 美股", "HK": "🇭🇰 港股"}
_LOG = logging.getLogger(__name__)

# 板块块缓存：(mkt, detect_date) -> (ts, payload)，1 小时
_SEC_CACHE = {"key": None, "ts": 0.0, "val": None}
# 申万行业映射缓存：6 小时（成分表变动极慢）
_SW_CACHE = {"ts": 0.0, "map": None}


def _mkt_of(qs):
    from urllib.parse import parse_qs
    m = ((parse_qs(qs or "").get("m") or [""])[0] or "CN").strip().upper()
    return m if m in _SFX else "CN"


def _sw_map():
    """申万行业映射 {6 位代码: (一级, 二级, 三级)}，进程缓存 6 小时。

    数据源 data/tdx/ts_index_member_all（申万三级分类，5914 只，覆盖率 100%）：
    本地没有任何「通达信一级行业成分」——tdx_sector_member 只有 58 个概念/风格块
    （破净资产、高分红股之流），拿来当板块会误导，所以板块块改用申万。
    """
    now = time.time()
    if _SW_CACHE["map"] is not None and now - _SW_CACHE["ts"] < 6 * 3600:
        return _SW_CACHE["map"]
    mp = {}
    try:
        import glob
        import pandas as pd
        parts = sorted(glob.glob(os.path.join(
            QBT_DIR, "data", "tdx", "ts_index_member_all", "*.parquet")))
        if parts:
            cols = ["l1_name", "l2_name", "l3_name", "ts_code", "out_date"]
            df = pd.concat([pd.read_parquet(p, columns=cols) for p in parts],
                           ignore_index=True)
            act = df[df["out_date"].isna()] if "out_date" in df else df
            for t, l1, l2, l3 in zip(act["ts_code"], act["l1_name"],
                                     act["l2_name"], act["l3_name"]):
                c6 = "".join(ch for ch in str(t) if ch.isdigit())[:6]
                if c6:
                    mp[c6] = (str(l1), str(l2), str(l3))
    except Exception as exc:
        _LOG.warning("silent-except buy-sw-map: %s", exc)
    _SW_CACHE.update({"ts": now, "map": mp})
    return mp


def _load_pool(sfx):
    """两遍流式：先取最新 detect_date 与近端买点日量，再收当日买点。

    单遍会把 1.1 万行全留在内存里，这里保持常量内存（与 heat 端点同口径）。
    """
    path = os.path.join(QBT_DIR, "data", "chanlun_pool%s.jsonl" % sfx)
    latest, counts = None, {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                d = r.get("detect_date")
                if not d:
                    continue
                if not latest or d > latest:
                    latest = d
                if r.get("type") in BUY_TYPES:
                    counts[d] = counts.get(d, 0) + 1
    except OSError:
        return None, [], []
    if not latest:
        return latest, [], sorted(counts.items())[-6:]
    buys = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (r.get("detect_date") == latest
                        and r.get("type") in BUY_TYPES):
                    buys.append(r)
    except OSError:
        pass
    return latest, buys, sorted(counts.items())[-6:]


def _load_paper(sfx):
    try:
        with open(os.path.join(QBT_DIR, "data",
                               "chanlun_paper%s.json" % sfx),
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        _LOG.warning("silent-except buy-paper: %s", exc)
        return {}


def _stat(rows, key="pnl"):
    """→ {n, win(%), avg(%)}；缺失值的行不计入。"""
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0, "win": None, "avg": None}
    wins = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "win": round(wins / len(vals) * 100, 1),
            "avg": round(sum(vals) / len(vals) * 100, 2)}


def _env(buys, open_buys, hist, per_type):
    """环境分 0-100 与结论。三因子都可解释，不做黑箱打分。"""
    n = len(buys)
    up_n = sum(1 for r in buys if r.get("trend") == "up")
    up_share = (up_n / n) if n else 0.0
    s_up = min(1.0, up_share / 0.5) * 40.0

    prev = [c for _, c in hist[:-1]]
    base = (sum(prev) / len(prev)) if prev else float(n or 1)
    ratio = (n / base) if base else 1.0
    s_vol = max(0.0, min(1.0, (ratio - 0.5) / 1.0)) * 30.0

    op = _stat(open_buys)
    avg = op["avg"] if op["avg"] is not None else 0.0
    s_pnl = max(0.0, min(1.0, (avg + 5.0) / 10.0)) * 30.0

    score = int(round(s_up + s_vol + s_pnl))
    # 阈值来自 K-RES-20261107-ENVSTRAT 回测（1829 笔 / 126 日，11/11 检验通过）：
    # >=75 高分段 alpha +2.38% / 胜率 54.4%；60-74 仅 +0.7~1.4%；<60 为负。
    # 断点在 70→75（+1.37 → +2.38 跳升），75 以上走平（2.38/2.49/2.19），
    # 故 75 是"机会 30% 换质量翻倍"的最优切点，60 太松等于没门槛。
    if score >= ENV_GATE:
        band, tone = "可试仓", "ok"
        tip = ("环境达标（%d ≥ 硬门槛 %d）：顺风买点 %d/%d，量能 %.1f×均值，"
               "持仓浮盈 %+.2f%%。回测此段 alpha +2.4%%、胜率 54%%，"
               "可正常出手，优先 1买/2买；3买 仍需三条件齐备。"
               % (score, ENV_GATE, up_n, n, ratio, avg))
    elif score >= 60:
        band, tone = "轻仓精选", "mid"
        tip = ("环境中性（60-74，未达硬门槛 %d）：回测此段 alpha 仅 +0.7~1.4%%、"
               "胜率约 50%%，只做「顺风 + 高置信」交集，单票半仓以下，不追量。"
               % ENV_GATE)
    else:
        band, tone = "只看不动", "bad"
        tip = ("环境未达标（%d < 60）：顺风买点仅 %d/%d，量能 %.1f×均值，"
               "持仓浮盈 %+.2f%%。回测此段 alpha 为负、胜率不足 45%%，"
               "今天只记录，不动手。" % (score, up_n, n, ratio, avg))
    # 3买 负 alpha 的额外提示（研究结论，始终显示）
    p3 = per_type.get("3买") or {}
    if (p3.get("alpha_avg") or 0) < 0:
        tip += " 另：3买 超额收益 %+.2f%%（负），本页仍列出但按纪律降权。" \
               % p3["alpha_avg"]
    return {"score": score, "band": band, "tone": tone, "tip": tip,
            "s_up": round(s_up), "s_vol": round(s_vol), "s_pnl": round(s_pnl),
            "up_share": up_share, "ratio": ratio, "avg_pnl": avg}


def _esc(s):
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _cls(v):
    """数值 → 红涨绿跌着色类（A 股习惯：正=红=up）。"""
    if v is None:
        return "dim"
    return "up" if v > 0 else ("dn" if v < 0 else "dim")


def _num(v, suffix="%", sign=True, nd=2):
    if v is None:
        return "—"
    return ("%+.*f" % (nd, v) if sign else "%.*f" % (nd, v)) + suffix


def _p(v):
    """p 值展示（三位小数，缺值给破折号）。"""
    return "—" if v is None else "%.3f" % v


# 环境分回测证据缓存：(mtime, html)
_EVBT_CACHE = {"mt": None, "html": ""}


def _env_bt_block():
    """①b 环境分回测证据块（读 portal/env_strat_evidence.json，按 mtime 缓存）。

    该 JSON 由 tools/research/env_strat_backtest.py 生成，随代码入库
    （quant_backtest/reports/ 被 gitignore，放那里生产部署会丢）。
    没有产出文件就不渲染——不编造数字。
    """
    global _EVBT_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "env_strat_evidence.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return ""
    if _EVBT_CACHE["mt"] == mt and _EVBT_CACHE["html"]:
        return _EVBT_CACHE["html"]
    try:
        with open(path, encoding="utf-8") as f:
            bt = json.load(f)
    except (OSError, ValueError) as exc:
        _LOG.warning("silent-except env-bt: %s", exc)
        return ""

    meta = bt.get("meta") or {}
    chk = bt.get("checks") or {}
    ok_n = sum(1 for v in chk.values() if v)
    tot = len(chk) or 1
    dc = (bt.get("day_cluster") or {}).get("alpha") or {}
    dcr = (bt.get("day_cluster") or {}).get("ret") or {}
    lag = bt.get("lag1") or {}
    conc = bt.get("concentration") or {}
    b3 = bt.get("band_q3") or []
    verdict = bt.get("verdict") or ""

    def band_rows(bands):
        out = []
        for b in bands:
            al = b.get("alpha") or {}
            rt = b.get("ret") or {}
            out.append(
                '<tr><td>%s</td><td class="dim">%d</td>'
                '<td class="%s">%s</td><td class="%s">%s</td>'
                '<td class="%s">%s</td><td class="%s">%s</td></tr>'
                % (_esc(b.get("label")), b.get("n") or 0,
                   _cls(al.get("avg")), _num(al.get("avg")),
                   _cls(rt.get("avg")), _num(rt.get("avg")),
                   _cls((al.get("win") or 0) - 50) if al.get("win") is not None
                   else "dim", _num(al.get("win"), sign=False, nd=1),
                   _cls((rt.get("win") or 0) - 50) if rt.get("win") is not None
                   else "dim", _num(rt.get("win"), sign=False, nd=1)))
        return "".join(out)

    head = ('<thead><tr><th>档位</th><th>笔数</th><th>alpha 均值</th>'
            '<th>收益均值</th><th>alpha 胜率</th><th>收益胜率</th></tr></thead>')

    chain = "  →  ".join(
        '<span class="%s">%s</span>'
        % (_cls((b.get("alpha") or {}).get("avg")),
           _num((b.get("alpha") or {}).get("avg")))
        for b in b3[:3])

    scan = bt.get("threshold_scan") or []
    scan_rows = "".join(
        '<tr%s><td>≥%d</td><td class="dim">%d</td><td class="dim">%.0f%%</td>'
        '<td class="%s">%s</td><td class="%s">%s</td></tr>'
        % (' style="background:rgba(227,179,65,.10)"' if s["th"] == ENV_GATE else "",
           s["th"], s["n"], s["cover"],
           _cls(s.get("alpha_avg")), _num(s.get("alpha_avg")),
           _cls((s.get("alpha_win") or 0) - 50) if s.get("alpha_win") is not None
           else "dim", _num(s.get("alpha_win"), sign=False, nd=1))
        for s in scan)

    chk_rows = "".join(
        '<div class="ebt-chk"><span class="%s">%s</span> %s</div>'
        % ("up" if v else "dn", "PASS" if v else "FAIL", _esc(k))
        for k, v in chk.items())

    segs = bt.get("segments") or {}
    seg_html = "".join(
        '<div class="ebt-sub">%s</div><table class="ebt-t">%s<tbody>%s</tbody></table>'
        % (_esc(lab), head, band_rows(rows)) for lab, rows in segs.items())
    ex3 = bt.get("excl_3buy") or []
    if ex3:
        seg_html += ('<div class="ebt-sub">剔除 3买 后（只剩 1买/2买）</div>'
                     '<table class="ebt-t">%s<tbody>%s</tbody></table>'
                     % (head, band_rows(ex3)))

    gate_cover = next((s["cover"] for s in scan if s["th"] == ENV_GATE), 0) or 0
    gate_alpha = next((s["alpha_avg"] for s in scan if s["th"] == ENV_GATE), None)

    html = (
        '<div class="sec"><h2>①b 环境分凭什么可信 '
        '<span class="tag g">%d/%d 项检验通过</span>'
        '<span class="tag">%s 笔 · %s 日 · %s</span></h2>'
        % (ok_n, tot, meta.get("sample"), meta.get("days"),
           _esc("~".join(meta.get("window") or [])))
        + '<div class="ebt">'
        + '<div class="ebt-line"><b>三档 alpha</b>（%s）%s　'
          '低中高单调递增，高段是唯一为正的一档</div>'
        % (_esc(" / ".join(b.get("label", "") for b in b3[:3])), chain)
        + '<div class="ebt-line"><b>日级聚类检验</b> 高 %s 日 vs 低 %s 日：'
          '差 <span class="%s">%s</span>，Welch t=%.2f，p=%.4f，'
          'bootstrap 95%%CI [%+.2f, %+.2f]</div>'
        % (dc.get("hi_days"), dc.get("lo_days"), _cls(dc.get("diff")),
           _num(dc.get("diff")), dc.get("t") or 0, dc.get("p") or 1,
           (dc.get("boot") or {}).get("lo") or 0,
           (dc.get("boot") or {}).get("hi") or 0)
        + '<div class="ebt-line"><b>绝对收益也同向</b> 高 %s vs 低 %s'
          '（差 %s，p=%.4f）——不是只靠 alpha 口径撑场面</div>'
        % (_num(dcr.get("hi_avg")), _num(dcr.get("lo_avg")),
           _num(dcr.get("diff")), dcr.get("p") or 1)
        + '<div class="ebt-line"><b>不是 beta 探测器</b> 高分日的指数收益反而更低'
          '（%s vs %s），说明它选的是相对强度而不是大盘方向</div>'
        % (_num((bt.get("beta_check") or {}).get("hi_idx")),
           _num((bt.get("beta_check") or {}).get("lo_idx")))
        + '<div class="ebt-line"><b>可执行性折损</b> 环境分收盘后才算得出，'
          '滞后一日（次日入场）后：日级差 %s（p=%.4f），'
          '但高分段日级 alpha 从 %s 掉到 %s —— '
          '<em>硬门槛能少亏，不等于能赚</em></div>'
        % (_num(lag.get("diff")), lag.get("p") or 1,
           _num(dc.get("hi_avg")), _num(lag.get("hi_day_alpha")))
        + '<div class="ebt-line"><b>结论</b> %s，硬门槛定为 <b>≥%d</b>'
          '（保留 %.0f%% 机会，换 alpha %s）；60 分太松，等同没有门槛</div>'
        % (_esc(verdict), ENV_GATE, gate_cover, _num(gate_alpha))
        + '</div>'
        + '<details class="ebt-d"><summary>展开完整证据：三档明细 / 分段 / '
          '门槛扫描 / 集中度 / 检验清单</summary>'
        + '<div class="ebt-sub">三分位（全因子 score）</div>'
        + ('<table class="ebt-t">%s<tbody>%s</tbody></table>'
           % (head, band_rows(b3)))
        + seg_html
        + '<div class="ebt-sub">门槛扫描（把环境分当硬门槛：机会 vs 质量）</div>'
        + ('<table class="ebt-t"><thead><tr><th>门槛</th><th>笔数</th>'
           '<th>机会占比</th><th>alpha 均值</th><th>alpha 胜率</th>'
           '</tr></thead><tbody>%s</tbody></table>' % scan_rows)
        + '<div class="ebt-sub">高档日集中度（前 3 大日占 %s%% 笔数，'
          '剔除后 alpha 差仍 %s，p=%.4f）</div>'
        % (_num(conc.get("top3_share"), suffix="", sign=False),
           _num(conc.get("diff_after_drop")), conc.get("p_after_drop") or 1)
        + '<div class="ebt-note">拥挤日（信号扎堆）alpha %s（%d 日）vs 稀疏日 %s'
          '（%d 日）——扎堆不额外加价，但也不失效。</div>'
        % (_num(conc.get("crowded_alpha")), conc.get("crowded_days") or 0,
           _num(conc.get("sparse_alpha")), conc.get("sparse_days") or 0)
        + ('<div class="ebt-sub">检验清单</div><div class="ebt-chks">%s</div>'
           % chk_rows)
        + '<div class="ebt-note">数据窗口自 %s 起：此前池每日买点仅 1-5 只'
          '（覆盖度突变），量能因子基数过小会虚假满分；该窗口仅损失 %s 笔。</div>'
        % (meta.get("stable_from"), meta.get("dropped"))
        + '</details>'
        + '<div class="legend">'
          '<b>与页面口径的差异</b>：回测里第三因子（持仓浮盈）用「截至当日的'
          '最近 30 笔已平仓买点均值」代理——没有每日持仓快照，这是最接近且严格因果的'
          '替代；另两项（顺风占比、量能比）与页面完全一致。<br>'
          '<b>alpha 口径</b>：个股 20 日收益 − β×同期全A等权基准（已扣大盘）。'
          '样本期市场整体下跌，绝对收益为负是常态，衡量信号质量看 alpha。</div>'
        + '</div>')
    _EVBT_CACHE.update({"mt": mt, "html": html})
    return html


# 20 日持有组合回测证据缓存
_PBT_CACHE = {"mt": None, "html": ""}


def _portfolio_bt_block():
    """①c 20 日持有 · 可执行口径回测块（读 portal/portfolio_bt_evidence.json）。

    这一块存在的唯一理由：`dt` 与 `detect_date` 不是一回事，而系统里到处
    在用 dt。按 dt+1 开盘回测会得到 1买 97.7% 胜率这种假结果——因为缠论买点
    需要后续 5~43 根 K 线才「长出来」（k 中位 22 天，k=0 占 0%）。
    这里把四种口径并列摆出来，让读页面的人一眼看到未来函数有多大。
    """
    global _PBT_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "portfolio_bt_evidence.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return ""
    if _PBT_CACHE["mt"] == mt and _PBT_CACHE["html"]:
        return _PBT_CACHE["html"]
    try:
        with open(path, encoding="utf-8") as f:
            bt = json.load(f)
    except (OSError, ValueError) as exc:
        _LOG.warning("silent-except pbt: %s", exc)
        return ""

    modes = {m["mode"]: m for m in (bt.get("modes") or [])}
    a, k, c, r = (modes.get(x) or {} for x in ("A", "K", "C", "R"))
    bench = bt.get("benchmark") or {}
    dt_ = bt.get("day_test") or {}
    port = bt.get("portfolio") or []
    by_type = bt.get("by_type") or []
    hold = bt.get("hold_days") or 20

    def ret(m, f="ret", key="avg"):
        return ((m.get(f) or {}) or {}).get(key)

    mode_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="%s">%s</td><td class="%s">%s</td></tr>'
        % (_esc(m.get("name")), ret(m, "ret", "n") or 0,
           _cls(ret(m)), _num(ret(m)),
           _cls((ret(m, "ret", "win") or 0) - 50)
           if ret(m, "ret", "win") is not None else "dim",
           _num(ret(m, "ret", "win"), sign=False, nd=1),
           _cls(ret(m, "alpha")), _num(ret(m, "alpha")),
           _cls((ret(m, "alpha", "win") or 0) - 50)
           if ret(m, "alpha", "win") is not None else "dim",
           _num(ret(m, "alpha", "win"), sign=False, nd=1))
        for m in (bt.get("modes") or []))

    type_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="%s">%s</td><td class="dim">%s</td></tr>'
        % (_esc(t.get("type")), ret(t, "ret", "n") or 0,
           _cls(ret(t)), _num(ret(t)),
           _cls((ret(t, "ret", "win") or 0) - 50)
           if ret(t, "ret", "win") is not None else "dim",
           _num(ret(t, "ret", "win"), sign=False, nd=1),
           _cls(ret(t, "alpha")), _num(ret(t, "alpha")),
           t.get("k_med") if t.get("k_med") is not None else "—")
        for t in by_type)

    port_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="%s">%s</td><td class="%s">%s</td></tr>'
        % (_esc(p.get("label")), p.get("n_cohort") or 0,
           _cls((p.get("cohort_win") or 0) - 50)
           if p.get("cohort_win") is not None else "dim",
           _num(p.get("cohort_win"), sign=False, nd=0),
           _cls(p.get("cohort_avg")), _num(p.get("cohort_avg")),
           _cls(p.get("total")), _num(p.get("total")),
           _cls(p.get("mdd")), _num(p.get("mdd")))
        for p in port)

    mon_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td></tr>'
        % (_esc(m.get("mon")), ret(m, "ret", "n") or 0,
           _cls(ret(m)), _num(ret(m)),
           _cls(ret(m, "alpha")), _num(ret(m, "alpha")))
        for m in (bt.get("monthly") or []))

    best = max(port, key=lambda p: (p.get("total")
                                    if p.get("total") is not None else -999)) if port else {}
    worst = min(port, key=lambda p: (p.get("total")
                                     if p.get("total") is not None else 999)) if port else {}
    a_avg, k_avg = ret(a), ret(k)
    gap = (a_avg or 0) - (k_avg or 0)

    html = (
        '<div class="sec"><h2>①c 20 日持有 · 可执行口径回测 '
        '<span class="tag g">%s 笔</span>'
        '<span class="tag">%s · 持 %d 日</span></h2>'
        % (bt.get("n_sig"), _esc("~".join(bt.get("window") or [])), hold)
        + '<div class="ebt">'
        + '<div class="ebt-line"><b>先看未来函数有多大</b> 同批信号，'
          '按信号日 dt+1 开盘入场算出 <span class="%s">%s</span>（胜率 %.1f%%），'
          '按「信号真正算得出的次日」入场只有 <span class="%s">%s</span>——'
          '差 <b>%.2f 个点</b>，前者是假的</div>'
        % (_cls(a_avg), _num(a_avg), ret(a, "ret", "win") or 0,
           _cls(k_avg), _num(k_avg), gap)
        + '<div class="ebt-line"><b>与随机对照比</b> 同日随机换一只票：'
          '主口径 <span class="%s">%s</span> vs 随机 <span class="%s">%s</span>，'
          '差 %s（日级 Welch p=%.3f）——<em>整体跑输随机，且是被 3买 拖下来的</em>'
          '（分型明细见下）</div>'
        % (_cls(k_avg), _num(k_avg), _cls(ret(r)), _num(ret(r)),
           _num(bt.get("lift_vs_random")), dt_.get("p") if dt_.get("p") is not None else 1)
        + '<div class="ebt-line"><b>基准</b> 同期上证 <span class="%s">%s</span>，'
          '信号窗口内指数平均 %s；主口径 %s —— 20 日持有跑输基准约 %.1f 个点</div>'
        % (_cls(bench.get("index_window_pct")),
           _num(bench.get("index_window_pct")),
           _num(bench.get("index_hold_mean_pct")),
           _num(k_avg),
           abs((k_avg or 0) - (bench.get("index_hold_mean_pct") or 0)))
        + '<div class="ebt-line"><b>分型：1买/2买 略赢随机，3买 明显输</b> '
          '1买 <span class="%s">%s</span>（alpha %s，比同日随机 %s，p=%s）／'
          '2买 %s（比随机 %s）／<span class="%s">3买 %s</span>'
          '（alpha %s，比随机 <span class="%s">%s</span>，p=%s）</div>'
        % (_cls(ret(by_type[0] if by_type else {}, "ret")),
           _num(ret(by_type[0] if by_type else {})),
           _num(ret(by_type[0] if by_type else {}, "alpha")),
           _num((by_type[0].get("lift") if by_type else None)),
           _p(by_type[0].get("lift_p") if by_type else None),
           _num(ret(by_type[1] if len(by_type) > 1 else {})),
           _num(by_type[1].get("lift") if len(by_type) > 1 else None),
           _cls(ret(by_type[2] if len(by_type) > 2 else {}, "ret")),
           _num(ret(by_type[2] if len(by_type) > 2 else {})),
           _num(ret(by_type[2] if len(by_type) > 2 else {}, "alpha")),
           _cls(by_type[2].get("lift") if len(by_type) > 2 else None),
           _num(by_type[2].get("lift") if len(by_type) > 2 else None),
           _p(by_type[2].get("lift_p") if len(by_type) > 2 else None))
        + '<div class="ebt-line"><b>组合级（20 槽轮动，每组 1/20 权益）</b> '
          '最差配置「%s」累计 <span class="%s">%s</span>；最好配置「%s」'
          '累计 <span class="%s">%s</span>、最大回撤 %s、夏普 %s</div>'
        % (_esc(worst.get("label")), _cls(worst.get("total")),
           _num(worst.get("total")),
           _esc(best.get("label")), _cls(best.get("total")),
           _num(best.get("total")), _num(best.get("mdd")),
           _num(best.get("sharpe"), suffix="", sign=False))
        + '<div class="ebt-line"><b>结论</b> 可执行口径下'
          '<em>这套买点不能当多头策略直接做</em>：绝对收益为负、超额为负、'
          '整体还略输随机（p=%.2f，不显著）。唯一站得住的两条：'
          '<b>3买 必须停用</b>（比随机还差 4 个点，p=%.3f）；'
          '<b>1买/2买 有约 1 个点的微弱选股优势但未达显著</b>，'
          '在下跌市里不足以覆盖 beta。要投实战，根子在「信号确认太晚」——'
          'k 中位 %d 个交易日，等它能算出来，底部那一段已经走完</div>'
        % (dt_.get("p") if dt_.get("p") is not None else 1,
           (by_type[2].get("lift_p") if len(by_type) > 2
            and by_type[2].get("lift_p") is not None else 1),
           22)
        + '</div>'
        + '<details class="ebt-d"><summary>展开完整证据：四口径 / 分型 / '
          '组合 / 按月</summary>'
        + '<div class="ebt-sub">四种入场口径（只改入场时点）</div>'
        + ('<table class="ebt-t"><thead><tr><th>口径</th><th>笔数</th>'
           '<th>净收益</th><th>收益胜率</th><th>alpha</th><th>alpha 胜率</th>'
           '</tr></thead><tbody>%s</tbody></table>' % mode_rows)
        + '<div class="ebt-sub">按分型（可执行口径）</div>'
        + ('<table class="ebt-t"><thead><tr><th>分型</th><th>笔数</th>'
           '<th>净收益</th><th>胜率</th><th>alpha</th><th>中位 k</th>'
           '</tr></thead><tbody>%s</tbody></table>' % type_rows)
        + '<div class="ebt-sub">组合级 20 槽轮动</div>'
        + ('<table class="ebt-t"><thead><tr><th>组合</th><th>到期组</th>'
           '<th>组胜率</th><th>组均</th><th>累计</th><th>最大回撤</th>'
           '</tr></thead><tbody>%s</tbody></table>' % port_rows)
        + '<div class="ebt-sub">按入场月</div>'
        + ('<table class="ebt-t"><thead><tr><th>月份</th><th>笔数</th>'
           '<th>净收益</th><th>alpha</th></tr></thead><tbody>%s</tbody></table>'
           % mon_rows)
        + '</details>'
        + '<div class="legend">'
          '<b>三个日期不是一回事</b>：<b>dt</b> 信号所在 K 线（买点成立的低点，'
          'price 就是该 bar 低点）；<b>可知日</b> 把 K 线截断重放时该信号首次'
          '出现的日子（k 就是落后的交易日数，实测 k=0 占 0%、中位 22 天）；'
          '<b>detect_date</b> 信号被扫进池子的日子（历史补录批次决定，与信息可得性无关）。'
          '只有「可知日次日开盘」才是真人能成交的价格。<br>'
          '<b>成本</b>：买 0.075% + 卖 0.125%（含印花税）；<b>复权</b>：'
          'close×复权因子，因子缺失沿用前值。<br>'
          '<b>alpha 口径不同，别跟 ⑤ 混着读</b>：本块 alpha = 个股收益 −'
          '同期上证指数；⑤ 分型战绩的 alpha = 个股收益 − β×全A等权。'
          '两者方向一致（1买/2买 略正、3买 明显负），幅度会有差异。</div>'
        + '</div>')
    _PBT_CACHE.update({"mt": mt, "html": html})
    return html


_CSS = """
<style>
:root{--bg:#0d1117;--card:#161b22;--card2:#1c2129;--line:#30363d;
--text:#e6edf3;--dim:#8b949e;--up:#f85149;--dn:#3fb950;--gold:#e3b341;
--brand:#58a6ff;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);
font:14px/1.72 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:16px 22px 56px}
h1{font-size:22px;letter-spacing:.3px;display:flex;align-items:center;gap:10px}
.sub{color:var(--dim);font-size:12.5px;margin:4px 0 14px}
.sub b{color:var(--text)}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(5,1fr);margin-bottom:12px}
@media(max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:11px 14px}
.kpi .v{font-size:21px;font-weight:800;font-family:Consolas,monospace;
line-height:1.25}
.kpi .l{font-size:11px;color:var(--dim);margin-top:2px}
.up{color:var(--up)}.dn{color:var(--dn)}.gd{color:var(--gold)}
.sec{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:15px 18px;margin-bottom:12px}
.sec h2{font-size:15px;font-weight:700;margin-bottom:10px;display:flex;
gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:11px;color:var(--brand);background:rgba(88,166,255,.12);
border-radius:4px;padding:1px 8px;font-weight:600;font-family:inherit}
.tag.g{color:var(--gold);background:rgba(227,179,65,.12)}
.tag.r{color:var(--up);background:rgba(248,81,73,.12)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--dim);font-weight:600;text-align:right;padding:6px 8px;
border-bottom:1px solid var(--line);font-size:11.5px;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #21262d;text-align:right;
font-family:Consolas,monospace;white-space:nowrap}
th:first-child,td:first-child,td.nm{text-align:left;font-family:inherit}
tbody tr:hover td{background:var(--card2)}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
.pill{display:inline-block;font-size:10.5px;border-radius:9px;padding:0 7px;
font-family:inherit;line-height:16px}
.p1{color:#f85149;border:1px solid #f85149}
.p2{color:#e3b341;border:1px solid #e3b341}
.p3{color:#58a6ff;border:1px solid #58a6ff}
.chip{display:inline-block;font-size:10.5px;border-radius:4px;padding:0 5px;
margin-right:3px;font-family:inherit;line-height:15px;background:#21262d;
color:var(--dim)}
.chip.on{color:var(--gold);background:rgba(227,179,65,.14)}
.chip.vv{color:var(--dn);background:rgba(63,185,80,.14)}
.chip.warn{color:var(--up);background:rgba(248,81,73,.14)}
.filters{display:flex;gap:6px;margin-bottom:9px;flex-wrap:wrap;
align-items:center}
.fbtn{background:#21262d;color:var(--text);border:1px solid var(--line);
border-radius:14px;padding:2px 13px;font-size:12px;cursor:pointer;
font-family:inherit}
.fbtn.on{border-color:var(--brand);color:var(--brand);font-weight:700}
.banner{border-radius:12px;padding:14px 18px;margin-bottom:12px;
border:1px solid var(--line);background:var(--card);
display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.banner.ok{border-color:#3fb950;background:rgba(63,185,80,.05)}
.banner.mid{border-color:#e3b341;background:rgba(227,179,65,.05)}
.banner.bad{border-color:#f85149;background:rgba(248,81,73,.05)}
.banner .score{font-size:34px;font-weight:900;font-family:Consolas,monospace;
line-height:1;min-width:74px;text-align:center}
.banner .band{font-size:16px;font-weight:800;margin-bottom:2px}
.banner .tip{font-size:12.5px;color:#c9d1d9;max-width:920px}
.bars{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.bars span{font-size:10.5px;color:var(--dim);background:#21262d;
border-radius:4px;padding:1px 7px;font-family:Consolas,monospace}
.bar{height:7px;border-radius:4px;background:#21262d;overflow:hidden;
margin-top:4px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff)}
.spark{display:flex;align-items:flex-end;gap:3px;height:36px;margin-top:4px}
.spark i{display:block;width:10px;background:#1f6feb;border-radius:2px 2px 0 0;
opacity:.85}
.dim{color:var(--dim)}
.legend{font-size:11.5px;color:var(--dim);margin-top:8px;line-height:1.7}
.legend b{color:#c9d1d9}
.ebt{background:var(--card2);border:1px solid var(--line);border-radius:8px;
padding:12px 14px}
.ebt-line{font-size:12.5px;color:#b9c3cd;line-height:2}
.ebt-line b{color:var(--text);margin-right:6px}
.ebt-line em{color:var(--gold);font-style:normal}
.ebt-d{margin-top:10px}
.ebt-d summary{cursor:pointer;font-size:12px;color:var(--brand);
padding:8px 0 4px;list-style:none;user-select:none}
.ebt-d summary::-webkit-details-marker{display:none}
.ebt-d summary:before{content:"▸ ";color:var(--dim)}
.ebt-d[open] summary:before{content:"▾ "}
.ebt-sub{font-size:11.5px;color:var(--gold);margin:12px 0 5px}
.ebt-t{width:auto;min-width:520px;border-collapse:collapse;font-size:12px}
.ebt-t th{text-align:left;color:var(--dim);font-weight:400;
border-bottom:1px solid var(--line);padding:4px 22px 4px 8px;white-space:nowrap}
.ebt-t td{padding:4px 22px 4px 8px;border-bottom:1px solid rgba(48,54,61,.4);
font-variant-numeric:tabular-nums;white-space:nowrap}
.ebt-chks{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
gap:2px 14px;font-size:11.5px}
.ebt-chk{color:var(--dim)}
.ebt-chk span{font-weight:600;margin-right:5px}
.ebt-note{font-size:11.5px;color:var(--dim);margin-top:6px;line-height:1.65}
.sec-list{display:flex;flex-direction:column;gap:6px}
.sec-row{display:grid;grid-template-columns:180px 1fr 74px 74px 62px;
gap:10px;align-items:center;font-size:12.5px}
.sec-row .nm{font-family:inherit;color:var(--text);overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.sec-row .track{background:#21262d;border-radius:4px;height:16px;
position:relative;overflow:hidden}
.sec-row .track i{display:block;height:100%;background:linear-gradient(90deg,
rgba(88,166,255,.55),rgba(88,166,255,.95))}
.sec-row .track b{position:absolute;right:6px;top:0;font-size:11px;
font-weight:700;font-family:Consolas,monospace;color:#e6edf3;line-height:16px}
.perf{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.perf{grid-template-columns:1fr}}
.pcard{background:var(--card2);border:1px solid var(--line);
border-radius:10px;padding:12px 14px}
.pcard .hd{font-size:13px;font-weight:700;margin-bottom:8px;display:flex;
gap:6px;align-items:center}
.pcard .row{display:flex;justify-content:space-between;font-size:12px;
padding:2px 0;font-family:Consolas,monospace}
.pcard .row span:first-child{font-family:inherit;color:var(--dim)}
.verdict{font-size:11.5px;margin-top:8px;padding-top:7px;
border-top:1px dashed var(--line);color:#c9d1d9}
.rules{font-size:13px;color:#c9d1d9;line-height:2.05}
.rules b{color:var(--text)}
.rules em{color:var(--gold);font-style:normal;font-weight:700}
</style>"""


def _buy_row(r):
    t = _esc(r.get("type"))
    cls = {"1买": "p1", "2买": "p2", "3买": "p3"}.get(t, "")
    trend = _esc(r.get("trend") or "")
    tr_txt = {"up": "↑ 顺", "range": "~ 震", "down": "↓ 逆"}.get(trend, trend or "—")
    tr_cls = {"up": "up", "range": "dim", "down": "dn"}.get(trend, "dim")
    conf = r.get("conf")
    conf_v = float(conf) if isinstance(conf, (int, float)) else 0.0
    chips = []
    for key, lab, title in (("bei", "背驰", "本级别背驰"), ("bei2", "二背", "二类背驰"),
                            ("tbe", "趋背", "趋势背驰"), ("mbei", "末背", "末段背驰")):
        if r.get(key):
            chips.append('<span class="chip on" title="%s">%s</span>' % (title, lab))
    if r.get("vv"):
        chips.append('<span class="chip vv" title="量能验证通过">量验</span>')
    if r.get("div_area"):
        chips.append('<span class="chip warn" title="面积背驰">面背</span>')
    reg = r.get("reg")
    if reg:
        chips.append('<span class="chip" title="中枢/走势方向 reg">%s</span>'
                     % {"up": "中枢↑", "down": "中枢↓",
                        "flat": "中枢平"}.get(_esc(reg), _esc(reg)))
    npr = r.get("n_prior")
    if npr:
        chips.append('<span class="chip" title="该股历史同类买点先验次数">先验%s</span>' % _esc(npr))
    fv = r.get("fv_area")
    fv_txt = ""
    if isinstance(fv, (list, tuple)) and len(fv) == 2 and fv[0]:
        try:
            fv_txt = "公允价值带 %s ~ %s" % (fv[0], fv[1])
        except Exception:
            fv_txt = ""
    entry = r.get("entry_px")
    entry_s = "—" if entry in (None, "None") else _esc(entry)
    price_cell = ('<td title="%s">%s</td>' % (_esc(fv_txt), entry_s)
                  if fv_txt else "<td>%s</td>" % entry_s)
    lag = r.get("lag")
    lag_s = ""
    if lag not in (None, "None", ""):
        lag_s = '<span class="chip warn" title="信号日早于检出日">滞后%s</span>' % _esc(lag)
    return (
        '<tr data-type="{t}" data-trend="{tr}" data-conf="{cf}">'
        '<td><a href="/bt-chanlun?code={c6}" title="看结构图">{code}</a></td>'
        '<td class="nm">{name}</td>'
        '<td><span class="pill {cls}">{t}</span></td>'
        '<td title="结构置信度 conf">{cv:.2f}<div class="bar" style="width:52px;margin-left:auto">'
        '<i style="width:{cw:.0f}%"></i></div></td>'
        '<td class="{trc}">{trt}</td>'
        '<td class="nm" style="text-align:left">{chips}</td>'
        '<td>{dt}</td>{px}<td class="nm" style="text-align:left">{lag}</td></tr>'
    ).format(t=t, tr=trend, cf=conf_v, c6=_esc("".join(ch for ch in str(r.get("code") or "")
                                                    if ch.isdigit())[:6]),
             code=_esc(r.get("code")), name=_esc(r.get("name")),
             cls=cls, cv=conf_v, cw=max(4.0, min(100.0, conf_v * 100)),
             trc=tr_cls, trt=tr_txt, chips="".join(chips),
             dt=_esc(r.get("dt") or "—"), px=price_cell, lag=lag_s)


def _pos_row(p):
    pnl = p.get("pnl")
    pnl_v = float(pnl) if isinstance(pnl, (int, float)) else 0.0
    days = p.get("days_held") or 0
    left = max(0, HOLD_DAYS - days)
    t = p.get("type")
    leave = ('<span class="dim">还剩 %d 日</span>' % left) if left else \
        '<span class="gd">到期应离场</span>'
    return (
        '<tr><td><a href="/bt-chanlun?code={c6}">{code}</a></td>'
        '<td class="nm">{name}</td>'
        '<td><span class="pill {cls}">{t}</span></td>'
        '<td>{ed}</td><td>{ep}</td><td>{lp}</td>'
        '<td class="{pc}">{pv:+.2f}%</td><td>{d}</td>'
        '<td class="nm" style="text-align:left">{leave}</td></tr>'
    ).format(c6=_esc("".join(ch for ch in str(p.get("code") or "")
                             if ch.isdigit())[:6]),
             code=_esc(p.get("code")), name=_esc(p.get("name")),
             cls={"1买": "p1", "2买": "p2", "3买": "p3"}.get(t, ""), t=_esc(t),
             ed=_esc(p.get("entry_date") or "—"), ep=_esc(p.get("entry_px") or "—"),
             lp=_esc(p.get("last_px") or "—"),
             pc="up" if pnl_v > 0 else "dn", pv=pnl_v * 100, d=days, leave=leave)


def _perf_card(t, s, a):
    """单分型战绩卡：绝对 + 超额双口径。"""
    if not s.get("n"):
        return ('<div class="pcard"><div class="hd"><span class="pill %s">%s</span>'
                '无样本</div></div>' % ({"1买": "p1", "2买": "p2", "3买": "p3"}[t], t))
    abs_cls = "up" if (s["avg"] or 0) > 0 else "dn"
    al_cls = "up" if (a["avg"] or 0) > 0 else "dn"
    if (a["avg"] or 0) > 0.5:
        verdict = ("超额为正（%+.2f%%），是可用的一类——顺风段优先。"
                   % a["avg"])
    elif (a["avg"] or 0) > 0:
        verdict = ("超额微正（%+.2f%%），只在顺风 + 高置信时用，别单独押。"
                   % a["avg"])
    else:
        verdict = ("超额为负（%+.2f%%），<em>连相对收益都吃不到</em>——降权，"
                   "只当反弹信号看。" % a["avg"])
    return (
        '<div class="pcard">'
        '<div class="hd"><span class="pill {cls}">{t}</span>样本 {n} 笔</div>'
        '<div class="row"><span>绝对收益均值</span><b class="{ac}">{av:+.2f}%</b></div>'
        '<div class="row"><span>绝对胜率</span><span>{aw}%</span></div>'
        '<div class="row"><span>超额均值 alpha</span><b class="{lc}">{lv:+.2f}%</b></div>'
        '<div class="row"><span>超额胜率</span><span>{lw}%</span></div>'
        '<div class="verdict">{vd}</div>'
        '</div>'
    ).format(cls={"1买": "p1", "2买": "p2", "3买": "p3"}[t], t=t, n=s["n"],
             ac=abs_cls, av=s["avg"] or 0, aw=s["win"] if s["win"] is not None else "—",
             lc=al_cls, lv=a["avg"] or 0, lw=a["win"] if a["win"] is not None else "—",
             vd=verdict)


class Handler:  # Mixin 壳：经基座多继承并入 Handler
    """🛒 买点实战台页面 + 板块聚集接口。"""

    def _chanlun_buy_page(self, qs=""):
        mkt = _mkt_of(qs)
        sfx = _SFX[mkt]
        detect, buys, hist = _load_pool(sfx)
        paper = _load_paper(sfx)
        open_buys = [p for p in (paper.get("open") or [])
                     if p.get("type") in BUY_TYPES]
        closed_buys = [p for p in (paper.get("closed") or [])
                       if p.get("type") in BUY_TYPES]

        per_type = {}
        for t in BUY_TYPES:
            rows = [r for r in closed_buys if r.get("type") == t]
            per_type[t] = {"abs": _stat(rows, "ret"), "alpha": _stat(rows, "alpha")}
        # 供 _env 用的扁平化（alpha_avg）
        flat = {t: {"alpha_avg": v["alpha"]["avg"] or 0.0} for t, v in per_type.items()}

        open_buys.sort(key=lambda r: (r.get("pnl") or 0), reverse=True)
        t_rank = {"up": 0, "range": 1, "down": 2}
        buys.sort(key=lambda r: (BUY_TYPES.index(r.get("type")),
                                 -float(r.get("conf") or 0),
                                 t_rank.get(r.get("trend"), 3)))

        n1 = sum(1 for r in buys if r.get("type") == "1买")
        n2 = sum(1 for r in buys if r.get("type") == "2买")
        n3 = sum(1 for r in buys if r.get("type") == "3买")
        up_n = sum(1 for r in buys if r.get("trend") == "up")
        hi_n = sum(1 for r in buys if float(r.get("conf") or 0) >= 0.5)
        env = _env(buys, open_buys, hist, flat)
        op_stat = _stat(open_buys)
        gain_n = sum(1 for p in open_buys if (p.get("pnl") or 0) > 0)

        mx = max([c for _, c in hist], default=1) or 1
        spark = "".join(
            '<i style="height:%dpx" title="%s：%d 个买点"></i>'
            % (max(3, c / mx * 36), _esc(d), c) for d, c in hist)
        hist_txt = " · ".join("%s %d" % (_esc(d[5:]), c) for d, c in hist[-6:])

        tone = env["tone"]
        tone_v = {"ok": "up", "mid": "gd", "bad": "dn"}[tone]
        rows_html = "".join(_buy_row(r) for r in buys) or \
            '<tr><td colspan="9" class="dim">当日无买点入池（空仓也是一种仓位）</td></tr>'
        pos_rows = open_buys[:10]
        tail = open_buys[-5:] if len(open_buys) > 20 else []
        pos_html = "".join(_pos_row(p) for p in pos_rows)
        if tail:
            pos_html += ('<tr><td colspan="9" class="dim" style="text-align:center;'
                         'border-bottom:1px dashed #30363d">'
                         '… 中间 %d 只省略（按浮动降序）…</td></tr>'
                         % (len(open_buys) - 15))
            pos_html += "".join(_pos_row(p) for p in tail)
        if not pos_html:
            pos_html = ('<tr><td colspan="9" class="dim">'
                        '当前无买点持仓</td></tr>')
        more = ('<div class="dim" style="font-size:11.5px;margin-top:6px">'
                '共 %d 只买点仓：列浮盈前 10 + 浮亏后 5（尾部才是风险所在），'
                '完整账本见 <a href="/bt-chanlun-pool">预警池</a></div>'
                % len(open_buys)) if len(open_buys) > 15 else ""
        perf_html = "".join(
            _perf_card(t, per_type[t]["abs"], per_type[t]["alpha"])
            for t in BUY_TYPES)

        mkt_links = " · ".join(
            ("<b>%s</b>" % _MKT_LABEL[k]) if k == mkt
            else ('<a href="/bt-buy?m=%s">%s</a>' % (k, _MKT_LABEL[k]))
            for k in ("CN", "US", "HK"))

        body = []
        add = body.append
        add("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<link rel='stylesheet' href='/static/tokens.css'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>🛒 买点实战台 · %s</title>" % _MKT_LABEL[mkt])
        add(_CSS)
        add("</head><body>")
        add(pill_nav("chanlun-buy"))
        add('<div class="wrap">')
        add('<h1>🛒 买点实战台 <span class="tag">只看买点</span>'
            '<span class="tag g">%s</span></h1>' % _MKT_LABEL[mkt])
        add('<div class="sub">数据日 <b>%s</b> · 账本更新 %s · 市场 %s · '
            '结构研究去 <a href="/bt-chanlun">🧭 缠论工作台</a> · '
            '六类信号影子去 <a href="/bt-chanlun-pool">🛟 预警池</a>'
            '</div>' % (_esc(detect or "—"), _esc(paper.get("updated_at") or "—"),
                        mkt_links))

        # ① 环境判定
        add('<div class="banner %s">' % tone)
        add('<div class="score %s">%d</div>' % (tone_v, env["score"]))
        add('<div style="flex:1;min-width:320px">'
            '<div class="band %s">① 环境判定 · %s</div>'
            '<div class="tip">%s</div>'
            '<div class="bars"><span>顺风 %d/40</span><span>量能 %d/30</span>'
            '<span>浮盈 %d/30</span><span>顺风占比 %.0f%%</span>'
            '<span>量能 %.1f× 近端均值</span></div></div>'
            % (tone_v, env["band"], env["tip"], env["s_up"], env["s_vol"],
               env["s_pnl"], env["up_share"] * 100, env["ratio"]))
        add("</div>")

        # ①b 环境分回测证据（无产出文件则不渲染）
        add(_env_bt_block())

        # ①c 20 日持有可执行口径回测（同上，没有快照就不渲染）
        add(_portfolio_bt_block())

        # ①d 粗信号可执行性检验（当天可判的那一层到底能不能赚）
        add(coarse_bt_block(_esc, _cls, _num, _LOG))

        # KPI
        add('<div class="grid kpis">')
        add('<div class="kpi"><div class="v">%d</div><div class="l">'
            '今日买点（1买 %d · 2买 %d · 3买 %d）</div></div>'
            % (len(buys), n1, n2, n3))
        add('<div class="kpi"><div class="v gd">%d<span style="font-size:12px">'
            ' / %d</span></div><div class="l">趋势顺风 up</div></div>'
            % (up_n, len(buys)))
        add('<div class="kpi"><div class="v">%d</div><div class="l">'
            '高置信 conf≥0.5</div></div>' % hi_n)
        add('<div class="kpi"><div class="v %s">%s</div><div class="l">'
            '买点仓 %d 只 · 浮盈 %d 只</div></div>'
            % ("up" if (op_stat["avg"] or 0) > 0 else "dn",
               ("%+.2f%%" % op_stat["avg"]) if op_stat["avg"] is not None else "—",
               len(open_buys), gain_n))
        add('<div class="kpi" title="近 6 个扫描日买点数：%s">'
            '<div class="v">%d</div><div class="l">'
            '近 6 个扫描日买点量（最新日）</div><div class="spark">%s</div></div>'
            % (_esc(hist_txt or "无历史"), len(buys), spark))
        add("</div>")

        # ② 名单
        add('<div class="sec">')
        add('<h2>② 今日买点名单 <span class="tag">%s 扫描</span>'
            '<span class="tag">共 <b id="bt-n">%d</b> 条</span>'
            '<span class="tag g">点代码进结构图</span></h2>' % (_esc(detect or "—"), len(buys)))
        add('<div class="filters">'
            '<button class="fbtn on" onclick="btF(this,\'\')">全部 %d</button>'
            '<button class="fbtn" onclick="btF(this,\'1买\')">1买 %d</button>'
            '<button class="fbtn" onclick="btF(this,\'2买\')">2买 %d</button>'
            '<button class="fbtn" onclick="btF(this,\'3买\')">3买 %d</button>'
            '<button class="fbtn" id="bt-up" onclick="btU(this)">只看顺风 ↑</button>'
            '<button class="fbtn" id="bt-hi" onclick="btH(this)">只看高置信 ≥0.5</button>'
            '</div>' % (len(buys), n1, n2, n3))
        add('<table id="bt-tbl"><thead><tr><th>代码</th><th>名称</th><th>分型</th>'
            '<th>置信</th><th>趋势</th><th style="text-align:left">结构证据</th>'
            '<th>信号日</th><th>参考入价</th><th style="text-align:left">时效</th>'
            '</tr></thead><tbody>')
        add(rows_html)
        add('</tbody></table>')
        add('<div class="legend">'
            '<b>参考入价</b> = 检出日收盘（paper 口径，悬停看公允价值带）；'
            '<b>置信</b> = 结构置信度 conf；<b>背驰/二背/趋背/末背</b> = 各级背驰判定；'
            '<b>量验</b> = 量能验证通过；<b>面背</b> = 面积背驰；'
            '<b>先验N</b> = 该股历史同类买点已出现次数；'
            '<b>滞后</b> = 信号日早于检出日（补扫）。</div>')
        add("</div>")

        # ③ 板块聚集（异步）
        add('<div class="sec"><h2>③ 板块聚集 <span class="tag">买点砸在哪</span>'
            '<span class="tag g" id="sec-st">加载中…</span></h2>'
            '<div class="sec-list" id="sec-list">'
            '<div class="dim" style="font-size:12.5px">正在按申万行业归并今日买点…</div>'
            '</div>'
            '<div class="legend">行业取<b>申万二级</b>（data/tdx/ts_index_member_all，'
            '5914 只全覆盖）；<b>多空比</b> = 该行业当日买点数 ÷ 卖点数（同一份池子、'
            '同一天，同口径）——买入点集中且卖点稀少的行业才是真转多；'
            '买点多但卖点更多的行业，说明只是波动大，别当板块启动。</div></div>')

        # ④ 持仓
        add('<div class="sec">')
        add('<h2>④ 持仓盯市 <span class="tag">买点仓 %d / 全账本 %d</span>'
            '<span class="tag g">持有 %d 交易日 · 到期收盘出</span></h2>'
            % (len(open_buys), len(paper.get("open") or []), HOLD_DAYS))
        add('<table><thead><tr><th>代码</th><th>名称</th><th>分型</th><th>入场日</th>'
            '<th>入价</th><th>现价</th><th>浮动</th><th>已持有</th>'
            '<th style="text-align:left">离场</th></tr></thead><tbody>')
        add(pos_html)
        add("</tbody></table>")
        add(more)
        add("</div>")

        # ⑤ 战绩
        add('<div class="sec">')
        add('<h2>⑤ 分型战绩 <span class="tag">已平仓买点 %d 笔</span>'
            '<span class="tag r">绝对收益 vs 超额收益</span></h2>' % len(closed_buys))
        add('<div class="perf">%s</div>' % perf_html)
        add('<div class="legend">'
            '<b>口径</b>：检出日收盘入场 · 持有 %d 交易日 · 无盘中止损 · 单票等权。'
            '<b>绝对收益</b> = 20 日持有收益；<b>超额收益 alpha</b> = 个股收益 − '
            '同期全A等权基准（EW-allA excl 000001.SH）。'
            '两者必须分开看：样本期市场整体下跌，买点绝对收益为负并不代表信号无效，'
            '真正该问的是「有没有跑赢基准」。<br>'
            '<b>结论</b>：1买/2买 超额为正但弱（约 +1%%/20日），3买 超额为负——'
            '实战中 3买 降权，1买/2买 也要叠环境判定才用。</div>' % HOLD_DAYS)
        add("</div>")

        # ⑥ 纪律卡
        add('<div class="sec"><h2>⑥ 纪律卡 <span class="tag">照做，别临场改</span></h2>')
        add('<div class="rules">'
            '<b>1 入场</b>：<em>环境分 ≥%d 才开新仓</em>（1829 笔回测的唯一断点，'
            '低于它 alpha 转负）；只做 1买/2买/3买，<em>三卖一律不碰</em>；'
            '参考入价取检出日收盘，次日开盘可跟，不追高。<br>'
            '<b>2 持有</b>：<em>%d 个交易日</em>，中途不看盘不做 T，不加仓不摊平。<br>'
            '<b>3 离场</b>：到期收盘出，不设盘中止损（与回测口径一致，'
            '改了就不是同一套战绩）。<br>'
            '<b>4 仓位</b>：单票等权。<em>环境分 &lt;60 → 空仓</em>（回测该段 alpha 为负、'
            '胜率 &lt;45%%）；60-74 → 总仓位 ≤3 成且只做顺风+高置信；≥%d → 可上 7 成。<br>'
            '<b>5 取舍</b>：<em>3买 超额为负</em>，只在顺风 + 高置信 + 强板块三者'
            '同时满足时做，否则跳过；1买/2买 是主力。<br>'
            '<b>6 停手</b>：环境分连续 3 日 &lt;60 → 整周只看不动，'
            '不要用「再试一次」跟市场讲道理。</div></div>'
            % (ENV_GATE, HOLD_DAYS, ENV_GATE))

        add('<div class="dim" style="font-size:11.5px;margin-top:14px">'
            '数据源 chanlun_pool%s.jsonl + chanlun_paper%s.json（只读）· '
            '与青萍之末（/bt-qpzm）、候风策略卡（独立 8124 静态服务）无关 · '
            '<b>模拟数据，不构成投资建议</b> · K-ORG-20261107-BUYDESK</div>'
            % (sfx, sfx))
        add("</div>")

        add("""
<script>
(function(){
 var T='', UP=false, HI=false;
 function apply(){
  var rows=document.querySelectorAll('#bt-tbl tbody tr'), n=0;
  for(var i=0;i<rows.length;i++){
   var tr=rows[i], ok=true;
   if(!tr.dataset.type){continue;}
   if(T && tr.dataset.type!==T) ok=false;
   if(ok && UP && tr.dataset.trend!=='up') ok=false;
   if(ok && HI && parseFloat(tr.dataset.conf||'0')<0.5) ok=false;
   tr.style.display = ok ? '' : 'none';
   if(ok) n++;
  }
  var el=document.getElementById('bt-n'); if(el) el.textContent=n;
 }
 window.btF=function(btn,t){
  T=t;
  var bs=document.querySelectorAll('.fbtn');
  for(var i=0;i<bs.length;i++) bs[i].classList.remove('on');
  btn.classList.add('on');
  if(UP) document.getElementById('bt-up').classList.add('on');
  if(HI) document.getElementById('bt-hi').classList.add('on');
  apply();
 };
 window.btU=function(btn){ UP=!UP; btn.classList.toggle('on',UP); apply(); };
 window.btH=function(btn){ HI=!HI; btn.classList.toggle('on',HI); apply(); };
 var m = new URLSearchParams(location.search).get('m') || 'CN';
 var d = document.getElementById('sec-list');
 if(d && window.fetch){
  fetch('/api/chanlun/buy/sectors?m='+encodeURIComponent(m))
   .then(function(r){return r.json()})
   .then(function(j){
     var st=document.getElementById('sec-st');
     if(!j || !j.ok || !(j.items||[]).length){
       st.textContent='无板块数据';
       d.innerHTML='<div class="dim" style="font-size:12.5px">'
         + '板块映射暂无覆盖（成分表未含今日买点个股），不影响名单使用。</div>';
       return;
     }
     st.textContent = '申万二级 · ' + j.mapped + '/' + j.total + ' 只已归并';
     var mx=j.items[0].n||1, h='';
     for(var i=0;i<j.items.length;i++){
       var it=j.items[i], w=Math.max(6, it.n/mx*100);
       var bs = (it.bs===null||it.bs===undefined) ? '仅买' : it.bs.toFixed(2);
       var bsCls = (it.bs===null||it.bs===undefined) ? 'up' : (it.bs>=1.5?'up':(it.bs>=1?'gd':'dn'));
       h += '<div class="sec-row">'
          + '<div class="nm" title="'+it.l1+' › '+it.sector+'　'+it.codes.join(' ')+'">'
          + it.sector + ' <span class="dim" style="font-size:10.5px">· '+it.l1+'</span></div>'
          + '<div class="track" title="今日买点 '+it.n+' 只"><i style="width:'+w+'%"></i>'
          + '<b>'+it.n+' 只</b></div>'
          + '<div class="'+((it.sell||0)>0?'dn':'dim')+'" title="同行业当日卖点数">卖 '+(it.sell||0)+'</div>'
          + '<div class="'+bsCls+'" title="买点÷卖点，越高越是真转多">比 '+bs+'</div>'
          + '<div class="dim" title="'+it.codes.join(' ')+'">'+it.codes.length+' 码</div>'
          + '</div>';
     }
     d.innerHTML=h;
   })
   .catch(function(){
     document.getElementById('sec-st').textContent='加载失败';
   });
 }
})();
</script>
</body></html>""")
        html = "".join(body)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _chanlun_buy_sectors(self, qs=""):
        """板块聚集：当日买点按申万二级行业归并 + 行业多空比（1 小时缓存）。

        「多空比」= 该行业当日买点数 / 卖点数，来自同一份池子（同口径、同一天），
        不看外部板块指数——外部指数要么没成分表，要么数据停在 10 月。
        """
        mkt = _mkt_of(qs)
        sfx = _SFX[mkt]
        detect, buys, _hist = _load_pool(sfx)
        key = (mkt, detect or "")
        now = time.time()
        if _SEC_CACHE["key"] == key and now - _SEC_CACHE["ts"] < 3600:
            return self._json(_SEC_CACHE["val"])

        mp = _sw_map()
        # 当日买/卖按行业计数（第二遍只取买点，这里补一遍卖点，同一天同口径）
        sell = {}
        if detect:
            try:
                with open(os.path.join(QBT_DIR, "data",
                                       "chanlun_pool%s.jsonl" % sfx),
                          encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if (r.get("detect_date") != detect
                                or r.get("type") not in ("1卖", "2卖", "3卖")):
                            continue
                        c6 = "".join(ch for ch in str(r.get("code") or "")
                                     if ch.isdigit())[:6]
                        ind = mp.get(c6)
                        if ind:
                            sell[ind[1]] = sell.get(ind[1], 0) + 1
            except OSError:
                pass

        agg, mapped = {}, 0
        for r in buys:
            c6 = "".join(ch for ch in str(r.get("code") or "")
                         if ch.isdigit())[:6]
            ind = mp.get(c6)
            if not ind:
                continue
            mapped += 1
            a = agg.setdefault(ind[1], {"sector": ind[1], "l1": ind[0],
                                        "n": 0, "sell": sell.get(ind[1], 0),
                                        "codes": []})
            a["n"] += 1
            a["codes"].append(str(r.get("code")))
        items = sorted(agg.values(), key=lambda x: (-x["n"], x["sell"]))[:12]
        for it in items:
            b, s = it["n"], it["sell"]
            it["bs"] = round(b / s, 2) if s else None
        out = {"ok": True, "detect_date": detect, "total": len(buys),
               "mapped": mapped, "items": items,
               "stale_sell": not bool(sell)}
        _SEC_CACHE.update({"key": key, "ts": now, "val": out})
        return self._json(out)
