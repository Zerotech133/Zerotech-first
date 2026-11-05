"""临界带人工确认台账（LLM 复核结论的人工二审通道）。

## 这个模块存在的理由

``StoryGrouper`` 的临界带 ``[直判门槛-0.05, 直判门槛)`` 是**机器也拿不准**的
窄带。2026-11-04 在 42 条真值语料上实测，该带内同时落着两类东西：

- ``s11-01``/``s11-02``（cos=0.7133，同一事件的不同进展，应当合并）；
- ``s04-01``/``solo-05``（cos=0.7203，主体相同但确实两件事，应当分开）。

两者只差 0.007。任何单一阈值都分不开，任何"间隔小就降门槛"的时间加权规则
也分不开（实测最优 slope=+0.000）。所以这一段只能升级判定能力，或者请人。

本模块提供"请人"的那一半：

1. **待确认队列**——LLM 越过门槛合进去的配对全部落盘（``pending``），
   每条带两侧标题、余弦、LLM 理由。人看这份清单，不用翻库。
2. **人工台账**——人拍板后把结论写进台账（``overrides``），下一轮聚类时
   台账**优先于** LLM 复核。机器再怎么算也不会覆盖人的判断。
3. **落到成员边的痕迹**——台账命中时 ``decided_by='manual'``，LLM 复核命中时
   ``decided_by='llm_recheck'``。两者与纯余弦的 ``vector`` 在库里可区分，
   事后能算出"人工和 LLM 各改了多少"。

## 为什么台账只对临界带生效

``sim >= 门槛`` 的配对已由余弦直判，``sim < 门槛 - 0.05`` 的配对证据太弱。
把人工否决权放开到全域，意味着人一个笔误就能把两条明显无关的事实并成一个
事件，而误合并会直接污染 ``independent_sources``（热度分母）且**事后极难
发现**——这是本项目反复吃过的亏。人工只在"机器也拿不准"的那条带上拥有
否决权，这与复核本身的意义一致。

## 文件格式

台账（``data/story_borderline_overrides.json``）::

    {
      "a||b": {"decision": "merge",  "note": "同一份自律倡议的后续", "reviewed_at": "..."},
      "c||d": {"decision": "keep_separate", "note": "主体相同但两件事"}
    }

键是 ``sorted([left, right])`` 用 ``||`` 连接——**必须无序**，理由见
:func:`knowledge.story_group.unordered_pair`。也接受 ``"a||b": true/false``
的简写形式，便于手工快速标注。

队列（``data/story_borderline_pending.jsonl``）每行一个样本，与
``GroupResult.borderline_promoted_samples`` 字段一致，另加 ``queued_at`` 与
``status``。

## 队列的生命周期（v1.8 补）

队列在 v1.5 落地时只有"写入"没有"关闭"，带来三个后果：``status`` 永远是
``pending``（裁决后无人回写）；程序化统计会把僵尸记录算成待办，报出一个只增
不减的积压数；最早被否决的配对若台账丢失便再也不会重新入队。

现在补后半段：

- :func:`queue_stats` —— 用台账把队列切成"真待办 / 已裁决"，给出积压天数；
- :func:`backlog_alert` —— 量阈值与滞留阈值分开判，缺一个就漏一类故障；
- :func:`prune_decided` —— 已裁决的行归档到 ``*.decided.jsonl``（保留 LLM
  当时的理由，台账只存布尔），队列因此有界且幂等；
- :func:`append_pending` —— 已裁决的配对不再入队。

## 台账损坏必须报错，不能当成空台账

"文件不存在" = 未启用，合法；"文件存在但解析失败" = 人工裁决全部失效，
必须抛 :class:`OverrideError`。若静默当成空台账，外部表现是"人工改的规则
突然不生效了"，而日志里什么都没说——这种故障比直接崩掉难查得多。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from common.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

#: 配对键的分隔符。选 ``||`` 而不是 ``-`` 或 ``:``：``unit_key`` 形如
#: ``src:xxx``，用冒号会与键本身的分节符混淆，反向解析就会切错位置。
PAIR_SEP: str = "||"

#: 项目根（``osint_video/``），用于把相对路径锚定到固定位置。
#: 不锚定的话，cron 从 ``/`` 启动与从项目目录启动会读写**两份不同**的台账，
#: 表现是"人工改的规则时灵时不灵"。
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: 台账默认路径（相对项目根）。
DEFAULT_OVERRIDES_PATH: str = "data/story_borderline_overrides.json"
#: 待确认队列默认路径（相对项目根）。
DEFAULT_QUEUE_PATH: str = "data/story_borderline_pending.jsonl"


class OverrideError(RuntimeError):
    """台账文件存在但无法解析。调用方**不得**把它当成空台账继续跑。"""


# ------------------------------------------------------------------ 键 --


def pair_id(left: Any, right: Any) -> str:
    """把两个 ``unit_key`` 归一成台账键（无序、跨进程稳定）。"""
    a, b = str(left or ""), str(right or "")
    if b < a:
        a, b = b, a
    return f"{a}{PAIR_SEP}{b}"


def parse_pair_id(text: str) -> tuple[str, str]:
    """把台账键拆回 ``(left, right)``（已排序）。格式非法时抛 ``ValueError``。"""
    raw = str(text or "")
    if PAIR_SEP not in raw:
        raise ValueError("配对键缺少分隔符 %r：%r" % (PAIR_SEP, raw))
    left, _, right = raw.partition(PAIR_SEP)
    left, right = left.strip(), right.strip()
    if not left or not right:
        raise ValueError("配对键有一侧为空：%r" % raw)
    return (left, right) if left <= right else (right, left)


# --------------------------------------------------------------- 路径 --


def resolve_path(path: str | Path | None, default: str) -> Path:
    """把（可能为相对的）路径锚定到项目根；``None`` 取默认值。"""
    target = Path(path or default)
    return target if target.is_absolute() else (PROJECT_ROOT / target)


# --------------------------------------------------------------- 台账 --


def _parse_decision(value: Any, key: str) -> bool:
    """把一条台账值解析成"是否同事件"。非法值抛 ``OverrideError``。

    接受三种形态：``true/false`` 简写、``"merge"/"keep_separate"`` 字符串、
    以及 ``{"decision": ...}`` 对象。**不接受模糊值**（如 ``"yes"``/``1``/空）：
    台账是人工意志的载体，含糊等于没写，且会静默改变聚类结果。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ("merge", "same", "same_story", "true"):
            return True
        if token in ("keep_separate", "separate", "unrelated", "false"):
            return False
        raise OverrideError("台账 %r 的 decision 无法识别：%r" % (key, value))
    if isinstance(value, Mapping):
        if "decision" not in value:
            raise OverrideError("台账 %r 缺少 decision 字段" % key)
        return _parse_decision(value["decision"], key)
    raise OverrideError("台账 %r 的值类型不支持：%s" % (key, type(value).__name__))


