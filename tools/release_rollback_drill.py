# -*- coding: utf-8 -*-
"""W1 Legion IDE 发布回滚演练（release rollback drill，隔离、可重复、一条命令）。

背景：``tools/release_legion_ide.py`` 已有 ``--promote/--switch-current/--rollback-to/
--status``，W1 审计做过 19/19 隔离演练，但没有**可重复执行**的命令与测试。本工具把
「promote → switch current junction → status(drift) → rollback → status」固化成一条
命令，默认只在 tempfile 临时根里演练，**绝不触碰生产 release root**。

用法：::

  python -X utf8 tools/release_rollback_drill.py                  # 默认：临时隔离根 + Markdown 报告
  python -X utf8 tools/release_rollback_drill.py --json           # 机器可读报告（CI/门禁）
  python -X utf8 tools/release_rollback_drill.py --markdown       # 显式 Markdown
  python -X utf8 tools/release_rollback_drill.py --scenario drift_detected --json
  python -X utf8 tools/release_rollback_drill.py --release-root C:\\Temp\\drill_x --keep  # 保留现场
  python -X utf8 tools/release_rollback_drill.py --release-root C:\\Temp\\drill_x --cleanup

退出码：``0`` 全部场景/硬检查通过；``1`` 演练失败或基建异常；``2`` 参数错误（argparse）。

安全边界（代码强制，非口头约定）：
  - 默认在 ``tempfile.mkdtemp()`` 下建隔离 release root，跑完自动清理（``--keep`` 保留）；
  - ``--release-root`` 与生产 ``DEFAULT_RELEASE_ROOT`` 有路径重叠（相等/父子/经链接解析后
    重叠）→ 直接拒跑，不落任何文件；与仓库工作树重叠同样拒绝（避免脏化 git）；
  - 显式 ``--release-root`` 必须不存在或为空目录（防覆盖他人发布现场），且不能是链接；
  - 只读生产 ``current.json``/``current`` 做前后快照对比（advisory，不写、不切）；
  - 不重启 8787/8103、不 build/promote 生产 artifact、不修改任何生产文件。

演练场景（每个场景在演练父根下用独立子根，互不污染）：
  1. ``happy_ab_rollback``    A→B→回滚 A 全链路；drift=false；可重复一轮
  2. ``bad_artifact_rejected`` 坏制品（空 manifest/hash 不符/缺 manifest/缺文件）一律 fail-closed
  3. ``drift_detected``       指针变了 junction 没切（及反向）→ status.drift=true 检出并可修
  4. ``mid_failure_recovery`` 中途崩溃（junction 已切指针未写、junction 丢失）→ 可恢复
  另附 ``isolation_safety``    演练根隔离 / 写入仅限演练根 / 生产快照未变（advisory）

实现上复用 ``tools/release_legion_ide.py`` 的 ``promote``/``rollback_to``/
``switch_current_link``/``status``/``verify_dir``（importlib 加载，不改该文件）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

SCHEMA = "legion-ide-rollback-drill/v1"
REPO = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
LEGION_TOOL = TOOLS_DIR / "release_legion_ide.py"
CURRENT_NAME = "current"
DEFAULT_TEMP_PREFIX = "legion_ide_drill_"

# 场景顺序即报告顺序；--scenario 子集按此顺序输出（用 * 选择全部）。
SCENARIO_TITLES = {
    "happy_ab_rollback": "成功链路：promote A→B→回滚 A（指针/junction/drift/内容四方对账）",
    "bad_artifact_rejected": "坏制品拒绝：空 manifest / hash 不符 / 缺 manifest / 缺文件均 fail-closed",
    "drift_detected": "drift 检出：指针变了 junction 没切（及反向漂移）→ drift=true 并可修",
    "mid_failure_recovery": "中途失败恢复：junction 已切/指针未写、junction 丢失 → 恢复一致",
    "isolation_safety": "隔离安全：演练根与生产不重叠、写入仅限演练根、生产快照未变",
}
DEFAULT_SCENARIOS = tuple(s for s in SCENARIO_TITLES if s != "isolation_safety")

NOTES = (
    "复用 tools/release_legion_ide.py 的 promote/rollback_to/switch_current_link/status/"
    "verify_dir，未修改该文件",
    "Windows 用 mklink /J 创建目录 junction；POSIX 用 symlink（由 release_legion_ide 内置）",
    "不重启 8787/8103、不写生产 current.json、不切生产 junction、不改 release artifact",
    "退出码：0=全部通过；1=演练失败/基建异常；2=参数错误",
    "生产快照对比为 advisory：并发发布方改动会告警，但不代表本工具写入（本工具只读生产）",
)

_MARK = {True: "PASS", False: "FAIL"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_utf8_stdout() -> None:
    """Windows 控制台默认 cp936：统一改 UTF-8，避免中文报告乱码/写失败。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 非标准流
            pass


