"""签准账本完整性链测试（WO-LEGION-S17-02，工位 FB）。

覆盖：哈希内嵌 entry（prev_hash + entry_hash）的追加与校验闭环——

1. 连续追加后链完整可验证（verify_ledger 通过，逐行 prev_hash == 前条
   entry_hash，entry_hash 与测试侧独立重算一致）；
2. 篡改任一已写行的一个字段 → LedgerCorrupt（校验变红，读路径同样拦截）；
3. 删除中间行（序号断档/链断裂）→ LedgerCorrupt；
4. 创世行 prev_hash 恒为 GENESIS_HASH 常量；
5. 空账本/空文件/仅创世行均可验证；
6. 追加是 O(1)：pending 追加不做全链重算（不触发全账本读取，尾行以
   反向 seek 读取）；
7. 旧格式（链启用前，无链键）行兼容口径：整本无链可读并留痕
   data_gaps、旧账本可续写带链新行、旧行被篡改且其后有带链行时检出。

纪律：账本一律落 tmp_path；数据均为模拟构造，不涉及任何真实身份；
不修改任何他人文件，只测 roles/approval_gate.py 的公开契约。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from roles.approval_gate import (
    GENESIS_HASH,
    SIG_SCHEME,
    ApprovalGate,
    LedgerCorrupt,
    PrincipalVerifier,
    signature_payload,
)

# 指向不可达回环端口：BaseAgent 构造需要该参数，但本组用例不发网络。
DUMMY_KW = {"report_to": "principal", "bus_url": "http://127.0.0.1:1"}

# metrics_port 每次递增，避免同进程内端口口径撞车。
_PORT_SEQ = itertools.count(58000)

# 测试密钥：本进程内临时生成，不落盘、不代表任何真实身份。
OWNER_KEY = Ed25519PrivateKey.generate()
OWNER_PUB = OWNER_KEY.public_key().public_bytes(
    encoding=Encoding.Raw, format=PublicFormat.Raw)


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """清空 prometheus 全局注册表，允许同进程反复实例化 BaseAgent 子类。"""
    from prometheus_client import REGISTRY

    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_gate(tmp_path: Path, *, configure: bool = True,
              ledger_name: str = "ledger.jsonl") -> ApprovalGate:
    verifier = PrincipalVerifier(OWNER_PUB) if configure else None
    return ApprovalGate(ledger_path=tmp_path / ledger_name,
                        verifier=verifier, metrics_port=next(_PORT_SEQ),
                        **DUMMY_KW)


def read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in
            path.read_text(encoding="utf-8").splitlines() if l.strip()]


def rewrite_lines(path: Path, rows: list[dict]) -> None:
    """把条目列表整体写回账本（供篡改/删行用例构造损坏文件）。"""
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in rows),
        encoding="utf-8")


# ---- 测试侧独立重算（与产品代码实现分离，防同源盲区） ----

def local_entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sign(item_id: str, fingerprint: str, *, action: str = "approve",
         nonce: str = "b" * 32, ts: float | None = None) -> dict:
    signed_ts = time.time() if ts is None else float(ts)
    payload = signature_payload(item_id=item_id, fingerprint=fingerprint,
                                action=action, ts=signed_ts, nonce=nonce)
    return {"scheme": SIG_SCHEME, "ts": signed_ts, "nonce": nonce,
            "sig": OWNER_KEY.sign(payload).hex()}


# ==================== 1) 链完整可验证 ====================

def test_chain_complete_after_appends(tmp_path: Path) -> None:
    """连续 submit 三条后：verify_ledger 通过，逐行链绑定与重算哈希一致。"""
    gate = make_gate(tmp_path)
    for i in range(3):
        gate.submit({"item_id": f"I{i}", "action": "trade", "n": i})

    assert gate.verify_ledger() == 3

    rows = read_lines(gate.ledger_path)
    assert len(rows) == 3
    assert rows[0]["prev_hash"] == GENESIS_HASH
    for i in range(1, 3):
        assert rows[i]["prev_hash"] == rows[i - 1]["entry_hash"]
    for row in rows:
        assert row["entry_hash"] == local_entry_hash(row)


def test_approve_entry_chained(tmp_path: Path) -> None:
    """approve 落盘的决定条目同样入链：pending → approved 链式绑定。"""
    gate = make_gate(tmp_path)
    item = {"item_id": "A1", "action": "publish"}
    item_id = gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    res = gate.approve(item_id, fp, {"by": "laotian"},
                       signature=sign(item_id, fp))
    assert res["ok"] is True
    # 链键出现在返回条目中，且与账本行一致
    assert "prev_hash" in res["entry"] and "entry_hash" in res["entry"]
    rows = read_lines(gate.ledger_path)
    assert res["entry"]["entry_hash"] == rows[1]["entry_hash"]
    assert len(rows) == 2
    assert rows[0]["status"] == "pending"
    assert rows[1]["status"] == "approved"
    assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
    assert gate.verify_ledger() == 2


# ==================== 2) 篡改检出 ====================

def test_tampered_field_detected_by_verify(tmp_path: Path) -> None:
    """篡改已写行的一个字段（保持 JSON 合法）→ verify_ledger 抛 LedgerCorrupt。"""
    gate = make_gate(tmp_path)
    gate.submit({"item_id": "T1", "action": "trade"})
    gate.submit({"item_id": "T2", "action": "trade"})

    rows = read_lines(gate.ledger_path)
    rows[1]["status"] = "approved"  # 静默改一条 pending 为 approved
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.verify_ledger()


def test_tampered_field_detected_on_load_path(tmp_path: Path) -> None:
    """加载路径（ledger()/approve）同样拦截篡改，不返回部分结果。"""
    gate = make_gate(tmp_path)
    item = {"item_id": "L1", "action": "trade"}
    item_id = gate.submit(item)
    fp = ApprovalGate.fingerprint(item)
    gate.approve(item_id, fp, {"by": "laotian"}, signature=sign(item_id, fp))

    rows = read_lines(gate.ledger_path)
    rows[0]["fingerprint"] = "f" * 64  # 改 pending 行指纹
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.ledger()
    with pytest.raises(LedgerCorrupt):
        gate.approve(item_id, fp, {"by": "laotian"},
                     signature=sign(item_id, fp, nonce="c" * 32))


def test_tampered_prev_hash_detected(tmp_path: Path) -> None:
    """改写某行 prev_hash（链绑定被篡改）→ LedgerCorrupt。"""
    gate = make_gate(tmp_path)
    gate.submit({"item_id": "P1"})
    gate.submit({"item_id": "P2"})

    rows = read_lines(gate.ledger_path)
    rows[1]["prev_hash"] = "e" * 64
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.verify_ledger()


# ==================== 3) 删行检出 ====================

def test_deleted_middle_row_detected(tmp_path: Path) -> None:
    """删除中间行（序号断档/链断裂）→ LedgerCorrupt。"""
    gate = make_gate(tmp_path)
    for i in range(3):
        gate.submit({"item_id": f"D{i}"})

    rows = read_lines(gate.ledger_path)
    del rows[1]
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.verify_ledger()


# ==================== 4) 创世哈希常量 ====================

def test_genesis_prev_hash_is_constant(tmp_path: Path) -> None:
    """首行 prev_hash 恒等于 GENESIS_HASH 常量（64 个 '0'）。"""
    assert GENESIS_HASH == "0" * 64
    gate = make_gate(tmp_path)
    gate.submit({"item_id": "G1"})
    rows = read_lines(gate.ledger_path)
    assert rows[0]["prev_hash"] == GENESIS_HASH


# ==================== 5) 空账本/仅创世行 ====================

def test_empty_and_single_entry_verifiable(tmp_path: Path) -> None:
    """不存在文件/空文件 → verify_ledger 返回 0 且不抛；仅创世行可验证。"""
    gate = make_gate(tmp_path)
    assert gate.verify_ledger() == 0          # 文件不存在
    gate.ledger_path.parent.mkdir(parents=True, exist_ok=True)
    gate.ledger_path.write_text("", encoding="utf-8")
    assert gate.verify_ledger() == 0          # 空文件
    gate.submit({"item_id": "S1"})
    assert gate.verify_ledger() == 1          # 仅创世行


# ==================== 6) 追加 O(1) ====================

def test_append_is_o1_no_full_chain_rehash(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """pending 追加不做全链重算：submit 路径不触发全账本读取。

    O(1) 证据（行为级）：monkeypatch _read_ledger_unlocked 计数——
    连续 submit 期间计数必须为 0（链尾由 O(1) 反向 seek 尾行提供），
    verify_ledger 全量校验时计数为 1。
    """
    gate = make_gate(tmp_path)
    counter = {"full_reads": 0}
    real_read = ApprovalGate._read_ledger_unlocked

    def counting_read(self):
        counter["full_reads"] += 1
        return real_read(self)

    monkeypatch.setattr(ApprovalGate, "_read_ledger_unlocked", counting_read)

    for i in range(5):
        gate.submit({"item_id": f"O{i}"})
    assert counter["full_reads"] == 0   # 追加路径零全账本读取
    assert gate.verify_ledger() == 5
    assert counter["full_reads"] == 1   # 校验路径恰好一次全量读取


# ==================== 7) 旧格式兼容口径 ====================

def test_legacy_rows_readable_and_recorded(tmp_path: Path) -> None:
    """整本无链键（链启用前旧格式）可读，verify 通过并记 data_gaps 留痕。"""
    gate = make_gate(tmp_path)
    legacy = [
        {"item_id": "OLD1", "status": "pending", "fingerprint": "a" * 64,
         "ts": 1.0},
        {"item_id": "OLD2", "status": "pending", "fingerprint": "b" * 64,
         "ts": 2.0},
    ]
    rewrite_lines(gate.ledger_path, legacy)

    assert gate.verify_ledger() == 2
    assert any("旧格式" in g for g in gate.data_gaps)


def test_legacy_ledger_can_append_chained_row(tmp_path: Path) -> None:
    """旧格式账本可续写：新行 prev_hash 绑到旧尾行的补算哈希，链延续。"""
    gate = make_gate(tmp_path)
    legacy = [{"item_id": "OLD1", "status": "pending",
               "fingerprint": "a" * 64, "ts": 1.0}]
    rewrite_lines(gate.ledger_path, legacy)

    gate.submit({"item_id": "NEW1"})
    rows = read_lines(gate.ledger_path)
    assert len(rows) == 2
    # 新行 prev_hash == 旧尾行（无链键）按同一规范化口径补算的哈希
    assert rows[1]["prev_hash"] == local_entry_hash(rows[0])
    assert gate.verify_ledger() == 2


def test_tampered_legacy_row_detected_when_chained_row_follows(
        tmp_path: Path) -> None:
    """旧行被篡改且其后有带链行 → 补算基线变化 → LedgerCorrupt。"""
    gate = make_gate(tmp_path)
    legacy = [{"item_id": "OLD1", "status": "pending",
               "fingerprint": "a" * 64, "ts": 1.0}]
    rewrite_lines(gate.ledger_path, legacy)
    gate.submit({"item_id": "NEW1"})

    rows = read_lines(gate.ledger_path)
    rows[0]["status"] = "approved"   # 篡改旧格式行
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.verify_ledger()


def test_missing_chain_keys_after_chained_row_detected(
        tmp_path: Path) -> None:
    """带链行之后出现无链行（抹链）→ LedgerCorrupt，不允许退化绕过。"""
    gate = make_gate(tmp_path)
    gate.submit({"item_id": "M1"})
    gate.submit({"item_id": "M2"})

    rows = read_lines(gate.ledger_path)
    del rows[1]["prev_hash"]
    del rows[1]["entry_hash"]
    rewrite_lines(gate.ledger_path, rows)

    with pytest.raises(LedgerCorrupt):
        gate.verify_ledger()


# ---------------- 耐久性：追加必须 fsync（WO-LEGION-S18-01） ----------------
#
# 契约：签准账本与 WAL 同一耐久口径——flush + os.fsync 后才算落盘成功。
# 承重点：若把 fsync 从追加路径移除（退回裸 flush），calls 停在 0，
# 下面第一例按预期变红。


def test_append_fsyncs_ledger(tmp_path, monkeypatch):
    """登记追加在返回前必须调用 fsync（耐久契约由测试看住）。"""
    import roles.approval_gate as gate_mod

    gate = make_gate(tmp_path)
    calls = {"n": 0}
    real_fsync = gate_mod.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(gate_mod.os, "fsync", counting_fsync)
    gate.submit({"item_id": "F-1", "action": "deploy"})
    assert calls["n"] >= 1


def test_append_fsync_failure_raises(tmp_path, monkeypatch):
    """fsync 失败 = 落盘失败：必须上抛，不得吞掉后回报登记成功。"""
    import roles.approval_gate as gate_mod

    gate = make_gate(tmp_path)

    def boom(fd):
        raise OSError(5, "模拟 fsync 失败（注入）")

    monkeypatch.setattr(gate_mod.os, "fsync", boom)
    with pytest.raises(OSError):
        gate.submit({"item_id": "F-2", "action": "deploy"})
    # 账本上不得出现任何 approved 决定（写入半途失败不构成已签准事实）
    rows = read_lines(tmp_path / "ledger.jsonl")
    assert all(r.get("status") != "approved" for r in rows)


# ---------------- 耐久性：追加必须 fsync（WO-LEGION-S18-01） ----------------
#
# 契约：签准账本与 WAL 同一耐久口径——flush + os.fsync 后才算落盘成功。
# 承重点：若把 fsync 从追加路径移除（退回裸 flush），calls 停在 0，
# 下面第一例按预期变红。


def test_append_fsyncs_ledger(tmp_path, monkeypatch):
    """登记追加在返回前必须调用 fsync（耐久契约由测试看住）。"""
    import roles.approval_gate as gate_mod

    gate = make_gate(tmp_path)
    calls = {"n": 0}
    real_fsync = gate_mod.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(gate_mod.os, "fsync", counting_fsync)
    gate.submit({"item_id": "F-1", "action": "deploy"})
    assert calls["n"] >= 1


def test_append_fsync_failure_raises(tmp_path, monkeypatch):
    """fsync 失败 = 落盘失败：必须上抛，不得吞掉后回报登记成功。"""
    import roles.approval_gate as gate_mod

    gate = make_gate(tmp_path)

    def boom(fd):
        raise OSError(5, "模拟 fsync 失败（注入）")

    monkeypatch.setattr(gate_mod.os, "fsync", boom)
    with pytest.raises(OSError):
        gate.submit({"item_id": "F-2", "action": "deploy"})
    # 账本上不得出现任何 approved 决定（写入半途失败不构成已签准事实）
    rows = read_lines(tmp_path / "ledger.jsonl")
    assert all(r.get("status") != "approved" for r in rows)
