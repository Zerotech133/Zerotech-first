"""签准网关持久化测试（工位 BB / WO-S11-02）。

覆盖两个真实漏洞的修复验收：

1. 账本只靠线程锁 → 跨进程写坏。本模块用**子进程**并发追加验证
   每行均可解析、条数等于提交数（Windows 下 multiprocessing 的
   spawn 需要目标函数可 pickle，且 pytest 载入的测试模块在子进程中
   未必可导入，容易假失败；改用 subprocess + `-c` 内联脚本，
   等价于真实跨进程，且不依赖测试模块可导入）。
2. nonce 已消费集合在内存 → 重启后同一签名可再用一次。本模块验证
   新建实例（同一账本、同一公钥）对同一 nonce 的签名返回
   `signature replayed`，并验证已消费集合是**从账本派生**的
   （含坏行留痕、pending 不消费、rejected 消费）。

纪律：
- 零真实网络：bus_url 指向不可达回环端口，用例不触发 report_up。
- 账本一律落 tmp_path；数据均为模拟构造，不涉及任何真实身份。
- 不修改任何他人的文件；本文件只测 roles/approval_gate.py 的公开契约。

注意：ApprovalGate 是 BaseAgent 子类，BaseAgent 在 prometheus 全局
注册表登记固定名称指标，同进程反复实例化会 DuplicateTimeseries；
base_agent 归其他工位维护，故用 autouse fixture 清空注册表。
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

import roles.approval_gate as gate_module
from roles.approval_gate import (
    MIN_NONCE_LEN,
    ApprovalGate,
    LedgerConflict,
    LedgerCorrupt,
    PrincipalVerifier,
    VerificationError,
    signature_payload,
)

# 指向不可达回环端口：BaseAgent 构造需要该参数，但本组用例不发网络。
DUMMY_KW = {"report_to": "principal", "bus_url": "http://127.0.0.1:1"}

# 模拟密钥对：仅本进程内生成，不落盘、不代表任何真实身份。
TEST_KEY = Ed25519PrivateKey.generate()
TEST_PUB = TEST_KEY.public_key().public_bytes(
    encoding=Encoding.Raw, format=PublicFormat.Raw)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# metrics_port 每次递增，避免同进程内端口口径撞车。
_PORT_SEQ = itertools.count(57000)


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """清空 prometheus 全局注册表，允许同进程反复实例化 BaseAgent 子类。"""
    from prometheus_client import REGISTRY

    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def sign(item_id: str, fingerprint: str, action: str = "approve",
         nonce: str | None = None, ts: float | None = None,
         key: Ed25519PrivateKey | None = None) -> dict:
    """按网关同款载荷签名，产出可提交的 signature 对象（模拟签名）。"""
    private_key = TEST_KEY if key is None else key
    signed_ts = time.time() if ts is None else float(ts)
    signed_nonce = nonce or f"nonce-{time.time_ns()}"
    assert len(signed_nonce) >= MIN_NONCE_LEN
    payload = signature_payload(item_id=item_id, fingerprint=fingerprint,
                                action=action, ts=signed_ts,
                                nonce=signed_nonce)
    return {"scheme": "ed25519-v1", "ts": signed_ts, "nonce": signed_nonce,
            "sig": private_key.sign(payload).hex(), "action": action,
            "item_id": item_id, "fingerprint": fingerprint}


def make_gate(ledger_path, pub: bytes | None = TEST_PUB,
              metrics_port: int | None = None) -> ApprovalGate:
    """构造网关实例；pub 为 None 表示未配置公钥（模拟未配密钥的部署）。"""
    verifier = PrincipalVerifier(pub)
    return ApprovalGate(ledger_path=Path(ledger_path), verifier=verifier,
                        metrics_port=next(_PORT_SEQ), **DUMMY_KW)


def read_entries(path: Path) -> list[dict]:
    """读取账本全部条目；任一行非法即失败（用于验证"没写坏"）。"""
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_raw(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def approved_line(item_id: str, nonce: str, ts: float | None = None,
                  status: str = "approved", verified: bool = True,
                  fingerprint: str = "f" * 64) -> str:
    """构造一条账本条目 JSON 文本（模拟历史账本，用于派生消费集合）。"""
    entry = {
        "item_id": item_id,
        "status": status,
        "fingerprint": fingerprint,
        "verified": verified,
        "verification": "ed25519-v1",
        "ts": time.time() if ts is None else ts,
        "principal_proof": {"by": "principal", "note": "模拟"},
        "signature": {"scheme": "ed25519-v1", "ts": time.time(),
                      "nonce": nonce, "sig": "00" * 64},
    }
    return json.dumps(entry, ensure_ascii=False)


# ---------------- 1. nonce 跨重启不得重放 ----------------


def test_replay_across_restart_rejected(tmp_path):
    """gate1 签准成功后，新实例 gate2 用同一 nonce 签另一 item 必被拒。"""
    ledger = tmp_path / "ledger.jsonl"
    nonce = "restart-nonce-0001"

    gate1 = make_gate(ledger)
    item_a = {"item_id": "A-1", "action": "deploy", "version": "v1"}
    gate1.submit(item_a)
    fp_a = ApprovalGate.fingerprint(item_a)
    first = gate1.approve("A-1", fp_a, {"by": "principal"},
                          signature=sign("A-1", fp_a, nonce=nonce))
    assert first["ok"] is True, first

    # 重启：新实例、同一账本、同一公钥，内存态全空。
    gate2 = make_gate(ledger)
    item_b = {"item_id": "B-1", "action": "deploy", "version": "v2"}
    gate2.submit(item_b)
    fp_b = ApprovalGate.fingerprint(item_b)
    replay = gate2.approve("B-1", fp_b, {"by": "principal"},
                           signature=sign("B-1", fp_b, nonce=nonce))
    assert replay["ok"] is False
    assert replay["err"] == "signature replayed"

    # 重放不得落盘：账本仍只有 pending A / approved A / pending B。
    entries = read_entries(ledger)
    assert [e["status"] for e in entries] == ["pending", "approved", "pending"]
    assert not any(e["item_id"] == "B-1" and e["status"] == "approved"
                   for e in entries)


def test_replay_within_same_instance_rejected(tmp_path):
    """同一实例内同一 nonce 二次签准同样被拒（基线不变）。"""
    ledger = tmp_path / "ledger.jsonl"
    nonce = "same-instance-nonce-01"
    gate = make_gate(ledger)
    for item_id in ("S-1", "S-2"):
        gate.submit({"item_id": item_id, "action": "deploy"})
    fp1 = ApprovalGate.fingerprint({"item_id": "S-1", "action": "deploy"})
    fp2 = ApprovalGate.fingerprint({"item_id": "S-2", "action": "deploy"})
    assert gate.approve("S-1", fp1, {}, signature=sign(
        "S-1", fp1, nonce=nonce))["ok"] is True
    second = gate.approve("S-2", fp2, {}, signature=sign(
        "S-2", fp2, nonce=nonce))
    assert second == {"ok": False, "err": "signature replayed"}


def test_failed_verification_does_not_burn_nonce_across_restart(tmp_path):
    """验签失败的 nonce 不算已消费：重启后同 nonce 合法签名仍可用。"""
    ledger = tmp_path / "ledger.jsonl"
    nonce = "unburned-nonce-0001"
    gate1 = make_gate(ledger)
    item = {"item_id": "U-1", "action": "deploy"}
    gate1.submit(item)
    fp = ApprovalGate.fingerprint(item)
    bad = gate1.approve("U-1", fp, {}, signature=sign(
        "U-1", fp, nonce=nonce, action="reject"))  # action 不符 → 验签失败
    assert bad["ok"] is False

    gate2 = make_gate(ledger)
    ok = gate2.approve("U-1", fp, {}, signature=sign(
        "U-1", fp, nonce=nonce))
    assert ok["ok"] is True, ok


# ---------------- 2. 消费集合从账本派生 ----------------


def test_reload_derives_consumed_nonces_from_ledger(tmp_path):
    """approved/rejected 且 verified 的条目 → nonce 计入已消费。"""
    ledger = tmp_path / "ledger.jsonl"
    write_raw(ledger, [
        approved_line("H-1", "hist-nonce-approved", status="approved"),
        approved_line("H-2", "hist-nonce-rejected", status="rejected"),
        approved_line("H-3", "hist-nonce-pending", status="pending",
                      verified=False),
        approved_line("H-4", "hist-nonce-unverified", status="approved",
                      verified=False),
    ])
    gate = make_gate(ledger)
    loaded = gate.reload_consumed_nonces()
    assert loaded == 2
    # WO-LEGION-S17-02 起，整本无链的旧格式账本读取时必须留痕一条
    # （内容不可校验属已知残余，可见可核对）——断言更新为精确相等。
    assert gate.data_gaps == [
        "账本共 4 行为完整性链启用前的旧格式（无链键），"
        "按旧口径读取：内容不可校验，建议运维修账本迁移"]

    for item_id, nonce, expect in (
            ("N-1", "hist-nonce-approved", "signature replayed"),
            ("N-2", "hist-nonce-rejected", "signature replayed"),
            ("N-3", "hist-nonce-pending", None),
            ("N-4", "hist-nonce-unverified", None)):
        item = {"item_id": item_id, "action": "deploy"}
        gate.submit(item)
        fp = ApprovalGate.fingerprint(item)
        res = gate.approve(item_id, fp, {}, signature=sign(item_id, fp,
                                                          nonce=nonce))
        if expect is None:
            assert res["ok"] is True, (item_id, res)
        else:
            assert res["err"] == expect, (item_id, res)


def test_reload_is_idempotent_and_reports_count(tmp_path):
    """重复 reload 不重复计数、不报错，返回值说明加载条数。"""
    ledger = tmp_path / "ledger.jsonl"
    write_raw(ledger, [approved_line("I-1", "idem-nonce-1"),
                       approved_line("I-2", "idem-nonce-2")])
    gate = make_gate(ledger)
    assert gate.reload_consumed_nonces() == 2
    assert gate.reload_consumed_nonces() == 2
    # 同上：旧格式账本读取留痕一条（WO-LEGION-S17-02），且重复 reload
    # 不重复计数（幂等断言本身不变）。
    assert gate.data_gaps == [
        "账本共 2 行为完整性链启用前的旧格式（无链键），"
        "按旧口径读取：内容不可校验，建议运维修账本迁移"]


def test_missing_ledger_yields_empty_consumed_set(tmp_path):
    """账本不存在 → 空集、无留痕、正常可签准。"""
    ledger = tmp_path / "nope" / "ledger.jsonl"
    gate = make_gate(ledger)
    assert gate.reload_consumed_nonces() == 0
    assert gate.data_gaps == []
    item = {"item_id": "M-1", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    assert gate.approve("M-1", fp, {}, signature=sign("M-1", fp))["ok"] is True


def test_corrupt_ledger_fails_closed(tmp_path):
    """坏行 → 抛 VerificationError，且不落任何决定条目（失败即拒绝）。

    选择失败即拒绝而不是"留痕跳过"：账本是"该事项是否仍 pending"的
    唯一权威，跳过一条损坏的 approved 记录会让同一事项被二次放行、其
    nonce 重新可用。对签准网关，"看不见"比"拒绝服务"危险得多。
    """
    ledger = tmp_path / "ledger.jsonl"
    write_raw(ledger, [
        approved_line("C-1", "corrupt-nonce-good"),
        "{ 这不是合法 JSON",
    ])
    gate = make_gate(ledger)
    with pytest.raises(VerificationError):
        gate.reload_consumed_nonces()
    with pytest.raises(VerificationError):
        gate.ledger()

    # WO-LEGION-S17-02 收紧：链启用后追加必须绑定可信链尾，坏尾行上
    # 追加会把链永久焊死在坏行上——登记一并拒绝（原口径"登记仍可"
    # 随链启用废止；收紧而非放松，工单归属规则允许并披露）。
    item = {"item_id": "C-2", "action": "deploy"}
    with pytest.raises(LedgerCorrupt):
        gate.submit(item)

    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert len(lines) == 2            # 原始 2 条原样，无任何追加


def test_missing_signature_is_soft_gap(tmp_path):
    """缺 signature.nonce 属软缺口：进 data_gaps，其余条目照常派生。

    与坏行区分：这是"字段缺了但整行可解析"，不至于让整本账不可信，
    故留痕继续；坏行则抛 LedgerCorrupt。
    """
    ledger = tmp_path / "ledger.jsonl"
    write_raw(ledger, [
        approved_line("G-1", "gap-nonce-good"),
        json.dumps({"item_id": "G-2", "status": "approved",
                    "verified": True, "ts": time.time()},
                   ensure_ascii=False),
    ])
    gate = make_gate(ledger)
    assert gate.reload_consumed_nonces() == 1
    assert any("signature.nonce" in gap for gap in gate.data_gaps)


def test_two_day_old_approval_still_blocks_replay(tmp_path):
    """2 天前签准（超过默认 24h TTL）的 nonce，重启后仍判 replay。

    证明账本派生的 durable nonce 不被 TTL 清理：旧实现里 TTL 一过，
    同一张签名就能再用一次。
    """
    ledger = tmp_path / "ledger.jsonl"
    old_ts = time.time() - 2 * 24 * 3600
    write_raw(ledger, [approved_line("T-1", "old-nonce-000001", ts=old_ts)])
    gate = make_gate(ledger)
    assert gate.reload_consumed_nonces() == 1

    item = {"item_id": "T-2", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    res = gate.approve("T-2", fp, {}, signature=sign(
        "T-2", fp, nonce="old-nonce-000001"))
    assert res == {"ok": False, "err": "signature replayed"}


# ---------------- 3. 并发：跨进程追加 / 抢同一 item ----------------


_CHILD_SCRIPT = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from roles.approval_gate import ApprovalGate, PrincipalVerifier

ledger, worker, count = sys.argv[2], sys.argv[3], int(sys.argv[4])
gate = ApprovalGate(ledger_path=Path(ledger), verifier=PrincipalVerifier(None),
                    report_to="principal", bus_url="http://127.0.0.1:1",
                    metrics_port=0)
for i in range(count):
    gate.submit({"item_id": f"{worker}-{i}", "action": "deploy",
                 "payload": "模拟·跨进程追加"})
"""


