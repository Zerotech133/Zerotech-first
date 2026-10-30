# -*- coding: utf-8 -*-
"""量化回测 CLI。

单股回测（CN=600519.SH / HK=01810 / US=NVDA）:
  python runner.py --strategy ma_cross --symbol 600519.SH --market CN \
      --start 2025-06-01 --end 2026-09-25 [--push]

自选股扫描（对 watchlist 全部标的按市场分组加载，按夏普排名）:
  python runner.py --strategy ma_cross --scan [--market ALL|CN|HK|US|CRYPTO] [--push]
  watchlist 代码映射: sh/sz→CN(.SH/.SZ)、rt_hk→HK(5位)、gb_→US(裸ticker)、
  纯符号→先按 US 查、无数据自动按 CRYPTO(symbol/USDT) 查。

结果落盘: backtest_results.jsonl（全量）+ last_backtest.json（最新，供门户）。
"""

import json
import math
import os
import subprocess
import sys
from urllib.parse import quote
from datetime import datetime, timedelta

import pandas as pd

import backtest
import report_page
import strategies as strat
from data_loader import load_bars

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-10-25 SEC 收口（EXEC-R16）：config.json 的 report 文档 URL 走环境变量/本地私有覆盖，
# 见 config_local.py；禁止把真值写回 tracked config。
from config_local import load_config as _load_quant_config  # noqa: E402
import logging

CFG = _load_quant_config()
RESULTS_JSONL = os.path.join(HERE, "backtest_results.jsonl")
LAST_JSON = os.path.join(HERE, "last_backtest.json")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


LAST_PUSH_FAIL = ""  # 最近一次 push_feishu 失败原因（SEND_FAIL 行/stderr 摘要，
# 供 guard_alert 告警日志取证；成功发送即清空）


def push_feishu(text, card=False):
    """text 模式传文本；card 模式传卡片 JSON 字符串。"""
    global LAST_PUSH_FAIL
    tmp = os.path.join(
        NEWS_DIR, "outbox", "pending_bt_card.json" if card else "pending_bt.txt"
    )
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    args = [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp]
    if card:
        args.append("interactive")
    r = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60
    )
    out = (r.stdout or "").strip()
    if out.startswith("SEND_OK"):
        LAST_PUSH_FAIL = ""
        return True
    # A4 20261019：失败原因不再丢弃——SEND_FAIL 原因行/stderr 摘要落
    # 模块变量（返回值仍为 bool，调用方零改动）。
    LAST_PUSH_FAIL = (out or (r.stderr or "").strip() or "send_feishu no output")[:200]
    return False


def _trades_shown():
    return int(CFG.get("report", {}).get("trades_shown", 8))


def _c_pct(v):
    """着色百分比（飞书卡片 markdown font 标签；统一境内习惯红涨绿跌——
    正/盈利=红，负/亏损=绿）。"""
    return "<font color='%s'>%+.1f%%</font>" % ("red" if v >= 0 else "green", v * 100)


def fmt_trades(m, color=False):
    tl = m.get("trade_list") or []
    if not tl:
        return "（无成交）"
    lines = []
    for t in tl[-_trades_shown() :][::-1]:
        pnl = _c_pct(t["pnl"]) if color else "%+.1f%%" % (t["pnl"] * 100)
        tail = (
            "<font color='grey'>（未平仓）</font>"
            if color and t.get("open")
            else "（未平仓）"
            if t.get("open")
            else ""
        )
        lines.append(
            "%s 买 %.2f → %s 卖 %.2f   %s%s"
            % (t["in"], t["in_px"], t["out"], t["out_px"], pnl, tail)
        )
    return "\n".join(lines)


def fmt_status(m, df, sig):
    """最新现价（含当日涨跌）/ 当前持仓 / 最近信号切换。"""
    px = float(df["close"].iloc[-1])
    d_last = df.index[-1].strftime("%m-%d")
    day_chg = (
        (float(df["close"].iloc[-1]) / float(df["close"].iloc[-2]) - 1.0)
        if len(df) > 1
        else 0.0
    )
    holding = "持有多头" if m["position"].iloc[-1] > 0 else "空仓观望"
    flips = sig[sig.diff() != 0].index
    flip_s = flips[-1].strftime("%m-%d") if len(flips) else "-"
    return "现价 %.2f（%s · 日 %+.1f%%）· 持仓 %s · 最近信号切换 %s" % (
        px,
        d_last,
        day_chg * 100,
        holding,
        flip_s,
    )


def cost_note(market=None):
    """成本口径标注行：让下游看到回测用的成本/T+1 假设（口径变化可见）。"""
    tc = backtest.load_trade_config(market or "CN")
    t1 = "T+1" if tc["t_plus_one"] else "T+0"
    return "成本假设 %s=%dbps（成本%d+滑点%d）· %s" % (
        market or "CN",
        tc["cost_bps"] + tc["slippage_bps"],
        tc["cost_bps"],
        tc["slippage_bps"],
        t1,
    )


def compare_strategies(df, mark_name=None, color=False, market=None):
    """同一标的同一区间跑全部策略，返回对比行列表。
    market：标的所属市场（CN/US/HK/CRYPTO），成本按市场分层取；
    拿不到市场时按 CN 保守口径。"""
    tc = backtest.load_trade_config(market or "CN")
    rows = []
    for sname in strat.REGISTRY:
        p = dict(CFG["strategies"].get(sname, {}))
        try:
            s = strat.REGISTRY[sname](df, p)
            mm = backtest.run(df, s, **tc)
        except Exception:
            continue
        ret = (
            _c_pct(mm["total_return"])
            if color
            else "%+.1f%%" % (mm["total_return"] * 100)
        )
        rows.append(
            "%s%s：%s · 夏普 %.2f · 回撤 %.1f%% · %d 笔"
            % (
                sname,
                "（本报告）" if sname == mark_name else "",
                ret,
                mm["sharpe"],
                mm["max_drawdown"] * 100,
                mm["trades"],
            )
        )
    return rows


