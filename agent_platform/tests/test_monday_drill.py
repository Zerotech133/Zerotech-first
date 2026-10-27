# -*- coding: utf-8 -*-
"""monday_drill（周一观察点只读 dry-run 演练器）测试。

用 subprocess + sys.executable 调真实 CLI（env PYTHONIOENCODING=utf-8），覆盖：
  1. --list 含五个时间锚点（16:50 / 17:10 / 17:25 / 17:35 / 周五）且标出「不要手动跑」；
  2. --at 17:20 落在 17:10–17:25 区间，识别「下一个是 17:25 巡检」+ 接下来三步；
  3. --at 非法格式 → 退出码 2（含越界、非数字、缺补零、分隔符错误）；
  4. --json 可 json.loads 且含 timeline 列表；--at + --json 的 current/next 正确；
  5. 轴外时刻（09:00 / 23:59）仍返回 0——轴外不是错误；
  6. 只读性：跑完 --list/--json/--at 后 data/ 目录树快照（文件集 + mtime/size）不变；
     且源码无执行/写盘入口（无 import subprocess / os.system / mkdir / makedirs / open()）。

刻意不触发任何计划任务、不写数据根——本文件只断言「什么都没发生」。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
TOOL = AP / "tools" / "monday_drill.py"
DATA = AP / "data"
ANCHORS = ("16:50", "17:10", "17:25", "17:35", "周五")


def run_cli(*args):
    """调用 CLI；cwd=agent_platform（与现场人工巡检一致）。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=str(AP),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def snapshot(root: Path):
    """目录树快照：相对路径 → (mtime_ns, size)；目录不存在返回 None。"""
    if not root.exists():
        return None
    out = {}
    for p in sorted(root.rglob("*")):
        try:
            st = p.stat()
        except OSError:
            continue
        out[p.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return out


def test_list_covers_five_anchors():
    r = run_cli("--list")
    assert r.returncode == 0, r.stderr
    for anchor in ANCHORS:
        assert anchor in r.stdout, "时间轴缺锚点 %s" % anchor
    # 17:10 段必须明确「不要手动跑」
    assert "不要手动跑" in r.stdout
    assert "不要手动跑 AgentShadowDaily" in r.stdout
    # 每段字段齐：该看什么 / 该跑哪条命令 / 期望退出码 / 期望看到 / 出问题查哪里
    for field in ("该看什么", "该跑哪条命令", "期望退出码", "期望看到", "出问题查哪里"):
        assert r.stdout.count(field) >= 5, "字段 %s 覆盖不足" % field


def test_at_1720_next_is_1725_check():
    r = run_cli("--at", "17:20")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "17:10" in out and "17:25" in out
    assert "处于 17:10–17:25 区间" in out
    assert "下一个是 17:25 巡检" in out
    assert "【接下来三步】" in out
    assert "17:35" in out and "周五" in out


def test_at_invalid_format_returns_2():
    for bad in ("25:00", "17:60", "abc", "7:20", "17-20", "17:2", "1710", "", "  "):
        r = run_cli("--at", bad)
        assert r.returncode == 2, "--at %r 应返回 2，实际 %d" % (bad, r.returncode)


def test_json_output_loadable_with_timeline():
    r = run_cli("--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)  # 必须可 json.loads
    assert isinstance(data.get("timeline"), list)
    assert len(data["timeline"]) >= 5
    assert data["read_only"] is True
    assert data["executes_commands"] is False
    for entry in data["timeline"]:
        assert entry["at"] and entry["label"] and entry["mode"]
        assert isinstance(entry["look"], list) and entry["look"]
        assert isinstance(entry["commands"], list) and entry["commands"]
        assert entry["troubleshoot"]
        for c in entry["commands"]:
            assert c["cmd"] and c["rc"] and c["expect"]
    ats = [e["at"] for e in data["timeline"]]
    for anchor in ANCHORS:
        assert any(a.startswith(anchor) for a in ats), "JSON 时间轴缺 %s" % anchor


def test_json_at_1720_current_and_next():
    r = run_cli("--json", "--at", "17:20")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["mode"] == "at" and data["at"] == "17:20"
    assert data["current"]["at"] == "17:10"
    assert [e["at"] for e in data["next"]] == ["17:25", "17:35", "周五 17:45"]
    assert len(data["next_steps_text"]) == 3
    assert data["anchor"] == {"now": "17:20", "previous": "17:10", "next": "17:25"}


def test_json_list_mode():
    r = run_cli("--list", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["mode"] == "list"
    assert data["current"] is None and data["next"] == []
    assert len(data["timeline"]) == 5


def test_outside_axis_still_returns_0():
    early = run_cli("--at", "09:00")
    assert early.returncode == 0, early.stderr
    assert "尚未进入观察窗" in early.stdout
    assert "下一个是 16:50" in early.stdout

    late = run_cli("--at", "23:59")
    assert late.returncode == 0, late.stderr
    assert "当日观察窗已结束" in late.stdout
    # 轴外也要给满三步（不足时用轴外提示补位）
    assert "【接下来三步】" in late.stdout
    assert late.stdout.count("\n  ") >= 3


def test_no_args_uses_now():
    r = run_cli()
    assert r.returncode == 0, r.stderr
    assert "现在该做什么" in r.stdout


def test_read_only_no_side_effects():
    before = snapshot(DATA)
    for args in (
        ("--list",),
        ("--json",),
        ("--at", "17:20"),
        ("--json", "--at", "17:25"),
    ):
        r = run_cli(*args)
        assert r.returncode == 0, r.stderr
    after = snapshot(DATA)
    assert before == after, "运行后 data/ 目录树发生变化（应为零写面）"


def test_source_has_no_exec_or_write_path():
    src = TOOL.read_text(encoding="utf-8")
    assert "READ_ONLY = True" in src
    assert "ReadOnlyViolation" in src
    # 无任何进程执行/写盘入口（注释里出现这些词不算，只看 import 行与调用点）
    imports = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    assert not any("subprocess" in ln for ln in imports)
    assert not any(
        ln.split()[1].split(".")[0] in ("os", "shutil", "pathlib") for ln in imports
    )
    for call in ("os.system(", "os.popen(", "os.exec", ".mkdir(", "makedirs(", "open("):
        assert call not in src, "源码出现潜在写盘/执行调用：%s" % call
    # 唯一的潜在执行入口只有 def 一行（无任何调用点）
    assert src.count("def run_command(") == 1
    assert (
        src.count("run_command(") - src.count("def run_command(") <= 2
    )  # 仅 docstring 引用


def test_run_command_always_refuses():
    """直接调「潜在执行入口」：必须抛 ReadOnlyViolation（连常量翻转也不放行）。"""
    sys.path.insert(0, str(AP / "tools"))
    import monday_drill as md

    assert md.READ_ONLY is True
    for cmd in (
        "schtasks /run /tn AgentShadowDaily",
        "python run_shadow.py",
        "$PY tools/check_observation_point.py --data-root data",
    ):
        try:
            md.run_command(cmd)
        except md.ReadOnlyViolation:
            pass
        else:
            raise AssertionError("run_command 竟然执行了命令：%s" % cmd)
    # 常量被翻转也仍然拒绝（常量只是第一道闸）
    md.READ_ONLY = False
    try:
        try:
            md.run_command("schtasks /run /tn AgentShadowDaily")
        except md.ReadOnlyViolation:
            pass
        else:
            raise AssertionError("READ_ONLY=False 时 run_command 仍不应执行")
    finally:
        md.READ_ONLY = True
