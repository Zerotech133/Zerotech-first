# -*- coding: utf-8 -*-
"""粗信号可执行性检验块（自 api_chanlun_buy 拆出，保持页面文本不变）。"""
import json
import os

_CBT_CACHE = {"mt": None, "html": ""}


def coarse_bt_block(esc, cls, num, log):
    """①d 可交易性检验块（读 portal/coarse_signal_evidence.json）。

    存在理由：①c 已经证明现有买点「按可执行口径没有 edge」，但没回答
    「那到底还有没有能当天判的买点」。这一块把信号拆成两层——
    细信号（线段/中枢/背驰确认）按定义要等后续 K 线，粗信号（分型 + 摆动腿
    力度 + 量能）收盘即可判——分别回测，并诚实披露「笔均好看 ≠ 账户赚钱」
    的扎堆效应。结论若不足以支撑实盘，就写不足以支撑实盘。
    """
    global _CBT_CACHE
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "coarse_signal_evidence.json")
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return ""
    if _CBT_CACHE["mt"] == mt and _CBT_CACHE["html"]:
        return _CBT_CACHE["html"]
    try:
        with open(path, encoding="utf-8") as f:
            bt = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("silent-except cbt: %s", exc)
        return ""

    lv = {x.get("lvl"): x for x in (bt.get("levels") or [])}
    cr = {x.get("lvl"): x for x in (bt.get("crowd") or [])}
    gt = {(x.get("lvl"), x.get("th")): x for x in (bt.get("gate") or [])}
    pf = {x.get("level"): x for x in (bt.get("portfolio") or [])}
    bench = bt.get("bench") or {}
    kz = bt.get("k_zero") or {}
    bands = {x.get("band"): x for x in (bt.get("lag_bands") or [])}
    corr = bt.get("lag_corr") or {}
    hold = bt.get("hold_days") or 20

    l0, l1, l1n = lv.get("L0") or {}, lv.get("L1") or {}, lv.get("L1n") or {}
    c1 = cr.get("L1") or {}
    g50 = gt.get(("L1", 50)) or {}
    p50 = pf.get("L1 ≥50") or {}
    b_lo, b_hi = bands.get("0-5") or {}, bands.get("31-60") or {}

    def av(d, key, sub="avg"):
        return ((d.get(key) or {}) or {}).get(sub)

    level_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="%s">%s</td><td class="dim">%s</td></tr>'
        % (esc(x.get("name")), av(x, "ret", "n") or 0,
           cls(av(x, "ret")), num(av(x, "ret")),
           cls(av(x, "alpha_xs")), num(av(x, "alpha_xs")),
           cls((av(x, "ret", "win") or 0) - 50)
           if av(x, "ret", "win") is not None else "dim",
           num(av(x, "ret", "win"), sign=False, nd=1),
           (("%.2f" % x["t_alpha"]["t"]) if x.get("t_alpha") else "—"))
        for x in (bt.get("levels") or []))

    gate_rows = "".join(
        '<tr><td>%s ≥%d</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="dim">%d 段 / t=%s</td></tr>'
        % (esc(x.get("lvl")), x.get("th") or 0, x.get("n_day") or 0,
           cls(x.get("day_mean")), num(x.get("day_mean")),
           cls(x.get("alpha_xs")), num(x.get("alpha_xs")),
           x.get("n_indep") or 0,
           ("%.2f" % x["indep_t"]) if x.get("indep_t") is not None else "—")
        for x in (bt.get("gate") or []))

    port_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="%s">%s</td>'
        '<td class="dim">%s</td><td class="dim">%s</td></tr>'
        % (esc(x.get("level")), x.get("n_entries") or 0,
           cls(x.get("total")), num(x.get("total")),
           cls(x.get("ann")), num(x.get("ann")),
           num(x.get("mdd")), ("%.2f" % x["sharpe"])
           if x.get("sharpe") is not None else "—")
        for x in (bt.get("portfolio") or []))

    mon_rows = "".join(
        '<tr><td>%s</td><td class="dim">%d</td>'
        '<td class="%s">%s</td><td class="dim">%s</td></tr>'
        % (esc(m.get("mon")), m.get("n") or 0,
           cls(m.get("alpha")), num(m.get("alpha")),
           num(m.get("win"), sign=False, nd=1))
        for m in (bt.get("monthly_L1n") or []))

    html = (
        '<div class="sec"><h2>①d 可交易性检验 · 粗信号 vs 细信号 '
        '<span class="tag g">%s 条</span>'
        '<span class="tag">%s · 持 %d 日</span></h2>'
        % (bt.get("n_sig_coarse"), esc("~".join(bt.get("window") or [])), hold)
        + '<div class="ebt">'
        + '<div class="ebt-line"><b>细信号当天算不出来</b> '
          '1买/2买/3买 都是「线段终结 / 中枢离开 / 回踩确认」的追认，'
          '把 K 线截断在信号日当天重放，<em>k=0 只有 %s%%</em>（%d 笔里 %d 笔），'
          '中位要等 22 个交易日 —— 按现有引擎的输出，收盘后无法下单</div>'
        % (kz.get("pct", 0), kz.get("n", 0), kz.get("zero", 0))
        + '<div class="ebt-line"><b>「等太晚」也不是唯一病根</b> '
          '按确认滞后分档：k≤5 的 <span class="%s">%s</span>、'
          'k 31~60 的 %s，秩相关 rho=%s —— 越早拿到信号反而更差，'
          '<em>说明问题在信号本身，不在快慢</em></div>'
        % (cls(av(b_lo, "alpha_xs")), num(av(b_lo, "alpha_xs")),
           num(av(b_hi, "alpha_xs")), ("%+.3f" % corr["rho"])
           if corr.get("rho") is not None else "—")
        + '<div class="ebt-line"><b>粗信号（收盘即可判、次日开盘买）</b> '
          'L0 裸底分型 %d 笔：单笔 %s、减同日全市场等权后 %s'
          '（日级 t=%.1f）；加「下跌力度收窄」L1 %s、加「创新低」L1n %s —— '
          '<em>条件越多 alpha 没变高，L0 反而最稳</em></div>'
        % (av(l0, "ret", "n") or 0, num(av(l0, "ret")),
           num(av(l0, "alpha_xs")),
           (l0.get("t_alpha") or {}).get("t") or 0,
           num(av(l1, "alpha_xs")), num(av(l1n, "alpha_xs")))
        + '<div class="ebt-line"><b>但笔均 ≠ 账户</b> '
          '同一天冒出几十个底分型，说明是大盘在跌。按日等权（组合真实口径）'
          'L1 只有 <span class="%s">%s</span>，拆开看：稀疏日 %s、'
          '扎堆日 %s，信号数与当日组收益 rho=+%.2f —— '
          '<em>钱是在「扎堆日」赚的，不是在「任意一天」赚的</em></div>'
        % (cls(c1.get("day_mean")), num(c1.get("day_mean")),
           num(c1.get("sparse_ret")), num(c1.get("crowd_ret")),
           c1.get("rho_n_ret") or 0)
        + '<div class="ebt-line"><b>加一道可执行的扎堆门槛</b> '
          '「当日信号数 ≥50 才建仓」：L1 建仓 %d 次，组均 %s、'
          '累计 <span class="%s">%s</span>（年化 %s，回撤 %s，夏普 %s）；'
          '同期全市场等权 %s、上证 %s</div>'
        % (p50.get("n_entries") or 0, num(g50.get("day_mean")),
           cls(p50.get("total")), num(p50.get("total")),
           num(p50.get("ann")), num(p50.get("mdd")),
           ("%.2f" % p50["sharpe"]) if p50.get("sharpe") is not None else "—",
           num(bench.get("xs_roll_pct")), num(bench.get("index_window_pct")))
        + '<div class="ebt-line"><b>结论：方向对，样本不够，不上实盘</b> '
          '门槛后日级 t=%.2f，但 20 日持有互相重叠，非重叠只剩 %d 段、'
          't=%.2f —— <em>不足以下实盘指令</em>。'
          '处置：粗信号进<b>影子清单</b>（每日记录、纸面跟踪），'
          '细信号继续只当结构提示；3买 维持停用</div>'
        % ((g50.get("t") or 0), (g50.get("n_indep") or 0),
           (g50.get("indep_t") or 0))
        + '<details class="ebt-d"><summary>展开完整证据：五档 / 门槛扫描 / '
          '组合净值 / 月度 / 板块</summary>'
        + '<div class="ebt-sub">粗信号五档（单笔口径）</div>'
        + ('<table class="ebt-t"><thead><tr><th>档位</th><th>笔数</th>'
           '<th>净收益</th><th>减等权</th><th>胜率</th><th>日级 t</th>'
           '</tr></thead><tbody>%s</tbody></table>' % level_rows)
        + '<div class="ebt-sub">扎堆门槛扫描（当日信号数 ≥ N 才建仓）</div>'
        + ('<table class="ebt-t"><thead><tr><th>档位</th><th>日数</th>'
           '<th>日组均</th><th>减等权</th><th>非重叠检验</th>'
           '</tr></thead><tbody>%s</tbody></table>' % gate_rows)
        + '<div class="ebt-sub">组合级 20 槽轮动</div>'
        + ('<table class="ebt-t"><thead><tr><th>档位</th><th>建仓</th>'
           '<th>累计</th><th>年化</th><th>回撤</th><th>夏普</th>'
           '</tr></thead><tbody>%s</tbody></table>' % port_rows)
        + '<div class="ebt-sub">L1n 月度超额</div>'
        + ('<table class="ebt-t"><thead><tr><th>月份</th><th>笔数</th>'
           '<th>减等权</th><th>胜率</th></tr></thead><tbody>%s</tbody></table>'
           % mon_rows)
        + '</details>'
        + '<div class="legend">'
          '<b>粗信号定义</b>：严格底分型（中间 K 线 low 最低且 high 最低，'
          '与前一根、后一根都成立）→ 次日收盘确认 → 再次日开盘买入。'
          'L1 加「本段跌幅 &lt; 上一段跌幅」（力度收窄）；L1n 再加「低点创新低」'
          '（真底背驰）；L2 再加「本段均量 &lt; 上一段」；L3 再加「上证 &gt; MA20」'
          '（实测这条最差，已弃）。<br>'
          '<b>为什么拒掉 L3</b>：大盘向上才买 = 趋势跟随，而粗信号的收益来自'
          '「跌完之后接反弹」，两者相冲，加了门槛组均从 +1.73%% 掉到 +1.02%%。<br>'
          '<b>口径</b>：减同日全市场等权 = 个股净收益 − 当日全市场等权买入持有 '
          '%d 日的均值（把 size/beta 全剥掉，剩下的才是选股能力）；'
          '成本买 0.075%% + 卖 0.125%%；北交所单独统计为负，组合一律剔除。</div>'
        % hold
        + '</div>')
    _CBT_CACHE.update({"mt": mt, "html": html})
    return html
