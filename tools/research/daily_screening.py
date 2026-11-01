# -*- coding: utf-8 -*-
"""每日筛选流水线（可手动运行；不注册定时任务，dry-run 安全）。

链路（同一数据日）：
  最新交易日探测 → S01 过滤（selection_engine.build）→ S02 研究排序（已暂停，仅参考）
  → S03 H18 业绩预告事件（selection_daily_<date>.json）→ 量学四因子信号并集
  → 可选 RS overlay（--rs-mode off/A/B/C；板块=行业等权当日涨幅）→ JSON/MD。

输出：research/reports/daily_screening_<date>.{json,md}（--dry-run 不写，只打印摘要）
用法：
  python tools/research/daily_screening.py                      # 最新交易日、RS off、写文件
  python tools/research/daily_screening.py --dry-run             # 只打印摘要，不写
  python tools/research/daily_screening.py --date 20261030 --rs-mode C --rs-top 30
边界：研究输出，不构成投资建议；不调用 LLM；S02/S05 已暂停/关闭（沿用原口径）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
sys.path.insert(0, str(REPO / "tools" / "research"))
import screening_20261101 as scr  # noqa: E402
import selection_engine as se  # noqa: E402
import factor_lib as fl  # noqa: E402
import book_batch_test as bbt  # noqa: E402

OUT_DEFAULT = REPO / "research" / "reports"
SCHEMA_VERSION = "daily_screening.v1"
FACTORS = {"LX20_牛股三绝": "lx20_three_absolutes", "LX06_长阴短柱": "lx06_long_yin_short",
           "LX12_双阴出货": "lx12_double_yin_exit", "LX22_凹口淘金": "aokou"}
PANEL_START = "20260801"
S02_PICK_LIMIT = 30


# ---------------------------------------------------------------------------
# 数据加载（薄封装，测试可注入 sources 覆盖）
# ---------------------------------------------------------------------------
def latest_date() -> str:
    """最新交易日：从 daily/d_YYYYMMDD.parquet 探测。"""
    return scr.detect_latest_trade_date()


def _load_selection(date: str) -> dict:
    sel = se.build(date, S02_PICK_LIMIT)
    filt = sel.get("filters") or {}
    picks = sel.get("picks") or []
    return {
        "input": filt.get("input"),
        "eligible_after_S01": sel.get("candidates"),
        "filters_dropped": {k: v for k, v in filt.items()
                            if k not in ("input", "eligible_before_h14")},
        "picks": [{"ts_code": p.get("ts_code"), "name": p.get("name"),
                   "industry": p.get("industry"), "score": p.get("score"),
                   "reason": p.get("reason")} for p in picks[:S02_PICK_LIMIT]],
    }


def _load_h18(date: str, out_dir: Path | str = OUT_DEFAULT) -> dict:
    for base in (Path(out_dir), OUT_DEFAULT):
        p = base / f"selection_daily_{date}.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            fw = data.get("forecast_watch") or {}
            return {"rule": fw.get("rule"), "count": len(fw.get("items") or []),
                    "items": (fw.get("items") or [])[:30]}
    return {"rule": None, "count": 0, "items": [], "note": f"无 selection_daily_{date}.json"}


def _load_pinxue(date: str) -> dict:
    nm = scr.names_map()
    panel = fl.load_panel(start=PANEL_START, end=date, horizon=20, universe="tradable",
                          load_roe=False)
    signals: dict[str, list[str]] = {}
    factors: dict[str, dict] = {}
    with bbt._scoped_registry():
        for label, fname in FACTORS.items():
            df = bbt.compute_factor(fname, panel)
            if "date" in df.columns:
                df = df.set_index("date")
            row = df.loc[date] if date in df.index else None
            if row is None:
                factors[label] = {"factor": fname, "signal_count": None, "note": "无该日数据"}
                continue
            sig = row.dropna()
            sig = sig[sig != 0]
            for code in sig.index:
                signals.setdefault(str(code), []).append(label)
            factors[label] = {"factor": fname, "signal_count": int(len(sig)),
                              "sample_codes": [str(c) for c in sig.index[:10]]}
    return {"factors": factors, "signals": signals, "union_count": len(signals),
            "names": {c: (nm.get(c) or {}) for c in signals}}


# ---------------------------------------------------------------------------
# 组装 / 渲染 / 写盘（纯函数，可单测）
# ---------------------------------------------------------------------------
def assemble_payload(date: str, selection: dict, h18: dict, pinxue: dict,
                     rs_overlay: dict | None = None, *, dry_run: bool = False) -> dict:
    """把各环节结果组装为统一 payload（JSON schema = daily_screening.v1）。"""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "daily_screening",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date": date,
        "dry_run": bool(dry_run),
        "disclaimer": "研究输出，不构成投资建议；S02/S05 已暂停/关闭，RS 仅为当日相对强度。",
        "universe": {
            "input": selection.get("input"),
            "eligible_after_S01": selection.get("eligible_after_S01"),
            "filters_dropped": selection.get("filters_dropped") or {},
        },
        "S02_ranking_research_only": {
            "note": "S02 复合排序已因影子盘 -9.6% 熔断暂停；仅研究参考，不作实盘依据",
            "top": int(S02_PICK_LIMIT),
            "picks": selection.get("picks") or [],
        },
        "S03_H18_watch": h18 or {},
        "pinxue_signals": {
            "factors": pinxue.get("factors") or {},
            "union_count": int(pinxue.get("union_count") or 0),
        },
        "RS_overlay": rs_overlay if rs_overlay is not None else
        {"enabled": False, "mode": "off", "date": date,
         "note": "RS 叠加未启用（--rs-mode off，默认）"},
        "boundary_notes": [
            "S02 已暂停、S05 已关闭；量学信号为反向/规避研究线索，不可直接用于 S01 大市值组合。",
            "RS overlay：板块=行业等权当日涨幅（pct_chg 百分比数值）；A/B/C 口径见文档。",
            "本脚本不调用 LLM、不重跑已有研究；定时任务未注册（需审批后另行接入）。",
        ],
    }
    return payload


def render_md(payload: dict) -> str:
    """payload → Markdown 报告（日期/过滤统计/S02/H18/量学/量学∩RS/边界）。"""
    rs = payload.get("RS_overlay") or {}
    L = [f"# 每日筛选流水线（数据日 {payload['date']}）", ""]
    L += [f"> 生成 {payload['generated_at']}｜dry_run={payload.get('dry_run')}｜"
          f"RS 模式 {rs.get('mode', 'off')}｜**研究输出，不构成投资建议**", ""]
    u = payload.get("universe") or {}
    L += ["## 一、S01 过滤统计", "",
          f"- 输入 {u.get('input')} 只 → 过滤后合格 **{u.get('eligible_after_S01')}** 只",
          f"- 剔除明细：{json.dumps(u.get('filters_dropped') or {}, ensure_ascii=False)}", ""]
    s2 = payload.get("S02_ranking_research_only") or {}
    L += ["## 二、S02 研究排序（⚠️ 已暂停，仅参考）", "",
          (s2.get("note") or ""), "",
          "| # | 代码 | 名称 | 行业 | 分数 |", "|---|---|---|---|---|"]
    for i, p in enumerate(s2.get("picks") or [], 1):
        score = p.get("score")
        score_txt = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
        L.append(f"| {i} | {p.get('ts_code')} | {p.get('name')} | {p.get('industry') or ''} | {score_txt} |")
    L.append("")
    h18 = payload.get("S03_H18_watch") or {}
    L += [f"## 三、S03 H18 业绩预告修订事件（{h18.get('count', 0)} 条）", "",
          "| 代码 | 报告期 | 公告日 | 类型 | 方向 | Δ中值(pp) |", "|---|---|---|---|---|---|"]
    for it in h18.get("items") or []:
        L.append(f"| {it.get('ts_code')} | {it.get('end_date')} | {it.get('ann_date')} | "
                 f"{it.get('type')} | {it.get('direction')} | {it.get('delta_pp')} |")
    if not (h18.get("items") or []):
        L.append("- 无事件/无数据")
    L.append("")
    px = payload.get("pinxue_signals") or {}
    L += [f"## 四、量学四因子信号（并集 {px.get('union_count', 0)} 只）", "",
          "| 因子 | 信号数 | 样例代码 |", "|---|---|---|"]
    for label, info in (px.get("factors") or {}).items():
        codes = info.get("sample_codes") or []
        L.append(f"| {label} | {info.get('signal_count')} | {'、'.join(codes[:8])} |")
    L += ["", "## 五、量学信号 ∩ RS", ""]
    if not rs.get("enabled"):
        L += [f"- RS 未启用（`--rs-mode off`，默认）：{rs.get('note', '')}", ""]
    else:
        L += [f"- 模式 **{rs.get('mode')}**：{rs.get('criteria', '')}",
              f"- 全市场 {rs.get('universe', {}).get('stocks')} 只 / {rs.get('universe', {}).get('boards')} 行业；"
              f"**RS 通过 {rs.get('pass_count')} 只（{rs.get('pass_rate_pct')}%）**；"
              f"量学信号 {rs.get('pinxue_signal_count')} 只 → **交集 {rs.get('intersection_count')} 只**", ""]
        if rs.get("intersection"):
            L += ["| RS | 代码 | 名称 | 行业 | 个股涨幅 | 板块涨幅 | 超额(pp) | 量学信号 |",
                  "|---|---|---|---|---|---|---|---|"]
            for it in rs["intersection"]:
                L.append(f"| {it['rs_flag']} | {it['ts_code']} | {it['name']} | {it['industry']} | "
                         f"{it['pct_chg']:+.2f}% | {it['board_pct_chg']:+.2f}% | "
                         f"{it['excess_pp']:+.2f} | {'、'.join(it['signals'])} |")
        else:
            L.append("- 无「量学信号 ∩ RS」标的")
        L.append("")
    L += ["## 六、边界与未来定时接入", ""]
    L += [f"- {x}" for x in payload.get("boundary_notes") or []]
    L.append("")
    return "\n".join(L)


def write_outputs(payload: dict, out_dir: Path | str = OUT_DEFAULT) -> dict:
    """写 research/reports/daily_screening_<date>.{json,md}；返回路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"daily_screening_{payload['date']}.json"
    mp = out / f"daily_screening_{payload['date']}.md"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_md(payload), encoding="utf-8")
    return {"json": str(jp), "md": str(mp)}


