"""上线前自检（release preflight）：把 §9 里三条"运维必须记得"变成可执行的检查。

为什么需要这个脚本
------------------
P1 收官时留下了三句写在文档里的话：

1. "把人工台账与队列文件纳入备份清单，丢了不可重建"；
2. "语料结构变更后必须重跑标定"；
3. "队列积压要有人看"。

第 3 条已经做成运行时告警（``review_backlog``）。前两条仍然只是**句子**：
它们依赖某个人在某个时刻想起来。而这两件事失效后的表现恰好都很安静——
备份缺失要到真正需要恢复时才知道；标定过期则表现为"口径还在用、依据已经
不成立"，运行日志里没有任何区别。

这个脚本的职责是把它们变成**一次退出码**：绿了就是真绿，红了就有一条具体
的、可立刻执行的修复命令。

检查项与分级
------------
分级刻意保守。**只有"恢复不了"和"依据不成立"才算 FAIL**，其余一律 WARN：

===============  ==================================================
检查              判据
===============  ==================================================
``calibration``  语料/探针集指纹与标定戳比对；缺失/过期/损坏 → FAIL
``backup``       最近一次备份的 SHA256SUMS 逐文件复算；不符 → FAIL
``backup.age``   从未备份或最近备份超过阈值 → WARN（附可执行命令）
``config``       运行配置与已定档口径不一致 → WARN
``backlog``      队列条数/滞留天数超阈 → WARN
===============  ==================================================

为什么不把"从未备份"判成 FAIL：它确实是缺口，但第一次跑自检必然如此，一上来
满屏红会让人直接忽略这个工具。**告警的价值取决于它红了之后有人动**，所以只在
"备份存在但已损坏"（真不可恢复）时判红。这一条写在交付文档里，不靠注释口口相传。

``STORY_BORDERLINE_JUDGE=off`` 显示为绿 + 「待裁定」标注，不是缺陷：生产是否常开
临界带复核尚未拍板，自检不该替人做决定，但必须让人看见当前形态。

用法
----
    python scripts/story_release_preflight.py
    python scripts/story_release_preflight.py --backup backups/story_state
    python scripts/story_release_preflight.py --json
    python scripts/story_release_preflight.py --max-backup-age-days 14

退出码：0 无 FAIL；1 有 FAIL；2 库不可用（标定检查无法进行，其余照跑）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.db import DatabaseUnavailableError  # noqa: E402
from common.paths import PROJECT_ROOT  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge import calibration_stamp as stamp  # noqa: E402
from knowledge.borderline_review import (  # noqa: E402
    ARCHIVE_SUFFIX,
    backlog_alert,
    load_overrides,
    queue_stats,
    resolve_path,
)

#: 检查级别。``skip`` 只用于"环境不具备、无法判定"，**不计入 FAIL**。
LEVEL_OK: str = "ok"
LEVEL_WARN: str = "warn"
LEVEL_FAIL: str = "fail"
LEVEL_SKIP: str = "skip"

#: 已定档口径（依据见项目档案 §9）。与运行配置不一致时 WARN 而非 FAIL：
#: 有意调参是允许的，但必须在自检里显式看见，而不是让下一次事故去发现。
DOCKED_MARGIN: float = 0.05
DOCKED_VARIANT: str = "v2"

#: 缺省备份根目录。刻意放在项目树内的独立目录而不是 data/：备份不该躺在
#: 被备份对象的旁边（``--backup`` 一抖就会连备份一起覆盖）。
DEFAULT_BACKUP_ROOT: str = str(PROJECT_ROOT / "backups" / "story_state")

#: 备份目录名的时间戳格式（文件名排序即时间序，省掉一次 stat）。
_TS_FORMAT: str = "%Y%m%dT%H%M%SZ"


@dataclass
class Check:
    """一条检查结果。``action`` 是给人看的下一步，缺省留空。"""

    name: str
    level: str
    title: str
    detail: str = ""
    action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
        }


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.level == LEVEL_FAIL]

    def count(self, level: str) -> int:
        return sum(1 for c in self.checks if c.level == level)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.as_dict() for c in self.checks],
            "summary": {
                "ok": self.count(LEVEL_OK),
                "warn": self.count(LEVEL_WARN),
                "fail": self.count(LEVEL_FAIL),
                "skip": self.count(LEVEL_SKIP),
            },
            "context": self.context,
        }


# ------------------------------------------------------------ 状态文件 --


def state_files() -> list[Path]:
    """需要备份的三件状态文件（绝对路径，顺序即报告顺序）。"""
    settings = get_settings()
    overrides = resolve_path(
        settings.story_borderline_overrides, "data/story_borderline_overrides.json"
    )
    pending = resolve_path(
        settings.story_borderline_pending, "data/story_borderline_pending.jsonl"
    )
    return [overrides, pending, pending.with_name(pending.name + ARCHIVE_SUFFIX)]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# -------------------------------------------------------------- 备份 --


def do_backup(root: Path, files: Sequence[Path]) -> Path | None:
    """把存在的状态文件复制到 ``root/<UTC时间戳>/``，写清单与摘要。

    只备份存在的文件：台账/归档在首次部署时本来就不存在，为此造空文件会让
    "文件存在"失去信息量（台账文件的**存在与否**本身语义就是启用/未启用）。

    Returns:
        备份目录；没有任何文件可备份时返回 ``None``。
    """
    existing = [f for f in files if f.exists()]
    if not existing:
        return None

    raw_ts = datetime.now(timezone.utc).strftime(_TS_FORMAT)
    target = root / raw_ts
    # 同一秒内跑两次：加序号而不是覆盖，否则第二次备份会静默吃掉第一次。
    index = 1
    while target.exists():
        target = root / f"{raw_ts}-{index}"
        index += 1
    target.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origin_root": str(PROJECT_ROOT),
        "files": [],
    }
    sums: list[str] = []
    for source in existing:
        destination = target / source.name
        shutil.copy2(source, destination)
        digest = _sha256_file(destination)
        manifest["files"].append(
            {
                "name": source.name,
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "source": str(source),
            }
        )
        sums.append(f"{digest}  {source.name}")

    (target / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    (target / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def latest_backup(root: Path) -> Path | None:
    """按目录名取最新一次备份；名字不合格式的目录忽略。

    用目录名而不是 mtime 排序：``shutil.copy2`` 与后续手工操作都会改 mtime，
    而目录名里的时间戳是备份那一刻的**事实**，且字符串序即时间序。
    """
    if not root.is_dir():
        return None
    valid: list[Path] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        head = entry.name.split("-")[0]
        try:
            datetime.strptime(head, _TS_FORMAT)
        except ValueError:
            continue
        valid.append(entry)
    if not valid:
        return None
    return max(valid, key=lambda p: p.name)


def verify_backup(directory: Path) -> tuple[bool, str]:
    """复算一次备份的 SHA256SUMS。返回 ``(ok, message)``。

    这一步是"备份存在"与"备份可用"的分界。没有它，备份清单只是几个文件路径，
    真正的损坏（半截复制、磁盘错误）要等到恢复那天才暴露。
    """
    sums = directory / "SHA256SUMS"
    if not sums.exists():
        return False, f"备份缺少 SHA256SUMS：{directory}"
    problems: list[str] = []
    total = 0
    for line in sums.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            problems.append(f"摘要行格式非法：{line!r}")
            continue
        expected, name = parts[0], parts[1].strip()
        target = directory / name
        if not target.exists():
            problems.append(f"清单里有、目录里没有：{name}")
            continue
        actual = _sha256_file(target)
        total += 1
        if actual != expected:
            problems.append(f"摘要不符：{name}")
    if problems:
        return False, "；".join(problems[:3])
    return True, f"{total} 个文件摘要全部一致"


# ------------------------------------------------------------ 各检查组 --


def check_calibration(report: Report) -> None:
    """语料/探针集是否仍与标定戳一致。"""
    try:
        fingerprint, units = stamp.corpus_fingerprint()
    except DatabaseUnavailableError as exc:
        report.add(
            Check(
                "calibration",
                LEVEL_SKIP,
                "标定新鲜度无法判定：数据库不可用",
                str(exc),
                "恢复 DB 后重跑本脚本；标定结论未被否定，只是无法核对",
            )
        )
        report.context["corpus_units"] = None
        return

    probe = stamp.probe_fingerprint()
    report.context["corpus_units"] = units
    report.context["corpus_fingerprint"] = fingerprint[:19]
    report.context["probe_fingerprint"] = (probe or "")[:19] or None

    cases = (
        (stamp.KIND_MARGIN, stamp.KIND_MARGIN, None),
        (stamp.KIND_PROMPT, stamp.KIND_PROMPT, probe),
    )
    for name, kind, probe_fp in cases:
        status, message = stamp.verify(kind, fingerprint, probe=probe_fp)
        if status == stamp.STATUS_OK:
            report.add(Check(f"calibration.{name}", LEVEL_OK, message))
        elif status == stamp.STATUS_MISSING:
            report.add(
                Check(
                    f"calibration.{name}",
                    LEVEL_FAIL,
                    f"{name} 从未标定（戳缺失）",
                    message + "。注意：标定脚本在样本不足时会**拒绝落戳**，"
                    "所以「戳缺失」有两种成因——真没跑过，或跑了但样本不够",
                    _rerun_command(name),
                )
            )
        elif status == stamp.STATUS_CORRUPT:
            report.add(
                Check(
                    f"calibration.{name}",
                    LEVEL_FAIL,
                    f"标定戳文件损坏：{name} 无法核对",
                    message,
                    f"删除 {stamp.stamp_path()} 后重跑标定：{_rerun_command(name)}",
                )
            )
        else:  # STATUS_STALE
            report.add(
                Check(
                    f"calibration.{name}",
                    LEVEL_FAIL,
                    f"{name} 标定已过期：输入变了但没重跑",
                    message,
                    _rerun_command(name),
                )
            )


def _rerun_command(kind: str) -> str:
    if kind == stamp.KIND_MARGIN:
        return "python scripts/calibrate_borderline_margin.py"
    return "python scripts/calibrate_judge_prompt.py --variants v2"


def check_backup(report: Report, files: Sequence[Path], root: Path, max_age_days: float) -> None:
    """备份清单完整性、最近一次备份的可用性与新鲜度。"""
    present = [f for f in files if f.exists()]
    missing = [f.name for f in files if not f.exists()]
    if not present:
        report.add(
            Check(
                "backup.targets",
                LEVEL_WARN,
                "三件状态文件当前都不存在（首次部署形态）",
                "台账/队列/归档均未生成，此时无人工成果可丢",
            )
        )
    else:
        report.add(
            Check(
                "backup.targets",
                LEVEL_OK,
                f"状态文件 {len(present)}/{len(files)} 件在册",
                "存在：" + "、".join(f.name for f in present)
                + (("；缺失：" + "、".join(missing)) if missing else ""),
            )
        )

    directory = latest_backup(root)
    if directory is None:
        report.add(
            Check(
                "backup.age",
                LEVEL_WARN,
                "从未做过备份",
                f"备份根目录：{root}",
                f"python scripts/story_release_preflight.py --backup {root}",
            )
        )
        report.add(
            Check(
                "backup.integrity",
                LEVEL_SKIP,
                "无可校验的备份",
                "先完成一次备份",
            )
        )
        return

    ok, message = verify_backup(directory)
    if ok:
        report.add(Check("backup.integrity", LEVEL_OK, f"最近备份可用（{directory.name}）", message))
    else:
        report.add(
            Check(
                "backup.integrity",
                LEVEL_FAIL,
                f"最近备份已损坏：{directory.name}",
                message,
                f"重新执行 --backup {root}；损坏的这份不要删，先留证",
            )
        )

    head = directory.name.split("-")[0]
    try:
        made = datetime.strptime(head, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        report.add(
            Check(
                "backup.age",
                LEVEL_WARN,
                f"备份目录名不含可解析时间戳：{directory.name}",
                "无法判断备份新鲜度",
                f"改用 --backup 生成规范命名的备份",
            )
        )
        return
    age_days = (datetime.now(timezone.utc) - made).total_seconds() / 86400
    if age_days > max_age_days:
        report.add(
            Check(
                "backup.age",
                LEVEL_WARN,
                f"最近备份已 {age_days:.1f} 天（阈值 {max_age_days:g}）",
                f"{directory.name}",
                f"python scripts/story_release_preflight.py --backup {root}",
            )
        )
    else:
        report.add(
            Check("backup.age", LEVEL_OK, f"最近备份 {age_days:.1f} 天前", directory.name)
        )


def check_config(report: Report) -> None:
    """运行配置与已定档口径的偏差。"""
    settings = get_settings()
    drift: list[str] = []
    if abs(float(settings.story_borderline_margin) - DOCKED_MARGIN) > 1e-9:
        drift.append(
            f"STORY_BORDERLINE_MARGIN={settings.story_borderline_margin}"
            f"（定档 {DOCKED_MARGIN}）"
        )
    if str(settings.judge_prompt_variant) != DOCKED_VARIANT:
        drift.append(
            f"JUDGE_PROMPT_VARIANT={settings.judge_prompt_variant}"
            f"（定档 {DOCKED_VARIANT}）"
        )
    if drift:
        report.add(
            Check(
                "config.docked",
                LEVEL_WARN,
                "运行配置偏离已定档口径",
                "；".join(drift),
                "确认是有意调整；若否，改回 .env 并重跑本脚本",
            )
        )
    else:
        report.add(
            Check(
                "config.docked",
                LEVEL_OK,
                f"口径与定档一致（margin={DOCKED_MARGIN}，variant={DOCKED_VARIANT}）",
            )
        )

    judge = str(settings.story_borderline_judge).lower()
    if judge == "off":
        report.add(
            Check(
                "config.borderline_judge",
                LEVEL_OK,
                "临界带复核当前关闭（待裁定项 1，不是缺陷）",
                "开启后临界带配对改由 LLM 复核，真机实测 2 次调用；"
                "生产为纯 CPU，首轮需手工预热规避冷加载撞 JUDGE_TIMEOUT_SEC",
            )
        )
    else:
        report.add(
            Check(
                "config.borderline_judge",
                LEVEL_OK,
                f"临界带复核已开启（{judge}）",
                "确认首轮预热已做，否则冷加载可能超时并回落规则判定",
            )
        )

    # 跨窗口召回是另一个"会改变事件数"的开关，与临界带复核同级：
    # 开与不开，同一批语料的事件数不同。自检必须把当前状态说出来，
    # 否则线上看到事件数变化时，第一反应会是"判定器坏了"。
    if bool(settings.story_cross_window):
        report.add(
            Check(
                "config.cross_window",
                LEVEL_OK,
                "跨窗口召回已开启",
                "窗外 42 天内已归属的锚点参与召回；护栏为 vector 口径 + 共同主体 + "
                "余弦≥0.70。真机 A/B：拆散 6→2、误合并 0、额外调用 0；"
                "单轮对照可加 --no-cross-window",
            )
        )
    else:
        report.add(
            Check(
                "config.cross_window",
                LEVEL_OK,
                "跨窗口召回当前关闭（待裁定项 2，不是缺陷）",
                "关闭时跨月发酵的同一件事会被切成两个事件、独立来源数各半",
            )
        )


def _declared_override_count(path: Path) -> int | None:
    """台账文件里**声明**了几条裁决（不含 ``_`` 前缀的元信息）。

    为什么需要这个：``load_overrides`` 对单条坏记录是"跳过并记 error"，只有整份
    JSON 损坏才抛异常。这个取舍是对的（一次笔误不该抹掉全部人工成果），但代价是
    **单条失效在调用方完全看不见**——返回的 dict 少一条，没有任何信号。而一条
    裁决失效意味着该配对退回 LLM 判定，人拍过的板被无声推翻。

    文件不存在或无法解析时返回 ``None``（后者由加载器抛异常，不在这里重复处理）。
    """
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return sum(1 for key in payload if not str(key).startswith("_"))


def check_backlog(report: Report) -> None:
    """队列积压：条数与滞留双维度；顺带核对台账是否有条目被静默跳过。"""
    settings = get_settings()
    overrides_path = resolve_path(
        settings.story_borderline_overrides, "data/story_borderline_overrides.json"
    )
    pending_path = resolve_path(
        settings.story_borderline_pending, "data/story_borderline_pending.jsonl"
    )
    if not pending_path.exists() and not overrides_path.exists():
        report.add(
            Check("backlog", LEVEL_OK, "无待确认队列与台账", "还没有配对需要人工二审")
        )
        return

    try:
        overrides = load_overrides(settings.story_borderline_overrides)
    except Exception as exc:  # OverrideError 等：整份坏掉必须暴露
        report.add(
            Check(
                "backlog",
                LEVEL_FAIL,
                "人工台账无法解析，积压无法统计",
                str(exc),
                f"检查 {settings.story_borderline_overrides}；损坏文件必须修或改名后重建",
            )
        )
        return

    declared = _declared_override_count(overrides_path)
    if declared is not None and declared != len(overrides):
        missing = declared - len(overrides)
        report.add(
            Check(
                "overrides.coverage",
                LEVEL_WARN,
                f"台账有 {missing} 条裁决未生效（已声明 {declared}、实际加载 {len(overrides)}）",
                "加载器对单条坏记录是跳过不抛错，所以这里才专门核对条数。"
                "失效那条会退回 LLM 判定，等于人拍过的板被无声推翻",
                "grep '跳过一条非法台账记录' logs/app.log 定位坏条目后修正",
            )
        )
    else:
        report.add(
            Check(
                "overrides.coverage",
                LEVEL_OK,
                f"台账 {len(overrides)} 条裁决全部生效",
            )
        )

    if not pending_path.exists():
        report.add(
            Check("backlog", LEVEL_OK, "无待确认队列文件", "说明还没有配对需要人工二审")
        )
        return

    stats = queue_stats(pending_path, overrides=overrides)
    report.context["backlog"] = stats.as_dict()
    alert = backlog_alert(
        stats,
        max_pending=settings.story_review_backlog_max,
        max_age_days=settings.story_review_max_age_days,
    )
    if alert:
        report.add(
            Check(
                "backlog",
                LEVEL_WARN,
                "待确认队列积压超阈",
                alert,
                "python scripts/borderline_review.py list --pending-only"
                " → approve/reject → compact",
            )
        )
    else:
        oldest = "—" if stats.oldest_days is None else f"{stats.oldest_days:.1f}"
        report.add(
            Check(
                "backlog",
                LEVEL_OK,
                f"队列待办 {stats.pending} 条，最老 {oldest} 天",
            )
        )


# ---------------------------------------------------------------- 输出 --


def _format(report: Report) -> str:
    icons = {LEVEL_OK: "[ OK ]", LEVEL_WARN: "[WARN]", LEVEL_FAIL: "[FAIL]", LEVEL_SKIP: "[SKIP]"}
    lines = ["情报体系 P1 · 上线前自检", ""]
    for check in report.checks:
        lines.append(f"{icons[check.level]} {check.title}")
        if check.detail:
            lines.append(f"        {check.detail}")
        if check.action:
            lines.append(f"        → {check.action}")
    lines.append("")
    lines.append(
        f"合计：{report.count(LEVEL_OK)} OK / {report.count(LEVEL_WARN)} WARN / "
        f"{report.count(LEVEL_FAIL)} FAIL / {report.count(LEVEL_SKIP)} SKIP"
    )
    if report.failed:
        lines.append("")
        lines.append("有 FAIL 项：按上面 → 给出的命令处理后重跑，不要带着过期依据上线。")
    return "\n".join(lines)


def build_report(
    *,
    backup_root: Path,
    max_backup_age_days: float,
    do_backup_now: bool,
) -> Report:
    report = Report()
    files = state_files()
    if do_backup_now:
        created = do_backup(backup_root, files)
        report.context["backup_created"] = str(created) if created else None
        if created is None:
            report.add(
                Check(
                    "backup.create",
                    LEVEL_WARN,
                    "本次没有可备份的文件",
                    "三件状态文件都不存在，未创建备份目录",
                )
            )
        else:
            report.add(Check("backup.create", LEVEL_OK, f"已备份到 {created}"))

    check_calibration(report)
    check_backup(report, files, backup_root, max_backup_age_days)
    check_config(report)
    check_backlog(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="情报体系 P1 上线前自检")
    parser.add_argument(
        "--backup",
        nargs="?",
        const=DEFAULT_BACKUP_ROOT,
        default=None,
        metavar="DIR",
        help=f"先做一次备份（缺省目录 {DEFAULT_BACKUP_ROOT}），再自查",
    )
    parser.add_argument(
        "--max-backup-age-days",
        type=float,
        default=7.0,
        help="备份新鲜度阈值（天），超过则 WARN。缺省 7",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    backup_root = Path(args.backup or DEFAULT_BACKUP_ROOT)
    if not backup_root.is_absolute():
        backup_root = PROJECT_ROOT / backup_root

    report = build_report(
        backup_root=backup_root,
        max_backup_age_days=args.max_backup_age_days,
        do_backup_now=args.backup is not None,
    )

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format(report))

    if report.failed:
        return 1
    if any(c.level == LEVEL_SKIP and c.name == "calibration" for c in report.checks):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