def load_legion_module(path: Path | None = None):
    """importlib 加载 ``tools/release_legion_ide.py``（不要求 tools 是 package）。"""
    path = Path(path or LEGION_TOOL)
    spec = importlib.util.spec_from_file_location("drill_release_legion_ide", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载复用模块: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _path_key(path) -> str:
    """路径归一化（realpath 解链接 + normcase 大小写归一），用于重叠判断。"""
    try:
        real = os.path.realpath(os.path.abspath(str(path)))
    except OSError:  # pragma: no cover - 极少数非法路径
        real = os.path.abspath(str(path))
    return os.path.normcase(os.path.normpath(real))


def paths_overlap(a, b) -> bool:
    """a、b 是否相等或互为父子（按链接解析后的真实路径判断）。"""
    ka, kb = _path_key(a), _path_key(b)
    if ka == kb:
        return True
    try:
        common = os.path.commonpath([ka, kb])
    except ValueError:  # 不同盘符
        return False
    return common in (ka, kb)


def is_under(child, parent) -> bool:
    """child 是否位于 parent 之内（含相等；按真实路径判断）。"""
    kc, kp = _path_key(child), _path_key(parent)
    if kc == kp:
        return True
    try:
        return os.path.commonpath([kc, kp]) == kp
    except ValueError:
        return False


def _is_drive_root(path: Path) -> bool:
    key = os.path.normpath(os.path.abspath(str(path)))
    return os.path.dirname(key) == key


def _path_lexists(path: Path) -> bool:
    """链接自身存在即算 True（目标丢失的 junction/symlink 也算）。"""
    return os.path.lexists(str(path)) or Path(path).exists()


def _is_dir_link(path: Path) -> bool:
    """True 表示 path 是目录 junction/symlink（Windows junction 在 py<3.12 不是 islink）。"""
    path = Path(path)
    try:
        if getattr(path, "is_junction", None) and path.is_junction():
            return True
    except OSError:
        return False
    try:
        os.readlink(str(path))
        return True
    except (OSError, ValueError):
        return False


def assert_isolated(release_root, production_root, repo: Path | None = None) -> None:
    """演练根隔离断言：不与生产 release root / 仓库工作树重叠，且不是盘符根。

    这是本工具的硬安全边界：任何重叠都直接抛 ``RuntimeError``，调用方不落任何文件。
    """
    root = Path(release_root)
    if _is_drive_root(root):
        raise RuntimeError("拒绝：演练根不能是盘符根目录：%s" % root)
    if paths_overlap(root, production_root):
        raise RuntimeError(
            "拒绝：演练根 %s 与生产 release root %s 路径重叠——本工具只允许隔离 temp 根，"
            "生产发布需人工/L2 窗口" % (root, production_root))
    repo = Path(repo if repo is not None else REPO)
    if paths_overlap(root, repo):
        raise RuntimeError("拒绝：演练根 %s 与仓库工作树 %s 路径重叠（避免脏化 git 工作区）"
                           % (root, repo))


def cleanup_root(root) -> dict:
    """删除演练根：先逐层摘掉 junction/symlink（os.rmdir 只删链接、不动目标），再删树。"""
    root = Path(root)
    existed = _path_lexists(root)
    errors: list[str] = []
    if existed:
        try:
            for dirpath, dirnames, _filenames in os.walk(str(root), topdown=True):
                keep = []
                for name in dirnames:
                    child = Path(dirpath) / name
                    if _is_dir_link(child):
                        try:
                            os.rmdir(str(child))
                        except OSError as exc:  # pragma: no cover - 权限类异常
                            errors.append("%s: %s" % (child, exc))
                    else:
                        keep.append(name)
                dirnames[:] = keep
            shutil.rmtree(str(root), ignore_errors=True)
        except OSError as exc:  # pragma: no cover - 权限类异常
            errors.append(str(exc))
    return {
        "existed": existed,
        "removed": existed and not _path_lexists(root),
        "exists_after": _path_lexists(root),
        "errors": errors[:5],
    }


# ---------------------------------------------------------------------------
# artifact / 生产快照
# ---------------------------------------------------------------------------
def make_artifact(release_root, version: str, content: bytes, *, kind: str = "ok") -> Path:
    """造最小 artifact（app.js + manifest.json），并可故意造坏。

    ``kind``：
      * ``ok``              合法 artifact（manifest sha256 与 app.js 一致）
      * ``empty_manifest``  manifest 为 ``{}``（verify_dir fail-closed 用例）
      * ``hash_mismatch``   manifest.sha256 与文件不符（篡改用例）
      * ``missing_manifest`` 缺 manifest.json
      * ``missing_file``    manifest 列出一个不存在的文件
    """
    root = Path(release_root)
    target = root / version
    target.mkdir(parents=True, exist_ok=False)
    app = target / "app.js"
    app.write_bytes(content)
    manifest = {
        "schema": "legion-ide-release/v1",
        "version": version,
        "commit": "drill-" + version,
        "files": {"app.js": {"sha256": _sha256(content), "bytes": len(content)}},
    }
    if kind == "empty_manifest":
        manifest = {}
    elif kind == "hash_mismatch":
        manifest["files"]["app.js"] = {"sha256": _sha256(b"drill-tampered"), "bytes": len(content)}
    elif kind == "missing_file":
        manifest["files"]["app.css"] = {"sha256": _sha256(b".drill{}"), "bytes": 8}
    if kind != "missing_manifest":
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def production_snapshot(legion, release_root=None) -> dict:
    """只读快照：生产 current.json（hash/指针）与 current junction 目标。不写任何东西。"""
    root = Path(release_root) if release_root is not None else Path(legion.DEFAULT_RELEASE_ROOT)
    snap = {
        "root": str(root),
        "exists": root.is_dir(),
        "current_json_sha256": "",
        "pointer_version": "",
        "junction_exists": False,
        "junction_target": "",
    }
    if not snap["exists"]:
        return snap
    current_json = root / "current.json"
    try:
        raw = current_json.read_bytes()
        snap["current_json_sha256"] = _sha256(raw)
        data = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(data, dict):
            snap["pointer_version"] = str(data.get("version") or "")
    except (OSError, ValueError):
        pass
    link = root / CURRENT_NAME
    try:
        snap["junction_exists"] = bool(legion._path_present(link))
        if snap["junction_exists"]:
            snap["junction_target"] = legion._display_target(legion._link_target(link))
    except Exception:  # noqa: BLE001 - 快照失败不阻塞演练
        pass
    return snap


# ---------------------------------------------------------------------------
# 演练上下文 / 场景骨架
# ---------------------------------------------------------------------------
class Scenario:
    """一个演练场景的检查收集器。advisory 检查不计入场景/总判定。"""

    def __init__(self, sid: str, title: str):
        self.id = sid
        self.title = title
        self.checks: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "", **extra) -> dict:
        item = {"name": str(name), "ok": bool(ok), "detail": str(detail)}
        item.update(extra)
        self.checks.append(item)
        return item

    def hard_checks(self) -> list[dict]:
        return [c for c in self.checks if not c.get("advisory", False)]