def run(date: str | None = None, rs_mode: str = "off", rs_top: int = 50,
        out_dir: Path | str = OUT_DEFAULT, dry_run: bool = False,
        sources: dict | None = None) -> dict:
    """跑完整流水线；sources 可注入 {selection,h18,pinxue,daily,nm}（测试用）。"""
    from screening_20261101 import RS_MODES, build_rs_overlay, load_daily_pct, names_map

    date = date or latest_date()
    if rs_mode not in RS_MODES:
        raise ValueError(f"未知 RS 模式：{rs_mode!r}（可选 {RS_MODES}）")
    src = sources or {}
    selection = src.get("selection")
    if selection is None:
        selection = _load_selection(date)
    h18 = src.get("h18")
    if h18 is None:
        h18 = _load_h18(date, out_dir)
    pinxue = src.get("pinxue")
    if pinxue is None:
        pinxue = _load_pinxue(date)
    if rs_mode != "off":
        daily_df = src.get("daily")
        if daily_df is None:
            daily_df = load_daily_pct(date)
        nm = src.get("nm")
        if nm is None:
            nm = names_map()
        rs = build_rs_overlay(date, rs_mode, rs_top, daily_df, nm,
                              pinxue.get("signals") or {})
    else:
        rs = {"enabled": False, "mode": "off", "date": date,
              "note": "RS 叠加未启用（--rs-mode off，默认）；原筛选结果不变"}
    payload = assemble_payload(date, selection, h18, pinxue, rs, dry_run=dry_run)
    if not dry_run:
        payload["outputs"] = write_outputs(payload, out_dir)
    else:
        payload["outputs"] = None
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="每日筛选流水线（dry-run 安全；不注册定时任务）")
    ap.add_argument("--date", default=None, help="数据日 YYYYMMDD（默认最新交易日自动探测）")
    ap.add_argument("--rs-mode", default="off", choices=("off", "A", "B", "C"),
                    help="RS overlay 模式（默认 off）")
    ap.add_argument("--rs-top", type=int, default=50, help="RS 交集清单条数（默认 50；0=不限）")
    ap.add_argument("--out-dir", default=str(OUT_DEFAULT), help="输出目录（默认 research/reports）")
    ap.add_argument("--dry-run", action="store_true", help="只打印摘要，不写 JSON/MD")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = run(date=args.date, rs_mode=args.rs_mode, rs_top=args.rs_top,
                  out_dir=args.out_dir, dry_run=args.dry_run)
    rs = payload["RS_overlay"]
    summary = {
        "date": payload["date"], "dry_run": payload["dry_run"],
        "input": payload["universe"]["input"],
        "eligible_after_S01": payload["universe"]["eligible_after_S01"],
        "s02_picks": len(payload["S02_ranking_research_only"]["picks"]),
        "h18_events": payload["S03_H18_watch"].get("count"),
        "pinxue_union": payload["pinxue_signals"]["union_count"],
        "rs_mode": rs.get("mode"), "rs_pass": rs.get("pass_count"),
        "rs_intersection": rs.get("intersection_count"),
        "outputs": payload.get("outputs"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
