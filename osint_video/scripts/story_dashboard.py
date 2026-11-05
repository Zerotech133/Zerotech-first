"""把事件层现状渲染成**单文件、只读、可离线打开**的看板页。

## 为什么是静态快照而不是在线服务

本机（以及生产 CentOS 7）都没装 uvicorn，先起一个 Web 服务才能看图，等于把
"看一眼现状"变成一次部署。快照的做法是：生成时刻把数据固化进 HTML，双击就能看，
不连库、不发请求、不改任何文件；要看最新状态就重新生成一次。

## 口径一致性约定

页面**不自己算健康度**。标定状态来自 :func:`knowledge.calibration_stamp.verify`，
轮次体检来自 :func:`knowledge.run_ledger.check_round`，事件列表来自
:func:`knowledge.story_query.list_stories`。看板只做呈现，避免出现"看板说绿灯、
自检说红灯"这种两套口径。

## 脱敏

与 ``knowledge/api.py`` 一致：**不取正文**（``content`` 可能含个人信息），
只取标题、来源、时间与判定元数据。

用法::

    python scripts/story_dashboard.py                    # 默认输出到 outputs/
    python scripts/story_dashboard.py --out x.html --hours 3360
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 允许 python scripts/xxx.py 直接跑
    sys.path.insert(0, str(ROOT))

from common import db  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge import calibration_stamp, run_ledger, story_query  # noqa: E402

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "story_dashboard.html"
#: 锚点：模板里必须且只能出现一次，替换前断言，防止模板改坏后静默输出空页。
_PAYLOAD_ANCHOR = "__PAYLOAD__"
#: 默认回溯 120 天。事件层的 last_seen 跨度会超过 7 天，用默认 168h 会漏历史事件。
DEFAULT_HOURS = 120 * 24

#: 语料构成：总量 / 验收语料量 / 带真值量 / 真值故事数。
#: 这四项决定"页面上的数字能不能当真实情报看"，必须随页展示。
_PROVENANCE_SQL = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE left(unit_key, 7) = 'verify-') AS seed_units,
       count(*) FILTER (WHERE extension ? 'truth_story') AS truth_units,
       count(*) FILTER (WHERE source_channel = 'manual') AS manual_units,
       count(DISTINCT extension->>'truth_story') AS truth_stories,
       min(created_at) AS first_unit_at,
       max(created_at) AS last_unit_at
  FROM osint_knowledge_unit
"""


def _iso(value: Any) -> str | None:
    """把 datetime / 字符串统一成可被 JS ``new Date()`` 解析的 ISO 串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value)
    # psycopg 的字符串形式是 "2026-11-02 11:07:56.369458+00:00"，空格分隔在部分
    # 浏览器里 new Date() 解析失败，统一换成 T 分隔的 ISO 8601。
    if len(text) > 10 and text[10] == " ":
        text = f"{text[:10]}T{text[11:]}"
    return text


def collect_stories(hours: int, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """取事件列表并补上成员明细。

    ``list_stories`` 的条数上限是 200（``MAX_LIMIT``），超出时如实标注截断，
    不假装是全量——否则 KPI 上的"事件总数"会变成一个看不出问题的错数。
    """
    listing = story_query.list_stories(hours=hours, limit=limit)
    items = listing.get("items") or []
    stories: list[dict[str, Any]] = []
    for row in items:
        detail = story_query.get_story_detail(int(row["story_id"])) or dict(row)
        members = detail.pop("members", []) or []
        for member in members:
            for key in ("linked_at", "published_at", "ingested_at"):
                member[key] = _iso(member.get(key))
        detail["members"] = members
        detail["member_count"] = len(members)
        for key in ("first_seen_at", "last_seen_at", "created_at", "updated_at"):
            detail[key] = _iso(detail.get(key))
        stories.append(detail)
    meta = {
        "truncated": len(items) >= story_query.MAX_LIMIT,
        "max_limit": story_query.MAX_LIMIT,
        "window_hours": hours,
    }
    return stories, meta


def collect_stamp() -> dict[str, Any]:
    """两类标定的当前状态。损坏要在页面上显性呈现，不能静默降级成"未标定"。"""
    try:
        raw = calibration_stamp.read_stamp()
    except ValueError:
        raw = {}  # 损坏由 verify 报 status=corrupt，这里不吞结论
    try:
        fingerprint, units = calibration_stamp.corpus_fingerprint()
    except Exception as exc:  # noqa: BLE001 - 库里没语料时也要能出页
        fingerprint, units = "", 0
        corpus_error = f"{type(exc).__name__}: {exc}"
    else:
        corpus_error = None
    try:
        probe = calibration_stamp.probe_fingerprint()
    except Exception:  # noqa: BLE001
        probe = None

    items: dict[str, Any] = {}
    for kind in calibration_stamp.KINDS:
        status, message = calibration_stamp.verify(
            kind,
            fingerprint,
            probe=probe if kind == calibration_stamp.KIND_PROMPT else None,
        )
        entry = raw.get(kind) if isinstance(raw, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        items[kind] = {
            "status": status,
            "message": message,
            "at": entry.get("at"),
            "units": entry.get("units"),
            "fingerprint": entry.get("fingerprint"),
            "detail": entry.get("detail") or {},
        }

    snapshot = calibration_stamp.StampSnapshot(
        margin=(items[calibration_stamp.KIND_MARGIN]["status"], ""),
        prompt=(items[calibration_stamp.KIND_PROMPT]["status"], ""),
        corpus_fingerprint=fingerprint,
        units=units,
        probe_fingerprint=probe,
    )
    return {
        "worst": snapshot.worst,
        "items": items,
        "fingerprint": fingerprint,
        "units": units,
        "probe": probe,
        "error": corpus_error,
    }


def collect_pending(path: Path) -> list[dict[str, Any]]:
    """读临界带待裁队列，只保留 ``status=pending``（已裁的留在文件里做审计）。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "pending").lower() != "pending":
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("queued_at") or ""), reverse=True)
    return rows


