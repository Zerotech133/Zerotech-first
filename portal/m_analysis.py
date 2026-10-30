# -*- coding: utf-8 -*-
"""分析总览 H5 payload（2026-10-26，统一分格第一批）。

把散落在 PC 页面/JSON 产物的"分析子板块"聚合为一页统一分格：
  市场环境(RPS) / 盘前要点 / 盘后复盘 / 卖点研判 / 九维候选 / 三线共振
数据来源：
  quant_backtest/reports/rps_latest.json
  8091 /premarket、/afterhours（服务端渲染 HTML → 文本）
  quant_backtest/data/sellpoint_cards.jsonl
  quant_backtest/data/nine_dim_scores.json
  quant_backtest/data/kg_resonance_last.json
"""

from __future__ import annotations

import html as _html
import json
import os
import time
from html.parser import HTMLParser
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QBT = os.path.join(ROOT, "quant_backtest")
DATA = os.path.join(QBT, "data")
REPORTS = os.path.join(QBT, "reports")
LOCAL = "http://127.0.0.1:8091"

_CACHE = {}


def _json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class _TextExtractor(HTMLParser):
    """HTML → 纯文本行（跳过 script/style/导航噪声）。"""

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "li", "p", "tr", "br", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_text(path, limit=1400, ttl=180):
    key = ("html", path)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    txt = ""
    try:
        with safe_urlopen(LOCAL + path, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
        p = _TextExtractor()
        p.feed(raw)
        text = "".join(p.parts)
        import re as _re

        _NAV = _re.compile(
            r"(返回门户|域内导航|盘前与驾驶|策略资产|研究与数据|"
            r"^指挥$|^量化$|^情报$|^创作$|^开发$|^系统$|"
            r"盘前$|盘后复盘$|数据大屏|金融驾驶舱|量化驾驶舱|"
            r"个股分析|查数$|驾驶舱$|超级助手|CowAgent|智策 OS|"
            r"AI 市场|算法市场|组织架构|策略中心|青萍之末|订阅推送|"
            r"缠论$|TBE 分层|量子双刀|预警池|三线联动|弱于板块案例|"
            r"九维$|共振$|投委会|操盘锦标赛|辩论室|卖点研判|报告$|"
            r"选股中心|板块强弱|期货量化|寻优|板块$|数据仓|"
            r"^←|^▶|^▼)"
        )
        lines = []
        for ln in text.splitlines():
            ln = " ".join(_html.unescape(ln).split())
            if len(ln) < 2:
                continue
            if _NAV.search(ln):
                continue
            emoji = sum(
                1
                for ch in ln
                if 0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF
            )
            if emoji >= 2:
                continue  # 多 emoji 行基本是菜单
            lines.append(ln)
        txt = "\n".join(lines[:28])[:limit]
    except Exception:  # noqa: BLE001
        txt = ""
    _CACHE[key] = (time.time(), txt)
    return txt


def _pct(v):
    try:
        return "%+.1f%%" % (float(v) * 100)
    except (TypeError, ValueError):
        return "—"


def _env_stats():
    d = _json(os.path.join(REPORTS, "rps_latest.json"), {}) or {}
    items = []
    for e in (d.get("envs") or [])[:3]:
        score = e.get("score")
        state = str(e.get("state") or "")
        level = (
            "ok"
            if (e.get("safe") is True)
            else ("warn" if score is not None and score >= 30 else "bad")
        )
        items.append(
            {
                "label": str(e.get("label") or "")[:12],
                "value": "%s" % (score if score is not None else "—"),
                "sub": "宽度 %s · %s" % (_pct(e.get("breadth")), state[:18] or "—"),
                "level": level,
            }
        )
    return d, items


def _sellpoint():
    stats = {"total": 0, "1卖": 0, "2卖": 0, "3卖": 0, "held": 0}
    rows = []
    _seen = set()
    try:
        with open(os.path.join(DATA, "sellpoint_cards.jsonl"), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    c = json.loads(ln)
                except ValueError:
                    continue
                key = (c.get("code"), c.get("type"), c.get("detect_date"))
                if key in _seen:
                    continue
                _seen.add(key)
                stats["total"] += 1
                t = str(c.get("type") or "")
                if t in stats:
                    stats[t] += 1
                if c.get("held"):
                    stats["held"] += 1
                rows.append(c)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/m_analysis.py", exc)
    rows = sorted(rows, key=lambda c: str(c.get("detect_date") or ""), reverse=True)[:8]
    items = [
        {
            "left": "%s %s" % (c.get("name") or "", c.get("code") or ""),
            "sub": "%s · %s · 现价 %s · %s"
            % (
                c.get("type") or "",
                c.get("detect_date") or "—",
                c.get("price") if c.get("price") is not None else "—",
                "持仓" if c.get("held") else "观察",
            ),
            "right": "TBE" if c.get("tbe") else "",
        }
        for c in rows
    ]
    return stats, items


def _nine_dim():
    d = _json(os.path.join(DATA, "nine_dim_scores.json"), {}) or {}
    rows = sorted((d.get("rows") or []), key=lambda r: -(r.get("score") or 0))[:6]
    items = [
        {
            "left": "%s %s" % (r.get("name") or "", r.get("code") or ""),
            "sub": "%s · RPS %.1f · 评分 %.1f · %s"
            % (
                r.get("industry") or "—",
                r.get("rps50") or 0,
                r.get("score") or 0,
                r.get("band") or "—",
            ),
            "right": "%+.1f%%" % (r.get("pct_chg") or 0),
        }
        for r in rows
    ]
    funnel = d.get("funnel") or {}
    return d, funnel, items


def _resonance():
    d = _json(os.path.join(DATA, "kg_resonance_last.json"), {}) or {}
    items = []
    for r in (d.get("full") or [])[:6]:
        lines = r.get("lines") or []
        items.append(
            {
                "left": "%s %s" % (r.get("name") or "", r.get("code") or ""),
                "sub": " · ".join(str(x) for x in lines[:3]) or "三线共振",
                "right": "全共振",
            }
        )
    return d, items


def analysis_payload():
    rps, env_items = _env_stats()
    pm_text = _html_text("/premarket", 1500)
    ah_text = _html_text("/afterhours", 1200)
    sp_stats, sp_items = _sellpoint()
    nd, funnel, nd_items = _nine_dim()
    kg, kg_items = _resonance()

    blocks = [
        {
            "type": "stats",
            "title": "市场环境（RPS）",
            "items": env_items,
            "note": "数据时点 %s" % (rps.get("latest") or "—"),
        },
        {
            "type": "md",
            "title": "盘前要点",
            "content": pm_text or "暂无数据",
            "note": "实时读取 /premarket",
        },
        {
            "type": "md",
            "title": "盘后复盘",
            "content": ah_text or "暂无数据",
            "note": "实时读取 /afterhours",
        },
        {
            "type": "list",
            "title": "卖点研判（最近 8 条）",
            "items": sp_items,
            "note": "共 %d 卡 · 1卖 %d / 2卖 %d / 3卖 %d · 持仓命中 %d"
            % (
                sp_stats["total"],
                sp_stats["1卖"],
                sp_stats["2卖"],
                sp_stats["3卖"],
                sp_stats["held"],
            ),
        },
        {
            "type": "list",
            "title": "九维候选（评分 TOP6）",
            "items": nd_items,
            "note": "候选池 %s · 买入 %s · 预警 %s · 生成 %s"
            % (
                funnel.get("候选池(≤400)") or "—",
                nd.get("n_buy") or 0,
                nd.get("n_alert") or 0,
                (nd.get("generated_at") or "")[:16],
            ),
        },
        {
            "type": "list",
            "title": "三线共振（全共振）",
            "items": kg_items,
            "note": "主题 %s · 窗口 %s 天 · 全文 %s 条 · 生成 %s"
            % (
                kg.get("theme") or "—",
                kg.get("window_days") or "—",
                kg.get("n_full") or 0,
                (kg.get("generated_at") or "")[:16],
            ),
        },
        {
            "type": "note",
            "content": "统一分格 · 分析总览 | 数据源：RPS/盘前/盘后/卖点/九维/三线共振；"
            "各板块详情页按同一模板逐步接入。",
        },
    ]
    return {
        "ok": True,
        "scene": "analysis",
        "title": "分析总览",
        "subtitle": "盘前盘后 · 板块环境 · 卖点 · 九维 · 三线共振",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
    }