def report_markdown(name, strat_name, params, m, bh, window, df, sig, market=None):
    """单股报告 → 卡片 markdown 块列表（每块一个元素，块间自动 hr）。"""
    emoji = "📈" if m["total_return"] >= 0 else "📉"
    excess = m["total_return"] - bh["total_return"]
    md = [
        "%s **%s** · 策略 %s\n区间 %s · 参数 %s"
        % (emoji, name, strat_name, window, json.dumps(params, ensure_ascii=False)),
        "**最新状态**\n" + fmt_status(m, df, sig),
        "**绩效**：收益 %s · 年化 %+.1f%% · 波动 %.1f%% · 夏普 %.2f · 索提诺 %.2f · 卡玛 %.2f\n"
        "回撤 %.1f%% · 胜率 %.0f%% · %d 笔 · 连亏 %d"
        % (
            _c_pct(m["total_return"]),
            m["cagr"] * 100,
            m["annual_vol"] * 100,
            m["sharpe"],
            m["sortino"],
            m["calmar"],
            m["max_drawdown"] * 100,
            m["win_rate"] * 100,
            m["trades"],
            m["max_loss_streak"],
        ),
        "**基准（买入持有）**：%+.1f%% · 夏普 %.2f · 回撤 %.1f%%  →  超额 %s"
        % (
            bh["total_return"] * 100,
            bh["sharpe"],
            bh["max_drawdown"] * 100,
            _c_pct(excess),
        ),
        "**四策略同期对比**\n"
        + "\n".join(compare_strategies(df, strat_name, color=True, market=market)),
        "**近 %d 笔交易**\n" % _trades_shown() + fmt_trades(m, color=True),
        "<font color='grey'>" + cost_note(market) + "</font>",
    ]
    return md


def _metric_col(label, value, bg):
    """关键指标列（复用 backup_alert 已验证的 Card 2.0 模式）。"""
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "background_style": bg,
        "padding": "12px",
        "vertical_spacing": "2px",
        "elements": [
            {
                "tag": "markdown",
                "text_size": "notation",
                "content": "<font color='grey'>" + label + "</font>",
            },
            {"tag": "markdown", "text_size": "heading-3", "content": value},
        ],
    }


def key_columns(m):
    """卡片顶部三列关键指标：收益按正负绿/红底，夏普浅蓝，回撤橙。"""
    return [
        (
            "总收益",
            _c_pct(m["total_return"]),
            "green-50" if m["total_return"] >= 0 else "red-50",
        ),
        ("夏普", "%.2f" % m["sharpe"], "wathet-50"),
        ("最大回撤", "%.1f%%" % (m["max_drawdown"] * 100), "orange-50"),
    ]


def _url_buttons(buttons):
    """URL 按钮组：buttons = [(label, url, type), ...]。

    v1 标准结构（tag=action + url）：兼容模式卡片下手机端可点。
    坑: 兼容模式卡片混用 2.0 的 column_set+behaviors 按钮时，
        桌面端容错渲染、手机端点击行为失效。"""
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if "primary" in btype else btype,
                "url": url,
            }
            for label, url, btype in buttons
        ],
    }


def _qr_element(qr_key, caption):
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {
                        "tag": "img",
                        "img_key": qr_key,
                        "alt": {"tag": "plain_text", "content": caption},
                        "custom_width": 200,
                        "custom_height": 200,
                    },
                    {
                        "tag": "markdown",
                        "text_size": "notation",
                        "content": "<font color='grey'>" + caption + "</font>",
                    },
                ],
            }
        ],
    }


def build_card(
    title,
    subtitle,
    template,
    md_blocks,
    footer=None,
    columns=None,
    buttons=None,
    qr_key=None,
    qr_caption="扫码打开门户控制台",
    use_schema2=False,
):
    # K43 use_schema2：真 Card 2.0（schema+body 包装）——手机端渲染需要
    # （混血卡手机空白判例）；2.0 下 v1 action 按钮被 400 拒、img 被 200621 拒，
    # 故 v2 模式提前把 buttons 降级为 md 超链接块、忽略 qr_key
    # （必须在 els 构造前拦截，事后置 None 不影响已入列的元素）。
    if use_schema2:
        qr_key = None
        if buttons:
            md_blocks = list(md_blocks) + [
                " · ".join("[%s](%s)" % (l, u) for l, u, _ in buttons)
            ]
            buttons = None
    els = []
    if columns:
        els.append(
            {
                "tag": "column_set",
                "flex_mode": "none",
                "horizontal_spacing": "8px",
                "columns": [_metric_col(l, v, bg) for l, v, bg in columns],
            }
        )
    for i, md in enumerate(md_blocks):
        if i or columns:
            els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": md})
    if buttons:
        els.append({"tag": "hr"})
        els.append(_url_buttons(buttons))
    if qr_key:
        els.append({"tag": "hr"})
        els.append(_qr_element(qr_key, qr_caption))
    if footer:
        els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": footer})
    elif qr_key:
        # column_set 不能作为末元素（200621），无 footer 时补尾部小字
        els.append(
            {
                "tag": "markdown",
                "text_size": "notation",
                "content": "<font color='grey'>长按识别二维码 · 手机直达</font>",
            }
        )
    # v1 标准结构：elements 必须在顶层。
    # 坑: 用 2.0 的 body 包装但不带 schema 字段时，服务端容错渲染内容、
    #     但按钮点击行为失效（手机/桌面均不可点）——实测定位。
    # K43 use_schema2：真 Card 2.0（schema+body 包装）——手机端渲染需要
    # （混血卡手机空白判例）；2.0 下 v1 action 按钮被 400 拒、img 被 200621 拒，
    # 故 v2 模式已在 els 构造前完成 buttons 降级与 qr_key 忽略。
    card = {
        # 坑: "schema": "2.0" + img 组件会被飞书拒（200621），去掉走兼容模式
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": template,
        },
        "elements": els,
    }
    if use_schema2:
        card["schema"] = "2.0"
        card["body"] = {"elements": els}
        del card["elements"]
    return card


def _card_actions(report_file=None, news_name=None):
    """K42-c 判例：卡内只放外网可达链接（飞书文档）；
    门户内网按钮/二维码手机不可达，一律不生成。"""
    doc = CFG.get("report", {}).get("doc_url")
    if not doc:
        return None, None
    return [("📄 回测文档", doc, "primary_filled")], None


def optimize_run(df, strat_name, cap=10, market=None):
    """网格参数寻优：网格在 config opt_grids；按夏普降序返回前 cap 行。
    样本内寻优，仅供参数敏感性参考。成本按 market 分层（拿不到按 CN）。"""
    tc = backtest.load_trade_config(market or "CN")
    grids = CFG.get("opt_grids", {}).get(strat_name)
    if not grids:
        return []
    combos = [{}]
    for k, vals in grids.items():
        combos = [dict(c, **{k: v}) for c in combos for v in vals]
    rows = []
    for c in combos:
        try:
            s = strat.REGISTRY[strat_name](df, c)
            mm = backtest.run(df, s, **tc)
            rows.append(
                {
                    "params": c,
                    "ret": mm["total_return"],
                    "sharpe": mm["sharpe"],
                    "dd": mm["max_drawdown"],
                    "trades": mm["trades"],
                }
            )
        except Exception:
            continue
    rows.sort(key=lambda x: x["sharpe"], reverse=True)
    return rows[:cap]


