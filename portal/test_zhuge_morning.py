# -*- coding: utf-8 -*-
# WO-OS-18 契约测试：诸葛亮晨报生成器（先红后绿）。
# 只验生成器：六源 stub → 规则引擎 → JSON 落盘；
# 零通知、零计划任务、零外部 HTTP 写，由源码禁项扫描兜底。
# 运行：python -m pytest test_zhuge_morning.py -q
import importlib
import io
import json
import re
import sys
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

BEIJING = timezone(timedelta(hours=8))
CLOCK = datetime(2026, 10, 21, 9, 0, tzinfo=BEIJING)
CLOCK_ISO = "2026-10-21T09:00:00+08:00"
DAY = "2026-10-21"

ALL_SOURCES = ("os", "home", "alerts", "notify", "tasks", "capability",
               "pushflow")  # WO-OS-20 起为七源


def _zm():
    return importlib.import_module("zhuge_morning")


def _stub_fetchers():
    """六源 stub（结构对齐 api_zhuge_brief 消费字段）。"""
    return {
        "os": lambda: {"ok": True},
        "home": lambda: {"ok": True},
        "alerts": lambda: {
            "ok": True,
            "source_health": {"sources": {
                "news": {"status": "ok", "as_of": CLOCK_ISO},
            }},
        },
        "notify": lambda: {
            "ok": True,
            "items": [{"id": "n1", "state": "blocked", "title": "阻断通知",
                       "reason": "待本人核对", "created_at": CLOCK_ISO}],
        },
        "tasks": lambda: {
            "ok": True,
            "tasks": [{"task_id": "t1", "status": "open", "title": "今日单",
                       "due": DAY, "created_at": CLOCK_ISO},
                      # WO-OS-23 起 blocked 聚合成 P2，桩需一条真 P1（逾期）才能验 top_priority
                      {"task_id": "t9", "status": "open", "title": "逾期单",
                       "due": "2026-10-19", "created_at": CLOCK_ISO}],
        },
        "capability": lambda: {"ok": True},
    }


# ── ① 写盘：文件落在 out-dir、文件名含北京日期、JSON 契约字段齐全 ──────
def test_writes_json_to_out_dir_with_beijing_day(tmp_path):
    zm = _zm()
    out = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers=_stub_fetchers())

    target = tmp_path / f"zhuge_brief_{DAY}.json"
    assert target.exists(), "应在 out-dir 生成 zhuge_brief_YYYY-MM-DD.json"
    assert out["written"] is True
    assert Path(out["path"]) == target

    data = json.loads(target.read_text(encoding="utf-8"))
    for key in ("engine", "suggestions", "gaps", "business_day",
                "generated_at", "model_invoked", "dispatched"):
        assert key in data, f"产物 JSON 缺字段 {key}"
    assert data["engine"] == "rule-based"
    assert data["model_invoked"] is False
    assert data["dispatched"] is False
    assert data["business_day"] == DAY
    assert data["generated_at"] == CLOCK_ISO
    assert isinstance(data["suggestions"], list) and data["suggestions"]
    assert isinstance(data["gaps"], list)


# ── ② 摘要诚实：建议数 / gaps 数 / 最高优先级条数 ──────────────────────
def test_summary_counts_match_payload(tmp_path):
    zm = _zm()
    out = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers=_stub_fetchers())
    data = out["brief"]
    summary = out["summary"]
    assert summary["suggestions"] == len(data["suggestions"])
    assert summary["gaps"] == len(data["gaps"])
    assert summary["top_priority"] == sum(
        1 for s in data["suggestions"] if s["priority"] == 1)
    assert summary["top_priority"] >= 1, "stub 里 notify:blocked / 逾期单应产优先级 1"


# ── ③ --dry-run：只打印不写盘（目录也不得建）───────────────────────────
def test_dry_run_writes_nothing(tmp_path):
    zm = _zm()
    out = tmp_path / "nested" / "brief"
    res = zm.generate(clock=CLOCK_ISO, out_dir=out, dry_run=True,
                      fetchers=_stub_fetchers())
    assert res["written"] is False
    assert not out.exists(), "dry-run 不得创建目录"
    assert list(tmp_path.rglob("*.json")) == []
    assert res["brief"]["suggestions"], "dry-run 仍应产出内存载荷"


# ── ④ 缺源：gaps 覆盖六源、suggestions 仍为数组（不炸不静默）───────────
def test_missing_sources_go_to_gaps_but_still_array(tmp_path):
    zm = _zm()
    res = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers={})
    data = res["brief"]
    assert {g["source"] for g in data["gaps"]} == set(ALL_SOURCES)
    assert all(g["note"].startswith("待核") for g in data["gaps"])
    assert isinstance(data["suggestions"], list) and data["suggestions"] == []
    assert (tmp_path / f"zhuge_brief_{DAY}.json").exists(), "缺源也要落盘（缺源即待核）"


# ── ⑤ 单源抛异常 → 按 None 降级进 gaps，其余源照常产建议 ───────────────
def test_broken_source_degrades_to_gap(tmp_path):
    zm = _zm()

    def _boom():
        raise RuntimeError("boom")

    fs = _stub_fetchers()
    fs["tasks"] = _boom
    data = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers=fs)["brief"]
    assert "tasks" in {g["source"] for g in data["gaps"]}
    assert any(s["source"] == "/api/notify/queue" for s in data["suggestions"])


