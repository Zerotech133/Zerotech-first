"""告警中心服务：委托持久化端口存取。

存储随后端切换（sql 落库 v2_alert / memory 进程内），业务层与路由无需感知存储细节。
此前本服务直连 SQL session/_engine，绕过 PersistencePort 且无视 persistence_backend，
导致内存后端/测试无法隔离——现统一走端口。

V1 移植（2026-08-24）：模块级 send_alert 带 同(level,title) 300s 频控（CRITICAL
豁免）+ 落 v2_alert（V1 只有日志，V2 落库改进）+ 场景函数（止损/风控拒绝/工作流
异常/组合超限/策略暴露/数据陈旧）。

Task 12（多渠道改造）：企业微信群机器人 webhook（ALERT_WEBHOOK_URL）**只承载
运维白名单告警**——send_alert 的 webhook 推送仅当 ``kind ∈ _ALLOWED_OPS_KINDS``
（notification_health_service）时发生；业务告警（kind=None：止损/风控拒绝/哨兵/
离群价等）只频控+落 v2_alert，普通用户业务正文绝不进运维群。运维告警唯一结构化
入口是 notification_health_service.send_notification_ops_alert（正文白名单字段）。
"""
from __future__ import annotations

import logging
import os
import time
from uuid import uuid4

from zhice_os_v2.domain.alert.alert import Alert, AlertLevel, AlertStatus
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)

_THROTTLE_TTL_SEC = 300          # V1: 同 (level,title) 5 分钟去重
_throttle_cache: dict[str, float] = {}

_EMOJI = {"WARN_LOW": "🔶", "WARN_HIGH": "⚠️", "CRITICAL": "🚨", "INFO": "ℹ️"}


def _send_wecom_webhook(level: str, title: str, body: str) -> bool:
    """底层企业微信群机器人 HTTP 发送（Task 12 抽出）：仅运维白名单路径可达。

    未配置 ALERT_WEBHOOK_URL 只记日志不伪装成功；HTTP 异常吞掉记 warning。
    正文由调用方按白名单字段构造（send_alert 的 kind 门保证）。

    成功判定（S-2）：企业微信**出错也返 HTTP 200**，真实结果在 body errcode——
    errcode==0 才算送达；非 0（如 key 失效 40001）/非 JSON/缺 errcode 一律失败。
    日志只记稳定 errcode，绝不回显 body 全文（防错误文本把业务内容回灌日志）。
    """
    webhook = os.getenv("ALERT_WEBHOOK_URL", "")
    if not webhook:
        return False
    try:
        import httpx

        emoji = _EMOJI.get(level, "ℹ️")
        resp = httpx.post(
            webhook,
            timeout=5.0,
            json={
                "msgtype": "text",
                "text": {"content": f"{emoji} [{level}] {title}\n{body}\n"
                                    f"{utc_now().strftime('%Y-%m-%d %H:%M:%S')}"},
            },
        )
        if resp.status_code != 200:
            return False
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001 - 非 JSON body（代理页等）→ 失败
            logger.warning("alert webhook bad_payload status=%s", resp.status_code)
            return False
        errcode = payload.get("errcode") if isinstance(payload, dict) else None
        if errcode == 0:
            return True
        logger.warning("alert webhook rejected errcode=%s", errcode)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert webhook failed: %s", exc)
        return False


def send_alert(
    level: str,
    title: str,
    body: str = "",
    dedup_open: bool = False,
    *,
    kind: str | None = None,
) -> bool:
    """统一告警出口：频控 →（仅运维白名单 kind）webhook 推送 → 落 v2_alert。永不抛异常。

    Task 12 起 ALERT_WEBHOOK_URL（企业微信群机器人）只承载运维告警：

    - kind=None（业务告警：止损/风控拒绝/哨兵/离群价/采集成功率等）——只频控+
      落 v2_alert，**绝不推 webhook**（普通用户业务正文不得进运维群），返回
      False（未尝试 webhook，与未配置 webhook 同口径，不伪装成功）；
    - kind ∈ _ALLOWED_OPS_KINDS（notification_health_service）：允许推 webhook，
      正文由调用方按白名单字段构造（运维唯一入口 send_notification_ops_alert）；
    - kind 给定但不在白名单：整条拒绝（记 warning return False）——不推 webhook
      也不落库，防借落库通道夹带业务正文。

    dedup_open=True：落库前查是否已有同 title 的 OPEN/ACKED 告警，有则不重复插入
    （用于哨兵这类"持续态"告警——条件持续成立时不逐日累加同题 OPEN）。
    """
    if kind is not None:
        from zhice_os_v2.application.services.notification_health_service import (
            _ALLOWED_OPS_KINDS,
        )
        if kind not in _ALLOWED_OPS_KINDS:
            logger.warning("send_alert rejected non-ops kind=%s title=%s", kind, title)
            return False

    now = time.monotonic()
    key = f"{level}:{title}"
    if level != "CRITICAL":
        last = _throttle_cache.get(key)
        if last is not None and now - last < _THROTTLE_TTL_SEC:
            return False  # 频控内静默
        _throttle_cache[key] = now

    # webhook：仅运维白名单 kind 可达（业务正文只落库）
    sent = _send_wecom_webhook(level, title, body) if kind is not None else False

    # 落库（best-effort，失败不影响调用方；内存后端/测试静默跳过）。
    # level 归一为 AlertLevel 合法值（WARN_HIGH/WARN_LOW→WARN；V1 细分保留在日志）
    try:
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend == "sql":
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            db_level = level if level in ("INFO", "WARN", "CRITICAL") else "WARN"
            session = get_session()
            try:
                if dedup_open:
                    from sqlalchemy import select as _select
                    exists = session.execute(
                        _select(orm.AlertModel.id).where(
                            orm.AlertModel.title == title,
                            orm.AlertModel.status.in_(("OPEN", "ACKED")),
                        ).limit(1)
                    ).first()
                    if exists is not None:
                        return sent  # 已有未闭合同题告警 → 不重复插入
                session.add(orm.AlertModel(
                    alert_id=f"alert-{uuid4().hex[:12]}",
                    level=db_level, title=title, message=body,
                    status="OPEN", created_at=utc_now()))
                session.commit()
            finally:
                session.close()
    except Exception:
        logger.debug("alert persist failed", exc_info=True)

    logger.info("alert_sent level=%s title=%s webhook=%s", level, title, sent)
    return sent


