#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3 判据测试：三分支（breadth 杀/med 杀/放行）+ 边界等值 +
缺维 safe=None + policy.json 冻结与篡改检出。"""

import hashlib
import json

from gatekeeper.policies import v3_breadth40_med20 as v3


def _fresh_policy(tmp_path):
    """在临时目录首日冻结一份 policy 并返回 V3Policy。"""
    fp = str(tmp_path / "policy.json")
    cfg, intact, sha = v3.ensure_policy(fp)
    assert intact
    pol = v3.V3Policy(fp)
    assert pol.intact and pol.sha == sha
    return pol, fp


def _write_cfg(tmp_path, cfg):
    fp = str(tmp_path / "policy.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return fp


def test_ver_and_template():
    assert v3.POLICY_VER == "v3_breadth40_med20"
    assert v3.POLICY_TEMPLATE["breadth_lt"] == 0.40
    assert v3.POLICY_TEMPLATE["med20_lt"] == -0.03


def test_three_branches(tmp_path):
    pol, _ = _fresh_policy(tmp_path)
    # breadth 杀：宽度 <40% 即否决（med20 再好也杀）
    safe, cond = pol.evaluate({"breadth": 0.35, "med20": 0.01})
    assert safe is False and cond["breadth_lt_0.4"] is True
    # med 杀：宽度够但 med20 <-3% 否决
    safe, cond = pol.evaluate({"breadth": 0.55, "med20": -0.05})
    assert safe is False and cond["med20_lt_-0.03"] is True
    # 放行：双条件都不触发
    safe, cond = pol.evaluate({"breadth": 0.50, "med20": 0.01})
    assert safe is True and not cond["breadth_lt_0.4"] and not cond["med20_lt_-0.03"]


def test_boundary_equality_not_killed(tmp_path):
    """判据是严格小于（<）：恰好压线不杀（同 qpzm env_gate 的
    ~(breadth<0.40 or med20<-0.03)）。"""
    pol, _ = _fresh_policy(tmp_path)
    safe, _ = pol.evaluate({"breadth": 0.40, "med20": -0.03})
    assert safe is True


def test_missing_dim_safe_none(tmp_path):
    """缺维 → safe=None（降权不误杀），conditions 记缺失维度。"""
    pol, _ = _fresh_policy(tmp_path)
    safe, cond = pol.evaluate({"breadth": None, "med20": 0.01})
    assert safe is None and cond["missing"] == ["breadth"]
    safe, cond = pol.evaluate({})
    assert safe is None and set(cond["missing"]) == {"breadth", "med20"}


def test_first_freeze_and_sha(tmp_path):
    fp = str(tmp_path / "policy.json")
    cfg, intact, sha = v3.ensure_policy(fp)
    assert intact and len(sha) == 16
    # sha = 规范化体（剔除 sha 键）sort_keys+紧凑分隔+UTF-8 的 sha256 前 16
    body = {k: v for k, v in cfg.items() if k != v3.SHA_KEY}
    calc = hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert sha == calc
    # 落盘文件回填了 sha，二次读取 intact
    with open(fp, encoding="utf-8") as f:
        assert json.load(f)[v3.SHA_KEY] == sha
    cfg2, intact2, sha2 = v3.ensure_policy(fp)
    assert intact2 and sha2 == sha and cfg2 == cfg


def test_tamper_detected(tmp_path):
    """篡改 policy.json（改参不改 sha）→ intact=False 检出。"""
    fp = str(tmp_path / "policy.json")
    cfg, _, sha = v3.ensure_policy(fp)
    tampered = dict(cfg)
    tampered["breadth_lt"] = 0.45  # 改参
    cfg2, intact2, sha2 = v3.ensure_policy(_write_cfg(tmp_path, tampered))
    assert intact2 is False
    assert sha2 != sha  # 重算 sha 随体变，与旧冻结不符
