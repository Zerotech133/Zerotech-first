# -*- coding: utf-8 -*-
"""军团 Codex bridge 测试（M1 校验器 + M2 试点注册）。

恶意样本集固化为回归测试（设计稿 §5.4 判例：校验器若静默放过一次，
漏洞即成通道契约）。测试全程 tmp 隔离，绝不写 agent_platform/bridge_inbox。
"""
import shutil
import sys
from pathlib import Path

import pytest

AP = Path(__file__).resolve().parent.parent
for p in ("adapters", "engine"):
    if str(AP / p) not in sys.path:
        sys.path.insert(0, str(AP / p))

from codex_bridge import (  # noqa: E402
    IMPORT_ALLOWLIST, load_registered, process_submission,
    register_from_source, validate_source, write_reject)
from strategy_registry import (  # noqa: E402
    StrategyError, StrategyRegistry)

PILOT = (AP / "bridge_inbox" / "processed" / "20261018_sma20_rsi14"
         / "strategy_20261018_sma20_rsi14.py").read_text(encoding="utf-8")

GOOD = '''# -*- coding: utf-8 -*-
"""测试插件：均值差一档。"""
from statistics import fmean
from strategy_registry import StrategyAgent, Verdict

TH = 0.01

class CodexGoodAgent(StrategyAgent):
    """单类合规插件。"""
    strategy_id = "codex_good"
    stance = "neutral"

    def propose(self, context):
        closes = [float(x) for x in (context.get("closes") or [])]
        symbol = context.get("symbol", "")
        if len(closes) < 2:
            return Verdict(strategy_id=self.strategy_id, symbol=symbol,
                           recommendation="hold", confidence=0.0,
                           stance="neutral", score=0.0, rationale="样本不足")
        prev = fmean(closes[:-1])
        ch = closes[-1] / prev - 1.0 if prev > 0 else 0.0
        rec = "buy" if ch > TH else ("sell" if ch < -TH else "hold")
        return Verdict(strategy_id=self.strategy_id, symbol=symbol,
                       recommendation=rec, confidence=0.5, stance="neutral",
                       score=round(ch, 4), rationale="单档均值差")
'''


# ---- M1：静态校验 ----

def test_pilot_and_good_plugin_pass():
    assert validate_source(PILOT) == []
    assert validate_source(GOOD) == []


def test_reject_subprocess_import():
    bad = GOOD.replace("from statistics import fmean",
                       "import subprocess")
    reasons = validate_source(bad)
    assert any("subprocess" in r for r in reasons)


def test_reject_importlib():
    bad = GOOD.replace("from statistics import fmean",
                       "import importlib")
    assert any("importlib" in r for r in validate_source(bad))


def test_reject_eval_call():
    bad = GOOD.replace("rec = \"buy\"", "rec = eval(\"'buy'\") && False or \"buy\"") \
        if False else GOOD.replace(
        '        rec = "buy" if ch > TH',
        '        rec = eval("chr(98)+chr(117)+chr(121)") if ch > TH')
    assert any("eval" in r for r in validate_source(bad))


def test_reject_open_write():
    bad = GOOD.replace('        prev = fmean(closes[:-1])',
                       '        open("/tmp/x.txt", "w").write("leak")\n'
                       '        prev = fmean(closes[:-1])')
    reasons = validate_source(bad)
    assert any("open" in r for r in reasons)


def test_reject_credential_assignment():
    bad = GOOD.replace('TH = 0.01', 'api_key = "sk-123"')
    assert any("凭据" in r for r in validate_source(bad))


def test_reject_double_subclass():
    bad = GOOD + "\n\n" + GOOD.replace("codex_good", "codex_good2") \
        .replace("class CodexGoodAgent", "class CodexSecondAgent")
    reasons = validate_source(bad)
    assert any("恰好定义一个" in r for r in reasons)


def test_reject_init_defined():
    bad = GOOD.replace('    def propose(self, context):',
                       '    def __init__(self):\n'
                       '        self.state = {}\n\n'
                       '    def propose(self, context):')
    assert any("__init__" in r for r in validate_source(bad))


def test_reject_missing_codex_prefix():
    bad = GOOD.replace('strategy_id = "codex_good"',
                       'strategy_id = "shadow_evil"')
    assert any("codex_" in r for r in validate_source(bad))


def test_reject_bad_stance():
    bad = GOOD.replace('stance = "neutral"', 'stance = "alpha"')
    assert any("stance" in r for r in validate_source(bad))


def test_reject_no_propose():
    bad = GOOD.replace("    def propose(self, context):",
                       "    def propose_x(self, context):")
    assert any("propose" in r for r in validate_source(bad))


def test_reject_oversize_lines():
    big = GOOD + "\n" * 400
    assert any("行数" in r for r in validate_source(big))