def load_overrides(
    path: str | Path | None = None,
) -> dict[tuple[str, str], bool]:
    """读取人工台账，返回 ``{无序对: 是否同事件}``。

    文件不存在 → 返回空字典（未启用，合法）。文件存在但解析失败 → 抛
    :class:`OverrideError`，由调用方决定是否中止——**绝不**当成空台账。

    解析粒度是"逐条"而不是"整份"：某一条写坏只让这一条失效并记 error，其余
    仍然生效。这样人手改台账时的一个笔误不会让全部裁决瞬间消失。
    """
    target = resolve_path(path, DEFAULT_OVERRIDES_PATH)
    if not target.exists():
        return {}

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise OverrideError("台账无法读取：%s（%s）" % (target, exc)) from exc

    text = raw.strip()
    if not text:
        # 空文件等价于"还没有任何裁决"，与"文件不存在"同义，不算损坏。
        return {}

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise OverrideError(
            "台账 JSON 解析失败：%s（%s）——请修复后重跑；"
            "本文件是人工裁决的唯一载体，损坏时拒跑比静默忽略安全" % (target, exc)
        ) from exc

    if not isinstance(payload, Mapping):
        raise OverrideError("台账顶层必须是对象，实际是 %s" % type(payload).__name__)

    overrides: dict[tuple[str, str], bool] = {}
    for key, value in payload.items():
        if str(key).startswith("_"):
            # 下划线开头保留给注释/元信息，不参与判定。
            continue
        try:
            pair = parse_pair_id(str(key))
            overrides[pair] = _parse_decision(value, str(key))
        except (ValueError, OverrideError) as exc:
            # 单条坏不影响其他条：一条写坏就整份作废，等于一次笔误抹掉全部
            # 人工成果，比漏一条严重得多。
            logger.error("跳过一条非法台账记录：%s", exc)
    if overrides:
        logger.info("人工台账已加载 %d 条（%s）", len(overrides), target)
    return overrides


