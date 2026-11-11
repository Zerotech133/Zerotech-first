# -*- coding: utf-8 -*-
"""黄忠 W1 批改工具单测（临时提交目录，不联网）。"""
import importlib.util
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
_FIXTURE = _TOOLS.parent / "agent_platform" / "tests" / "fixtures" / "huangzhong_w1"


def _load():
    spec = importlib.util.spec_from_file_location("huangzhong_grade",
                                                  _TOOLS / "huangzhong_grade.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(d: Path, name, text):
    (d / name).write_text(text, encoding="utf-8")


def _good_submission(d: Path):
    for i in range(3):
        _write(d, "criteria_%02d.md" % (i + 1),
               "判据：证据路径 / 命令：pytest -q / 通过线：rc=0")
    for i in range(2):
        _write(d, "gate_review_%02d.md" % (i + 1),
               "compileall ports secrets names capabilities evals tests 全部 ok")
    for i in range(2):
        _write(d, "rollback_%02d.md" % (i + 1),
               "git revert abc123；重启 Start-ScheduledTask，验证 /api/ 恢复")
    _write(d, "self_review.md", "自评分数：90/100\n\n问题：\n- 问题 1：x\n- 问题 2：y\n- 问题 3：z")


def test_grade_good(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    _good_submission(d)
    g = _load()
    rep = g.grade(d, out_dir=tmp_path / "out")
    assert rep["pass"] is True and rep["total"] >= 80
    assert Path(rep["report_json"]).exists() and Path(rep["report_md"]).exists()


def test_grade_missing_files(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    _write(d, "criteria_01.md", "证据 命令 通过线")
    rep = _load().grade(d, out_dir=tmp_path / "out")
    assert rep["files"]["criteria"] == 1
    assert rep["pass"] is False and rep["total"] < 80


def test_grade_bad_rollback(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    _good_submission(d)
    for i in range(2):
        _write(d, "rollback_%02d.md" % (i + 1), "只写 revert，没有重启和验证")
    rep = _load().grade(d, out_dir=tmp_path / "out")
    assert "回滚三步完整" in " ".join(rep["suggestions"])


def test_grade_readonly(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    _good_submission(d)
    before = sorted(p.name for p in d.iterdir())
    _load().grade(d, out_dir=tmp_path / "out")
    assert sorted(p.name for p in d.iterdir()) == before


def test_grade_fixture_template_exists():
    assert (_FIXTURE / "2026-10-28_黄忠W1提交模板").is_dir()
    assert len(list((_FIXTURE / "2026-10-28_黄忠W1提交模板").glob("*.md"))) >= 8
    assert (_FIXTURE / "2026-10-28_黄忠W1验收样例与批改标准_v1.md").is_file()


def test_grade_default_dir_fallback_exists():
    """A 方案兜底：生产默认目录仍可解析到原路径或归档路径。"""
    g = _load()
    assert g.DEFAULT_DIR.is_dir()
    assert len(list(g.DEFAULT_DIR.glob("*.md"))) >= 8
