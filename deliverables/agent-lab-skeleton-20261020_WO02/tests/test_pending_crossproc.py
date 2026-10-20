"""工位CB2测试：pending_reports 队列的「单发送者租约」跨进程/跨实例并发语义。

守卫的是 S12 的核心命题——**同一条待发记录任一时刻最多被一个发送者发送**，
同时不得以「不双投」换「丢数据」。覆盖：
- 无队列文件时零副作用提前返回（返回值精确相等、不建租约/锁/目录）；
- 返回值键集严格为 {"sent", "remaining"}（防后来者加键打断既有精确断言）；
- 双实例对同一队列并发重发：每条恰好发一次（Barrier 同步，不靠 sleep）；
- 他人活跃租约在途：本轮空转、零 HTTP、不碰他人租约；
- 租约持有者正在发送时，另一实例必定让路（确定性阻塞，非竞态碰运气）；
- 崩溃遗留的超期租约：可夺权回收并继续发送，记录不丢；
- 超期回收必留 WARNING 痕迹；
- 发送阶段他方入队不丢：残留 = 失败数 + 追加数，内容逐条精确保留；
- 全部成功：队列文件删除、无 .claim/.tmp 残留（.lock 为长驻锚点，见用例注释）；
- 全部失败：队列字节级原样保留、可被下次重发；
- 同进程双实例指向同一 pending_dir 并发，不触发 file_lock 重入 RuntimeError；
- 发送途中租约易主：立即停手，剩余条目留给接管者，且不删他人租约。

全部为模拟数据；零真实网络（monkeypatch httpx.post，禁止起 HTTP 服务）；
落盘一律进 tmp_path，不写骨架内真实 data/。
"""
from __future__ import annotations

import itertools
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import roles.base_agent as base_agent
from roles.base_agent import (
    PENDING_CLAIM_SUFFIX,
    PENDING_CLAIM_TTL_S,
    PENDING_QUEUE_FILENAME,
    PENDING_REPLACE_RETRIES,
    BaseAgent,
    Report,
)

_PORTS = itertools.count(9210)


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """每个用例前清空 prometheus 默认注册表。

    同进程重复实例化席位会向默认注册表注册同名指标（Duplicated timeseries）；
    本文件大量构造实例，必须逐用例隔离。
    """
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


# ---------------- 夹具与工具（模拟数据） ----------------

def make_agent(tmp_path: Path, name: str = "cb2") -> BaseAgent:
    """最小 BaseAgent 实例（模拟席位），队列目录落在 tmp_path。"""
    agent = BaseAgent(name=name, role="tester", report_to="platform",
                      bus_url="http://bus.test", metrics_port=next(_PORTS))
    agent.pending_dir = str(tmp_path / "pending")
    agent.sleep_fn = lambda seconds: None  # 重试退避不真睡
    return agent


def queue_path(agent: BaseAgent) -> Path:
    return Path(agent.pending_dir) / PENDING_QUEUE_FILENAME


def claim_path(agent: BaseAgent) -> Path:
    return Path(str(queue_path(agent)) + PENDING_CLAIM_SUFFIX)


def lock_path(agent: BaseAgent) -> Path:
    return Path(str(queue_path(agent)) + ".lock")


def seed_queue(agent: BaseAgent, reports: list) -> Path:
    qpath = queue_path(agent)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(
        "".join(json.dumps({"report": r.__dict__}, ensure_ascii=False) + "\n"
                for r in reports),
        encoding="utf-8")
    return qpath


def reports(*subjects: str) -> list:
    return [Report(level="agent", source="cb2", subject=s, body="b（模拟）")
            for s in subjects]


