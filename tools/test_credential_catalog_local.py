# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import credential_catalog as CC  # noqa: E402


def test_local_dpapi_merge_and_override(tmp_path):
    ws = tmp_path / "ws"
    root = ws / "老田的AI助手" / "work" / "credentials"
    root.mkdir(parents=True)
    (ws / "_vault").mkdir()
    (root / "newkey.dpapi").write_bytes(b"x")
    idx = root / "index.json"
    idx.write_text(json.dumps({
        "newkey": {"name": "newkey", "category": "data", "env": "NEW_KEY", "file": "newkey.dpapi"},
        "tushare": {"name": "tushare", "category": "data", "env": "TUSHARE_TOKEN", "file": "newkey.dpapi"},
    }), encoding="utf-8")
    cfg = tmp_path / "registry.json"
    cfg.write_text(json.dumps({"entries": [
        {"name": "Tushare Token", "category": "data", "env": "TUSHARE_TOKEN", "source": "missing.txt"},
    ]}), encoding="utf-8")
    d = CC.catalog(config_path=cfg, workspace=ws, local_index=idx)
    names = [x["name"] for x in d["items"]]
    assert "newkey" in names
    ts = next(x for x in d["items"] if x["env"] == "TUSHARE_TOKEN")
    assert ts["encrypted"] is True and ts["status"] == "ok"
    assert d["counts"]["missing"] == 0
