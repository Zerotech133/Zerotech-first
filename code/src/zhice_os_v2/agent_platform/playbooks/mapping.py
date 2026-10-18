"""PlaybookMapping — 标的→打法包映射（T06）：内存态 + aw_playbook_binding 持久化。

- playbook_for(symbol)：缺省 DEFAULT_PLAYBOOK_ID（不落行的标的都是默认包）。
- bind/unbind：写/删 aw_playbook_binding（session_factory 为 None 时纯内存态）。
- group_by_playbook：daemon 分组执行的入口（symbol 列表 → {playbook_id: [symbols]}）。
"""
from __future__ import annotations

import logging

from zhice_os_v2.agent_platform.playbooks.spec import DEFAULT_PLAYBOOK_ID

logger = logging.getLogger(__name__)


class PlaybookMapping:
    """标的 → playbook_id 映射器。线程模型：daemon 单线程周期内调用。"""

    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory
        self._overrides: dict[str, str] = {}
        if session_factory is not None:
            self._load_from_db()

    # ── 查询 ────────────────────────────────────────────────────────────────
    def playbook_for(self, symbol: str) -> str:
        """标的所属打法包 id（缺省 default）。"""
        return self._overrides.get(symbol, DEFAULT_PLAYBOOK_ID)

    def group_by_playbook(self, symbols: list[str]) -> dict[str, list[str]]:
        """按打法包分组（保持入参顺序；default 组始终存在——空组便于上层统一遍历）。"""
        groups: dict[str, list[str]] = {DEFAULT_PLAYBOOK_ID: []}
        for sym in symbols:
            groups.setdefault(self.playbook_for(sym), []).append(sym)
        return groups

    def bindings(self) -> dict[str, str]:
        """当前全部显式绑定（不含默认包标的）。"""
        return dict(self._overrides)

    # ── 变更 ────────────────────────────────────────────────────────────────
    def bind(self, symbol: str, playbook_id: str) -> None:
        """绑定/改绑：内存即时生效 + aw_playbook_binding upsert。"""
        if not playbook_id:
            raise ValueError("playbook_id 不能为空")
        self._overrides[symbol] = playbook_id
        self._persist_upsert(symbol, playbook_id)

    def unbind(self, symbol: str) -> str:
        """解绑 → 回默认包；返回解绑前 playbook_id（未绑定时返回 default）。"""
        old = self._overrides.pop(symbol, DEFAULT_PLAYBOOK_ID)
        self._persist_delete(symbol)
        return old

    # ── 持久化 ──────────────────────────────────────────────────────────────
    def _load_from_db(self) -> None:
        from zhice_os_v2.agent_platform.models import AwPlaybookBindingModel

        try:
            with self._session_factory() as session:
                for row in session.query(AwPlaybookBindingModel).all():
                    self._overrides[row.symbol] = row.playbook_id
        except Exception as exc:
            logger.warning("aw_playbook_binding 读取失败(%s) — 空映射起步", exc)

    def _persist_upsert(self, symbol: str, playbook_id: str) -> None:
        if self._session_factory is None:
            return
        from zhice_os_v2.agent_platform.config import utc_now
        from zhice_os_v2.agent_platform.models import AwPlaybookBindingModel

        try:
            with self._session_factory() as session:
                row = session.get(AwPlaybookBindingModel, symbol)
                if row is None:
                    session.add(AwPlaybookBindingModel(
                        symbol=symbol, playbook_id=playbook_id, bound_at=utc_now()))
                else:
                    row.playbook_id = playbook_id
                    row.bound_at = utc_now()
                session.commit()
        except Exception as exc:
            logger.warning("aw_playbook_binding 写失败(%s) — 内存绑定仍生效", exc)

    def _persist_delete(self, symbol: str) -> None:
        if self._session_factory is None:
            return
        from zhice_os_v2.agent_platform.models import AwPlaybookBindingModel

        try:
            with self._session_factory() as session:
                row = session.get(AwPlaybookBindingModel, symbol)
                if row is not None:
                    session.delete(row)
                    session.commit()
        except Exception as exc:
            logger.warning("aw_playbook_binding 删失败(%s) — 内存解绑仍生效", exc)