class _Ctx:
    """演练上下文：父根 + 复用模块 + 写入台账（供 isolation_safety 断言写入范围）。"""

    def __init__(self, root: Path, legion):
        self.root = Path(root)
        self.legion = legion
        self.writes: list[dict] = []

    def scenario_root(self, sid: str) -> Path:
        path = self.root / sid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def note_write(self, op: str, path, **info) -> None:
        record = {"op": op, "path": str(path)}
        record.update({k: str(v) for k, v in info.items()})
        self.writes.append(record)


def _new_scenario(sid: str) -> Scenario:
    return Scenario(sid, SCENARIO_TITLES[sid])


def _finish_scenario(sc: Scenario, error: str = "") -> dict:
    hard = sc.hard_checks()
    return {
        "id": sc.id,
        "title": sc.title,
        "ok": bool(hard) and all(c["ok"] for c in hard) and not error,
        "error": error,
        "checks": sc.checks,
        "checks_total": len(sc.checks),
        "checks_failed": sum(1 for c in hard if not c["ok"]),
    }


def _run_scenario(sid: str, ctx: _Ctx) -> dict:
    """执行单个场景；场景内异常转成本场景失败，不拖垮其它场景。"""
    sc = _new_scenario(sid)
    error = ""
    try:
        _SCENARIO_FUNCS[sid](ctx, sc)
    except Exception as exc:  # noqa: BLE001 - 演练工具：单场景异常必须被记录而非中断
        error = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        sc.check("scenario.exception", False, error,
                 trace=traceback.format_exc(limit=4).strip().splitlines()[-4:])
    return _finish_scenario(sc, error)


def _state_check(sc: Scenario, prefix: str, status: dict, expect_version: str,
                 expect_content: bytes, root: Path) -> None:
    """指针/junction/drift/内容 四方对账（一次检查四类断言）。"""
    junction = status.get("junction") or {}
    actual_pointer = status.get("pointer_version")
    sc.check(prefix + ".pointer_version", actual_pointer == expect_version,
             "pointer_version=%r 期望=%r" % (actual_pointer, expect_version))
    sc.check(prefix + ".junction_version", junction.get("version") == expect_version,
             "junction.version=%r 期望=%r" % (junction.get("version"), expect_version))
    sc.check(prefix + ".junction_target_exists", bool(junction.get("target_exists")),
             "junction.target=%r target_exists=%r"
             % (junction.get("target"), junction.get("target_exists")))
    sc.check(prefix + ".drift_false", status.get("drift") is False,
             "drift=%r 期望 False" % (status.get("drift"),))
    current_app = Path(root) / CURRENT_NAME / "app.js"
    try:
        actual_content = current_app.read_bytes()
    except OSError:
        actual_content = b""
    ok = actual_content == expect_content
    detail = ("经 current junction 读到 artifact %s 内容" % expect_version
              if ok else "current/app.js=%r 期望=%r" % (actual_content, expect_content))
    sc.check(prefix + ".current_content", ok, detail)


