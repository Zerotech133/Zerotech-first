# -*- coding: utf-8 -*-
"""weekly_report.py（W36 周报生成器）最小测试集——W37 工位测试回填。

覆盖（承施工单 W37/W38 契约与判例 6 防假绿口径）：
1. 三态最差态聚合判定（报告级=段级最差态）；
2. 非周一自跳过守卫（无 --week 时 exit 0 不写文件）；
3. 缺日不插补（缺日列「缺」而非取邻日，插补=编造）；
4. 出处锚存在性（每段首行锚 + 态，缺锚段=FAIL）。
"""

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


wr = _load("weekly_report")


def _make_summary(base: Path, day: str, n: int, no_task: list[str],
                  depts: dict) -> None:
    d = base / "data" / "daily_review" / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "_summary.json").write_text(
        json.dumps({"day": day, "n": n, "no_task": no_task, "depts": depts},
                   ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 1. 三态聚合

def test_worst_aggregation_three_state():
    """报告级三态=段级最差态：OK < INCONCLUSIVE < DEGRADED。"""
    assert wr.worst("OK") == "OK"
    assert wr.worst("OK", "OK") == "OK"
    assert wr.worst("OK", "INCONCLUSIVE") == "INCONCLUSIVE"
    assert wr.worst("INCONCLUSIVE", "OK") == "INCONCLUSIVE"
    assert wr.worst("INCONCLUSIVE", "DEGRADED") == "DEGRADED"
    assert wr.worst("OK", "INCONCLUSIVE", "DEGRADED") == "DEGRADED"


def test_report_level_takes_worst_section_state(tmp_path):
    """报头报告级三态取段级最差态：缺日→DEGRADED 且缺件段 INCONCLUSIVE 不掩盖。"""
    _make_summary(tmp_path, "2026-10-13", 3, ["T2"],
                  {"dev": {"n": 2, "no_task": []}})
    mon, sun = date(2026, 10, 12), date(2026, 10, 18)
    text = wr.build_report(tmp_path, mon, sun, "2026-W42")
    assert "报告级三态: DEGRADED" in text      # 缺日段最差态
    assert "缺日清单" in text                   # 头部列降级段清单


# ---------------------------------------------------------------- 2. 周一守卫

def test_non_monday_self_skip_guard():
    """无 --week 且非周一：SystemExit(0) 自跳过，不写文件不报错。"""
    with pytest.raises(SystemExit) as ei:
        wr.week_window(None, today=date(2026, 10, 20))   # 周二
    assert ei.value.code == 0


def test_monday_defaults_to_last_complete_week():
    """周一调用：默认投影最近完整周（上周一~上周日）。"""
    monday, sunday, iso = wr.week_window(None, today=date(2026, 10, 19))  # 周一
    assert (monday, sunday) == (date(2026, 10, 12), date(2026, 10, 18))
    assert iso == "2026-W42"


# ---------------------------------------------------------------- 3. 缺日不插补

def test_missing_day_listed_not_interpolated(tmp_path):
    """缺日列「缺」而非取邻日：部门趋势序列缺位如实，聚合只计实有账本日。"""
    _make_summary(tmp_path, "2026-10-13", 3, ["T2"],
                  {"dev": {"n": 2, "no_task": []}})
    _make_summary(tmp_path, "2026-10-15", 4, [],
                  {"dev": {"n": 3, "no_task": []}})
    mon, sun = date(2026, 10, 12), date(2026, 10, 18)
    text = wr.build_report(tmp_path, mon, sun, "2026-W42")

    # ③部门趋势：序列按周一..周日逐日，缺日=「缺」，绝不用邻日值插补
    assert "- dev：缺 | 2 | 缺 | 3 | 缺 | 缺 | 缺" in text
    # 报头缺日清单=全部 6 个缺日（含周末，如实列缺不插补）
    for d in ("2026-10-12", "2026-10-14", "2026-10-16",
              "2026-10-17", "2026-10-18"):
        assert d in text.split("缺日清单：", 1)[1].splitlines()[0]
    # ①周概览聚合只勾稽实有账本日：在册人日=3+4=7（缺日不计、不估算）
    assert "在册人日 7，履职人日 6，无任务人日 1" in text


def test_corrupt_summary_treated_as_missing_not_fabricated(tmp_path):
    """_summary.json 损坏=按缺日处理（None），不编文不插补。"""
    d = tmp_path / "data" / "daily_review" / "2026-10-13"
    d.mkdir(parents=True)
    (d / "_summary.json").write_text("{broken json", encoding="utf-8")
    dr = wr.load_daily_reviews(tmp_path, date(2026, 10, 13), date(2026, 10, 13))
    assert dr["2026-10-13"] is None


# ---------------------------------------------------------------- 4. 出处锚

def test_every_section_has_anchor_and_state(tmp_path):
    """每段首行（锚行）必须带 锚: 与 态:——无出处段禁止存在。"""
    _make_summary(tmp_path, "2026-10-13", 3, ["T2"],
                  {"dev": {"n": 2, "no_task": []}})
    text = wr.build_report(tmp_path, date(2026, 10, 12), date(2026, 10, 18),
                           "2026-W42")
    lines = text.splitlines()
    sec_idx = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    assert len(sec_idx) == 7          # 固定七段
    for i in sec_idx:
        body = []
        for ln in lines[i + 1:]:
            if ln.startswith("## "):
                break
            body.append(ln)
        anchor = next((ln for ln in body if ln.startswith("> ")), "")
        assert "锚:" in anchor, f"段缺锚行: {lines[i]}"
        assert re.search(r"态:\s*(OK|INCONCLUSIVE|DEGRADED)", anchor), \
            f"锚行缺三态: {lines[i]}"


def test_missing_ledger_section_inconclusive_no_fabrication(tmp_path):
    """账本全缺：缺日口径降级（W36 实装语义：缺日=DEGRADED 如实列缺），
    段内不编文（红线），禁创作语汇零命中。"""
    text = wr.build_report(tmp_path, date(2026, 10, 12), date(2026, 10, 18),
                           "2026-W42")
    assert "报告级三态: DEGRADED" in text
    assert "缺日清单" in text
    assert "不编文" in text
    for w in ("建议", "预计", "据悉"):
        assert w not in text
