"""CardBridge — 飞书卡片送审 + ack 回收（jev_gate 双轨模式复刻，httpx 重写简版）。

双轨（对齐 jev_gate push_feishu 的可用性模式，不复制代码）：
  路① OpenAPI webhook 直连：环境变量 AW_CARD_WEBHOOK（自建机器人 webhook）；
  路② OpenAPI 凭证：AW_CARD_APP_ID / AW_CARD_APP_SECRET / AW_CARD_OPEN_ID。
两路皆未配置 → **dryrun 模式**（一等公民）：卡片 JSON 落盘 learning/cards_outbox/*.json
+ 日志，不发送。本机不确定有飞书凭证，闭环全程可在 dryrun 下演示。

ack 回收取舍（v1）：lark_card_handler 卡片回查/webhook 太重，v1 选 **文件 ack**——
人工/脚本向 learning/ack_inbox/ 放 JSON（decision_id/action/operator），poll_acks()
轮询消费。实现成本最低、dryrun 可演示、生产可无缝替换为 HTTP 端点（接口不变）。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from zhice_os_v2.agent_platform.config import utc_now

logger = logging.getLogger(__name__)

# 卡片目录缺省值：包内 learning/ 下（与代码同库，方便 dryrun 演示与测试隔离注入）
_DEFAULT_BASE = Path(__file__).resolve().parent

ACTION_CONFIRM = "CONFIRM_SELL"
ACTION_IGNORE = "IGNORE"


def default_outbox_dir() -> Path:
    return _DEFAULT_BASE / "cards_outbox"


def default_ack_inbox_dir() -> Path:
    return _DEFAULT_BASE / "ack_inbox"


def build_decision_card(decision: dict) -> dict:
    """决策 dict（AgentDecision.to_dict() 或 ORM row）→ 飞书 v1 交互卡片 JSON。

    内容：标的/方向/score + 各方法票人话版 reasons + 「确认卖点」「忽略」两按钮
    （action_value 带 decision_id，jev_gate 实证可用的 tag:button v1 schema）。
    """
    d = decision
    votes = d.get("votes_json") or []
    vote_lines = []
    for v in votes:
        reasons = "；".join(v.get("reasons") or []) or "（无依据）"
        vote_lines.append(
            f"**{v.get('method_id', '?')}** → {v.get('direction', '?')} "
            f"(conf={v.get('confidence', 0):.2f}, w={v.get('weight', 1.0):.2f})：{reasons}"
        )
    votes_md = "\n".join(vote_lines) or "（无票）"
    model_verdict = d.get("model_verdict")
    llm_line = ""
    if model_verdict:
        llm_line = (f"\n**LLM 兜底**: {model_verdict.get('action')} "
                    f"(conf={model_verdict.get('confidence', 0):.2f}) "
                    f"— {model_verdict.get('rationale', '')[:120]}")
    decision_id = d.get("decision_id", "")
    header = {
        "template": "red" if d.get("action") == "SELL" else "blue",
        "title": {"tag": "plain_text",
                  "content": f"🎯 卖点决策送审 · {d.get('ts_code', '?')} ({d.get('market', '?')})"},
    }
    def _btn(content: str, btype: str, action: str) -> dict:
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": content},
            "type": btype,
            "value": {"action": action, "decision_id": decision_id},
        }
    return {
        "config": {"wide_screen_mode": True},
        "header": header,
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md",
                         "content": f"**方向** {d.get('action', '?')}　|　"
                                    f"**score(SELL)** {d.get('score_sell', 0):.3f}　|　"
                                    f"**score(HOLD)** {d.get('score_hold', 0):.3f}　|　"
                                    f"**路径** {d.get('arbitration_path', '?')}　|　"
                                    f"**risk_off** {d.get('regime_risk_off', False)}"},
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": f"**各方法投票**\n{votes_md}{llm_line}"},
            {"tag": "hr"},
            {"tag": "action", "actions": [
                _btn("确认卖点", "danger", ACTION_CONFIRM),
                _btn("忽略", "default", ACTION_IGNORE),
            ]},
            {"tag": "note", "elements": [
                {"tag": "plain_text",
                 "content": f"decision_id={decision_id} · "
                            f"bar_time={d.get('bar_time') or '-'} · "
                            f"generated={utc_now().isoformat(timespec='seconds')}"},
            ]},
        ],
    }


class CardBridge:
    """飞书卡片送审 + ack 回收。

    模式判定（环境变量，aw_card.env 同款命名）：
    - AW_CARD_WEBHOOK                     → 路① webhook 直连（msg_type=interactive）
    - AW_CARD_APP_ID/SECRET/OPEN_ID 齐备   → 路② OpenAPI tenant_access_token + im 发送
    - 均未配置                              → dryrun：卡片 JSON 落盘 cards_outbox/
    """

    def __init__(self, outbox_dir: str | Path | None = None,
                 ack_inbox_dir: str | Path | None = None,
                 webhook: str | None = None,
                 app_id: str | None = None,
                 app_secret: str | None = None,
                 open_id: str | None = None) -> None:
        self.outbox_dir = Path(outbox_dir) if outbox_dir else default_outbox_dir()
        self.ack_inbox_dir = Path(ack_inbox_dir) if ack_inbox_dir else default_ack_inbox_dir()
        self.webhook = webhook or os.environ.get("AW_CARD_WEBHOOK", "")
        self.app_id = app_id or os.environ.get("AW_CARD_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("AW_CARD_APP_SECRET", "")
        self.open_id = open_id or os.environ.get("AW_CARD_OPEN_ID", "")
        self._token_cache: tuple[float, str] | None = None   # (expire_ts, token)
        self.sent = 0
        self.dryrun_cards = 0

    @property
    def dryrun(self) -> bool:
        return not (self.webhook or (self.app_id and self.app_secret and self.open_id))

    # ── 送审 ────────────────────────────────────────────────────────────────
    def send_decision_card(self, decision: dict) -> str:
        """发送决策卡片，返回 card_msg_id（dryrun 时为落盘文件标识）。不抛异常。"""
        card = build_decision_card(decision)
        decision_id = decision.get("decision_id", "")
        card_msg_id = f"dry-{uuid.uuid4().hex[:16]}"
        try:
            if self.webhook:
                ok = self._send_via_webhook(card)
            elif self.app_id and self.app_secret and self.open_id:
                ok = self._send_via_openapi(card)
            else:
                ok = False
        except Exception as exc:  # 推送失败不阻塞主链 → 降级 dryrun 落盘
            logger.warning("卡片发送异常(%s) → 降级 dryrun 落盘: %s", type(exc).__name__, exc)
            ok = False
        if ok:
            self.sent += 1
            return card_msg_id
        return self._dump_dryrun(decision, card, decision_id)

    def _send_via_webhook(self, card: dict) -> bool:
        import httpx

        resp = httpx.post(self.webhook, json={"msg_type": "interactive",
                                              "card": card}, timeout=10.0)
        data = resp.json() if resp.content else {}
        ok = resp.status_code == 200 and (data.get("code") in (0, None)
                                          or data.get("StatusCode") == 0)
        if not ok:
            logger.warning("webhook 发送失败: %s %s", resp.status_code, str(data)[:160])
        return bool(ok)

    def _send_via_openapi(self, card: dict) -> bool:
        import httpx

        token = self._tenant_token()
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": self.open_id,
                  "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            params={"receive_id_type": "open_id"},
            timeout=10.0,
        )
        data = resp.json()
        ok = resp.status_code == 200 and data.get("code") == 0
        if not ok:
            logger.warning("OpenAPI 发送失败: %s %s", resp.status_code, str(data)[:160])
        return bool(ok)

    def _tenant_token(self) -> str:
        """tenant_access_token 获取（简版缓存至过期前 60s）。"""
        import time

        import httpx

        now = time.time()
        if self._token_cache and self._token_cache[0] > now + 60:
            return self._token_cache[1]
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10.0,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"tenant_access_token 获取失败: {data.get('msg')}")
        expire = float(data.get("expire", 300))
        token = data["tenant_access_token"]
        self._token_cache = (now + expire, token)
        return token

    def _dump_dryrun(self, decision: dict, card: dict, decision_id: str) -> str:
        """dryrun 落盘：cards_outbox/<ts>_<decision_id>.json（卡片+决策摘要）。"""
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        ts = utc_now().strftime("%Y%m%dT%H%M%S")
        f = self.outbox_dir / f"{ts}_{decision_id or uuid.uuid4().hex[:8]}.json"
        payload = {
            "mode": "dryrun",
            "decision_id": decision_id,
            "ts_code": decision.get("ts_code"),
            "market": decision.get("market"),
            "action": decision.get("action"),
            "card": card,
            "generated_at": utc_now().isoformat(),
        }
        f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.dryrun_cards += 1
        logger.info("[CardBridge:dryrun] 卡片落盘 %s (%s %s)",
                    f.name, decision.get("ts_code"), decision.get("action"))
        return f"dryrun:{f.name}"

    # ── ack 回收（v1：文件 ack_inbox 轮询） ─────────────────────────────────
    def poll_acks(self) -> list:
        """轮询 ack_inbox/*.json → list[CardAck]；消费后文件改名 *.consumed（去重）。"""
        from zhice_os_v2.agent_platform.learning import CardAck

        acks: list[CardAck] = []
        if not self.ack_inbox_dir.exists():
            return acks
        for f in sorted(self.ack_inbox_dir.glob("*.json")):
            if f.name.endswith(".consumed.json"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("ack 文件损坏(%s): %s — 跳过", f.name, exc)
                continue
            action = str(data.get("action", "")).upper()
            if action not in (ACTION_CONFIRM, ACTION_IGNORE):
                logger.warning("ack 动作非法(%r) — 跳过 %s", data.get("action"), f.name)
                continue
            acks.append(CardAck(
                decision_id=str(data.get("decision_id", "")),
                operator=str(data.get("operator", "manual")),
                action=action,
                reason=str(data.get("reason", "")),
            ))
            consumed = f.with_suffix(".consumed.json")
            try:
                f.rename(consumed)
            except OSError:
                pass
        return acks

    def write_ack(self, decision_id: str, action: str,
                  operator: str = "manual", reason: str = "") -> Path:
        """便利方法：写一条 ack 文件（dryrun 演示/人工补 ack 用）。"""
        self.ack_inbox_dir.mkdir(parents=True, exist_ok=True)
        ts = utc_now().strftime("%Y%m%dT%H%M%S%f")
        f = self.ack_inbox_dir / f"{ts}_{decision_id}.json"
        f.write_text(json.dumps({
            "decision_id": decision_id, "action": action,
            "operator": operator, "reason": reason,
            "acked_at": utc_now().isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return f