def _read_status(legion, root: Path) -> dict:
    return legion.status(Path(root))


# ---------------------------------------------------------------------------
# 场景 1：成功链路 promote A→B→回滚 A
# ---------------------------------------------------------------------------
def _scenario_happy_ab_rollback(ctx: _Ctx, sc: Scenario) -> None:
    root = ctx.scenario_root(sc.id)
    legion = ctx.legion
    content_a, content_b = b"drill-artifact-A-v1\n", b"drill-artifact-B-v2\n"
    make_artifact(root, "A", content_a)
    make_artifact(root, "B", content_b)
    ctx.note_write("make_artifact", root / "A", version="A")
    ctx.note_write("make_artifact", root / "B", version="B")

    # 1) 首发 A：指针 + current junction 同一动作
    result = legion.promote(root, "A", switch_current=True)
    ctx.note_write("promote", root, version="A", switch_current=True)
    switched = bool((result.get("junction_switch") or {}).get("switched"))
    sc.check("promote_a.junction_switched", switched, "promote 返回值 junction_switch.switched=true")
    _state_check(sc, "promote_a", _read_status(legion, root), "A", content_a, root)

    # 2) 发布 B：A 保留为 current_prev_B 回滚位
    legion.promote(root, "B", switch_current=True)
    ctx.note_write("promote", root, version="B", switch_current=True)
    _state_check(sc, "promote_b", _read_status(legion, root), "B", content_b, root)
    prev = root / ("current_prev_%s" % "B")
    prev_exists = _path_lexists(prev)
    prev_target = legion._display_target(legion._link_target(prev)) if prev_exists else ""
    prev_version = Path(prev_target).name if prev_target else ""
    sc.check("promote_b.prev_junction_kept", prev_exists and prev_version == "A",
             "回滚位 %s → %r（期望 A）" % (prev.name, prev_target))

    # 3) 回滚 A
    legion.rollback_to(root, "A", switch_current=True)
    ctx.note_write("rollback", root, version="A", switch_current=True)
    _state_check(sc, "rollback_a", _read_status(legion, root), "A", content_a, root)

    # 4) 可重复：再来一轮 B → 回滚 A，结果必须仍一致（无残留状态导致的漂移）
    legion.promote(root, "B", switch_current=True)
    ctx.note_write("promote", root, version="B", switch_current=True)
    legion.rollback_to(root, "A", switch_current=True)
    ctx.note_write("rollback", root, version="A", switch_current=True)
    _state_check(sc, "repeat_rollback_a", _read_status(legion, root), "A", content_a, root)

    # 5) 无临时 junction 残留（.current_new_* 半切换现场）
    orphans = sorted(p.name for p in root.iterdir() if p.name.startswith(".current_new_"))
    sc.check("no_orphan_temp_junction", not orphans,
             "临时 junction 残留=%s" % (orphans if orphans else "无"))


# ---------------------------------------------------------------------------
# 场景 2：坏制品拒绝（fail-closed）
# ---------------------------------------------------------------------------
_BAD_VARIANTS = (
    ("empty_manifest", "manifest 为 {}（空 files 曾 fail-open）"),
    ("hash_mismatch", "manifest.sha256 与 app.js 不符（篡改）"),
    ("missing_manifest", "缺 manifest.json"),
    ("missing_file", "manifest 列出的文件缺失"),
)


def _scenario_bad_artifact_rejected(ctx: _Ctx, sc: Scenario) -> None:
    root = ctx.scenario_root(sc.id)
    legion = ctx.legion
    make_artifact(root, "A", b"drill-A-stable\n")
    legion.promote(root, "A", switch_current=True)
    ctx.note_write("promote", root, version="A", switch_current=True)
    baseline = _read_status(legion, root)
    base_pointer = baseline.get("pointer_version")
    base_junction = (baseline.get("junction") or {}).get("version")

    for variant, desc in _BAD_VARIANTS:
        make_artifact(root, variant, ("drill-bad-%s\n" % variant).encode("utf-8"), kind=variant)
        ctx.note_write("make_artifact", root / variant, version=variant)
        raised = ""
        try:
            legion.promote(root, variant, switch_current=True)
        except RuntimeError as exc:
            raised = str(exc)
        sc.check(variant + ".rejected", bool(raised),
                 "%s → fail-closed（%s）" % (desc, raised[:140] if raised else "未抛错！"))
        status = _read_status(legion, root)
        junction = status.get("junction") or {}
        unchanged = (status.get("pointer_version") == base_pointer
                     and junction.get("version") == base_junction
                     and status.get("drift") is False)
        sc.check(variant + ".fail_closed", unchanged,
                 "坏制品未改动指针/junction（pointer=%r junction=%r drift=%r）"
                 % (status.get("pointer_version"), junction.get("version"), status.get("drift")))
        # 旧式 pointer-only 入口同样在写指针前被 verify_dir 拦下
        raised_pointer = ""
        try:
            legion.promote(root, variant)
        except RuntimeError as exc:
            raised_pointer = str(exc)
        sc.check(variant + ".pointer_only_rejected", bool(raised_pointer),
                 "pointer-only promote 也被拒绝（%s）" % (raised_pointer[:120] or "未抛错"))

    # 绕过 promote 直接切 junction 也必须拒绝
    raised_switch = ""
    try:
        legion.switch_current_link(root, "empty_manifest")
    except RuntimeError as exc:
        raised_switch = str(exc)
    sc.check("switch_current_link.rejected", bool(raised_switch),
             "switch_current_link(坏制品) fail-closed（%s）" % (raised_switch[:140] or "未抛错"))

    # 拒绝坏制品后发布能力不受污染：正常发布 B 仍可用
    make_artifact(root, "B", b"drill-B-good\n")
    ctx.note_write("make_artifact", root / "B", version="B")
    legion.promote(root, "B", switch_current=True)
    ctx.note_write("promote", root, version="B", switch_current=True)
    _state_check(sc, "recover_after_bad", _read_status(legion, root), "B", b"drill-B-good\n", root)

    orphans = sorted(p.name for p in root.iterdir() if p.name.startswith(".current_new_"))
    sc.check("no_orphan_temp_junction", not orphans,
             "临时 junction 残留=%s" % (orphans if orphans else "无"))


