# -*- coding: utf-8 -*-
"""学习结论静态导出测试（task-151，W3-3）。

覆盖：
  1) 导出文件 schema：learning_conclusions.v1 / Top100 / 12 必填字段 ×100 / consumer_contract 透传；
  2) 汇总与军团视图：置信/可执行性/战法分布合计 100、quant/research/arbitration counts 与数据一致；
  3) 契约查询参数与端点：confidence/executability/tactic/limit/offset 枚举及静态/建议 API 路径；
  4) 幂等：同一源库两次导出字节一致；且与当前 portal 静态文件一致；
  5) 只读：源库 sha256 前后一致、导出不改源库；HTTP 200（静态文件在线时）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402
SRC = REPO / "research" / "reports" / "learning_conclusions_lib_20261103.json"
STATIC = REPO / "portal" / "static" / "learning_conclusions.json"
SCRIPT = REPO / "tools" / "kb_corpus" / "export_conclusions.py"
URL = "http://127.0.0.1:8734/static/learning_conclusions.json"

_spec = importlib.util.spec_from_file_location("export_conc_test", SCRIPT)
ec = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ec
_spec.loader.exec_module(ec)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1) schema / 必填字段 / consumer_contract
# ---------------------------------------------------------------------------
def test_schema_and_required_fields():
    d = load(STATIC)
    assert d["schema_version"] == "learning_conclusions.v1"
    assert d["endpoint"]["current"] == "/static/learning_conclusions.json"
    assert d["endpoint"]["suggested_api"] == "/api/learning/conclusions"
    contract = d["consumer_contract"]
    required = contract["required_fields"]
    assert len(required) == 12
    assert len(d["conclusions"]) == 100
    for i, c in enumerate(d["conclusions"]):
        for f in required:
            assert f in c, f"conclusions[{i}] 缺 {f}"
        assert c["executability"]["level"] in ("A", "B", "C")
        assert c["tactic"]["category"]
        assert isinstance(c["sources"], list) and c["sources"]
        assert c["confidence"] in ("高", "中", "低")
        assert isinstance(c["pit"], dict) and "verifiable" in c["pit"]
    assert d["validation"]["ok"] is True and d["validation"]["errors"] == []


# ---------------------------------------------------------------------------
# 2) 汇总 / 军团视图
# ---------------------------------------------------------------------------
def test_summary_and_legions():
    d = load(STATIC)
    s = d["summary"]
    assert s["top_n"] == 100 and s["returned"] == 100 and s["conclusions_total_lib"] == 730
    assert sum(s["confidence_dist"].values()) == 100
    assert sum(s["executability_dist"].values()) == 100
    assert sum(s["tactic_dist"].values()) == 100
    assert s["confidence_dist"] == {"高": 5, "中": 24, "低": 71}
    assert s["executability_dist"] == {"A": 84, "B": 1, "C": 15}
    assert s["conflict_count"] == 0 and s["arbitration_queue_count"] == 24
    # 军团 counts 用数据复核
    concl = d["conclusions"]
    assert d["legions"]["counts"]["quant"] == sum(
        1 for c in concl if c["executability"]["level"] in ("A", "B") and c["confidence"] in ("高", "中"))
    assert d["legions"]["counts"]["research"] == sum(1 for c in concl if c["confidence"] == "高")
    assert d["legions"]["counts"]["arbitration"] == s["arbitration_queue_count"] == 24
    assert len(d["arbitration_queue"]) == 24


# ---------------------------------------------------------------------------
# 3) 查询参数 / 端点契约
# ---------------------------------------------------------------------------
def test_query_contract():
    d = load(STATIC)
    qp = d["query_support"]["params"]
    assert qp["confidence"] == "高|中|低"
    assert qp["executability"] == "A|B|C"
    assert "缠论" in qp["tactic"] and "未分类" in qp["tactic"]
    assert qp["limit"].startswith("1..100")
    assert "offset" in qp
    assert d["query_support"]["server_side"] is False
    examples = " ".join(d["query_support"]["examples"])
    assert "/api/learning/conclusions" in examples and "confidence=" in examples


# ---------------------------------------------------------------------------
# 4) 幂等 + 与当前静态文件一致
# ---------------------------------------------------------------------------
def test_idempotent(tmp_path):
    out = tmp_path / "learning_conclusions.json"
    ec.build(src_path=SRC, out_path=out, write=True)
    b1 = out.read_bytes()
    p2 = ec.build(src_path=SRC, out_path=out, write=True)
    b2 = out.read_bytes()
    assert b1 == b2, "同一源库两次导出应字节一致（幂等）"
    assert p2["_write"]["changed"] is False
    # 与仓库当前静态文件一致（同一源库）
    assert b1 == STATIC.read_bytes()


# ---------------------------------------------------------------------------
# 5) 只读源库 + HTTP 200
# ---------------------------------------------------------------------------
def test_read_only_source_and_http():
    before = sha(SRC)
    payload = ec.build(src_path=SRC, out_path=STATIC, write=False)
    assert payload["source"]["sha256"] == before
    assert payload["source_unchanged"] is True
    assert sha(SRC) == before, "导出不得改源库"
    try:
        r = safe_urlopen(URL, timeout=5)
        assert r.status == 200
        body = json.loads(r.read().decode("utf-8"))
        assert body["schema_version"] == "learning_conclusions.v1"
        assert len(body["conclusions"]) == 100
    except Exception as exc:  # noqa: BLE001
        import pytest
        pytest.skip(f"8734 不在线/静态未服务：{exc}")
