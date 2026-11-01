# -*- coding: utf-8 -*-
"""军团 Codex bridge 测试（M1 校验器 + M2 试点注册）。

恶意样本集固化为回归测试（设计稿 §5.4 判例：校验器若静默放过一次，
漏洞即成通道契约）。测试全程 tmp 隔离，绝不写 agent_platform/bridge_inbox。
"""

import sys
import time
from pathlib import Path

import pytest

AP = Path(__file__).resolve().parent.parent
for p in ("adapters", "engine"):
    if str(AP / p) not in sys.path:
        sys.path.insert(0, str(AP / p))

from codex_bridge import (  # noqa: E402
    IMPORT_ALLOWLIST,
    load_registered,
    process_submission,
    register_from_source,
    validate_source,
    write_reject,
)
from strategy_registry import (  # noqa: E402
    StrategyError,
    StrategyRegistry,
)

PILOT = (
    AP
    / "bridge_inbox"
    / "processed"
    / "20261018_sma20_rsi14"
    / "strategy_20261018_sma20_rsi14.py"
).read_text(encoding="utf-8")

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
    bad = GOOD.replace("from statistics import fmean", "import subprocess")
    reasons = validate_source(bad)
    assert any("subprocess" in r for r in reasons)


def test_reject_importlib():
    bad = GOOD.replace("from statistics import fmean", "import importlib")
    assert any("importlib" in r for r in validate_source(bad))


def test_reject_eval_call():
    bad = (
        GOOD.replace('rec = "buy"', 'rec = eval("\'buy\'") && False or "buy"')
        if False
        else GOOD.replace(
            '        rec = "buy" if ch > TH',
            '        rec = eval("chr(98)+chr(117)+chr(121)") if ch > TH',
        )
    )
    assert any("eval" in r for r in validate_source(bad))


def test_reject_open_write():
    bad = GOOD.replace(
        "        prev = fmean(closes[:-1])",
        '        open("/tmp/x.txt", "w").write("leak")\n'
        "        prev = fmean(closes[:-1])",
    )
    reasons = validate_source(bad)
    assert any("open" in r for r in reasons)


def test_reject_credential_assignment():
    bad = GOOD.replace("TH = 0.01", 'api_key = "sk-123"')
    assert any("凭据" in r for r in validate_source(bad))


def test_reject_double_subclass():
    bad = (
        GOOD
        + "\n\n"
        + GOOD.replace("codex_good", "codex_good2").replace(
            "class CodexGoodAgent", "class CodexSecondAgent"
        )
    )
    reasons = validate_source(bad)
    assert any("恰好定义一个" in r for r in reasons)


def test_reject_init_defined():
    bad = GOOD.replace(
        "    def propose(self, context):",
        "    def __init__(self):\n"
        "        self.state = {}\n\n"
        "    def propose(self, context):",
    )
    assert any("__init__" in r for r in validate_source(bad))


def test_reject_missing_codex_prefix():
    bad = GOOD.replace('strategy_id = "codex_good"', 'strategy_id = "shadow_evil"')
    assert any("codex_" in r for r in validate_source(bad))


def test_reject_bad_stance():
    bad = GOOD.replace('stance = "neutral"', 'stance = "alpha"')
    assert any("stance" in r for r in validate_source(bad))


def test_reject_no_propose():
    bad = GOOD.replace(
        "    def propose(self, context):", "    def propose_x(self, context):"
    )
    assert any("propose" in r for r in validate_source(bad))


def test_reject_oversize_lines():
    big = GOOD + "\n" * 400
    assert any("行数" in r for r in validate_source(big))


def test_reject_syntax_error():
    assert any("语法错误" in r for r in validate_source("def broken(:\n"))


