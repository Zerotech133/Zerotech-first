#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3 判据：safe = not (breadth<0.40 or med20<-0.03)（现行判据冻结）。

出处：qpzm_backtest.env_gate（130-144 行，复刻 market_env，
2026-10-15 预注册冻结）。本文件只做判据壳，参数阈值在同目录
policy.json 用规范化体 sha256 冻结（sort_keys + separators=(',',':')
+ ensure_ascii=False，剔除 sha 键自身）——复刻 voltarget_ab.
ensure_config 范式（b建仓评估启动包决议②），防事后归因。

特征缺失（breadth/med20 任一为 None）→ safe=None 缺维降权，
不误杀（market_env S6 既有语义）。
"""

import hashlib
import json
import os

POLICY_VER = "v3_breadth40_med20"

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_FP = os.path.join(HERE, "policy.json")

# 现行判据参数（qpzm_backtest.env_gate 现值，2026-10-15 冻结）
POLICY_TEMPLATE = {
    "breadth_lt": 0.40,  # 宽度低于 40% 触发否决
    "med20_lt": -0.03,  # 20 日收益中位低于 -3% 触发否决
    "policy_sha256": None,  # 冻结后回填，ensure_policy 自证未被改
}

SHA_KEY = "policy_sha256"


def _norm_sha(cfg):
    """规范化体 sha256：剔除 sha 键自身后 sort_keys+紧凑分隔+UTF-8。"""
    body = {k: v for k, v in cfg.items() if k != SHA_KEY}
    return hashlib.sha256(
        json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()[:16]


def ensure_policy(fp=POLICY_FP):
    """首日冻结判据参数进 policy.json；已存在则校验 sha 未被改。

    返回 (cfg, intact, sha)。intact=False 即 policy.json 被手工改参
    ——产线必须拒绝使用该配置（只认冻结版）。
    """
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            cfg = json.load(f)
        stored = cfg.get(SHA_KEY)
        calc = _norm_sha(cfg)
        return cfg, (stored == calc), calc
    cfg = json.loads(json.dumps(POLICY_TEMPLATE))
    cfg[SHA_KEY] = _norm_sha(cfg)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)
    return cfg, True, cfg[SHA_KEY]


class V3Policy:
    """v3 判据对象（EnvGate 的 policy 插件）。

    evaluate(features) -> (safe | None, conditions dict)。
    """

    def __init__(self, fp=POLICY_FP):
        self.cfg, self.intact, self.sha = ensure_policy(fp)
        self.ver = POLICY_VER

    def evaluate(self, features):
        feats = features or {}
        b = feats.get("breadth")
        m = feats.get("med20")
        if b is None or m is None:
            missing = [k for k, v in (("breadth", b), ("med20", m)) if v is None]
            return None, {"missing": missing}
        b_kill = float(b) < float(self.cfg["breadth_lt"])
        m_kill = float(m) < float(self.cfg["med20_lt"])
        cond = {
            "breadth_lt_%g" % float(self.cfg["breadth_lt"]): b_kill,
            "med20_lt_%g" % float(self.cfg["med20_lt"]): m_kill,
        }
        return (not (b_kill or m_kill)), cond
