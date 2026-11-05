"""标定新鲜度戳（calibration stamp）：证明"语料变了之后重跑过标定"。

要解决的问题
------------
"语料结构变更后必须重跑标定"目前只是文档里的一句话。没人会记得。语料一改
（新增事实、修订真值、换探针集），``STORY_BORDERLINE_MARGIN`` 与提示词版本的
标定结论就**静默过期**：口径还在用，依据已经不成立。

这类失效和真值标注错误是同一类风险——**不报警、不报错、数字照样好看**。
带宽 0.05 的依据是"带内恰好漏合归零、信噪比 1:1"，那是 42 条语料上的结论；
语料扩到 200 条后这个数可能仍然可用，也可能已经在漏合，而运行日志里没有任何
区别。所以需要把"标定依据"变成一个可校验的物证。

做法
----
把标定的**输入**压成指纹，连同标定时间与结论摘要落到一个 sidecar：

``data/story_calibration_stamp.json``::

    {
      "borderline_margin": {
        "fingerprint": "sha256:...", "units": 50,
        "at": "2026-11-05T...Z", "detail": {"recommended_margin": 0.05, "missed": 0}
      },
      "judge_prompt": {
        "fingerprint": "sha256:...", "at": "...",
        "detail": {"probe_fingerprint": "sha256:...", "variant": "v2", "errors": 0}
      }
    }

上线前自检（``scripts/story_release_preflight.py``）重算指纹与戳比对：一致=
标定仍然有效；不一致=**语料变了但没重跑**，必须重跑后再上线。

三条刻意的设计
--------------
1. **指纹与行序无关**：按 ``unit_key`` 排序后再哈希。DB 返回顺序不保证稳定，
   不排序就会把"顺序变了"误报成"语料变了"——一张狼来了的告警卡，第三次之后
   就没人看了。
2. **null 真值 ≠ 缺真值**：``truth_story`` 为 ``null`` 是"独立事件"这一合法
   结论（独事实按定义不与任何事件合并），必须与"这一行没有真值标注"区分开。
   否则把独事实补上真值、或把真值改成 null，指纹都不变——最该检出的一类
   语料变更恰好检不出来。
3. **两类标定互不覆盖**：写提示词戳不会顺手刷新带宽戳。否则"只重跑了一半"
   看起来像全跑了，而这正是分两次作业时最容易发生的事。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from common.atomic_io import atomic_write_text
from common.paths import DATA_DIR, PROJECT_ROOT

#: 标定种类。两类标定的输入不同（语料 vs 语料+探针集），戳必须分开记。
KIND_MARGIN: str = "borderline_margin"
KIND_PROMPT: str = "judge_prompt"
KINDS: tuple[str, ...] = (KIND_MARGIN, KIND_PROMPT)

#: 缺省戳文件与探针集位置。
DEFAULT_STAMP_PATH: str = str(DATA_DIR / "story_calibration_stamp.json")
DEFAULT_PROBE_PATH: str = str(PROJECT_ROOT / "tests" / "fixtures" / "judge_probe.json")

#: 取真值语料。只取带 ``truth_story`` 键的行——注意 ``WHERE extension ? 'truth_story'``
#: 判的是"键存在"，与"值为 null"是两件事，后者是合法的独立事件标注。
_TRUTH_SQL: str = """
SELECT unit_key, extension->>'truth_story' AS truth
  FROM osint_knowledge_unit
 WHERE extension ? 'truth_story'