def test_allowlist_is_pure_computation_plus_host():
    assert "strategy_registry" in IMPORT_ALLOWLIST
    for banned in (
        "os",
        "sys",
        "socket",
        "subprocess",
        "requests",
        "urllib",
        "ctypes",
        "shutil",
        "pathlib",
    ):
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
    v = agent.propose(
        {"symbol": "600519", "closes": [10.0 + 0.01 * i for i in range(21)]}
    )
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
    # rejects 按日归档（§5 风险 6）：md 档落日期子目录
    assert (tmp_path / "rejects" / time.strftime("%Y%m%d") / "s-bad.md").exists()
    assert not (tmp_path / "inbox" / "s-bad").exists()
    assert reg.list_ids() == []


def test_process_submission_accept_moves_to_processed(tmp_path):
    reg = _fresh_registry()
    ok, reasons = process_submission(tmp_path, "s-ok", GOOD, reg)
    assert ok and not reasons
    assert (tmp_path / "processed" / "s-ok" / "strategy_s-ok.py").exists()
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
    loaded, skipped = load_registered(_fresh_registry(), bridge_dir=tmp_path / "nope")
    assert (loaded, skipped) == (0, 0)


def test_pilot_real_inbox_registers_into_default_registry():
    """M2 端到端：试点提交物注册进 default registry（真实 inbox 文件）。"""
    from shadow_strategies import build_default_registry

    reg = build_default_registry()
    loaded, _ = load_registered(reg)
    assert loaded >= 1
    assert "codex_sma20_rsi14" in reg.list_ids()


# ---- 军团-M3：子进程沙箱试跑 ----

LOOP = '''# -*- coding: utf-8 -*-
"""对抗样本：死循环（静态无懈可击，运行时永不停机）。"""
from strategy_registry import StrategyAgent, Verdict

class CodexLoopAgent(StrategyAgent):
    """单类合规插件。"""
    strategy_id = "codex_loop"
    stance = "neutral"

    def propose(self, context):
        while True:
            pass
        return Verdict(strategy_id=self.strategy_id, symbol="X",
                       recommendation="hold", confidence=0.0,
                       stance="neutral")
'''

CRASH = GOOD.replace(
    "        prev = fmean(closes[:-1])",
    '        raise ZeroDivisionError("对抗样本：运行时崩")\n'
    "        prev = fmean(closes[:-1])",
)

NONVERDICT = GOOD.replace(
    "        return Verdict(strategy_id=self.strategy_id, symbol=symbol,\n"
    "                       recommendation=rec,",
    "        return None  # 对抗样本：非 Verdict 返回\n"
    "        return Verdict(strategy_id=self.strategy_id, symbol=symbol,\n"
    "                       recommendation=rec,",
)

BADSTANCE = GOOD.replace(
    'recommendation=rec, confidence=0.5, stance="neutral",',
    'recommendation=rec, confidence=0.5, stance="yolo",',
)


def test_sandbox_child_env_scrubs_secrets(monkeypatch):
    """H6 第二层：子进程不继承宿主 API key/token；只保留启动必需变量。"""
    from codex_bridge import _sandbox_child_env

    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("FEISHU_APP_SECRET", "feishu-secret")
    env = _sandbox_child_env("engine", "platform", "repo")
    assert "OPENAI_API_KEY" not in env
    assert "FEISHU_APP_SECRET" not in env
    assert env["PYTHONPATH"].startswith("engine")
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_sandbox_trial_pilot_ok():
    from codex_bridge import sandbox_trial

    r = sandbox_trial(PILOT)
    assert r["ok"] is True and r["killed"] is False and r["reason"] == ""
    assert r.get("isolation") in ("job", "plain")
    assert r["elapsed_ms"] > 0
    v = r["verdict"]
    assert v["strategy_id"] == "codex_sma20_rsi14"
    assert v["recommendation"] in ("strong_buy", "buy", "hold", "sell", "strong_sell")
    assert 0.0 <= v["confidence"] <= 1.0 and v["stance"] in ("bull", "bear", "neutral")