def write_opt_page(frames=None):
    """全池寻优榜单页（/bt-opt）：4 策略各 Top5，当前参数高亮。"""
    import opt_defaults as od

    if frames is None:
        look = int(CFG.get("daily_scan", {}).get("lookback_days", 365))
        start = (datetime.now() - timedelta(days=look)).strftime("%Y-%m-%d")
        frames = od.pool_frames(start, None)
    data = []
    for name in report_page.SIG_STRATS:
        rows = od.tune(name, frames, CFG["trade"])[:5]
        if rows:
            data.append(
                {
                    "name": name,
                    "rows": rows,
                    "cur": _effective_params(name, CFG["strategies"].get(name, {})),
                }
            )
    report_page.write_opt_html(data, _now())
    print("OPT_PAGE %d strategies %d stocks" % (len(data), len(frames)))


def _collect_signals():
    """全池 51 只 × 4 策略最新信号（信号页与持仓账本共用数据源）。"""
    wl = json.load(
        open(os.path.join(NEWS_DIR, "news_config.json"), encoding="utf-8")
    ).get("watchlist", [])
    entries = resolve_watchlist(wl)
    by_mkt = {}
    for mkt, sym, nm in entries:
        by_mkt.setdefault(mkt, []).append((sym, nm))
    start = (datetime.now() - timedelta(days=150)).strftime("%Y-%m-%d")
    rows = []
    for mkt, pairs in by_mkt.items():
        got = load_bars(mkt, [s for s, _ in pairs], start, None)
        for sym, nm in pairs:
            df = got.get(sym)
            if df is None or len(df) < 35:
                continue
            sigs, since, since_full = {}, {}, {}
            for sn in report_page.SIG_STRATS:
                try:
                    sig = strat.REGISTRY[sn](df, dict(CFG["strategies"].get(sn, {})))
                except Exception:
                    sigs[sn], since[sn], since_full[sn] = 0, "", ""
                    continue
                arr = sig.values
                i = len(arr) - 1
                while i > 0 and arr[i - 1] == arr[i]:
                    i -= 1
                sigs[sn] = int(arr[-1])
                since[sn] = sig.index[i].strftime("%m-%d")
                since_full[sn] = sig.index[i].strftime("%Y-%m-%d")
            rows.append(
                {
                    "market": mkt,
                    "symbol": sym,
                    "name": nm,
                    "px": float(df["close"].iloc[-1]),
                    "open": float(df["open"].iloc[-1]),
                    "date": df.index[-1].strftime("%Y-%m-%d"),
                    "sigs": sigs,
                    "since": since,
                    "since_full": since_full,
                    "res": sum(sigs.values()),
                }
            )
    return rows


def write_signals_page():
    """全池 51 只 × 4 策略最新信号 → reports/signals.html（/bt-signals）。"""
    rows = _collect_signals()
    rows.sort(key=lambda r: (-r["res"], r["name"]))
    report_page.write_signals_html(rows, _now())
    print("SIGNALS %d stocks" % len(rows))
    return len(rows)


LEDGER_JSON = os.path.join(HERE, "positions.json")


def _load_ledger(path=None):
    try:
        with open(path or LEDGER_JSON, encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        led = {}
    for k in ("pending", "open", "closed"):
        led.setdefault(k, [])
    return led


def _d8(s):
    return datetime.strptime(s, "%Y-%m-%d")


def max_holding_days(cfg=None):
    """最长持有期（交易日，[20261014_p1_tail]）：config backtest 节
    max_holding_days，缺省 60。open 持有超过 N 个交易日强制平仓入 closed
    并标注 forced_close（区别于信号翻空的正常平仓）。"""
    return int((cfg or CFG).get("backtest", {}).get("max_holding_days", 60))


def _sig_key(e):
    """入账幂等键（[20261014_p1_tail]）：code+signal_date+strategy。
    判例：审计发现同信号重复入账风险——同键已入账（含历史已平仓）即跳过。"""
    return (e.get("market"), e.get("symbol"), e.get("sig_date"), e.get("strategy"))


def update_ledger(refresh_signals=True, ledger_path=None):
    """盘后账本更新（16:00 顺带）：pending 待买 → 按当日开盘建仓；
    持仓信号翻空 → 收盘平仓；当日新翻多 → 进 pending（明晨盘前卡）。
    refresh_signals=True 时顺带刷新 /bt-signals 页（共用本次数据）。
    [20261014_p1_tail] 新增：
      a) 入账幂等：键=code+signal_date+strategy 全节（pending/open/closed）
         查重，重复跳过并计数落日志（dedup_skip）；
      b) max_holding_days：open 超过 N 个交易日强制平仓入 closed 并标注
         forced_close（持有交易日数用 bars_held 增量计数，每日新 bar +1，
         与 marks 打点同语义；旧档无 bars_held 用 marks 长度回填，再无按
         自然日回填——保守偏高，宁可早平不强拿）；
      c) ledger_path 参数化：默认 positions.json，单测传 tmp 不碰生产数据。
    """
    led_path = ledger_path or LEDGER_JSON
    max_hold = max_holding_days()
    rows = _collect_signals()
    if refresh_signals:
        report_page.write_signals_html(
            sorted(rows, key=lambda r: (-r["res"], r["name"])), _now()
        )
    info = {(r["market"], r["symbol"]): r for r in rows}
    led = _load_ledger(led_path)

    # 1) pending → open（出现新 bar 才按开盘价成交，否则继续等）
    still, opened = [], []
    for e in led["pending"]:
        r = info.get((e["market"], e["symbol"]))
        if r and r["date"] > e["sig_date"]:
            e.update(
                {
                    "entry_date": r["date"],
                    "entry_px": r["open"] or r["px"],
                    "last_px": r["px"],
                    "last_date": r["date"],
                    "bars_held": 1,
                }
            )  # 建仓日即第 1 个持有交易日
            opened.append(e)
        else:
            e["last_px"] = r["px"] if r else e.get("last_px")
            still.append(e)

    # 2) open：翻空平仓（收盘价）；超 max_holding_days 强平（标注
    #    forced_close）；否则刷新最新价
    keep, closed, forced = [], [], []
    for e in led["open"]:
        r = info.get((e["market"], e["symbol"]))
        if not r:  # 已移出自选：保留不动
            keep.append(e)
            continue
        # 持有交易日计数：每日新 bar 才 +1（ffill 打点同语义，重复跑同日不胀）
        if "bars_held" not in e:
            seed = len(e.get("marks") or [])
            if not seed and e.get("entry_date"):
                seed = max(
                    (_d8(e.get("last_date") or r["date"]) - _d8(e["entry_date"])).days,
                    1,
                )
            e["bars_held"] = max(seed, 1)
        elif e.get("last_date") != r["date"]:
            e["bars_held"] += 1
        e["last_px"], e["last_date"] = r["px"], r["date"]
        if r["sigs"].get(e["strategy"]) == 0:
            e["exit_date"], e["exit_px"] = r["date"], r["px"]
            e["days"] = max((_d8(r["date"]) - _d8(e["entry_date"])).days, 0)
            e["pnl"] = (r["px"] - e["entry_px"]) / e["entry_px"]
            e.pop("forced_close", None)  # 正常平仓不留强平标注
            closed.append(e)
        elif int(e["bars_held"]) >= max_hold:
            e["exit_date"], e["exit_px"] = r["date"], r["px"]
            e["days"] = max((_d8(r["date"]) - _d8(e["entry_date"])).days, 0)
            e["pnl"] = (r["px"] - e["entry_px"]) / e["entry_px"]
            e["forced_close"] = True  # 超期强平标注（审计可见）
            forced.append(e)
            closed.append(e)
        else:
            keep.append(e)

    # 3) 当日新翻多 → pending（次日开盘应买入）
    # 既有持仓语义：同 code+strategy 在册（pending/open/closed 本轮）不加；
    # 幂等键语义：同 code+signal_date+strategy 已入过账（含历史已平仓）
    # 不重复入账，跳过计数（判例：同信号重复入账风险）
    have_pos = {
        (e["market"], e["symbol"], e["strategy"])
        for e in still + opened + keep + closed
    }
    have_sig, dup_skip = set(), 0
    for sec in ("pending", "open", "closed"):
        for e in led[sec]:
            k = _sig_key(e)
            if k in have_sig:
                dup_skip += 1  # 账内既有重复：只计数不再入账
            else:
                have_sig.add(k)
    for r in rows:
        for sn in report_page.SIG_STRATS:
            if (
                r["sigs"].get(sn) == 1
                and r["since_full"].get(sn) == r["date"]
                and (r["market"], r["symbol"], sn) not in have_pos
            ):
                k = (r["market"], r["symbol"], r["date"], sn)
                if k in have_sig:
                    dup_skip += 1
                    continue
                still.append(
                    {
                        "market": r["market"],
                        "symbol": r["symbol"],
                        "name": r["name"],
                        "strategy": sn,
                        "sig_date": r["date"],
                        "last_px": r["px"],
                    }
                )
                have_pos.add((r["market"], r["symbol"], sn))
                have_sig.add(k)

    led["pending"] = still
    led["open"] = keep + opened
    led["closed"] = (closed + led["closed"])[:500]
    for e in led["open"]:
        e["days"] = max((_d8(e["last_date"]) - _d8(e["entry_date"])).days, 0)
        e["pnl"] = (e["last_px"] - e["entry_px"]) / e["entry_px"]
        # 每日收盘浮盈留痕（近 60 日轨迹，供持仓页复盘）
        marks = e.setdefault("marks", [])
        if not marks or marks[-1].get("d") != e["last_date"]:
            marks.append(
                {"d": e["last_date"], "px": e["last_px"], "pnl": round(e["pnl"], 5)}
            )
            e["marks"] = marks[-60:]
    led["ts"] = _now()
    _atomic(led_path, led)
    report_page.write_positions_html(led, _now())
    try:  # 详情看板镜像（失败不影响主流程）
        import doc_board

        doc_board.refresh()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)
    if dup_skip:
        print("LEDGER dedup_skip=%d（同信号重复入账已跳过）" % dup_skip)
    if forced:
        print(
            "LEDGER forced_close=%d（超 max_holding_days=%d 强平）"
            % (len(forced), max_hold)
        )
    print(
        "LEDGER pending=%d open=%d closed_today=%d total_closed=%d"
        % (len(still), len(led["open"]), len(closed), len(led["closed"]))
    )
    return led


