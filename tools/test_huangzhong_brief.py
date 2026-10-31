# -*- coding: utf-8 -*-
"""黄忠每日简报工具单测（tmp 夹具；不碰真实 work/ 与 deliverables）。

覆盖：
  · 正常汇总 → 简报 md 落盘、七段结构/六条表/KPI/趋势/结论齐全，JSON 摘要结构；
  · 缺 review → fail-visible（md 标注缺失），KPI 用 daily+dev_events 兜底；
  · 坏 JSON / events 坏行 → 容错标注、不中断、rc0；
  · env HUANGZHONG_EVENT_PUSH 覆盖 push_config（OFF→ON）；
  · 缺 events/incidents → 标注缺失；
  · --days 窗口过滤（过期事件不计入）；
  · CLI --json / --date 命名 / --date 非法 rc2；
  · 不写 work/（运行前后 work 目录快照一致）。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 31, 15, 0, tzinfo=TZ)
NOW_ISO = NOW.isoformat(timespec="seconds")


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_brief", _TOOLS / "huangzhong_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 夹具数据

REVIEW = {
    "ok": True, "tool": "huangzhong_review.py", "version": "v1.1 (2026-10-30)",
    "generated_at": "2026-10-31T14:54:47+08:00",
    "items": [
        {"id": "①", "title": "PASS 验收样本量", "status": "✓",
         "measured": "58/10 单", "target": "≥10 单 PASS"},
        {"id": "②", "title": "受控拒收演练", "status": "✓",
         "measured": "2 次（REJECT=1 OBSERVE=1）", "target": "≥1 次"},
        {"id": "③", "title": "误拒绝率 <5%", "status": "✓",
         "measured": "自然 REJECT 0<20；逐单复核 0 推翻（清单 1 条）",
         "target": "<5% 或逐单复核 0 推翻"},
        {"id": "④", "title": "漏放 = 0", "status": "tracking",
         "measured": "满窗 0/58 单（58 单跟踪中）", "target": "满 7 天窗口漏放=0"},
        {"id": "⑤", "title": "证据链 100% + ≤48h", "status": "✓",
         "measured": "100%（52/52）", "target": "100% + ≤48h"},
        {"id": "⑥", "title": "赵云抽查 3/3", "status": "✓",
         "measured": "3/3 一致", "target": "3 单抽查一致（3/3）"},
    ],
    "summary": {"overall_pass": False, "passed": 5, "total": 6},
    "kpi": {"window_days": 7, "runs": 59, "merge_done": 1468,
            "coverage_rate": 0.0402,
            "counts": {"PASS": 59, "REJECT": 0, "OBSERVE": 0}},
    "warnings": ["dev_events.jsonl 坏行 1 行已跳过"],
}

ACCEPTANCE = {
    "ok": True, "acceptance_id": "HZ-a1d977ae-20261031144500",
    "generated_at": "2026-10-31T14:45:00+08:00",
    "mode": "observe", "blocking": False,
    "commit": {"ok": True, "sha": "a1d977aebd7e3a8e700b6789be1aeb2404400fd0",
               "subject": "merge task/WO-GUANYU-P134-20261031"},
    "verdict": "PASS", "reasons": [],
    "evidence": {
        "baseline": {"generated_at": "2026-10-31T00:53:16+08:00",
                     "age_hours": 13.86, "stale": False},
        "redteam": {"generated_at": "2026-10-31T00:59:40+08:00",
                    "age_hours": 13.8, "stale": False},
    },
}

EVENT_LINES = [
    {"ts": "2026-10-30T13:15:00+08:00", "kind": "acceptance", "verdict": "OBSERVE",
     "commit": "aaaaaaaaaaaa", "reasons": ["证据超龄"], "baseline_age_h": 50.0,
     "redteam_rate": 1.0},
    {"ts": "2026-10-30T14:45:00+08:00", "kind": "acceptance", "verdict": "PASS",
     "commit": "bbbbbbbbbbbb", "reasons": [], "baseline_age_h": 12.0,
     "redteam_rate": 1.0},
    {"ts": "2026-10-31T09:33:41+08:00", "kind": "acceptance", "verdict": "PASS",
     "commit": "7e6dbe8a95b7", "reasons": [], "baseline_age_h": 8.67,
     "redteam_rate": 1.0},
    {"ts": "2026-10-31T14:45:00+08:00", "kind": "acceptance", "verdict": "PASS",
     "commit": "a1d977aebd7e", "reasons": [], "baseline_age_h": 13.86,
     "redteam_rate": 1.0},
    # 过期事件（--days=7 窗口外）
    {"ts": "2026-10-01T10:00:00+08:00", "kind": "acceptance", "verdict": "REJECT",
     "commit": "cccccccccccc", "reasons": ["旧事件"], "baseline_age_h": 1.0,
     "redteam_rate": 0.5},
]

INCIDENT_LINES = [
    {"ts": "2026-10-30T10:00:00+08:00", "kind": "incident", "commit": "",
     "desc": "无已知事故演示条目", "source": "test"},
    {"ts": "2026-10-31T10:00:00+08:00", "kind": "revert", "commit": "dddddddd",
     "desc": "回滚演示条目", "source": "test"},
]


# ------------------------------------------------------------------ 环境夹具

def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _write_jsonl(path: Path, rows, bad_line: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    if bad_line:
        lines.insert(1, "{bad json line")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "portal" / "data").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True)
    out = tmp_path / "out"
    # dev_events：10 条 merge_done（KPI 兜底分母）
    now = NOW
    dev_rows = [
        {"ts": (_iso(now - timedelta(hours=i + 1))), "kind": "merge_done",
         "task": "DEMO", "detail": "demo merge"} for i in range(10)]
    _write_jsonl(repo / "portal" / "data" / "dev_events.jsonl", dev_rows)
    return {"repo": repo, "work": work, "out": out}


def _iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _install_defaults(env):
    """默认完整数据集：review + acceptance + events + incidents + push_config。"""
    work = env["work"]
    _write_json(work / "review_20261031_145447.json", REVIEW)
    _write_json(work / "acceptance_a1d977ae.json", ACCEPTANCE)
    _write_jsonl(work / "events.jsonl", EVENT_LINES, bad_line=True)
    _write_jsonl(work / "incidents.jsonl", INCIDENT_LINES)
    _write_json(work / "push_config.json",
                {"enabled": False, "updated_at": "2026-10-31T12:00:00+08:00",
                 "by": "DSH-Lead"})
    # daily.jsonl（KPI 兜底用）
    _write_jsonl(work / "daily.jsonl", [
        {"ts": "2026-10-31T14:15:00+08:00", "commit": "x", "verdict": "PASS"},
        {"ts": "2026-10-31T14:45:00+08:00", "commit": "y", "verdict": "PASS"},
    ])


def _setup(mod, monkeypatch, env):
    logs = []
    monkeypatch.setattr(mod, "_log", lambda msg: logs.append(str(msg)))
    return logs


def _run(mod, env, **kwargs):
    kwargs.setdefault("work_dir", str(env["work"]))
    kwargs.setdefault("out_dir", str(env["out"]))
    kwargs.setdefault("repo_root", str(env["repo"]))
    kwargs.setdefault("now", NOW)
    return mod.run(**kwargs)


def _work_snapshot(work: Path) -> dict:
    snap = {}
    for p in sorted(work.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(work))] = (st.st_size, st.st_mtime_ns)
    return snap


# ------------------------------------------------------------------ 用例

def test_full_brief_generated(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _install_defaults(env)
    before = _work_snapshot(env["work"])

    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["report_written"] is True
    report = Path(rep["report_path"])
    assert report.is_file()
    assert report.name == "2026-10-31_黄忠每日简报_2026-10-31.md"
    text = report.read_text(encoding="utf-8")

    for header in ("## 一、六条判据", "## 二、KPI 与趋势", "## 三、最新验收单",
                   "## 四、事故台账与 B 推送开关", "## 五、异常与待办",
                   "## 六、证据引用", "## 七、生成信息与边界"):
        assert header in text
    assert "一句话结论" in text
    assert "overall_pass = False（5/6 ✓）" in text
    assert "④" in text and "tracking" in text
    assert "4.02%" in text                          # review kpi 覆盖率
    assert "58/10 单" in text                       # 六条实测
    assert "近 7 天事件趋势" in text
    assert "| 2026-10-31 | 2 | 0 | 0 | 2 |" in text   # 当日 2 条 PASS
    assert "| 2026-10-30 | 1 | 0 | 1 | 2 |" in text   # 前一日 PASS+OBSERVE
    assert "不自动推送" in text or "不推送" in text
    assert "B 推送" in text and "OFF" in text
    assert "PASS" in text and "a1d977ae" in text
    assert "事故台账" in text

    # JSON 摘要
    assert rep["kpi"]["source"] == "review.kpi"
    assert rep["events"]["total"] == 4              # 窗口内 4 条（过期 1 条被过滤）
    assert rep["events"]["counts"] == {"PASS": 3, "REJECT": 0, "OBSERVE": 1}
    assert rep["events"]["bad_lines"] == 1
    assert rep["push_state"]["enabled"] is False
    assert rep["push_state"]["source"] == "file"
    assert rep["review"]["summary"]["passed"] == 5
    assert rep["acceptance"]["verdict"] == "PASS"
    assert rep["acceptance"]["commit"] == "a1d977aebd7e"
    assert rep["incidents"]["rows"] == 2
    assert rep["conclusion"]

    # 不写 work/：工作目录快照前后一致
    assert _work_snapshot(env["work"]) == before


def test_missing_review_fail_visible_with_kpi_fallback(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    # 只有 daily + dev_events（无 review / acceptance / events / incidents）
    _write_jsonl(env["work"] / "daily.jsonl", [
        {"ts": "2026-10-31T14:15:00+08:00", "commit": "x", "verdict": "PASS"},
        {"ts": "2026-10-31T14:45:00+08:00", "commit": "y", "verdict": "PASS"},
    ])
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["review"]["status"] == "missing"
    assert rep["kpi"]["source"].startswith("fallback")
    assert rep["kpi"]["runs"] == 2
    assert rep["kpi"]["merge_done"] == 10
    assert rep["kpi"]["coverage"] == 0.2
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "缺失" in text
    assert "20.00%" in text or "20%" in text
    assert "不自动推送" in text or "不推送" in text


def test_bad_json_and_bad_events_tolerated(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    (env["work"] / "review_20261031_145447.json").write_text(
        "{bad json", encoding="utf-8")
    (env["work"] / "events.jsonl").write_text(
        'not-json\n{"ts": "2026-10-31T14:45:00+08:00", "verdict": "PASS"}\n',
        encoding="utf-8")
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["review"]["status"] == "bad"
    assert rep["events"]["bad_lines"] >= 1
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "不可读" in text or "缺失" in text


def test_push_env_override_on(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _install_defaults(env)          # 文件开关 OFF
    monkeypatch.setenv("HUANGZHONG_EVENT_PUSH", "1")
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["push_state"]["enabled"] is True
    assert rep["push_state"]["source"] == "env"
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "ON" in text
    assert "请人工确认并关闭" in text          # ON 时给出待办提示


def test_missing_events_and_incidents_flagged(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _write_json(env["work"] / "review_20261031_145447.json", REVIEW)
    _write_json(env["work"] / "acceptance_a1d977ae.json", ACCEPTANCE)
    _write_json(env["work"] / "push_config.json", {"enabled": False})
    rep = _run(mod, env)
    assert rep["ok"] is True
    assert rep["events"]["status"] == "missing"
    assert rep["incidents"]["status"] == "missing"
    text = Path(rep["report_path"]).read_text(encoding="utf-8")
    assert "events.jsonl 缺失" in text or "无事件数据" in text
    assert "事故台账缺失" in text
    assert "无已知事故" in text


def test_days_window_filters_old_events(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _write_jsonl(env["work"] / "events.jsonl", EVENT_LINES)
    # 默认 7 天 → 4 条（过期 REJECT 被过滤）
    rep7 = _run(mod, env, days=7)
    assert rep7["events"]["total"] == 4
    # 40 天 → 5 条（含过期 REJECT）
    rep40 = _run(mod, env, days=40)
    assert rep40["events"]["total"] == 5
    assert rep40["events"]["counts"]["REJECT"] == 1


def test_cli_json_date_and_rc(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _install_defaults(env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--date", "2026-11-04",
                   "--now", NOW_ISO])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["date"] == "2026-11-04"
    assert out["push"] is False
    report = Path(out["report_path"])
    assert report.is_file()
    assert report.name == "2026-10-31_黄忠每日简报_2026-11-04.md"
    assert out["events"]["total"] == 4


def test_cli_invalid_date_returns_rc2(monkeypatch, env, capsys):
    mod = _load()
    _setup(mod, monkeypatch, env)
    rc = mod.main(["--json", "--repo-root", str(env["repo"]),
                   "--work-dir", str(env["work"]),
                   "--out-dir", str(env["out"]),
                   "--date", "2026/11/04",
                   "--now", NOW_ISO])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err


def test_no_write_report_when_disabled(monkeypatch, env):
    mod = _load()
    _setup(mod, monkeypatch, env)
    _install_defaults(env)
    rep = _run(mod, env, write_report=False)
    assert rep["ok"] is True
    assert rep["report_written"] is False
    assert rep["report_path"] is None
    assert not env["out"].exists() or not list(env["out"].glob("*_黄忠每日简报_*.md"))