"""

#: 校验结果状态。
STATUS_OK: str = "ok"
STATUS_MISSING: str = "missing"
STATUS_STALE: str = "stale"
STATUS_CORRUPT: str = "corrupt"


def _sha256_lines(lines: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def fingerprint_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[str, int]:
    """把真值语料压成指纹，返回 ``(fingerprint, unit_count)``。

    纯函数，不碰库也不碰盘，便于在不连 DB 的环境里钉住行为。

    指纹内容为每行 ``unit_key \\t truth_marker`` 排序后哈希。``truth`` 为 ``None``
    时用哨兵 ``\\x00`` 表示（见模块文档第 2 条），空串是合法真值、不能与 null 混用。
    """
    lines: list[str] = []
    for row in rows:
        key = str(row.get("unit_key") or "").strip()
        if not key:
            # 主键为空的行不该存在；直接跳过但会让 units 少 1，比在指纹里
            # 塞一个空键更能暴露问题（计数与库里对不上就是线索）。
            continue
        truth = row.get("truth")
        marker = "\x00" if truth is None else str(truth).strip()
        lines.append(f"{key}\t{marker}")
    lines.sort()
    return _sha256_lines(lines), len(lines)


def corpus_rows() -> list[dict[str, Any]]:
    """从库里取真值语料。库不可用时抛 :class:`common.db.DatabaseUnavailableError`。"""
    from common.db import fetch_all  # 延迟导入：纯函数单测不需要 DB 依赖

    return fetch_all(_TRUTH_SQL)


def corpus_fingerprint() -> tuple[str, int]:
    """当前真值语料的指纹与规模。"""
    return fingerprint_rows(corpus_rows())


def probe_fingerprint(path: str | Path | None = None) -> str | None:
    """探针集文件的内容指纹；文件不存在返回 ``None``。

    ``None`` 与"空文件指纹"必须区分：前者说明标定依据缺失，后者说明探针集
    被清空了——两者在自检里应给出不同提示，混成一个会让排查方向跑偏。
    """
    target = Path(path or DEFAULT_PROBE_PATH)
    if not target.exists():
        return None
    return _sha256_lines([target.read_text(encoding="utf-8")])


def stamp_path(path: str | Path | None = None) -> Path:
    target = Path(path or DEFAULT_STAMP_PATH)
    return target if target.is_absolute() else (PROJECT_ROOT / target)


def read_stamp(path: str | Path | None = None) -> dict[str, Any]:
    """读戳文件。缺文件返回空 dict；**损坏则抛** ``ValueError``。

    与台账口径一致：损坏静默当空，会让"标定过期"退化成"从未标定"，
    两种处置方式不同（前者要重跑，后者可能只是戳文件被误删），必须能分辨。
    """
    target = stamp_path(path)
    if not target.exists():
        return {}
    raw = target.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"标定戳文件损坏，无法解析：{target}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"标定戳文件顶层应为对象：{target}")
    return data


def write_stamp(
    kind: str,
    *,
    fingerprint: str,
    units: int | None = None,
    detail: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    """写入（或更新）某一类标定的戳，返回戳文件路径。

    **只动 ``kind`` 这一个键**，另一类标定的记录原样保留（模块文档第 3 条）。
    """
    if kind not in KINDS:
        raise ValueError(f"未知标定种类：{kind!r}（可选 {KINDS}）")
    target = stamp_path(path)
    try:
        data = read_stamp(target)
    except ValueError:
        # 戳文件已损坏时重建：它的价值在于"上一次标定是什么时候、依据什么"，
        # 损坏的旧戳没有可保留信息，而卡在这里会让重跑标定也无法落账。
        data = {}

    entry: dict[str, Any] = {
        "fingerprint": fingerprint,
        "at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if units is not None:
        entry["units"] = units
    if detail:
        entry["detail"] = dict(detail)
    data[kind] = entry

    atomic_write_text(
        target, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return target


def verify(
    kind: str,
    fingerprint: str,
    *,
    probe: str | None = None,
    path: str | Path | None = None,
) -> tuple[str, str]:
    """校验某类标定是否仍然对应当前输入，返回 ``(status, message)``。

    Args:
        kind: 标定种类（``KIND_MARGIN`` / ``KIND_PROMPT``）。
        fingerprint: 当前输入的指纹。
        probe: 探针集指纹；仅当该类标定依赖探针集时传入，``None`` 表示不校验。
        path: 戳文件路径。

    Returns:
        ``(status, message)``，``status`` 取 :data:`STATUS_OK` /
        :data:`STATUS_MISSING` / :data:`STATUS_STALE` / :data:`STATUS_CORRUPT`。
    """
    try:
        data = read_stamp(path)
    except ValueError as exc:
        return STATUS_CORRUPT, str(exc)

    entry = data.get(kind)
    if not isinstance(entry, dict) or not entry.get("fingerprint"):
        return STATUS_MISSING, f"没有 {kind} 的标定记录（从未跑过或戳被删除）"

    recorded = str(entry["fingerprint"])
    if recorded != fingerprint:
        return (
            STATUS_STALE,
            f"{kind} 标定已过期：语料指纹 {recorded[:19]}… ≠ 当前 {fingerprint[:19]}…",
        )

    recorded_probe = (entry.get("detail") or {}).get("probe_fingerprint")
    if probe is not None and recorded_probe and recorded_probe != probe:
        return (
            STATUS_STALE,
            f"{kind} 标定已过期：探针集 {str(recorded_probe)[:19]}… ≠ 当前 {probe[:19]}…",
        )

    at = entry.get("at", "未知时间")
    return STATUS_OK, f"{kind} 标定有效（{at}，指纹 {recorded[:19]}…）"


@dataclass(frozen=True)
class StampSnapshot:
    """一次自检需要的全部标定事实，便于上层一次性取齐。"""

    margin: tuple[str, str] = field(default=(STATUS_MISSING, ""))
    prompt: tuple[str, str] = field(default=(STATUS_MISSING, ""))
    corpus_fingerprint: str = ""
    units: int = 0
    probe_fingerprint: str | None = None

    @property
    def worst(self) -> str:
        """最严重状态：任一类过期/损坏即为该状态，否则缺失优于过期。"""
        order = [STATUS_CORRUPT, STATUS_STALE, STATUS_MISSING, STATUS_OK]
        for status in order:
            if status in (self.margin[0], self.prompt[0]):
                return status
        return STATUS_OK


__all__ = [
    "KIND_MARGIN",
    "KIND_PROMPT",
    "KINDS",
    "STATUS_OK",
    "STATUS_MISSING",
    "STATUS_STALE",
    "STATUS_CORRUPT",
    "DEFAULT_STAMP_PATH",
    "DEFAULT_PROBE_PATH",
    "StampSnapshot",
    "corpus_rows",
    "corpus_fingerprint",
    "fingerprint_rows",
    "probe_fingerprint",
    "read_stamp",
    "stamp_path",
    "verify",
    "write_stamp",
]
