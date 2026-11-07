"""keepalive_9899 全局预算契约（2026-11-07 K-ORG-20261107-WATCHDOG）。

背景：8099 守护任务执行上限 PT2M。"四端口各自算宽限"的形态下，多口同时死
最坏 ≈103s 贴上限；一旦被强杀，Job 连坐带走刚 spawn 的子进程（K-PUSH-004
判例）→ 现象是"端口被拉起又被杀"，日志里看不出原因（静默失效）。

本测锁三条不变量（假时钟推进，不真睡、不真 spawn）：
1) 任一守卫的宽限循环都不能让整轮超出 BUDGET_S；
2) 预算不足时**不 spawn**——宁缺一跳，也不造一个随即被任务强杀的孤儿；
3) main() 在入口起算截止时刻，且四口顺序固定 picks→km→push→cow。

配套：guard_cow 曾用 sys.exit() 收尾（独立进程时代的写法），并入 main() 后会
横穿调用栈提前终止进程 → 断言源码无 sys.exit，且函数以返回值收尾。
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(REPO, "ops", "keepalive_9899.py")


def _load():
    spec = importlib.util.spec_from_file_location("keepalive_budget_t", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Clock:
    """替身 time：sleep 只推进虚拟时刻，测试零等待。"""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Sub:
    """替身 subprocess：记录 Popen 调用，不产生真实进程。"""

    DEVNULL = -3
    STDOUT = -2

    def __init__(self):
        self.calls = []

    def Popen(self, *a, **k):  # noqa: N802
        self.calls.append((a, k))
        return object()


def _prep(monkeypatch, dead=True):
    mod = _load()
    clock = _Clock()
    sub = _Sub()
    logs = []
    monkeypatch.setattr(mod, "time", clock)
    monkeypatch.setattr(mod, "alive_on", lambda port: not dead)
    monkeypatch.setattr(mod, "alive", lambda: not dead)
    monkeypatch.setattr(mod, "subprocess", sub)
    monkeypatch.setattr(mod, "log", logs.append)
    return mod, clock, sub, logs


def test_grace_loop_cannot_exceed_budget(monkeypatch, tmp_path):
    """宽限再多也不能把一轮拖过 BUDGET_S（cow 形态：40 次 × 3s = 120s）。"""
    mod, clock, sub, logs = _prep(monkeypatch)
    mod._DEADLINE = clock.t + mod.BUDGET_S
    t0 = clock.t
    mod.guard_port(65000, ["x.py", "--bg"], str(tmp_path / "nul.log"), 40, "t")
    elapsed = clock.t - t0
    assert sub.calls, "应拉起一次（死亡端口）"
    assert elapsed <= mod.BUDGET_S, "宽限循环超出全局预算：%.1fs" % elapsed
    assert any("REVIVE_UNCONFIRMED" in s for s in logs), logs


def test_short_grace_still_reports_fail(monkeypatch, tmp_path):
    """宽限够短时不触发 UNCONFIRMED，仍走原有 REVIVE_FAIL 判据（不弱化原语义）。"""
    mod, clock, sub, logs = _prep(monkeypatch)
    mod._DEADLINE = clock.t + mod.BUDGET_S
    mod.guard_port(65000, ["x.py", "--bg"], str(tmp_path / "nul.log"), 5, "t")
    assert any("REVIVE_FAIL" in s for s in logs), logs
    assert not any("UNCONFIRMED" in s for s in logs), logs


def test_low_budget_skips_without_spawn(monkeypatch, tmp_path):
    """预算不足以完成 x2 探针 → SKIP，且**一次都不 spawn、一秒都不睡**。"""
    mod, clock, sub, logs = _prep(monkeypatch)
    mod._DEADLINE = clock.t + 5  # <= 8
    t0 = clock.t
    mod.guard_port(65000, ["x.py", "--bg"], str(tmp_path / "nul.log"), 5, "t")
    assert not sub.calls, "预算不足却仍 spawn 了（会造出随即被强杀的孤儿）"
    assert clock.t == t0, "SKIP 分支不应有 sleep"
    assert any("SKIP" in s for s in logs), logs


def test_main_sets_deadline_and_guard_order(monkeypatch):
    """main() 入口起算截止时刻，四口顺序固定。"""
    mod, clock, _sub, _logs = _prep(monkeypatch)
    order = []
    for name in ("guard_picks_sched", "guard_km_hub", "guard_push_admin", "guard_cow"):
        monkeypatch.setattr(mod, name, (lambda n: lambda: order.append(n))(name))
    assert mod._DEADLINE is None, "模块导入时不应有截止时刻（单函数直调保持旧语义）"
    assert mod.main() == 0
    assert order == ["guard_picks_sched", "guard_km_hub", "guard_push_admin", "guard_cow"]
    assert mod._DEADLINE == clock.t + mod.BUDGET_S


def test_main_all_ports_dead_stays_within_budget(monkeypatch):
    """端到端：四口全死（最坏形态）整轮也不得超出 BUDGET_S。

    没有预算时这轮是 4×(3+5×3)=72s(picks/km/push) + cow 60s ≈ 132s，
    直接顶爆 PT2M —— 正是本契约要防的形态。
    """
    mod, clock, sub, _logs = _prep(monkeypatch)
    mod._DEADLINE = None
    t0 = clock.t
    assert mod.main() == 0
    elapsed = clock.t - t0
    assert elapsed <= mod.BUDGET_S, "四口全死超出预算：%.1fs" % elapsed
    assert len(sub.calls) == 4, "四口都应尝试拉起，实为 %d" % len(sub.calls)
    assert mod._DEADLINE == t0 + mod.BUDGET_S


def test_guard_cow_returns_instead_of_exiting(monkeypatch):
    """cow 活着时时返回 True，不抛 SystemExit（并入 main 后可安全续跑）。"""
    mod, clock, _sub, _logs = _prep(monkeypatch, dead=False)
    assert mod.guard_cow() is True
    assert mod._DEADLINE is None


def test_no_sys_exit_in_guards():
    """源码级：守卫函数不得再用 sys.exit 收尾（会横穿 main 的调用栈）。
    用 AST 找 sys.exit 的**调用**节点——注释/docstring 里提到它不算违规。"""
    import ast

    tree = ast.parse(open(TOOL, encoding="utf-8").read())
    hits = [
        (n.lineno, n.func.attr)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "exit"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "sys"
    ]
    assert hits == [], "仍有 sys.exit 调用：%r" % (hits,)
