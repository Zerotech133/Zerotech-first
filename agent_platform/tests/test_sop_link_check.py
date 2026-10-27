# -*- coding: utf-8 -*-
"""tools/sop_link_check.py 测试（WO-DRILL5-SOPLINK）。

全部用 tmp_path 造 ``root/docs`` 假仓库，不碰真实文档：
- 合法链接（文档目录相对 / 仓库根相对）、断链、外链与锚点跳过；
- 反引号内 tools/docs/agent_platform 路径提取与断链；
- --check 退出码、--json 契约、--docs 多文档覆盖、缺失文档。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import sop_link_check as L  # noqa: E402


def _write(root: Path, rel: str, body: str = "") -> Path:
    """在假仓库里落一个文件（自动建父目录）。"""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1) 提取逻辑
# ---------------------------------------------------------------------------


def test_extract_markdown_links_skips_external_and_anchor():
    text = (
        "[同目录](guide.md)\n"
        "[根相对](docs/other.md)\n"
        "[外链](https://example.com/x.md)\n"
        "[邮件](mailto:a@b.com)\n"
        "[锚点](#section)\n"
        "![图片](images/pic.png)\n"
    )
    targets = L.extract_targets(text)
    assert [(t.kind, t.target) for t in targets] == [
        ("markdown", "guide.md"),
        ("markdown", "docs/other.md"),
    ]


def test_extract_backtick_paths_with_prefix_and_punctuation():
    text = (
        "① 建单：`python tools/devtask.py new <TID> \"<目标>\"`；\n"
        "② 参见 `docs/adr/ADR-003-dev-routing-tool-matrix.md#决策`。\n"
        "③ 代码在 `agent_platform/x/y.py`，权限字段 `command_authority` 不算路径，\n"
        "   `mytools/devtask.py` 也不算；裸写 tools/foo.py 不提取。\n"
    )
    targets = L.extract_targets(text)
    assert [(t.kind, t.target, t.line) for t in targets] == [
        ("backtick", "tools/devtask.py", 1),
        ("backtick", "docs/adr/ADR-003-dev-routing-tool-matrix.md", 2),
        ("backtick", "agent_platform/x/y.py", 3),
    ]


def test_extract_angle_target_with_space_and_title():
    text = (
        "[尖括号](<docs/带 空格.md>)\n"
        "[带标题](docs/title.md \"章节标题\")\n"
    )
    targets = L.extract_targets(text)
    assert [t.target for t in targets] == ["docs/带 空格.md", "docs/title.md"]


# ---------------------------------------------------------------------------
# 2) 解析顺序
# ---------------------------------------------------------------------------


def test_resolve_prefers_doc_dir_then_root(tmp_path):
    root = tmp_path
    doc = _write(root, "docs/plans/a.md")
    doc_side = _write(root, "docs/plans/tools/x.py")
    root_side = _write(root, "tools/x.py")

    # 文档目录命中优先，即便仓库根也有同名文件
    assert L.resolve_target(root, doc, "tools/x.py") == doc_side
    doc_side.unlink()
    # 文档目录缺失时回退仓库根
    assert L.resolve_target(root, doc, "tools/x.py") == root_side
    # 两处都没有 → None
    assert L.resolve_target(root, doc, "tools/none.py") is None


# ---------------------------------------------------------------------------
# 3) 端到端：合法文档
# ---------------------------------------------------------------------------


def test_check_passes_for_valid_links(tmp_path, capsys):
    root = tmp_path
    _write(root, "docs/guide.md", "# 指南\n")
    _write(root, "docs/sub/item.md", "# 子项\n")
    _write(root, "docs/plans/sibling.md", "# 同级\n")
    _write(root, "tools/devtask.py", "# tool\n")
    _write(root, "agent_platform/mod.py", "# mod\n")
    _write(
        root,
        "docs/plans/a.md",
        "# 计划\n"
        "\n"
        "[同级](sibling.md) 与 [根相对](docs/guide.md) 与 [上跳](../sub/item.md)\n"
        "[外链](https://example.com) [邮件](mailto:a@b.com) [锚点](#top)\n"
        "命令：`python tools/devtask.py new TID`；模块 `agent_platform/mod.py`。\n",
    )

    rc = L.main(["--root", str(root), "--docs", "docs/plans/a.md", "--check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "断链 0 条" in out
    assert "结论：通过" in out


# ---------------------------------------------------------------------------
# 4) 端到端：断链与退出码
# ---------------------------------------------------------------------------


def test_broken_markdown_link_check_rc(tmp_path, capsys):
    root = tmp_path
    _write(root, "docs/a.md", "[坏链](missing.md)\n")

    assert L.main(["--root", str(root), "--docs", "docs/a.md"]) == 0
    assert "断链" in capsys.readouterr().out

    assert L.main(["--root", str(root), "--docs", "docs/a.md", "--check"]) == 1
    assert "missing.md" in capsys.readouterr().out


def test_broken_backtick_path_check_rc(tmp_path, capsys):
    root = tmp_path
    _write(root, "docs/a.md", "缺文件：`tools/not_exist.py`\n")
    rc = L.main(["--root", str(root), "--docs", "docs/a.md", "--check"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "[断链]" in out
    assert "tools/not_exist.py" in out


# ---------------------------------------------------------------------------
# 5) JSON 契约
# ---------------------------------------------------------------------------


def test_json_output_contract(tmp_path, capsys):
    root = tmp_path
    _write(root, "docs/ok.md", "[好](target.md)\n")
    _write(root, "docs/target.md", "# t\n")
    _write(root, "docs/bad.md", "[坏](nope.md)\n")

    rc = L.main(
        [
            "--root",
            str(root),
            "--docs",
            "docs/ok.md",
            "docs/bad.md",
            "--check",
            "--json",
        ]
    )
    assert rc == 1
    data = json.loads(capsys.readouterr().out)

    assert data["summary"] == {
        "docs": 2,
        "targets": 2,
        "broken": 1,
        "missing_docs": 0,
    }
    assert [d["doc"] for d in data["docs"]] == ["docs/ok.md", "docs/bad.md"]
    assert data["docs"][0]["broken"] == 0
    assert data["docs"][1]["broken"] == 1
    broken = data["broken"][0]
    assert broken["kind"] == "markdown"
    assert broken["target"] == "nope.md"
    assert broken["reason"] == "not_found"
    assert broken["line"] == 1
    assert broken["resolved"] is None


# ---------------------------------------------------------------------------
# 6) --docs 覆盖 / 缺失文档 / 默认文档
# ---------------------------------------------------------------------------


def test_multiple_docs_override(tmp_path, capsys):
    root = tmp_path
    _write(root, "docs/a.md", "见 `docs/a.md`\n")
    _write(root, "docs/b.md", "见 `tools/gone.py`\n")

    rc = L.main(
        [
            "--root",
            str(root),
            "--docs",
            "docs/a.md",
            "docs/b.md",
            "--check",
            "--json",
        ]
    )
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["docs"] == 2
    assert data["summary"]["targets"] == 2
    assert data["summary"]["broken"] == 1


def test_missing_doc_counts_as_broken(tmp_path, capsys):
    root = tmp_path
    rc = L.main(["--root", str(root), "--docs", "docs/nope.md", "--check", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["missing_docs"] == 1
    assert data["summary"]["broken"] == 1
    assert data["broken"][0]["reason"] == "missing_doc"


def test_default_docs_exist_in_repo():
    for rel in L.DEFAULT_DOCS:
        assert (L.REPO_ROOT / rel).is_file(), f"默认文档不存在：{rel}"
