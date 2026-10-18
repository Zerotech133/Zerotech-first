# -*- coding: utf-8 -*-
"""xq_label_drift -- 标签口径漂移探针测试。

全部合成分布 + tmp_path 隔离，不触生产库、不写 C:/aios/。
覆盖：同分布 STABLE / 广告 3% vs 9% ALERT / σ 正确性 / CRITICAL 升级 /
空版本跳过 / 单版本无对拍 / JSON 结构完整 / MD 报告落盘 / rule 通道排除 / NULL 归一。
"""
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tools"))

import xq_label_drift as M  # noqa: E402


def _seed(cur, version: str, event_type: str, n: int) -> None:
    cur.executemany(
        "INSERT INTO xq_status_classified (status_id, event_type, prompt_version) VALUES (?, ?, ?)",
        [("%s|%s|%d" % (version, event_type, i), event_type, version) for i in range(n)],
    )


def _db(dist_spec: list) -> sqlite3.Connection:
    """按 [(version, event_type, n)] 建内存库，返回只读连接。"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE xq_status_classified ("
        "status_id TEXT PRIMARY KEY, event_type TEXT, prompt_version TEXT)"
    )
    for version, event_type, n in dist_spec:
        _seed(cur, version, event_type, n)
    conn.commit()
    return conn


# ---------- 判定规则 ----------

def test_same_distribution_stable():
    """两版本完全同分布 → 无 ALERT，verdict STABLE。"""
    conn = _db([("v1", "广告推广", 30), ("v1", "行情观点", 70),
                ("v2", "广告推广", 30), ("v2", "行情观点", 70)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert payload["verdict"] == "STABLE"
    assert payload["pairs"][0]["alerts"] == []


def test_ad_3_vs_9_alert_warn():
    """广告 3% vs 9%（6pp 差）→ ALERT，级别 WARN（未过 10pp/3σ 临界）。"""
    conn = _db([("v1.2-re", "广告推广", 3), ("v1.2-re", "行情观点", 97),
                ("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert payload["verdict"] == "DRIFT-WARN"
    alerts = payload["pairs"][0]["alerts"]
    ad = [a for a in alerts if a["event_type"] == "广告推广"]
    assert len(ad) == 1
    assert ad[0]["level"] == "WARN"
    assert abs(ad[0]["diff_pp"] - 6.0) < 0.01


def test_sigma_multiple_correct():
    """σ 倍数：两版本 3% vs 9%，全体序列 [3, 9] 的 pstdev=3pp，diff/sigma = 2.0。"""
    conn = _db([("v1", "广告推广", 3), ("v1", "行情观点", 97),
                ("v2", "广告推广", 9), ("v2", "行情观点", 91)])
    try:
        alerts = M.drift_matrix(M.collect(conn.cursor()))
    finally:
        conn.close()
    ad = [a for a in alerts[0]["alerts"] if a["event_type"] == "广告推广"][0]
    assert abs(ad["sigma"] - 2.0) < 0.01


def test_critical_level_upgrade():
    """占比差 >10pp → CRITICAL，verdict DRIFT-CRITICAL。"""
    conn = _db([("v1", "广告推广", 5), ("v1", "行情观点", 95),
                ("v2", "广告推广", 20), ("v2", "行情观点", 80)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert payload["verdict"] == "DRIFT-CRITICAL"
    ad = [a for a in payload["pairs"][0]["alerts"] if a["event_type"] == "广告推广"]
    assert ad[0]["level"] == "CRITICAL"
    assert ad[0]["diff_pp"] > M.CRIT_DIFF_PP


# ---------- 边界与结构 ----------

def test_empty_version_pair_skipped():
    """总行数为 0 的版本（空 dict）无法算占比 → 跳过，不产生 NaN。"""
    conn = _db([("v1", "广告推广", 50), ("v1", "行情观点", 50),
                ("v2", "广告推广", 50), ("v2", "行情观点", 50)])
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM xq_status_classified WHERE prompt_version='v2'")
        conn.commit()
        payload = M.run(cur)
    finally:
        conn.close()
    # v2 变空版本 → 只剩 v1，无对拍
    assert payload["pairs"] == []
    assert payload["verdict"] == "STABLE"


def test_single_version_no_pairs():
    """单 LLM 版本无对拍 → pairs=[]，verdict STABLE。"""
    conn = _db([("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert payload["versions"] == ["v1.3"]
    assert payload["pairs"] == []
    assert payload["verdict"] == "STABLE"


def test_json_structure_complete():
    """JSON 输出结构完整：必填字段齐备且类型正确（automation 消费契约）。"""
    conn = _db([("v1.2-re", "广告推广", 3), ("v1.2-re", "行情观点", 97),
                ("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    text = json.dumps(payload, ensure_ascii=False)  # 可 JSON 序列化
    loaded = json.loads(text)
    for key in ("generated_at", "versions", "rule_versions", "pairs", "verdict"):
        assert key in loaded
    assert loaded["verdict"] in ("STABLE", "DRIFT-WARN", "DRIFT-CRITICAL")
    assert isinstance(loaded["versions"], list) and len(loaded["versions"]) == 2
    pair = loaded["pairs"][0]
    assert set(pair.keys()) >= {"a", "b", "alerts"}
    alert = pair["alerts"][0]
    for key in ("event_type", "pct_a", "pct_b", "diff_pp", "sigma", "level"):
        assert key in alert


def test_md_report_written(tmp_path):
    """MD 报告落盘：默认目录逻辑之外显式写 tmp_path，内容含版本名与 verdict。"""
    conn = _db([("v1.2-re", "广告推广", 3), ("v1.2-re", "行情观点", 97),
                ("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    report = tmp_path / "label_drift.md"
    M.write_report(payload, report)
    assert report.exists()
    content = report.read_text(encoding="utf-8")
    assert "v1.2-re" in content and "v1.3" in content
    assert payload["verdict"] in content
    assert "| 广告推广 |" in content


def test_rule_channel_excluded():
    """v*-rule 规则通道排除在版本对拍外：不进 versions、不进 pairs。"""
    conn = _db([("v1.2-re", "广告推广", 3), ("v1.2-re", "行情观点", 97),
                ("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91),
                ("v1.3-rule", "广告推广", 100)])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert "v1.3-rule" not in payload["versions"]
    assert "v1.3-rule" in payload["rule_versions"]
    for pair in payload["pairs"]:
        assert M.is_rule_version(pair["a"]) is False
        assert M.is_rule_version(pair["b"]) is False
    # 规则通道 100% 广告（v1.3 才 9%）也不触发对拍 ALERT
    assert payload["verdict"] == "DRIFT-WARN"
    assert len(payload["pairs"]) == 1


def test_null_event_type_normalized():
    """NULL event_type / prompt_version 归一为 unknown，不炸 JSON。"""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE xq_status_classified ("
        "status_id TEXT PRIMARY KEY, event_type TEXT, prompt_version TEXT)"
    )
    cur.executemany(
        "INSERT INTO xq_status_classified VALUES (?, ?, ?)",
        [("s1", None, "v1"), ("s2", "广告推广", None)],
    )
    conn.commit()
    try:
        dist = M.collect(cur)
        assert dist["v1"] == {"unknown": 1}
        assert dist["unknown"] == {"广告推广": 1}
        payload = M.run(cur)
        json.dumps(payload, ensure_ascii=False)  # 不抛异常即可
    finally:
        conn.close()


# ---------- 最小样本量门槛 ----------

def test_too_few_samples_excluded_from_pairs():
    """n < 30 的 LLM 版本不参与对拍：极小样本 100%/0% 不再产生假 CRITICAL。"""
    conn = _db([
        # v1.2 仅 2 条（复现实库极小样本噪音源）
        ("v1.2", "个股研判", 2),
        # 两个正常版本：同分布 → 无 ALERT
        ("v1.2-re", "广告推广", 30), ("v1.2-re", "行情观点", 70),
        ("v1.3", "广告推广", 30), ("v1.3", "行情观点", 70),
    ])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    assert payload["versions"] == ["v1.2-re", "v1.3"]
    assert payload["excluded_versions"] == {"v1.2": {"n": 2, "reason": "too_few_samples"}}
    for pair in payload["pairs"]:
        assert "v1.2" not in (pair["a"], pair["b"])
    assert payload["verdict"] == "STABLE"  # 同分布正常版本对拍无 ALERT


def test_excluded_versions_json_and_md_report(tmp_path):
    """excluded_versions 进 JSON 结构，MD 报告展示排除表。"""
    conn = _db([
        ("v1.2", "个股研判", 2),
        ("v1.2-re", "广告推广", 3), ("v1.2-re", "行情观点", 97),
        ("v1.3", "广告推广", 9), ("v1.3", "行情观点", 91),
    ])
    try:
        payload = M.run(conn.cursor())
    finally:
        conn.close()
    # JSON：字段存在且结构正确
    loaded = json.loads(json.dumps(payload, ensure_ascii=False))
    assert loaded["excluded_versions"]["v1.2"]["n"] == 2
    assert loaded["excluded_versions"]["v1.2"]["reason"] == "too_few_samples"
    # MD 报告：排除表落盘
    report = tmp_path / "label_drift.md"
    M.write_report(payload, report)
    content = report.read_text(encoding="utf-8")
    assert "too_few_samples" in content
    assert "| v1.2 | 2 | too_few_samples |" in content