def read_subjects(agent: BaseAgent) -> list:
    qpath = queue_path(agent)
    if not qpath.exists():
        return []
    return [json.loads(ln)["report"]["subject"]
            for ln in qpath.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def write_claim(agent: BaseAgent, token: str) -> Path:
    """手工伪造他方租约（模拟另一个仍存活的发送者已认领）。"""
    claim = claim_path(agent)
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(json.dumps({"token": token, "pid": os.getpid(),
                                 "ts": time.time()}), encoding="utf-8")
    return claim


class PostSpy:
    """httpx.post 替身：线程安全计数，成功/失败可按主题控制。"""

    def __init__(self, fail_subjects=()):
        self.seen: list = []
        self.fail_subjects = set(fail_subjects)
        self._guard = threading.Lock()

    def __call__(self, url, **kwargs):
        subject = kwargs["json"]["report"]["subject"]
        with self._guard:
            self.seen.append(subject)
        if subject in self.fail_subjects:
            raise httpx.ConnectError("总线不可达（模拟）")
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    def count(self) -> int:
        with self._guard:
            return len(self.seen)

    def snapshot(self) -> list:
        with self._guard:
            return list(self.seen)


def patch_post(monkeypatch, spy) -> None:
    monkeypatch.setattr(base_agent.httpx, "post", spy)


def dir_entries(agent: BaseAgent) -> list:
    directory = Path(agent.pending_dir)
    return sorted(p.name for p in directory.iterdir())


# ---------------- 1) 无队列文件：零副作用提前返回 ----------------

def test_missing_queue_returns_exact_zero_without_side_effects(
        tmp_path, monkeypatch):
    """队列文件不存在：返回精确 {"sent":0,"remaining":0}，且不建 pending
    目录、不建 .claim、不建 .lock、不发任何 HTTP。

    这条同时是 `send_pending` 开头 path.exists() 提前返回的回归守卫：
    若有人把该判断挪到认领之后，这里会立刻出现 .claim/.lock 与目录。
    """
    agent = make_agent(tmp_path)
    spy = PostSpy()
    patch_post(monkeypatch, spy)

    result = agent.send_pending()

    assert result == {"sent": 0, "remaining": 0}
    assert type(result) is dict
    assert spy.count() == 0
    assert not Path(agent.pending_dir).exists()   # 连目录都不该被创建
    assert not claim_path(agent).exists()
    assert not lock_path(agent).exists()
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 2) 返回值键集严格不变 ----------------

def test_return_shape_is_exactly_sent_and_remaining(tmp_path, monkeypatch):
    """三条路径（正常 / 他人持租约 / 无文件）返回值键集均恰为两键且值为 int。

    既有多处守卫按整字典相等断言，新增任何键都会打断它们；新增可观测字段
    只能挂 last_pending_stats。
    """
    agent = make_agent(tmp_path)
    spy = PostSpy()
    patch_post(monkeypatch, spy)

    assert set(agent.send_pending()) == {"sent", "remaining"}  # 无文件路径

    seed_queue(agent, reports("a", "b"))
    result = agent.send_pending()
    assert set(result) == {"sent", "remaining"}                 # 正常路径
    assert all(type(v) is int for v in result.values())

    seed_queue(agent, reports("c"))                             # 他人持租约路径
    write_claim(agent, "someone-else")
    busy = agent.send_pending()
    assert set(busy) == {"sent", "remaining"}
    assert all(type(v) is int for v in busy.values())


# ---------------- 3) 确定性：同一条只被发一次 ----------------

def test_two_instances_send_each_record_exactly_once(
        tmp_path, monkeypatch):
    """双实例、双线程对同一队列并发 send_pending：每条恰好发送一次。

    确定性手段（全程无线程调度碰运气）：
    1) threading.Barrier(2) 让两个发送者真正同时起跑；
    2) O_CREAT|O_EXCL 只有一个赢家，赢家在第一发处被 Event 卡住（租约确定
       在途且队列确定完好），此时输家必然已返回——用「有人已返回」事件
       等到这一刻再放行，因此输家的读数与赢家的清理绝不重叠；
    3) 结论不依赖谁先抢到租约：输家要么看到活跃租约立即空转，要么在赢家
       清空队列后才起跑（届时队列文件已不存在，零副作用返回）。
    故 HTTP 计数恒等于队列条数，且 sent 之和恒等于条数。
    """
    agent_a = make_agent(tmp_path, "cb2-a")
    agent_b = make_agent(tmp_path, "cb2-b")
    seed_queue(agent_a, reports("r1", "r2", "r3", "r4"))

    spy = PostSpy()
    first_post = threading.Event()
    release = threading.Event()
    returned = threading.Event()

    def blocked_post(url, **kwargs):
        first_post.set()              # 赢家已进入网络阶段（租约在途）
        assert release.wait(10)       # 卡住，直到输家确认已返回
        return spy(url, **kwargs)

    patch_post(monkeypatch, blocked_post)
    barrier = threading.Barrier(2, timeout=10)
    results: list = []
    errors: list = []
    results_guard = threading.Lock()

    def worker(agent: BaseAgent) -> None:
        try:
            barrier.wait()          # 双方均就绪才同时进入
            out = agent.send_pending()
            with results_guard:
                results.append(out)
            returned.set()          # 输家（未持租约者）在此刻已确定返回
        except Exception as exc:    # pragma: no cover - 触发即失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(a,))
               for a in (agent_a, agent_b)]
    for t in threads:
        t.start()
    try:
        assert first_post.wait(10)
        assert returned.wait(10)    # 非持租约方已返回，双发窗口被排除
    finally:
        release.set()
    for t in threads:
        t.join(20)

    assert errors == []
    with results_guard:
        assert len(results) == 2
        # 每条恰好一次：计数等于条数（不是两倍），且四条各出现一次
        assert spy.count() == 4
        assert sorted(spy.snapshot()) == ["r1", "r2", "r3", "r4"]
        # 只有持租约者发送：sent 集合恰为 {0, 4}，空转方 remaining 为队列全量
        assert sorted(r["sent"] for r in results) == [0, 4]
        assert sorted(r["remaining"] for r in results) == [0, 4]
    assert not queue_path(agent_a).exists()
    assert not claim_path(agent_a).exists()


