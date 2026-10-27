# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import credential_catalog as C  # noqa: E402


def test_catalog_status(tmp_path):
    ws = tmp_path
    (ws / "_vault" / "secrets").mkdir(parents=True)
    (ws / "_vault" / "secrets.enc").mkdir(parents=True)
    (ws / "_vault" / "secrets" / "a.json").write_text("{}", encoding="utf-8")
    (ws / "_vault" / "secrets.enc" / "a.json.age").write_text("cipher", encoding="utf-8")
    cfg = tmp_path / "registry.json"
    cfg.write_text(json.dumps({"entries": [
        {"name": "A", "category": "jev", "env": "A_TOKEN",
         "source": "_vault/secrets/a.json",
         "encrypted_source": "_vault/secrets.enc/a.json.age"},
        {"name": "B", "category": "data", "source": "_vault/secrets/missing.txt"},
    ]}), encoding="utf-8")
    d = C.catalog(cfg, workspace=ws)
    a = next(x for x in d["items"] if x["name"] == "A")
    b = next(x for x in d["items"] if x["name"] == "B")
    assert a["encrypted"] is True and a["status"] == "ok"
    assert b["status"] == "missing"
    assert "vault_enc" not in json.dumps(d)


def test_no_secret_fields():
    d = C.catalog()
    for x in d["items"]:
        assert "secret" not in json.dumps(x).lower() or "encrypted_source" in x
