"""轮次台账：把每轮聚类结果落成**可跨轮比较**的历史。

## 为什么需要它

``story_group_run.py`` 的 payload 只 ``print`` 到 stdout，跑完就散。于是：

- "事件数昨天 29 今天 31" 这类问题，只能在恰好有人保存了上次输出时才发现；
- "待确认队列是否持续上涨"本质上是个**跨轮问题**，单轮快照永远答不了；
- 判定器静默降级（``judge_failures > 0`` 后回落规则）只在 stderr 一闪而过，
  日志采集抓不到就彻底消失——而它恰好是"数字仍然合理但含义已经变了"的典型。

§9 写了"首个真实轮次要盯三个数"，但没有任何机制承载这件事。本模块补的就是这个承载。

## 三条设计约束

1. **追加写 JSONL，不重写全文件**。一轮写坏不能毁掉历史；读的时候坏行跳过并计数，
   而不是整段历史读不出来。

2. **记录可比性前提**（库名 / 窗口 / limit / dry_run / 开关），不只是结果数字。
   在 ``osint`` 与 ``osint_calib`` 上各跑一轮，事件数天然不同，混在一起算趋势会把
   "换了库"读成"聚类劣化"。**趋势只在前提完全相同的轮次之间比**；找不到同前提基线
   就明说"只做绝对体检"，而不是硬给一个差值。

3. **判据不猜阈值**。临界带的"期望 2 对"是语料特定的，硬编码进代码就等于把某次标定
   的结果当成永久真理。所以这里只判**结构性矛盾**（有锚点却零合并、开了复核却零调用、
   判定器失败后回落、积压告警非空），量的漂移留给人和标定脚本。

## 与 preflight 的分工

``story_release_preflight.py`` 判"**能不能上线**"（依据是否过期、备份是否新鲜），
本模块判"**跑得对不对**"（这一轮的数和上一轮比是否正常）。两者都不改聚类行为。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "DEFAULT_LEDGER",
    "Finding",
    "build_record",
    "append_round",
    "load_rounds",
    "ledger_health",
    "find_baseline",
    "check_round",
    "summarize",
]

#: 默认台账位置。与 ``data/story_borderline_pending.jsonl`` 同级，便于一起备份。
DEFAULT_LEDGER = "data/story_run_ledger.jsonl"

#: 追加写锁。同进程内多线程写同一台账时避免行交错；跨进程追加依赖
#: "单次 write 不跨行"这个前提（每行都控制在一次 write 内完成）。
_APPEND_LOCK = threading.Lock()

#: 进入台账的指标白名单。不直接存整个 payload：``borderline_samples`` /
#: ``borderline_promoted_samples`` 都是**带事实标题的明细列表**（受
#: ``BORDERLINE_SAMPLE_LIMIT`` 限制），落台账等于让台账变成事件表的第二份副本。
#: 台账要的是能跨轮比较的数，不是某一轮的样本。
_METRIC_KEYS: tuple[str, ...] = (
    "units_scanned",
    "stories_created",
    "stories_reused",
    "members_added",
    "units_skipped_existing",
    "unrelated",
    "judge_calls",
    "judge_failures",
    "anchors_loaded",
    "cross_window_merged",
    "borderline",
    "borderline_rechecked",
    "borderline_promoted",
    "manual_overrides",
    "pairs_observed",
    "elapsed_ms",
    "degraded",
    "degrade_reason",
    "review_queue_written",
)

#: 决定"两轮是否可比"的上下文键。任何一项不同，事件数变化就不能归因于聚类质量。
_CONTEXT_KEYS: tuple[str, ...] = (
    "db_name",
    "window_days",
    "limit",
    "dry_run",
    "cross_window",
    "borderline_judge",
    "judge_name",
)

#: 只依赖"库 + 是否 dry-run"的量，不受窗口/开关影响，可以跨更多情形比较。
#: 目前只有积压数属于这类：它是文件状态，不是本轮计算产物。
_CONTEXT_KEYS_QUEUE: tuple[str, ...] = ("db_name", "dry_run")


@dataclass(frozen=True)
class Finding:
    """一条体检结论。

    Args:
        level: ``ok`` / ``warn`` / ``fail``。``fail`` 表示数字已经不可信，
            不是"需要关注"。
        code: 机读标识，便于过滤与测试钉住。
        message: 人读的一句话。
        action: 该做什么。留空表示不需要动作。
    """

    level: str
    code: str
    message: str
    action: str = ""


def build_record(
    payload: Mapping[str, Any],
    *,
    db_name: str,
    window_days: float,
    limit: int,
    only_reviewed: bool = True,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """把一轮 payload 折成台账行。

    只保留白名单内的指标，其余（如 ``borderline_promoted_samples``）不落台账——
    它们含事实标题，属于事件层数据，台账不该成为第二份副本。

    Args:
        payload: ``story_group_run.py`` 输出的 JSON 对象。
        db_name: 本轮连接的库名。**必须显式传入**，不能让台账自己去问 settings：
            否则"这一轮连的是哪个库"会随读取时的配置漂移，历史记录失去意义。
        window_days: 本轮取数窗口。
        limit: 本轮 limit。
        only_reviewed: 是否只跑已复核事实。
        recorded_at: 记录时刻；缺省取当前 UTC 时间。测试里注入固定值。

    Returns:
        可直接 ``json.dumps`` 的字典。
    """
    when = recorded_at or datetime.now(timezone.utc)
    backlog = payload.get("review_backlog") or {}
    metrics = {key: payload.get(key) for key in _METRIC_KEYS}
    # 积压单独取：它在 payload 里是嵌套结构，且是跨轮趋势里最有用的一个量。
    metrics["review_pending"] = backlog.get("pending")
    metrics["review_oldest_days"] = backlog.get("oldest_days")
    metrics["review_decided"] = backlog.get("decided")
    metrics["review_alert"] = backlog.get("alert")

    return {
        "recorded_at": when.isoformat(),
        # 空转轮次：窗内没有待归属事实，什么都没算。必须显式标记——否则
        # "每 15 分钟一轮、整夜空转"会把台账灌满噪声，而 `units_scanned`
        # 从 0 跳到 8 还会被趋势判据读成"处理量激增"。
        "noop": not metrics.get("units_scanned"),
        "context": {
            "db_name": db_name,
            "window_days": float(window_days),
            "limit": int(limit),
            "only_reviewed": bool(only_reviewed),
            "dry_run": bool(payload.get("dry_run")),
            "cross_window": payload.get("cross_window"),
            "borderline_judge": payload.get("borderline_judge"),
            "judge_name": payload.get("judge_name"),
        },
        "metrics": metrics,
    }


def append_round(path: str | Path, record: Mapping[str, Any]) -> None:
    """追加一行到台账。

    用追加模式而非"读全文-改-写回"：后者在一轮写坏时会把整段历史变成不可解析，
    而台账的价值恰恰在历史。单次 ``write`` 写完整行（含结尾换行），避免与其他
    进程的半行交错。

    Raises:
        OSError: 目录不可建或磁盘不可写。**不由本函数吞掉**——调用方应把它降级
            成告警而不是让整轮聚类失败，但"降级"这个决定要留在调用方，否则
            台账静默失效时会看起来像"一直很健康"。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _APPEND_LOCK:
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)


