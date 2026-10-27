"""scan_secrets 误报修复回归（WO-GUANYU-SCANFIX-20261027，2026-10-27）。

覆盖三类误报豁免 + 三类真实凭据不弱化：
- kwarg/属性自引用（app_secret=app_secret、self.sign_secret=sign_secret）
- Python 字面量（password=None）、env 读取（pwd=os.environ.get(...)）、未加引号标识符引用
- 时间戳/epoch 被误判手机号；multipart boundary / 字典键名误判 HIGH_ENTROPY
- 真实凭据仍命中：加引号 token、加引号 password、.env 未加引号赋值
"""
import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "scan_secrets.py")
_spec = importlib.util.spec_from_file_location("scan_secrets_t", TOOL)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


def _hits(tmp_path, content, fname="t.py"):
    p = tmp_path / fname
    p.write_text(content, encoding="utf-8")
    hits, _meta = ss.scan_file(str(p))
    return [h for h in hits if not h[3]]


@pytest.mark.parametrize("content,fname", [
    ("send(app_id=app_id, app_secret=app_secret)\n", "t.py"),
    ("self.sign_secret = sign_secret\n", "t.py"),
    ("connect(password=None, db=db)\n", "t.py"),
    ('pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()\n', "t.py"),
    ("token = datamart._READ_CATALOG.set(catalog)\n", "t.py"),
    ("# Tushare 仓 data/tushare：manifest + 文件统计\n", "t.py"),
    ('"created_at": 1760582400000,\n', "t.py"),
    ("unix = (ft - 116444736000000000) / 10_000_000\n", "t.py"),
    ('boundary = "----QPZMabcdEFGH1234gW"\n', "t.py"),
    ('"sign_field_8": self._secret_fp,\n', "t.py"),
])
def test_false_positive_exempt(tmp_path, content, fname):
    assert _hits(tmp_path, content, fname) == []


@pytest.mark.parametrize("content,fname", [
    ('TUSHARE_TOKEN = "abcd1234efgh5678ijkl"\n', "t.py"),
    ('password = "S3cr3t!Pass99"\n', "t.py"),
    ('"url": "https://api.tushare.pro/mcp/?token=abcd1234efgh5678ijkl"' + "\n", "t.json"),
])
def test_real_credentials_still_detected(tmp_path, content, fname):
    assert _hits(tmp_path, content, fname), "真实凭据必须仍被检出（不得因豁免而弱化）"
