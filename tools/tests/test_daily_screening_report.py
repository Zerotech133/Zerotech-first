# -*- coding: utf-8 -*-
"""每日筛选正式输出：日报摘要 + 门户静态 JSON（task-101）。

本文件同时承担「导出器」职责（仅 `if __name__ == "__main__"` 时写盘，pytest 导入不写）：
  python tools/tests/test_daily_screening_report.py          # 由正式 full JSON 生成 brief + portal static
也可在代码中调用 `export_outputs(report_json, brief_path, portal_path)` 复现。

输入（正式输出，task-101 ①）：
  research/reports/daily_screening_20261030.json   （full schema daily_screening.v1）
产出：
  research/reports/daily_screening_brief_20261030.md（人类可读摘要）
  portal/static/daily_screening.json                （精简 schema，供以后页面消费；先不接页面）

测试（任务验收 ≥3）：
  1. full 报告 schema/关键数字/RS 段；
  2. 门户静态 JSON 精简 schema + 与 full 数字一致性；
  3. 日报摘要含概览/过滤/S02 top10/H18 top10/量学∩RS/边界声明，且无收益承诺类违规表述；
  4. 导出器可复现（tmp 重导出与正式文件一致）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_daily_screening_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "research" / "reports"
FULL_JSON = REPORTS / "daily_screening_20261030.json"
BRIEF_MD = REPORTS / "daily_screening_brief_20261030.md"
PORTAL_JSON = ROOT / "portal" / "static" / "daily_screening.json"
PORTAL_SCHEMA = "daily_screening_portal.v1"
FORBIDDEN_PHRASES = ("保证收益", "必涨", "稳赚", "推荐买入", "建议买入", "包赚", "无风险收益")


# ---------------------------------------------------------------------------
# 导出器（纯函数 + 写盘）
# ---------------------------------------------------------------------------
def build_summary(payload: dict) -> dict:
    rs = payload.get("RS_overlay") or {}
    return {
        "input": (payload.get("universe") or {}).get("input"),
        "eligible_after_S01": (payload.get("universe") or {}).get("eligible_after_S01"),
        "s02_top": len((payload.get("S02_ranking_research_only") or {}).get("picks") or []),
        "h18_events": (payload.get("S03_H18_watch") or {}).get("count"),
        "pinxue_union": (payload.get("pinxue_signals") or {}).get("union_count"),
        "rs_mode": rs.get("mode"),
        "rs_pass": rs.get("pass_count"),
        "rs_intersection": rs.get("intersection_count"),
    }


def build_portal(payload: dict) -> dict:
    """精简门户 schema：date/summary/s02_top(≤10)/rs_intersection/boards_top/边界。"""
    rs = payload.get("RS_overlay") or {}
    s02 = ((payload.get("S02_ranking_research_only") or {}).get("picks") or [])[:10]
    inter = (rs.get("intersection") or []) if rs.get("enabled") else []
    return {
        "schema_version": PORTAL_SCHEMA,
        "kind": "daily_screening",
        "date": payload.get("date"),
        "generated_at": payload.get("generated_at"),
        "summary": build_summary(payload),
        "s02_top": [{"ts_code": p.get("ts_code"), "name": p.get("name"),
                     "industry": p.get("industry"), "score": p.get("score")} for p in s02],
        "rs_intersection": [{
            "ts_code": it.get("ts_code"), "name": it.get("name"), "industry": it.get("industry"),
            "pct_chg": it.get("pct_chg"), "board_pct_chg": it.get("board_pct_chg"),
            "excess_pp": it.get("excess_pp"), "rs_flag": it.get("rs_flag"),
            "signals": it.get("signals") or [],
        } for it in inter],
        "boards_top": (rs.get("boards_top") or [])[:5],
        "boundary_notes": (payload.get("boundary_notes") or [])[:2],
        "disclaimer": payload.get("disclaimer"),
    }


def build_brief(payload: dict) -> str:
    """人类可读摘要：概览/过滤/S02 top10/H18 top10/量学∩RS/边界声明。"""
    rs = payload.get("RS_overlay") or {}
    u = payload.get("universe") or {}
    s2 = (payload.get("S02_ranking_research_only") or {}).get("picks") or []
    h18 = (payload.get("S03_H18_watch") or {}).get("items") or []
    pmid = payload.get("pinxue_signals") or {}
    L = [f"# 每日筛选日报 · {payload.get('date')}（RS 模式 {rs.get('mode')}）", ""]
    L.append(f"> 生成 {payload.get('generated_at')}｜**研究输出，不构成投资建议**｜"
             f"数据日 {payload.get('date')}（最新交易日）")
    L += ["", "## 一、市场概览", "",
          f"- 全市场样本 **{u.get('input')}** 只 → S01 过滤后合格 **{u.get('eligible_after_S01')}** 只",
          f"- S02 研究排序 top {len(s2)}（已暂停，仅参考）｜S03 H18 事件 **{len(h18)}** 条",
          f"- 量学四因子信号并集 **{pmid.get('union_count')}** 只",
          f"- RS 模式 **{rs.get('mode')}**：全市场通过 **{rs.get('pass_count')}** 只"
          f"（{rs.get('pass_rate_pct')}%）｜**量学信号 ∩ RS = {rs.get('intersection_count')} 只**", ""]
    if rs.get("boards_top"):
        L += ["**板块强度 Top3：** " + "；".join(
            f"{b['industry']} {b['pct_chg']:+.2f}%（{b['n']} 只）" for b in rs["boards_top"][:3]), ""]
    L += ["## 二、过滤统计", "",
          f"- 输入 {u.get('input')} 只 → 过滤后 **{u.get('eligible_after_S01')}** 只",
          f"- 剔除明细：{json.dumps(u.get('filters_dropped') or {}, ensure_ascii=False)}", ""]
    L += ["## 三、S02 研究排序 Top10（⚠️ 已暂停，仅研究参考）", "",
          "| # | 代码 | 名称 | 行业 | 分数 |", "|---|---|---|---|---|"]
    for i, p in enumerate(s2[:10], 1):
        sc = p.get("score")
        L.append(f"| {i} | {p.get('ts_code')} | {p.get('name')} | {p.get('industry') or ''} | "
                 f"{f'{sc:.3f}' if isinstance(sc, (int, float)) else sc} |")
    L += ["", "## 四、H18 业绩预告修订事件 Top10（观察，不构成买卖信号）", "",
          "| # | 代码 | 报告期 | 公告日 | 类型 | 方向 | Δ中值(pp) |", "|---|---|---|---|---|---|---|"]
    for i, it in enumerate(h18[:10], 1):
        L.append(f"| {i} | {it.get('ts_code')} | {it.get('end_date')} | {it.get('ann_date')} | "
                 f"{it.get('type')} | {it.get('direction')} | {it.get('delta_pp')} |")
    L += ["", f"## 五、量学信号 ∩ RS（{rs.get('intersection_count')} 只，按超额排序）", "",
          "| # | 代码 | 名称 | 行业 | 个股涨幅 | 板块涨幅 | 超额(pp) | RS | 量学信号 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(rs.get("intersection") or [], 1):
        L.append(f"| {i} | {it.get('ts_code')} | {it.get('name')} | {it.get('industry')} | "
                 f"{it.get('pct_chg'):+.2f}% | {it.get('board_pct_chg'):+.2f}% | "
                 f"{it.get('excess_pp'):+.2f} | {it.get('rs_flag')} | {'、'.join(it.get('signals') or [])} |")
    if not rs.get("intersection"):
        L.append("- 无「量学信号 ∩ RS」标的")
    L += ["", "## 六、边界声明", ""]
    L += [f"- {x}" for x in payload.get("boundary_notes") or []]
    L += [f"- 免责声明：{payload.get('disclaimer')}",
          "- 本日报由固定规则/本地数据生成，未调用 LLM；S02/S05 已暂停/关闭，RS 仅为当日相对强度。", ""]
    return "\n".join(L)


def export_outputs(report_json: Path | str = FULL_JSON,
                   brief_path: Path | str = BRIEF_MD,
                   portal_path: Path | str = PORTAL_JSON) -> dict:
    """由正式 full JSON 导出 brief MD + 门户精简 JSON；返回摘要。"""
    payload = json.loads(Path(report_json).read_text(encoding="utf-8"))
    brief = build_brief(payload)
    portal = build_portal(payload)
    brief_path, portal_path = Path(brief_path), Path(portal_path)
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    portal_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief, encoding="utf-8")
    portal_path.write_text(json.dumps(portal, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"brief": str(brief_path), "portal": str(portal_path),
            "summary": portal["summary"]}


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def _full() -> dict:
    assert FULL_JSON.exists(), f"缺少正式 full 报告：{FULL_JSON}"
    return json.loads(FULL_JSON.read_text(encoding="utf-8"))


def test_full_report_schema_and_numbers():
    payload = _full()
    for key in ("schema_version", "kind", "date", "universe", "S02_ranking_research_only",
                "S03_H18_watch", "pinxue_signals", "RS_overlay", "boundary_notes",
                "disclaimer"):
        assert key in payload, f"full 报告缺字段 {key}"
    assert payload["schema_version"] == "daily_screening.v1"
    assert payload["date"] == "20261030"
    u = payload["universe"]
    assert u["input"] == 5571 and u["eligible_after_S01"] == 2583
    assert len(payload["S02_ranking_research_only"]["picks"]) == 30
    assert payload["S03_H18_watch"]["count"] == 20
    assert payload["pinxue_signals"]["union_count"] == 4437
    rs = payload["RS_overlay"]
    assert rs["enabled"] is True and rs["mode"] == "C"
    assert rs["pass_count"] == 35 and rs["intersection_count"] == 18
    assert len(rs["intersection"]) == 18
    for key in ("ts_code", "name", "industry", "pct_chg", "board_pct_chg",
                "excess_pp", "rs_flag", "signals"):
        assert key in rs["intersection"][0]


def test_portal_static_schema():
    full = _full()
    portal = build_portal(full)
    assert portal["schema_version"] == PORTAL_SCHEMA
    assert portal["date"] == "20261030"
    for key in ("date", "summary", "s02_top", "rs_intersection", "boundary_notes",
                "disclaimer"):
        assert key in portal, f"门户 JSON 缺字段 {key}"
    assert portal["summary"]["input"] == 5571
    assert portal["summary"]["eligible_after_S01"] == 2583
    assert portal["summary"]["rs_intersection"] == 18
    assert len(portal["s02_top"]) <= 10
    assert len(portal["rs_intersection"]) == 18
    for key in ("ts_code", "name", "industry", "excess_pp", "rs_flag"):
        assert key in portal["rs_intersection"][0]
    # 与正式静态文件一致
    assert PORTAL_JSON.exists()
    on_disk = json.loads(PORTAL_JSON.read_text(encoding="utf-8"))
    assert on_disk["date"] == portal["date"]
    assert on_disk["summary"] == portal["summary"]
    assert len(on_disk["rs_intersection"]) == 18


def test_brief_sections_and_no_investment_advice_violation():
    full = _full()
    brief = build_brief(full)
    for needle in ("市场概览", "过滤统计", "S02 研究排序 Top10", "H18 业绩预告修订事件 Top10",
                   "量学信号 ∩ RS", "边界声明"):
        assert needle in brief, f"日报缺章节 {needle}"
    assert "不构成投资建议" in brief
    assert "18" in brief and "锦江航运" in brief
    for bad in FORBIDDEN_PHRASES:
        assert bad not in brief, f"日报含违规表述：{bad}"
    # 正式 brief 文件存在且一致
    assert BRIEF_MD.exists()
    assert BRIEF_MD.read_text(encoding="utf-8").strip() == brief.strip()


def test_export_reproducible(tmp_path):
    out = export_outputs(FULL_JSON, tmp_path / "brief.md", tmp_path / "portal.json")
    assert Path(out["brief"]).exists() and Path(out["portal"]).exists()
    brief = Path(out["brief"]).read_text(encoding="utf-8")
    portal = json.loads(Path(out["portal"]).read_text(encoding="utf-8"))
    assert PORTAL_SCHEMA == portal["schema_version"]
    assert "量学信号 ∩ RS" in brief
    assert BRIEF_MD.read_text(encoding="utf-8").strip() == brief.strip()


# ---------------------------------------------------------------------------
# CLI：生成正式输出（仅直接运行时写盘）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result = export_outputs()
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(0)