# ---------------------------------------------------------------------------
# 场景 3：pointer/junction drift 检出与修复
# ---------------------------------------------------------------------------
def _scenario_drift_detected(ctx: _Ctx, sc: Scenario) -> None:
    root = ctx.scenario_root(sc.id)
    legion = ctx.legion
    content_a, content_b = b"drill-A-stable\n", b"drill-B-next\n"
    make_artifact(root, "A", content_a)
    make_artifact(root, "B", content_b)
    ctx.note_write("make_artifact", root / "A", version="A")
    ctx.note_write("make_artifact", root / "B", version="B")
    legion.promote(root, "A", switch_current=True)
    ctx.note_write("promote", root, version="A", switch_current=True)

    # 3.1 旧式 pointer-only promote：指针 B、junction 仍 A —— W1 要禁止的「指针变了、目录没切」
    legion.promote(root, "B")  # switch_current 缺省 False（旧调用方兼容行为）
    ctx.note_write("promote", root, version="B", switch_current=False)
    status = _read_status(legion, root)
    junction = status.get("junction") or {}
    sc.check("pointer_only.pointer_moved", status.get("pointer_version") == "B",
             "pointer_version=%r 期望 B" % (status.get("pointer_version"),))
    sc.check("pointer_only.junction_not_moved", junction.get("version") == "A",
             "junction 仍指向 A（%r）" % (junction.get("version"),))
    sc.check("pointer_only.drift_detected", status.get("drift") is True,
             "status.drift=True（检出指针/junction 不一致）")
    current_app = root / CURRENT_NAME / "app.js"
    served = current_app.read_bytes() if current_app.is_file() else b""
    sc.check("pointer_only.current_content_still_a", served == content_a,
             "服务实际仍读到 A —— 漂移风险可见（指针说 B，目录是 A）")

    # 3.2 修复漂移：指针 + junction 一起切 B
    legion.promote(root, "B", switch_current=True)
    ctx.note_write("promote", root, version="B", switch_current=True)
    _state_check(sc, "repair_by_promote", _read_status(legion, root), "B", content_b, root)

    # 3.3 反向漂移：只切 junction、不写指针（= promote 中途崩溃窗口）
    legion.switch_current_link(root, "A")
    ctx.note_write("switch_current_link", root, version="A")
    status = _read_status(legion, root)
    junction = status.get("junction") or {}
    sc.check("junction_only.pointer_still_b", status.get("pointer_version") == "B",
             "pointer_version=%r 期望 B" % (status.get("pointer_version"),))
    sc.check("junction_only.junction_moved", junction.get("version") == "A",
             "junction.version=%r 期望 A" % (junction.get("version"),))
    sc.check("junction_only.drift_detected", status.get("drift") is True,
             "status.drift=True（反向漂移同样检出）")

    # 3.4 用回滚动作把指针/junction 修回一致
    legion.rollback_to(root, "A", switch_current=True)
    ctx.note_write("rollback", root, version="A", switch_current=True)
    _state_check(sc, "repair_by_rollback", _read_status(legion, root), "A", content_a, root)


