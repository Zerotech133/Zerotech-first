# -*- coding: utf-8 -*-
# K-STOCK-408 守卫测试：PORTAL_KEY 轮换底座（不碰生产配置/不发网络请求）。
# 覆盖：①发码侧 _load_portal_key 三级取值；②校验侧 server_gate 双口令
# （新/旧码都放行，错码/空码拒）；③种新码 Cookie 换轨条件；④两源一致性
# 软校验（portal/work/portal_key.json 存在时才比对）。
# 运行：python portal/test_portal_key_rotation.py
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAIL = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAIL.append(name)


# ── ① 发码侧：_load_portal_key 三级取值 ─────────────────────
import feishu_card_v2 as FC  # noqa: E402

tmp_dir = tempfile.mkdtemp(prefix="_k408_")
_orig_env = os.environ.pop("PORTAL_KEY", None)
_orig_here = FC.HERE
_orig_vault = FC.VAULT_DIR
try:
    # env 覆盖优先
    os.environ["PORTAL_KEY"] = "env-code"
    check("env 覆盖生效", FC._load_portal_key() == "env-code")
    del os.environ["PORTAL_KEY"]

    # vault 文件（FC.VAULT_DIR 重定向到临时目录，读值路径走生产逻辑）
    FC.HERE = tmp_dir
    FC.VAULT_DIR = os.path.join(tmp_dir, "vault")
    os.makedirs(FC.VAULT_DIR, exist_ok=True)
    with open(os.path.join(FC.VAULT_DIR, "portal_key.json"), "w", encoding="utf-8") as f:
        json.dump({"key": "  file-code  \n"}, f)  # 带空白验证 strip
    check("vault 文件读取+strip", FC._load_portal_key() == "file-code")

    # 旧路径回退（vault 缺失时读 HERE/work）
    os.remove(os.path.join(FC.VAULT_DIR, "portal_key.json"))
    os.makedirs(os.path.join(tmp_dir, "work"), exist_ok=True)
    with open(os.path.join(tmp_dir, "work", "portal_key.json"), "w", encoding="utf-8") as f:
        json.dump({"key": "legacy-code"}, f)
    check("旧路径回退", FC._load_portal_key() == "legacy-code")

    # 缺失 → 空串 fail-safe
    os.remove(os.path.join(tmp_dir, "work", "portal_key.json"))
    check("配置缺失=空串 fail-safe", FC._load_portal_key() == "")
finally:
    FC.HERE = _orig_here
    FC.VAULT_DIR = _orig_vault
    if _orig_env is not None:
        os.environ["PORTAL_KEY"] = _orig_env
    elif "PORTAL_KEY" in os.environ:
        del os.environ["PORTAL_KEY"]

# ── ② 校验侧：server_gate 双口令 ───────────────────────────
import server_gate as SG  # noqa: E402

_PROD_NEW_PW = SG.NEW_PW  # K-STOCK-410：留生产值——②节 monkeypatch 双口令
# 分叉场景会污染模块属性，④节两源一致性必须比对这个

SG.NEW_PW, SG.OLD_PW = "new-code", "old-code"  # 双口令分叉场景
check("_key_ok 新码", SG._key_ok("new-code"))
check("_key_ok 旧码（兼容期）", SG._key_ok("old-code"))
check("_key_ok 错码拒", not SG._key_ok("wrong"))
check("_key_ok 空码拒", not SG._key_ok(""))


def _h(path="/?key=old-code", cookie=None, auth=None):
    """Handler 裸实例（绕 __init__ 不起服务），只挂 headers/path。"""
    hh = SG.Handler.__new__(SG.Handler)
    hh.path = path
    hh.headers = {}
    if cookie:
        hh.headers["Cookie"] = "picks_key=%s" % cookie
    if auth:
        hh.headers["Authorization"] = auth
    return hh


check("query 旧码放行", _h("/?key=old-code")._query_key_ok())
check("query 新码放行", _h("/?key=new-code")._query_key_ok())
check("query 错码拒", not _h("/?key=wrong")._query_key_ok())
check("cookie 旧码放行", _h("/", cookie="old-code")._cookie_ok())
check("cookie 新码放行", _h("/", cookie="new-code")._cookie_ok())
check("cookie 错码拒", not _h("/", cookie="wrong")._cookie_ok())

import base64  # noqa: E402

b64 = base64.b64encode(b"lao:old-code").decode()
check("basic 旧码放行", _h("/", auth="Basic " + b64)._basic_ok())
b64w = base64.b64encode(b"lao:wrong").decode()
check("basic 错码拒", not _h("/", auth="Basic " + b64w)._basic_ok())

# ── ③ 换轨条件：Cookie 非新码 + query 带码 → 该种新码 ───────
_h1 = _h("/?key=old-code")  # 无 Cookie
_h2 = _h("/?key=old-code", cookie="old-code")  # 旧 Cookie 在身（关键换轨场景）
_h3 = _h("/?key=new-code", cookie="new-code")  # 已是新码（不必重种）
cond = lambda hh: hh._query_key_ok() and hh._cookie_value() != SG.NEW_PW
check("无Cookie+旧key → 种新码", cond(_h1))
check("旧Cookie+旧key → 种新码（无感换轨核心）", cond(_h2))
check("新Cookie+新key → 不重种", not cond(_h3))

# ── ④ 两源一致性（软校验：配置文件存在才比对）──────────────
cfg_real = os.path.join(HERE, "work", "portal_key.json")
if os.path.exists(cfg_real):
    with open(cfg_real, encoding="utf-8") as f:
        cfg_key = (json.load(f).get("key") or "").strip()
    # K-STOCK-410：比 _PROD_NEW_PW（import 时原值），不吃②节 monkeypatch 污染
    check("两源一致（work 配置 == server_gate.NEW_PW）", cfg_key == _PROD_NEW_PW)
else:
    print("SKIP 两源一致性（portal/work/portal_key.json 尚未创建——轮换动作落地时生效）")

print("FAILURES: %d" % len(FAIL))
sys.exit(1 if FAIL else 0)
