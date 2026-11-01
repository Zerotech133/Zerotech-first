# -*- coding: utf-8 -*-
"""8091 问答 HTTP 端到端验收：mock 契约测试（不真调 8091/LLM）。

覆盖：
  1. 选样：retrieval 3 / single 2 / multi 3，不重复、覆盖多主题；
  2. 统计 summarize：HTTP 200、multi 灰度字段（prompt_version/guard_marker/citation_retry）、
     边界用例通过计数；
  3. 结论判定：全通过→go；边界失败→conditional/no-go；缺 v4/segment→需修项；retrieval llm_called→风险；
  4. 全流程（mock fetch_fn）→ schema/JSON/MD 落盘 + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACC_PATH = REPO / "tools" / "kb_corpus" / "qa_http_live_acceptance.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("qa_http_live_acceptance_under_test", ACC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acc = _load()


def make_case(mode, status=200, citations=2, prompt_version="v4", marker="segment",
              retry=True, fallback=False, guard_passed=True, ok=True):
    return {"url": "u", "params": {"mode": mode, "q": "问题"}, "status": status, "ok": ok,
            "error": None if ok else {"code": "INVALID_ARGUMENT"},
            "elapsed_seconds": 1.5,
            "meta": {"mode": mode, "k": 8, "clusters": 2 if mode == "multi" else 0,
                     "topk": 8, "cache_hit": False, "llm_called": mode != "retrieval",
                     "prompt_version": prompt_version, "guard_marker": marker,
                     "citation_retry": retry, "citation_retry_info": None},
            "guard": {"enabled": True, "applicable": mode == "multi",
                      "passed": guard_passed, "on_fail": "fallback"},
            "data": {"answer_length": 100, "citations": citations, "refusal": False,
                     "fallback": fallback,
                     "confidence": {"level": "high" if guard_passed else "low"}}}


def make_boundary(name, status, expected):
    return {"case": name, "status": status, "expected_status": expected,
            "pass": status == expected, "elapsed_seconds": 0.1}


# ---------------------------------------------------------------------------
# 1. 选样
# ---------------------------------------------------------------------------
def test_pick_questions_counts_and_themes():
    qs = acc.load_questions()
    assert len(qs) >= 8
    picked = acc.pick_questions(qs, 3, 2, 3)
    assert len(picked["retrieval"]) == 3
    assert len(picked["single"]) == 2
    assert len(picked["multi"]) == 3
    all_ids = [q["id"] for group in picked.values() for q in group]
    assert len(all_ids) == len(set(all_ids)), "三个通道选题不应重复"
    assert all(q.get("question") for q in picked["multi"])
    with _raises(acc.AcceptanceError):
        acc.load_questions(Path("nope.json"))


# ---------------------------------------------------------------------------
# 2. 统计
# ---------------------------------------------------------------------------
def test_summarize_multi_fields_and_boundary():
    normal = [make_case("retrieval", ok=True) for _ in range(3)] + \
             [make_case("single") for _ in range(2)] + \
             [make_case("multi", fallback=True, guard_passed=False) for _ in range(1)] + \
             [make_case("multi") for _ in range(2)]
    boundary = [make_boundary("topk_zero", 400, 400),
                make_boundary("empty_q", 400, 400),
                make_boundary("invalid_mode", 400, 400)]
    s = acc.summarize(normal, boundary)
    assert s["totals"]["normal"] == 8 and s["totals"]["http_200"] == 8
    assert s["retrieval"]["http_200"] == 3 and s["retrieval"]["llm_called"] is False
    assert s["single"]["n"] == 2 and s["single"]["llm_called"] == 2
    assert s["multi"]["n"] == 3 and s["multi"]["http_200"] == 3
    assert s["multi"]["prompt_versions"] == ["v4"]
    assert s["multi"]["guard_markers"] == ["segment"]
    assert s["multi"]["citation_retry_values"] == [True, True, True]
    assert s["multi"]["guard_passed"] == 2 and s["multi"]["fallback"] == 1
    assert s["boundary"]["passed"] == 3


# ---------------------------------------------------------------------------
# 3. 结论判定
# ---------------------------------------------------------------------------
def test_build_conclusion_cases():
    ok_normal = [make_case("retrieval") for _ in range(3)] + [make_case("single") for _ in range(2)] + \
                [make_case("multi") for _ in range(3)]
    ok_boundary = [make_boundary("a", 400, 400), make_boundary("b", 400, 400),
                   make_boundary("c", 400, 400)]
    c = acc.build_conclusion(acc.summarize(ok_normal, ok_boundary))
    assert c["can_go_live"] == "go" and not c["fixes_needed"]
    assert c["risks"]

    # 边界失败 → conditional（正常路径通过）
    bad_boundary = [make_boundary("topk_zero", 200, 400), make_boundary("b", 400, 400),
                    make_boundary("c", 400, 400)]
    c2 = acc.build_conclusion(acc.summarize(ok_normal, bad_boundary))
    assert c2["can_go_live"] == "conditional" and c2["fixes_needed"]

    # 灰度字段缺失 → 需修项
    no_v4 = [make_case("retrieval") for _ in range(3)] + [make_case("single") for _ in range(2)] + \
            [make_case("multi", prompt_version="v1", marker="strict", retry=False) for _ in range(3)]
    c3 = acc.build_conclusion(acc.summarize(no_v4, ok_boundary))
    assert c3["can_go_live"] == "conditional"
    assert any("v4" in x for x in c3["fixes_needed"])
    assert any("segment" in x for x in c3["fixes_needed"])

    # 正常路径错误 → no-go
    bad = [make_case("retrieval", status=503, ok=False) for _ in range(3)] + \
          [make_case("single") for _ in range(2)] + [make_case("multi") for _ in range(3)]
    c4 = acc.build_conclusion(acc.summarize(bad, ok_boundary))
    assert c4["can_go_live"] == "no-go"


# ---------------------------------------------------------------------------
# 4. 全流程（mock fetch）+ 落盘 + CLI
# ---------------------------------------------------------------------------
def _mock_fetch_factory(calls):
    def fetch(url, timeout=30.0):
        calls.append(url)
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        mode = query.get("mode")
        if mode not in ("retrieval", "single", "multi"):
            return 400, {"ok": False, "error": {"code": "INVALID_ARGUMENT"}}, 0.1
        if not query.get("q"):
            return 400, {"ok": False, "error": {"code": "INVALID_ARGUMENT"}}, 0.1
        if mode == "retrieval" and query.get("topk") == "0":
            return 400, {"ok": False, "error": {"code": "INVALID_ARGUMENT"}}, 0.1
        data = {"answer": "答案 [u1] p.1", "citations": [{"unit_id": "u1"}],
                "confidence": {"level": "high"}, "fallback": False, "refusal": False}
        meta = {"mode": mode, "k": 8, "clusters": 2 if mode == "multi" else 0, "topk": 8,
                "llm_called": mode != "retrieval", "prompt_version": "v4",
                "guard_marker": "segment", "citation_retry": True if mode == "multi" else None,
                "citation_retry_info": {"used": False},
                "guard": {"enabled": True, "applicable": mode == "multi", "passed": True,
                          "on_fail": "fallback"}}
        return 200, {"ok": True, "data": data, "meta": meta, "error": None}, 1.2
    return fetch


def test_run_acceptance_schema_with_mock(tmp_path):
    calls = []
    questions = json.loads(Path(V3_BANK).read_text(encoding="utf-8"))["questions"][:8]
    payload = acc.run_acceptance(fetch_fn=_mock_fetch_factory(calls), questions=questions,
                                 clock=lambda: 0.0)
    assert payload["kind"] == "qa_http_live_acceptance"
    assert payload["config"]["wired"] is True
    assert len(payload["cases"]["normal"]) == 8
    assert len(payload["cases"]["boundary"]) == 3
    assert payload["conclusion"]["can_go_live"] == "go"
    # 请求 URL 带正确参数
    assert any("mode=multi" in u and "topk=8" in u for u in calls)
    assert any("topk=0" in u for u in calls)
    jp, mp = acc.write_outputs(payload, tmp_path / "a.json", tmp_path / "a.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "验收矩阵", "multi 灰度字段校验", "边界参数", "逐条记录", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["summary"]["totals"]["http_200"] == 8
    proc = subprocess.run([sys.executable, "-X", "utf8", str(ACC_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