def test_cross_process_append_keeps_ledger_parseable(tmp_path):
    """4 个子进程 × 8 条并发追加：每行可 json.load，条目数等于提交数。"""
    ledger = tmp_path / "ledger.jsonl"
    workers, per_worker = 4, 8
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD_SCRIPT, str(_REPO_ROOT),
             str(ledger), f"w{i}", str(per_worker)],
            cwd=str(_REPO_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        for i in range(workers)
    ]
    try:
        for proc in procs:
            _, err = proc.communicate(timeout=120)
            assert proc.returncode == 0, err.decode("utf-8", "replace")
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()

    entries = read_entries(ledger)          # 任一行损坏即在此抛错
    assert len(entries) == workers * per_worker
    assert all(e["status"] == "pending" for e in entries)
    ids = {e["item_id"] for e in entries}
    assert len(ids) == workers * per_worker  # 无丢条、无串行化截断


def test_threaded_append_single_instance(tmp_path):
    """单实例多线程追加：self._lock 串行化，账本不损坏。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    per_thread = 10
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            for i in range(per_thread):
                gate.submit({"item_id": f"t{idx}-{i}", "action": "deploy"})
        except BaseException as exc:          # 线程内异常需带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    entries = read_entries(ledger)
    assert len(entries) == 8 * per_thread


def test_concurrent_approve_same_item_single_winner(tmp_path):
    """两个线程同时签准同一 pending item（不同 nonce）：只允许一条 approved。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    item = {"item_id": "RACE-1", "action": "deploy", "version": "v9"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)

    barrier = threading.Barrier(2)
    results: list[dict] = []
    lock = threading.Lock()

    def racer(idx: int) -> None:
        sig = sign("RACE-1", fp, nonce=f"race-nonce-{idx:06d}")
        barrier.wait(timeout=30)
        res = gate.approve("RACE-1", fp, {"by": f"p{idx}"}, signature=sig)
        with lock:
            results.append(res)

    threads = [threading.Thread(target=racer, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    winners = [r for r in results if r["ok"] is True]
    losers = [r for r in results if r["ok"] is False]
    assert len(winners) == 1, results
    assert len(losers) == 1
    assert "pending" in losers[0]["err"]

    entries = read_entries(ledger)
    approved = [e for e in entries if e["item_id"] == "RACE-1"
                and e["status"] == "approved"]
    assert len(approved) == 1


_APPROVE_CHILD_SCRIPT = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from roles.approval_gate import ApprovalGate, PrincipalVerifier, signature_payload

ledger, priv_hex, pub_hex, item_id, fp, nonce = sys.argv[2:8]
start_at = float(sys.argv[8])
key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
gate = ApprovalGate(ledger_path=__import__("pathlib").Path(ledger),
                    verifier=PrincipalVerifier(bytes.fromhex(pub_hex)),
                    report_to="principal", bus_url="http://127.0.0.1:1",
                    metrics_port=0)
ts = time.time()
payload = signature_payload(item_id=item_id, fingerprint=fp,
                            action="approve", ts=ts, nonce=nonce)
sig = {"scheme": "ed25519-v1", "ts": ts, "nonce": nonce,
       "sig": key.sign(payload).hex()}
# 对齐起跑时刻，尽量把两个进程挤进同一个读-判-验窗口。
while time.time() < start_at:
    time.sleep(0.002)
print(json.dumps(gate.approve(item_id, fp, {"by": "principal"},
                              signature=sig), ensure_ascii=False))
"""


def test_cross_process_approve_same_item_single_winner(tmp_path):
    """两个**进程**同时对同一 pending item 签准：账本只允许一条 approved。

    这是跨进程 CAS 的关键验收：读-判-验在进程内完成，落盘时的复核与
    追加必须在同一把跨进程锁内，否则两边都"读到 pending"，各写一条。
    """
    ledger = tmp_path / "ledger.jsonl"
    item = {"item_id": "XP-1", "action": "deploy", "version": "v3"}
    gate = make_gate(ledger)
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)

    priv_hex = TEST_KEY.private_bytes(
        encoding=Encoding.Raw, format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption()).hex()
    start_at = time.time() + 1.5
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _APPROVE_CHILD_SCRIPT, str(_REPO_ROOT),
             str(ledger), priv_hex, TEST_PUB.hex(), "XP-1", fp,
             f"xp-race-nonce-{i:04d}", str(start_at)],
            cwd=str(_REPO_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        for i in (1, 2)
    ]
    try:
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            assert proc.returncode == 0, err.decode("utf-8", "replace")
            outputs.append(json.loads(out.decode("utf-8").strip().splitlines()[-1]))
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()

    oks = [r for r in outputs if r.get("ok") is True]
    assert len(oks) == 1, outputs
    entries = read_entries(ledger)
    approved = [e for e in entries if e["item_id"] == "XP-1"
                and e["status"] == "approved"]
    assert len(approved) == 1, entries


def test_append_cas_rejects_second_decision(tmp_path):
    """临界区内复核（CAS）：对已决定的事项再追加决定条目 → 抛 LedgerConflict。

    这是"并发抢同一 item"的确定性验收——不依赖线程/进程调度，
    直接验证落盘前的复核闸门本身。
    """
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    item = {"item_id": "D-1", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    assert gate.approve("D-1", fp, {}, signature=sign("D-1", fp))["ok"] is True

    with pytest.raises(LedgerConflict):
        gate._append_locked({"item_id": "D-1", "status": "approved",
                             "verified": True, "ts": time.time(),
                             "signature": {"nonce": "cas-nonce-000001"}})

    lines = [e for e in read_entries(ledger) if e["item_id"] == "D-1"]
    assert [e["status"] for e in lines] == ["pending", "approved"]


def test_two_instances_same_ledger_concurrent_write(tmp_path):
    """同进程两实例指向同一账本并发写：不抛异常、不丢条、不重条、不写坏。

    这是"按路径分片锁"的守卫：删掉 _path_lock 后功能上一切正常，只有
    本场景会炸（file_lock 的同进程重入登记不区分线程，直接抛
    RuntimeError）。所以必须有这条会变红的用例钉住它。
    """
    ledger = tmp_path / "ledger.jsonl"
    g1 = make_gate(ledger)
    g2 = make_gate(ledger)
    # 按路径分片（不是按实例）：同一路径必须拿到同一把锁。
    assert gate_module._path_lock(ledger) is gate_module._path_lock(ledger)
    # 不同路径必须拿到不同的锁，否则退化为全局单锁、失去分片语义。
    assert (gate_module._path_lock(ledger)
            is not gate_module._path_lock(tmp_path / "other_ledger.jsonl"))

    per_thread, threads_per_instance = 10, 2
    total = per_thread * threads_per_instance * 2
    errors: list[BaseException] = []

    def worker(gate_obj: ApprovalGate, tag: str) -> None:
        try:
            for i in range(per_thread):
                gate_obj.submit({"item_id": f"{tag}-{i}", "action": "deploy"})
        except BaseException as exc:      # 线程内异常带回主线程断言
            errors.append(exc)

    threads = []
    for inst, gate_obj in ((1, g1), (2, g2)):
        for k in range(threads_per_instance):
            threads.append(threading.Thread(
                target=worker, args=(gate_obj, f"g{inst}t{k}")))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []                       # 一个异常都不许有
    entries = read_entries(ledger)            # 逐行 json.loads，坏行即失败
    assert len(entries) == total              # 不丢写
    assert len({e["item_id"] for e in entries}) == total   # 不重复写成同一条
    assert all(e["status"] == "pending" for e in entries)

    residual = [p.name for p in tmp_path.iterdir()
                if p.name.endswith((".tmp", "-journal", "-wal", "-shm"))]
    assert residual == []                     # 无中间文件残留


def test_path_lock_missing_causes_runtime_error(tmp_path, monkeypatch):
    """反证：分片锁失效时，同进程并发写必然抛 RuntimeError。

    做法：把模块级 _path_lock() 换成"每次返回新锁"，再让一个线程持住
    file_lock，本线程调 _append_locked。没有共享分片锁，第二方会直接
    撞上 file_lock 的同进程重入登记 → 响亮失败且不落盘。这条证明
    _path_lock 不是冗余，谁删它谁的红。
    """
    monkeypatch.setattr(gate_module, "_path_lock",
                        lambda path: threading.Lock())
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    holder_ready = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with gate_module.file_lock(gate._ledger_lock_path()):
            holder_ready.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holder_ready.wait(5), "持锁线程未能就位"
        with pytest.raises(RuntimeError):
            gate._append_locked({"item_id": "NEG-1", "status": "pending"})
    finally:
        release.set()
        thread.join()

    assert not ledger.exists()                # 失败不落盘


# ---------------- 4. 语义不变回归 ----------------


def test_no_public_key_fails_closed(tmp_path):
    """未配置公钥：任何签名都不得放行，且不落 approved。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger, pub=None)
    item = {"item_id": "K-1", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    res = gate.approve("K-1", fp, {}, signature=sign("K-1", fp))
    assert res["ok"] is False
    assert res["err"]
    assert [e["status"] for e in read_entries(ledger)] == ["pending"]


def test_action_binding_still_enforced(tmp_path):
    """签名 action=reject 不得用于 approve（载荷绑定不可放宽）。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    item = {"item_id": "K-2", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    res = gate.approve("K-2", fp, {}, signature=sign("K-2", fp,
                                                     action="reject"))
    assert res["ok"] is False
    assert [e["status"] for e in read_entries(ledger)] == ["pending"]


def test_expired_timestamp_rejected(tmp_path):
    """时间戳超出 max_skew_s 窗口 → 拒绝，且不消费 nonce。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    item = {"item_id": "K-3", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    stale = sign("K-3", fp, nonce="stale-nonce-0001", ts=time.time() - 3600)
    res = gate.approve("K-3", fp, {}, signature=stale)
    assert res["ok"] is False
    assert res["err"]
    assert [e["status"] for e in read_entries(ledger)] == ["pending"]

    # 过期签名不烧凭据：fresh 时间戳可正常签准。
    fresh = sign("K-3", fp, nonce="fresh-nonce-0001")
    assert gate.approve("K-3", fp, {}, signature=fresh)["ok"] is True


def test_ledger_write_failure_is_raised_not_swallowed(tmp_path, monkeypatch):
    """落盘失败必须抛出（closed on failure），不得返回 ok=True。"""
    ledger = tmp_path / "ledger.jsonl"
    gate = make_gate(ledger)
    item = {"item_id": "F-1", "action": "deploy"}
    gate.submit(item)
    fp = ApprovalGate.fingerprint(item)

    real_append = gate._append_locked

    def boom(*args, **kwargs):
        real_append(*args, **kwargs)          # 先写入，再让调用方感知失败
        raise OSError("模拟磁盘写满")

    monkeypatch.setattr(gate, "_append_locked", boom)
    with pytest.raises(OSError):
        gate.approve("F-1", fp, {}, signature=sign("F-1", fp))