# ── ⑥ 同日重复生成：覆盖同名文件，generated_at 随本次时钟 ───────────────
def test_same_day_regenerate_overwrites(tmp_path):
    zm = _zm()
    first = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers=_stub_fetchers())
    later = "2026-10-21T18:30:00+08:00"
    second = zm.generate(clock=later, out_dir=tmp_path, fetchers=_stub_fetchers())
    assert first["path"] == second["path"]
    files = list(tmp_path.glob("zhuge_brief_*.json"))
    assert len(files) == 1, "同日只应有一份文件"
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["generated_at"] == later


# ── ⑦ 坏 clock：显式抛错（不静默回落到当前时间）────────────────────────
@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45T09:00:00+08:00",
                                 "2026-10-21T09:00:00", "", "  "])
def test_bad_clock_raises(bad):
    zm = _zm()
    with pytest.raises(ValueError):
        zm.generate(clock=bad, out_dir=Path("unused"), fetchers={})


def test_naive_datetime_clock_raises():
    zm = _zm()
    with pytest.raises(ValueError):
        zm.generate(clock=datetime(2026, 10, 21, 9, 0), out_dir=Path("unused"),
                    fetchers={})


# ── ⑧ CLI：--dry-run 不写盘；--out-dir/--clock 生效；退出码 0 ───────────
def test_cli_dry_run_prints_without_writing(tmp_path, capsys):
    zm = _zm()
    rc = zm.main(["--dry-run", "--out-dir", str(tmp_path), "--clock", CLOCK_ISO])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in printed.lower() or "DRY-RUN" in printed
    assert not list(tmp_path.rglob("*.json"))


def test_cli_writes_and_reports_path(tmp_path, capsys):
    zm = _zm()
    rc = zm.main(["--out-dir", str(tmp_path), "--clock", CLOCK_ISO])
    printed = capsys.readouterr().out
    target = tmp_path / f"zhuge_brief_{DAY}.json"
    assert rc == 0
    assert target.exists()
    assert str(target) in printed or target.name in printed


def test_cli_bad_clock_exit_nonzero(tmp_path):
    zm = _zm()
    assert zm.main(["--dry-run", "--out-dir", str(tmp_path),
                    "--clock", "garbage"]) != 0


# ── ⑨ 默认产物路径：portal/data/zhuge_brief/（可由 --out-dir 覆盖）──────
def test_default_out_dir_is_portal_data_zhuge_brief():
    zm = _zm()
    assert Path(zm.DEFAULT_OUT_DIR).as_posix().endswith(
        "portal/data/zhuge_brief")
    assert Path(zm.DEFAULT_OUT_DIR).is_absolute()


# ── ⑩ 源码禁项扫描：零通知发送 / 零计划任务 / 零外部 HTTP 写 ────────────
def test_source_has_no_dispatch_or_schedule_wiring():
    src = (HERE / "zhuge_morning.py").read_text(encoding="utf-8")
    for banned in ("requests.", "urllib.request", "smtplib",
                   "schedules.json", "subprocess"):
        assert banned not in src, f"生成器源码不得出现 {banned}"
    for banned in ("urlopen", "http.client", "socket", "os.system", "popen"):
        assert banned not in src, f"生成器源码不得出现 {banned}"


def _code_names_and_ops(src: str) -> str:
    """去掉注释与字符串字面量后的代码骨架（避免中文说明误伤）。"""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except tokenize.TokenError:  # pragma: no cover - 语法残缺本身就说明坏了
        raise AssertionError("源码无法 tokenize")
    return " ".join(out)


def test_source_has_no_send_calls():
    src = (HERE / "zhuge_morning.py").read_text(encoding="utf-8")
    code = _code_names_and_ops(src)
    # 白名单式判断：只盯"关键调用"，注释/文档字符串里的说明不算
    hits = re.findall(r"\b\w*send\w*\b", code, flags=re.IGNORECASE)
    assert hits == [], f"代码里不得出现发送类调用/标识：{hits}"


def test_source_does_not_touch_six_sources_or_engine():
    """不修改六源与引擎本体：生成器只 import，不写盘到那些模块。"""
    src = (HERE / "zhuge_morning.py").read_text(encoding="utf-8")
    for mod in ("api_misc", "api_common", "api_notify", "api_tickets",
                "api_capability", "api_zhuge_brief"):
        assert f"import {mod}" in src or f"from {mod}" in src or mod not in src
    assert "open(" in src, "落盘应使用最小 open 写，便于审计"
    assert "__file__" in src


# ── ⑪ WO-OS-20 第七源 pushflow：注入当日失败率 → 产物 JSON 含该建议 ─────
def test_pushflow_suggestion_lands_in_artifact(tmp_path):
    zm = _zm()
    fs = _stub_fetchers()
    fs["pushflow"] = lambda: {
        "ok": True, "today": 276, "attempted": 276, "ok_count": 124,
        "fail_count": 152, "fail_rate": 0.55, "policy_suppressed": 0,
        "channels": {"openapi": {"ok": 100, "fail": 150},
                     "lark-cli": {"ok": 24, "fail": 2}},
    }
    res = zm.generate(clock=CLOCK_ISO, out_dir=tmp_path, fetchers=fs)
    data = json.loads((tmp_path / f"zhuge_brief_{DAY}.json")
                      .read_text(encoding="utf-8"))
    s = next((x for x in data["suggestions"]
              if x["id"] == "pushflow:daily_rate"), None)
    assert s is not None, "注入 pushflow 后产物应含 pushflow:daily_rate"
    assert s["priority"] == 1
    assert "今日真实投递失败率 55%" in s["reason"]
    assert "不是历史失败簇" in s["reason"]
    assert s["source"] == "/api/zhuge/brief.pushflow"
    assert res["summary"]["top_priority"] >= 2
    assert all(g["source"] != "pushflow" for g in data["gaps"])