# ---------------------------------------------------------------------------
# 场景 4：中途失败恢复
# ---------------------------------------------------------------------------
def _scenario_mid_failure_recovery(ctx: _Ctx, sc: Scenario) -> None:
    root = ctx.scenario_root(sc.id)
    legion = ctx.legion
    content_a, content_b = b"drill-A-safe\n", b"drill-B-safe\n"
    make_artifact(root, "A", content_a)
    make_artifact(root, "B", content_b)
    ctx.note_write("make_artifact", root / "A", version="A")
    ctx.note_write("make_artifact", root / "B", version="B")
    legion.promote(root, "A", switch_current=True)
    ctx.note_write("promote", root, version="A", switch_current=True)

    # 4.1 崩溃窗口①：junction 已切 B，但 current.json 仍指 A（promote 写指针前崩溃）
    legion.switch_current_link(root, "B")
    ctx.note_write("switch_current_link", root, version="B")
    status = _read_status(legion, root)
    junction = status.get("junction") or {}
    sc.check("crash_after_switch.pointer_old", status.get("pointer_version") == "A",
             "pointer_version=%r 期望 A" % (status.get("pointer_version"),))
    sc.check("crash_after_switch.junction_new", junction.get("version") == "B",
             "junction.version=%r 期望 B" % (junction.get("version"),))
    sc.check("crash_after_switch.drift_detected", status.get("drift") is True,
             "status.drift=True（半切换现场可检出）")

    # 4.2 恢复：以回滚动作把指针 + junction 一致回 A（fail-safe 方向）
    legion.rollback_to(root, "A", switch_current=True)
    ctx.note_write("rollback", root, version="A", switch_current=True)
    _state_check(sc, "recover_from_crash", _read_status(legion, root), "A", content_a, root)

    # 4.3 崩溃窗口②：current junction 丢失（半切换清理/误删）
    link = root / CURRENT_NAME
    drop_error = ""
    if _is_dir_link(link):
        try:
            os.rmdir(str(link))  # 只摘链接，不动 artifact 目录
        except OSError as exc:
            drop_error = str(exc)
    else:
        drop_error = "current 不是 junction/symlink，无法模拟丢失"
    ctx.note_write("remove_junction", link)
    sc.check("junction_missing.simulated", not drop_error,
             "模拟 current junction 丢失：%s" % (drop_error or "已摘除链接"))
    status = _read_status(legion, root)
    junction = status.get("junction") or {}
    sc.check("junction_missing.detected",
             junction.get("exists") is False and junction.get("target_exists") is False,
             "status.junction.exists=%r target_exists=%r（可检出缺失）"
             % (junction.get("exists"), junction.get("target_exists")))

    # 4.4 恢复：重新 promote A，指针 + junction 同时重建
    legion.promote(root, "A", switch_current=True)
    ctx.note_write("promote", root, version="A", switch_current=True)
    _state_check(sc, "recover_missing_junction", _read_status(legion, root), "A", content_a, root)

    # 4.5 故障后全链路仍可用：发布 B → 回滚 A
    legion.promote(root, "B", switch_current=True)
    ctx.note_write("promote", root, version="B", switch_current=True)
    _state_check(sc, "post_fault_publish_b", _read_status(legion, root), "B", content_b, root)
    legion.rollback_to(root, "A", switch_current=True)
    ctx.note_write("rollback", root, version="A", switch_current=True)
    _state_check(sc, "post_fault_rollback_a", _read_status(legion, root), "A", content_a, root)


# ---------------------------------------------------------------------------
# 场景 5：隔离安全（不写入生产）
# ---------------------------------------------------------------------------
def _scenario_isolation_safety(ctx: _Ctx, sc: Scenario, prod_before: dict, prod_after: dict) -> None:
    isolated = True
    detail = ""
    try:
        assert_isolated(ctx.root, ctx.legion.DEFAULT_RELEASE_ROOT)
    except RuntimeError as exc:
        isolated = False
        detail = str(exc)
    sc.check("release_root_isolated", isolated,
             detail or "演练根 %s 与生产 %s 无路径重叠"
             % (ctx.root, ctx.legion.DEFAULT_RELEASE_ROOT))

    outside = [w for w in ctx.writes if not is_under(w["path"], ctx.root)]
    sc.check("writes_confined", not outside,
             "写入台账 %d 条全部落在演练根 %s" % (len(ctx.writes), ctx.root)
             if not outside else "越界写入=%s" % (outside[:3],))

    same = prod_before == prod_after
    sc.check("production_untouched", same,
             "生产 current.json/junction 只读快照前后一致"
             if same else "生产快照前后变化（可能为并发发布，非本工具写入）：%r → %r"
             % (prod_before, prod_after), advisory=True)


_SCENARIO_FUNCS = {
    "happy_ab_rollback": _scenario_happy_ab_rollback,
    "bad_artifact_rejected": _scenario_bad_artifact_rejected,
    "drift_detected": _scenario_drift_detected,
    "mid_failure_recovery": _scenario_mid_failure_recovery,
}


# ---------------------------------------------------------------------------
# 演练主流程
# ---------------------------------------------------------------------------
def _select_scenarios(spec) -> list[str]:
    """解析 --scenario：逗号分隔字符串或场景 id 序列；空=全部；未知 id 抛 ValueError。"""
    if not spec:
        return list(DEFAULT_SCENARIOS)
    if isinstance(spec, (list, tuple, set)):
        items = [str(s).strip() for s in spec if str(s).strip()]
    else:
        items = [s.strip() for s in str(spec).split(",") if s.strip()]
    unknown = [s for s in items if s not in DEFAULT_SCENARIOS]
    if unknown:
        raise ValueError("未知场景 %s（可选：%s）" % (unknown, ",".join(DEFAULT_SCENARIOS)))
    if not items:
        return list(DEFAULT_SCENARIOS)
    return [s for s in DEFAULT_SCENARIOS if s in items]