# ---------------- 4) 他人活跃租约：本轮空转，零 HTTP ----------------

def test_live_foreign_claim_skips_without_http(tmp_path, monkeypatch):
    """他方持有未过期租约：本轮不发（宁可空转，不可双投），且不碰他人租约。

    断言：sent=0、remaining=条数、零 HTTP、队列原样、租约内容原样（未被
    夺权/未被误删）、连 .lock 都不创建。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("x1", "x2"))
    claim = write_claim(agent, "foreign-token")
    before = claim.read_text(encoding="utf-8")

    spy = PostSpy()
    patch_post(monkeypatch, spy)

    result = agent.send_pending()

    assert result == {"sent": 0, "remaining": 2}
    assert spy.count() == 0                       # 零 HTTP
    assert read_subjects(agent) == ["x1", "x2"]   # 队列未动
    assert claim.exists()
    assert claim.read_text(encoding="utf-8") == before   # 他人租约未被改写
    assert json.loads(before)["token"] == "foreign-token"
    assert not lock_path(agent).exists()          # 空转路径不碰边车锁
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 5) 持有者在途：另一实例必定让路（确定性） ----------------

def test_second_instance_yields_while_first_is_in_flight(
        tmp_path, monkeypatch):
    """A 在 HTTP 阶段被阻塞（租约确定在途），此时 B 的 send_pending 必须
    立即空转并返回 sent=0，零 HTTP。全程无 sleep：用 Event 制造确定的时序。
    """
    agent_a = make_agent(tmp_path, "cb2-a")
    agent_b = make_agent(tmp_path, "cb2-b")
    seed_queue(agent_a, reports("first", "second"))

    in_flight = threading.Event()
    release = threading.Event()
    spy = PostSpy()
    real_call = spy

    def holding_post(url, **kwargs):
        # 仅第一条卡住：模拟「网络 RTT 很长」的持有者。B 若违规发车，其调用
        # 会直接落到 real_call 被计数，而不是一起卡死（断言更干净）。
        if kwargs["json"]["report"]["subject"] == "first":
            in_flight.set()
            assert release.wait(10)
        return real_call(url, **kwargs)

    patch_post(monkeypatch, holding_post)
    errors: list = []
    holder_result: list = []

    def holder() -> None:
        try:
            holder_result.append(agent_a.send_pending())
        except Exception as exc:   # pragma: no cover - 触发即失败
            errors.append(exc)

    t = threading.Thread(target=holder)
    t.start()
    try:
        assert in_flight.wait(10)          # A 已持租约且正卡在发送中
        b_result = agent_b.send_pending()  # B 此刻进入，必然看到活跃租约
        assert b_result == {"sent": 0, "remaining": 2}
        assert spy.count() == 0            # B 一条都没发
        assert claim_path(agent_a).exists()  # A 的租约仍在
    finally:
        release.set()
        t.join(20)

    assert errors == []
    assert holder_result == [{"sent": 2, "remaining": 0}]
    assert spy.count() == 2
    assert not queue_path(agent_a).exists()


# ---------------- 6) 崩溃遗留超期租约：夺权回收、不丢 ----------------

def test_stale_claim_reclaimed_after_ttl(tmp_path, monkeypatch, caplog):
    """持有者被强杀：租约残留且 mtime 超过 TTL。下一次 send_pending 必须
    夺权回收（reclaimed>=1）并把记录全部发出，一条不丢，并留 WARNING 痕迹。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("c1", "c2", "c3"))
    claim = write_claim(agent, "killed-holder")
    old = time.time() - (PENDING_CLAIM_TTL_S + 60)
    os.utime(claim, (old, old))        # 模拟「早已停止续租」

    spy = PostSpy()
    patch_post(monkeypatch, spy)

    with caplog.at_level("WARNING", logger="os-legion"):
        result = agent.send_pending()

    assert result == {"sent": 3, "remaining": 0}     # 不丢
    assert agent.last_pending_stats["reclaimed"] >= 1  # 夺权发生
    assert agent.last_pending_stats["sent"] == 3
    assert sorted(spy.snapshot()) == ["c1", "c2", "c3"]  # 每条一次
    assert spy.count() == 3
    assert not queue_path(agent).exists()
    assert not claim_path(agent).exists()            # 本进程已释放
    # 夺权必留痕（否则「疑似双投」无从追查）
    assert any("超期回收" in r.getMessage() for r in caplog.records)
    # 回收过程不留 .stale 中转文件
    leftovers = [n for n in dir_entries(agent) if ".stale." in n]
    assert leftovers == []


