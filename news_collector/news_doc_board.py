# -*- coding: utf-8 -*-
"""信息采集·今日简报 看板镜像（飞书云文档）：卡片「📄 详情文档」按钮直达。

  手机飞书内原生打开，不依赖家里网络/Tailscale —— 解决移动端
  「按钮点不开、看不到详情」。每次晨报/复盘推送后整体 overwrite 刷新；
  文档 URL 存 news_config.json doc_url。
"""
import json
import os
import sqlite3
import subprocess
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")


def _cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _latest_report(db, table):
    try:
        return db.execute(
            "SELECT date, ts, model, ok, markets, brief FROM %s "
            "ORDER BY date DESC LIMIT 1" % table).fetchone()
    except Exception:
        return None


def _recent_hits(db, limit=20):
    """近 24h 关注命中快讯（hit=1 或关联自选股）。"""
    try:
        import news_ai
        since = (news_ai.now_cst() - timedelta(hours=24)
                 ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        since = "2000-01-01 00:00"
    try:
        rows = db.execute(
            "SELECT source, ts, title, hit, COALESCE(stocks,'') FROM news "
            "WHERE created_at>=? AND (hit=1 OR (stocks IS NOT NULL AND stocks!='')) "
            "ORDER BY created_at DESC LIMIT ?", (since, limit)).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            stocks = json.loads(r[4]) if r[4] else []
        except Exception:
            stocks = []
        out.append(dict(source=r[0], ts=r[1], title=r[2], hit=r[3], stocks=stocks))
    return out


def _report_section(db, table, title, with_sectors=False):
    import news_ai
    lines = ["## %s" % title, ""]
    row = _latest_report(db, table)
    if not row:
        lines.append("（暂无数据，等待今日自动生成）")
        return lines
    date, ts, model, ok, markets_j, brief = row
    lines.append("**%s · 生成于 %s（北京）· 模型 %s%s**" % (
        date, ts, model, "" if ok else " · ⚠ AI 降级为原文汇总"))
    try:
        markets = json.loads(markets_j or "[]")
    except Exception:
        markets = []
    if markets:
        lines += ["", "### 全球市场速览", "", "```",
                  news_ai.format_markets(markets), "```"]
    if with_sectors:
        try:
            with open(os.path.join(HERE, "sectors.json"), encoding="utf-8") as f:
                sec = json.load(f)
            st = news_ai.format_sectors(sec.get("items") or [])
        except Exception:
            st = ""
        if st:
            lines += ["", "### 行业板块热力（%s）" % sec.get("ts", ""), "",
                      "```", st, "```"]
    lines += ["", brief or "（无正文）"]
    return lines


def build_md():
    import news_ai
    now = news_ai.now_cst().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 信息采集·今日简报",
        "",
        "> 更新：%s（北京）· 每日 08:00 晨报 / 15:30 收盘复盘后自动刷新"
        " · 仅信息参考，不构成投资建议" % now,
        "",
    ]
    db = sqlite3.connect(DB)
    try:
        lines += _report_section(db, "ai_briefs", "🤖 AI 金融晨报")
        lines += ["", "---", ""]
        lines += _report_section(db, "ai_reviews", "📊 AI 收盘复盘",
                                 with_sectors=True)
        hits = _recent_hits(db)
        if hits:
            lines += ["", "---", "",
                      "## ⭐ 最新关注命中（近 24h）", "", "```",
                      news_ai.format_news(list(reversed(hits))), "```"]
    finally:
        db.close()
    return "\n".join(lines)


def refresh():
    """整体刷新简报文档；成功 True，失败 False（绝不影响卡片推送）。"""
    doc = _cfg().get("doc_url")
    if not doc:
        return False
    tmp = os.path.join(HERE, "_board_tmp.md")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(build_md())
        r = subprocess.run(
            ["lark-cli", "docs", "+update", "--as", "user", "--doc", doc,
             "--command", "overwrite", "--doc-format", "markdown",
             "--content", "@./_board_tmp.md", "--format", "json"],
            capture_output=True, text=True, timeout=120, cwd=HERE)
        out = (r.stdout or "")
        return '"ok": true' in out or '"ok":true' in out
    except Exception:
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        print(build_md())
    else:
        print("REFRESH", "OK" if refresh() else "FAIL")
    sys.exit(0)
