"""签准网关（approval_gate）。

职责：本人签准与审批账本。待签事项必须绑定材料指纹；只有老田本人
可闭环签准。红线：自报 USER/confirmed_by、会议 confirmed、integrity=valid、
门户 token 均不构成本人签准；本网关无任何自动放行路径。

本人身份核验（老田 2026-10-20 裁定·离线签名）
------------------------------------------------
裁定原文口径：「离线签名」——本人持私钥（U盾/HSM/本地密钥），签准动作
提交对 (item_id + 材料指纹 + 动作 + 时间戳 + nonce) 的 Ed25519 签名，
本网关持**公钥**验签。私钥永不进入本仓库、本进程、本账本。

三条不可让渡的约束：

1. **无公钥即拒绝**：未配置 verifier 时，approve/reject 一律返回
   ``{"ok": False, "err": "principal verification not configured"}``，
   不落盘、不降级为"仅留痕"。宁可签不了，不可放行疑。
2. **签名必绑定语义**：签名载荷含 item_id、材料指纹与动作（approve/
   reject）三者，故"否决的签名"不能挪用来"通过"，"A 事项的签名"不能
   挪用到 B 事项——这是防张冠李戴的唯一手段。
3. **防重放**：载荷含毫秒级时间戳与一次性 nonce。时间戳超出
   ``max_skew_s`` 窗口拒绝；nonce 用过即作废（successful 决定才消费），
   重放同一条签名拒绝。已消费集合在进程重启后从账本重建（
   ``reload_consumed_nonces``），故重启不构成重放窗口。

错误协议（调用方必读，两种失败形态不可混用）
------------------------------------------
- ``submit(item)``：只追加 pending，**不做全账本读取**（O(1) 反向读
  尾行取链尾，见 _append_unlocked）→ 中间行的坏行/篡改不由 submit
  拦截（读路径拦）。但尾行本身非法/缺链时抛 ``LedgerCorrupt``：
  在坏账本上追加会把完整性链永久焊死在坏行上，宁可拒登记。
- ``approve`` / ``reject``：业务层面失败返回 ``{"ok": False, "err": ...}``
  （item not found / item not pending / fingerprint conflict /
  principal verification not configured / signature ...）。
  但**账本损坏时抛 ``LedgerCorrupt``（继承 VerificationError），不返回
  dict**——此时审批判定不可信，不做部分信任、不降级继续。
- ``ledger()`` / ``reload_consumed_nonces()``：账本损坏时同样抛
  ``LedgerCorrupt``，不返回部分结果。
故调用方必须 ``try/except VerificationError`` 兜住，**不能只判
``res["err"]``**——否则 LedgerCorrupt 会当未捕获异常崩掉调用链。
口径一句话：**登记与决定都过账本**（WO-LEGION-S17-02 起收紧——submit
反向读尾行取链尾，坏尾行上登记与决定一并拒绝；approve/reject 读全账本
做全链校验）。账本损坏的恢复手段是运维修账本（TODO(领域实现): 账本修复
工具；校验已由 ``verify_ledger()`` 提供），不是网关自行跳过坏行。

仍未实现（下游，不属本次裁定范围）：密钥轮换与吊销、多设备公钥并存、
密钥丢失后的恢复流程。账本完整性链（WO-LEGION-S17-02）已实现：哈希
内嵌 entry（prev_hash + entry_hash）可**检测**链内篡改/删行/重排；
但纯哈希链无外部锚点，"删尾部行 / 整本重写并重算全链"仍不可检出——
带密钥的外部锚定（账本签名/周期锚定）仍属下游。

红线（本模块全文件适用，写入代码注释并受其约束）：
    principal_proof 仍是自由留痕字段（备注/工号等），**不构成认证**；
    认证只来自 Ed25519 验签结果。本模块不存在任何自动放行路径。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from roles.base_agent import BaseAgent, file_lock

# 默认账本路径（相对工作目录）；测试场景经构造参数注入 tmp_path。
DEFAULT_LEDGER_PATH = Path("data") / "approval_ledger.jsonl"

# 签名方案标识：进签名载荷，防止换方案后旧签名被误用。
SIG_SCHEME = "ed25519-v1"
# 时间戳允许偏移（秒）：超出即视为过期/未来签名。
DEFAULT_MAX_SKEW_S = 300.0
# nonce 保活时长（秒）：超期后清理，防内存无界增长。
DEFAULT_NONCE_TTL_S = 86400.0
# nonce 最短长度：太短可被撞库，直接判非法。
MIN_NONCE_LEN = 16

# 账本写/读路径的**进程内按路径分片锁**。
# 为什么需要它：``self._lock`` 是实例级的，同进程内若有两个 Gate 实例
# 指向同一账本，实例锁互不相识；而 file_lock 的同进程重入登记表不区分
# 线程，两个线程并发进入同一路径会被判为"重入"而响亮抛 RuntimeError。
# 分片锁按账本绝对路径归并同一把 RLock（同线程可重入、跨线程串行），
# 把同进程并发收敛成串行后再进 file_lock。锁序全文件统一：
# self._lock → 分片锁 → file_lock。
_PATH_LOCKS: Dict[str, Any] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Path) -> Any:
    """取账本路径对应的进程内分片锁（RLock，同线程可重入）。

    语义是"按路径分片"，不是"按实例"：同一账本路径必须拿到**同一把**
    锁，否则同进程多个 Gate 实例并发时会同时进入 file_lock，而
    file_lock 的同进程重入登记表不区分线程——直接抛 RuntimeError。
    这条不是冗余：删掉本函数后功能上一切正常，只有同进程双实例并发
    写同一账本时才炸（守卫用例 test_path_lock_is_required_... 会先叫）。
    """
    key = str(Path(path).resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


class VerificationError(ValueError):
    """签名/凭据非法（结构性错误），与"验签不通过"区分。"""


class LedgerConflict(RuntimeError):
    """账本并发冲突：临界区内复核发现该事项已非 pending，拒绝重复落盘。

    与"落盘 I/O 失败"区分：前者是并发竞争的正常结果（对方先落盘了），
    后者必须原样上抛。调用方（_decide）把前者翻译成 "item not pending"。
    """


class LedgerCorrupt(VerificationError):
    """账本损坏（坏行/非对象行/缺 item_id/完整性链断裂/行哈希不符）：
    硬错误，一律拒绝服务。

    取舍：宁可让网关暂时签不了，也不部分信任账本。账本是"该事项是否仍
    pending"的唯一权威——把坏行当空行跳过，等于让一条损坏的 approved
    记录失效：同一事项可被再签一次，其 nonce 也重新可用。对签准网关而言，
    "看不见"比"拒绝服务"危险得多，故一律抛错、不返回部分结果。
    恢复手段是运维修账本（TODO(领域实现): 账本修复工具；校验已由
    verify_ledger() 提供），
    不由网关自行降级。

    WO-LEGION-S17-02 起本异常同样覆盖**解析成功但内容被篡改**的情形：
    entry_hash 重算不符、prev_hash 链断裂（删行/重排/改链）、带链行之后
    出现无链行（抹链）均按同口径抛出——篡改与解析失败同级。
    """


# ---- 账本完整性链（WO-LEGION-S17-02） ----

# 创世哈希常量：账本第一条记录的 prev_hash。用全零串而非某个真实哈希，
# 语义即"本条之前没有任何记录"，可被任何校验方独立复现。
GENESIS_HASH = "0" * 64


def _ledger_canonical_bytes(obj: Any) -> bytes:
    """账本完整性链的**规范化序列化**（口径写死，双侧共用同一函数）。

    精确定义：``json.dumps(obj, ensure_ascii=False, sort_keys=True,
    separators=(",", ":")).encode("utf-8")``。
    - sort_keys=True：键序标准化，杜绝键序扰动导致哈希漂移；
    - separators 紧凑：无空格歧义；
    - ensure_ascii=False + utf-8 编码：中文原样参与哈希（与签名载荷
      signature_payload 的规范化口径一致，仅字段集合不同）。
    注意：账本**落盘行格式**（_append_unlocked 的 json.dumps，默认
    分隔符）与本口径无关——校验总是把行解析回对象后按本函数重新
    规范化，改落盘格式不影响链。
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _entry_chain_hash(entry: Dict[str, Any]) -> str:
    """计算单条 entry 的链哈希：sha256(规范化序列化(去掉 entry_hash 键后的 entry))。

    entry_hash 本身必须排除在哈希输入外（自引用不可能）；其余全部键——
    包括 prev_hash 与业务字段——都参与哈希，故篡改任一字段或改写
    prev_hash 都会让重算结果与存储值不符。
    """
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(_ledger_canonical_bytes(body)).hexdigest()