def test_loop_passes_static_gate_but_killed_by_sandbox():
    """M3 核心判据②：静态过关的运行时对抗样本在沙箱门被击杀。

    WO-GATE-FLAKE-20261031：本用例曾在 fast gate 并行负载下偶发红
    （实测 r["elapsed_ms"]=2996 < 3000）。根因不在测试，而在沙箱超时的
    测量口径：subprocess.communicate 的截止钟是 time.monotonic（Windows =
    GetTickCount64，15.625ms 粒度），跨 tick 边界时真实等待可比标称
    timeout 短最多一个 tick；sandbox_trial 已改用 perf_counter 补齐到真正
    墙钟截止再击杀（见适配器同处注释），故下面的严格断言——killed=True、
    reason 含「超时」、elapsed 覆盖 timeout——现在确定性成立。
    不得把本用例登记进 known_failures/flaky 基线掩盖。
    """
    from codex_bridge import sandbox_trial

    assert validate_source(LOOP) == []  # 静态门放行（无任何可静态命中的违规）
    r = sandbox_trial(LOOP, timeout_s=3.0)
    assert r["ok"] is False and r["killed"] is True
    assert "超时" in r["reason"] and r["elapsed_ms"] >= 3000


def test_sandbox_rejects_runtime_crash():
    from codex_bridge import sandbox_trial

    r = sandbox_trial(CRASH)
    assert r["ok"] is False and r["killed"] is False
    assert "ZeroDivisionError" in r["reason"]


def test_sandbox_rejects_non_verdict_return():
    from codex_bridge import sandbox_trial

    r = sandbox_trial(NONVERDICT)
    assert r["ok"] is False
    assert "非 Verdict" in r["reason"]


def test_sandbox_rejects_bad_stance():
    from codex_bridge import sandbox_trial

    r = sandbox_trial(BADSTANCE)
    assert r["ok"] is False
    assert "stance" in r["reason"]


def test_host_side_verdict_dict_recheck():
    """宿主侧出口再验：runner 输出与契约脱钩的第二道网。"""
    from codex_bridge import _verdict_dict_issues

    good = {
        "strategy_id": "codex_x",
        "symbol": "S",
        "recommendation": "hold",
        "confidence": 0.5,
        "stance": "neutral",
    }
    assert _verdict_dict_issues(good) == []
    assert any(
        "前缀" in i for i in _verdict_dict_issues({**good, "strategy_id": "evil_x"})
    )
    assert any(
        "五档" in i for i in _verdict_dict_issues({**good, "recommendation": "yolo"})
    )
    assert any("越界" in i for i in _verdict_dict_issues({**good, "confidence": 1.5}))
    assert any(
        "非数值" in i
        for i in _verdict_dict_issues({**good, "confidence": "0.5; drop table"})
    )
    assert any("三档" in i for i in _verdict_dict_issues({**good, "stance": "yolo"}))
    assert any("symbol" in i for i in _verdict_dict_issues({**good, "symbol": ""}))


def test_process_submission_sandbox_rejects_runtime_crash(tmp_path):
    """M3 管线：试跑拒绝 → 拒档 stage=沙箱试跑 + inbox 移 rejects。"""
    ok, reasons = process_submission(tmp_path, "s-crash", CRASH, _fresh_registry())
    assert ok is False and reasons and "ZeroDivisionError" in reasons[0]
    rj = (tmp_path / "rejects" / time.strftime("%Y%m%d") / "s-crash.md").read_text(
        encoding="utf-8"
    )
    assert "沙箱试跑" in rj and "ZeroDivisionError" in rj
    assert not (tmp_path / "inbox" / "s-crash").exists()
    assert not (tmp_path / "processed" / "s-crash").exists()


