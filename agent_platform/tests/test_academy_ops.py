# -*- coding: utf-8 -*-
"""研究院运营三件套测试（WO-LEGION-ACAD1-P0）。

覆盖：intake/review/weekly 产出文件与内容 / --dry-run 零写 / 缺真源退出码 2 /
事件层新 kind 已登记（research_intake/research_review/research_report）。
全部用例走 --no-emit，避免污染真实 dev_events。
"""

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "academy_ops.py"
ADAPTER = REPO / "tools" / "acad1_pool_adapter.py"
MANIFEST = REPO / "compositions" / "acad1-legion" / "manifest.yaml"
NEW_KINDS = {"research_intake", "research_review", "research_report"}


def _mkroot(tmp_path: Path) -> Path:
    academy = tmp_path / "agent_platform" / "data" / "academy"
    academy.mkdir(parents=True)
    topics = [
        {
            "topic_id": "AC-TEST-1",
            "title": "测试课题",
            "direction": "strategy_lab",
            "owner": "STRAT1",
            "sponsor": "D1",
            "question": "问题",
            "object": "对象",
            "hypothesis": "假设",
            "evidence_scope": "口径",
            "method": "回测",
            "conclusion": "待验证",
            "action": "补证",
            "risks": "风险",
            "status": "intake",
            "updated": "2026-10-27T00:00:00",
        },
        {
            "topic_id": "AC-TEST-2",
            "title": "待评审课题",
            "direction": "commercialization_lab",
            "owner": "COMM1",
            "status": "review",
            "conclusion": "暂定结论",
            "decision": "",
        },
    ]
    (academy / "research_topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (academy / "backtests.jsonl").write_text(
        json.dumps(
            {
                "topic_id": "AC-TEST-1",
                "status": "queued",
                "strategy": "LCS-1",
                "symbol": "600337",
                "data_version": "待DATA1冻结",
                "sample_in": "待登记",
                "sample_out": "待登记",
                "cost_model": "待登记",
                "go_no_go": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(root: Path, action: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(TOOL),
            action,
            "--root",
            str(root),
            "--no-emit",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_events_kinds_registered():
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import events  # noqa: WPS433

        assert NEW_KINDS.issubset(set(events.KINDS))
    finally:
        if str(REPO / "tools") in sys.path:
            sys.path.remove(str(REPO / "tools"))


def test_intake_writes_brief(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "intake")
    assert proc.returncode == 0, proc.stderr
    out = root / "agent_platform" / "data" / "academy" / "briefs"
    files = list(out.glob("*_intake.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "AC-TEST-1" in text
    assert "立项简报" in text


def test_dry_run_zero_write(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["dry_run"] is True
    assert not (root / "agent_platform" / "data" / "academy" / "reports").exists()


def test_review_lists_pending(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "review")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reviews").glob("*_review.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "AC-TEST-2" in text
    assert "评审检查表" in text
    # 数据冻结跟踪（P1）：四字段未冻结须显形，不得默认已冻结
    assert "数据冻结跟踪" in text
    assert "待DATA1冻结" in text
    assert "0/1 已冻结" in text
    assert "data_version" in text and "cost_model" in text


def test_weekly_contains_freeze_progress(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reports").glob("*_weekly.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "数据冻结" in text
    assert "0/1 已冻结" in text


def test_weekly_contains_kpi(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reports").glob("*_weekly.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "KPI 对照" in text
    assert "AC-TEST-1" not in text or "课题在册" in text


def test_missing_registry_exit_2(tmp_path):
    proc = _run(tmp_path, "intake")
    assert proc.returncode == 2
    assert "research_topics.json missing" in proc.stdout


def test_review_includes_strategy_portfolio(tmp_path):
    root = _mkroot(tmp_path)
    academy = root / "agent_platform" / "data" / "academy"
    (academy / "strategy_portfolio.json").write_text(json.dumps({
        "schema": "academy_strategy_portfolio/v1",
        "strategies": [
            {"id": "qpzm", "name": "青萍之末", "health": "green", "health_evidence": "RpsDaily rc=0"},
            {"id": "strong", "name": "板块强弱", "health": "red", "health_evidence": "rc=3"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    proc = _run(root, "review")
    assert proc.returncode == 0, proc.stderr
    files = list((academy / "reviews").glob("*_review.md"))
    text = files[0].read_text(encoding="utf-8")
    assert "策略组合" in text
    assert "strong" in text
    assert "待处理" in text and "rc=3" in text


# ── ACL-101：ACAD1 池口径（pool_date / data_date_lag）只读回归 ──────────
def _load_pool_adapter():
    spec = importlib.util.spec_from_file_location(
        "acad1_pool_adapter_under_test_ops", ADAPTER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_sla() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["spec"]["data"]["sla"]


def _fake_market_reads() -> dict:
    """合成数据仓读取结果（不落盘/不读真实 parquet），只验证口径计算。"""
    return {
        "CN": {
            "daily": {"600000.SH": {"trade_date": "20261031", "pre_close": 10.0,
                                    "close": 10.5, "pct_chg": 5.0}},
            "basic": {"600000.SH": {"name": "浦发银行", "industry": "银行",
                                    "list_date": "19991110"}},
            "namechange": {}, "daily_available": True, "basic_available": True,
        },
        "HK": {
            "daily": {"00700.HK": {"trade_date": "20261031", "pre_close": 400.0,
                                   "close": 410.0, "pct_chg": 2.5}},
            "basic": {"00700.HK": {"name": "腾讯控股", "industry": "游戏",
                                   "list_date": "20040616"}},
            "daily_available": True, "basic_available": True, "scanned_partitions": 1,
        },
        "US": {
            "daily": {"CART": {"trade_date": "20261031", "pre_close": 30.0,
                               "close": 31.0, "pct_change": 3.3}},
            "basic": {"CART": {"name": "Instacart", "industry": "电商",
                               "list_date": "20230919"}},
            "daily_available": True, "basic_available": True,
        },
    }


def _patch_pool_reads(monkeypatch):
    adapter = _load_pool_adapter()
    reads = _fake_market_reads()
    monkeypatch.setattr(adapter, "read_cn", lambda data_root, codes: reads["CN"])
    monkeypatch.setattr(
        adapter, "read_hk", lambda data_root, codes, scan_limit=0: reads["HK"]
    )
    monkeypatch.setattr(adapter, "read_us", lambda data_root, codes: reads["US"])
    return adapter


def test_pool_adapter_pool_date_data_lag_caliber(tmp_path, monkeypatch):
    adapter = _patch_pool_reads(monkeypatch)
    sla = _manifest_sla()
    assert sla["data_date_lag_days"] == 1
    cards = [
        {"market": "CN", "candidate_id": "sh600000", "name": "浦发银行"},
        {"market": "HK", "candidate_id": "00700", "name": "腾讯控股"},
        {"market": "US", "candidate_id": "CART", "name": "Instacart"},
    ]
    rep = adapter.build_report(
        tmp_path / "cards.json", tmp_path / "data", cards, "2026-11-01",
        now=dt.datetime(2026, 11, 1, 17, 0),
    )
    assert rep["readonly"] is True
    assert rep["side_effects"] == []
    assert rep["verdict"] == "pass"
    assert rep["summary"]["cards_total"] == 3
    assert rep["summary"]["both_hit"] == 3
    for card in rep["cards"]:
        # data_lag_days = pool_date - 行情最新交易日（日历日）
        assert card["data_date"] == "2026-10-31"
        assert card["data_lag_days"] == sla["data_date_lag_days"] == 1
        assert card["missing_reason"] is None
    assert "data_lag_days = pool_date" in "".join(rep["notes"])
    # --pool-date 覆盖：池数据日推进一天，同一批卡 lag 变 2 → 超 SLA 口径
    rep2 = adapter.build_report(
        tmp_path / "cards.json", tmp_path / "data", cards, "2026-11-02",
        now=dt.datetime(2026, 11, 2, 17, 0),
    )
    assert all(c["data_lag_days"] == 2 for c in rep2["cards"])
    assert all(c["data_lag_days"] > sla["data_date_lag_days"] for c in rep2["cards"])


def test_pool_adapter_missing_and_crypto_not_fake_green(tmp_path, monkeypatch):
    adapter = _patch_pool_reads(monkeypatch)
    cards = [
        {"market": "CN", "candidate_id": "sh600000"},
        {"market": "CN", "candidate_id": "sz000001"},  # 行情/基础均缺
        {"market": "CRYPTO", "candidate_id": "ETH/USDT"},
    ]
    rep = adapter.build_report(
        tmp_path / "cards.json", tmp_path / "data", cards, "2026-11-01",
        now=dt.datetime(2026, 11, 1, 17, 0),
    )
    assert rep["verdict"] == "fail"
    by_id = {c["candidate_id"]: c for c in rep["cards"]}
    assert by_id["sh600000"]["data_lag_days"] == 1
    # 缺行情不得硬编 0 天滞后（unknown 不伪造 fresh）
    assert by_id["sz000001"]["data_lag_days"] is None
    assert by_id["sz000001"]["missing_reason"] == "daily_missing+basic_missing"
    assert by_id["ETH/USDT"]["missing_reason"] == "crypto_unsupported"
    assert rep["summary"]["unsupported_markets"] == ["CRYPTO"]
    assert rep["summary"]["supported_total"] == 2
    assert len(rep["missing"]) == 2


def test_pool_adapter_missing_cards_exit_2(tmp_path):
    missing = tmp_path / "missing_cards.json"
    proc = subprocess.run(
        [
            sys.executable, "-X", "utf8", str(ADAPTER),
            "--cards", str(missing), "--data-root", str(tmp_path), "--json",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 2, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "cards 文件不存在" in payload["error"]
    assert not missing.exists()  # 只读：不创建缺文件
