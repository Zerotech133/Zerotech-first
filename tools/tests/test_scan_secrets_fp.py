"""scan_secrets 误报修复回归（WO-GUANYU-SCANFIX-20261027，2026-10-27）。

覆盖三类误报豁免 + 三类真实凭据不弱化：
- kwarg/属性自引用（app_secret=app_secret、self.sign_secret=sign_secret）
- Python 字面量（password=None）、env 读取（pwd=os.environ.get(...)）、未加引号标识符引用
- 时间戳/epoch 被误判手机号；multipart boundary / 字典键名误判 HIGH_ENTROPY
- 真实凭据仍命中：加引号 token、加引号 password、.env 未加引号赋值

2026-10-31 WO-SCANFP-FLAG-20261031（第8轮）：HIGH_ENTROPY 把 docstring/注释里的
布尔开关 `warn_unmatched_over_20pct=True`（含 `=`、熵≈4.1）误判密钥 → 窄豁免
（仅整串 `标识符=True|False` 且无凭据语境）；真实高熵 token/带凭据语境的开关
仍必须拦截。
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
    ("def _load_neo4j_password() -> str:\n", "t.py"),
    ("# 凭据：env → .env.local（见 _load_neo4j_password；不再有硬编码旧值兜底）\n", "t.py"),
    ("NEO4J_PASSWORD = _load_neo4j_password()\n", "t.py"),
    ("folder.RegisterTaskDefinition(TASK_NAME, td, 6, None, None, 3)  # 6=CREATE_OR_UPDATE, 3=TASK_LOGON_INTERACTIVE_TOKEN\n", "t.py"),
    # 2026-10-31 WO-SCANFP-FLAG：docstring/注释里的布尔开关参数（HIGH_ENTROPY 误报）
    ("warn_unmatched_over_20pct=True\n", "t.py"),
    ("warn_unmatched_over_20pct=False\n", "t.py"),
    ("some_flag_20pct=True\n", "t.py"),
    ("SOME_FLAG_20PCT=True\n", "t.py"),
    ('"""\nconfig flags:\nwarn_unmatched_over_20pct=True\n"""\n', "t.py"),
    ("# note: some_flag_20pct=False\n", "t.py"),
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


# ── 第8轮（2026-10-31 WO-SCANFP-FLAG）：布尔开关参数窄豁免 ─────────────
def test_boolean_flag_assign_narrow_unit():
    """豁免必须窄：仅整串 `name=True|False` 且 name/行内无凭据语境。"""
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=True") is True
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=False") is True
    assert ss.is_boolean_flag_assign("SOME_FLAG_20PCT=True") is True
    # 值不是布尔字面量/带多余尾巴 → 不豁免（交回原高熵规则）
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=True123") is False
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=TruePassword") is False
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=Tru") is False
    assert ss.is_boolean_flag_assign("warn_unmatched_over_20pct=1") is False
    # 凭据语境（name 内含 token/key/secret/password，或整行含凭据词）→ 不豁免
    assert ss.is_boolean_flag_assign("api_key_rotation_enabled=True") is False
    assert ss.is_boolean_flag_assign("token_flag_20pct_enabled=True") is False
    assert ss.is_boolean_flag_assign("some_flag_20pct=True",
                                     "password = x") is False


@pytest.mark.parametrize("content", [
    "api_key_rotation_enabled_20pct=True\n",   # name 含 api_key
    "token_flag_20pct_enabled=True\n",         # name 含 token
    "warn_unmatched_over_20pct=TruePassword\n",  # 非布尔字面量（不可豁免）
])
def test_boolean_flag_boundary_not_weakened(tmp_path, content):
    """边界：带凭据语境/非布尔字面量 → 不得借窄豁免放行。"""
    assert _hits(tmp_path, content, "t.py"), (
        "边界形态不得被布尔开关豁免放行：%r" % content)


@pytest.mark.parametrize("content", [
    'token = "Ab3dE5fG7hI9jK1lM3nO5pQ7r"\n',
    'x = "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2Ji1Hg0Fe"\n',   # 无凭据语境的真高熵串
    'TUSHARE_TOKEN = "abcd1234efgh5678ijkl"\n',
    "api_key = '9f8e7d6c5b4a39281706f5e4d3c2b1a0'\n",
])
def test_real_high_entropy_secret_still_blocked(tmp_path, content):
    """真实高熵密钥仍必须拦截（豁免不得弱化 HIGH_ENTROPY 主规则）。"""
    hits = _hits(tmp_path, content, "t.py")
    assert hits, "真实高熵密钥必须仍被检出：%r" % content
    assert any(h[1] in ("HIGH_ENTROPY", "TOKEN_ASSIGN", "PASSWORD_LITERAL")
               for h in hits), hits