def test_registration_row_records_sandbox_ms(tmp_path):
    """M3 判据③：单次试跑耗时基线随注册事件入档。"""
    data_root = tmp_path / "dataroot"
    ok, _ = process_submission(
        tmp_path, "s-ms", GOOD, _fresh_registry(), data_root=data_root
    )
    assert ok is True
    import json as _json

    rows = [
        _json.loads(l)
        for l in (data_root / "data" / "codex_bridge" / "registrations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    assert len(rows) == 1 and rows[0]["submit_id"] == "s-ms"
    assert isinstance(rows[0]["sandbox_ms"], int) and rows[0]["sandbox_ms"] > 0


def test_load_registered_skips_sandbox_trial(tmp_path, monkeypatch):
    """口径：processed/ 件已过门，幂等装载不起子进程（零额外延迟）。"""
    reg = _fresh_registry()
    process_submission(tmp_path, "s-nosb", GOOD, reg)
    reg2 = _fresh_registry()

    import codex_bridge

    def _boom(*a, **k):
        raise AssertionError("load_registered 不得触发沙箱试跑")

    monkeypatch.setattr(codex_bridge, "sandbox_trial", _boom)
    loaded, skipped = load_registered(reg2, bridge_dir=tmp_path)
    assert loaded == 1 and skipped == 0
    assert "codex_good" in reg2.list_ids()


# ---- 提交纪律：按日限频 + rejects 按日归档（设计稿 §5 风险 6） ----


def _variant(sid):
    """不同 strategy_id 的合规提交物（多提交受理测试防漂移冲突）。"""
    return GOOD.replace("codex_good", sid)


def test_daily_limit_rejects_and_archives(tmp_path):
    """限频触发：拒绝受理 + fail-visible 落档 stage=限频，inbox 不动。"""
    data_root = tmp_path / "dataroot"
    ok, _ = process_submission(
        tmp_path, "s-1", GOOD, _fresh_registry(), data_root=data_root, daily_limit=1
    )
    assert ok is True
    today = time.strftime("%Y%m%d")
    ok2, reasons2 = process_submission(
        tmp_path,
        "s-2",
        _variant("codex_two"),
        _fresh_registry(),
        data_root=data_root,
        daily_limit=1,
    )
    assert ok2 is False and any("限频" in r for r in reasons2)
    md = tmp_path / "rejects" / today / "s-2.md"
    assert md.exists() and "限频" in md.read_text(encoding="utf-8")
    assert not (tmp_path / "inbox" / "s-2").exists()  # inbox 不动
    assert not (tmp_path / "processed" / "s-2").exists()
    assert "codex_two" not in _fresh_registry().list_ids()  # 未注册


def test_daily_limit_boundary_exact_nth_accepted(tmp_path):
    """临界值：恰好第 N 个仍受理，第 N+1 个被拒。"""
    data_root = tmp_path / "dataroot"
    for i in (1, 2):
        ok, _ = process_submission(
            tmp_path,
            "s-b%d" % i,
            _variant("codex_b%d" % i),
            _fresh_registry(),
            data_root=data_root,
            daily_limit=2,
        )
        assert ok is True
    ok3, reasons3 = process_submission(
        tmp_path,
        "s-b3",
        _variant("codex_b3"),
        _fresh_registry(),
        data_root=data_root,
        daily_limit=2,
    )
    assert ok3 is False and any("限频" in r for r in reasons3)


def test_default_no_daily_limit_unlimited(tmp_path):
    """默认 daily_limit=None=不限频，向后兼容（现有调用方零变化）。"""
    for i in (1, 2, 3):
        ok, _ = process_submission(
            tmp_path, "s-u%d" % i, _variant("codex_u%d" % i), _fresh_registry()
        )
        assert ok is True
        assert (tmp_path / "processed" / ("s-u%d" % i)).exists()


def test_reject_moves_into_dated_archive(tmp_path):
    """rejects 按日归档：md 档与 inbox 移档均落日期子目录。"""
    bad = GOOD.replace("from statistics import fmean", "import os")
    ok, _ = process_submission(tmp_path, "s-date", bad, _fresh_registry())
    assert not ok
    today = time.strftime("%Y%m%d")
    assert (tmp_path / "rejects" / today / "s-date").exists()  # inbox 移入
    assert (tmp_path / "rejects" / today / "s-date.md").exists()


def test_daily_limit_counts_rejects_plus_registrations(tmp_path):
    """限频计数同日累计：registrations + rejects 合计口径。"""
    data_root = tmp_path / "dataroot"
    reg = _fresh_registry()
    bad = GOOD.replace("from statistics import fmean", "import os")
    ok, _ = process_submission(
        tmp_path, "s-r1", bad, reg, data_root=data_root, daily_limit=2
    )
    assert ok is False  # reject 计 1
    ok2, _ = process_submission(
        tmp_path, "s-r2", _variant("codex_r2"), reg, data_root=data_root, daily_limit=2
    )
    assert ok2 is True  # registration 计 1，合计 2
    ok3, reasons3 = process_submission(
        tmp_path,
        "s-r3",
        _variant("codex_r3"),
        _fresh_registry(),
        data_root=data_root,
        daily_limit=2,
    )
    assert ok3 is False and any("限频" in r for r in reasons3)  # 合计 2 ≥ 2


# ---- 旧单文件形态兼容读（aba47e39 前 rejects/<submit_id>.md 顶层散档） ----


def test_daily_limit_legacy_flat_reject_counted(tmp_path):
    """旧形态兼容读：顶层 rejects/<submit_id>.md（mtime=今日）计入
    当日限频口径，同日新旧形态无缝衔接。"""
    data_root = tmp_path / "dataroot"
    legacy = tmp_path / "rejects" / "legacy-sub.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# 旧形态拒绝档（aba47e39 前落法）\n", encoding="utf-8")
    ok, reasons = process_submission(
        tmp_path,
        "s-legacy",
        _variant("codex_legacy"),
        _fresh_registry(),
        data_root=data_root,
        daily_limit=1,
    )
    assert ok is False and any("限频" in r for r in reasons)
    md = tmp_path / "rejects" / time.strftime("%Y%m%d") / "s-legacy.md"
    assert md.exists() and "限频" in md.read_text(encoding="utf-8")
    assert not (tmp_path / "inbox" / "s-legacy").exists()  # inbox 不动
    assert not (tmp_path / "processed" / "s-legacy").exists()


def test_daily_limit_legacy_flat_stale_not_counted(tmp_path):
    """旧形态跨日失效：mtime 非今日的顶层旧档不永久占用限频额度。"""
    import os as _os

    data_root = tmp_path / "dataroot"
    legacy = tmp_path / "rejects" / "stale-sub.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# 昨日旧档\n", encoding="utf-8")
    stale = time.time() - 86400 * 2
    _os.utime(str(legacy), (stale, stale))
    ok, _ = process_submission(
        tmp_path,
        "s-stale",
        _variant("codex_stale"),
        _fresh_registry(),
        data_root=data_root,
        daily_limit=1,
    )
    assert ok is True  # 跨日旧档不计入，额度正常
    assert (tmp_path / "processed" / "s-stale").exists()