def test_unexpired_claim_is_not_reclaimed(tmp_path, monkeypatch, caplog):
    """TTL 边界：刚创建（未超期）的租约不得被夺权——宁可空转一整轮。

    这是 TTL 语义的下界守卫：把 TTL 判据写成「存在即回收」会在此失败。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("n1"))
    claim = write_claim(agent, "live-holder")
    os.utime(claim, None)  # mtime = now

    spy = PostSpy()
    patch_post(monkeypatch, spy)

    with caplog.at_level("WARNING", logger="os-legion"):
        result = agent.send_pending()

    assert result == {"sent": 0, "remaining": 1}
    assert spy.count() == 0
    assert agent.last_pending_stats["reclaimed"] == 0
    assert not any("超期回收" in r.getMessage() for r in caplog.records)
    assert claim.exists()


# ---------------- 7) 发送阶段并发入队不丢 ----------------

def test_enqueue_during_send_preserved(tmp_path, monkeypatch):
    """发送在途时他方追加：最终残留 = 发送失败数 + 追加数，追加内容逐条
    精确保留且保序。用 Event 保证追加确定落在网络阶段内（不靠 sleep）。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("ok", "fail"))
    other = make_agent(tmp_path, "cb2-writer")   # 同目录的另一实例

    appended = threading.Event()

    def append_two() -> None:
        other._enqueue_pending(Report(level="agent", source="cb2-writer",
                                      subject="new1", body="b（模拟）"))
        other._enqueue_pending(Report(level="agent", source="cb2-writer",
                                      subject="new2", body="b（模拟）"))
        appended.set()

    spy = PostSpy(fail_subjects={"fail"})

    def post(url, **kwargs):
        if kwargs["json"]["report"]["subject"] == "ok":
            append_two()          # 确定落在「快照已取、写回未做」的窗口内
            assert appended.is_set()
        return spy(url, **kwargs)

    patch_post(monkeypatch, post)

    result = agent.send_pending()

    assert result == {"sent": 1, "remaining": 3}   # 1 失败 + 2 追加
    assert read_subjects(agent) == ["fail", "new1", "new2"]  # 保序且逐条完整
    assert spy.snapshot() == ["ok", "fail"]
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 8) 全部成功：队列删除、无租约/临时文件残留 ----------------