def _prepare_root(explicit_root, production: Path) -> tuple[Path, bool, bool]:
    """准备演练父根；返回 (root, 是否显式指定, 是否在系统临时目录下)。"""
    temp_base = Path(os.path.realpath(tempfile.gettempdir()))
    if explicit_root:
        root = Path(os.path.abspath(str(explicit_root)))
        assert_isolated(root, production)
        if _is_dir_link(root):
            raise RuntimeError("拒绝：显式 --release-root 不能是链接/junction：%s" % root)
        if _path_lexists(root):
            if not root.is_dir():
                raise RuntimeError("拒绝：显式 --release-root 不是目录：%s" % root)
            if any(root.iterdir()):
                raise RuntimeError(
                    "拒绝：显式 --release-root 必须不存在或为空目录（当前非空，防覆盖他人发布现场）：%s"
                    % root)
        else:
            root.mkdir(parents=True, exist_ok=True)
        return root, True, is_under(root, temp_base)
    root = Path(tempfile.mkdtemp(prefix=DEFAULT_TEMP_PREFIX))
    try:
        assert_isolated(root, production)
    except Exception:  # noqa: BLE001 - 隔离失败先清掉刚建的 temp 根再抛
        cleanup_root(root)
        raise
    return root, False, is_under(root, temp_base)


def _safety_summary(scenarios: list[dict], explicit: bool, under_temp: bool) -> dict:
    safety = next((s for s in scenarios if s["id"] == "isolation_safety"), None)
    checks = {c["name"]: bool(c["ok"]) for c in (safety or {}).get("checks", [])}
    return {
        "isolated_from_production": checks.get("release_root_isolated", False),
        "writes_confined_to_drill_root": checks.get("writes_confined", False),
        "production_untouched": checks.get("production_untouched", False),
        "explicit_release_root": bool(explicit),
        "under_system_temp": bool(under_temp),
    }


def run_drill(release_root=None, keep: bool = False, cleanup: bool = False,
              scenarios=None, legion=None) -> dict:
    """执行全部（或指定）演练场景，返回结构化报告（不抛异常）。

    ``release_root=None``：tempfile 临时隔离根（默认，结束清理，``keep=True`` 保留）；
    ``release_root=path``：显式隔离根（须不存在/为空；默认保留现场，``cleanup=True`` 删除）。
    """
    started = time.perf_counter()
    legion = legion or load_legion_module()
    production = Path(legion.DEFAULT_RELEASE_ROOT)
    explicit_root = None if release_root in (None, "") else Path(release_root)
    error = ""
    scenarios_out: list[dict] = []
    root: Path | None = None
    explicit = False
    under_temp = False
    should_clean = False
    cleanup_info = {"requested": False, "existed": False, "removed": False,
                    "exists_after": False, "errors": []}
    prod_before: dict = {}
    prod_after: dict = {}

    try:
        selected = _select_scenarios(scenarios)
        root, explicit, under_temp = _prepare_root(explicit_root, production)
        should_clean = bool(cleanup) if explicit else (not keep)
        prod_before = production_snapshot(legion, production)
        ctx = _Ctx(root, legion)
        for sid in selected:
            scenarios_out.append(_run_scenario(sid, ctx))
        prod_after = production_snapshot(legion, production)

        safety_sc = _new_scenario("isolation_safety")
        safety_error = ""
        try:
            _scenario_isolation_safety(ctx, safety_sc, prod_before, prod_after)
        except Exception as exc:  # noqa: BLE001 - 安全场景异常同样转为失败
            safety_error = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            safety_sc.check("scenario.exception", False, safety_error)
        scenarios_out.append(_finish_scenario(safety_sc, safety_error))
    except Exception as exc:  # noqa: BLE001 - 拒绝/基建异常：进报告，退出码 1
        error = "%s: %s" % (type(exc).__name__, str(exc)[:400])
    finally:
        if root is not None:
            if should_clean:
                cleanup_info = cleanup_root(root)
                cleanup_info["requested"] = True
            else:
                cleanup_info["exists_after"] = _path_lexists(root)

    hard_checks = [c for s in scenarios_out for c in s.get("checks", [])
                   if not c.get("advisory", False)]
    advisory_failed = sum(1 for s in scenarios_out for c in s.get("checks", [])
                          if c.get("advisory") and not c["ok"])
    total_checks = sum(len(s.get("checks", [])) for s in scenarios_out)
    ok = (not error) and bool(scenarios_out) and all(s["ok"] for s in scenarios_out)
    report = {
        "schema": SCHEMA,
        "ok": bool(ok),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "error": error,
        "platform": {
            "os": os.name,
            "link_kind": "junction" if os.name == "nt" else "symlink",
        },
        "release_root": str(root) if root is not None else "",
        "release_root_explicit": explicit,
        "release_root_under_temp": under_temp,
        "production": {
            "release_root": str(production),
            "snapshot_before": prod_before,
            "snapshot_after": prod_after,
        },
        "safety": _safety_summary(scenarios_out, explicit, under_temp),
        "scenarios": scenarios_out,
        "summary": {
            "scenarios_total": len(scenarios_out),
            "scenarios_passed": sum(1 for s in scenarios_out if s["ok"]),
            "scenarios_failed": sum(1 for s in scenarios_out if not s["ok"]),
            "checks_total": total_checks,
            "checks_failed": sum(1 for c in hard_checks if not c["ok"]),
            "advisory_failed": advisory_failed,
            "selected": [s["id"] for s in scenarios_out],
        },
        "cleanup": cleanup_info,
        "notes": list(NOTES),
    }
    return report


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------
def _mark(ok: bool) -> str:
    return _MARK[bool(ok)]