def collect_ledger(path: Path) -> dict[str, Any]:
    """台账：明细 + 汇总 + 坏行统计 + 对最新一轮做体检。"""
    rounds = run_ledger.load_rounds(path)
    summary = run_ledger.summarize(rounds)
    health = run_ledger.ledger_health(path)
    findings: list[dict[str, Any]] = []
    if rounds:
        try:
            for finding in run_ledger.check_round(rounds[-1], rounds[:-1]):
                findings.append(
                    {
                        "level": finding.level,
                        "code": finding.code,
                        "message": finding.message,
                        "hint": finding.action,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 体检失败不该让整页打不开
            findings.append(
                {
                    "level": "warn",
                    "code": "ledger.check_failed",
                    "message": f"本轮体检未执行：{type(exc).__name__}: {exc}",
                    "hint": "看板只呈现后端结论，体检异常需回命令行跑脚本复核",
                }
            )
    return {
        "rounds": rounds,
        "summary": summary,
        "health": health,
        "findings": findings,
        "latest_at": _iso(rounds[-1].get("recorded_at")) if rounds else None,
        "latest_noop": bool(rounds[-1].get("noop")) if rounds else None,
    }


#: 真值对照：每条归属边的真值标记。只对 seed 语料有意义，没有真值时整块不展示。
_TRUTH_CHECK_SQL = """
SELECT m.story_id AS story_id,
       m.unit_key AS unit_key,
       u.extension->>'truth_story' AS truth
  FROM osint_story_member m
  JOIN osint_knowledge_unit u
    ON u.unit_key = m.unit_key
   AND u.unit_version = m.unit_version
"""


def collect_truth_check() -> dict[str, Any]:
    """拿真值去校事件：拆散（一组真值进了多个事件）/ 误合并（一个事件里有多个真值）。

    这是"事件数看着合理"之外唯一的硬校验。没有真值标记（线上真实采集）时
    ``available=False``，页面不显示这一行——不假装有结论。
    """
    rows = db.fetch_all(_TRUTH_CHECK_SQL)
    by_truth: dict[str, dict[str, int]] = {}
    by_story: dict[int, dict[str, Any]] = {}
    for row in rows:
        story_id = int(row["story_id"])
        truth = row.get("truth")
        bucket = by_story.setdefault(story_id, {"truths": set(), "solos": set()})
        if truth:
            by_truth.setdefault(str(truth), {})[story_id] = (
                by_truth.setdefault(str(truth), {}).get(story_id, 0) + 1
            )
            bucket["truths"].add(str(truth))
        else:
            bucket["solos"].add(str(row.get("unit_key") or ""))

    split = [
        {"truth": truth, "stories": sorted(evs), "units": sum(evs.values())}
        for truth, evs in by_truth.items()
        if len(evs) > 1
    ]
    merged = []
    for story_id, bucket in sorted(by_story.items()):
        truths = sorted(bucket["truths"])
        solos = sorted(bucket["solos"])
        if len(truths) > 1 or (truths and solos) or len(solos) > 1:
            merged.append({"story_id": story_id, "truths": truths, "solos": solos})
    return {
        "available": bool(by_truth),
        "groups": len(by_truth),
        "events": len(by_story),
        "split": split,
        "merged": merged,
    }


def collect_provenance(settings: Any, stories: list[dict[str, Any]]) -> dict[str, Any]:
    """数据来源自述：这批数字从哪来、是不是真数据。

    "事件从哪来"和"事件是不是真的"是两个问题。后者只能用真值语料的占比回答：
    ``unit_key`` 带 ``verify-`` 前缀、``extension`` 里有 ``truth_story`` 的，
    都是 ``scripts/seed_verify_corpus.py`` 灌进来的合成语料。占比高时必须显式
    提示，否则一屏漂亮数字很容易被当成线上采集结果。
    """
    rows = db.fetch_all(_PROVENANCE_SQL)
    row = rows[0] if rows else {}
    total = int(row.get("total") or 0)
    seed_units = int(row.get("seed_units") or 0)
    truth_units = int(row.get("truth_units") or 0)
    story_created = sorted(s.get("created_at") for s in stories if s.get("created_at"))
    return {
        "db": f"{settings.pg_db}@{settings.pg_host}:{settings.pg_port}",
        "tables": [
            "osint_story",
            "osint_story_member",
            "osint_knowledge_unit",
            "osint_knowledge_resource",
        ],
        "files": [
            "data/story_calibration_stamp.json",
            "data/story_borderline_pending.jsonl",
            "data/story_run_ledger.jsonl",
        ],
        "config": ["config/settings.py", ".env"],
        "units_total": total,
        "units_seed": seed_units,
        "truth_units": truth_units,
        "manual_units": int(row.get("manual_units") or 0),
        "truth_stories": int(row.get("truth_stories") or 0),
        "seed_ratio": round(seed_units / total, 4) if total else 0.0,
        "first_unit_at": _iso(row.get("first_unit_at")),
        "last_unit_at": _iso(row.get("last_unit_at")),
        "story_created_at": story_created[0] if story_created else None,
        "story_created_last": story_created[-1] if story_created else None,
    }


def build_payload(hours: int, limit: int, settings: Any) -> dict[str, Any]:
    stories, cut = collect_stories(hours, limit)
    stamp = collect_stamp()
    pending = collect_pending(Path(settings.story_borderline_pending))
    ledger = collect_ledger(Path(settings.story_run_ledger))
    provenance = collect_provenance(settings, stories)
    truth_check = collect_truth_check()

    versions = Counter(s.get("model_version") for s in stories if s.get("model_version"))
    member_total = sum(int(s.get("member_count") or 0) for s in stories)

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db_name": settings.pg_db,
            "db_host": f"{settings.pg_host}:{settings.pg_port}",
            "threshold": settings.story_recheck_similarity,
            "borderline_margin": settings.story_borderline_margin,
            "borderline_judge": settings.story_borderline_judge,
            "cross_window": bool(settings.story_cross_window),
            "model_version": versions.most_common(1)[0][0] if versions else None,
            "member_total": member_total,
            "story_total": len(stories),
            "truncated": cut["truncated"],
            "max_limit": cut["max_limit"],
            "window_hours": hours,
        },
        "stories": stories,
        "stamp": stamp,
        "pending": pending,
        "ledger": ledger,
        "provenance": provenance,
        "truth_check": truth_check,
    }


