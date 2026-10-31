# -*- coding: utf-8 -*-
"""模拟交易所多租户/账户契约纯模型（2026-10-31 WO-PAPER-TENANT-20261031，Phase 0-C）。

为「很多人同时模拟」打底：把「谁（tenant）用哪本账（official/legacy）、在哪个
账户/组合里下单」写成可校验的**纯契约**——本模块只做身份/隔离键/配额/状态的
判定，不接执行、不写账本、不读环境、不落盘。

边界（本单硬约束，测试锁定）：
- 纯模型：无文件/网络/时间依赖，不 import ledger/engine/risk_gate/paper_book；
- 不迁移账本：official 与 legacy 只做 namespace 前缀隔离（``sim/official/...``、
  ``sim/legacy/...``），两本账的物理文件与统计口径一根毫毛都不动；
- 确定性：id 校验、状态机、配额都是纯函数，同一输入永远同一结果。

术语与隔离键（namespace = 全链路隔离键）：
- ``tenant_id``：租户（一个模拟用户/一个训练小组）——配额与状态的锚点；
- ``account_id``：租户内资金账户（隔离键第二段）；
- ``combo_id``：账户内策略组合（第三段，可选；缺省=账户级引用）；
- ``book``：账本归属——``official``=D1 官方模拟执行账、``legacy``=K-STOCK 训练
  沙盘，两账不得合并统计或互相预填（依据 agent_platform/registry/sim_exchange.json
  的 single_source_of_truth；本模块不碰两本账的物理文件）；
- namespace 形状：``sim/<book>/<tenant_id>[/<account_id>[/<combo_id>]]``。
  同一 tenant_id 可以同时登记在 official 与 legacy 两本账（同一人的训练沙盘 +
  官方执行账），但两边的 namespace 天然不同、引用不可跨账，这就是「前缀不混」。

失败语义（closed on failure，与 k_stock/paper/combo_registry.py 同判例）：
非法 id/账本/状态、跨租户/跨账本引用、重复登记、配额超限一律抛错，绝不静默
纠正或降级到默认值——错参数落单比不落单危害大。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# 契约版本：结构变更（字段语义/规则）必须升版本，读侧按版本拒绝未知结构。
SCHEMA_VERSION = 1

# namespace 根与两本账的固定前缀（不得由调用方拼接，防前缀混用）。
NAMESPACE_ROOT = "sim"
BOOKS = ("official", "legacy")
NAMESPACE_BY_BOOK = {
    "official": NAMESPACE_ROOT + "/official",
    "legacy": NAMESPACE_ROOT + "/legacy",
}

# 租户状态机：active=可开户/可挂组合；suspended=冻结（在册不动，拒绝新增）；
# closed=终态（不可恢复）。
TENANT_STATUSES = ("active", "suspended", "closed")
_STATUS_TRANSITIONS = {
    "active": ("suspended", "closed"),
    "suspended": ("active", "closed"),
    "closed": (),
}

# 保留字：这些 id 会与 namespace/账本语义撞车，tenant_id 一律拒收。
RESERVED_TENANT_IDS = frozenset({
    NAMESPACE_ROOT, "official", "legacy", "paper", "ledger", "system", "admin", "root",
})

# id 规则：小写字母/数字开头，只含小写字母/数字/下划线/短横线；禁点、斜杠、空格、
# 大写——namespace 是路径式隔离键，任何可注入分隔符的字符都必须在源头拒绝。
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAX_ID_LEN = 32
_MAX_COMBO_ID_LEN = 64


class TenantContractError(ValueError):
    """租户/账户契约失败基类（继承 ValueError，调用方兜底捕获即可）。"""


class TenantValidationError(TenantContractError):
    """tenant/id/book/status/quota 结构非法。"""


class TenantNotFound(TenantContractError):
    """租户未在该账本登记。"""


class TenantInactive(TenantContractError):
    """租户状态 suspended/closed，拒绝新增账户或组合。"""


class TenantTransitionError(TenantContractError):
    """状态迁移非法（如 closed→active）。"""


class CrossTenantError(TenantContractError):
    """跨租户或跨账本引用（两账不得合并、他人账户不得借用）。"""


class DuplicateTenantError(TenantContractError):
    """同一账本内 tenant_id 重复登记。"""


class DuplicateAccountError(TenantContractError):
    """同一租户内 account_id / (account_id, combo_id) 重复登记。"""


class AccountNotFound(TenantContractError):
    """账户/组合未在该租户下登记。"""


class QuotaExceeded(TenantContractError):
    """账户数或单账户组合数超出租户配额。"""


def _check_id(field_name, value, max_len=_MAX_ID_LEN):
    """id 段校验：类型/空白/长度/字符集；返回原值便于链式使用。"""
    if not isinstance(value, str) or isinstance(value, bool):
        raise TenantValidationError("%s 须为字符串: %r" % (field_name, value))
    if not value or value != value.strip():
        raise TenantValidationError("%s 不能为空或首尾带空白: %r" % (field_name, value))
    if len(value) > max_len:
        raise TenantValidationError("%s 超长（>%d）: %r" % (field_name, max_len, value))
    if not _ID_RE.match(value):
        raise TenantValidationError(
            "%s 非法（只允许小写字母/数字/下划线/短横线，且以字母或数字开头）: %r"
            % (field_name, value))
    return value


def _check_tenant_id(tenant_id):
    """tenant_id 额外拒收保留字（sim/official/legacy 等会与账本语义撞车）。"""
    _check_id("tenant_id", tenant_id, _MAX_ID_LEN)
    if tenant_id in RESERVED_TENANT_IDS:
        raise TenantValidationError("tenant_id 为保留字，拒收: %r" % (tenant_id,))
    return tenant_id


def _check_book(book):
    """账本归属校验：只认 official/legacy（两账物理隔离，无第三本账）。"""
    if not isinstance(book, str) or book not in BOOKS:
        raise TenantValidationError(
            "book 非法: %r（只认 %s，两账不得混用）" % (book, "/".join(BOOKS)))
    return book


def _check_status(status):
    """租户状态校验：只认 active/suspended/closed。"""
    if not isinstance(status, str) or status not in TENANT_STATUSES:
        raise TenantValidationError(
            "status 非法: %r（只认 %s）" % (status, "/".join(TENANT_STATUSES)))
    return status


def namespace_prefix(book):
    """取某本账的 namespace 前缀：official→``sim/official``、legacy→``sim/legacy``。"""
    _check_book(book)
    return NAMESPACE_BY_BOOK[book]


def tenant_namespace(tenant_id, book="official", account_id=None, combo_id=None):
    """生成隔离键 namespace（纯字符串拼接 + 逐段校验）。

    - ``tenant_namespace("t1")`` → ``sim/official/t1``；
    - ``tenant_namespace("t1", "legacy")`` → ``sim/legacy/t1``；
    - 带账户/组合时逐段追加：``sim/official/t1/a1/combo_x``。
    combo_id 必须依附 account_id；book 前缀不可手工拼接绕过本函数。
    """
    _check_tenant_id(tenant_id)
    _check_book(book)
    parts = [NAMESPACE_ROOT, book, tenant_id]
    if account_id is not None:
        _check_id("account_id", account_id)
        parts.append(account_id)
        if combo_id is not None:
            _check_id("combo_id", combo_id, _MAX_COMBO_ID_LEN)
            parts.append(combo_id)
    elif combo_id is not None:
        raise TenantValidationError("combo_id 需要 account_id 才能组成 namespace")
    return "/".join(parts)


def parse_namespace(namespace):
    """解析隔离键 → dict；根/账本前缀/各段非法一律抛错（前缀混用=硬拒绝）。

    返回 ``{namespace, book, tenant_id, account_id, combo_id, account_level}``；
    未带账户/组合时对应字段为 None。
    """
    if not isinstance(namespace, str):
        raise TenantValidationError("namespace 须为字符串: %r" % (namespace,))
    parts = namespace.split("/")
    if len(parts) not in (3, 4, 5):
        raise TenantValidationError(
            "namespace 段数非法（须 3~5 段 sim/<book>/<tenant>[/<account>[/<combo>]]）: %r"
            % (namespace,))
    if parts[0] != NAMESPACE_ROOT:
        raise TenantValidationError(
            "namespace 根须为 %r（模拟交易所唯一根）: %r" % (NAMESPACE_ROOT, namespace))
    book = parts[1]
    if book not in BOOKS:
        raise TenantValidationError(
            "namespace 两账前缀非法（只认 official/legacy，禁混用）: %r" % (namespace,))
    tenant_id = _check_tenant_id(parts[2])
    account_id = None
    combo_id = None
    if len(parts) >= 4:
        account_id = _check_id("account_id", parts[3])
    if len(parts) == 5:
        combo_id = _check_id("combo_id", parts[4], _MAX_COMBO_ID_LEN)
    return {
        "namespace": namespace,
        "book": book,
        "tenant_id": tenant_id,
        "account_id": account_id,
        "combo_id": combo_id,
        "account_level": account_id is not None and combo_id is None,
    }


@dataclass(frozen=True)
class TenantQuota:
    """租户配额：账户上限 + 单账户组合上限（0 表示冻结新增该类资源）。"""

    max_accounts: int = 8
    max_combos_per_account: int = 4

    def __post_init__(self):
        for name in ("max_accounts", "max_combos_per_account"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TenantValidationError(
                    "TenantQuota.%s 须为非负整数: %r" % (name, value))


@dataclass(frozen=True)
class Tenant:
    """租户契约（不可变）：身份 + 账本归属 + 状态 + 配额。"""

    tenant_id: str
    book: str = "official"
    status: str = "active"
    quota: TenantQuota = field(default_factory=TenantQuota)
    display_name: str = ""

    def __post_init__(self):
        _check_tenant_id(self.tenant_id)
        _check_book(self.book)
        _check_status(self.status)
        if not isinstance(self.quota, TenantQuota):
            raise TenantValidationError(
                "Tenant.quota 须为 TenantQuota: %r" % (self.quota,))
        if not isinstance(self.display_name, str):
            raise TenantValidationError(
                "Tenant.display_name 须为字符串: %r" % (self.display_name,))

    @property
    def namespace(self):
        """租户级隔离键：``sim/<book>/<tenant_id>``。"""
        return tenant_namespace(self.tenant_id, self.book)

    @property
    def is_active(self):
        """当前状态是否可开户/挂组合。"""
        return self.status == "active"


@dataclass(frozen=True)
class AccountRef:
    """账户/组合引用契约（不可变）：租户 × 账户 × 组合 × 账本，自带隔离键。"""

    tenant_id: str
    account_id: str
    combo_id: str | None = None
    book: str = "official"

    def __post_init__(self):
        _check_tenant_id(self.tenant_id)
        _check_id("account_id", self.account_id)
        if self.combo_id is not None:
            _check_id("combo_id", self.combo_id, _MAX_COMBO_ID_LEN)
        _check_book(self.book)

    @property
    def namespace(self):
        """账户/组合级隔离键（对外唯一寻址口径）。"""
        return tenant_namespace(self.tenant_id, self.book,
                                account_id=self.account_id,
                                combo_id=self.combo_id)

    @property
    def isolation_key(self):
        """隔离键别名：namespace 即隔离键（供下游日志/键值存储按同一口径引用）。"""
        return self.namespace

    @property
    def is_account_level(self):
        """是否为账户级引用（不带 combo）。"""
        return self.combo_id is None


@dataclass(frozen=True)
class QuotaStatus:
    """配额检查结果（只读快照）：ok 表示未超限，reason 给出超限说明。"""

    tenant_id: str
    book: str
    used_accounts: int
    max_accounts: int
    combos_by_account: tuple = ()
    max_combos_per_account: int = 0
    ok: bool = True
    reason: str = ""

    def to_dict(self):
        """序列化为只读 dict（供日志/断言使用，不含任何落盘动作）。"""
        return {
            "tenant_id": self.tenant_id,
            "book": self.book,
            "used_accounts": self.used_accounts,
            "max_accounts": self.max_accounts,
            "combos_by_account": [list(x) for x in self.combos_by_account],
            "max_combos_per_account": self.max_combos_per_account,
            "ok": self.ok,
            "reason": self.reason,
        }


def _as_refs(accounts):
    """把 accounts 入参归一成 AccountRef 列表；类型不对直接抛错。"""
    if accounts is None:
        return []
    if isinstance(accounts, (str, bytes)):
        raise TenantValidationError("accounts 须为 AccountRef 序列: %r" % (accounts,))
    try:
        refs = list(accounts)
    except TypeError as exc:
        raise TenantValidationError(
            "accounts 须为可迭代的 AccountRef 序列: %r" % (accounts,)) from exc
    for ref in refs:
        if not isinstance(ref, AccountRef):
            raise TenantValidationError("accounts 元素须为 AccountRef: %r" % (ref,))
    return refs


def _assert_same_owner(tenant, refs):
    """归属校验：引用必须同租户同账本，跨账/跨租户一律拒绝（两账不合并）。"""
    for ref in refs:
        if ref.book != tenant.book:
            raise CrossTenantError(
                "跨账本引用（official/legacy 两账不得合并）: tenant=%s/%s ref=%s"
                % (tenant.book, tenant.tenant_id, ref.namespace))
        if ref.tenant_id != tenant.tenant_id:
            raise CrossTenantError(
                "跨租户引用: tenant=%s/%s ref=%s"
                % (tenant.book, tenant.tenant_id, ref.namespace))


def quota_check(tenant, accounts=()):
    """配额检查（不抛超限错，返回 QuotaStatus 供调用方判定）。

    - 结构错（tenant 非 Tenant / accounts 元素错 / 跨租户）仍抛错——配额检查不
      吞跨租户错误；
    - 超限时返回 ``ok=False`` 且 reason 非空；需要「拒绝」语义用 enforce_quota()。
    """
    if not isinstance(tenant, Tenant):
        raise TenantValidationError("tenant 须为 Tenant: %r" % (tenant,))
    refs = _as_refs(accounts)
    _assert_same_owner(tenant, refs)

    combo_counts = {}
    account_ids = set()
    for ref in refs:
        account_ids.add(ref.account_id)
        if ref.combo_id is not None:
            combo_counts[ref.account_id] = combo_counts.get(ref.account_id, 0) + 1

    used_accounts = len(account_ids)
    problems = []
    if used_accounts > tenant.quota.max_accounts:
        problems.append(
            "账户数 %d 超配额 %d" % (used_accounts, tenant.quota.max_accounts))
    for account_id in sorted(combo_counts):
        used = combo_counts[account_id]
        if used > tenant.quota.max_combos_per_account:
            problems.append(
                "账户 %s 组合数 %d 超配额 %d"
                % (account_id, used, tenant.quota.max_combos_per_account))
    return QuotaStatus(
        tenant_id=tenant.tenant_id,
        book=tenant.book,
        used_accounts=used_accounts,
        max_accounts=tenant.quota.max_accounts,
        combos_by_account=tuple(sorted(combo_counts.items())),
        max_combos_per_account=tenant.quota.max_combos_per_account,
        ok=not problems,
        reason="；".join(problems),
    )


def enforce_quota(tenant, accounts=()):
    """配额强制：超限即抛 QuotaExceeded（新增账户/组合前的闸门）。"""
    status = quota_check(tenant, accounts)
    if not status.ok:
        raise QuotaExceeded(
            "租户 %s/%s 配额超限: %s"
            % (tenant.book, tenant.tenant_id, status.reason))
    return status


def validate_tenant(tenant, accounts=()):
    """整单校验租户 + 在册账户/组合；返回只读快照 dict。

    校验项：
    1) tenant 自身合法（二次校验，防 frozen 实例被篡改后流入）；
    2) accounts 全部归属该租户该账本（跨租户/跨账本 → CrossTenantError）；
    3) 重复引用（同 account_id+combo_id 或重复账户级引用 → DuplicateAccountError）；
    4) 组合必须挂在已登记的账户上（否则 TenantValidationError）；
    5) 配额（账户数/单账户组合数 → QuotaExceeded）。

    状态门不在本函数（suspended/closed 的在册账户仍应可校验通过）；
    新增账户/组合的状态拦截由 TenantRegistry.register_* 负责。
    """
    if not isinstance(tenant, Tenant):
        raise TenantValidationError("tenant 须为 Tenant: %r" % (tenant,))
    # 二次校验：frozen dataclass 仍可能被 object.__setattr__ 改写，入口重检一遍。
    _check_tenant_id(tenant.tenant_id)
    _check_book(tenant.book)
    _check_status(tenant.status)
    if not isinstance(tenant.quota, TenantQuota):
        raise TenantValidationError("Tenant.quota 须为 TenantQuota: %r" % (tenant.quota,))

    refs = _as_refs(accounts)
    _assert_same_owner(tenant, refs)

    account_level = set()
    seen = set()
    combo_refs = []
    for ref in refs:
        key = (ref.account_id, ref.combo_id)
        if key in seen:
            raise DuplicateAccountError(
                "重复引用: %s%s" % (ref.namespace,
                                   "（账户级）" if ref.is_account_level else ""))
        seen.add(key)
        if ref.is_account_level:
            account_level.add(ref.account_id)
        else:
            combo_refs.append(ref)
    # 两遍扫描：先收齐账户级引用再判组合挂载，避免受入参顺序影响。
    combos = set()
    for ref in combo_refs:
        if ref.account_id not in account_level:
            raise TenantValidationError(
                "组合 %s 挂在不存在的账户 %s 上" % (ref.combo_id, ref.account_id))
        combos.add("%s/%s" % (ref.account_id, ref.combo_id))

    enforce_quota(tenant, refs)
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant.tenant_id,
        "book": tenant.book,
        "status": tenant.status,
        "namespace": tenant.namespace,
        "namespace_prefix": namespace_prefix(tenant.book),
        "accounts": tuple(sorted(account_level)),
        "combos": tuple(sorted(combos)),
        "account_count": len(account_level),
        "quota": {
            "max_accounts": tenant.quota.max_accounts,
            "max_combos_per_account": tenant.quota.max_combos_per_account,
        },
    }


class TenantRegistry:
    """进程内纯模型注册表：登记租户/账户/组合并拒绝非法/跨租户/重复/超配额。

    只活在内存里：不落盘、不读环境、不碰 ledger/engine——本 Phase 只交付契约，
    接执行/迁移账本不在本单范围（参见 sim_exchange.json 的 single_source_of_truth）。
    同一 tenant_id 可分别登记在 official 与 legacy 两本账（键=(book, tenant_id)），
    但两边引用不可跨账解析。
    """

    def __init__(self):
        self._tenants = {}    # (book, tenant_id) -> Tenant
        self._accounts = {}   # (book, tenant_id) -> [AccountRef, ...]

    # ---------------- 租户 ----------------
    def register_tenant(self, tenant):
        """登记租户：非法 → TenantValidationError；同账同 id 重复 → DuplicateTenantError。"""
        validate_tenant(tenant)
        key = (tenant.book, tenant.tenant_id)
        if key in self._tenants:
            raise DuplicateTenantError(
                "租户重复注册: %s/%s" % (tenant.book, tenant.tenant_id))
        self._tenants[key] = tenant
        self._accounts[key] = []
        return tenant

    def get_tenant(self, tenant_id, book="official"):
        """按 (tenant_id, book) 取租户；他账存在而本账不存在 → 跨账本拒绝。"""
        _check_book(book)
        _check_tenant_id(tenant_id)
        tenant = self._tenants.get((book, tenant_id))
        if tenant is not None:
            return tenant
        for other_book in BOOKS:
            if other_book != book and (other_book, tenant_id) in self._tenants:
                raise CrossTenantError(
                    "跨账本访问：租户 %s 只登记在 %s 账（请求 %s 账），两账不得合并"
                    % (tenant_id, other_book, book))
        raise TenantNotFound("租户未注册: %s/%s" % (book, tenant_id))

    def tenants(self, book=None):
        """列出已登记租户（可按账本过滤，返回元组，只读）。"""
        if book is None:
            return tuple(self._tenants[key] for key in sorted(self._tenants))
        _check_book(book)
        return tuple(self._tenants[key] for key in sorted(self._tenants)
                     if key[0] == book)

    def set_status(self, tenant_id, status, book="official"):
        """状态迁移：按 active→suspended/closed、suspended→active/closed 表执行。

        幂等：迁移到当前状态直接返回原租户；closed 是终态（任何迁移都拒绝）。
        """
        _check_status(status)
        tenant = self.get_tenant(tenant_id, book)
        if status == tenant.status:
            return tenant
        if status not in _STATUS_TRANSITIONS[tenant.status]:
            raise TenantTransitionError(
                "状态迁移非法: %s/%s %s→%s（closed 为终态）"
                % (tenant.book, tenant_id, tenant.status, status))
        updated = replace(tenant, status=status)
        self._tenants[(updated.book, updated.tenant_id)] = updated
        return updated

    # ---------------- 账户/组合 ----------------
    def register_account(self, ref):
        """登记账户级引用（combo_id=None）；租户不存在/跨账/非 active/重复/超配额均拒绝。"""
        self._check_ref(ref)
        if not ref.is_account_level:
            raise TenantValidationError(
                "register_account 只收账户级引用（combo_id=None），组合走 register_combo: %r"
                % (ref,))
        tenant = self.get_tenant(ref.tenant_id, ref.book)
        self._assert_active(tenant)
        refs = self._accounts[(tenant.book, tenant.tenant_id)]
        if any(item.account_id == ref.account_id for item in refs):
            raise DuplicateAccountError(
                "账户重复注册: %s/%s/%s"
                % (tenant.book, tenant.tenant_id, ref.account_id))
        enforce_quota(tenant, refs + [ref])
        refs.append(ref)
        return ref

    def register_combo(self, ref):
        """登记组合引用（combo_id 必填）：账户须先登记，重复/超配额/跨租户均拒绝。"""
        self._check_ref(ref)
        if ref.is_account_level:
            raise TenantValidationError(
                "register_combo 需要 combo_id，账户级引用走 register_account: %r" % (ref,))
        tenant = self.get_tenant(ref.tenant_id, ref.book)
        self._assert_active(tenant)
        refs = self._accounts[(tenant.book, tenant.tenant_id)]
        if not any(item.account_id == ref.account_id and item.is_account_level
                   for item in refs):
            self._raise_if_cross_tenant(ref)
            raise AccountNotFound(
                "账户未注册，组合不得先挂: %s/%s/%s"
                % (tenant.book, tenant.tenant_id, ref.account_id))
        if any(item.account_id == ref.account_id and item.combo_id == ref.combo_id
               for item in refs):
            raise DuplicateAccountError("组合重复注册: %s" % (ref.namespace,))
        enforce_quota(tenant, refs + [ref])
        refs.append(ref)
        return ref

    def register(self, ref):
        """按 combo_id 是否为空分派到 register_account/register_combo。"""
        self._check_ref(ref)
        return self.register_account(ref) if ref.is_account_level \
            else self.register_combo(ref)

    def accounts_of(self, tenant_id, book="official"):
        """列出某租户在册的账户/组合引用（只读元组，按登记顺序）。"""
        tenant = self.get_tenant(tenant_id, book)
        return tuple(self._accounts[(tenant.book, tenant.tenant_id)])

    def resolve(self, tenant_id, account_id, combo_id=None, book="official"):
        """按 (租户, 账户, 组合, 账本) 解析出 AccountRef；跨租户借用 → CrossTenantError。"""
        _check_id("account_id", account_id)
        if combo_id is not None:
            _check_id("combo_id", combo_id, _MAX_COMBO_ID_LEN)
        tenant = self.get_tenant(tenant_id, book)
        refs = self._accounts[(tenant.book, tenant.tenant_id)]
        for ref in refs:
            if ref.account_id == account_id and ref.combo_id == combo_id:
                return ref
        self._raise_if_cross_tenant(
            AccountRef(tenant_id=tenant_id, account_id=account_id,
                       combo_id=combo_id, book=book))
        raise AccountNotFound(
            "账户/组合未注册: %s/%s/%s%s"
            % (book, tenant_id, account_id,
               ("/" + combo_id) if combo_id is not None else ""))

    # ---------------- 内部 ----------------
    @staticmethod
    def _check_ref(ref):
        if not isinstance(ref, AccountRef):
            raise TenantValidationError("ref 须为 AccountRef: %r" % (ref,))

    @staticmethod
    def _assert_active(tenant):
        if not tenant.is_active:
            raise TenantInactive(
                "租户 %s/%s 状态 %s，拒绝新增账户/组合"
                % (tenant.book, tenant.tenant_id, tenant.status))

    def _raise_if_cross_tenant(self, ref):
        """账户/组合不在本租户但登记在别的租户名下 → 明确报跨租户，而不是笼统未注册。"""
        for (book, tenant_id), refs in self._accounts.items():
            if book != ref.book or tenant_id == ref.tenant_id:
                continue
            if any(item.account_id == ref.account_id for item in refs):
                raise CrossTenantError(
                    "跨租户引用: 账户 %s 属于 %s/%s，不是 %s/%s（他人账户不可借用）"
                    % (ref.account_id, book, tenant_id, ref.book, ref.tenant_id))


__all__ = [
    "SCHEMA_VERSION",
    "NAMESPACE_ROOT",
    "BOOKS",
    "TENANT_STATUSES",
    "NAMESPACE_BY_BOOK",
    "RESERVED_TENANT_IDS",
    "TenantContractError",
    "TenantValidationError",
    "TenantNotFound",
    "TenantInactive",
    "TenantTransitionError",
    "CrossTenantError",
    "DuplicateTenantError",
    "DuplicateAccountError",
    "AccountNotFound",
    "QuotaExceeded",
    "TenantQuota",
    "Tenant",
    "AccountRef",
    "QuotaStatus",
    "TenantRegistry",
    "namespace_prefix",
    "tenant_namespace",
    "parse_namespace",
    "quota_check",
    "enforce_quota",
    "validate_tenant",
]