# ---- H6（K-ORG-20261101-H6SANDBOX）：属性链禁 + 主进程零 exec ----

_PROBE_ANCHOR = '        closes = [float(x) for x in (context.get("closes") or [])]'


def _with_probe(expr):
    """在 GOOD.propose 开头插入 `_probe = <expr>`（保守地构造绕过样本）。"""
    assert _PROBE_ANCHOR in GOOD
    return GOOD.replace(_PROBE_ANCHOR, "        _probe = %s\n" % expr + _PROBE_ANCHOR, 1)


def test_h6_rejects_dunder_attribute_chains():
    """审计 PoC 及同类反射链：AST 属性链禁命中即拒。"""
    payloads = {
        "__class__": "(1).__class__",
        "__mro__": "(1).__class__.__mro__[1]",
        "__subclasses__": "[].__class__.__base__.__subclasses__()",
        "__globals__": "propose.__globals__",
        "__code__": "(lambda: 0).__code__",
        "__dict__": "type.__dict__",
        "__builtins__": "__builtins__",
        "__bases__": "[].__class__.__bases__",
        "__getattribute__": "object.__getattribute__",
        "__reduce__": "(1).__reduce__",
    }
    for name, expr in payloads.items():
        bad = _with_probe(expr)
        reasons = validate_source(bad)
        assert reasons, name
        hit = [r for r in reasons if "禁用属性链" in r or "禁用 dunder" in r]
        assert hit, (name, reasons)


