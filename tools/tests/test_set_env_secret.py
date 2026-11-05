# -*- coding: utf-8 -*-
"""tools/set_env_secret.py 单测（DPAPI 用桩替换，不触碰真实凭据库）。"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "set_env_secret.py"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 仅 Windows")


def load_tool():
    spec = importlib.util.spec_from_file_location("set_env_secret_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tool(monkeypatch):
    mod = load_tool()
    # 桩：dpapi 只做可逆变换，避免测试依赖真实密钥库
    monkeypatch.setattr(mod, "dpapi_protect", lambda b: b"P:" + b[::-1])
    monkeypatch.setattr(mod, "dpapi_unprotect", lambda b: b[2:][::-1])
    return mod


def make_enc(path: Path, keys: dict) -> Path:
    enc = {k: base64.b64encode(b"P:" + v.encode()[::-1]).decode() for k, v in keys.items()}
    path.write_text(json.dumps(enc), encoding="utf-8")
    return path


def test_read_from_dotenv_strips_quotes(tool, tmp_path):
    p = tmp_path / ".env.local"
    p.write_text('OTHER=1\nTUSHARE_TOKEN="abc123"\n', encoding="utf-8")
    assert tool.read_from_dotenv(str(p), "TUSHARE_TOKEN") == "abc123"


def test_read_from_dotenv_missing_key(tool, tmp_path):
    p = tmp_path / ".env.local"
    p.write_text("OTHER=1\n", encoding="utf-8")
    with pytest.raises(KeyError):
        tool.read_from_dotenv(str(p), "TUSHARE_TOKEN")


def test_dry_run_does_not_write(tool, tmp_path):
    enc = make_enc(tmp_path / "e.enc", {"TUSHARE_TOKEN": "old", "K2": "v2"})
    src = tmp_path / "src.env"
    src.write_text("TUSHARE_TOKEN=newvalue\n", encoding="utf-8")
    before = enc.read_bytes()
    rc = tool.main(["--key", "TUSHARE_TOKEN", "--from-dotenv", str(src),
                    "--enc", str(enc), "--dry-run"])
    assert rc == 0
    assert enc.read_bytes() == before


def test_rotate_updates_only_target_key(tool, tmp_path):
    enc = make_enc(tmp_path / "e.enc", {"TUSHARE_TOKEN": "old", "K2": "v2"})
    src = tmp_path / "src.env"
    src.write_text("TUSHARE_TOKEN=newvalue\n", encoding="utf-8")
    rc = tool.main(["--key", "TUSHARE_TOKEN", "--from-dotenv", str(src),
                    "--enc", str(enc), "--no-backup"])
    assert rc == 0
    data = json.loads(enc.read_text(encoding="utf-8"))
    assert tool.dpapi_unprotect(base64.b64decode(data["TUSHARE_TOKEN"])).decode() == "newvalue"
    assert tool.dpapi_unprotect(base64.b64decode(data["K2"])).decode() == "v2"


def test_same_value_is_noop(tool, tmp_path):
    enc = make_enc(tmp_path / "e.enc", {"TUSHARE_TOKEN": "same"})
    src = tmp_path / "src.env"
    src.write_text("TUSHARE_TOKEN=same\n", encoding="utf-8")
    before = enc.read_bytes()
    rc = tool.main(["--key", "TUSHARE_TOKEN", "--from-dotenv", str(src), "--enc", str(enc)])
    assert rc == 0
    assert enc.read_bytes() == before


def test_empty_value_aborts(tool, tmp_path):
    enc = make_enc(tmp_path / "e.enc", {"TUSHARE_TOKEN": "old"})
    src = tmp_path / "src.env"
    src.write_text("TUSHARE_TOKEN=\n", encoding="utf-8")
    before = enc.read_bytes()
    rc = tool.main(["--key", "TUSHARE_TOKEN", "--from-dotenv", str(src), "--enc", str(enc)])
    assert rc == 2
    assert enc.read_bytes() == before


def test_unknown_key_aborts(tool, tmp_path):
    enc = make_enc(tmp_path / "e.enc", {"K2": "v2"})
    src = tmp_path / "src.env"
    src.write_text("NOPE=x\n", encoding="utf-8")
    assert tool.main(["--key", "NOPE", "--from-dotenv", str(src), "--enc", str(enc)]) == 2
