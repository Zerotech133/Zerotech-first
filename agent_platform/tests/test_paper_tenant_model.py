# -*- coding: utf-8 -*-
"""模拟交易所多租户/账户契约纯模型测试（WO-PAPER-TENANT-20261031，Phase 0-C）。

覆盖：合法 tenant/namespace、非法 id/账本/状态、跨租户/跨账本拒绝、重复登记拒绝、
配额超限拒绝、状态机（active/suspended/closed）、official/legacy 前缀不混、
以及「纯模型」守卫（不 import 账本/引擎、无文件 IO、无 ledger 写函数）。
"""
import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform.paper import tenant_model as tm  # noqa: E402

MODEL_SRC = REPO / "agent_platform" / "paper" / "tenant_model.py"


def _tenant(tenant_id="t1", book="official", status="active", quota=None):
    return tm.Tenant(tenant_id, book=book, status=status,
                     quota=quota or tm.TenantQuota())


def _acct(tenant_id="t1", account_id="a1", combo_id=None, book="official"):
    return tm.AccountRef(tenant_id, account_id, combo_id=combo_id, book=book)


def _registry():
    reg = tm.TenantRegistry()
    reg.register_tenant(_tenant("t1"))
    reg.register_tenant(_tenant("t2"))
    return reg


# ---------------- 合法 / namespace 规则 ----------------
def test_legal_tenant_and_namespace():
    tenant = _tenant("d1", quota=tm.TenantQuota(max_accounts=3, max_combos_per_account=2))
    snap = tm.validate_tenant(tenant, [_acct("d1", "main"), _acct("d1", "main", "combo_x")])
    assert snap["schema_version"] == tm.SCHEMA_VERSION
    assert snap["namespace"] == "sim/official/d1"
    assert snap["namespace_prefix"] == "sim/official"
    assert snap["accounts"] == ("main",) and snap["combos"] == ("main/combo_x",)
    assert snap["quota"] == {"max_accounts": 3, "max_combos_per_account": 2}
    assert tenant.is_active is True


def test_namespace_shapes_and_parse_roundtrip():
    assert tm.tenant_namespace("t1") == "sim/official/t1"
    assert tm.tenant_namespace("t1", "legacy") == "sim/legacy/t1"
    assert tm.tenant_namespace("t1", "official", "a1", "combo_x") == "sim/official/t1/a1/combo_x"
    assert _acct("t1", "a1").isolation_key == "sim/official/t1/a1"

    parsed = tm.parse_namespace("sim/legacy/t1/a1/combo_x")
    assert parsed == {
        "namespace": "sim/legacy/t1/a1/combo_x",
        "book": "legacy",
        "tenant_id": "t1",
        "account_id": "a1",
        "combo_id": "combo_x",
        "account_level": False,
    }
    assert tm.parse_namespace("sim/official/t1")["account_level"] is False
    assert tm.parse_namespace("sim/official/t1/a1")["account_level"] is True


# ---------------- 非法 tenant ----------------
@pytest.mark.parametrize("bad_id", [
    "", " ", " T1", "T1", "t1 ", "t 1", "t/1", "t.1", "t\\1", "-t1", "_t1",
    "a" * 33, "official", "legacy", "sim", "paper", "root",
])
def test_illegal_tenant_id_rejected(bad_id):
    with pytest.raises(tm.TenantValidationError):
        tm.Tenant(bad_id)


@pytest.mark.parametrize("bad_tenant", [
    lambda: tm.Tenant(123),
    lambda: tm.Tenant(None),
    lambda: tm.Tenant("t1", book="paper"),
    lambda: tm.Tenant("t1", book="official2"),
    lambda: tm.Tenant("t1", status="frozen"),
    lambda: tm.Tenant("t1", quota=None),
    lambda: tm.Tenant("t1", quota=tm.TenantQuota(max_accounts=-1)),
    lambda: tm.Tenant("t1", quota=tm.TenantQuota(max_combos_per_account=1.5)),
])
def test_illegal_tenant_shape_rejected(bad_tenant):
    with pytest.raises(tm.TenantValidationError):
        bad_tenant()


def test_validate_tenant_rejects_non_tenant_or_bad_accounts():
    with pytest.raises(tm.TenantValidationError):
        tm.validate_tenant({"tenant_id": "t1"})
    with pytest.raises(tm.TenantValidationError):
        tm.validate_tenant(_tenant(), ["a1"])  # 非 AccountRef
    with pytest.raises(tm.TenantValidationError):
        tm.validate_tenant(_tenant(), "sim/official/t1")


# ---------------- 跨租户 / 跨账本 ----------------
def test_cross_tenant_account_rejected():
    # 把 t1 的账户挂到 t2 名下 → 跨租户
    with pytest.raises(tm.CrossTenantError):
        tm.validate_tenant(_tenant("t2"), [_acct("t1", "a1")])
    # 组合也必须同租户
    with pytest.raises(tm.CrossTenantError):
        tm.validate_tenant(_tenant("t2"), [_acct("t1", "a1"), _acct("t1", "a1", "combo_x")])