def _atomic_write(target: Path, text: str) -> None:
    """先写同目录临时文件再 ``replace``，避免写一半被打断留下半截 JSON。

    台账是人手维护的，也是最难重建的文件之一（每条都对应一次人工判断）。
    非原子写在断电/cron kill 时会留下不可解析的半截文件，而我们的加载器
    对损坏文件是**拒绝运行**的，等于一次意外把整条聚类链路停掉。

    实现已上移到 :func:`common.atomic_io.atomic_write_text`，因为标定新鲜度戳
    与备份清单同样需要这套语义（含 Windows ``WinError 5`` 的有界重试）；此处
    保留函数名，调用方与既有测试无需改动。
    """
    atomic_write_text(target, text)


def save_overrides(
    overrides: Mapping[tuple[str, str], bool],
    path: str | Path | None = None,
    *,
    notes: Mapping[tuple[str, str], str] | None = None,
) -> Path:
    """整份写出台账（``decision`` 用可读的 ``merge``/``keep_separate``）。"""
    target = resolve_path(path, DEFAULT_OVERRIDES_PATH)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "_comment": "临界带人工裁决台账；键为 sorted([left,right]) 以 || 连接",
        "_updated_at": stamp,
    }
    for pair, merge in sorted(overrides.items()):
        key = pair_id(*pair)
        payload[key] = {
            "decision": "merge" if merge else "keep_separate",
            "note": (notes or {}).get(pair, ""),
            "reviewed_at": stamp,
        }
    _atomic_write(
        target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    logger.info("人工台账已写出 %d 条：%s", len(overrides), target)
    return target


def record_override(
    left: str,
    right: str,
    *,
    merge: bool,
    note: str = "",
    path: str | Path | None = None,
) -> Path:
    """写入/更新**一条**裁决，保留台账里其他条目与它们的备注。

    刻意做成"读-改-写"而不是直接 append：JSON 不是可追加格式，而覆盖整份
    文件又要求调用方先读出全部条目再传回来——CLI 每次只关心一条，来回搬运
    容易把别人的裁决弄丢。
    """
    target = resolve_path(path, DEFAULT_OVERRIDES_PATH)
    existing = load_overrides(target)

    # 备注要保留：load_overrides 只返回 bool，重建时按条重新读原始文件。
    notes: dict[tuple[str, str], str] = {}
    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8") or "{}")
            if isinstance(payload, Mapping):
                for key, value in payload.items():
                    if str(key).startswith("_") or not isinstance(value, Mapping):
                        continue
                    try:
                        notes[parse_pair_id(str(key))] = str(value.get("note") or "")
                    except ValueError:
                        continue
        except (ValueError, OSError):
            # 已有文件坏了，但本次是要**覆盖**某一条。让 load_overrides 在
            # 上面先抛，走不到这里；保险起见保留空备注而不是中断写入。
            notes = {}

    pair = parse_pair_id(pair_id(left, right))
    existing[pair] = bool(merge)
    if note:
        notes[pair] = note
    return save_overrides(existing, target, notes=notes)


# --------------------------------------------------------------- 队列 --


def load_pending(path: str | Path | None = None) -> list[dict[str, Any]]:
    """读取待确认队列；文件不存在返回空列表。

    单行解析失败只跳过该行并 warn——队列是**只追加**的日志，一行写坏不该
    让整份清单读不出来。这与台账的"损坏即报错"不同，因为队列丢了可以重建
    （重跑一轮就有），台账丢了不可重建。
    """
    target = resolve_path(path, DEFAULT_QUEUE_PATH)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except ValueError:
            logger.warning("待确认队列第 %d 行不是合法 JSON，已跳过", lineno)
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_pending(
    samples: Iterable[Mapping[str, Any]],
    path: str | Path | None = None,
    *,
    overrides: Mapping[tuple[str, str], bool] | None = None,
) -> int:
    """把本轮被提升的配对追加进队列，**已在队列里的配对不重复写**。

    去重是必须的：聚类是幂等可重跑的，同一条待确认项每跑一轮就多一份，
    人手翻清单时会被同一对刷屏，真正新出现的那几条反而看不见。

    ``overrides`` 里的配对**不再入队**：那是已经被人裁决过的，重新入队只会
    制造一条永远没人处理的僵尸记录，并把它从"新待办"里挤出去。注意这不等于
    "机器否决会被人的裁决继承"——台账命中时聚类根本不会走到提升分支，能走到
    这里的只可能是台账文件丢了或换了路径，那时 ``overrides`` 里也没有该对，
    该对会正常重新入队，人不会因此错过它。

    返回本次新增条数。
    """
    target = resolve_path(path, DEFAULT_QUEUE_PATH)
    known = {
        pair_id(row.get("left"), row.get("right"))
        for row in load_pending(target)
    }
    closed = set(overrides or {})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    fresh: list[str] = []
    skipped_closed = 0
    for sample in samples:
        key = pair_id(sample.get("left"), sample.get("right"))
        if key in known:
            continue
        if parse_pair_id(key) in closed:
            skipped_closed += 1
            continue
        known.add(key)
        record = dict(sample)
        record["queued_at"] = stamp
        record["status"] = "pending"
        fresh.append(json.dumps(record, ensure_ascii=False))

    if skipped_closed:
        logger.debug("待确认队列跳过 %d 条已裁决配对", skipped_closed)
    if not fresh:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(fresh) + "\n")
    logger.info("待确认队列新增 %d 条：%s", len(fresh), target)
    return len(fresh)