def signature_payload(*, item_id: str, fingerprint: str, action: str,
                      ts: float, nonce: str, scheme: str = SIG_SCHEME) -> bytes:
    """构造签名载荷（本人侧与本网关共用同一函数，避免两边拼串不一致）。

    规范化 JSON（sort_keys + 紧凑分隔符 + 不转义中文），字段集合固定：
    新增字段必须升 SIG_SCHEME，否则旧签名会被静默判为不匹配。
    """
    return json.dumps({
        "scheme": scheme,
        "item_id": str(item_id),
        "fingerprint": str(fingerprint),
        "action": str(action),
        "ts": float(ts),
        "nonce": str(nonce),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class PrincipalVerifier:
    """本人公钥验签器（Ed25519）。无私钥、不签名、不生成密钥。

    本类只做一件事：拿公钥验证"这条决定确实是持私钥者签的"。密钥生成
    与签名动作在本人侧离线工具（tools/sign_approval.py）完成。
    """

    def __init__(self, public_key: Optional[bytes] = None, *,
                 max_skew_s: float = DEFAULT_MAX_SKEW_S,
                 nonce_ttl_s: float = DEFAULT_NONCE_TTL_S) -> None:
        self.max_skew_s = float(max_skew_s)
        self.nonce_ttl_s = float(nonce_ttl_s)
        self._key: Optional[Ed25519PublicKey] = None
        if public_key is not None:
            self._key = Ed25519PublicKey.from_public_bytes(public_key)
        self._seen: Dict[str, float] = {}      # 已消费 nonce -> 消费时刻
        # 账本可派生的 nonce（durable）：只要账本里还留着那条已验签决定，
        # 它就永久有效，不参与 nonce_ttl_s 清理。历史增长上界 = 真实签准
        # 笔数，本来就有界；不这样做的话，签准 24h 后重启即重新可重放。
        self._durable_nonces: set = set()
        self._lock = threading.Lock()

    # ---- 配置 ----

    @classmethod
    def from_public_key_file(cls, path: Path, **kw: Any) -> "PrincipalVerifier":
        """从文件读公钥（hex 或原始 32 字节）。文件缺失即抛错，不静默无密钥。"""
        raw = Path(path).read_bytes()
        try:
            key = bytes.fromhex(raw.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError):
            key = raw.strip()
        return cls(key, **kw)

    @property
    def configured(self) -> bool:
        return self._key is not None

    # ---- 验签 ----

    def verify(self, *, item_id: str, fingerprint: str, action: str,
               signature: Dict[str, Any], now: Optional[float] = None,
               consume: bool = True) -> Tuple[bool, str]:
        """验签。(ok, err) 二元返回；err 为机器可读短码，便于审计聚合。

        错误码：not configured / missing signature / unsupported scheme /
        bad signature encoding / signature expired / signature replayed /
        signature invalid。

        ``consume=False`` 时只做重放**检查**而不消费 nonce——供
        ApprovalGate 在"验签通过 → 落盘成功 → 再消费"的时序下使用，
        避免落盘失败却把 nonce 烧掉、逼本人重新签名。
        """
        if self._key is None:
            return False, "not configured"
        if not isinstance(signature, dict):
            return False, "missing signature"
        if signature.get("scheme") != SIG_SCHEME:
            return False, "unsupported scheme"
        nonce = signature.get("nonce")
        if not isinstance(nonce, str) or len(nonce) < MIN_NONCE_LEN:
            return False, "bad signature encoding"
        try:
            ts = float(signature["ts"])
            raw_sig = bytes.fromhex(str(signature["sig"]))
        except (KeyError, TypeError, ValueError):
            return False, "bad signature encoding"

        current = time.time() if now is None else now
        if abs(current - ts) > self.max_skew_s:
            return False, "signature expired"

        payload = signature_payload(item_id=item_id, fingerprint=fingerprint,
                                    action=action, ts=ts, nonce=nonce)
        try:
            self._key.verify(raw_sig, payload)
        except InvalidSignature:
            return False, "signature invalid"

        # 验签通过后才检查重放：未通过的不必污染 nonce 表。
        with self._lock:
            self._purge_locked(current)
            if nonce in self._seen:
                return False, "signature replayed"
            if consume:
                self._seen[nonce] = current
        return True, ""

    def mark_used(self, nonce: str, now: Optional[float] = None) -> None:
        """把 nonce 记为已消费（供"验签通过但决定未落盘"的场景补偿）。"""
        with self._lock:
            self._seen.setdefault(nonce, time.time() if now is None else now)

    def seed_consumed(self, nonce: str, used_at: float) -> None:
        """按**原消费时刻**回填已消费 nonce（重启后从账本重建用）。

        与 mark_used 的区别有两点，都不可省：

        1. 时刻来源：本方法用账本里那条决定的 ts，而不是"现在"——否则
           每次重启都把旧凭据续命一个 TTL。
        2. durability：回填的 nonce 同时打上 durable 标记，``_purge_locked``
           不再按 TTL 清理它。理由：只要账本里还留着那条 approved/rejected
           记录，这个 nonce 就是"可从账本派生"的，清掉它等于凭空开一个
           重放窗口；而这类 nonce 的数量上界是真实签准笔数，有界。
           仅运行期消费（mark_used，账本尚未体现）的 nonce 仍受 TTL 约束。

        只写更早的时刻：同一 nonce 多次出现时取最早消费，清理时机单调
        向前，不会因重复回填而延长存活。

        内存上界：``_seen`` 的大小上界 = 账本里已签准笔数，它本身就是业务
        量、不是异常增长源，随年份线性增长属可接受；如确需硬上界，必须
        配合账本归档策略一起做（独立工单）——"启动只加载最近 N 条"会把
        N 之前的凭据重新变成可重放，窗口比 TTL 更隐蔽，不可取。
        """
        with self._lock:
            self._durable_nonces.add(nonce)
            prev = self._seen.get(nonce)
            if prev is None or used_at < prev:
                self._seen[nonce] = float(used_at)

    def _purge_locked(self, now: float) -> None:
        expired = [n for n, t in self._seen.items()
                   if n not in self._durable_nonces
                   and now - t > self.nonce_ttl_s]
        for n in expired:
            del self._seen[n]


class ApprovalGate(BaseAgent):
    def __init__(self, ledger_path: Optional[Path] = None,
                 verifier: Optional[PrincipalVerifier] = None,
                 **kw: Any) -> None:
        super().__init__(name="approval_gate", role="guard:approval",
                         level="principal", **kw)
        self.ledger_path = Path(ledger_path) if ledger_path else DEFAULT_LEDGER_PATH
        # 本人身份核验器；None 表示未配置 → approve/reject 一律拒绝（fail-closed）。
        self.verifier = verifier
        # 实例级互斥（最外层）。账本 I/O 的跨进程互斥在 _append_locked /
        # _read_ledger 里以 分片锁 → file_lock 补齐；锁序全文件统一为
        # self._lock → 分片锁 → file_lock，禁止反向获取。
        self._lock = threading.Lock()
        # 账本"读不全"的**软缺口**留痕（如缺 ts 需兜底那类可继续的情况）。
        # 硬错误（坏行/非对象行/缺 item_id）不走这里，一律抛 LedgerCorrupt
        # 拒绝服务——口径与 tian_feng 等席位一致，便于排查。
        self.data_gaps: List[str] = []

    # ---- 材料指纹：确认"签的是哪份材料" ----

    @staticmethod
    def fingerprint(payload: Any) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---- 待签事项登记 ----

    def submit(self, item: Dict[str, Any]) -> str:
        """登记待签事项（含材料指纹），追加写入账本，返回 item_id。

        账本为不可变追加日志（jsonl）：每次 submit 追加一条
        status="pending" 条目，含 item_id / fingerprint / submitted_ts / ts。

        TODO(领域实现):
        - 事项分类（交易/生产变更/发布/派工）与对应审批五要素校验；
        - 逾期提醒与作废。
        """
        item_id = str(item.get("item_id") or uuid.uuid4().hex)
        now = time.time()
        entry: Dict[str, Any] = {
            "item_id": item_id,
            "fingerprint": self.fingerprint(item),
            "submitted_ts": now,
            "ts": now,
            "status": "pending",
        }
        with self._lock:
            self._append_locked(entry)
        return item_id

    # ---- 本人签准（强制人工 + 离线签名验签） ----

    def approve(self, item_id: str, expected_fingerprint: str,
                principal_proof: Optional[Dict[str, Any]] = None, *,
                signature: Optional[Dict[str, Any]] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
        """本人确认签准（须附 Ed25519 离线签名）。

        流程：锁内读账本 → 找该 item 最新条目 → 比对指纹 → **验签** →
        追加 status="approved" 条目。

        错误协议：
        - 事项不存在 → {"ok": False, "err": "item not found"}
        - 最新条目非 pending → {"ok": False, "err": "item not pending"}
        - 指纹不匹配 → {"ok": False, "err": "fingerprint conflict"}（不落盘）
        - 未配置 verifier → {"ok": False, "err": "principal verification not configured"}
        - 签名缺失/过期/重放/不匹配 → {"ok": False, "err": "signature ..."}

        签名载荷必须绑定 (item_id, 材料指纹, action="approve", ts, nonce)；
        reject 的签名不能挪用于 approve（action 在载荷内）。
        """
        return self._decide(item_id, expected_fingerprint, principal_proof,
                            status="approved", action="approve",
                            signature=signature, now=now)

    def reject(self, item_id: str, expected_fingerprint: str,
               principal_proof: Optional[Dict[str, Any]] = None, *,
               signature: Optional[Dict[str, Any]] = None,
               now: Optional[float] = None) -> Dict[str, Any]:
        """本人否决。与 approve 同构，签名载荷 action="reject"。"""
        return self._decide(item_id, expected_fingerprint, principal_proof,
                            status="rejected", action="reject",
                            signature=signature, now=now)

    def _decide(self, item_id: str, expected_fingerprint: str,
                principal_proof: Optional[Dict[str, Any]], *, status: str,
                action: str, signature: Optional[Dict[str, Any]],
                now: Optional[float]) -> Dict[str, Any]:
        """approve/reject 共用：读账本 → 指纹比对 → 验签 → 追加决定条目。

        顺序刻意如此：前三步都是"不碰密钥的廉价校验"，验签放在落盘前
        最后一道。任何一步失败都**不落盘**；nonce 只在落盘成功后才消费
        （consume=False → 落盘 → mark_used），落盘失败不会烧掉一次性凭据。
        """
        with self._lock:
            # 重启后首次决定前，先从账本重建"已消费 nonce"集合，
            # 否则同一张签名在进程重启后可再放行一次（真实重放窗口）。
            # 加载在实例锁内完成，避免两个线程各重建一半；重建只读账本，
            # 不写任何旁路状态。
            self._ensure_consumed_loaded()
            entries = self._read_ledger()
            latest: Optional[Dict[str, Any]] = None
            for e in entries:
                if e.get("item_id") == item_id:
                    latest = e  # 追加日志：后写覆盖，取最后一条即最新
            if latest is None:
                return {"ok": False, "err": "item not found"}
            if latest.get("status") != "pending":
                return {"ok": False, "err": "item not pending"}
            if expected_fingerprint != latest.get("fingerprint"):
                # 指纹冲突：明确报错，不落盘，不得静默覆盖
                return {"ok": False, "err": "fingerprint conflict"}

            # ---- 本人身份核验（fail-closed）----
            if self.verifier is None or not self.verifier.configured:
                return {"ok": False,
                        "err": "principal verification not configured"}
            if not isinstance(signature, dict):
                return {"ok": False, "err": "missing signature"}
            ok, err = self.verifier.verify(
                item_id=item_id, fingerprint=latest["fingerprint"],
                action=action, signature=signature, now=now, consume=False)
            if not ok:
                return {"ok": False, "err": err}

            now_ts = time.time() if now is None else now
            entry: Dict[str, Any] = {
                "item_id": item_id,
                "fingerprint": latest["fingerprint"],
                "status": status,
                "decided_ts": now_ts,
                "ts": now_ts,
                # 认证结论来自验签，principal_proof 仅作自由留痕，不构成认证。
                "verification": SIG_SCHEME,
                "verified": True,
                "signature": {
                    "scheme": signature.get("scheme"),
                    "ts": signature.get("ts"),
                    "nonce": signature.get("nonce"),
                    "sig": signature.get("sig"),
                },
                "principal_proof": principal_proof,
            }
            try:
                self._append_locked(entry)
            except LedgerConflict:
                # 另一个进程/实例在本进程"读 → 判 → 验"之后先落了决定：
                # 视同"已非 pending"，不落盘、不消费凭据。
                return {"ok": False, "err": "item not pending"}
            # 落盘成功后才消费 nonce：落盘失败由异常上抛，凭据仍可用。
            self.verifier.mark_used(signature["nonce"], now_ts)
            return {"ok": True, "entry": entry}

    # ---- 防重放状态：已消费 nonce 集合（从账本派生，跨重启有效） ----

    def _record_gap(self, message: str) -> None:
        """记一条账本留痕；同一事实不重复堆叠（reload 会被反复调用）。"""
        if message not in self.data_gaps:
            self.data_gaps.append(message)

    def reload_consumed_nonces(self) -> int:
        """从账本重建"已消费 nonce"集合，返回本次加载条数。

        派生规则：``status in ("approved", "rejected")`` 且
        ``verified is True`` 的条目，其 ``signature.nonce`` 即已消费；
        消费时刻取条目 ``ts``（缺则 ``decided_ts``），交给 verifier 的
        nonce TTL 清理使用——用**原时刻**而不是"现在"，否则每次重启都
        会给旧凭据续命。

        为何从账本派生：nonce 表原本只在内存，进程重启即清空，同一张
        签名可以再放行一次。账本本就是审批决定的唯一权威记录，另开旁路
        文件只会多一个可能与账本不一致的真相源。

        坏行不再跳过：_read_ledger 直接抛 LedgerCorrupt（理由见该类
        docstring），本方法随之上抛——账本损坏时宁可整表拒绝，也不基于
        残缺内容构造防重放集合（残缺集合就是重放窗口）。
        缺 signature.nonce、缺时间戳属**软缺口**：前者无法派生该 nonce，
        记入 data_gaps 后跳过（可见、可核对）；后者按当前时间兜底并留痕。

        幂等：重复调用不重复计数；已过 TTL 被清理的 nonce 也不会被"复活"
        成新凭据（seed_consumed 只写更早时刻）。
        """
        if self.verifier is None:
            # 无公钥即拒绝：此时签准路径本就关闭，无可派生的凭据。
            return 0
        loaded = 0
        for idx, entry in enumerate(self._read_ledger(), 1):
            if entry.get("status") not in ("approved", "rejected"):
                continue
            if entry.get("verified") is not True:
                continue
            signature = entry.get("signature")
            nonce = (signature.get("nonce")
                     if isinstance(signature, dict) else None)
            if not isinstance(nonce, str) or not nonce:
                self._record_gap(
                    f"账本第 {idx} 条已验签决定缺 signature.nonce"
                    f"（item_id={entry.get('item_id')!r}）：该凭据无法派生为"
                    f"已消费，存在重放窗口")
                continue
            used_at = entry.get("ts")
            if not isinstance(used_at, (int, float)):
                used_at = entry.get("decided_ts")
            if not isinstance(used_at, (int, float)):
                # 缺时间戳时按"现在"兜底：宁可让该 nonce 多活一个 TTL，
                # 也不能当成未消费；同时留痕供核对。
                self._record_gap(
                    f"账本第 {idx} 条缺可用时间戳"
                    f"（item_id={entry.get('item_id')!r}）：消费时刻按当前"
                    f"时间兜底")
                used_at = time.time()
            self.verifier.seed_consumed(nonce, float(used_at))
            loaded += 1
        self.__dict__["_consumed_loaded_for"] = str(
            Path(self.ledger_path).resolve())
        return loaded

    def _ensure_consumed_loaded(self) -> None:
        """首次决定前懒加载账本派生的已消费集合（按账本路径判断是否需要）。

        账本路径允许实例化后注入/替换，故以"路径是否变过"为准，而不是
        一次性的布尔开关——换了账本就得按新账本重建，否则会把上一本账
        的消费集合错用到新账本上。之后的新消费由 _decide 在**落盘成功**
        后 mark_used 维护，时序不变。

        加载失败不吞：_read_ledger 的异常照常上抛——宁可签不了，不可在
        "防重放状态未知"时放行。
        """
        current = str(Path(self.ledger_path).resolve())
        if self.__dict__.get("_consumed_loaded_for") != current:
            self.reload_consumed_nonces()

    # ---- 查询（只读） ----

    def ledger(self, since: Optional[float] = None) -> List[Dict[str, Any]]:
        """审批账本只读查询。since 给定时返回 ts >= since 的条目。

        账本损坏时抛 LedgerCorrupt（fail-closed），不返回部分结果——
        否则调用方会拿着残缺视图做判断。

        TODO(领域实现): 账本存储与审计导出。
        """
        entries = self._read_ledger()
        if since is None:
            return entries
        return [e for e in entries if e.get("ts", 0.0) >= since]

    # ---- 账本 I/O（追加写；调用方须已持 self._lock） ----

    def _ledger_lock_path(self) -> Path:
        """账本边车锁文件：``<账本名>.lock``。

        不直锁账本本体：file_lock 以 open("a") 打开锁句柄，位置停在
        EOF，msvcrt 的字节区间锁会与临界区内真正的追加句柄互相冲突
        （Windows 强制区间锁，实测追加即抛 PermissionError）。边车锁
        不落在被写文件上，读写两条路径共用同一把锁。
        """
        return self.ledger_path.with_name(self.ledger_path.name + ".lock")

    def _append_locked(self, entry: Dict[str, Any]) -> None:
        """追加一条 jsonl 记录。账本只追加、不改写、不删除。

        互斥两层：进程内按路径分片锁（覆盖同进程多实例）+ 边车 file_lock
        （跨进程，修复"只靠 threading.Lock 跨进程写坏账本"）。锁序全文件
        统一为 self._lock → 分片锁 → file_lock；本函数内**不再**嵌套同路径
        file_lock（file_lock 同进程重入会响亮抛 RuntimeError）。

        决定类条目（approved/rejected）在**同一临界区内复核**该事项最新
        条目仍是 pending，否则抛 LedgerConflict 拒绝落盘：_decide 的
        "读 → 判 → 验"在进入本函数前完成，两个进程可能都读到 pending，
        复核加追加必须在同一把锁里才构成跨进程 CAS；否则同一事项会被
        写成两条 approved。

        落盘失败（磁盘满/权限/句柄异常）一律上抛：closed on failure，
        绝不吞掉后回报"已签准"。
        """
        with (_path_lock(self.ledger_path),
              file_lock(self._ledger_lock_path())):
            if entry.get("status") in ("approved", "rejected"):
                item_id = entry.get("item_id")
                latest: Optional[Dict[str, Any]] = None
                for existing in self._read_ledger_unlocked():
                    if existing.get("item_id") == item_id:
                        latest = existing
                if latest is None or latest.get("status") != "pending":
                    # 复核不通过一律拒绝（含"事项忽然不存在"）：宁可签不了，
                    # 不可在并发窗口里重复落盘。
                    raise LedgerConflict(
                        f"事项 {item_id!r} 最新状态为 "
                        f"{(latest or {}).get('status')!r}，拒绝重复落盘")
            self._append_unlocked(entry)

    def _append_unlocked(self, entry: Dict[str, Any]) -> None:
        """无锁追加（调用方须已持分片锁 + file_lock）。

        完整性链（WO-LEGION-S17-02）：临界区内先 O(1) 反向 seek 读尾行
        取链尾哈希（空账本用 GENESIS_HASH），写入 prev_hash + entry_hash
        后落盘——**算哈希、追加、落盘同一临界区完成**。追加整体 O(1)：
        不重算全链、不读全账本，尾行哈希即链尾权威；跨进程/跨实例互斥
        由分片锁 + file_lock 保证，故无需内存缓存链尾，也就没有多进程
        下缓存失效的问题。
        """
        tail = self._read_tail_entry_unlocked()
        if tail is None:
            prev = GENESIS_HASH
        else:
            claimed = tail.get("entry_hash")
            # 带链行以存储哈希为链尾；旧格式行（无链键，链启用前遗留）
            # 按同一规范化口径补算哈希作基线——新行挂在补算哈希上，
            # 之后旧行被篡改且其后有带链行时即可检出。
            prev = claimed if isinstance(claimed, str) \
                else _entry_chain_hash(tail)
        entry["prev_hash"] = prev
        entry["entry_hash"] = _entry_chain_hash(entry)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False,
                               sort_keys=True) + "\n")
            f.flush()
            # 耐久契约与 WAL 同一口径（WO-LEGION-S18-01）：flush 只到 OS
            # 缓冲，fsync 后才对断电/系统崩溃负责。签准事实是唯一权威
            # 载体，耐久性不得弱于下游（WAL/降级账本均 flush+fsync）。
            os.fsync(f.fileno())

    def _read_tail_entry_unlocked(self) -> Optional[Dict[str, Any]]:
        """O(1) 读取账本最后一行（二进制反向 seek，不读全文件）。

        仅供追加路径取链尾哈希，不做全链校验（那是读路径的职责）。
        空账本（不存在/零字节/全空行）返回 None。

        尾行非法 JSON / 非对象 / 缺 item_id 一律抛 LedgerCorrupt：
        在坏尾行上追加会把链永久焊死在坏行上，fail-closed 拒绝登记。
        注意这是比旧行为（submit 完全不读账本）**收紧**的口径——链启用
        后追加必须绑定前条哈希，故必须能读到可信的链尾。
        """
        path = self.ledger_path
        if not path.exists():
            return None
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            pos = size
            chunk = 8192
            data = b""
            # 从文件尾向前找：一旦块内出现换行，最后一个非空行必然完整
            # （它前面有换行边界）；无换行则整块同属一行，继续向前读。
            while pos > 0:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
                if b"\n" in data:
                    break
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        if not lines:
            return None
        raw = lines[-1]
        try:
            entry = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerCorrupt(
                "账本尾行不是合法 JSON：在坏账本上追加会永久断链，"
                "拒绝登记") from exc
        if not isinstance(entry, dict):
            raise LedgerCorrupt(
                "账本尾行不是 JSON 对象：拒绝在坏账本上追加")
        if not entry.get("item_id"):
            raise LedgerCorrupt(
                "账本尾行缺 item_id：拒绝在坏账本上追加")
        return entry

    def _read_ledger(self) -> List[Dict[str, Any]]:
        """读取全部账本条目（空文件/不存在返回空列表）。

        读也持同一把边车锁：否则可能读到别的进程刚写了一半的行。
        坏行按 fail-closed 抛 LedgerCorrupt（见 _read_ledger_unlocked），
        故 approve/reject/ledger() 在账本损坏时一律失败，不返回部分结果。
        """
        with (_path_lock(self.ledger_path),
              file_lock(self._ledger_lock_path())):
            return self._read_ledger_unlocked()

    def _read_ledger_unlocked(self) -> List[Dict[str, Any]]:
        """无锁读取（调用方须已持分片锁 + file_lock）；坏行/坏链一律抛错。

        fail-closed（失败即拒绝）：非法 JSON / 非对象行 / 缺 item_id 都抛
        LedgerCorrupt，不返回任何部分结果。理由见 LedgerCorrupt 的
        docstring——跳过坏行会让"已签准"记录凭空消失，从而对同一事项
        二次放行，并让该 nonce 重新可用。宁可签不了，不可部分信任账本。

        完整性链校验（WO-LEGION-S17-02，同样失败即拒绝）：
        - 带链行（有 entry_hash）：重算 sha256(规范化序列化(去 entry_hash
          后的 entry)) 与存储值比对，不符 = 内容被篡改；prev_hash 与前条
          有效哈希（首条用 GENESIS_HASH）比对，不符 = 链断裂（删行/重排/
          改链）。两查任一不过即抛 LedgerCorrupt。
        - 无链行（缺 entry_hash，链启用前旧格式）：只允许出现在账本最前
          段；其"有效哈希"按同一口径补算，作为后继行 prev_hash 的比对
          基线——故旧行被篡改且其后有带链行时同样被检出。带链行之后出现
          无链行 = 链键被抹（退化绕过），一律 LedgerCorrupt。
        - 整本无链：按旧口径读取并记一条 data_gaps 留痕（旧行内容不可
          校验属已知残余，可见、可核对，建议运维修账本迁移）。
        """
        if not self.ledger_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        prev = GENESIS_HASH          # 前条有效哈希（旧格式行为补算值）
        chain_rows = 0
        plain_rows = 0
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerCorrupt(
                        f"账本第 {lineno} 行不是合法 JSON（{exc.msg}）："
                        f"审批判定与防重放状态不可信，拒绝一切决定") from exc
                if not isinstance(entry, dict):
                    raise LedgerCorrupt(
                        f"账本第 {lineno} 行不是 JSON 对象"
                        f"（{type(entry).__name__}）：拒绝一切决定")
                if not entry.get("item_id"):
                    raise LedgerCorrupt(
                        f"账本第 {lineno} 行缺 item_id：无法归属事项，"
                        f"拒绝一切决定")
                claimed = entry.get("entry_hash")
                if isinstance(claimed, str):
                    if _entry_chain_hash(entry) != claimed:
                        raise LedgerCorrupt(
                            f"账本第 {lineno} 行 entry_hash 与内容重算不符："
                            f"检测到篡改，审批判定与防重放状态不可信，"
                            f"拒绝一切决定")
                    if entry.get("prev_hash") != prev:
                        raise LedgerCorrupt(
                            f"账本第 {lineno} 行 prev_hash 与前条断裂"
                            f"（疑似删行/重排/改链）：拒绝一切决定")
                    prev = claimed
                    chain_rows += 1
                else:
                    if chain_rows:
                        raise LedgerCorrupt(
                            f"账本第 {lineno} 行缺完整性链键，但其前已有"
                            f"带链行：疑似抹链篡改，拒绝一切决定")
                    prev = _entry_chain_hash(entry)
                    plain_rows += 1
                entries.append(entry)
        if plain_rows and not chain_rows:
            self._record_gap(
                f"账本共 {plain_rows} 行为完整性链启用前的旧格式（无链键），"
                f"按旧口径读取：内容不可校验，建议运维修账本迁移")
        return entries

    def verify_ledger(self) -> int:
        """校验账本完整性链（公开只读入口，WO-LEGION-S17-02）。

        校验口径与加载路径一致（见 _read_ledger_unlocked）：行哈希不符、
        prev_hash 链断裂（删行/重排/改链）、带链行后抹链，任一发生即抛
        LedgerCorrupt——篡改与解析失败同级，宁可拒绝服务不可静默接受
        坏账。

        返回校验通过的条目数；空账本（不存在/空文件）返回 0。锁口径与
        _read_ledger 相同（分片锁 → file_lock），可与并发追加互斥。
        """
        with (_path_lock(self.ledger_path),
              file_lock(self._ledger_lock_path())):
            return len(self._read_ledger_unlocked())
