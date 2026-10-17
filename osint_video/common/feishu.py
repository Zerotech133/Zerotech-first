"""飞书机器人 webhook 客户端（告警卡片 / 日报，架构 §2.5）。

降级策略（架构 §7.7，硬性要求）：
- webhook 未配置（``FEISHU_WEBHOOK_URL`` 为空）→ 仅记录日志、返回 ``False``；
- 发送失败 / 网络异常 / 响应非 0 → 记录 warning、返回 ``False``；
- **任何情况下不抛异常、不阻塞主流程**。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书自定义机器人 webhook 客户端。

    Args:
        webhook_url: 显式传入 webhook 地址；缺省读 ``FEISHU_WEBHOOK_URL`` 配置。
        timeout: HTTP 超时秒数（告警类调用不应久等）。
    """

    def __init__(self, webhook_url: str | None = None, timeout: float = 5.0) -> None:
        settings = get_settings()
        configured = webhook_url if webhook_url is not None else settings.feishu_webhook_url
        self.webhook_url: str = (configured or "").strip()
        self.timeout: float = timeout

    # -------------------------------------------------- 基础能力 --
    @property
    def enabled(self) -> bool:
        """webhook 是否已配置。"""
        return bool(self.webhook_url.startswith(("http://", "https://")))

    def _post(self, payload: dict[str, Any]) -> bool:
        """POST 到 webhook；未配置/失败一律降级为日志，返回是否成功。"""
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

    def send_text(self, title: str, content: str) -> bool:
        """发送文本消息（title 与正文换行拼接）。"""
        return self._post({"msg_type": "text", "content": {"text": f"{title}\n{content}"}})

    def send_card(self, card: dict[str, Any]) -> bool:
        """发送 interactive 卡片（结构由调用方组装）。"""
        return self._post({"msg_type": "interactive", "card": card})

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
        """Cookie 过期告警：附人工更新通道提示（更新后自动恢复，无需重启）。"""
        card = self._simple_card(
            "⚠️ 元宝 Cookie 已过期",
            [
                f"- 挂起任务数：**{waiting_count}**（状态 waiting_cookie）",
                "- 处置：浏览器登录元宝 → F12 复制 Cookie → 调用更新接口：",
                "  `POST /api/v1/credentials/yuanbao_cookie`",
                "- 更新后挂起任务**自动恢复 pending**，全程无需重启服务。",
            ],
        )
        return self.send_card(card)

    def alert_cookie_updated(self, operator: str, resumed_count: int) -> bool:
        """Cookie 更新成功通知。"""
        return self.send_text(
            "✅ 元宝 Cookie 已更新",
            f"操作人：{operator}\n自动恢复任务数：{resumed_count}",
        )

    def alert_dead_letter(self, dead_count: int, samples: list[str]) -> bool:
        """死信超阈值告警（架构 §2.1：死信进飞书周报人工处置）。"""
        sample_lines = [f"- {s}" for s in samples[:5]] or ["-（无明细）"]
        card = self._simple_card(
            "🪦 链接队列死信告警",
            [f"当前死信总数：**{dead_count}**", "最近死信样本：", *sample_lines],
        )
        return self.send_card(card)

    def send_daily_report(self, stats: dict[str, int]) -> bool:
        """每日运营日报（架构 §2.5：新增/成功/失败/累计）。"""
        lines = [
            f"- 今日新增链接：{stats.get('new_links', 0)}",
            f"- 完成抽取：{stats.get('done', 0)}",
            f"- 失败（待重试）：{stats.get('failed', 0)}",
            f"- 死信：{stats.get('dead', 0)}",
            f"- Cookie 挂起：{stats.get('waiting_cookie', 0)}",
            f"- 队列累计：{stats.get('total', 0)}",
        ]
        return self.send_text("📊 osint_video 日报", "\n".join(lines))
