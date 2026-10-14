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


def _find_cli():
    """lark-cli 解析（K-STOCK-396 解耦）：仓库 bin/ 固定副本优先，
    插件目录版本扫描回退，找不到回退裸名（PATH 侥幸）。"""
    _bin = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    root = os.path.join(os.path.expanduser("~"), ".trae-cn", "plugins",
                        "trae-remote-official", "lark")
    if os.path.isdir(root):
        for d in sorted(os.listdir(root), reverse=True):
            p = os.path.join(root, d, "bin", "lark-cli.exe")
            if os.path.isfile(p):
                return p
    return "lark-cli"


def _cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}  # 豁免（C2批2）：配置缺失→默认 doc id，refresh 返回值由调用方记录


def _latest_report(db, table):
    try:
        return db.execute(
            "SELECT date, ts, model, ok, markets, brief FROM %s "
            "ORDER BY date DESC LIMIT 1" % table).fetchone()
    except Exception:
        return None  # 豁免（C2批2）：板块降级为空，文档刷新主流程不受阻


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
        return []  # 豁免（C2批2）：命中快照板块降级为空，文档刷新主流程不受阻
    out = []
    for r in rows:
        try:
            stocks = json.loads(r[4]) if r[4] else []
        except Exception:
            stocks = []
        out.append(dict(source=r[0], ts=r[1], title=r[2], hit=r[3], stocks=stocks))
    return out


# K-STOCK-362 表格化渲染：对齐 doc_board.py（量化实战·详情看板）判例——
# 结构化数据一律 Markdown 表格、零码块；news_ai.format_* 保留给飞书卡纯文本降级。
IDX_GRPS = ("A股", "港股", "美股", "商品", "汇率")


def _pct_cell(pct):
    """涨跌单元格：▲+1.25% / ▼0.43% / —（None）。"""
    if pct is None:
        return "—"
    if pct > 0:
        return "▲%+.2f%%" % pct
    if pct < 0:
        return "▼%.2f%%" % abs(pct)
    return "0.00%"


def _markets_tables(markets):
    """全球市场速览 → 指数表 + 自选表（自选数量多，独立小节）。"""
    idx = [m for m in markets if m.get("grp") in IDX_GRPS]
    watch = [m for m in markets if m.get("grp") not in IDX_GRPS]
    lines = []
    if idx:
        lines += ["| 市场 | 指数 | 点位 | 涨跌 |",
                  "| --- | --- | --- | --- |"]
        lines += ["| %s | %s | %s | %s |" % (
            m["grp"], m["name"],
            ("%." + str(m["dec"]) + "f") % m["price"],
            _pct_cell(m.get("pct"))) for m in idx]
    if watch:
        lines += ["", "### 自选速览", "",
                  "| 标的 | 现价 | 涨跌 |",
                  "| --- | --- | --- |"]
        lines += ["| %s | %s | %s |" % (
            m["name"], ("%." + str(m["dec"]) + "f") % m["price"],
            _pct_cell(m.get("pct"))) for m in watch]
    return lines


def _sectors_tables(sectors, ts, top_n=6):
    """板块热力 → 广度一句话 + 领涨/领跌合并表（方向列）。"""
    if not sectors:
        return []
    up = sum(1 for s in sectors if s["pct"] > 0)
    dn = sum(1 for s in sectors if s["pct"] < 0)
    mood = "普涨" if up * 3 > len(sectors) * 2 else (
        "普跌" if up * 3 < len(sectors) else "分化")
    lines = ["全行业 %d 个：%d 涨 / %d 跌（上涨占比 %d%%，%s）" % (
        len(sectors), up, dn, round(up * 100 / len(sectors)), mood), "",
        "| 方向 | 板块 | 涨跌 |",
        "| --- | --- | --- |"]
    lines += ["| 🔴 领涨 | %s | %+.2f%% |" % (s["name"], s["pct"])
              for s in sectors[:top_n]]
    lines += ["| 🟢 领跌 | %s | %.2f%% |" % (s["name"], s["pct"])
              for s in list(reversed(sectors))[:top_n]]
    return lines  # ts 由调用方拼进小节标题


_SRC_NAME = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}


def _hits_table(items):
    """关注命中 → 表格（时间取 HH:MM，标题前缀【关联股】）。"""
    lines = ["| 时间 | 来源 | 标题 |",
             "| --- | --- | --- |"]
    for it in items:
        t = (it.get("ts") or "")
        t = t[-5:] if len(t) >= 5 else t  # "2026-10-14 07:21" → "07:21"
        tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
        lines.append("| %s | %s | %s%s |" % (
            t, _SRC_NAME.get(it["source"], it["source"]),
            tag, (it.get("title") or "").replace("|", "／")[:70]))
    return lines


def _report_section(db, table, title, with_sectors=False):
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
        lines += ["", "### 全球市场速览", ""]
        lines += _markets_tables(markets)
    if with_sectors:
        try:
            with open(os.path.join(HERE, "sectors.json"), encoding="utf-8") as f:
                sec = json.load(f)
            st_lines = _sectors_tables(sec.get("items") or [],
                                       sec.get("ts", ""))
        except Exception:
            st_lines = []
        if st_lines:
            lines += ["", "### 行业板块热力（%s）" % sec.get("ts", ""), ""]
            lines += st_lines
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
                      "## ⭐ 最新关注命中（近 24h）", ""]
            lines += _hits_table(list(reversed(hits)))
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
            [_find_cli(), "docs", "+update", "--as", "user", "--doc", doc,
             "--command", "overwrite", "--doc-format", "markdown",
             "--content", "@./_board_tmp.md", "--format", "json"],
            capture_output=True, text=True, timeout=120, cwd=HERE)
        out = (r.stdout or "")
        return '"ok": true' in out or '"ok":true' in out
    except Exception:
        return False  # 豁免（C2批2）：失败随返回值上抛，调用方（daily_report/news_ai）已有日志
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass  # 豁免（C2批2）：临时文件清理兜底


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        print(build_md())
    else:
        print("REFRESH", "OK" if refresh() else "FAIL")
    sys.exit(0)
