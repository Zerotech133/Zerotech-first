# -*- coding: utf-8 -*-
"""AI 工作站统一日报：每晚聚合一天核心产出，推一条飞书汇总卡（对外价值版）。

K-STOCK-349 拍板（2026-10-14 老田）：彻底简化——砍系统健康/内容产出
（内部运维与内容线与读者无关），聚焦四块：
  🧭 舆论风向一行（news.db jev 24h 聚合，同新闻日报 v2 口径）
  📰 信息采集量（status.json：24h/累计/源在线——只报数不报内部任务状态）
  🔥 板块异动（sectors.json 15:30 复盘落盘）
  💼 量化实战（positions.json——浮盈口径=等权平均，禁用逐股收益率加总）

用法: python daily_report.py [--push]
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import card_build

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
CST = timezone(timedelta(hours=8))


def _j(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # 豁免（C2批2）：数据文件缺失/损坏属常态，各 block 按 None 降级跳过


def _green(v, fmt="%+.2f%%"):
    return "<font color='green'>%s</font>" % (fmt % v) if v >= 0 \
        else "<font color='red'>%s</font>" % (fmt % v)


def _sentiment_line():
    """舆论风向一行版：news.db jev 24h 聚合（口径同新闻日报 v2，缺库静默跳过）。"""
    import sqlite3
    try:
        db = sqlite3.connect(os.path.join(HERE, "news.db"))
        since = (datetime.now(CST) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        rows = db.execute(
            "SELECT jev FROM news WHERE jev IS NOT NULL AND jev != '' "
            "AND created_at >= ?", (since,)).fetchall()
        db.close()
    except Exception:
        return None
    cnt = {"bull": 0, "bear": 0, "neutral": 0}
    for (jev,) in rows:
        try:
            b = json.loads(jev).get("bias")
        except ValueError:
            continue
        if b in cnt:
            cnt[b] += 1
    total = sum(cnt.values())
    if not total:
        return None
    pb, ps = cnt["bull"] / total, cnt["bear"] / total
    skew = (pb - ps) * 100
    if skew > 10:
        tone, color = "整体偏暖", "red"
    elif skew < -10:
        tone, color = "整体偏冷", "green"
    else:
        tone, color = "多空均衡", "grey"
    return ("🧭 **舆论风向：<font color='%s'>%s</font>**"
            "（偏多 %.0f%% / 偏空 %.0f%% · AI 判定 %d 条）"
            % (color, tone, pb * 100, ps * 100, total))


def build_blocks(today):
    """返回 md_blocks（四块：风向/采集/板块/量化，缺啥跳啥）。"""
    blocks = []
    sl = _sentiment_line()
    if sl:
        blocks.append(sl)

    # ── 📰 信息采集（只报数：24h/累计/源在线） ────────────────
    st = _j(os.path.join(HERE, "status.json")) or {}
    srcs = st.get("sources") or {}
    n_ok = sum(1 for v in srcs.values() if v and v.get("ok"))
    blocks.append("📰 **信息采集**\n24h 入库 %s 条 · 累计 %s 条 · 采集源在线 %d/%d" % (
        st.get("news_24h", "?"), st.get("news_total", "?"),
        n_ok, len(srcs)))

    # ── 🔥 板块异动 ──────────────────────────────────────────
    sec = _j(os.path.join(HERE, "sectors.json")) or {}
    items = sec.get("items") or []
    if items:
        s = sorted(items, key=lambda x: -x.get("pct", 0))
        top = "、".join("%s %s" % (x["name"], _green(x["pct"]))
                       for x in s[:3])
        bot = "、".join("%s %s" % (x["name"], _green(x["pct"]))
                        for x in s[-3:])
        blocks.append("🔥 **板块异动**（%s）\n领涨：%s\n领跌：%s" % (
            sec.get("ts", "—"), top, bot))
    else:
        blocks.append("🔥 **板块异动**（暂无，交易日 15:30 后生成）")

    # ── 💼 量化实战（纸面） ──────────────────────────────────
    # 浮盈口径=等权平均（K-STOCK-349 判例：逐股收益率直接加总无组合意义）
    led = _j(os.path.join(STATION, "quant_backtest",
                          "positions.json")) or {}
    pend = led.get("pending") or []
    open_ = led.get("open") or []
    closed = led.get("closed") or []
    n_today = sum(1 for c in closed if c.get("exit_date") == today)
    wins = sum(1 for c in closed if c.get("pnl", 0) > 0)
    wr = wins * 100.0 / len(closed) if closed else 0
    lines = ["💼 **量化实战（纸面）**",
             "明晨待买 %d · 持仓 %d · 今日平仓 %d" % (
                 len(pend), len(open_), n_today)]
    if closed:
        lines.append("累计平仓 %d 笔 · 胜率 %.0f%%" % (len(closed), wr))
    if open_:
        avg = sum(e.get("pnl", 0) for e in open_) / len(open_)
        best = max(open_, key=lambda e: e.get("pnl", 0))
        worst = min(open_, key=lambda e: e.get("pnl", 0))
        lines.append("持仓等权浮盈 %s（%d 只 · 最佳 %s %s · 最差 %s %s）" % (
            _green(avg * 100), len(open_),
            best.get("name") or best.get("symbol"), _green(best["pnl"] * 100),
            worst.get("name") or worst.get("symbol"), _green(worst["pnl"] * 100)))
    blocks.append("\n".join(lines))
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
