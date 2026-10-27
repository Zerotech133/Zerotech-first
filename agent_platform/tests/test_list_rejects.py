# -*- coding: utf-8 -*-
"""list_rejects 拒档只读浏览工具测试。

tmp_path 构造假 rejects 树，覆盖：摘要表字段完整 / --show 输出全文 /
--stats 计数正确 / 空目录与缺目录中性 / 旧版扁平布局兼容 / md 内容变体
容错。全程不对真仓 data/ 做任何写入。
"""

import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "list_rejects.py"
PYTHON = sys.executable


def run_tool(args):
    return subprocess.run(
        [PYTHON, str(TOOL)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def make_reject_md(day_dir: Path, submit_id: str, stage: str, reasons) -> Path:
    """按 adapters/codex_bridge.py::write_reject 的真实档案结构构造 md 档。"""
    lines = [
        "# bridge 拒绝档：%s" % submit_id,
        "",
        "- **阶段**：%s" % stage,
        "- **时间**：2026-10-18 12:00:00",
        "- **命中规则**：",
        "",
    ]
    lines += ["%d. %s" % (i + 1, r) for i, r in enumerate(reasons)]
    path = day_dir / ("%s.md" % submit_id)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---- 摘要表 ----


def test_summary_fields_complete(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    make_reject_md(day, "sub-001", "静态校验", ["源码中出现 eval("])
    day2 = root / "20261017"
    day2.mkdir()
    make_reject_md(day2, "sub-002", "限频", ["当日受理已达上限 3"])
    (day2 / "sub-002").mkdir()  # md + 目录同框：只一条记录

    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    out = proc.stdout
    assert "20261018" in out and "sub-001" in out
    assert "静态校验" in out and "源码中出现 eval(" in out
    assert "20261017" in out and "sub-002" in out
    assert "限频" in out
    # md + 目录同框不重复计数
    assert out.count("sub-002") == 1
    assert "共 2 条拒档记录" in out


def test_summary_truncates_long_reason(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    make_reject_md(day, "long-1", "静态校验", ["很" * 100])

    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    out = proc.stdout
    # 原因行带编号前缀 "1. "，截断 60 字符含前缀：剩 57 个「很」+ 省略号
    assert "1. " + "很" * 57 + "…" in out
    assert "很" * 58 + "…" not in out
    assert "很" * 100 not in out


# ---- --show ----


def test_show_prints_full_md(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    md = make_reject_md(day, "sub-777", "沙箱试跑", ["子进程超时 10s"])

    proc = run_tool(["--show", "sub-777", "--rejects-root", str(root)])
    assert proc.returncode == 0
    assert md.read_text(encoding="utf-8") in proc.stdout
    assert "sub-777" in proc.stdout


def test_show_not_found_neutral(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)

    proc = run_tool(["--show", "ghost", "--rejects-root", str(root)])
    assert proc.returncode == 0
    assert "无拒档" in proc.stdout


def test_show_rejects_dir_without_md(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    (day / "dir-only").mkdir()

    proc = run_tool(["--show", "dir-only", "--rejects-root", str(root)])
    assert proc.returncode == 0
    assert "只有落档目录、无 md 档" in proc.stdout


def test_show_and_stats_mutually_exclusive(tmp_path):
    proc = run_tool(["--show", "x", "--stats", "--rejects-root", str(tmp_path)])
    assert proc.returncode == 2


# ---- --stats ----


def test_stats_counts_by_stage(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    make_reject_md(day, "a1", "静态校验", ["r1"])
    make_reject_md(day, "a2", "静态校验", ["r2"])
    make_reject_md(day, "a3", "限频", ["r3"])
    make_reject_md(day, "a4", "沙箱试跑", ["r4"])

    proc = run_tool(["--stats", "--rejects-root", str(root)])
    assert proc.returncode == 0
    out = proc.stdout
    assert "共 4 档" in out
    assert "静态校验" in out and "2" in out
    assert "限频" in out and "沙箱试跑" in out
    # 计数最高的 stage 排最前
    lines = [l for l in out.splitlines() if l.strip().startswith("静态校验")]
    assert lines and lines[0].strip().split()[-1] == "2"


# ---- 空目录 / 缺目录中性 ----


def test_empty_dir_neutral(tmp_path):
    root = tmp_path / "rejects"
    root.mkdir()
    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    assert "无拒档" in proc.stdout


def test_missing_dir_neutral(tmp_path):
    proc = run_tool(["--rejects-root", str(tmp_path / "nope")])
    assert proc.returncode == 0
    assert "无拒档" in proc.stdout


# ---- 旧版扁平布局兼容 ----


def test_legacy_flat_layout(tmp_path):
    root = tmp_path / "rejects"
    root.mkdir()
    # 根下直接是 <submit_id>.md（无日期层）
    (root / "old-1.md").write_text(
        "# bridge 拒绝档：old-1\n\n- **阶段**：静态校验\n"
        "- **命中规则**：\n\n1. 禁用函数 import os\n",
        encoding="utf-8",
    )
    # 根下直接是 <submit_id>/ 目录
    (root / "old-2").mkdir()

    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    out = proc.stdout
    assert "old-1" in out and "静态校验" in out and "import os" in out
    assert "old-2" in out and "未知" in out
    assert "(扁平)" in out
    assert "共 2 条拒档记录" in out

    # --show 也能在扁平层找到
    proc = run_tool(["--show", "old-1", "--rejects-root", str(root)])
    assert proc.returncode == 0
    assert "禁用函数 import os" in proc.stdout


# ---- md 内容变体容错 ----


def test_md_content_variants(tmp_path):
    root = tmp_path / "rejects"
    day = root / "20261018"
    day.mkdir(parents=True)
    # 缺「阶段」行 + 缺「命中规则」行
    (day / "v1.md").write_text(
        "# bridge 拒绝档：v1\n\n只有一行自由文本\n", encoding="utf-8"
    )
    # 阶段行存在但命中规则为空
    (day / "v2.md").write_text(
        "# bridge 拒绝档：v2\n\n- **阶段**：限频\n- **命中规则**：\n", encoding="utf-8"
    )

    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    out = proc.stdout
    assert "v1" in out and "未知" in out and "(无命中规则行)" in out
    assert "v2" in out and "限频" in out
    # v2 命中规则空：取不到原因行 → 中性占位
    assert out.count("(无命中规则行)") >= 1


def test_date_dirs_sorted_ascending(tmp_path):
    root = tmp_path / "rejects"
    for d in ("20261018", "20261016", "20261017"):
        day = root / d
        day.mkdir(parents=True)
        make_reject_md(day, "s-" + d, "限频", ["r"])
    proc = run_tool(["--rejects-root", str(root)])
    assert proc.returncode == 0
    dates = [l.split()[0] for l in proc.stdout.splitlines() if l.startswith("2026")]
    assert dates == ["20261016", "20261017", "20261018"]
