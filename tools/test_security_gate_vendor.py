# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import security_gate as SG  # noqa: E402


def test_vendor_dirs_excluded(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".venvs" / "pkg").mkdir(parents=True)
    (tmp_path / ".venvs" / "pkg" / "bad.py").write_text("eval('x')\n", encoding="utf-8")
    rows = list(SG._iter(tmp_path))
    rels = [rel for _, rel in rows]
    assert "tools/ok.py" in rels
    assert not any(rel.startswith(".venvs/") for rel in rels)