def test_all_success_removes_queue_and_claim_tmp_residue(
        tmp_path, monkeypatch):
    """全部发送成功：队列文件删除，目录内无 .claim、无 .tmp.*。

    注：`pending_reports.jsonl.lock` 会保留——它是 file_lock 的长驻锚点
    （0 字节空文件），删掉会凭空放开第二个跨进程写者，属刻意保留而非残留；
    故此处断言它存在且为空，并把目录条目锁死为仅此一项。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("s1", "s2"))
    spy = PostSpy()
    patch_post(monkeypatch, spy)

    result = agent.send_pending()

    assert result == {"sent": 2, "remaining": 0}
    assert not queue_path(agent).exists()
    assert not claim_path(agent).exists()
    assert not [n for n in dir_entries(agent) if ".tmp." in n]
    assert dir_entries(agent) == [PENDING_QUEUE_FILENAME + ".lock"]
    assert lock_path(agent).stat().st_size == 0
    assert agent.last_pending_stats["released"] is True
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 9) 全部失败：条目原样保留、可重发 ----------------

def test_all_fail_keeps_queue_and_retry_succeeds(tmp_path, monkeypatch):
    """HTTP 全失败：条目原样保留（字节级），下一轮可完整重发。"""
    agent = make_agent(tmp_path)
    qpath = seed_queue(agent, reports("d1", "d2"))
    original = qpath.read_text(encoding="utf-8")

    patch_post(monkeypatch, PostSpy(fail_subjects={"d1", "d2"}))
    assert agent.send_pending() == {"sent": 0, "remaining": 2}
    assert qpath.read_text(encoding="utf-8") == original   # 逐字节不变
    assert not claim_path(agent).exists()                  # 失败也释放租约

    spy = PostSpy()
    patch_post(monkeypatch, spy)
    assert agent.send_pending() == {"sent": 2, "remaining": 0}
    assert sorted(spy.snapshot()) == ["d1", "d2"]
    assert not qpath.exists()


# ---------------- 10) 同进程双实例并发：不触发 file_lock 重入 ----------------

def test_two_instances_same_dir_no_reentry_runtimeerror(
        tmp_path, monkeypatch):
    """同进程两实例指向同一 pending_dir，并发入队 + 重发：
    不得出现 RuntimeError: file_lock 不支持同进程重入，且零丢失零重复。

    竞态面：实例 B 的追加（分片锁 → file_lock）与实例 A 的阶段四重写
    （分片锁 → file_lock）同时发生——没有按路径分片的进程内 RLock 收敛，
    file_lock 的不分线程重入登记表就会响亮抛错。
    发送侧只留单线程（交替使用两个实例），避免「空转方读队列 / 持租约方
    os.replace 队列」这种 Windows 文件共享冲突干扰本用例的判据。
    """
    agent_a = make_agent(tmp_path, "cb2-a")
    agent_b = make_agent(tmp_path, "cb2-b")
    spy = PostSpy()
    patch_post(monkeypatch, spy)
    # 先备好一条，保证发送线程一开始就有活干（不必碰运气等待）
    seed_queue(agent_a, reports("seed"))

    barrier = threading.Barrier(3, timeout=20)
    errors: list = []

    def writer(agent: BaseAgent, tag: str) -> None:
        try:
            barrier.wait()
            for i in range(10):
                agent._enqueue_pending(Report(
                    level="agent", source=tag, subject=f"{tag}-{i}",
                    body="b（模拟）"))
        except Exception as exc:   # pragma: no cover - 触发即失败
            errors.append(exc)

    def sender() -> None:
        try:
            barrier.wait()
            for i in range(30):
                # 交替实例：跨实例复用同一把 file_lock / 分片锁
                (agent_a if i % 2 == 0 else agent_b).send_pending()
        except Exception as exc:   # pragma: no cover - 触发即失败
            errors.append(exc)

    expected = [f"{tag}-{i}" for tag in ("wa", "wb") for i in range(10)]
    threads = [threading.Thread(target=writer, args=(agent_a, "wa")),
               threading.Thread(target=writer, args=(agent_b, "wb")),
               threading.Thread(target=sender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert errors == []                       # 无 RuntimeError 重入
    # 有界排空（不 sleep、不无限重试）
    for _ in range(50):
        if not queue_path(agent_a).exists():
            break
        if agent_a.send_pending()["remaining"] == 0:
            break
    assert not queue_path(agent_a).exists()
    assert sorted(spy.snapshot()) == sorted(["seed"] + expected)  # 每条一次
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 11) 租约易主：立即停手并把剩余留给接管者 ----------------

def test_claim_takeover_stops_further_sending(tmp_path, monkeypatch):
    """发送第一条后租约被他人改写（模拟超期被夺权）：本进程必须立即停手，
    剩余条目不得继续发送，队列保留剩余供接管者处理；且不得删他人租约。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("t1", "t2", "t3"))

    spy = PostSpy()

    def stealing_post(url, **kwargs):
        # 第一条发出后立刻改写租约 token：等价于「回收已把租约移交他人」
        claim_path(agent).write_text(
            json.dumps({"token": "new-owner", "pid": os.getpid(),
                        "ts": time.time()}), encoding="utf-8")
        return spy(url, **kwargs)

    patch_post(monkeypatch, stealing_post)

    result = agent.send_pending()

    assert result == {"sent": 1, "remaining": 2}   # 只发了第一条
    assert spy.snapshot() == ["t1"]               # 后两条未被发送
    assert read_subjects(agent) == ["t2", "t3"]   # 剩余留给接管者（保序）
    assert claim_path(agent).exists()             # 他人租约未被删
    assert json.loads(claim_path(agent).read_text(
        encoding="utf-8"))["token"] == "new-owner"
    assert agent.last_pending_stats["released"] is False  # 未释放他人租约
    assert base_agent._HELD_LOCK_PATHS == {}


