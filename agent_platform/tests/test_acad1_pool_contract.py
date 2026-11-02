# -*- coding: utf-8 -*-
"""ACL-103：ACAD1 池 ODCS 契约 v1.0.0 + agentPolicy 只读校验回归（ADR-018）。

只读断言，不改实现/运行服务：
- 契约文件可解析、id/version/status 正确；
- 10+1（11）字段、必填/主键/枚举；
- 质量规则（唯一键/完整性/freshness≤24h/evidence_ref/pool_max=30/配额）；
- agentPolicy 最小集（allowedUse 3 / prohibitedUse 4 / PII / residency / evidence+human approval / maxRows）；
- 阶段诚实口径：仅登记 + 只读校验，不宣称运行时强制；
- 与 `compositions/acad1-legion/manifest.yaml` 的 contract/dataset ref 与 SLA 唯一落点一致；
- 既有 9 份契约不被替换（目录约定）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO / "agent_platform" / "catalogs" / "contracts"
CONTRACT = CONTRACTS_DIR / "v2_acad1_pool.odcs.yaml"
MANIFEST = REPO / "compositions" / "acad1-legion" / "manifest.yaml"

CONTRACT_ID = "zerobond.selection.v2_acad1_pool"
EXPECTED_FIELDS = [
    "pool_date", "market", "code", "name", "strategy", "signal_type",
    "score", "data_date", "evidence_ref", "created_at", "source_pool",
]
REQUIRED_FIELDS = {"pool_date", "market", "code", "name", "data_date", "source_pool"}
MARKET_ENUM = ["CN", "HK", "US", "CRYPTO"]
ALLOWED_USE = ["research", "selection_review", "watchlist_monitoring"]
PROHIBITED_USE = [
    "order_execution", "real_trading", "auto_approval", "personal_data_export",
]
# 既有生成器产出的核心契约（本单不得替换/误删）
CORE_CONTRACTS = (
    "daily.odcs.yaml", "adj_factor.odcs.yaml", "daily_basic.odcs.yaml",
    "index_daily.odcs.yaml", "moneyflow.odcs.yaml", "margin_detail.odcs.yaml",
)


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _properties() -> dict:
    return {p["name"]: p for p in _contract()["schema"][0]["properties"]}


def _quality_by_type() -> dict:
    out: dict = {}
    for rule in _contract().get("quality") or []:
        out.setdefault(rule.get("type"), []).append(rule)
    return out


# ── 1. 文件/标识/版本 ───────────────────────────────────────────────────────
def test_contract_file_parses_and_identity():
    assert CONTRACT.is_file(), "契约文件缺失：%s" % CONTRACT
    c = _contract()
    assert c["apiVersion"] == "v3.0.2"
    assert c["kind"] == "DataContract"
    assert c["id"] == CONTRACT_ID
    assert c["name"] == "v2_acad1_pool"
    assert c["version"] == "1.0.0"          # ADR-018 D3/P6-A：入主区即 1.0.0
    assert c["status"] == "active"           # D3：draft → active
    assert c["domain"] == "selection"
    sch = c["schema"]
    assert len(sch) == 1 and sch[0]["name"] == "v2_acad1_pool"
    # 头注释含 ADR-018 与 manifest↔ODCS 标识映射（防两套标识漂移）
    text = CONTRACT.read_text(encoding="utf-8")
    assert "ADR-018" in text
    assert "acad1-pool/v1" in text
    assert "zerobond.selection.v2_acad1_pool@1.0.0" in text


def test_contract_id_unique_among_contracts():
    """目录内 id 唯一（本契约 1 份，不与既有 9 份冲突）。"""
    ids = []
    for path in sorted(CONTRACTS_DIR.glob("*.odcs.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        ids.append(data.get("id"))
    assert ids.count(CONTRACT_ID) == 1
    assert len(ids) == len(set(ids)), "契约 id 重复：%s" % ids
    assert CONTRACT.name == CONTRACT_ID.rsplit(".", 1)[-1] + ".odcs.yaml"


# ── 2. 10+1 字段 / 必填 / 主键 / 枚举 ────────────────────────────────────────
def test_contract_fields_10_plus_1():
    props = _properties()
    assert list(props) == EXPECTED_FIELDS, "字段顺序/名称与 ADR-018 D4 不一致"
    assert len(props) == 11, "10+1 字段（10 原有 + source_pool）"
    required = {name for name, p in props.items() if p.get("required")}
    assert required == REQUIRED_FIELDS
    assert props["market"]["enum"] == MARKET_ENUM
    assert props["source_pool"]["enum"] == ["pg", "projection"]
    pks = [name for name, p in props.items() if p.get("primaryKey")]
    assert pks == ["pool_date", "market", "code"]
    # created_at / evidence_ref 为条件必填（description 必须写明条件语义）
    assert "条件必填" in props["evidence_ref"]["description"]
    assert "条件必填" in props["created_at"]["description"]


# ── 3. 质量规则 ──────────────────────────────────────────────────────────────
def test_quality_rules_uniqueness_completeness_freshness_evidence():
    q = _quality_by_type()
    assert q.get("uniqueness"), "缺唯一键规则"
    assert q["uniqueness"][0]["columns"] == ["pool_date", "market", "code"]
    assert q.get("completeness"), "缺完整性规则"
    assert q["completeness"][0]["threshold"] == 1.0
    assert set(q["completeness"][0]["columns"]) >= {
        "pool_date", "market", "code", "name", "data_date", "source_pool",
    }
    assert q.get("freshness"), "缺 freshness 规则"
    fresh = q["freshness"][0]
    assert fresh["column"] == "data_date"
    assert fresh["maxAgeHours"] == 24
    assert q.get("referential"), "缺 evidence_ref 引用规则"
    assert q["referential"][0]["column"] == "evidence_ref"
    joined = "\n".join(str(rule) for rule in _contract()["quality"])
    assert "evidence_ref" in joined and ("WATCH" in joined or "directional_decision" in joined)


def test_quality_pool_max_and_quota():
    c = _contract()
    pool = c["x-quality-pool"]
    assert pool["poolMax"] == 30
    assert pool["quota"] == {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}
    assert set(pool["quota"]) == set(MARKET_ENUM)
    assert pool["overflowBehavior"] == "error_not_truncate"
    joined = "\n".join(str(rule) for rule in c["quality"])
    assert "pool_max=30" in joined, "质量规则须显式 pool_max=30（超限不静默截断）"
    assert "CN12/HK10/US6/CRYPTO2" in joined


# ── 4. agentPolicy 最小集 + 阶段诚实口径 ─────────────────────────────────────
def test_agent_policy_minimal_set():
    pol = _contract()["x-agentPolicy"]
    assert pol["allowedUse"] == ALLOWED_USE
    assert pol["prohibitedUse"] == PROHIBITED_USE
    assert pol["pii"] == "none"
    assert pol["residency"] == "CN"
    assert pol["maxRowsPerQuery"] == 500
    assert set(pol["evidenceRequiredFor"]) == {"directional_decision", "candidate_freeze"}
    assert {"directional_decision", "candidate_freeze"} <= set(pol["humanApprovalRequired"])
    assert "人工审批" in pol["description"]


def test_enforcement_registered_only_not_runtime_claim():
    """阶段诚实口径：v1.0.0 仅登记 + 只读校验，不宣称运行时强制（ADR-018 D5-A）。"""
    enf = _contract()["x-enforcement"]
    assert enf["runtimeEnforced"] is False
    assert enf["stage"] == "registered_readonly_validation"
    assert "登记" in enf["statement"] and "只读校验" in enf["statement"]
    assert "运行时强制" in enf["statement"]
    assert enf["lintStatus"] == "not_installed"


# ── 5. 与 manifest 引用一致（contract/dataset/SLA 唯一落点） ──────────────────
def test_contract_ref_and_sla_consistent_with_manifest():
    c, m = _contract(), _manifest()
    spec = m["spec"]
    sla = spec["data"]["sla"]
    x_manifest = c["x-manifest"]
    # contract 引用点一致：manifest `acad1-pool/v1` ↔ ODCS id@version
    assert spec["data"]["contract"] == "acad1-pool/v1" == x_manifest["contractRef"]
    assert x_manifest["mapping"] == "acad1-pool/v1 ↔ %s@%s" % (c["id"], c["version"])
    # dataset ref 命名与 manifest data.requires 一致
    refs = [item["ref"] for item in spec["data"]["requires"]]
    assert x_manifest["datasetRef"] == "dataset/pg/v2_acad1_pool"
    assert x_manifest["datasetRef"] in refs
    # 契约不双写 SLA：只引用 manifest spec.data.sla，派生口径必须一致
    x_sla = c["x-slaRef"]
    assert x_sla["path"] == "spec.data.sla"
    assert set(x_sla["fields"]) == set(sla)
    assert "不复制权威值" in x_sla["note"]
    fresh = _quality_by_type()["freshness"][0]
    assert fresh["maxAgeHours"] == sla["freshness_days"] * 24
    assert c["x-quality-pool"]["poolMax"] == sla["pool_max"]
    assert c["x-freshness"]["dataDateLagDaysMax"] == sla["data_date_lag_days"]
    assert "26" in c["x-freshness"]["evidenceMaxAgeHoursRef"]


# ── 6. PIT / 双池投影口径 ────────────────────────────────────────────────────
def test_pit_and_projection_declared():
    c = _contract()
    pit = c["x-pit"]
    assert pit["dataDateField"] == "data_date"
    assert pit["poolDateField"] == "pool_date"
    assert "不得以采集时点替代" in pit["note"]
    proj = c["x-projection"]
    assert proj["role"] == "readonly_projection"
    assert proj["sourcePoolField"] == "source_pool"
    assert "stale" in proj["rule"]
    props = _properties()
    assert props["source_pool"]["required"] is True
    assert "唯一真源" in props["source_pool"]["description"]


# ── 7. 目录约定：既有契约不被本单改动/替换 ───────────────────────────────────
def test_contracts_dir_convention_and_core_contracts_kept():
    files = {p.name for p in CONTRACTS_DIR.glob("*.odcs.yaml")}
    assert CONTRACT.name in files
    for name in CORE_CONTRACTS:
        assert name in files, "既有核心契约缺失/被误删：%s" % name
    assert len(files) >= 10  # 既有 9 份 + 本契约 ≥10
    # 无 contract registry/index 文件（现状）：登记=目录文件本身；
    # 若未来新增 index/registry（非 .odcs.yaml），必须同步登记本契约（ADR-018 D2 目录统一）。
    for path in CONTRACTS_DIR.iterdir():
        if not path.is_file() or path.name.endswith(".odcs.yaml"):
            continue
        if path.name.lower().startswith(("index", "registry")):
            text = path.read_text(encoding="utf-8", errors="replace")
            assert CONTRACT_ID in text or CONTRACT.name in text, (
                "contract registry/index 未登记本契约：%s" % path.name
            )