def resolve_sentinel_alerts(active_titles: set[str]) -> int:
    """哨兵自动消解：把属哨兵前缀、但本轮不在 active_titles 的 OPEN/ACKED 告警置 RESOLVED。

    条件已解除（策略被激活/已建仓）的旧哨兵告警不再滞留为 stale 噪音。仅 sql 后端生效，
    best-effort 不抛。返回关闭条数。复用 acked_at 作关闭时刻（免加列/迁移）。
    """
    from zhice_os_v2.application.services.idle_strategy_monitor import (
        SENTINEL_PREFIXES,
        select_stale_titles,
    )
    try:
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend != "sql":
            return 0
        from sqlalchemy import or_

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        session = get_session()
        try:
            rows = session.query(orm.AlertModel).filter(
                orm.AlertModel.status.in_(("OPEN", "ACKED")),
                or_(*[orm.AlertModel.title.like(p + "%") for p in SENTINEL_PREFIXES]),
            ).all()
            stale = set(select_stale_titles([r.title for r in rows], active_titles))
            n = 0
            for r in rows:
                if r.title in stale:
                    r.status = "RESOLVED"
                    r.acked_at = r.acked_at or utc_now()
                    n += 1
            session.commit()
            return n
        finally:
            session.close()
    except Exception:
        logger.debug("resolve_sentinel_alerts failed", exc_info=True)
        return 0


# ---------- V1 场景函数（level 1:1） ----------

def alert_stop_loss(ts_code: str, current_price: float, stop_price: float) -> bool:
    return send_alert("WARN_HIGH", f"止损触发 {ts_code}",
                      f"现价 {current_price} ≤ 止损价 {stop_price}，已执行平仓")


def alert_risk_rejected(plan_id: str, reason: str) -> bool:
    return send_alert("WARN_HIGH", f"风控拦截 {plan_id[:40]}", reason)


def alert_workflow_error(workflow: str, error: str) -> bool:
    return send_alert("CRITICAL", f"工作流异常 {workflow}", error[:200])


def alert_portfolio_overexposed(detail: str) -> bool:
    return send_alert("CRITICAL", "组合暴露超限", detail)


def alert_strategy_exposure_high(sv: str, exposure_pct: float) -> bool:
    return send_alert("WARN_LOW", f"策略暴露偏高 {sv}",
                      f"当前敞口 {exposure_pct:.0%}，接近上限")


def alert_data_stale(ts_code: str, last_update: str) -> bool:
    return send_alert("WARN_LOW", f"行情数据过期 {ts_code}",
                      f"快照最后更新 {last_update}，超过 30 分钟")


class AlertService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def create_alert(self, level: AlertLevel, title: str, message: str = "") -> Alert:
        alert = Alert(
            alert_id=f"alert-{uuid4().hex[:12]}",
            level=AlertLevel(str(level)),
            title=title,
            message=message,
            status=AlertStatus.OPEN,
            created_at=utc_now(),
        )
        self._db.save_alert(alert)
        return alert

    def list_alerts(self, status: AlertStatus | None = None) -> list[Alert]:
        return self._db.list_alerts(status=status)

    def ack(self, alert_id: str) -> Alert:
        alert = self._db.get_alert(alert_id)
        if alert is None:
            raise KeyError(alert_id)
        alert.status = AlertStatus.ACKED
        alert.acked_at = utc_now()
        self._db.save_alert(alert)
        return alert
