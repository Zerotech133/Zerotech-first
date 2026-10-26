# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spec_flow as S  # noqa: E402


def test_init_and_check_fails_then_passes(tmp_path):
    root = tmp_path / "specs"
    r = S.init("SPEC-T1", "测试规格", root=root)
    assert r["ok"] and (root / "SPEC-T1" / "spec.md").exists()
    c = S.check("SPEC-T1", root=root)
    assert c["ok"] is False and c["missing"]
    # 填充必需内容
    d = root / "SPEC-T1"
    (d / "constitution.md").write_text("# c\n\n## 原则\n- x\n\n## 边界\n- y\n\n## 质量门\n- z\n",
                                       encoding="utf-8")
    (d / "spec.md").write_text("# s\n\n## 目标\ng\n\n## 场景\ns\n\n## 验收标准\n- [ ] a\n\n## 非目标\nn\n",
                               encoding="utf-8")
    (d / "plan.md").write_text("# p\n\n## 架构\na\n\n## 接口/数据\nd\n\n## 回滚\nr\n",
                               encoding="utf-8")
    (d / "tasks.md").write_text("# t\n\n## 任务清单\n| ID | 任务 | Owner | 验收 | 状态 |\n|---|---|---|---|---|\n| T1 | x | 田丰 | y | doing |\n",
                                encoding="utf-8")
    (d / "converge.md").write_text("# c\n\n## 证据\n- evidence.json\n\n## 结论\nconverged\n",
                                   encoding="utf-8")
    c2 = S.check("SPEC-T1", root=root)
    assert c2["ok"] is True, c2
    assert c2["stages"][-1]["verdict"] == "converged"
    rows = S.list_specs(root)
    assert rows and rows[0]["id"] == "SPEC-T1"


def test_bad_id(tmp_path):
    try:
        S.init("../evil", "x", root=tmp_path)
        assert False
    except ValueError:
        pass
