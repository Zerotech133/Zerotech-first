# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import codebase_intel as C  # noqa: E402


def test_map_search_impact(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "core.py").write_text(
        "class Engine:\n    '''引擎'''\n    def run(self):\n        return helper()\n\n"
        "def helper():\n    '''工具函数'''\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from pkg.core import Engine\n\nEngine().run()\n",
                                      encoding="utf-8")
    data = C.build_map(tmp_path, scope=[], use_cache=False)
    assert data["file_count"] >= 3
    names = {s["name"] for s in data["symbols"]}
    assert {"Engine", "run", "helper"} <= names
    hits = C.search("helper", tmp_path, scope=[])
    assert hits and hits[0]["name"] == "helper"
    ref = C.refs("Engine", tmp_path, scope=[])
    assert any(x["path"] == "app.py" for x in ref)
    imp = C.impact("pkg/core.py", tmp_path, scope=[])
    assert any(x["path"] == "app.py" for x in imp["importers"])
