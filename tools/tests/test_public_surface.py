#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_public_surface 的行为测试（2026-11-12）

为什么要有这组用例：
    公开面门禁最容易的失败方式不是报错，而是「永远通过」——基线写错、
    排除规则过宽、或者规则值被写成转义后匹配不到任何东西，都会让它安静
    地放行一切。所以每个用例都按「必须先失败」设计：
      ① 全新文件带敏感特征 -> 必须 rc=1
      ② 已有文件命中数增加 -> 必须 rc=1
      ③ 已有文件命中数不变 -> 必须 rc=0（存量冻结，不误伤）
      ④ --warn -> 必须 rc=0 但输出含「warn」
      ⑤ 目标远端不是 github.com -> 必须 rc=0 且不做网络请求
    ③ 是关键的反向用例：没有它，①② 通过也可能只是「见谁都拦」。

在一个临时 git 仓库里跑，不碰共享工作区（并行会话多，禁 checkout/reset）。
运行：python -X utf8 tools/tests/test_public_surface.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
SCRIPT = REPO / "tools" / "check_public_surface.py"

# 用例数据本身也不能在公开面留特征：本文件会被自己的门禁扫到。
# IP 用拼接、中文规则名用 \u 转义，源码文本里就不存在连续的特征串。
IP = "10.205.0." + "118"
NO_AUTH = "\u65e0\u9274\u6743"
VAULT = "_" + "vault"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True)


def _make_repo(tmp: Path, files: dict[str, str], baseline_rows: list[tuple[str, str, int]]) -> Path:
    _git(tmp, "init")
    _git(tmp, "config", "user.email", "t@example.invalid")
    _git(tmp, "config", "user.name", "t")
    (tmp / "tools").mkdir(exist_ok=True)
    shutil.copy2(SCRIPT, tmp / "tools" / "check_public_surface.py")

    head = [
        "# public-surface baseline - test fixture",
        "# generated: fixture",
        "#",
        "path\trule\tcount",
    ]
    body = ["%s\t%s\t%d" % row for row in baseline_rows]
    (tmp / "tools" / "public_surface_baseline.tsv").write_text(
        "\n".join(head + body) + "\n", encoding="utf-8", newline="\n")

    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-m", "fixture")
    return tmp


def _run(tmp: Path, *extra: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(tmp / "tools" / "check_public_surface.py"),
         "--remote-url", "git@github.com:Zerotech133/Zerotech-first.git", *extra],
        cwd=str(tmp), capture_output=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace")


def case_new_file_blocked() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/leak.md": "主机 %s %s\n" % (IP, NO_AUTH)},
                   [("docs/old.md", "INTRANET_IP", 1)])
        (tmp / "docs" / "old.md").write_text("老内容 %s\n" % IP, encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "add old")
        rc, out = _run(tmp)
        assert rc == 1, "新文件带内网地址应阻断，实际 rc=%s\n%s" % (rc, out)
        assert "leak.md" in out, "报错必须指到具体文件\n%s" % out
        assert "新增文件" in out, "应识别为新增文件而非增量\n%s" % out


def case_count_increase_blocked() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/old.md": "第一处 %s\n" % IP},
                   [("docs/old.md", "INTRANET_IP", 1)])
        (tmp / "docs" / "old.md").write_text(
            "第一处 %s\n第二处 %s\n" % (IP, IP), encoding="utf-8")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "add second hit")
        rc, out = _run(tmp)
        assert rc == 1, "命中数增加应阻断，实际 rc=%s\n%s" % (rc, out)
        assert "命中增加" in out, "应识别为增量\n%s" % out


def case_unchanged_passes() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/old.md": "存量 %s\n" % IP},
                   [("docs/old.md", "INTRANET_IP", 1)])
        rc, out = _run(tmp)
        assert rc == 0, "存量内容必须放行，实际 rc=%s\n%s" % (rc, out)
        assert "通过" in out, out


def case_warn_mode_passes() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/leak.md": "新泄漏 %s\n" % IP}, [])
        rc, out = _run(tmp, "--warn")
        assert rc == 0, "--warn 应放行，实际 rc=%s\n%s" % (rc, out)
        assert "warn" in out.lower(), out


def case_non_github_skipped() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/leak.md": "新泄漏 %s\n" % IP}, [])
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(tmp / "tools" / "check_public_surface.py"),
             "--remote-url", "git@qgit-aios:aios.git"],
            cwd=str(tmp), capture_output=True,
        )
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        assert proc.returncode == 0, "非 github 远端应跳过，实际 rc=%s\n%s" % (proc.returncode, out)
        assert "不适用" in out, out


def case_rules_actually_match() -> None:
    """规则值写成转义后必须仍能匹配——否则门禁会安静放行一切。"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_repo(tmp, {"docs/leak.md": "%s %s %s\n" % (NO_AUTH, VAULT, IP)}, [])
        rc, out = _run(tmp)
        assert rc == 1, "规则必须真的匹配到内容，实际 rc=%s\n%s" % (rc, out)
        for rule in ("NO_AUTH", "VAULT_PATH", "INTRANET_IP"):
            assert rule in out, "规则 %s 未命中，转义写法可能失效\n%s" % (rule, out)


CASES = [
    ("新文件带敏感特征 -> 阻断", case_new_file_blocked),
    ("已有文件命中增加 -> 阻断", case_count_increase_blocked),
    ("存量不变 -> 放行", case_unchanged_passes),
    ("--warn -> 放行但告警", case_warn_mode_passes),
    ("非 github 远端 -> 跳过", case_non_github_skipped),
    ("规则值转义后仍匹配", case_rules_actually_match),
]


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print("  PASS  %s" % name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL  %s\n        %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERROR %s\n        %r" % (name, exc))
    print("\n%d passed, %d failed" % (len(CASES) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