def test_cross_book_reference_rejected():
    official = _tenant("t1", "official")
    with pytest.raises(tm.CrossTenantError):
        tm.validate_tenant(official, [_acct("t1", "a1", book="legacy")])
    # 同一 tenant_id 可分别登记两本账，但引用不可跨账解析
    reg = tm.TenantRegistry()
    reg.register_tenant(_tenant("t1", "official"))
    reg.register_tenant(_tenant("t1", "legacy"))
    reg.register_account(_acct("t1", "a1", book="official"))
    assert [r.namespace for r in reg.accounts_of("t1", "official")] == ["sim/official/t1/a1"]
    assert reg.accounts_of("t1", "legacy") == ()
    assert reg.accounts_of("t1", "legacy") != reg.accounts_of("t1", "official")


def test_registry_cross_tenant_and_cross_book_lookup_rejected():
    reg = _registry()
    reg.register_account(_acct("t1", "a1"))
    # 借用他租户账户
    with pytest.raises(tm.CrossTenantError):
        reg.resolve("t2", "a1")
    with pytest.raises(tm.CrossTenantError):
        reg.register_combo(_acct("t2", "a1", "combo_x"))
    # t2 未登记 legacy 账 → 跨账本拒绝（而不是当成同一个人）
    with pytest.raises(tm.CrossTenantError):
        reg.get_tenant("t2", "legacy")
    assert reg.get_tenant("t1").namespace == "sim/official/t1"


# ---------------- 重复 ----------------
def test_duplicate_tenant_rejected():
    reg = _registry()
    with pytest.raises(tm.DuplicateTenantError):
        reg.register_tenant(_tenant("t1"))
    # 不同账本同 id 不算重复（两本账各自登记）
    reg.register_tenant(_tenant("t1", "legacy"))
    assert len(reg.tenants()) == 3


def test_duplicate_account_and_combo_rejected():
    reg = _registry()
    reg.register_account(_acct("t1", "a1"))
    with pytest.raises(tm.DuplicateAccountError):
        reg.register_account(_acct("t1", "a1"))
    reg.register_combo(_acct("t1", "a1", "combo_x"))
    with pytest.raises(tm.DuplicateAccountError):
        reg.register_combo(_acct("t1", "a1", "combo_x"))
    # 组合不得先于账户挂载
    with pytest.raises(tm.AccountNotFound):
        reg.register_combo(_acct("t1", "a2", "combo_y"))
    # validate_tenant 层面的重复引用同样拒绝
    with pytest.raises(tm.DuplicateAccountError):
        tm.validate_tenant(_tenant(), [_acct("t1", "a1"), _acct("t1", "a1")])


# ---------------- 配额 ----------------
def test_quota_exceeded_rejected():
    reg = tm.TenantRegistry()
    tenant = _tenant("q1", quota=tm.TenantQuota(max_accounts=2, max_combos_per_account=1))
    reg.register_tenant(tenant)
    reg.register_account(_acct("q1", "a1"))
    reg.register_account(_acct("q1", "a2"))
    with pytest.raises(tm.QuotaExceeded):
        reg.register_account(_acct("q1", "a3"))
    # 单账户组合配额
    reg.register_combo(_acct("q1", "a1", "combo_x"))
    with pytest.raises(tm.QuotaExceeded):
        reg.register_combo(_acct("q1", "a1", "combo_y"))


def test_quota_check_probe_vs_enforce():
    tenant = _tenant(quota=tm.TenantQuota(max_accounts=1, max_combos_per_account=1))
    refs = [_acct("t1", "a1"), _acct("t1", "a2")]
    status = tm.quota_check(tenant, refs)  # 探测不抛错
    assert status.ok is False and "超配额" in status.reason
    assert status.used_accounts == 2 and status.max_accounts == 1
    assert status.to_dict()["ok"] is False
    with pytest.raises(tm.QuotaExceeded):
        tm.enforce_quota(tenant, refs)
    # 未超限
    assert tm.quota_check(tenant, [_acct("t1", "a1")]).ok is True
    assert tm.quota_check(tenant, []).ok is True
    # 配额检查不吞跨租户错误
    with pytest.raises(tm.CrossTenantError):
        tm.quota_check(tenant, [_acct("t9", "a1")])


# ---------------- 状态机 ----------------
def test_status_gate_and_transitions():
    reg = tm.TenantRegistry()
    reg.register_tenant(_tenant("s1"))
    reg.register_account(_acct("s1", "a1"))
    reg.set_status("s1", "suspended")
    with pytest.raises(tm.TenantInactive):
        reg.register_account(_acct("s1", "a2"))
    with pytest.raises(tm.TenantInactive):
        reg.register_combo(_acct("s1", "a1", "combo_x"))
    # 恢复后可再开户
    reg.set_status("s1", "active")
    reg.register_account(_acct("s1", "a2"))
    # 幂等 + 终态
    assert reg.set_status("s1", "active").status == "active"
    reg.set_status("s1", "closed")
    assert reg.get_tenant("s1").status == "closed"
    with pytest.raises(tm.TenantInactive):
        reg.register_account(_acct("s1", "a3"))
    with pytest.raises(tm.TenantTransitionError):
        reg.set_status("s1", "active")
    with pytest.raises(tm.TenantValidationError):
        reg.set_status("s1", "frozen")


