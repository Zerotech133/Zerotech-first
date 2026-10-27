"""gen_principal_key.ps1 回归测试（WO-LEGION-KEYGENFIX-20261026）。

背景：脚本原用 `& $python -c $code` 传内嵌 Python，Windows PowerShell 5.1
会吞掉字符串内双引号（实测 `+ "\n"` 变成 `+ \n`）→ Python SyntaxError。
修复：把 $code 落临时 .py 文件再执行。

本测试不生成 principal 密钥、不触碰 _vault：只把脚本内嵌代码抽出来，在
tmp_path 用**测试口令**生成一次性密钥对，验证 (a) 代码可执行 (b) PEM/hex 合法。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[2] / "tools"
_PS1 = _TOOLS / "gen_principal_key.ps1"
_TEST_PP = "test-" + "only-placeholder"


def _read_ps1() -> str:
    try:
        return _PS1.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 环境异常
        pytest.skip("ps1 不可读：%s" % exc)


def _embedded_code(text: str) -> str:
    m = re.search(r"\$code = @'\r?\n(.*?)\r?\n'@", text, re.S)
    assert m, "未找到内嵌 Python 代码块（$code = @' ... '@）"
    return m.group(1)


def test_script_no_longer_passes_code_via_dash_c():
    """修复锚点：不得再用 python -c $code；必须走临时 .py 文件。"""
    text = _read_ps1()
    assert "& $python -c $code" not in text
    assert "principal_keygen_" in text and "$pyPath" in text
    assert "& $python $pyPath" in text


def test_embedded_code_generates_valid_ed25519_keypair(tmp_path):
    """内嵌代码本身可执行：生成 EF 加密 PKCS8 私钥 + 64 hex 公钥。"""
    text = _read_ps1()
    code = _embedded_code(text)
    priv = tmp_path / "principal_private.pem"
    pub = tmp_path / "principal_pub.hex"
    env = dict(os.environ)
    env.update(
        {
            "DSH_KEY_OUT_PRIV": str(priv),
            "DSH_KEY_OUT_PUB": str(pub),
            "DSH_KEY_PASSPHRASE": _TEST_PP,
            "PYTHONUTF8": "1",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "fingerprint_sha256=" in proc.stdout
    hex_text = pub.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", hex_text), hex_text[:80]

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(
        priv.read_bytes(), password=_TEST_PP.encode()
    )
    assert isinstance(key, Ed25519PrivateKey)
    # 公钥 hex 必须与私钥派生的公钥一致（防写错文件）
    derived = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    assert derived == hex_text