# ------------------------------------------------------ 队列生命周期 --
#
# 队列在 v1.5 落地时只做了"写入"，没有"关闭"。于是：人裁决后行仍在，``status``
# 永远是 ``pending``；任何程序化统计（积压监控、日报标注）都会被这批僵尸记录
# 骗，报出一个只增不减的待办数。这里补上后半段：能统计、能判断超期、能归档。

#: 归档文件名后缀。已裁决的行搬到这里而不是直接丢弃——``rationale`` 是 LLM
#: 当时的判断依据，事后复盘"机器为什么错"要靠它，而台账只存一个布尔。
ARCHIVE_SUFFIX: str = ".decided.jsonl"


def parse_queue_ts(text: Any) -> datetime | None:
    """解析 ``queued_at``；无法解析返回 ``None``。

    只接受带时区的 ISO 格式。**不按本地时区猜**：队列时间统一写 UTC（``Z``
    结尾），若把无时区的串当本地时间，部署在 UTC+8 的机器上算出的"积压天数"
    会凭空少 8 小时，14 天阈值就永远差那么一点才触发。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class QueueStats:
    """待确认队列的积压画像。

    与 :func:`load_pending` 的区别：那个返回全部行（含已裁决），这个回答
    "**还有多少条真正等着人看**"，以及"最老的一条压了多久"。
    """

    #: 队列累计条数（去重后，含已裁决）。
    total: int = 0
    #: 尚未被台账裁决的条数，即真正的待办量。
    pending: int = 0
    #: 已被台账裁决的条数（僵尸行，待 ``prune_decided`` 归档）。
    decided: int = 0
    #: 待办项里最老一条的积压天数；没有待办或无法计算时为 ``None``。
    oldest_days: float | None = None
    #: 待办项里缺少/无法解析 ``queued_at`` 的条数。非 0 说明这份队列是手写
    #: 或旧版本产的，积压天数不可信，需人看一眼。
    unknown_age: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "pending": self.pending,
            "decided": self.decided,
            "oldest_days": (
                None if self.oldest_days is None else round(self.oldest_days, 2)
            ),
            "unknown_age": self.unknown_age,
        }


def queue_stats(
    path: str | Path | None = None,
    *,
    overrides: Mapping[tuple[str, str], bool] | None = None,
    now: datetime | None = None,
) -> QueueStats:
    """统计队列积压情况。``now`` 可注入，便于测试不依赖真实时钟。"""
    rows = load_pending(path)
    closed = set(overrides or {})
    reference = now or datetime.now(timezone.utc)

    seen: set[tuple[str, str]] = set()
    pending = 0
    decided = 0
    unknown_age = 0
    oldest: float | None = None

    for row in rows:
        key = parse_pair_id(pair_id(row.get("left"), row.get("right")))
        if key in seen:
            # 队列理论上已去重，但历史文件可能有重复（v1.5 之前的版本不去重）。
            # 重复计入会让积压量虚高，直接决定告警是否提前触发。
            continue
        seen.add(key)
        if key in closed:
            decided += 1
            continue
        pending += 1
        stamp = parse_queue_ts(row.get("queued_at"))
        if stamp is None:
            unknown_age += 1
            continue
        age = (reference - stamp).total_seconds() / 86400.0
        if age < 0:
            # 时钟回拨或未来时间戳：不计入积压，但必须让 unknown_age 反映出来，
            # 否则"压了 -3 天"会被静默丢掉，人看不到异常。
            unknown_age += 1
            continue
        oldest = age if oldest is None else max(oldest, age)

    return QueueStats(
        total=len(seen),
        pending=pending,
        decided=decided,
        oldest_days=oldest,
        unknown_age=unknown_age,
    )


def backlog_alert(
    stats: QueueStats,
    *,
    max_pending: int,
    max_age_days: float,
) -> str | None:
    """积压是否该报警；不报返回 ``None``。

    两条独立阈值回答两个不同问题：**量**（人跟不上机器的产出速度）与
    **滞留**（人根本没在看）。只设量阈值挡不住"每天来一对、三个月没处理"，
    只设时长阈值挡不住"一天来五十对"。
    """
    reasons: list[str] = []
    if max_pending > 0 and stats.pending >= max_pending:
        reasons.append(f"待确认 {stats.pending} 条 ≥ 上限 {max_pending}")
    if (
        max_age_days > 0
        and stats.oldest_days is not None
        and stats.oldest_days >= max_age_days
    ):
        reasons.append(
            f"最老一条已压 {stats.oldest_days:.1f} 天 ≥ 上限 {max_age_days:g} 天"
        )
    if stats.unknown_age:
        reasons.append(f"{stats.unknown_age} 条入队时间不可解析，积压天数不可信")
    return "；".join(reasons) if reasons else None


def prune_decided(
    path: str | Path | None = None,
    *,
    overrides: Mapping[tuple[str, str], bool] | None = None,
) -> tuple[int, int]:
    """把已被台账裁决的行搬进归档文件，返回 ``(归档条数, 保留条数)``。

    幂等：第二次跑归档 0 条。队列被裁掉的行**不是删除**——原样追加到
    ``<队列名>.decided.jsonl``。台账丢了可以重建，LLM 当时的理由重建不出来。

    写回用临时文件 + 替换，避免裁剪过程中被打断导致队列半截。
    """
    target = resolve_path(path, DEFAULT_QUEUE_PATH)
    rows = load_pending(target)
    if not rows:
        return (0, 0)

    closed = set(overrides or {})
    keep: list[dict[str, Any]] = []
    archived: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = parse_pair_id(pair_id(row.get("left"), row.get("right")))
        if key in seen:
            # 重复行直接丢：留一份就够，两份会让归档文件也长出重复。
            continue
        seen.add(key)
        if key in closed:
            archived.append(row)
        else:
            keep.append(row)

    if archived:
        archive = target.with_name(target.name + ARCHIVE_SUFFIX)
        with archive.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in archived) + "\n"
            )
        logger.info("待确认队列归档 %d 条已裁决记录：%s", len(archived), archive)

    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in keep)
    # 保留行必须有结尾换行，否则下一轮 append 会把新行粘到最后一行尾部，
    # 两行并成一行 JSON 非法，整条记录静默消失。
    _atomic_write(target, text + "\n" if text else "")
    return (len(archived), len(keep))


__all__ = [
    "ARCHIVE_SUFFIX",
    "DEFAULT_OVERRIDES_PATH",
    "DEFAULT_QUEUE_PATH",
    "OverrideError",
    "PAIR_SEP",
    "PROJECT_ROOT",
    "QueueStats",
    "append_pending",
    "backlog_alert",
    "load_overrides",
    "load_pending",
    "pair_id",
    "parse_pair_id",
    "parse_queue_ts",
    "prune_decided",
    "queue_stats",
    "record_override",
    "resolve_path",
    "save_overrides",
]