# ---------------- 12) os.replace 撞上读句柄：有限重试后照常成功 ----------------

def test_replace_permission_error_retried_then_succeeds(
        tmp_path, monkeypatch):
    """Windows 语义：os.replace 目标被只读句柄占用时抛 [WinError 5]。

    首次注入 PermissionError、第二次放行 → 本轮发送照常完成，返回值正常，
    重试次数进 last_pending_stats["write_retries"]（观测得到、可追查）。

    注：必须留下一条失败记录，阶段四才会真正重写队列（全发成功走的是
    删文件分支，不经过 os.replace）。
    """
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("w1", "w2"))
    spy = PostSpy(fail_subjects={"w2"})
    patch_post(monkeypatch, spy)

    real_replace = base_agent.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *args, **kwargs):
        if Path(dst).name != PENDING_QUEUE_FILENAME:
            return real_replace(src, dst, *args, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "拒绝访问（模拟 WinError 5）")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(base_agent.os, "replace", flaky_replace)

    result = agent.send_pending()

    assert result == {"sent": 1, "remaining": 1}   # 重试成功，本轮照常收口
    assert calls["n"] == 2                     # 首次失败 + 重试成功
    assert agent.last_pending_stats["write_retries"] == 1
    assert spy.count() == 2
    assert read_subjects(agent) == ["w2"]       # 失败条目仍在
    assert not [n for n in dir_entries(agent) if ".tmp." in n]


# ---------------- 13) 重试耗尽：抛异常、不半截、租约释放 ----------------

def test_replace_permission_error_exhausted_raises_and_keeps_queue(
        tmp_path, monkeypatch):
    """连续 PermissionError 超过上限：抛异常而非静默吞；队列逐行可 json.load
    （未被写坏）；租约由 finally 释放（下一轮能正常重试）。"""
    agent = make_agent(tmp_path)
    qpath = seed_queue(agent, reports("p1", "p2"))
    original = qpath.read_text(encoding="utf-8")
    # 全失败 → 阶段四残留非空 → 必然走到 os.replace
    patch_post(monkeypatch, PostSpy(fail_subjects={"p1", "p2"}))

    calls = {"n": 0}
    real_replace = base_agent.os.replace

    def always_denied(src, dst, *args, **kwargs):
        if Path(dst).name != PENDING_QUEUE_FILENAME:
            return real_replace(src, dst, *args, **kwargs)
        calls["n"] += 1
        raise PermissionError(13, "拒绝访问（模拟 WinError 5）")

    monkeypatch.setattr(base_agent.os, "replace", always_denied)

    with pytest.raises(PermissionError):
        agent.send_pending()

    assert calls["n"] == 1 + PENDING_REPLACE_RETRIES   # 有限，不是无限重试
    # 队列未被半截写坏：字节级不变且逐行可解析
    text = qpath.read_text(encoding="utf-8")
    assert text == original
    assert all(isinstance(json.loads(ln), dict)
               for ln in text.splitlines() if ln.strip())
    # 租约已由 finally 释放，下一轮可重发（失败即拒绝但不僵死）
    assert not claim_path(agent).exists()
    assert agent.last_pending_stats["released"] is True
    assert not [n for n in dir_entries(agent) if ".tmp." in n]

    patch_post(monkeypatch, PostSpy())          # 故障消失
    assert agent.send_pending() == {"sent": 2, "remaining": 0}


# ---------------- 14) 积压量读不到：显式 -1，不撒谎成 0 ----------------

