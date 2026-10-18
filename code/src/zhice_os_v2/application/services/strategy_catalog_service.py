"""策略目录服务：公共/私有策略归属、owner 校验、发布申请与审核派生。

职责边界（与 StrategyService 区分）：
- StrategyService 管理**平台全局**策略（注册/审批/生命周期/删除），无 owner 概念。
- StrategyCatalogService 管理**多用户归属**面：私有策略的 owner 权限、可见性目录、
  以及"私有→公共"的发布申请/审核派生。二者共用同一 PersistencePort 与领域对象，
  互不覆盖对方端点。
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from zhice_os_v2.domain.security.user import User
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import (
    Strategy,
    StrategyPublicationRequest,
    StrategyStatus,
    StrategyVersion,
)
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


def _default_spec(market: str) -> StrategySpec:
    return StrategySpec(spec_version=1, market=market, universe={"type": "all"})


class StrategyCatalogService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    # ---------- 读 ----------
    def get_strategy(self, strategy_id: str) -> Strategy | None:
        return self._db.get_strategy(strategy_id)

    def list_all(self, market: str | None = None) -> list[Strategy]:
        """全部策略（含所有用户私有；无 owner/可见性过滤）。仅供 ADMIN 全局查看 API。"""
        out: list[Strategy] = []
        for s in self._db.list_strategies():
            if market and s.market != market:
                continue
            out.append(s)
        return out

    def list_visible(self, user: User, market: str | None = None) -> list[Strategy]:
        """目录 = 所有 PUBLIC 策略 + 本人 PRIVATE 策略（他人私有不可见）。"""
        out: list[Strategy] = []
        for s in self._db.list_strategies():
            if market and s.market != market:
                continue
            if s.visibility == "PUBLIC" or s.owner_user_id == user.user_id:
                out.append(s)
        return out

    # ---------- 版本（Phase D T4：我的策略页版本侧 UI 依赖）----------
    def list_all_versions(self) -> list[StrategyVersion]:
        """全部策略版本（API 层一次性分组用，避免逐策略 N+1）。"""
        return self._db.list_strategy_versions()

    def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        """某策略全部版本，按 version_no 数值升序（"v2" 排在 "v10" 之前）。"""
        return sorted(
            (v for v in self._db.list_strategy_versions() if v.strategy_id == strategy_id),
            key=self._version_sort_key,
        )

    # ---------- 建 ----------
    def create_public(
        self, name: str, market: str, spec: StrategySpec | None = None
    ) -> Strategy:
        return self._create(
            strategy_id=name,
            name=name,
            market=market,
            spec=spec,
            owner_user_id=None,
            visibility="PUBLIC",
            publication_status="PUBLISHED",
            status=StrategyStatus.STAGING,
        )

    def create_private(
        self, user: User, name: str, market: str, spec: StrategySpec | None = None
    ) -> Strategy:
        """新建私有策略（owner=user）。

        strategy_id 用防碰撞唯一 id（不用原始 name 当 id）：save_strategy 按 strategy_id
        upsert，若用 name 当 id，则 alice/bob 同名私有会整条覆盖并改 owner——破坏归属隔离。
        name 只存进 strategy_name。id 掺入随机组件且沿用 `strategy-{market}-*` 家族命名，
        与 StrategyService 的 `strategy-{market}-{n}`（n 为小整数）无碰撞可能。

        注：返回 Strategy（非计划签名标注的 StrategyVersion）——归属/可见性字段在
        Strategy 上，且计划的验收测试 `test_publish_approval_derives_public_copy`
        直接断言返回值的 `.visibility`/`.strategy_id`，故以 Strategy 为准。
        """
        strategy_id = f"strategy-{market}-{secrets.token_hex(6)}"
        return self._create(
            strategy_id=strategy_id,
            name=name,
            market=market,
            spec=spec,
            owner_user_id=user.user_id,
            visibility="PRIVATE",
            publication_status="PRIVATE",
            status=StrategyStatus.DRAFT,
        )

    def _create(
        self,
        *,
        strategy_id: str,
        name: str,
        market: str,
        spec: StrategySpec | None,
        owner_user_id: str | None,
        visibility: str,
        publication_status: str,
        status: StrategyStatus,
    ) -> Strategy:
        spec = spec or _default_spec(market)
        strategy = Strategy(
            strategy_id=strategy_id,
            strategy_name=name,
            market=market,
            strategy_type="V2_EXTERNAL",
            status=status,
            owner_user_id=owner_user_id,
            visibility=visibility,
            publication_status=publication_status,
        )
        self._db.save_strategy(strategy)
        version = StrategyVersion(
            strategy_version_id=f"{strategy_id}-v1",
            strategy_id=strategy_id,
            version_no="v1",
            market=market,
            spec=spec,
            status=status,
            research_ref=spec.research_ref,
            created_at=datetime.now(UTC),
        )
        self._db.save_strategy_version(version)
        return strategy

    # ---------- 改（owner-scoped）----------
    def _load_owned(self, user: User, strategy_id: str) -> Strategy:
        """载入本人私有策略；不存在或非 owner 一律 PermissionError（不泄露存在性）。"""
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None or strategy.owner_user_id != user.user_id:
            raise PermissionError("strategy.not_found_or_forbidden")
        return strategy

    def update_private(self, user: User, strategy_id: str, **patch: object) -> Strategy:
        strategy = self._load_owned(user, strategy_id)
        if "name" in patch:
            strategy.strategy_name = str(patch["name"])
        if "description" in patch:
            strategy.description = str(patch["description"])
        strategy.updated_at = datetime.now(UTC)
        self._db.save_strategy(strategy)
        return strategy

    # ---------- 发布申请 / 审核 ----------
    def request_publication(
        self, user: User, strategy_id: str
    ) -> StrategyPublicationRequest:
        strategy = self._load_owned(user, strategy_id)
        # 已发布过（存在派生公共副本）则不允许再次申请：early-fail，避免产生一条注定被
        # approve 拒绝的死 PENDING 申请（会被部分唯一索引卡住无法再申请）。发布"更新为新
        # 版本 vN"是 Phase B 语义，此处只安全关闭覆盖漏洞。
        if self._db.get_strategy(f"{strategy_id}-pub") is not None:
            raise ValueError("strategy.already_published")
        # 同一 source 已有 PENDING 申请则拒绝重复（服务校验 + DB 部分唯一索引双保险）。
        if self._db.get_pending_publication_request(strategy_id) is not None:
            raise ValueError("strategy.publication_already_pending")
        strategy.publication_status = "PENDING_REVIEW"
        strategy.updated_at = datetime.now(UTC)
        self._db.save_strategy(strategy)
        request = StrategyPublicationRequest(
            request_id=f"pubreq-{uuid.uuid4().hex[:12]}",
            source_strategy_id=strategy_id,
            requester_user_id=user.user_id,
            status="PENDING",
            created_at=datetime.now(UTC),
        )
        self._db.save_publication_request(request)
        return request

    @staticmethod
    def _version_sort_key(version: StrategyVersion) -> tuple[int, datetime]:
        """按 version_no 数值优先、created_at 次之排序（"vN" → N；无法解析退 0）。"""
        vn = version.version_no or ""
        try:
            num = int(vn.lstrip("vV"))
        except ValueError:
            num = 0
        return (num, version.created_at)

    def _latest_source_version(self, strategy_id: str, market: str) -> StrategyVersion:
        versions = [
            v for v in self._db.list_strategy_versions() if v.strategy_id == strategy_id
        ]
        if not versions:
            return StrategyVersion(
                strategy_version_id=f"{strategy_id}-v1",
                strategy_id=strategy_id,
                version_no="v1",
                market=market,
                spec=_default_spec(market),
            )
        return max(versions, key=self._version_sort_key)

    def approve_publication(self, request_id: str, actor: User) -> StrategyVersion:
        """审核通过：在**单一事务**内派生平台所有(owner=None, PUBLIC)的公共策略 + 不可变
        公共版本、reset 源 publication_status=PRIVATE、置 request=APPROVED+derived_id。

        经持久化层 commit_publication_approval 单会话单 commit 保证原子性——消除"派生
        PUBLIC 已落库可被选用/运行，但 request 仍 PENDING"的未审批上线窗口。取源最新版本
        spec 按 version_no 显式排序（非插入/created_at 末位）。
        """
        request = self._db.get_publication_request(request_id)
        if request is None:
            raise LookupError("strategy.publication_request_not_found")
        # 状态守卫：仅 PENDING 可审核，防对已 APPROVED/REJECTED 的 request 重复派生/翻状态。
        if request.status != "PENDING":
            raise ValueError("strategy.publication_not_pending")
        source = self._db.get_strategy(request.source_strategy_id)
        if source is None:
            raise LookupError("strategy.source_not_found")

        derived_id = f"{source.strategy_id}-pub"
        # 硬安全门：目标公共策略已存在则拒绝，绝不经 commit_publication_approval 的 UPSERT
        # 覆盖已被 UserStrategyInstance 引用的公共版本 spec（破坏"版本一旦引用即不可变"）。
        if self._db.get_strategy(derived_id) is not None:
            raise ValueError("strategy.already_published")

        latest = self._latest_source_version(source.strategy_id, source.market)
        spec = latest.spec

        now = datetime.now(UTC)
        derived_strategy = Strategy(
            strategy_id=derived_id,
            strategy_name=source.strategy_name,
            market=source.market,
            strategy_type=source.strategy_type,
            status=StrategyStatus.STAGING,
            description=source.description,
            owner_user_id=None,
            visibility="PUBLIC",
            publication_status="PUBLISHED",
            created_at=now,
            updated_at=now,
        )
        derived_version = StrategyVersion(
            strategy_version_id=f"{derived_id}-v1",
            strategy_id=derived_id,
            version_no="v1",
            market=source.market,
            spec=spec,
            status=StrategyStatus.STAGING,
            research_ref=spec.research_ref,
            created_at=now,
        )
        request.status = "APPROVED"
        request.reviewed_by = actor.user_id
        request.reviewed_at = now
        request.derived_strategy_id = derived_id

        self._db.commit_publication_approval(
            derived_strategy=derived_strategy,
            derived_version=derived_version,
            source_strategy_id=source.strategy_id,
            request=request,
        )
        return derived_version

    def reject_publication(
        self, request_id: str, actor: User
    ) -> StrategyPublicationRequest:
        """审核拒绝：单事务置 request=REJECTED + reviewer/time、reset 源 publication_status
        =PRIVATE（visibility 不变），**不**产生公共副本。拒绝后 owner 可重新申请（旧申请
        已 REJECTED，部分唯一索引 uq_v2_publication_pending_source 不再阻拦）。
        """
        request = self._db.get_publication_request(request_id)
        if request is None:
            raise LookupError("strategy.publication_request_not_found")
        if request.status != "PENDING":
            raise ValueError("strategy.publication_not_pending")
        source = self._db.get_strategy(request.source_strategy_id)
        if source is None:
            raise LookupError("strategy.source_not_found")

        request.status = "REJECTED"
        request.reviewed_by = actor.user_id
        request.reviewed_at = datetime.now(UTC)
        self._db.commit_publication_rejection(
            source_strategy_id=source.strategy_id, request=request
        )
        return request