def render(payload: dict[str, Any], template: str | None = None) -> str:
    """把 payload 塞进模板。锚点必须恰好一处，否则说明模板被改坏了。"""
    html = template if template is not None else TEMPLATE_PATH.read_text(encoding="utf-8")
    if html.count(_PAYLOAD_ANCHOR) != 1:
        raise ValueError(
            f"模板锚点 {_PAYLOAD_ANCHOR} 出现 {html.count(_PAYLOAD_ANCHOR)} 次，应为 1 次；"
            "拒绝生成（否则会输出一个空白看板）"
        )
    # </ 在 <script type="application/json"> 里若出现 </script 会提前闭合标签。
    blob = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    return html.replace(_PAYLOAD_ANCHOR, blob, 1)


def default_out() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "outputs" / f"story_dashboard_{stamp}.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成事件层只读看板（单文件 HTML）")
    parser.add_argument("--out", type=Path, default=None, help="输出路径，默认 outputs/story_dashboard_<UTC>.html")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help=f"回溯小时数，默认 {DEFAULT_HOURS}")
    parser.add_argument("--limit", type=int, default=story_query.MAX_LIMIT, help="事件条数上限")
    args = parser.parse_args(argv)

    settings = get_settings()
    try:
        payload = build_payload(max(1, args.hours), max(1, args.limit), settings)
    except db.DatabaseUnavailableError as exc:
        print(f"数据库不可用，未生成看板：{exc}", file=sys.stderr)
        return 2

    out = args.out or default_out()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")

    meta = payload["meta"]
    print(f"已生成 {out}")
    print(
        f"  事件 {meta['story_total']} · 归属边 {meta['member_total']} · "
        f"待裁 {len(payload['pending'])} · 标定 {payload['stamp']['worst']} · "
        f"台账 {payload['ledger']['health']['lines']} 轮"
    )
    if meta["truncated"]:
        print(f"  注意：事件数已到上限 {meta['max_limit']}，页面不是全量", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