def test_reject_syntax_error():
    assert any("语法错误" in r for r in validate_source("def broken(:\n"))


def test_allowlist_is_pure_computation_plus_host():
    assert "strategy_registry" in IMPORT_ALLOWLIST
    for banned in ("os", "sys", "socket", "subprocess", "requests",
                   "urllib", "ctypes", "shutil", "pathlib"):
        assert banned not in IMPORT_ALLOWLIST


# ---- M1：注册与漂移拒绝 ----

def _fresh_registry():
    return StrategyRegistry()


def test_register_from_source_ok_and_drift_reject(tmp_path):
    reg = _fresh_registry()
    agent = register_from_source(GOOD, reg)
    assert "codex_good" in reg.list_ids()
    assert agent.strategy_id == "codex_good"
    with pytest.raises(StrategyError):
        register_from_source(GOOD, reg)  # 同键异值=漂移拒绝


def test_register_refuses_unvalidated_source():
    bad = GOOD.replace("from statistics import fmean", "import os")
    with pytest.raises(StrategyError):
        register_from_source(bad, _fresh_registry())


def test_pilot_registers_and_produces_verdict():
    from strategy_registry import Verdict
    reg = _fresh_registry()
    agent = register_from_source(PILOT, reg)
    assert agent.strategy_id == "codex_sma20_rsi14"
    v = agent.propose({"symbol": "600519",
                       "closes": [10.0 + 0.01 * i for i in range(21)]})
    assert isinstance(v, Verdict) and v.strategy_id == "codex_sma20_rsi14"
    v.validate()
    # 顺势未极端：匀速上涨 RSI=100 超买 → 反向 sell
    assert v.recommendation == "sell"
    # 样本不足降级 hold
    v2 = agent.propose({"symbol": "600519", "closes": [10.0, 10.1]})
    assert v2.recommendation == "hold" and v2.confidence == 0.0


# ---- fail-visible 落档 + 管线 ----

def test_write_reject_creates_readable_doc(tmp_path):
    path = write_reject(tmp_path, "sub-1", "静态校验", ["规则A", "规则B"])
    text = path.read_text(encoding="utf-8")
    assert "sub-1" in text and "规则A" in text and "静态校验" in text


def test_process_submission_reject_moves_to_rejects(tmp_path):
    reg = _fresh_registry()
    bad = GOOD.replace("from statistics import fmean", "import os")
    ok, reasons = process_submission(tmp_path, "s-bad", bad, reg)
    assert not ok and reasons
    assert (tmp_path / "rejects" / "s-bad.md").exists()
    assert not (tmp_path / "inbox" / "s-bad").exists()
    assert reg.list_ids() == []


def test_process_submission_accept_moves_to_processed(tmp_path):
    reg = _fresh_registry()
    ok, reasons = process_submission(tmp_path, "s-ok", GOOD, reg)
    assert ok and not reasons
    assert (tmp_path / "processed" / "s-ok" /
            "strategy_s-ok.py").exists()
    assert "codex_good" in reg.list_ids()


def test_process_submission_dry_run_no_ledger(tmp_path, capsys):
    reg = _fresh_registry()
    process_submission(tmp_path, "s-dry", GOOD, reg, data_root=None)
    assert not (tmp_path / "data" / "codex_bridge").exists()  # 无 root 不落账


def test_process_submission_registers_event_with_root(tmp_path):
    reg = _fresh_registry()
    data_root = tmp_path / "dataroot"
    process_submission(tmp_path, "s-ledger", GOOD, reg, data_root=data_root)
    ledger = data_root / "data" / "codex_bridge" / "registrations.jsonl"
    assert ledger.exists()
    assert "s-ledger" in ledger.read_text(encoding="utf-8")


def test_load_registered_idempotent(tmp_path):
    reg = _fresh_registry()
    process_submission(tmp_path, "s-load", GOOD, reg)
    reg2 = _fresh_registry()
    loaded, skipped = load_registered(reg2, bridge_dir=tmp_path)
    assert loaded == 1 and skipped == 0
    loaded2, skipped2 = load_registered(reg2, bridge_dir=tmp_path)
    assert loaded2 == 0 and skipped2 == 1  # 同 id=重复装载跳过
    assert "codex_good" in reg2.list_ids()


def test_load_registered_no_dir_is_noop(tmp_path):
    loaded, skipped = load_registered(_fresh_registry(),
                                      bridge_dir=tmp_path / "nope")
    assert (loaded, skipped) == (0, 0)


def test_pilot_real_inbox_registers_into_default_registry():
    """M2 端到端：试点提交物注册进 default registry（真实 inbox 文件）。"""
    from shadow_strategies import build_default_registry
    reg = build_default_registry()
    loaded, _ = load_registered(reg)
    assert loaded >= 1
    assert "codex_sma20_rsi14" in reg.list_ids()