def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(report: dict) -> str:
    """Markdown 报告（供评审/日报引用；不依赖 stdout 编码）。"""
    summary = report.get("summary") or {}
    platform = report.get("platform") or {}
    cleanup = report.get("cleanup") or {}
    safety = report.get("safety") or {}
    lines = [
        "# W1 发布回滚演练报告（Legion IDE release rollback drill）",
        "",
        "- 生成时间：%s" % report.get("generated_at", ""),
        "- 平台：os=%s / 链接类型=%s" % (platform.get("os"), platform.get("link_kind")),
        "- 演练根：`%s`（%s）" % (report.get("release_root") or "（未创建）",
                              "显式根" if report.get("release_root_explicit") else "临时根"),
        "- 生产 release root：`%s`（只读快照；本工具不写）"
        % (report.get("production") or {}).get("release_root", ""),
    ]
    if cleanup.get("requested"):
        lines.append("- 清理：%s%s" % ("已删除演练根" if cleanup.get("removed") else "删除失败",
                                    "" if cleanup.get("removed") else "：%s" % cleanup.get("errors")))
    elif report.get("release_root"):
        lines.append("- 清理：保留现场（--keep / 未请求 --cleanup）")
    lines += [
        "",
        "**结果：%s** — 场景 %s/%s 通过；硬检查 %s 项失败；advisory %s 项告警"
        % ("PASS" if report.get("ok") else "FAIL",
           summary.get("scenarios_passed"), summary.get("scenarios_total"),
           summary.get("checks_failed"), summary.get("advisory_failed")),
        "",
    ]
    if report.get("error"):
        lines += ["> 错误：%s" % report["error"], ""]
    lines += [
        "## 安全边界",
        "",
        "| 项 | 结果 |",
        "|---|---|",
        "| 演练根与生产 release root 隔离 | %s |" % _mark(safety.get("isolated_from_production")),
        "| 写入仅限演练根 | %s |" % _mark(safety.get("writes_confined_to_drill_root")),
        "| 生产 current.json/junction 快照未变（advisory） | %s |"
        % _mark(safety.get("production_untouched")),
        "| 演练根位于系统临时目录 | %s |" % _mark(safety.get("under_system_temp")),
        "",
    ]
    for scenario in report.get("scenarios") or []:
        lines += [
            "## %s · %s（%s）" % (_mark(scenario.get("ok")), scenario.get("title"), scenario.get("id")),
            "",
        ]
        if scenario.get("error"):
            lines += ["> 场景异常：%s" % scenario["error"], ""]
        lines += ["| # | 检查 | 结果 | 详情 |", "|---|---|---|---|"]
        for index, check in enumerate(scenario.get("checks") or [], 1):
            mark = _mark(check.get("ok"))
            if check.get("advisory") and not check.get("ok"):
                mark = "WARN"
            lines.append("| %d | %s | %s | %s |" % (
                index, _md_cell(check.get("name")), mark, _md_cell(check.get("detail"))))
        lines.append("")
    lines += ["## 说明", ""]
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="W1 Legion IDE 发布回滚演练（隔离 temp root；默认不触碰生产 release）")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="输出机器可读 JSON 报告")
    output.add_argument("--markdown", action="store_true", help="输出 Markdown 报告（默认）")
    parser.add_argument("--release-root", default="",
                        help="显式隔离根（必须不存在或为空目录；默认 tempfile 临时根）")
    parser.add_argument("--keep", action="store_true", help="保留临时演练根（默认删除 temp 根）")
    parser.add_argument("--cleanup", action="store_true", help="显式 --release-root 也删除（默认保留现场）")
    parser.add_argument("--scenario", default="",
                        help="逗号分隔场景子集：%s" % ",".join(DEFAULT_SCENARIOS))
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    if args.scenario:
        try:
            _select_scenarios(args.scenario)
        except ValueError as exc:
            print("参数错误：%s" % exc, file=sys.stderr)
            return 2
    report = run_drill(release_root=(args.release_root or None), keep=args.keep,
                       cleanup=args.cleanup, scenarios=(args.scenario or None))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