def test_h6_normal_attribute_usage_still_passes():
    """纵深防御不误伤正常属性访问（context.get/self.strategy_id/math.sqrt）。"""
    assert validate_source(PILOT) == []
    assert validate_source(GOOD) == []
    normal = GOOD.replace("from statistics import fmean",
                          "import math\nfrom statistics import fmean").replace(
        "TH = 0.01", "TH = math.sqrt(0.0001)")
    assert validate_source(normal) == []


def test_h6_register_returns_isolated_proxy():
    import codex_bridge as cb
    import inspect

    reg = _fresh_registry()
    agent = register_from_source(GOOD, reg)
    assert type(agent).__name__ == "_IsolatedPluginAgent"
    assert isinstance(agent, cb.StrategyAgent)
    assert agent.strategy_id == "codex_good" and agent.stance == "neutral"
    # 结构断言：注册函数不再 compile/exec 插件代码（主进程零执行）
    src = inspect.getsource(cb.register_from_source)
    assert "exec(" not in src and "compile(" not in src


def test_h6_main_process_does_not_execute_plugin_code():
    """运行时证明：插件顶层副作用只在子进程生效，主进程 math 不被污染。"""
    import math

    from strategy_registry import Verdict

    src = GOOD.replace(
        "from statistics import fmean",
        "import math\nfrom statistics import fmean\n\nmath.H6_PROBE = 1",
    )
    assert not hasattr(math, "H6_PROBE")
    reg = _fresh_registry()
    agent = register_from_source(src, reg)
    assert "codex_good" in reg.list_ids()
    assert not hasattr(math, "H6_PROBE"), "主进程执执行了插件代码（H6 回归）"
    v = agent.propose({"symbol": "S", "closes": [1.0, 2.0, 3.0]})
    assert isinstance(v, Verdict) and v.strategy_id == "codex_good"


def test_h6_register_fail_closed_on_runtime_crash():
    reg = _fresh_registry()
    with pytest.raises(StrategyError, match="隔离试跑失败"):
        register_from_source(CRASH, reg)
    assert reg.list_ids() == []


def test_h6_register_timeout_cleanup(monkeypatch):
    import codex_bridge as cb

    monkeypatch.setattr(cb, "SANDBOX_TIMEOUT_S", 1.0)
    t0 = time.time()
    with pytest.raises(StrategyError, match="超时"):
        cb.register_from_source(LOOP, _fresh_registry())
    assert time.time() - t0 < 15.0  # 超时被杀，不会挂住


def test_h6_proxy_propose_fail_closed(monkeypatch):
    import codex_bridge as cb

    reg = _fresh_registry()
    agent = register_from_source(GOOD, reg)
    monkeypatch.setattr(cb, "sandbox_trial", lambda *a, **k: {
        "ok": False, "killed": False, "reason": "boom",
        "elapsed_ms": 1, "verdict": None,
    })
    with pytest.raises(StrategyError, match="隔离试跑失败"):
        agent.propose({"symbol": "S", "closes": [1.0, 2.0]})


def test_h6_ast_ban_documented_as_defense_in_depth():
    import codex_bridge as cb

    doc = cb.__doc__ or ""
    assert "纵深防御" in doc and "不是安全边界" in doc
    assert "Job Object" in doc  # 架构建议：OS 级隔离待独立单