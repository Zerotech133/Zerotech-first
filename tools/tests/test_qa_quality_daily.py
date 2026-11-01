# -*- coding: utf-8 -*-
"""问答质量每日抽检（qa_quality_daily）契约测试 —— mock 不真调 8091/LLM。

覆盖：
  1. 历史累积 append_entry：追加/同日覆盖/rolling 7d 窗口统计；
  2. 漂移判定 drift_check：可答率<80%、guard<80%、fallback>20%、趋势下滑；
  3. 告警格式化 format_alert_text：无告警/有告警文本、含 rolling 对比；
  4. 全流程 run_daily（mock fetch）：当日汇总 + schema + 输出 JSON/MD + 历史文件；
  5. 调度产物：daily_qa_quality_task.ps1 存在且 UTF-8 BOM。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "kb_corpus" / "qa_quality_daily.py"
PS1 = REPO / "tools" / "scheduled" / "daily_qa_quality_task.ps1"
UNITS = {"aaaa1111bbbb", "cccc2222dddd"}
ANSWER = ("【事实摘录】\n"
          "【事实】M9 级覆铜板支持 224Gbps（[aaaa1111bbbb] p.2）。\n"
          "【事实】单颗 GPU 配套 PCB 价值量约为传统方案三倍（[cccc2222dddd] p.5）。\n"
          "【推断】综合看价值量提升（依据：[aaaa1111bbbb] p.2、[cccc2222dddd] p.5）。")


def _load():
    spec = importlib.util.spec_from_file_location("qa_quality_daily_under_test", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()


def envelope(answer=ANSWER, citations=None, fallback=False):
    citations = citations if citations is not None else [{"unit_id": "aaaa1111bbbb"},
                                                         {"unit_id": "cccc2222dddd"}]
    return {"ok": True,
            "meta": {"mode": "multi", "k": 8, "prompt_version": "v4", "guard_marker": "segment",
                     "citation_retry": True, "llm_called": True,
                     "guard": {"applied": True, "on_fail": "fallback"}},
            "data": {"answer": answer, "citations": citations,
                     "retrieved": [{"unit_id": "aaaa1111bbbb"}, {"unit_id": "cccc2222dddd"}],
                     "refusal": False, "fallback": fallback}}


def entry(date, ans=1.0, guard=1.0, fb=0.0):
    return {"date": date, "n": 2, "answerable_rate": ans, "guard_pass_rate": guard,
            "fallback_rate": fb, "avg_citations": 2.5,
            "avg_citation_resolvable_rate": 1.0, "avg_inference_ratio": 0.35,
            "avg_elapsed_seconds": 10.0, "avg_completeness_score": 5.0,
            "sample_ids": ["q1", "q2"]}


# ---------------------------------------------------------------------------
# 1. 历史累积 + rolling
# ---------------------------------------------------------------------------
def test_history_accumulate_and_rolling():
    history = {"entries": [], "rolling_7d": {}}
    history = m.append_entry(history, entry("2026-11-01"))
    history = m.append_entry(history, entry("2026-11-02", ans=0.5, guard=0.5, fb=0.5))
    assert len(history["entries"]) == 2
    assert history["rolling_7d"]["n_days"] == 2
    assert history["rolling_7d"]["answerable_rate"] == 0.75
    # 同日重跑覆盖（不重复累积）
    history = m.append_entry(history, entry("2026-11-02", ans=1.0, guard=1.0, fb=0.0))
    assert len(history["entries"]) == 2
    assert history["entries"][-1]["answerable_rate"] == 1.0
    # rolling 7d 窗口：8 天只取最后 7 天
    h2 = {"entries": [entry(f"2026-10-{d:02d}", ans=0.1) for d in range(1, 9)], "rolling_7d": {}}
    h2 = m.append_entry(h2, entry("2026-11-02", ans=1.0))
    assert h2["rolling_7d"]["n_days"] == 7
    assert h2["rolling_7d"]["answerable_rate"] > 0.1


# ---------------------------------------------------------------------------
# 2. 漂移判定
# ---------------------------------------------------------------------------
def test_drift_check_thresholds():
    day_bad = {"answerable_rate": 0.5, "guard_pass_rate": 0.5, "fallback_rate": 0.5}
    alerts = m.drift_check(day_bad, {"answerable_rate": 0.9})
    codes = {a["code"] for a in alerts}
    assert {"ANSWERABLE_LOW", "GUARD_LOW", "FALLBACK_HIGH", "TREND_DOWN"} <= codes
    assert all(a["level"] == "warn" for a in alerts)
    day_ok = {"answerable_rate": 1.0, "guard_pass_rate": 1.0, "fallback_rate": 0.0}
    assert m.drift_check(day_ok, {"answerable_rate": 1.0}) == []
    # 边界：恰好等于阈值不触发
    day_edge = {"answerable_rate": 0.8, "guard_pass_rate": 0.8, "fallback_rate": 0.2}
    assert m.drift_check(day_edge, {}) == []


# ---------------------------------------------------------------------------
# 3. 告警格式化
# ---------------------------------------------------------------------------
def test_format_alert_text():
    day = {"answerable_rate": 0.5, "guard_pass_rate": 0.5, "fallback_rate": 0.5, "n": 2}
    rolling = {"answerable_rate": 0.9, "guard_pass_rate": 0.9, "fallback_rate": 0.05,
               "n_days": 7}
    alerts = m.drift_check(day, rolling)
    text = m.format_alert_text("2026-11-02", day, alerts, rolling)
    assert "问答质量告警" in text and "ANSWERABLE_LOW" in text
    assert "rolling 7d" in text and "90%" in text
    ok_text = m.format_alert_text("2026-11-02", day, [], rolling)
    assert "正常" in ok_text and "告警" not in ok_text


# ---------------------------------------------------------------------------
# 4. 全流程（mock fetch）+ 输出
# ---------------------------------------------------------------------------
def test_run_daily_schema_with_mock(tmp_path):
    def fake_fetch(url, timeout=180):
        assert "mode=multi" in url and "cache=0" in url
        return 200, envelope(), 9.5

    history_path = tmp_path / "history.json"
    payload = m.run_daily(n=2, history_path=history_path, fetch_fn=fake_fetch,
                          unit_ids=UNITS)
    assert payload["kind"] == "qa_quality_daily"
    assert payload["day"]["n"] == 2 and payload["day"]["http_200"] == 2
    assert payload["day"]["answerable_rate"] == 1.0
    assert payload["config"]["llm_calls"] == 2
    assert "rolling_7d" in payload and "alerts" in payload
    jp, mp = m.write_outputs(payload, tmp_path / "d.json", tmp_path / "d.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("当日结论", "rolling 7d 趋势", "告警文本", "抽检样本"):
        assert anchor in md, anchor
    assert payload["samples"]
    # 历史文件由 CLI 落盘（run_daily 只返回），此处验证 append_entry 可累积
    history = m.append_entry(m.load_history(history_path), {
        "date": payload["date"], **{k: payload["day"][k] for k in
                                    ("n", "answerable_rate", "guard_pass_rate", "fallback_rate",
                                     "avg_citations", "avg_citation_resolvable_rate",
                                     "avg_inference_ratio", "avg_elapsed_seconds",
                                     "avg_completeness_score")},
        "sample_ids": [q["id"] for q in (payload.get("samples") or [])]})
    assert history["entries"]


# ---------------------------------------------------------------------------
# 5. 调度产物
# ---------------------------------------------------------------------------
def test_scheduled_ps1_bom():
    assert PS1.exists(), f"缺少计划任务脚本：{PS1}"
    raw = PS1.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "ps1 必须 UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    assert "qa_quality_daily.py run" in text
    assert "zero_daily_qa_quality.log" in text
    assert "exit $rc" in text
