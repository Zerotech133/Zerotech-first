# -*- coding: utf-8 -*-
"""deploy_package 打包链测试（协作协议 v1 · 2026-10-15）。

用临时沙盒目录（monkeypatch 模块常量），不触碰真实 strategies/。
"""

import importlib.util
import json
import os
import unittest
from unittest import mock

# 顶层名 `tools` 会被先收集的 test_qd_backtest_1445 等（module 级
# sys.path.insert(agent_platform/engine)，engine/tools.py 同名文件）
# 毒化为 sys.modules['tools'] 劫持——按文件路径直接加载被测模块，
# 绕开顶层名解析（K-STOCK-211 任务 4，2026-10-17）。
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "deploy_package_under_test",
    os.path.join(os.path.dirname(_here), "deploy_package.py"),
)
DP = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DP)

FROZEN_SHA_BODY = {
    "strategy": "t1",
    "version": "v2",
    "signals": {"top_n": 5},
    "thresholds": {"gate": 0.45},
}


def _make_policy(fp, sha):
    cfg = dict(FROZEN_SHA_BODY)
    cfg["config_sha256"] = sha
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return cfg


def _sha_of(cfg_wo_sha):
    import hashlib

    body = json.dumps(
        cfg_wo_sha, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class TestDeploy(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="deploy_pkg_test_")
        self.addCleanup(_rmtree, self.tmp)
        self.strat = os.path.join(self.tmp, "strategies", "t1")
        self.dep = os.path.join(self.tmp, "deploy_packages")
        os.makedirs(self.strat)
        for fn in DP.PKG_FILES:
            open(os.path.join(self.strat, fn), "w", encoding="utf-8").close()
        _make_policy(os.path.join(self.strat, "policy.json"), "X" * 64)
        with open(
            os.path.join(self.strat, "MANIFEST.json"), "w", encoding="utf-8"
        ) as f:
            json.dump({"name": "t1", "deploy": {"deployed": False}}, f)
        self.patcher = mock.patch.multiple(
            DP, STRAT_DIR=os.path.join(self.tmp, "strategies"), DEPLOY_DIR=self.dep
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_unfrozen_rejected(self):
        # PLACEHOLDER 未冻结 → fail-closed 拒绝部署
        self.assertEqual(DP.deploy("t1"), 3)
        self.assertFalse(os.path.isdir(self.dep))

    def test_sha_mismatch_rejected(self):
        # 文件体与冻结值不一致 → 拒绝
        _make_policy(os.path.join(self.strat, "policy.json"), "A" * 64)
        self.assertEqual(DP.deploy("t1"), 3)

    def test_missing_files_rejected(self):
        os.remove(os.path.join(self.strat, "feeder.yaml"))
        self.assertEqual(DP.deploy("t1"), 2)

    def test_deploy_ok_and_idempotent(self):
        sha = _sha_of(dict(FROZEN_SHA_BODY))
        _make_policy(os.path.join(self.strat, "policy.json"), sha)
        self.assertEqual(DP.deploy("t1"), 0)
        vdir = os.path.join(self.dep, "t1", "v2_" + sha[:8])
        self.assertTrue(os.path.exists(os.path.join(vdir, "PACKAGE.json")))
        self.assertTrue(os.path.exists(os.path.join(vdir, "policy.json")))
        # PACKAGE.json sha 与规范化体哈希一致
        with open(os.path.join(vdir, "PACKAGE.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["policy_sha256"], sha)
        # MANIFEST.deploy 回写
        with open(os.path.join(self.strat, "MANIFEST.json"), encoding="utf-8") as f:
            man = json.load(f)
        self.assertTrue(man["deploy"]["deployed"])
        self.assertEqual(man["deploy"]["sha"], sha)
        # 幂等：再跑 SKIP
        self.assertEqual(DP.deploy("t1"), 0)


def _rmtree(p):
    import shutil

    shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
