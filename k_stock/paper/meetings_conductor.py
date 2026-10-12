# -*- coding: utf-8 -*-
"""K-STOCK-149 · 经理会议线触发编排（conductor）

职责：由定时调度（WorkBuddy automation，工作日 09:05/15:15 双触发同一命令）
调起，为全部 active 雇佣逐对召开 pre_open/post_close 会议。
本脚本只做编排（枚举/去重/上下文组装/失败隔离），会议本体在 meeting.hold_meeting。

去重：以 minutes 账本为准——同 mid+kind+当日已存在纪要则跳过（重复触发幂等）。
交易日：weekday<5 粗判（未接节假日历，节假日空跑无害——无 active 雇佣或照常
开会只是纪要内容不含休市说明，接受此限制）。
失败隔离：单对雇佣失败不阻断其余；有失败则 exit 2（供调度层汇报）。
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import manager  # noqa: E402
import meeting  # noqa: E402

STRATEGIST_LATEST = HERE.parent / "data" / "strategist_latest.json"
# K-STOCK-158 · S-g：订阅服务开关（客户可选盘前/盘后报告；缺省全开兼容存量）
STRATEGY_SUBSCRIPTIONS = HERE.parent.parent / "portal" / "data" / "strategy_subscriptions.json"


def kind_auto(now: datetime | None = None) -> str:
    """按触发时刻定会议类型：12 点前=盘前会，之后=收盘会。"""
    now = now or datetime.now()
    return "pre_open" if now.hour < 12 else "post_close"


def _today_prefix(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def already_held(mid: str, kind: str, now: datetime | None = None) -> bool:
    """账本去重：同 mid+kind+当日已有纪要 → True。"""
    prefix = _today_prefix(now)
    for rec in minutes_list(mid):
        created = str(rec.get("created_at", ""))
        if rec.get("kind") == kind and created.startswith(prefix):
            return True
    return False


def minutes_list(mid):
    import minutes
    return minutes.list_minutes(mid)


def active_pairs() -> list[tuple[str, str]]:
    """全部 active 雇佣的去重 (mid, source) 对（按账本序）。"""
    seen, pairs = set(), []
    for h in manager.hires_list():
        if h.get("status") != "active":
            continue
        pair = (h.get("manager"), h.get("source"))
        if pair[0] and pair[1] and pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _load_services() -> dict[str, dict]:
    """读订阅清单的组合服务开关（pf_id → services）；缺文件/坏 JSON → {}（全开兜底，
    与 manager 线缺省兜底风格一致）。"""
    try:
        d = json.loads(STRATEGY_SUBSCRIPTIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"WARN 订阅清单不可读（{STRATEGY_SUBSCRIPTIONS}：{e}），报告服务开关全开",
              file=sys.stderr)
        return {}
    return {p.get("id"): (p.get("services") or {})
            for p in d.get("portfolios", []) if p.get("id")}


def service_enabled(services_map: dict, source: str, kind: str) -> bool:
    """按组合判定 kind 报告服务是否开启：
    - source 不在订阅清单（雇佣的 source 非订阅组合）→ 开启（不误杀）；
    - services 或对应子键缺失 → 视为 true（缺省全开，存量行为零变化）；
    - 显式 false → 关闭。"""
    services = services_map.get(source)
    if services is None:
        return True
    return services.get(f"{kind}_report") is not False


def build_context(now: datetime | None = None, source: str | None = None) -> str:
    """轻量上下文：策略官最新产出摘要（缺失则空，hold_meeting 有兜底文案）。

    K-STOCK-163：source 为组合（pf_*）时追加「今日买入意图清单摘要」
    （plan_review.collect_context，失败静默为空——无产物时输出与旧版完全一致，
    09:05 盘前会向后兼容）。"""
    now = now or datetime.now()
    lines = [f"日期：{now.strftime('%Y-%m-%d %A')}"]
    try:
        d = json.loads(STRATEGIST_LATEST.read_text(encoding="utf-8"))
        gen = d.get("generated_at", "")
        title = d.get("title") or d.get("summary") or ""
        if title:
            lines.append(f"策略官最新产出（{gen}）：{str(title)[:200]}")
    except (OSError, ValueError):
        pass
    if source:
        try:
            import plan_review
            digest = plan_review.collect_context(source, now)
            if digest:
                lines.append(digest)
        except Exception:
            pass  # 意图摘要是增强信息：任何失败静默为空，不阻断会议线
    return "\n".join(lines)


def _auto_plan_review(mid: str, source: str) -> None:
    """K-STOCK-168 · 盘前会后自动生成经理审批单（plan_review.review_plan）。

    保险丝：KSTOCK_PLAN_REVIEW=0 可整体关闭（缺省开）——防止审批单 LLM 调用
    拖垮会议编排主链。失败只 WARN 不阻断、不改变 exit code 语义（会议成功
    仍是主目标）。重复跑覆盖当日审批单（以最新审查为准），无需去重。
    """
    if os.environ.get("KSTOCK_PLAN_REVIEW") == "0":
        return
    try:
        import plan_review
    except Exception as e:  # 模块级故障同走 WARN，不阻断会议线
        print(f"WARN 审批单生成失败 {source}: {e}")
        return
    try:
        r = plan_review.review_plan(pf_id=source, manager_id=mid)
        fn = f"{r.get('date')}_{r.get('pf_id')}.json"
        print(f"OK 审批单 {source} → {fn} (provider={r.get('provider')})")
    except plan_review.PlanReviewError as e:
        print(f"WARN 审批单生成失败 {source}: {e}")
    except Exception as e:
        print(f"WARN 审批单生成失败 {source}: {e}")


def run(kind: str, dry_run: bool = False, now: datetime | None = None) -> tuple[int, list[dict]]:
    """编排一轮会议。返回 (exit_code, 结果明细)。"""
    now = now or datetime.now()
    if kind == "auto":
        kind = kind_auto(now)
    if kind not in meeting.KINDS:
        print(f"FAIL 非法会议类型: {kind!r}")
        return 2, []
    if now.weekday() >= 5:
        print(f"SKIP 周末（{now:%Y-%m-%d %a}），不开会")
        return 0, []
    pairs = active_pairs()
    if not pairs:
        print("SKIP 无 active 雇佣，不开会")
        return 0, []

    services_map = _load_services()
    results, failed = [], 0
    for mid, source in pairs:
        if not service_enabled(services_map, source, kind):
            print(f"SKIP {mid}×{source} {kind} 客户未订阅该报告服务")
            results.append({"mid": mid, "source": source, "status": "skipped"})
            continue
        if already_held(mid, kind, now):
            print(f"SKIP {mid}×{source} {kind} 今日已开（账本去重）")
            results.append({"mid": mid, "source": source, "status": "skipped"})
            continue
        if dry_run:
            print(f"DRY {mid}×{source} {kind} 将召开（dry-run 不实际开会）")
            results.append({"mid": mid, "source": source, "status": "dry"})
            continue
        try:
            rec, provider = meeting.hold_meeting(mid, source, kind,
                                                 context=build_context(now, source=source))
            print(f"OK {mid}×{source} {kind} → {rec.get('title')} "
                  f"(channel={rec.get('channel')}, provider={provider})")
            results.append({"mid": mid, "source": source, "status": "ok",
                            "title": rec.get("title"), "provider": provider})
            if kind == "pre_open":   # K-STOCK-168：审批单属盘前语义，盘后不生成
                _auto_plan_review(mid, source)
        except meeting.MeetingError as e:
            failed += 1
            print(f"FAIL {mid}×{source} {kind} → {e}")
            results.append({"mid": mid, "source": source, "status": "failed",
                            "error": str(e)[:200]})
    if failed:
        print(f"SUMMARY {len(pairs) - failed}/{len(pairs)} 成功，{failed} 失败")
        return 2, results
    print(f"SUMMARY {len(results)}/{len(pairs)} 完成（含去重跳过）")
    return 0, results


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meetings_conductor",
                                 description="经理会议线触发编排（交易日/去重/失败隔离）")
    ap.add_argument("--kind", default="auto", help="pre_open|post_close|auto（按小时判定）")
    ap.add_argument("--dry-run", action="store_true", help="只枚举不实际开会")
    args = ap.parse_args(argv)
    code, _ = run(args.kind, dry_run=args.dry_run)
    return code


if __name__ == "__main__":
    sys.exit(_main())
