"""飞书通知客户端（I6 收口：默认经 8097 推送闸门计量发送）。

收口语义（联动接口设计稿 I5-I7 §I6 方案 A）：
- 默认路径：POST ``http://127.0.0.1:8097/api/send``（``X-Push-Hub-Key`` 鉴权，
  键链 = env ``PUSH_HUB_KEY`` → ``portal/work/push_secrets.json`` 的
  ``push_hub_key``，复用 news_collector.feishu_client 判例；密钥不落日志/输出）；
- 8097 不可达/拒绝 → closed on failure：只记 warning，绝不回落旧 webhook
  直发（K-PUSH-012 语义：闸门是唯一通路）；死信/计量证据归 8097 账本体系；
- ``FEISHU_OSINT_DIRECT=1``：应急逃生口，默认关，走此路不经计量（无
  静默/配额/证据分级），仅限闸门故障应急；
- 任何情况下不抛异常、不阻塞主流程（架构 §7.7 硬性降级策略保留）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

#: 仓库根（news_collector 的父目录，跨目录导入判例：feishu_client docstring）
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
_NEWS_DIR = str(Path(_REPO_ROOT) / "news_collector")

#: 应急逃生口开关（默认未设/空=走 8097；显式 1/true/on 才恢复旧直发）
DIRECT_ENV = "FEISHU_OSINT_DIRECT"
#: osint 推送目标名（须已在 news_collector/news_config.json push_targets 注册）
TARGET_ENV = "FEISHU_OSINT_TARGET"


def _direct_enabled() -> bool:
    """应急逃生口判据：仅显式开启时恢复旧 webhook 直发，默认关。"""
    return (os.environ.get(DIRECT_ENV) or "").strip().lower() in ("1", "true", "on")


def _load_hub_modules():
    """跨目录导入 8097 闸门客户端（feishu_client docstring 判例）。

    sys.path 挂 news_collector 目录后 import；append 而非 insert，
    避免遮蔽 osint_video 自身包名。返回 (feishu_client, push_targets)。
    """
    for path in (_REPO_ROOT, _NEWS_DIR):
        if path not in sys.path:
            sys.path.append(path)
    import feishu_client  # noqa: PLC0415 —— 纯 stdlib 模块，惰性加载
    import push_targets  # noqa: PLC0415

    return feishu_client, push_targets


class FeishuNotifier:
    """飞书通知客户端（I6 收口版：默认走 8097 计量通道）。

    Args:
        webhook_url: 显式传入 webhook 地址；缺省读 ``FEISHU_WEBHOOK_URL`` 配置。
            收口后仅作 ``enabled`` 展示与应急直发（``FEISHU_OSINT_DIRECT=1``），
            不再承担默认发送。
        timeout: HTTP 超时秒数（告警类调用不应久等）。
        target_name: osint 推送目标在 push_targets 注册表里的**名字**
            （禁写死 ou_/oc_，push_targets.py 纪律）；缺省读 env
            ``FEISHU_OSINT_TARGET``；空 = hub 通道未就绪，发送降级日志。
        kind_prefix: 计量 kind 前缀（默认 ``osint``）。
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        timeout: float = 5.0,
        target_name: str | None = None,
        kind_prefix: str = "osint",
    ) -> None:
        settings = get_settings()
        configured = webhook_url if webhook_url is not None else settings.feishu_webhook_url
        self.webhook_url: str = (configured or "").strip()
        self.timeout: float = timeout
        # 设计稿 A-13 原定 settings 新增 feishu_push_target 配置项；因本工位
        # 独占路径不含 config/settings.py，改由 env 承接（偏差已汇报）。
        raw_target = (
            target_name if target_name is not None else os.environ.get(TARGET_ENV, "")
        )
        self.target_name: str = (raw_target or "").strip()
        self.kind_prefix: str = kind_prefix or "osint"

    # -------------------------------------------------- 基础能力 --
    @property
    def enabled(self) -> bool:
        """webhook 是否已配置（语义不动，供 healthz 展示；不再承担默认发送）。"""
        return bool(self.webhook_url.startswith(("http://", "https://")))

    @property
    def hub_ready(self) -> bool:
        """8097 计量通道是否就绪（hub key 可用 且 目标已在 push_targets 注册）。

        与 ``enabled`` 是两个事实两个键，禁混为一键（设计稿 A-3）。
        """
        try:
            fc, pt = _load_hub_modules()
        except Exception:  # noqa: BLE001 —— 跨包导入失败按未就绪降级，不抛
            return False
        if not self.target_name:
            return False
        if not fc._hub_key():
            return False
        return pt.target_id(self.target_name) is not None

    def _target_entry(self, pt: Any) -> tuple[str, str] | None:
        """按名取注册表条目 → (id, receive_id_type)；查无此名返回 None。

        设计稿 A-4 仅取 id 并写死 open_id；现实注册表带 type 字段
        （open_id/chat_id），按名一并取用防错配（偏差已汇报）。
        """
        if not self.target_name:
            return None
        for entry in pt.targets():
            if entry.get("name") == self.target_name:
                return str(entry.get("id") or ""), str(entry.get("type") or "open_id")
        return None

    def _hub_send(
        self, content: str, msg_type: str, kind: str | None, critical: bool
    ) -> tuple[bool, str, str | None]:
        """经 8097 闸门发送 → (ok, why, message_id)。

        鉴权键链复用 news_collector.feishu_client（env ``PUSH_HUB_KEY`` →
        ``portal/work/push_secrets.json`` 的 ``push_hub_key``），密钥不打印
        不落日志。hub 不可达/拒绝也返回失败元组，绝不回落直发。
        """
        try:
            fc, pt = _load_hub_modules()
        except Exception as exc:  # noqa: BLE001
            return False, "hub client import unavailable: %s" % exc, None
        if not fc._hub_key():
            return False, "hub authorization not configured", None
        entry = self._target_entry(pt)
        if entry is None:
            return (
                False,
                "hub target not registered in push_targets: %r" % self.target_name,
                None,
            )
        receive_id, receive_id_type = entry
        ok, why, message_id = fc.send_msg_tracked(
            content,
            msg_type,
            receive_id,
            receive_id_type or "open_id",
            kind=kind,
            critical=critical,
            via_hub=True,
        )
        return bool(ok), str(why), (str(message_id) if message_id else None)

    def _post(
        self,
        payload: dict[str, Any],
        kind: str | None = None,
        critical: bool = False,
    ) -> bool:
        """发送内核（I6 收口）：默认走 8097 计量通道。

        未配置/失败一律降级为日志并返回 False；不抛不阻塞；closed on
        failure 绝不回落旧 webhook 直发。成功/失败都把 evidence
        （message_id/why）落日志，失败不静默。
        """
        if _direct_enabled():
            # 应急逃生口，默认关，走此路不经计量（无静默/配额/证据分级）
            return self._post_direct(payload)
        msg_type = str(payload.get("msg_type") or "text")
        if msg_type == "interactive":
            content = json.dumps(payload.get("card") or {}, ensure_ascii=False)
        else:
            content = str((payload.get("content") or {}).get("text", ""))
        if kind is None:
            # 缺 kind 不过闸计费判据：hub 侧按 auto: 前缀口径不计费
            # （对齐 push_flow.infer_kind 的 auto: 惯例，设计稿 A-5）
            logger.warning("[feishu:hub] 未传 kind，过闸按 auto: 口径不计费")
        if not self.hub_ready:
            logger.warning(
                "[feishu:hub降级] 8097 通道未就绪（target_name=%r 未注册或缺 hub key），"
                "closed on failure 不回落直发，msg_type=%s kind=%s",
                self.target_name,
                msg_type,
                kind,
            )
            return False
        try:
            ok, why, message_id = self._hub_send(content, msg_type, kind, bool(critical))
        except Exception as exc:  # noqa: BLE001 —— 闸门故障绝不阻塞管线
            logger.warning(
                "[feishu:hub降级] 8097 调用异常（closed on failure，不回落直发）：%s", exc
            )
            return False
        if ok:
            logger.info(
                "[feishu:hub] 已发送 kind=%s message_id=%s why=%s", kind, message_id, why
            )
            return True
        logger.warning(
            "[feishu:hub降级] 8097 拒发/不可达（closed on failure，不回落直发）"
            "kind=%s why=%s message_id=%s",
            kind,
            why,
            message_id,
        )
        return False

    def _post_direct(self, payload: dict[str, Any]) -> bool:
        """应急逃生口（``FEISHU_OSINT_DIRECT=1``）：旧 webhook 直发路径。

        走此路不经 8097 闸门——无计量/无静默/无配额/无证据分级，仅限
        闸门故障应急，默认关闭（未设/空一律走 8097）。
        """
        if not self.enabled:
            logger.info("[feishu:降级] webhook 未配置，payload=%s", payload)
            return False
        try:
            response = httpx.post(self.webhook_url, json=payload, timeout=self.timeout)
            data: dict[str, Any] = response.json() if response.content else {}
            # 飞书 webhook 旧版返回 {StatusCode:0}，新版返回 {code:0}
            code = data.get("code", data.get("StatusCode", response.status_code))
            if response.status_code == 200 and code in (0, "0"):
                return True
            logger.warning("[feishu:降级] 发送失败 status=%s body=%s", response.status_code, data)
            return False
        except Exception as exc:  # noqa: BLE001 —— 飞书故障绝不阻塞管线
            logger.warning("[feishu:降级] 发送异常：%s", exc)
            return False

    def send_text(
        self, title: str, content: str, kind: str | None = None, critical: bool = False
    ) -> bool:
        """发送文本消息（title 与正文换行拼接；kind/critical 透传闸门计量）。"""
        return self._post(
            {"msg_type": "text", "content": {"text": f"{title}\n{content}"}},
            kind=kind,
            critical=critical,
        )

    def send_card(
        self,
        card: dict[str, Any],
        kind: str | None = None,
        critical: bool = False,
    ) -> bool:
        """发送 interactive 卡片（结构由调用方组装；kind/critical 透传闸门计量）。"""
        return self._post(
            {"msg_type": "interactive", "card": card}, kind=kind, critical=critical
        )

    # -------------------------------------------------- 预置告警 --
    @staticmethod
    def _simple_card(title: str, lines: list[str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """构造简单文本卡片（绿/黄/红按 tag 前缀由 lines 内容体现）。"""
        elements: list[dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}
        ]
        if extra:
            elements.append(extra)
        return {
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        }

    def alert_cookie_expired(self, waiting_count: int) -> bool:
        """Cookie 过期告警：附人工更新通道提示（更新后自动恢复，无需重启）。

        影响采集连续性的运维告警 → critical=True（豁免静默先例）。
        """
        card = self._simple_card(
            "⚠️ 元宝 Cookie 已过期",
            [
                f"- 挂起任务数：**{waiting_count}**（状态 waiting_cookie）",
                "- 处置：浏览器登录元宝 → F12 复制 Cookie → 调用更新接口：",
                "  `POST /api/v1/credentials/yuanbao_cookie`",
                "- 更新后挂起任务**自动恢复 pending**，全程无需重启服务。",
            ],
        )
        return self.send_card(
            card, kind=f"{self.kind_prefix}:cookie_expired", critical=True
        )

    def alert_cookie_updated(self, operator: str, resumed_count: int) -> bool:
        """Cookie 更新成功通知（通知类 → critical=False，进静默窗排队）。"""
        return self.send_text(
            "✅ 元宝 Cookie 已更新",
            f"操作人：{operator}\n自动恢复任务数：{resumed_count}",
            kind=f"{self.kind_prefix}:cookie_updated",
        )

    def alert_dead_letter(self, dead_count: int, samples: list[str]) -> bool:
        """死信超阈值告警（架构 §2.1：死信进飞书周报人工处置）。

        死信超阈=风险提示不可吞 → critical=True（K-PUSH-007 per-kind exempt 先例）。
        """
        sample_lines = [f"- {s}" for s in samples[:5]] or ["-（无明细）"]
        card = self._simple_card(
            "🪦 链接队列死信告警",
            [f"当前死信总数：**{dead_count}**", "最近死信样本：", *sample_lines],
        )
        return self.send_card(
            card, kind=f"{self.kind_prefix}:dead_letter", critical=True
        )

    def send_daily_report(self, stats: dict[str, int]) -> bool:
        """每日运营日报（架构 §2.5：新增/成功/失败/累计；日报性质进静默窗排队）。"""
        lines = [
            f"- 今日新增链接：{stats.get('new_links', 0)}",
            f"- 完成抽取：{stats.get('done', 0)}",
            f"- 失败（待重试）：{stats.get('failed', 0)}",
            f"- 死信：{stats.get('dead', 0)}",
            f"- Cookie 挂起：{stats.get('waiting_cookie', 0)}",
            f"- 队列累计：{stats.get('total', 0)}",
        ]
        return self.send_text(
            "📊 osint_video 日报",
            "\n".join(lines),
            kind=f"{self.kind_prefix}:daily_report",
        )
