# -*- coding: utf-8 -*-
"""AI 工作站统一日报：每晚聚合全系统一天产出，推一条飞书汇总卡。

数据源（全部只读本地落盘，缺啥跳啥，单源失败不影响整卡）:
  news_collector/status.json      新闻采集量/源状态
  news_collector/ai_brief.json    AI 晨报执行状态
  news_collector/ai_review.json   收盘复盘执行状态
  news_collector/sectors.json     板块涨跌（15:30 复盘落盘）
  quant_backtest/positions.json   纸面持仓账本（16:00 盘后更新）
  quant_backtest/last_backtest.json  最新回测结果
  gzh/drafts/index.json           公众号草稿
  video/output/index.json         短视频成片

用法: python daily_report.py [--push]
"""
import json
import os
import sys
from datetime import datetime

import card_build

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)


def _j(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # 豁免（C2批2）：数据文件缺失/损坏属常态，各 block 按 None 降级跳过


def _green(v, fmt="%+.2f%%"):
    return "<font color='green'>%s</font>" % (fmt % v) if v >= 0 \
        else "<font color='red'>%s</font>" % (fmt % v)


def _ok(flag):
    return "✓" if flag else "<font color='red'>✗</font>"


def _health_block():
    """调用 ops/health_check.py 汇总系统健康（缺啥跳啥）。"""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(STATION, "ops", "health_check.py"),
             "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        h = json.loads((r.stdout or "").strip() or "null")
        if not h:
            return None
    except Exception:
        return None  # 豁免（C2批2）：docstring 声明——health 缺啥跳啥（健康块降级不阻断日报）
    svcs = h.get("services") or {}
    n_ok = sum(1 for v in svcs.values() if v)
    dk = h.get("docker") or {}
    tk = h.get("tasks") or {}
    bad = tk.get("bad") or []
    lines = ["**🖥 系统健康**",
             "服务 %d/%d · 容器 %s · 计划任务 %d（异常 %d）" % (
                 n_ok, len(svcs), dk.get("total", "?"),
                 tk.get("total", 0), len(bad))]
    for iss in (h.get("issues") or [])[:4]:
        lines.append("<font color='red'>⚠ %s</font>" % iss)
    return "\n".join(lines)


def build_blocks(today):
    """返回 (md_blocks, 汇总统计 dict)。"""
    blocks = []
    hb = _health_block()
    if hb:
        blocks.append(hb)

    # ── 📰 信息采集 ──────────────────────────────────────────
    st = _j(os.path.join(HERE, "status.json")) or {}
    srcs = st.get("sources") or {}
    n_ok = sum(1 for v in srcs.values() if v and v.get("ok"))
    parts = ["**📰 信息采集**",
             "24h 入库 %s 条（累计 %s）· 源在线 %d/%d · 最近采集 %s" % (
                 st.get("news_24h", "?"), st.get("news_total", "?"),
                 n_ok, len(srcs), st.get("last_collect", "—"))]
    ai = _j(os.path.join(HERE, "ai_brief.json")) or {}
    rv = _j(os.path.join(HERE, "ai_review.json")) or {}
    parts.append("AI 晨报 %s · 收盘复盘 %s" % (
        _ok(ai.get("ok")), _ok(rv.get("ok"))))
    blocks.append("\n".join(parts))

    # ── 🔥 板块异动 ──────────────────────────────────────────
    sec = _j(os.path.join(HERE, "sectors.json")) or {}
    items = sec.get("items") or []
    if items:
        s = sorted(items, key=lambda x: -x.get("pct", 0))
        top = "、".join("%s %s" % (x["name"], _green(x["pct"]))
                       for x in s[:3])
        bot = "、".join("%s %s" % (x["name"], _green(x["pct"]))
                       for x in s[-3:])
        blocks.append("**🔥 板块异动**（%s）\n领涨：%s\n领跌：%s" % (
            sec.get("ts", "—"), top, bot))
    else:
        blocks.append("**🔥 板块异动**（暂无，交易日 15:30 后生成）")

    # ── 💼 量化实战（纸面） ──────────────────────────────────
    led = _j(os.path.join(STATION, "quant_backtest",
                          "positions.json")) or {}
    pend = led.get("pending") or []
    open_ = led.get("open") or []
    closed = led.get("closed") or []
    n_today = sum(1 for c in closed if c.get("exit_date") == today)
    wins = sum(1 for c in closed if c.get("pnl", 0) > 0)
    wr = wins * 100.0 / len(closed) if closed else 0
    pnl_open = sum(e.get("pnl", 0) for e in open_)
    lines = ["**💼 量化实战（纸面）**",
             "明晨待买 %d · 持仓 %d · 今日平仓 %d" % (
                 len(pend), len(open_), n_today)]
    if closed:
        lines.append("累计平仓 %d 笔 · 胜率 %.0f%%" % (len(closed), wr))
    if open_:
        lines.append("持仓合计浮盈 %s" % _green(pnl_open))
    bt = _j(os.path.join(STATION, "quant_backtest",
                         "last_backtest.json")) or {}
    if bt.get("mode") == "scan" and bt.get("results"):
        b = bt["results"][0]
        lines.append("最新扫描 %s · Top [%s] %s 夏普%.2f %s" % (
            bt.get("ts", "")[:10], b.get("market"), b.get("name"),
            b.get("sharpe", 0), _green(b.get("total_return", 0) * 100)))
    elif bt.get("symbol"):
        lines.append("最新单股 [%s] %s · %s · 夏普%.2f %s" % (
            bt.get("market"), bt.get("symbol"),
            bt.get("ts", "")[:10], bt.get("sharpe", 0),
            _green((bt.get("total_return") or 0) * 100)))
    blocks.append("\n".join(lines))

    # ── 📝 内容产出 ──────────────────────────────────────────
    gz = _j(os.path.join(STATION, "gzh", "drafts", "index.json")) or []
    vd = _j(os.path.join(STATION, "video", "output", "index.json")) or []
    gz_today = sum(1 for x in gz if str(x.get("ts", "")).startswith(today))
    vd_today = sum(1 for x in vd if str(x.get("ts", "")).startswith(today))
    pub = sum(1 for x in gz if x.get("status") == "published")
    rev = sum(1 for x in gz if x.get("status") == "reviewed")
    blocks.append("**📝 内容产出**\n公众号：今日 +%d 稿（累计 %d · 已审 %d · "
                  "已发 %d）\n短视频：今日 +%d 片（累计 %d）" % (
                      gz_today, len(gz), rev, pub, vd_today, len(vd)))
    return blocks


def main():
    push = "--push" in sys.argv
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cfg = _j(os.path.join(HERE, "news_config.json")) or {}
    portal = cfg.get("portal_url")
    doc = cfg.get("doc_url", "")
    blocks = build_blocks(today)
    footer = "统一日报 · 各域计划任务自动产出汇总 · 不构成投资建议"
    # K43 切真 Card 2.0：手机端渲染需要（混血卡手机空白判例）；
    # 内网按钮/二维码已移除（外网不可达），doc 飞书文档链接改 md 超链接
    blocks.append("")
    blocks.append("[📄 详情文档](%s)" % doc if doc else
                  "（未配置文档 URL）")
    card = card_build.build_card(
        "🌙 AI 工作站统一日报", now + " · 五域一天全览", "violet",
        blocks, footer=footer, use_schema2=True)
    if not push:
        for b in blocks:
            print(b, "\n" + "─" * 40)
        print("DRY_RUN (add --push to send)")
        return
    # K163 三目标推送：首目标（个人）card_build 完整通道，家人群/量化群 OpenAPI
    # 直连——复用 alert_push.push_card_multi 判例，勿在此重写发送逻辑
    from alert_push import push_card_multi, _targets
    res = push_card_multi(card, targets=_targets(cfg))
    print("DAILY_REPORT pushed %s" % res)
    if res.get("个人"):  # 首目标成功即触发文档刷新（群目标成败已含在 res 打印里）
        try:  # 同步刷新飞书简报文档（失败静默）
            import news_doc_board
            news_doc_board.refresh()
        except Exception as e:
            print("doc_board refresh err %s" % str(e)[:100])  # C2批2 吞错落盘
    else:  # 文本兜底
        import subprocess
        txt = "\n\n".join(blocks) + "\n—— " + footer
        tmp = os.path.join(HERE, "outbox", "pending_daily.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt)
        r = subprocess.run([sys.executable,
                            os.path.join(HERE, "send_feishu.py"), tmp],
                           capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        print("DAILY_REPORT text_fallback=%s"
              % (r.stdout or "").strip().startswith("SEND_OK"))


if __name__ == "__main__":
    main()