def _today_open():
    """K98：今日是否 A 股交易日（作战卡服务于当日开盘，K97 观察项②）。fail-open。"""
    try:
        import glob

        files = sorted(
            glob.glob(os.path.join(HERE, "data", "tushare", "trade_cal", "*.parquet"))
        )
        cal = pd.concat([pd.read_parquet(f) for f in files]).drop_duplicates(
            subset=["cal_date"]
        )
        today = datetime.now().strftime("%Y%m%d")
        r = cal[cal["cal_date"] == today]
        return True if r.empty else bool(int(r.iloc[0]["is_open"]) == 1)
    except Exception:
        return True


def morning_push():
    """盘前作战计划卡（08:15 计划任务）：今日应买入 + 持仓概览。"""
    if not _today_open():
        print("MORNING skip：今日非 A 股交易日（休市，不产卡不推送）")
        return
    led = _load_ledger()
    pend = led.get("pending") or []
    open_ = led.get("open") or []
    if not pend and not open_:
        print("MORNING skip: 无待买且无持仓")
        return
    blocks = []
    if pend:
        lines = ["**今日应买入（开盘执行）**"]
        for e in pend:
            lines.append(
                "· [%s] %s · %s · 昨收 %.4g"
                % (
                    e["market"],
                    e["name"],
                    report_page.OPT_HEAD.get(e["strategy"], e["strategy"]),
                    e.get("last_px") or 0,
                )
            )
        blocks.append("\n".join(lines))
    # W1-2：持仓/待买命中九维警报池 → 卡片加警示块（反指实证 K32/K33：
    # score≥75 前向收益显著为负，≥85 段 r20 -71.5%；命中=风险提醒非买入信号）
    # 开关 nine_dim_alert_card（config.json 顶层，默认开）；fail-open 不炸卡
    if CFG.get("nine_dim_alert_card", True):
        try:
            codes = {
                c
                for c in (
                    report_page.nine_dim_cn_code(e.get("market"), e.get("symbol"))
                    for e in pend + open_
                )
                if c
            }
            hits = report_page.nine_dim_alert_check(codes)
        except Exception:
            hits = {}
        if hits:
            by_code = {}
            for e in pend + open_:  # 同码待买优先展示（pending 在前）
                c = report_page.nine_dim_cn_code(e.get("market"), e.get("symbol"))
                if c in hits and c not in by_code:
                    by_code[c] = e
            lines = ["**⚠️ 警报池命中（九维反指）**"]
            for c, e in sorted(by_code.items()):
                dt, score, band = hits[c]
                lines.append(
                    "· [%s] %s · 命中 %s-%s-%s · 分数 %s（%s）"
                    % (
                        e["market"],
                        e["name"],
                        dt[:4],
                        dt[4:6],
                        dt[6:8],
                        score,
                        band or "alert",
                    )
                )
            lines.append("提示：高分=拥挤警报，非买入信号")
            blocks.append("\n".join(lines))
            print(
                "MORNING nine_dim_alert hits=%d: %s"
                % (len(hits), " ".join(sorted(by_code)))
            )
    if open_:
        lines = ["**持仓中（信号未翻空前继续持有）**"]
        for e in sorted(open_, key=lambda x: -x.get("pnl", 0)):
            lines.append(
                "· [%s] %s · %s · 买 %.4g → 现 %.4g  %s（%d天）"
                % (
                    e["market"],
                    e["name"],
                    report_page.OPT_HEAD.get(e["strategy"], e["strategy"]),
                    e["entry_px"],
                    e.get("last_px") or e["entry_px"],
                    _c_pct(e.get("pnl", 0)),
                    e.get("days", 0),
                )
            )
        blocks.append("\n".join(lines))
    n_c = len(led.get("closed") or [])
    wins = sum(1 for c in led["closed"] if c.get("pnl", 0) > 0)
    footer = "纸面模拟，不构成投资建议 · 累计平仓 %d 笔 · 胜率 %.0f%%" % (
        n_c,
        wins * 100.0 / n_c if n_c else 0,
    )
    doc_url = CFG.get("report", {}).get("doc_url")
    # K42-c 判例：内网按钮/二维码不进卡（手机不可达），仅留飞书文档链接
    buttons = [("📄 详情文档", doc_url, "default")] if doc_url else None
    qr = None
    card = build_card(
        "💼 盘前作战计划",
        "%s · 量化信号实战（纸面）" % _now(),
        "turquoise",
        blocks,
        footer=footer,
        use_schema2=True,
        columns=[
            ("待买入", str(len(pend)), "blue-50"),
            ("持仓中", str(len(open_)), "wathet-50"),
            ("平仓累计", str(n_c), "grey-50"),
        ],
        buttons=buttons,
        qr_key=qr,
    )
    if push_feishu(json.dumps(card, ensure_ascii=False), card=True):
        print("MORNING pushed buy=%d hold=%d" % (len(pend), len(open_)))
    else:
        txt = "\n".join(blocks) + "\n—— " + footer
        ok = push_feishu(txt)
        print("MORNING text_fallback=%s" % ok)
        if not ok:
            sys.exit(1)  # K98：卡片+文本双败必须非零退出（alert_fail 接力，防假绿）


