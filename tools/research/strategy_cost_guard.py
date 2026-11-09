# -*- coding: utf-8 -*-
"""60 日主基线实盘成本阈值告警。

研究口径：不交易、不改策略，只把「实盘成本估计」映射成 ok/warning/critical，
并在超过 100bp 时提示重评容量；超过约 198bp 盈亏平衡时提示策略无经济性。

运行：
  python tools/research/strategy_cost_guard.py --cost-bp 120
  python tools/research/strategy_cost_guard.py --from-file research/reports/strategy_live_cost.json --json
  python tools/research/strategy_cost_guard.py --cost-bp 120 --no-fail   # 监控任务不因告警失败
  python tools/research/strategy_cost_guard.py --cost-bp 120 --send      # 显式发送飞书（默认 dry-run）

返回码：0=ok；2=warning；3=critical；4=输入/参数错误；--no-fail 时告警也返回 0。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
OUT_DEFAULT = REPO / "research" / "reports"
SCHEMA_VERSION = "strategy_cost_alert.v1"

sys.path.insert(0, str(REPO / "tools" / "research"))
from factor_portfolio import (  # noqa: E402
    COST_ALERT_THRESHOLD_BP,
    COST_BREAKEVEN_BP,
    assess_cost,
)


def _load_cost_file(path: Path) -> tuple[float, str]:
    # utf-8-sig accepts both plain UTF-8 and Windows PowerShell BOM output.
    text = path.read_text(encoding="utf-8-sig").strip()
    keys = ("cost_bp", "live_cost_bp", "estimated_cost_bp", "all_in_cost_bp")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in keys:
                if key in data:
                    return float(data[key]), f"file:{path.name}:{key}"
        elif isinstance(data, (int, float)):
            return float(data), f"file:{path.name}"
    except json.JSONDecodeError:
        pass
    try:
        return float(text), f"file:{path.name}"
    except ValueError as exc:
        raise ValueError(
            f"无法从 {path} 解析成本（支持 JSON 键 {keys} 或纯数字）"
        ) from exc


def resolve_cost(args) -> tuple[float, str]:
    if args.cost_bp is not None:
        return float(args.cost_bp), "cli"
    if args.from_file:
        return _load_cost_file(Path(args.from_file))
    env = os.environ.get("STRATEGY_COST_BP")
    if env:
        return float(env), "env:STRATEGY_COST_BP"
    raise ValueError("缺少成本输入：--cost-bp、--from-file 或 STRATEGY_COST_BP")


def build_payload(args) -> dict:
    cost, source = resolve_cost(args)
    result = assess_cost(cost, threshold_bp=args.threshold_bp,
                         breakeven_bp=args.breakeven_bp)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "strategy": "neutralized_book_to_market_60d",
        "cost_source": source,
        "cost_curve": "research/reports/portfolio_bm_pit_l2_cost200_covfix_20261105.json",
        "action_required": result["capacity_review_required"],
        **result,
    }
    payload["alert_text"] = build_alert_text(payload)
    return payload


def build_alert_text(payload: dict) -> str:
    lines = [
        f"【60日策略成本阈值告警】{payload['status'].upper()}",
        f"实盘成本：{payload['cost_bp']:.1f}bp（来源：{payload['cost_source']}）",
        f"阈值：{payload['threshold_bp']:.0f}bp；盈亏平衡：{payload['breakeven_bp']:.0f}bp；"
        f"剩余空间：{payload['headroom_bp']:.1f}bp",
    ]
    if payload.get("estimated_ann_return") is not None:
        lines.append(f"60d 历史成本曲线插值年化：{payload['estimated_ann_return'] * 100:.2f}%")
    else:
        lines.append("60d 历史成本曲线：超出已测 0–250bp，无法插值")
    lines.append(f"结论：{payload['message']}")
    lines.append("本告警仅研究口径，不构成投资建议，不自动暂停策略。")
    return "\n".join(lines)


def _status_title(status: str) -> tuple[str, str]:
    """(Feishu header template, status text)."""
    if status == "critical":
        return "red", "CRITICAL"
    return "wathet", "WARNING"


def _metric_column(label: str, value: str, color: str | None = None) -> dict:
    shown = "<font color='%s'>**%s**</font>" % (color, value) if color else "**%s**" % value
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [
            {
                "tag": "markdown",
                "content": "<font color='grey'>%s</font>\n%s" % (label, shown),
            }
        ],
    }


def build_alert_card(payload: dict) -> dict | None:
    """Build a 零界简报-style Feishu 2.0 card.

    Visual direction: the 2026-11-05 简报 UI — light-blue header, white body,
    big-number metric blocks, small grey labels, pill-like tags in the footer.
    This is a self-contained card so the monitor has no dependency on the
    portal runtime; card failure falls back to plain text in ``_send_feishu``.
    """
    try:
        status = payload["status"]
        template, status_text = _status_title(status)
        color = "red" if status == "critical" else "orange"
        cost = float(payload["cost_bp"])
        threshold = float(payload["threshold_bp"])
        breakeven = float(payload["breakeven_bp"])
        headroom = float(payload["headroom_bp"])
        if payload.get("estimated_ann_return") is not None:
            est = "%.2f%%" % (payload["estimated_ann_return"] * 100)
        else:
            est = "超出曲线"

        metrics_1 = {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                _metric_column("实盘成本", "%.1fbp" % cost, color),
                _metric_column("重评阈值", "%.0fbp" % threshold),
            ],
        }
        metrics_2 = {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                _metric_column("盈亏平衡", "%.0fbp" % breakeven),
                _metric_column("剩余空间", "%.1fbp" % headroom),
            ],
        }
        metrics_3 = {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                _metric_column("60d 插值年化", est, color),
                _metric_column("状态", status_text, color),
            ],
        }

        now = datetime.now().strftime("%m-%d %H:%M")
        card = {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": "零界 · 成本阈值告警"},
                "subtitle": {
                    "tag": "plain_text",
                    "content": "中性化 book_to_market 60日 · 研究口径",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": "<font color='%s'>**⚠ %s**</font>　实盘成本超过 %.0fbp 重评阈值\n"
                        "<font color='grey'>数据快照 · %s · 成本来源 %s</font>"
                        % (color, status_text, threshold, now, payload["cost_source"]),
                    },
                    {"tag": "hr"},
                    metrics_1,
                    metrics_2,
                    metrics_3,
                    {"tag": "hr"},
                    {
                        "tag": "markdown",
                        "content": "**结论**\n%s" % payload["message"],
                    },
                    {"tag": "hr"},
                    {
                        "tag": "markdown",
                        "content": "**口径说明**\n"
                        "• **bp**：1bp = 0.01%%；%.1fbp 即单边成本 %.2f%%。\n"
                        "• **100bp 阈值**：超过后重评容量/换手/执行方式；不是自动停策略。\n"
                        "• **%0.fbp 盈亏平衡**：按 150/200/250bp 成本曲线线性插值，60d 净年化≈0。\n"
                        "• **本次 %.1fbp**：60d 插值年化 %s，较 30bp 基线 1.95%% 少约 %.2fpp。\n"
                        "• **成本口径**：含价差、冲击与换手成本，不只是佣金；研究输出，不构成投资建议。"
                        % (
                            cost,
                            cost / 100.0,
                            breakeven,
                            cost,
                            est,
                            (1.9538 - (payload.get("estimated_ann_return") or 0) * 100)
                            if payload.get("estimated_ann_return") is not None
                            else 1.9538,
                        ),
                    },
                    {
                        "tag": "markdown",
                        "content": "<font color='grey'>#cost_alert #strategy_60d · strategy_cost_guard · "
                        "研究口径 · %s</font>" % now,
                    },
                ]
            },
        }
        return card
    except Exception:  # noqa: BLE001 - a card failure must not block fallback text
        return None


def _send_feishu(text: str, card: dict | None = None) -> dict:
    try:
        sys.path.insert(0, str(REPO / "news_collector"))
        from send_feishu import send_content, send_text  # noqa: PLC0415

        card_result = None
        if card:
            try:
                card_ok = bool(
                    send_content(json.dumps(card, ensure_ascii=False), "interactive")
                )
                card_result = {"ok": card_ok, "mode": "interactive"}
                if card_ok:
                    return {
                        "ok": True,
                        "sent": True,
                        "channel": "feishu",
                        "mode": "interactive",
                    }
            except Exception as exc:  # noqa: BLE001
                card_result = {"ok": False, "mode": "interactive", "error": str(exc)}
        text_ok = bool(send_text(text))
        result = {
            "ok": text_ok,
            "sent": text_ok,
            "channel": "feishu",
            "mode": "text",
        }
        if card_result is not None:
            result["card_fallback"] = card_result
        return result
    except Exception as exc:  # pragma: no cover - 网络/配置异常不应让监控崩
        return {"ok": False, "sent": False, "channel": "feishu", "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="60 日主基线实盘成本阈值告警")
    parser.add_argument("--cost-bp", type=float, default=None, help="实盘单边成本（bp）")
    parser.add_argument("--from-file", default=None, help="JSON/文本成本文件")
    parser.add_argument("--threshold-bp", type=float, default=COST_ALERT_THRESHOLD_BP,
                        help="重评容量阈值（默认 100bp）")
    parser.add_argument("--breakeven-bp", type=float, default=COST_BREAKEVEN_BP,
                        help="60d 盈亏平衡参考（默认 198bp）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT), help="JSON 落盘目录")
    parser.add_argument("--no-write", action="store_true", help="不落盘 JSON")
    parser.add_argument("--send", action="store_true", help="status!=ok 时发送飞书（默认 dry-run）")
    parser.add_argument("--no-fail", action="store_true", help="告警时仍返回 0（供监控任务）")
    args = parser.parse_args(argv)

    try:
        payload = build_payload(args)
    except Exception as exc:
        print(f"[strategy-cost-guard] ERROR: {exc}", file=sys.stderr)
        return 4

    if not args.no_write:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"strategy_cost_alert_{stamp}.json"
        payload["report_path"] = str(out)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        payload["report_path"] = None

    if args.send and payload["status"] != "ok":
        payload["feishu_card"] = build_alert_card(payload)
        payload["send"] = _send_feishu(payload["alert_text"], payload["feishu_card"])

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload["alert_text"])
        if payload.get("report_path"):
            print(f"\n报告：{payload['report_path']}")

    code = {"ok": 0, "warning": 2, "critical": 3}.get(payload["status"], 4)
    return 0 if (args.no_fail and code != 4) else code


if __name__ == "__main__":
    raise SystemExit(main())