# ---------------- official / legacy 前缀不混 ----------------
def test_official_legacy_prefix_not_mixed():
    assert tm.namespace_prefix("official") == "sim/official"
    assert tm.namespace_prefix("legacy") == "sim/legacy"
    assert not tm.namespace_prefix("legacy").startswith(tm.namespace_prefix("official"))
    assert not tm.namespace_prefix("official").startswith(tm.namespace_prefix("legacy"))
    assert tm.Tenant("t1", "official").namespace != tm.Tenant("t1", "legacy").namespace
    assert tm.NAMESPACE_BY_BOOK == {"official": "sim/official", "legacy": "sim/legacy"}

    for bad_ns in ("sim/paper/t1", "sim/officialx/t1", "paper/official/t1",
                   "sim/official/", "sim/official//a1", "sim/official/t1/a1/x/extra",
                   "sim/official/official", "sim/official/T1"):
        with pytest.raises(tm.TenantValidationError):
            tm.parse_namespace(bad_ns)
    # 两账 namespace 互不兼容：legacy 前缀解析出的 book 恒为 legacy
    assert tm.parse_namespace(tm.tenant_namespace("t1", "legacy"))["book"] == "legacy"


def test_registry_namespace_isolation_between_books():
    reg = tm.TenantRegistry()
    reg.register_tenant(_tenant("same", "official"))
    reg.register_tenant(_tenant("same", "legacy"))
    reg.register_account(_acct("same", "a1", book="official"))
    reg.register_account(_acct("same", "a1", book="legacy"))
    official_ref = reg.resolve("same", "a1", book="official")
    legacy_ref = reg.resolve("same", "a1", book="legacy")
    assert official_ref.namespace == "sim/official/same/a1"
    assert legacy_ref.namespace == "sim/legacy/same/a1"
    assert official_ref.namespace != legacy_ref.namespace
    # 账本不同即跨账：两个引用不能互相替代
    with pytest.raises(tm.CrossTenantError):
        tm.validate_tenant(_tenant("same", "official"), [legacy_ref])


# ---------------- 纯模型守卫：不接执行 / 不写账本 ----------------
def test_pure_model_no_ledger_or_engine_dependency():
    source = MODEL_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    # 只允许纯标准库（结构定义用），任何账本/引擎/k_stock 依赖都在禁区
    assert set(imported) <= {"__future__", "re", "dataclasses"}, imported
    banned_modules = {"os", "io", "pathlib", "json", "sqlite3", "subprocess",
                      "socket", "time", "datetime", "random", "pickle", "shutil"}
    assert not (set(imported) & banned_modules)
    assert not [m for m in imported if "ledger" in m or "engine" in m or "paper_book" in m
                or "risk_gate" in m or "k_stock" in m]

    # 无文件 IO / 账本写入函数调用
    calls = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in calls and "eval" not in calls and "exec" not in calls
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for banned_attr in ("append_ledger", "apply_decision", "save_state",
                        "write_text", "write_bytes"):
        assert banned_attr not in attrs
    # 模块也不暴露任何写盘/执行入口
    for banned_name in ("append_ledger", "save_state", "apply_decision", "execute_order"):
        assert not hasattr(tm, banned_name)


def test_model_operations_leave_filesystem_untouched(tmp_path, monkeypatch):
    """跑一遍主流程，确认不产生任何文件（纯内存/纯函数）。"""
    monkeypatch.chdir(tmp_path)
    reg = tm.TenantRegistry()
    reg.register_tenant(_tenant("p1"))
    reg.register_account(_acct("p1", "a1"))
    reg.register_combo(_acct("p1", "a1", "combo_x"))
    reg.set_status("p1", "suspended")
    tm.validate_tenant(reg.get_tenant("p1"), reg.accounts_of("p1"))
    tm.quota_check(reg.get_tenant("p1"), reg.accounts_of("p1"))
    assert list(tmp_path.rglob("*")) == []


def test_registry_snapshot_is_read_only():
    reg = _registry()
    reg.register_account(_acct("t1", "a1"))
    snapshot = tm.validate_tenant(reg.get_tenant("t1"), reg.accounts_of("t1"))
    snapshot["accounts"] = ("tampered",)  # 快照是普通 dict，不反向改注册表
    assert reg.accounts_of("t1")[0].account_id == "a1"
    refs = reg.accounts_of("t1")
    assert isinstance(refs, tuple)
    with pytest.raises(AttributeError):  # frozen dataclass 不可改
        tm.Tenant("t1").tenant_id = "t2"