def load_rounds(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    """读台账，返回按时间正序的记录。

    坏行**跳过而不是抛**：台账是观测设施，一行解析不了不该让整段历史读不出来。
    但损坏本身要能被发现，所以坏行数记在返回列表的属性上不可行（list 无法带属性），
    改由 :func:`summarize` 重新统计——读两遍成本可接受，语义也更直白。

    Args:
        path: 台账路径。不存在时返回空列表（首轮之前的状态，不是错误）。
        limit: 只取最后 N 条。

    Returns:
        记录列表，按文件中的出现顺序（即时间正序）。
    """
    target = Path(path)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            # 旧行没有 noop 字段（该字段后加），读时派生而不是只在写入时判断：
            # 否则历史里的空转轮次会被当成实跑，跨轮趋势拿它做基线。
            if "noop" not in row:
                row["noop"] = not (row.get("metrics") or {}).get("units_scanned")
            rows.append(row)
    if limit is not None and limit > 0:
        rows = rows[-limit:]
    return rows


def _same_context(
    left: Mapping[str, Any], right: Mapping[str, Any], keys: Sequence[str]
) -> tuple[bool, str]:
    """两轮的前提是否一致；不一致时返回第一个差异，便于直接印给人看。"""
    lctx = left.get("context") or {}
    rctx = right.get("context") or {}
    for key in keys:
        lv, rv = lctx.get(key), rctx.get(key)
        if lv != rv:
            return False, f"{key}: {lv!r} ≠ {rv!r}"
    return True, ""


def find_baseline(
    current: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    *,
    skip_noop: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """在历史里找**最近一条前提相同**的轮次。

    前提不同就继续往前找，而不是直接放弃——阵发性地改一次窗口再改回来，
    不该让趋势断掉。

    Args:
        current: 本轮记录。
        history: 历史记录（时间正序，且应已排除本轮自身）。
        skip_noop: 跳过空转轮次（``units_scanned == 0``）。空转轮次的指标全是 0，
            拿它当基线会把"上游断流后恢复"读成"处理量激增"。

    Returns:
        ``(基线记录或 None, 说明)``。说明用于解释"为什么没有基线"。
    """
    for candidate in reversed(history):
        if skip_noop and candidate.get("noop"):
            continue
        ok, diff = _same_context(current, candidate, _CONTEXT_KEYS)
        if ok:
            return candidate, ""
    # 走到这里说明所有历史都不同前提，给出最接近的一条的差异，便于人判断
    if history:
        _, diff = _same_context(current, history[-1], _CONTEXT_KEYS)
        skipped = sum(1 for r in history if r.get("noop")) if skip_noop else 0
        extra = f"；其中 {skipped} 轮为空转已跳过" if skipped else ""
        return None, f"无同前提基线（与最近一轮的差异：{diff}{extra}）"
    return None, "台账为空，这是首轮"


def _noop_streak(history: Sequence[Mapping[str, Any]]) -> tuple[int, str | None]:
    """从历史末尾往前数连续空转的轮次数，并返回最后一条非空转轮次的时刻。

    只报事实、不给阈值：调度频率没有写进仓库（技术债 #26），"连续 3 轮算异常"
    在每 15 分钟的调度下是 45 分钟，在每天一次的调度下是 3 天，同一个阈值含义
    完全不同。所以这里把"连续 N 轮、自某时刻起"直接讲出来，由看的人结合调度
    频率判断是否该去查采集侧。
    """
    streak = 0
    for row in reversed(history):
        if not row.get("noop"):
            return streak, row.get("recorded_at")
        streak += 1
    return streak, None


def _num(record: Mapping[str, Any], key: str) -> float | None:
    """取数值指标；缺失或非数值返回 ``None``。"""
    value = (record.get("metrics") or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _queue_finding(metrics: Mapping[str, Any]) -> Finding | None:
    """积压告警（复用队列自身的双维度判据，不在这里重算阈值）。

    阈值定义在 ``borderline_review.backlog_alert``，台账只负责转述。重算一遍
    会出现"队列说没超、台账说超了"的分裂，比不报更糟。
    """
    alert = metrics.get("review_alert")
    if not alert:
        return None
    return Finding(
        "warn", "review.backlog_alert", str(alert), "去跑 borderline_review.py list"
    )


def check_round(
    current: Mapping[str, Any], history: Sequence[Mapping[str, Any]]
) -> list[Finding]:
    """对本轮做体检：结构性矛盾 + 跨轮趋势。

    刻意**不判"事件数应该等于几"**。事件数取决于语料，任何硬编码的期望值都会在
    语料变化后变成假警报；能判的是"这一轮的内部逻辑是否自相矛盾"和"和上一轮比
    是否出现了不该有的跳变"。

    Args:
        current: 本轮记录（由 :func:`build_record` 生成）。
        history: 本轮之前的历史记录，时间正序。

    Returns:
        结论列表；全部正常时包含若干 ``ok``，不会返回空列表。
    """
    findings: list[Finding] = []
    ctx = current.get("context") or {}
    metrics = current.get("metrics") or {}

    # ---------- 0. 空转轮次：没算东西，后面的结构性判据全部无意义 ----------
    if current.get("noop"):
        streak, since = _noop_streak(history)
        tail = f"，已连续空转 {streak} 轮" if streak else ""
        since_txt = (
            f"（最后一条非空转轮次：{str(since)[:19]}）"
            if since
            else "（此前从无非空转轮次）"
        )
        findings.append(
            Finding(
                "ok",
                "round.noop",
                f"窗内无待归属事实，本轮未做任何计算{tail}{since_txt}",
                "空转不是故障，但它会掩盖'上游没在灌数据'；"
                "是否异常取决于调度频率，而调度定义不在仓库内（技术债 #26）",
            )
        )
        # 积压是文件状态，与"本轮算了多少"无关，空转轮次照样要报
        queue = _queue_finding(metrics)
        if queue:
            findings.append(queue)
        return findings

    # ---------- 1. 判定器是否静默降级 ----------
    # 这是最危险的一类：数字仍然合理，但"LLM 判定过"这个前提已经不成立。
    if metrics.get("degraded"):
        findings.append(
            Finding(
                "fail",
                "judge.degraded",
                f"本轮判定器降级：{metrics.get('degrade_reason') or '未给出原因'}",
                "数字不能与正常轮次直接比较；先查 Ollama/模型可用性再重跑",
            )
        )
    failures = _num(current, "judge_failures") or 0
    if failures > 0:
        findings.append(
            Finding(
                "warn",
                "judge.failures",
                f"LLM 判定失败 {int(failures)} 次，失败的对按规则判定处理",
                "失败会回落规则判定而非冒充无关；若持续出现说明模型在超时或输出非法 JSON",
            )
        )

    # ---------- 2. 跨窗口召回 ----------
    if ctx.get("cross_window"):
        anchors = _num(current, "anchors_loaded") or 0
        merged = _num(current, "cross_window_merged") or 0
        if anchors == 0:
            # 这不是缺陷：窗口比语料历史还长时必然如此。但与"护栏全挡掉"必须区分开。
            findings.append(
                Finding(
                    "ok",
                    "cross_window.no_anchor",
                    "跨窗口已开，但窗外无已归属事实（锚点 0 条）",
                    "窗口太宽或语料太新；跨窗在本轮不可能生效，属预期",
                )
            )
        elif merged == 0:
            findings.append(
                Finding(
                    "warn",
                    "cross_window.zero_merge",
                    f"有 {int(anchors)} 条窗外锚点却零合并",
                    "两种可能：护栏（余弦≥0.70 + 共同主体）全挡；或窗内先自成簇导致"
                    "第三路未走到（技术债 #25）。先看 borderline_rechecked 是否有值",
                )
            )
        else:
            findings.append(
                Finding(
                    "ok",
                    "cross_window.merged",
                    f"跨窗合并 {int(merged)} 对（锚点 {int(anchors)} 条）",
                )
            )

    # ---------- 3. 临界带复核 ----------
    borderline = _num(current, "borderline") or 0
    rechecked = _num(current, "borderline_rechecked") or 0
    if ctx.get("borderline_judge") == "llm":
        if borderline > 0 and rechecked == 0:
            findings.append(
                Finding(
                    "warn",
                    "borderline.not_rechecked",
                    f"临界带有 {int(borderline)} 对，复核却 0 次",
                    "复核已开却不调用，通常是模型预热失败或全部命中人工台账；"
                    "看 stderr 的预热告警与 overrides 条数",
                )
            )
        else:
            promoted = _num(current, "borderline_promoted") or 0
            findings.append(
                Finding(
                    "ok",
                    "borderline.rechecked",
                    f"临界带 {int(borderline)} 对，复核 {int(rechecked)} 次，"
                    f"提升 {int(promoted)} 对",
                )
            )
    elif borderline > 0:
        findings.append(
            Finding(
                "ok",
                "borderline.off",
                f"临界带 {int(borderline)} 对未复核（复核关闭）",
                "这是 P0 行为：带内配对直接按主判定器结论处理",
            )
        )

    # ---------- 4. 积压告警 ----------
    queue = _queue_finding(metrics)
    if queue:
        findings.append(queue)

    # ---------- 5. 跨轮趋势 ----------
    baseline, reason = find_baseline(current, history)
    if baseline is None:
        findings.append(
            Finding("ok", "trend.no_baseline", reason, "本轮只做绝对体检，趋势从下一轮开始")
        )
        return findings

    prev_when = str(baseline.get("recorded_at") or "?")
    # 积压只依赖库与 dry-run，可以跨窗口/开关比较
    ok_queue, _ = _same_context(current, baseline, _CONTEXT_KEYS_QUEUE)
    cur_pending = _num(current, "review_pending")
    prev_pending = _num(baseline, "review_pending")
    if ok_queue and cur_pending is not None and prev_pending is not None:
        delta = cur_pending - prev_pending
        if delta > 0:
            findings.append(
                Finding(
                    "warn",
                    "trend.pending_up",
                    f"待确认积压 {int(prev_pending)} → {int(cur_pending)}（+{int(delta)}）",
                    "LLM 提升的合并已进入事件表与独立来源数；积压上涨等于'未确认的合并'在累积",
                )
            )
        else:
            findings.append(
                Finding(
                    "ok",
                    "trend.pending",
                    f"待确认积压 {int(prev_pending)} → {int(cur_pending)}",
                )
            )

    # 跨窗合并从有变无，是最典型的静默回归
    prev_merged = _num(baseline, "cross_window_merged") or 0
    cur_merged = _num(current, "cross_window_merged") or 0
    if ctx.get("cross_window") and prev_merged > 0 and cur_merged == 0:
        findings.append(
            Finding(
                "warn",
                "trend.cross_window_lost",
                f"跨窗合并由 {int(prev_merged)} 降到 0（基线 {prev_when}）",
                "先看本轮 anchors_loaded 是否也归零；若锚点还在，说明护栏或配对顺序变了",
            )
        )

    findings.append(
        Finding(
            "ok",
            "trend.baseline",
            f"趋势基线：{prev_when}（前提相同：库/窗口/limit/dry_run/开关）",
        )
    )
    return findings


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """台账概况：条数、时间跨度、最新一轮的关键数与运行前提。"""
    if not records:
        return {"rounds": 0, "first_at": None, "last_at": None, "latest": None}
    latest = records[-1]
    return {
        "rounds": len(records),
        "first_at": records[0].get("recorded_at"),
        "last_at": latest.get("recorded_at"),
        "latest": latest.get("metrics"),
        "latest_context": latest.get("context"),
    }


def ledger_health(path: str | Path) -> dict[str, int]:
    """台账健康度：总行数 / 可解析行数 / 坏行数。

    坏行数单独统计而不是由 :func:`load_rounds` 顺带返回，是因为"台账里有几行
    读不出来"本身是运维信号——它意味着有过一次写坏，或有人手工编辑过。
    ``load_rounds`` 的职责只是"尽量把历史读出来"，不该同时兼任质检。
    """
    target = Path(path)
    if not target.exists():
        return {"lines": 0, "parsed": 0, "corrupt": 0}
    lines = parsed = 0
    with target.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            lines += 1
            try:
                if isinstance(json.loads(raw), dict):
                    parsed += 1
            except json.JSONDecodeError:
                continue
    return {"lines": lines, "parsed": parsed, "corrupt": lines - parsed}
