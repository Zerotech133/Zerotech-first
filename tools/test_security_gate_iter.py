# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import security_gate as SG  # noqa: E402


def test_iter_skips_inaccessible_files(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "mysql.sock"
    bad.write_text("x", encoding="utf-8")
    real = Path.is_file

    def fake_is_file(self):
        if self.name == "mysql.sock":
            raise OSError(1920, "系统无法访问此文件")
        return real(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    rows = list(SG._iter(tmp_path))
    assert any(rel == "ok.py" for _, rel in rows)
    assert all(rel != "mysql.sock" for _, rel in rows)