def test_line_count_read_failure_returns_minus_one(
        tmp_path, monkeypatch):
    """让路分支读队列失败（读取竞态）：remaining 必须是 -1（不可判定），
    不能是 0（会被误读成「队列空了」）；且不删改他人租约、不发 HTTP。"""
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("q1", "q2"))
    claim = write_claim(agent, "foreign-token")
    before = claim.read_text(encoding="utf-8")

    real_read = Path.read_text

    def racy_read(self, *args, **kwargs):
        if self.name == PENDING_QUEUE_FILENAME:
            raise PermissionError(13, "拒绝访问（模拟读取竞态）")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(base_agent.Path, "read_text", racy_read)
    spy = PostSpy()
    patch_post(monkeypatch, spy)

    result = agent.send_pending()

    assert result == {"sent": 0, "remaining": -1}   # 显式不可判定
    assert result["remaining"] != 0                 # 不得谎报为空队列
    assert spy.count() == 0
    assert claim.exists()
    assert claim.read_text(encoding="utf-8") == before
    assert agent.last_pending_stats["skipped_other_holder"] is True


# ---------------- 15) 让路分支必须刷新统计（不留上一轮残值） ----------------

def test_yield_branch_refreshes_last_pending_stats(tmp_path, monkeypatch):
    """先跑一轮真发送（stats 有值），再让他人持租约跑第二轮：stats 必须是
    本轮的（sent=0、claimed=0、skipped_other_holder=True），不能残留上一轮。"""
    agent = make_agent(tmp_path)
    seed_queue(agent, reports("y1"))
    patch_post(monkeypatch, PostSpy())

    assert agent.send_pending() == {"sent": 1, "remaining": 0}
    assert agent.last_pending_stats["sent"] == 1          # 第一轮确有值
    assert agent.last_pending_stats["skipped_other_holder"] is False

    seed_queue(agent, reports("y2", "y3"))
    write_claim(agent, "other-holder")
    result = agent.send_pending()

    assert result == {"sent": 0, "remaining": 2}
    stats = agent.last_pending_stats
    assert stats["sent"] == 0                             # 已刷新，未残留 1
    assert stats["claimed"] == 0
    assert stats["reclaimed"] == 0
    assert stats["released"] is False
    assert stats["skipped_other_holder"] is True          # 空转可区分
    assert stats["write_retries"] == 0


# ---------------- 16) 修复回归：多发送者压测不再出现 WinError 5 ----------------

def test_multi_instance_multi_sender_no_permission_error(
        tmp_path, monkeypatch):
    """缺陷固化用例：同进程 2 实例 × 3 发送线程 + 2 写线程并发。

    修复前（让路方在锁外 read_text 读队列 + os.replace 无重试）本场景会
    抛 PermissionError [WinError 5] 并中断发送轮次；修复后读路径进分片锁，
    同进程内读/替换不再交叠，另加写侧有限重试兜跨进程窗口。
    同时断言零丢失零重复（HTTP 调用总数 = 总条数，且每条各出现一次）。
    """
    agent_a = make_agent(tmp_path, "cb2-a")
    agent_b = make_agent(tmp_path, "cb2-b")
    spy = PostSpy()
    patch_post(monkeypatch, spy)
    seed_queue(agent_a, reports("seed"))

    barrier = threading.Barrier(5, timeout=20)
    errors: list = []

    def writer(agent: BaseAgent, tag: str) -> None:
        try:
            barrier.wait()
            for i in range(10):
                agent._enqueue_pending(Report(
                    level="agent", source=tag, subject=f"{tag}-{i}",
                    body="b（模拟）"))
        except Exception as exc:   # pragma: no cover - 触发即失败
            errors.append(exc)

    def sender(agent: BaseAgent) -> None:
        try:
            barrier.wait()
            for _ in range(15):
                agent.send_pending()
        except Exception as exc:   # pragma: no cover - 触发即失败
            errors.append(exc)

    expected = [f"{tag}-{i}" for tag in ("wa", "wb") for i in range(10)]
    threads = [threading.Thread(target=writer, args=(agent_a, "wa")),
               threading.Thread(target=writer, args=(agent_b, "wb")),
               threading.Thread(target=sender, args=(agent_a,)),
               threading.Thread(target=sender, args=(agent_a,)),
               threading.Thread(target=sender, args=(agent_b,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert errors == []                       # 修复前在这里拿到 PermissionError
    for _ in range(50):
        if not queue_path(agent_a).exists():
            break
        if agent_a.send_pending()["remaining"] == 0:
            break
    assert not queue_path(agent_a).exists()
    assert sorted(spy.snapshot()) == sorted(["seed"] + expected)  # 零丢失零重复
    assert base_agent._HELD_LOCK_PATHS == {}
