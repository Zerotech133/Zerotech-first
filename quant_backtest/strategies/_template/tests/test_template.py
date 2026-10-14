# -*- coding: utf-8 -*-
"""模板最小测试（策略包规格 v1，出处：方案 §2.1 tests/、方案 §六回归锁）。

复制后按本文件三个测试位填本策略断言：
  1) 纯函数性：signals 无 IO/无全局态——同输入必同输出（回归锁前提）；
  2) 输出契约：列 [date, code, signal]，signal ∈ {0,1}；
  3) 判据边界：thresholds 边界值行为回归（迁移包改为等价 diff=0 断言，
     出处：241 日 diff=0 范式）。
"""
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
SIGNAL_PY = os.path.join(PKG, "signal.py")


def _load_signal_mod():
    """按文件路径加载包内 signal.py。

    注意：不能 `import signal`——标准库 signal 易被 pytest 等框架预
    加载进 sys.modules 而遮蔽包内同名文件，故显式按路径装载。
    """
    spec = importlib.util.spec_from_file_location("pkg_signal", SIGNAL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _policy():
    with open(os.path.join(PKG, "policy.json"), encoding="utf-8") as f:
        return json.load(f)


def test_policy_sha_present():
    """policy.json 必须含 sha 字段（冻结机制占位校验，规格 §2）。"""
    p = _policy()
    assert "config_sha256" in p


def test_signals_pure_function():
    """同输入两次调用输出一致（纯函数约定）。填实现后启用真实数据。"""
    # 占位：模板未实现，先验证接口存在；填实现后替换为真实断言
    sig = _load_signal_mod()
    assert callable(sig.load_features)
    assert callable(sig.signals)


def test_signals_output_contract():
    """输出契约：[date, code, signal] 且 signal ∈ {0,1}。

    填实现后示例：
        feats = sig.load_features(feeder)
        df = sig.signals(feats, _policy())
        assert list(df.columns) >= ["date", "code", "signal"]
        assert set(df["signal"].unique()) <= {0, 1}
    """
    assert True  # 占位
