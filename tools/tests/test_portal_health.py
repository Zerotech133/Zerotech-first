# -*- coding: utf-8 -*-
"""tools/tests/test_portal_health.py — 全站巡检 + 静态契约 + 系统总览（task-114）。

覆盖（任务验收 ≥3）：
  1. 页面扫描：list_pages / inspect_pages（fake fetch：200/404/超时）→ 状态/异常记录；
  2. JSON 契约：合法 JSON、关键 schema（卡数 104、样例 v4/8、daily_screening、report_panel）、
     非法 JSON/契约不符被标记；
  3. 报告 schema / MD：build_report 汇总与章节；run() 写出 4 个产物（tmp 路径）；
  4. 真实产物：portal_health_20261102.json + system_status_20261102.json + 总览 md 存在且 schema 正确。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_portal_health.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))


def _load():
    p = ROOT / "tools" / "research" / "portal_health.py"
    spec = importlib.util.spec_from_file_location("portal_health_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ------------------------------------------------------------------ 1. 页面扫描 / 路由别名
def test_route_aliases_for_extensionless_pages():
    """仅以无扩展名路由提供的页面：巡检应使用别名路由（.html 路径 404 属预期）。"""
    assert mod.PAGE_ROUTE_ALIASES == {"bt_view.html": "/btview",
                                      "bt2_view.html": "/bt2view",
                                      "sandbox.html": "/sandbox"}
    seen = []

    def fake_fetch(url, timeout=5.0):
        seen.append(url)
        return {"status": 200, "size": 100, "ms": 1.0, "error": None}

    rows = mod.inspect_pages("http://x", ["sandbox.html", "bt_view.html"], fetch_fn=fake_fetch)
    assert all(r["ok"] for r in rows)
    assert all(r.get("alias_of") for r in rows)
    assert any(url.endswith("/sandbox") for url in seen)
    assert any(url.endswith("/btview") for url in seen)
    assert not any(url.endswith(".html") for url in seen)


def test_list_pages_and_inspect_pages():
    pages = mod.list_pages()
    assert "screening.html" in pages and "kb_reports.html" in pages
    assert "selection.html" in pages and "card_review.html" in pages
    assert len(pages) >= 80

    def fake_fetch(url, timeout=5.0):
        if "ok" in url:
            return {"status": 200, "size": 1234, "ms": 5.0, "error": None}
        if "notfound" in url:
            return {"status": 404, "size": 0, "ms": 3.0, "error": "HTTPError 404"}
        return {"status": None, "size": 0, "ms": 5000.0, "error": "TimeoutError: boom"}

    rows = mod.inspect_pages("http://x", ["ok.html", "notfound.html", "timeout.html"],
                             fetch_fn=fake_fetch)
    assert [r["ok"] for r in rows] == [True, False, False]
    assert rows[0]["status"] == 200 and rows[0]["size"] == 1234
    assert rows[1]["status"] == 404 and "404" in rows[1]["error"]
    assert rows[2]["error"].startswith("TimeoutError")


# ------------------------------------------------------------------ 2. JSON 契约
def test_contract_checks(tmp_path):
    (tmp_path / "kb_learning_cards.json").write_text(
        json.dumps({"cards": [{"id": i} for i in range(104)]}), encoding="utf-8")
    (tmp_path / "kb_qa_samples.json").write_text(
        json.dumps({"schema_version": 2, "prompt_version": "v4",
                    "samples": [{"id": i} for i in range(8)]}), encoding="utf-8")
    (tmp_path / "daily_screening.json").write_text(
        json.dumps({"schema_version": "daily_screening_portal.v1", "date": "20261030",
                    "rs_intersection": [{"ts_code": "x"}] * 18}), encoding="utf-8")
    (tmp_path / "report_panel.json").write_text(
        json.dumps({"reports": [{"report_id": "R1"}], "candidates": []}), encoding="utf-8")
    (tmp_path / "other.json").write_text("{not json", encoding="utf-8")
    records = {r["file"]: r for r in mod.inspect_static(tmp_path)}
    assert records["kb_learning_cards.json"]["contract_ok"] is True
    assert records["kb_qa_samples.json"]["contract_ok"] is True
    assert records["daily_screening.json"]["contract_ok"] is True
    assert records["report_panel.json"]["contract_ok"] is True
    assert records["other.json"]["valid_json"] is False
    # 契约不符（卡数不是 104）单独目录验证
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "kb_learning_cards.json").write_text(
        json.dumps({"cards": [{"id": 1}]}), encoding="utf-8")
    (bad_dir / "kb_qa_samples.json").write_text(
        json.dumps({"schema_version": 2, "prompt_version": "v1", "samples": []}),
        encoding="utf-8")
    bad = {r["file"]: r for r in mod.inspect_static(bad_dir)}
    assert bad["kb_learning_cards.json"]["contract_ok"] is False
    assert any("cards" in x for x in bad["kb_learning_cards.json"]["issues"])
    assert bad["kb_qa_samples.json"]["contract_ok"] is False


# ------------------------------------------------------------------ 3. 报告 / run()
def test_report_and_run_outputs(tmp_path):
    pages = [{"page": "a.html", "url": "u", "status": 200, "size": 10, "ms": 1.0,
              "ok": True, "error": None},
             {"page": "b.html", "url": "u", "status": 404, "size": 0, "ms": 2.0,
              "ok": False, "error": "HTTPError 404"}]
    statics = [{"file": "x.json", "size": 5, "valid_json": True,
                "contract_ok": True, "issues": [], "type": "dict"}]
    system = {"generated_at": "2026-11-02T00:00:00+08:00", "learning_cards": 104,
              "kb_units": 516, "tools_py": 1000, "research_scripts": 100,
              "portal_pages": 93, "commits": {"count": 7284, "latest": "abc msg"},
              "daily_screening": {"date": "20261030", "rs_intersection": 18},
              "qa_samples": {"schema_version": 2, "prompt_version": "v4", "n": 8}}
    rep = mod.build_report(pages, statics, system)
    assert rep["summary"]["pages_total"] == 2 and rep["summary"]["pages_ok"] == 1
    assert rep["summary"]["pages_fail"] == 1
    md = mod.render_md(rep)
    for needle in ("页面巡检", "静态 JSON 契约", "系统总览", "异常页", "结论"):
        assert needle in md
    out = mod.run("http://x", out_dir=tmp_path / "reports",
                  deliver_dir=tmp_path / "deliver",
                  report_json=tmp_path / "reports" / "h.json",
                  report_md=tmp_path / "reports" / "h.md",
                  system_json=tmp_path / "reports" / "s.json",
                  system_md=tmp_path / "deliver" / "overview.md",
                  fetch_fn=lambda url, timeout=5.0: {"status": 200, "size": 1,
                                                     "ms": 1.0, "error": None})
    for key in ("portal_health_json", "portal_health_md", "system_status",
                "system_overview_md"):
        assert Path(out["outputs"][key]).exists(), f"缺产物 {key}"
    sj = json.loads((tmp_path / "reports" / "s.json").read_text(encoding="utf-8"))
    assert sj["schema_version"] == "system_status.v1" and sj["learning_cards"] >= 100
    # 契约不符用例：kb_learning_cards 卡数低于下限
    bad = tmp_path / "bad2"
    bad.mkdir(exist_ok=True)
    (bad / "kb_learning_cards.json").write_text(json.dumps({"cards": [{"id": 1}]}),
                                                encoding="utf-8")
    chk = mod.check_contract("kb_learning_cards.json",
                             json.loads((bad / "kb_learning_cards.json").read_text(encoding="utf-8")))
    assert chk["ok"] is False and any("cards" in x for x in chk["issues"])


# ------------------------------------------------------------------ 4. 真实产物
def test_real_outputs_schema():
    rj = ROOT / "research" / "reports" / "portal_health_20261102.json"
    sj = ROOT / "research" / "reports" / "system_status_20261102.json"
    sm = ROOT / "deliverables" / "2026-11-02_系统总览_v1.md"
    assert rj.exists() and sj.exists() and sm.exists()
    rep = json.loads(rj.read_text(encoding="utf-8"))
    assert rep["schema_version"] == "portal_health.v1"
    s = rep["summary"]
    assert s["pages_total"] >= 80 and s["static_total"] >= 30
    assert s["json_invalid"] == 0
    assert s["pages_fail"] == 0, [p for p in rep["pages"] if not p["ok"]]
    assert s["contract_fail"] == 0
    aliases = {p["page"]: p for p in rep["pages"] if p.get("alias_of")}
    assert set(aliases) == {"bt_view.html", "bt2_view.html", "sandbox.html"}
    for page, rec in aliases.items():
        assert rec["ok"] is True and rec["url"].endswith(rec["alias_of"])
    assert len(rep["pages"]) == s["pages_total"]
    system = json.loads(sj.read_text(encoding="utf-8"))
    assert system["schema_version"] == "system_status.v1"
    assert system["learning_cards"] >= 100 and system["portal_pages"] >= 80
    assert "系统总览" in sm.read_text(encoding="utf-8")