def _effective_params(strat_name, params):
    """策略默认值 + config 覆盖 = 实际生效参数（寻优"当前"行匹配用）。"""
    eff = dict(strat.PARAM_DEFAULTS.get(strat_name, {}))
    eff.update(params or {})
    return eff


def _monthly(eq, beq):
    """月度收益 [(ym, 策略, 持有)]：月末净值环比；首月以区间首日为基。"""
    out = []
    try:
        try:
            me, mb = eq.resample("ME").last(), beq.resample("ME").last()
        except ValueError:
            me, mb = eq.resample("M").last(), beq.resample("M").last()
        ps = pb = None
        for ym in me.index:
            if ym not in mb.index:
                continue
            se, be = float(me.loc[ym]), float(mb.loc[ym])
            sb = ps if ps is not None else float(eq.iloc[0])
            bb = pb if pb is not None else float(beq.iloc[0])
            out.append((ym.strftime("%Y-%m"), se / sb - 1, be / bb - 1))
            ps, pb = se, be
    except Exception:
        return []
    return out


def _heat(eq, sig):
    """日收益热力：[(ym, [(日收益或None, 是否持仓)])]（T+1：当日仓位=昨日信号）。"""
    try:
        dr = eq.pct_change().fillna(0.0)
        held = sig.shift(1).fillna(0).astype(int)
        rows = {}
        for d, r in dr.items():
            rows.setdefault(d.strftime("%Y-%m"), [None] * 32)[d.day] = (
                float(r),
                int(held.loc[d]),
            )
        return [(ym, cells[1:]) for ym, cells in sorted(rows.items())]
    except Exception:
        return []


