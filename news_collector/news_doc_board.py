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
import sys as _sys0
import logging

if HERE not in _sys0.path:
    _sys0.path.insert(0, HERE)
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")


def _find_cli():
    """lark-cli 解析（K-STOCK-396 解耦）：仓库 bin/ 固定副本优先，
    插件目录版本扫描回退，找不到回退裸名（PATH 侥幸）。"""
    _bin = os.path.join(os.path.dirname(HERE), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    root = os.path.join(
        os.path.expanduser("~"), ".trae-cn", "plugins", "trae-remote-official", "lark"
    )
    if os.path.isdir(root):
        for d in sorted(os.listdir(root), reverse=True):
            p = os.path.join(root, d, "bin", "lark-cli.exe")
            if os.path.isfile(p):
                return p
    return "lark-cli"


def _cfg():
    # 2026-10-25 SEC 收口（EXEC-R14）：doc_url 读 env/本地私有覆盖，见 config_local.py
    from config_local import load_news_config

    return load_news_config()


def _latest_report(db, table):
    try:
        return db.execute(
            "SELECT date, ts, model, ok, markets, brief FROM %s "  # nosec B608 - 值参数化/内部标识符（已抽查）
            "ORDER BY date DESC LIMIT 1" % table
        ).fetchone()
    except Exception:
        return None  # 豁免（C2批2）：板块降级为空，文档刷新主流程不受阻


def _recent_hits(db, limit=20):
    """近 24h 关注命中快讯（hit=1 或关联自选股）。"""
    try:
        import news_ai

        since = (news_ai.now_cst() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        since = "2000-01-01 00:00"
    try:
        rows = db.execute(
            "SELECT source, ts, title, hit, COALESCE(stocks,'') FROM news "
            "WHERE created_at>=? AND (hit=1 OR (stocks IS NOT NULL AND stocks!='')) "
            "ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
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
        lines += ["| 市场 | 指数 | 点位 | 涨跌 |", "| --- | --- | --- | --- |"]
        lines += [
            "| %s | %s | %s | %s |"
            % (
                m["grp"],
                m["name"],
                ("%." + str(m["dec"]) + "f") % m["price"],
                _pct_cell(m.get("pct")),
            )
            for m in idx
        ]
    if watch:
        lines += [
            "",
            "### 自选速览",
            "",
            "| 标的 | 现价 | 涨跌 |",
            "| --- | --- | --- |",
        ]
        lines += [
            "| %s | %s | %s |"
            % (
                m["name"],
                ("%." + str(m["dec"]) + "f") % m["price"],
                _pct_cell(m.get("pct")),
            )
            for m in watch
        ]
    return lines


def _sectors_tables(sectors, ts, top_n=6):
    """板块热力 → 广度一句话 + 领涨/领跌合并表（方向列）。"""
    if not sectors:
        return []
    up = sum(1 for s in sectors if s["pct"] > 0)
    dn = sum(1 for s in sectors if s["pct"] < 0)
    mood = (
        "普涨"
        if up * 3 > len(sectors) * 2
        else ("普跌" if up * 3 < len(sectors) else "分化")
    )
    lines = [
        "全行业 %d 个：%d 涨 / %d 跌（上涨占比 %d%%，%s）"
        % (len(sectors), up, dn, round(up * 100 / len(sectors)), mood),
        "",
        "| 方向 | 板块 | 涨跌 |",
        "| --- | --- | --- |",
    ]
    lines += [
        "| 🔴 领涨 | %s | %+.2f%% |" % (s["name"], s["pct"]) for s in sectors[:top_n]
    ]
    lines += [
        "| 🟢 领跌 | %s | %.2f%% |" % (s["name"], s["pct"])
        for s in list(reversed(sectors))[:top_n]
    ]
    return lines  # ts 由调用方拼进小节标题


_SRC_NAME = {"wallstreetcn": "见闻", "sina": "新浪", "eastmoney": "东财"}


def _hits_table(items):
    """关注命中 → 表格（时间取 HH:MM，标题前缀【关联股】）。"""
    lines = ["| 时间 | 来源 | 标题 |", "| --- | --- | --- |"]
    for it in items:
        t = it.get("ts") or ""
        t = t[-5:] if len(t) >= 5 else t  # "2026-10-14 07:21" → "07:21"
        tag = ("【%s】" % "/".join(it["stocks"])) if it.get("stocks") else ""
        lines.append(
            "| %s | %s | %s%s |"
            % (
                t,
                _SRC_NAME.get(it["source"], it["source"]),
                tag,
                (it.get("title") or "").replace("|", "／")[:70],
            )
        )
    return lines


def _report_section(db, table, title, with_sectors=False):
    lines = ["## %s" % title, ""]
    row = _latest_report(db, table)
    if not row:
        lines.append("（暂无数据，等待今日自动生成）")
        return lines
    date, ts, model, ok, markets_j, brief = row
    lines.append(
        "**%s · 生成于 %s（北京）· 模型 %s%s**"
        % (date, ts, model, "" if ok else " · ⚠ AI 降级为原文汇总")
    )
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
            st_lines = _sectors_tables(sec.get("items") or [], sec.get("ts", ""))
        except Exception:
            st_lines = []
        if st_lines:
            lines += ["", "### 行业板块热力（%s）" % sec.get("ts", ""), ""]
            lines += st_lines
    lines += ["", brief or "（无正文）"]
    return lines


def _report_meta(row):
    """→ dict 或 None。"""
    if not row:
        return None
    date, ts, model, ok, markets_j, brief = row
    try:
        markets = json.loads(markets_j or "[]")
    except Exception:
        markets = []
    return {
        "date": date,
        "ts": ts,
        "model": model,
        "ok": ok,
        "markets": markets,
        "brief": brief or "",
    }


def _sector_items():
    try:
        with open(os.path.join(HERE, "sectors.json"), encoding="utf-8") as f:
            sec = json.load(f)
        return sec.get("items") or [], sec.get("ts", "")
    except Exception:
        return [], ""


def _compact_brief(text, limit=1600):
    """AI 正文压缩：去重复空行；超长截断。"""
    t = (text or "").strip()
    if not t:
        return "（无正文）"
    out, blank = [], 0
    for ln in t.splitlines():
        ln = ln.rstrip()
        if not ln:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    t = "\n".join(out)
    return t if len(t) <= limit else t[:limit] + "\n\n…（正文已截断）"


def build_md():
    """信息采集今日简报 · 定版编排（2026-10-26 重排）。

    结构：标题 → 口径条 → 今日速览 → ①市场速览 → ②关注命中 → ③板块热力
          → ④AI 晨报 → ⑤AI 收盘复盘 → 口径与来源。
    """
    import sys as _sys

    if HERE not in _sys.path:
        _sys.path.insert(0, HERE)
    import news_ai

    now = news_ai.now_cst().strftime("%Y-%m-%d %H:%M")
    db = sqlite3.connect(DB)
    try:
        brief = _report_meta(_latest_report(db, "ai_briefs"))
        review = _report_meta(_latest_report(db, "ai_reviews"))
        hits = _recent_hits(db)
    finally:
        db.close()

    mk = (
        review
        if (review and (not brief or str(review["date"]) >= str(brief["date"])))
        else brief
    )
    markets = (mk or {}).get("markets") or []
    sectors, sector_ts = _sector_items()
    n_up = sum(1 for x in sectors if x.get("pct", 0) > 0)
    n_dn = sum(1 for x in sectors if x.get("pct", 0) < 0)
    idx_n = sum(1 for m in markets if m.get("grp") in IDX_GRPS)
    watch_n = sum(1 for m in markets if m.get("grp") not in IDX_GRPS)

    lines = [
        "# 零界 · 信息采集今日简报",
        "",
        "> **更新** %s（北京）· **口径** 晨报 08:00 / 复盘 15:30 自动刷新 · "
        "**范围** 资讯与行情摘要（只读）· **免责** 仅信息参考，不构成投资建议" % now,
        "",
        "## 今日速览",
        "",
        "- **市场**：指数 %d 项 · 自选 %d 项（详见 ① 市场速览）" % (idx_n, watch_n),
        "- **板块**：%d 个行业，%d 涨 / %d 跌%s"
        % (len(sectors), n_up, n_dn, "（%s）" % sector_ts if sector_ts else ""),
        "- **情报**：近 24h 关注命中 **%d** 条" % len(hits),
        "- **AI**：晨报 %s · 复盘 %s"
        % (
            (brief["ts"] if brief else "未生成"),
            (review["ts"] if review else "未生成"),
        ),
        "",
        "---",
        "",
        "## ① 市场速览",
        "",
        "*数据时点：%s · 中式涨跌（红涨绿跌）*" % ((mk or {}).get("ts") or "—"),
        "",
    ]
    lines += _markets_tables(markets) or ["（暂无行情快照）"]

    lines += [
        "",
        "---",
        "",
        "## ② 今日关注命中（近 24h）",
        "",
        "*来源：新闻采集命中 + 关联自选股；按时间倒序*",
        "",
    ]
    lines += _hits_table(list(reversed(hits))) if hits else ["（近 24h 暂无命中）"]

    lines += [
        "",
        "---",
        "",
        "## ③ 行业板块热力",
        "",
        "*数据时点：%s · 领涨/领跌各取前 6*" % (sector_ts or "—"),
        "",
    ]
    lines += _sectors_tables(sectors, sector_ts, top_n=6) or ["（今日暂无板块快照）"]

    lines += ["", "---", "", "## ④ AI 金融晨报", ""]
    if brief:
        lines += [
            "*%s · 生成于 %s（北京）· 模型 %s%s*"
            % (
                brief["date"],
                brief["ts"],
                brief["model"],
                "" if brief["ok"] else " · ⚠ AI 降级为原文汇总",
            ),
            "",
        ]
        lines += [_compact_brief(brief["brief"])]
    else:
        lines += ["（今日晨报未生成）"]

    lines += ["", "---", "", "## ⑤ AI 收盘复盘", ""]
    if review:
        lines += [
            "*%s · 生成于 %s（北京）· 模型 %s%s*"
            % (
                review["date"],
                review["ts"],
                review["model"],
                "" if review["ok"] else " · ⚠ AI 降级为原文汇总",
            ),
            "",
        ]
        lines += [_compact_brief(review["brief"])]
    else:
        lines += ["（今日复盘未生成）"]

    lines += [
        "",
        "---",
        "",
        "## 口径与来源",
        "",
        "- 更新机制：每日 08:00 晨报、15:30 复盘后整体覆盖刷新（只读镜像）",
        "- 行情：指数/自选快照由晨报与复盘引擎生成；缺失项显示「—」不猜测",
        "- 板块：全行业涨跌统计（sectors.json），领涨/领跌各取前 6",
        "- 命中：近 24h 命中自选股/关键词的快讯，来源含见闻/新浪/东财",
        "- 涨跌颜色：红涨绿跌（中式）；文档生成：零界 · 信息采集域",
        "",
    ]
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
            [
                _find_cli(),
                "docs",
                "+update",
                "--as",
                "user",
                "--doc",
                doc,
                "--command",
                "overwrite",
                "--doc-format",
                "markdown",
                "--content",
                "@./_board_tmp.md",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=HERE,
        )
        out = r.stdout or ""
        return '"ok": true' in out or '"ok":true' in out
    except Exception:
        return False  # 豁免（C2批2）：失败随返回值上抛，调用方（daily_report/news_ai）已有日志
    finally:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/news_doc_board.py", exc)


if __name__ == "__main__":
    import sys

    if "--dry" in sys.argv:
        print(build_md())
    else:
        print("REFRESH", "OK" if refresh() else "FAIL")
    sys.exit(0)