def _chart_png(tag, m, bh):
    """matplotlib 暗色双联图（净值+回撤）→ reports/charts/；失败返回 None。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
        plt.rcParams["axes.unicode_minus"] = False
        import re

        eq, beq = m["equity"], bh["equity"]
        dd = eq / eq.cummax() - 1
        bdd = beq / beq.cummax() - 1
        fig, (a1, a2) = plt.subplots(
            2, 1, figsize=(10, 6.4), dpi=130, sharex=True, facecolor="#0d1117"
        )
        for a in (a1, a2):
            a.set_facecolor("#0d1117")
            a.tick_params(colors="#8b949e", labelsize=8)
            for sp in a.spines.values():
                sp.set_color("#30363d")
            a.grid(color="#21262d", lw=0.6)
        up = m["total_return"] >= 0
        a1.plot(
            eq.index,
            eq.values,
            lw=1.4,
            color="#f85149" if up else "#3fb950",
            label="策略",
        )
        a1.plot(
            beq.index, beq.values, color="#8b949e", lw=1.0, ls="--", label="买入持有"
        )
        a1.axhline(1.0, color="#30363d", lw=0.8, ls=":")
        a1.legend(
            facecolor="#161b22", labelcolor="#c9d1d9", fontsize=8, edgecolor="#30363d"
        )
        a1.set_title("策略净值 vs 买入持有", color="#c9d1d9", fontsize=10)
        a2.fill_between(
            dd.index, dd.values * 100, 0, color="#f85149", alpha=0.28, label="策略"
        )
        a2.plot(bdd.index, bdd.values * 100, color="#8b949e", lw=0.9, label="买入持有")
        a2.set_title("回撤曲线 %（灰 = 买入持有）", color="#c9d1d9", fontsize=10)
        a2.legend(
            facecolor="#161b22", labelcolor="#c9d1d9", fontsize=8, edgecolor="#30363d"
        )
        d = os.path.join(HERE, "reports", "charts")
        os.makedirs(d, exist_ok=True)
        import uuid

        fn = "%s_%s_%s.png" % (
            datetime.now().strftime("%Y%m%d-%H%M%S"),
            uuid.uuid4().hex[:4],
            re.sub(r"[^0-9A-Za-z_.\-]+", "_", tag)[:30],
        )
        fig.tight_layout()
        fig.savefig(os.path.join(d, fn), facecolor="#0d1117")
        plt.close(fig)
        pngs = sorted(x for x in os.listdir(d) if x.endswith(".png"))
        for old in pngs[:-40]:  # 保留最近 40 张
            try:
                os.remove(os.path.join(d, old))
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)
        return fn
    except Exception:
        return None


def _page_section(name, strat_name, params, m, bh, window, df, sig, market=None):
    """报告页 section 数据（净值降采样 + 交易表 + 对比）。"""
    return {
        "name": name,
        "strategy": strat_name,
        "window": window,
        "params": params,
        "m": {
            k: v
            for k, v in m.items()
            if k not in ("equity", "net_ret", "position", "trade_list")
        },
        "bh": {
            k: v for k, v in bh.items() if k not in ("equity", "net_ret", "position")
        },
        "equity": report_page._downsample(m["equity"]),
        "bench_equity": report_page._downsample(bh["equity"]),
        "dd": report_page._downsample(m["equity"] / m["equity"].cummax() - 1),
        "bench_dd": report_page._downsample(bh["equity"] / bh["equity"].cummax() - 1),
        "opt": optimize_run(df, strat_name, market=market),
        "opt_base": _effective_params(strat_name, params),
        "monthly": _monthly(m["equity"], bh["equity"]),
        "heat": _heat(m["equity"], sig),
        "png": _chart_png(name, m, bh),
        "trades": (m.get("trade_list") or [])[-30:][::-1],
        "compare": compare_strategies(df, strat_name, market=market),
        "last_px": float(df["close"].iloc[-1]),
        "last_date": df.index[-1].strftime("%m-%d"),
        "day_chg": (float(df["close"].iloc[-1]) / float(df["close"].iloc[-2]) - 1.0)
        if len(df) > 1
        else 0.0,
        "status": "持有多头" if m["position"].iloc[-1] > 0 else "空仓观望",
        "gen": _now(),
    }


def push_report_card(
    name,
    strat_name,
    params,
    m,
    bh,
    window,
    df,
    sig,
    title_prefix="📈 单股回测",
    report_file=None,
    news_name=None,
):
    """推送单股报告卡片；失败降级文本报告。返回是否成功。"""
    excess = m["total_return"] - bh["total_return"]
    buttons, qr = _card_actions(report_file, news_name)
    card = build_card(
        "%s · %s" % (title_prefix, name),
        window,
        "turquoise" if excess >= 0 else "red",
        report_markdown(name, strat_name, params, m, bh, window, df, sig),
        footer="仅回测参考，不构成投资建议 · 量化回测域",
        columns=key_columns(m),
        buttons=buttons,
        qr_key=qr,
        use_schema2=True,
    )
    if push_feishu(json.dumps(card, ensure_ascii=False), card=True):
        return True
    return push_feishu(
        fmt_report(name, strat_name, params, m, bh, window, df=df, sig=sig)
    )


def fmt_report(
    symbol, strat_name, params, m, bh, window, df=None, sig=None, market=None
):
    lines = [
        "📊 量化回测报告",
        "标的 %s · 策略 %s" % (symbol, strat_name),
        "区间 %s · 参数 %s" % (window, json.dumps(params, ensure_ascii=False)),
        "",
        "策略绩效:",
        "  总收益 %+.2f%%   年化 %+.2f%%" % (m["total_return"] * 100, m["cagr"] * 100),
        "  夏普 %.2f   索提诺 %.2f   最大回撤 %.2f%%"
        % (m["sharpe"], m["sortino"], m["max_drawdown"] * 100),
        "  胜率 %.1f%%   交易 %d 次   连亏 %d"
        % (m["win_rate"] * 100, m["trades"], m["max_loss_streak"]),
    ]
    if df is not None:
        lines += ["", "最新状态:  " + fmt_status(m, df, sig)]
    lines += [
        "",
        "基准（买入持有）:",
        "  总收益 %+.2f%%   夏普 %.2f   最大回撤 %.2f%%"
        % (bh["total_return"] * 100, bh["sharpe"], bh["max_drawdown"] * 100),
        "",
        "超额: 策略 %+.2f%% vs 持有 %+.2f%%"
        % (m["total_return"] * 100, bh["total_return"] * 100),
    ]
    if df is not None:
        lines += ["", "四策略同期对比:"] + [
            "  " + r for r in compare_strategies(df, strat_name, market=market)
        ]
        lines += ["", "近 %d 笔交易:" % _trades_shown()] + [
            "  " + x for x in fmt_trades(m).splitlines()
        ]
    lines.append("成本口径: " + cost_note(market))
    lines.append("—— 量化回测域 · 仅回测参考，不构成投资建议")
    return "\n".join(lines)


def run_single(strategy_name, symbol, market, start, end, push=False):
    frames = load_bars(market, [symbol], start, end)
    if symbol not in frames:
        print("BT_FAIL no data for %s/%s" % (market, symbol))
        return None
    df = frames[symbol]
    if len(df) < 30:
        print("BT_FAIL insufficient bars (%d) for %s" % (len(df), symbol))
        return None

    params = dict(CFG["strategies"].get(strategy_name, {}))
    sig = strat.REGISTRY[strategy_name](df, params)
    tc = backtest.load_trade_config(market)  # 成本按市场分层（CN/US/HK/CRYPTO）
    m = backtest.run(df, sig, **tc)
    bh = backtest.buy_and_hold(df)

    window = "%s ~ %s" % (
        df.index[0].strftime("%Y-%m-%d"),
        df.index[-1].strftime("%Y-%m-%d"),
    )
    out = {
        "ts": _now(),
        "strategy": strategy_name,
        "market": market,
        "symbol": symbol,
        "window": window,
        "params": params,
        "total_return": m["total_return"],
        "cagr": m["cagr"],
        "sharpe": m["sharpe"],
        "sortino": m["sortino"],
        "max_drawdown": m["max_drawdown"],
        "win_rate": m["win_rate"],
        "trades": m["trades"],
        "bar_count": m["bar_count"],
        "benchmark_return": bh["total_return"],
        "benchmark_sharpe": bh["sharpe"],
    }
    with open(RESULTS_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    _atomic(LAST_JSON, out)
    snap_file = None
    try:
        title = "单股回测 · %s" % symbol
        sec = _page_section(
            symbol, strategy_name, params, m, bh, window, df, sig, market=market
        )
        report_page.write_report_html(title, [sec])
        snap_file = report_page.write_snapshot(title, [sec], tag=symbol)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)

    report = fmt_report(
        symbol, strategy_name, params, m, bh, window, df=df, sig=sig, market=market
    )
    sent = False
    news_name = None
    try:
        wl = json.load(
            open(os.path.join(NEWS_DIR, "news_config.json"), encoding="utf-8")
        ).get("watchlist", [])
        for _m, _s, _n in resolve_watchlist(wl):
            if _s == symbol:
                news_name = _n
                break
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)
    if push and CFG["feishu"].get("push", True):
        sent = push_report_card(
            symbol,
            strategy_name,
            params,
            m,
            bh,
            window,
            df,
            sig,
            report_file=snap_file,
            news_name=news_name,
        )
    print(
        "BT_OK %s sharpe=%.2f ret=%+.2f%% bh=%+.2f%% sent=%s"
        % (symbol, m["sharpe"], m["total_return"] * 100, bh["total_return"] * 100, sent)
    )
    if not push:
        print(report)
    return out


def resolve_watchlist(wl):
    """watchlist 代码 → [(market, db_symbol, name)]。纯符号先按 US，运行时无数据再降级 CRYPTO。"""
    sys.path.insert(0, NEWS_DIR)
    from quote_snapshot import normalize_code

    out = []
    for w in wl:
        c = normalize_code(w.get("code", ""))
        nm = w.get("name", "")
        if c.startswith("sh"):
            out.append(("CN", c[2:] + ".SH", nm))
        elif c.startswith("sz") or c.startswith("bj"):
            out.append(("CN", c[2:] + "." + c[:2].upper(), nm))
        elif c.startswith("rt_hk"):
            out.append(("HK", "%05d" % int(c[5:]), nm))
        elif c.startswith("gb_"):
            out.append(("US", c[3:].upper().replace(".", "_"), nm))  # BRK.B → BRK_B
        else:  # 纯符号或已带 /USDT：US 优先，CRYPTO 兜底
            s = c.upper()
            out.append(("US", s, nm))
            out.append(("CRYPTO", s if "/" in s else s + "/USDT", nm))
    return out


_SCAN_MEDALS = ("🥇", "🥈", "🥉")


def _stock_drill(name, symbol):
    """卡内股票名 → 门户个股全景页下钻链接（K-STOCK-388）。

    base 读 report.portal_url：局域网反代（8080 链路）即电脑可达；
    B 方案（K-STOCK-374 frpc+云nginx）落地后换公网域名，全卡自动升级、
    手机端随之可达。portal_url 未配置时回落纯文本（K42-c 判例默认面）。
    代码规则同 report_page._stock_link：CN 去后缀取 6 位，其余原样编码。"""
    base = (CFG.get("report", {}).get("portal_url") or "").rstrip("/")
    nm = str(name or "")
    c = str(symbol or "").strip()
    if not base or not c:
        return nm
    if c.endswith((".SH", ".SZ")):
        c = c[:6]
    return "[%s](%s/kg/stock/%s)" % (nm, base, quote(c, safe=""))


def _is_nan(v):
    """基准 nan 守卫（停牌/退市标的 buy_and_hold 可能返回 nan）。"""
    try:
        return v is None or math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def scan_text(strategy_name, results, mkts):
    """扫描文本版（卡片推送失败时的降级面；基准 nan 以 — 展示）。"""
    lines = ["🔍 策略扫描 %s（%d 只 · 按夏普排名）" % (strategy_name, len(results))]
    for r in results:
        bench = (
            "—"
            if _is_nan(r["benchmark_return"])
            else "%+.1f%%" % (r["benchmark_return"] * 100)
        )
        lines.append(
            "· [%s] %s 夏普%.2f 收益%+.1f%% 基准%s 回撤%.1f%%"
            % (
                r["market"],
                r["name"],
                r["sharpe"],
                r["total_return"] * 100,
                bench,
                r["max_drawdown"] * 100,
            )
        )
    # 成本口径标注：让下游看到各市场回测假设（口径变化可见）
    lines.append("—— " + " · ".join(cost_note(m) for m in mkts))
    lines.append("—— 量化回测域")
    return "\n".join(lines)


def scan_card(strategy_name, results, mkts, ts):
    """扫描榜单卡（K-STOCK-386）：复用单票卡判例（schema2 + 红涨绿跌 _c_pct）。
    结构：顶部三列指标（跑赢基准/平均夏普/最深回撤）→ 正负夏普分区榜单 →
    成本口径灰字尾注。榜单行=奖牌|#名次 名称[可下钻→/kg/stock][市场灰标]
    ｜夏普｜收益｜基准｜超额｜回撤；超额=收益-基准（基准 nan 以 — 展示，不出现 nan%）。"""
    beat = sum(
        1
        for r in results
        if not _is_nan(r["benchmark_return"])
        and r["total_return"] > r["benchmark_return"]
    )
    avg_sharpe = sum(r["sharpe"] for r in results) / len(results) if results else 0.0
    worst_dd = min((r["max_drawdown"] for r in results), default=0.0)

    def line(i, r):
        rank = _SCAN_MEDALS[i] if i < 3 else "#%d" % (i + 1)
        bench = r["benchmark_return"]
        b = "<font color='grey'>—</font>" if _is_nan(bench) else _c_pct(bench)
        exc = (
            "<font color='grey'>—</font>"
            if _is_nan(bench)
            else _c_pct(r["total_return"] - bench)
        )
        nm = _stock_drill(r["name"], r["symbol"])  # K-STOCK-388 可下钻
        return (
            "%s %s <font color='grey'>[%s]</font>｜夏普 **%.2f**"
            "｜收益 %s｜基准 %s｜超额 %s｜回撤 %.1f%%"
        ) % (
            rank,
            nm,
            r["market"],
            r["sharpe"],
            _c_pct(r["total_return"]),
            b,
            exc,
            r["max_drawdown"] * 100,
        )

    md = []
    pos = [line(i, r) for i, r in enumerate(results) if r["sharpe"] >= 0]
    neg = [line(i, r) for i, r in enumerate(results) if r["sharpe"] < 0]
    if pos:
        md.append("**📈 正夏普区（%d 只）**\n" % len(pos) + "\n".join(pos))
    if neg:
        md.append("**📉 负夏普区（%d 只）**\n" % len(neg) + "\n".join(neg))
    footer = (
        "<font color='grey'>%s</font>\n"
        "<font color='grey'>仅回测参考，不构成投资建议 · 量化回测域</font>"
        % " · ".join(cost_note(m) for m in mkts)
    )
    return build_card(
        "🔍 策略扫描 · %s" % strategy_name,
        "%d 只 · 按夏普排名 · %s" % (len(results), ts),
        "turquoise" if avg_sharpe >= 0 else "red",
        md,
        footer=footer,
        columns=[
            ("跑赢基准", "%d/%d" % (beat, len(results)), "wathet-50"),
            ("平均夏普", "%.2f" % avg_sharpe, "wathet-50"),
            ("最深回撤", "%.1f%%" % (worst_dd * 100), "orange-50"),
        ],
        use_schema2=True,
    )


def run_scan(strategy_name, market, start, end, push=False):
    wl = json.load(
        open(os.path.join(NEWS_DIR, "news_config.json"), encoding="utf-8")
    ).get("watchlist", [])
    entries = resolve_watchlist(wl)
    if market and market != "ALL":
        entries = [e for e in entries if e[0] == market]
    if not entries:
        print("SCAN empty watchlist")
        return
    params = dict(CFG["strategies"].get(strategy_name, {}))

    # 按市场分组，单市场一次查询；去重（同一 watchlist 项 US/CRYPTO 双注册只留有数据的）
    by_mkt = {}
    for mkt, sym, nm in entries:
        by_mkt.setdefault(mkt, []).append((sym, nm))
    frames = {}
    name_of = {}
    for mkt, pairs in by_mkt.items():
        syms = [s for s, _ in pairs]
        got = load_bars(mkt, syms, start, end)
        for sym, nm in pairs:
            if sym in got and len(got[sym]) >= 30:
                frames[(mkt, sym)] = got[sym]
                name_of[(mkt, sym)] = nm

    results = []
    for (mkt, sym), df in frames.items():
        sig = strat.REGISTRY[strategy_name](df, params)
        tc = backtest.load_trade_config(mkt)  # 成本按市场分层
        m = backtest.run(df, sig, **tc)
        bh = backtest.buy_and_hold(df)
        results.append(
            {
                "market": mkt,
                "symbol": sym,
                "name": name_of.get((mkt, sym), sym),
                "sharpe": m["sharpe"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "win_rate": m["win_rate"],
                "benchmark_return": bh["total_return"],
            }
        )
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    out = {
        "ts": _now(),
        "strategy": strategy_name,
        "market": market or "ALL",
        "mode": "scan",
        "results": results,
    }
    _atomic(LAST_JSON, out)
    mkts = sorted({r["market"] for r in results})
    txt = scan_text(strategy_name, results, mkts)
    sent = False
    if push and CFG["feishu"].get("push", True):
        card = scan_card(strategy_name, results, mkts, out["ts"])
        # 卡片优先，失败降级文本（同单票卡 push_report_card 判例）
        sent = push_feishu(
            json.dumps(card, ensure_ascii=False), card=True
        ) or push_feishu(txt)
    print("SCAN done %d stocks sent=%s" % (len(results), sent))
    if not push:
        print(txt)
    return out


def run_daily():
    """计划任务入口：读 config daily_scan（strategy/lookback_days/push/top_reports）。"""
    d = CFG.get("daily_scan", {})
    strategy_name = d.get("strategy", "trend_follow")
    start = (
        datetime.now() - timedelta(days=int(d.get("lookback_days", 365)))
    ).strftime("%Y-%m-%d")
    out = run_scan(strategy_name, "ALL", start, None, push=bool(d.get("push", True)))
    try:
        update_ledger()  # 16:00 顺带：信号页刷新 + 纸面持仓账本更新
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)
    topn = int(d.get("top_reports", 0) or 0)
    if not topn or not out or not out.get("results"):
        return
    # 对夏普 Top N 生成完整单股卡片报告（合并一张卡片；不覆盖门户的扫描结果）
    params = dict(CFG["strategies"].get(strategy_name, {}))
    blocks = []
    page_secs = []
    n_ok = 0
    for r in out["results"][:topn]:
        got = load_bars(r["market"], [r["symbol"]], start, None)
        if r["symbol"] not in got or len(got[r["symbol"]]) < 30:
            continue
        df = got[r["symbol"]]
        sig = strat.REGISTRY[strategy_name](df, params)
        tc = backtest.load_trade_config(r["market"])  # 成本按市场分层
        m = backtest.run(df, sig, **tc)
        bh = backtest.buy_and_hold(df)
        window = "%s ~ %s" % (
            df.index[0].strftime("%Y-%m-%d"),
            df.index[-1].strftime("%Y-%m-%d"),
        )
        name = "[%s] %s" % (r["market"], r["name"])
        blocks += report_markdown(
            name, strategy_name, params, m, bh, window, df, sig, market=r["market"]
        )
        page_secs.append(
            _page_section(
                name, strategy_name, params, m, bh, window, df, sig, market=r["market"]
            )
        )
        n_ok += 1
    if not n_ok:
        return
    snap_file = None
    try:
        title = "每日扫描 Top%d 回测报告" % n_ok
        report_page.write_report_html(title, page_secs)
        snap_file = report_page.write_snapshot(title, page_secs, tag="top")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/runner.py", exc)
    buttons, qr = _card_actions(snap_file)
    card = build_card(
        "📈 每日扫描 Top%d 单股回测详情" % n_ok,
        "16:00 自动 · %s · 近 %d 天"
        % (strategy_name, int(d.get("lookback_days", 365))),
        "indigo",
        blocks,
        footer="仅回测参考，不构成投资建议 · 量化回测域",
        buttons=buttons,
        qr_key=qr,
        use_schema2=True,
    )
    if push_feishu(json.dumps(card, ensure_ascii=False), card=True):
        print("TOP_REPORTS pushed %d" % n_ok)
    else:
        # 卡片失败降级纯文本
        texts = []
        for r in out["results"][:topn]:
            got = load_bars(r["market"], [r["symbol"]], start, None)
            if r["symbol"] not in got or len(got[r["symbol"]]) < 30:
                continue
            df = got[r["symbol"]]
            sig = strat.REGISTRY[strategy_name](df, params)
            tc = backtest.load_trade_config(r["market"])  # 成本按市场分层
            m = backtest.run(df, sig, **tc)
            bh = backtest.buy_and_hold(df)
            window = "%s ~ %s" % (
                df.index[0].strftime("%Y-%m-%d"),
                df.index[-1].strftime("%Y-%m-%d"),
            )
            texts.append(
                fmt_report(
                    "[%s] %s" % (r["market"], r["name"]),
                    strategy_name,
                    params,
                    m,
                    bh,
                    window,
                    df=df,
                    sig=sig,
                    market=r["market"],
                )
            )
        if texts and push_feishu("\n\n────────────\n\n".join(texts)):
            print("TOP_REPORTS pushed %d (text fallback)" % len(texts))


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--strategy", default="ma_cross", choices=list(strat.REGISTRY))
    p.add_argument(
        "--market",
        default="ALL",
        choices=["ALL", "CN", "HK", "US", "CRYPTO"],
        help="单股模式必填；扫描模式默认 ALL",
    )
    p.add_argument("--symbol")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--scan", action="store_true")
    p.add_argument(
        "--daily", action="store_true", help="计划任务模式：读 config daily_scan"
    )
    p.add_argument(
        "--signals",
        action="store_true",
        help="只刷新全池信号页（/bt-signals），不回测不推送",
    )
    p.add_argument(
        "--ledger",
        action="store_true",
        help="盘后更新纸面持仓账本并刷新 /bt-positions 页",
    )
    p.add_argument(
        "--morning", action="store_true", help="盘前推送作战计划卡（计划任务用，纸面）"
    )
    p.add_argument(
        "--optpage", action="store_true", help="只刷新全池寻优榜单页（/bt-opt），不推送"
    )
    p.add_argument("--push", action="store_true")
    a = p.parse_args()
    if a.daily:
        run_daily()
    elif a.ledger:
        update_ledger()
    elif a.morning:
        morning_push()
    elif a.signals:
        write_signals_page()
    elif a.optpage:
        write_opt_page()
    elif a.scan:
        run_scan(a.strategy, a.market, a.start, a.end, push=a.push)
    elif a.symbol:
        mkt = a.market
        if mkt == "ALL":  # 按代码格式推断
            s = a.symbol.upper()
            mkt = (
                "CRYPTO"
                if "/" in s
                else "HK"
                if s.isdigit() and len(s) == 5
                else "CN"
                if s.endswith((".SH", ".SZ", ".BJ"))
                else "US"
            )
        run_single(a.strategy, a.symbol, mkt, a.start, a.end, push=a.push)
    else:
        p.print_help()


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
