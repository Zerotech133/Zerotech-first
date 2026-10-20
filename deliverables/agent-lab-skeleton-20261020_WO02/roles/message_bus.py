"""汇报总线（message_bus）骨架。

职责（对齐 README 角色表 #9 与 draft.json 契约）：
- 分层汇报路由：agent → platform → command → principal；
- 拦截越级直发与伪造署名，拒绝条目进死信（dlq）并附明确原因；
- 正常消息追加 jsonl 账本（只追加，不改写）；
- 状态点归集：OS 只收「谁·在干什么·干到哪·产出在哪」，不造数；
- 未投递挂起：目标席位尚未注册的消息进挂起队列，register 成功时补投
  （FIFO），超 PENDING_DELIVERY_MAX 丢最旧并留痕（见该常量注释）；
  注入 pending_path 时挂起队列同步落快照并在启动时加载（S16：总线重启
  不再无声丢失未送达报告；默认 None 纯内存零持久化）。

本模块为骨架（scaffold）：标注 TODO 处为业务策略占位，需按领域实现。
所有 I/O 仅用标准库（json/pathlib/threading）；第三方仅 fastapi。
账本写锁复用 roles.base_agent.file_lock（跨进程边车锁，工位Z 同款）；
已实测 base_agent 不反向 import 本模块，from roles.base_agent import
file_lock 不构成循环导入。
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# 跨进程边车锁原语（工位Z 同款，见 _append docstring 的锁关系说明）。
# 循环导入实测：base_agent 仅 import 标准库/httpx/fastapi/prometheus_client，
# 不 import message_bus，故此导入安全（2026-10-20 工位AC 验证）。
from roles.base_agent import file_lock

# 汇报层级：基层 → 平台 → 中台 → 本人（与 roles/base_agent.py 保持同一契约）。
REPORT_LEVELS = ("agent", "platform", "command", "principal")

# 允许直发本人（level=principal）的席位白名单：
#   risk_guard    —— 风控预警（只预警不执行，白名单既有语义）
#   approval_gate —— 本人签准闭环（白名单既有语义）
#   tian_feng     —— 归口席扩员（老田 2026-10-20 裁定：方案B 白名单扩员）；
#                     仅阻断类可直发本人，分级判定在归口席（tian_feng），
#                     总线只校验消息类别与来源，不掌握分级语义。
# 其余席位（含监督席 zhuge_liang/zhao_yun）一律越级拦截入死信。
PRINCIPAL_WHITELIST = ("risk_guard", "approval_gate", "tian_feng")

# principal 层消息类别（可审计分类）：类别 → (subject 类别前缀, 允许发送该类别的席位)。
#
# 白名单扩员后 principal 通道同时承载预警、签准、整改工单与升级请示；
# 审计上必须能区分「归口派单（整改工单）」与「越级上报（升级请示）」，
# 否则无法分辨消息性质。故以 subject 类别前缀作为分类载体
# （Report 结构变更需版本化，前缀为等价机制；TODO(领域实现): 若 Report
# 增列 kind 字段，改为字段承载）。
PRINCIPAL_MESSAGE_CLASSES: Dict[str, tuple] = {
    "remediation_ticket": ("[整改工单]", ("tian_feng",)),
    "escalation_request": ("[升级请示]", ("tian_feng",)),
    "alert": ("[预警]", ("risk_guard",)),
    "approval": ("[待签]", ("approval_gate",)),
}

# 状态点归集字段：黑盒原则，只登记这六项，缺失如实存 None，不造数。
STATUS_POINT_FIELDS = ("agent", "platform", "doing", "progress", "artifact", "ts")

# 状态点过期 TTL（秒）：ts 距 now 严格超过该值即在 board() 标注 stale=True。
# 占位值 300：待真实心跳基线标定后调整（挂账：真实基线标定，见 board docstring）。
STATUS_STALE_TTL_S = 300

# 状态板契约版本（工位BC）：挂在 HTTP /board 的**信封层**——
#   {"contract_version": BOARD_CONTRACT_VERSION, "points": [<状态点>, ...]}
# 用途：消费方先读版本再解析 points，避免「上游改一句文案就把下游静默打断」。
# 版本是"响应整体"的属性，不是单个状态点的属性，故不落行（行级键集由
# tests/test_bus_stale.py 的精确集合断言守卫，新增行内键须另立波次并升版本）。
# 行内既有键的语义、取值域或键集发生任何不兼容变更，都必须在这里升版本
# 并知会下游，不得只改文案。本波只把版本显性化，行内键零删改，故仍是 v1。
BOARD_CONTRACT_VERSION = "v1"

# stale_reason 取值域：原因从"自由字符串"收敛为枚举常量，消费方可穷举判断，
# 见到未知值即可告警（说明契约已漂移）。
#   STALE_REASON_NONE              —— 无原因（未过期，或正常超 TTL 过期）；
#                                     此时 board() 不输出 stale_reason 字段。
#   STALE_REASON_TS_MISSING        —— ts 为 None（不代填时间）。
#   STALE_REASON_TS_UNPARSEABLE    —— ts 存在但无法 float 化（不强转）。
STALE_REASON_NONE = ""
STALE_REASON_TS_MISSING = "ts_missing"
STALE_REASON_TS_UNPARSEABLE = "ts_unparseable"

# 非空原因的全集（消费方按此校验取值域；新增原因须同时登记到此集合）。
STALE_REASONS = frozenset({STALE_REASON_TS_MISSING, STALE_REASON_TS_UNPARSEABLE})

# 死信回捞许可名单：仅中台三席或本人可发起回捞。
# 与 PRINCIPAL_WHITELIST 无关：risk_guard/approval_gate 只放行 principal 层消息，
# 不拥有死信回捞权。
REQUEUE_AUTHORIZED = ("zhuge_liang", "tian_feng", "zhao_yun", "principal")

# 挂起队列上限（条，全队列口径而非单席位口径）。
# 占位值 1000：待真实席位吞吐与积压水位基线标定后调整——
# 挂账：PENDING_DELIVERY_MAX 标定（见 _enqueue_pending 的超限策略）。
PENDING_DELIVERY_MAX = 1000

# 挂起队列快照格式版本（整队列 JSON 信封）：{"version": N, "pending": [...]}。
# 格式不兼容变更必须升版本并知会消费方，不得只改结构。
PENDING_SNAPSHOT_VERSION = 1


class PendingSnapshotCorrupt(RuntimeError):
    """挂起队列快照损坏（非法 JSON 或结构不符），启动加载时抛出。

    口径对齐 approval_gate.LedgerCorrupt（坏账拒绝启动）：总线重启后若快照
    读不出来，宁可响亮拒绝启动，也不静默当空队列——把未知当已知会制造
    无声漏报（挂起的升级/告警报告以为还在，实际已丢），漏报比重复危险。
    """

# ---------------- 总线 API 鉴权（HTTP 身份层） ----------------
#
# 契约：默认关闭 → 启用即强制（closed on failure）。
#   - MessageBus(seat_credentials=None)      —— 未配置，鉴权关闭：
#     行为与此前完全一致（零校验、零拒绝），既有调用方与既有用例零破坏；
#   - MessageBus(seat_credentials={...})     —— 已配置，鉴权启用：
#     /route /status /board 一律强制校验，**不存在「请求缺头就放过」的路径**；
#   - MessageBus(seat_credentials={})         —— 空表也是「已配置」：
#     启用但无任何席位可放行，故受保护路由全部 401（不是"关闭"）。
#     这样「配错成空表」只会更严，不会退化成无鉴权。
#
# 为什么不做「兼容开关」（如 require_auth=True/False 之外再叠一个 auth_enabled）：
# 多一个布尔开关就多一条 fail-open 路径——任何人都能把鉴权显式关掉，
# 事故复盘时无法区分「本来就没配」与「被人关了」。故只用「凭证表是否为
# None」这一个事实表达开关，且判定集中在一处。
#
# 为什么拒绝语义是 HTTP 401 而非 200 + {"ok": false}：
# 既有业务拒绝（伪署名/越级/类别越权）走 200 + {"ok": false, "dlq": true}
# 进死信（S10 判例），那是"消息被审理后驳回"；鉴权失败是**身份层失败**，
# 请求根本没进入业务审理，调用方必须能区分「你没权限」与「你的消息被死信」。
# 两者混用会让调用方把 401 当成"再改改就能过"的业务重试。
#
# token 明文禁令：日志、异常、响应体一律不得回显 token 或其前缀/长度
# （长度也是侧信道），错误信息只给原因码与固定文案。

# 席位身份请求头：两个都必须携带并校验。
AUTH_HEADER_SEAT = "X-Seat-Name"
AUTH_HEADER_TOKEN = "X-Seat-Token"

# 免鉴权路径（白名单，仅存活/就绪探测）。
# 取舍：探活由外部编排器发起、通常没有席位凭证，若也要求凭证则探活永远失败，
# 服务会被误判为宕机而反复重启。故 /health 放行，代价是它对外暴露
# 「服务名 + 账本/死信路径」——**不含账本内容与状态数据**，泄露面可接受
# （路径本身在部署清单里也是公开的）。任何新增的读取型路径都必须进
# 受保护集合，不得往本白名单加。
AUTH_EXEMPT_PATHS = frozenset({"/health"})

# 鉴权失败原因码（可区分、便于审计聚合；401 响应体只回原因码与固定文案）。
AUTH_ERR_SEAT_MISSING = "auth_seat_missing"     # 未携带 X-Seat-Name
AUTH_ERR_SEAT_UNKNOWN = "auth_seat_unknown"     # 席位名未登记在凭证表
AUTH_ERR_TOKEN_MISSING = "auth_token_missing"   # 席位已登记但未携带 X-Seat-Token
AUTH_ERR_TOKEN_INVALID = "auth_token_invalid"   # token 与登记值不一致

# 原因码 → 对外文案（固定字符串，不含任何凭证内容）。
AUTH_ERR_DETAILS: Dict[str, str] = {
    AUTH_ERR_SEAT_MISSING: f"鉴权失败：缺少席位身份头 {AUTH_HEADER_SEAT}",
    AUTH_ERR_SEAT_UNKNOWN: "鉴权失败：席位未登记",
    AUTH_ERR_TOKEN_MISSING: f"鉴权失败：缺少席位凭证头 {AUTH_HEADER_TOKEN}",
    AUTH_ERR_TOKEN_INVALID: "鉴权失败：席位凭证不匹配",
}

# 挂起队列超限丢弃留痕的 logger（warning 级，便于运维侧告警接线）。
logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _stale_state(ts: Any, now: float) -> "tuple[bool, str]":
    """判定单条状态点过期状态；返回 (stale, reason)，reason 仅异常时非空。

    - ts 距 now 严格大于 STATUS_STALE_TTL_S 才算 stale（恰等于不算）；
    - ts 缺失（None）或不可解析（无法 float 化）如实 stale=True 并给出
      reason（STALE_REASON_TS_MISSING / STALE_REASON_TS_UNPARSEABLE），
      不猜、不代填时间。
    - reason 只取 STALE_REASON_* 常量（不允许自由字符串），消费方可穷举。
    """
    if ts is None:
        return True, STALE_REASON_TS_MISSING
    try:
        age = now - float(ts)
    except (TypeError, ValueError):
        return True, STALE_REASON_TS_UNPARSEABLE
    return age > STATUS_STALE_TTL_S, STALE_REASON_NONE


def classify_message_class(subject: str) -> str:
    """从 subject 前缀解析消息类别；无已登记前缀返回空串。

    前缀是本骨架的分类载体（Report 结构变更需版本化，见
    PRINCIPAL_MESSAGE_CLASSES 注释）。解析只认已登记前缀，
    未登记前缀不猜类别（返回空串，交由调用方按层级规则处理）。
    """
    text = str(subject or "")
    for name, (prefix, _allowed) in PRINCIPAL_MESSAGE_CLASSES.items():
        if text.startswith(prefix):
            return name
    return ""


def _normalize_auth_path(path: str) -> str:
    """免鉴权白名单比对用的路径归一化：去掉尾部斜杠。

    只归一化尾部斜杠（"/health/" 与 "/health" 视为同一路径，避免探活因
    一个斜杠被判 401），不做解码/大小写折叠——不做多余猜测，减小绕过面。
    """
    stripped = (path or "").rstrip("/")
    return stripped or "/"


def _sanitize_seat_credentials(
    seat_credentials: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """构造期过滤无效凭证条目：席位名或 token 为空/非字符串/纯空白一律剔除。

    为什么剔除而不是放行：空 token 一旦进表，"空 token 即可通过"就成了绕过
    路径（请求方只需猜中席位名）。剔除后该席位等价于"未登记"，鉴权依然启用，
    请求方拿到 401 —— 失败即拒绝，坏配置只会更严，绝不会退化成放行。
    为什么剔除而不是整体抛错：单个席位配坏不该让整条总线起不来（其余席位
    仍需服务）；代价是坏席位被静默降级为"未登记"，故这里 warning 留痕
    （只报席位名与原因，**不报 token 内容/长度**）。
    注意：非 None 的空 dict 不在此处被"修复"成 None —— 开关语义只由
    __init__ 判定，本函数只做条目净化，不得改变启用/关闭状态。
    """
    if not seat_credentials:
        return {}
    cleaned: Dict[str, str] = {}
    for name, token in seat_credentials.items():
        if not isinstance(name, str) or not name.strip():
            logger.warning("席位凭证配置忽略：席位名为空或非字符串")
            continue
        if not isinstance(token, str) or not token.strip():
            logger.warning("席位凭证配置忽略：席位 %s 的 token 为空或非字符串"
                           "（空 token 不得成为免凭证通道）", name)
            continue
        cleaned[name] = token
    return cleaned


class MessageBus:
    """汇报总线：席位注册、层级路由校验、死信与账本、状态点归集。"""

    def __init__(
        self,
        ledger_path: PathLike = "data/bus_ledger.jsonl",
        dlq_path: PathLike = "data/bus_dlq.jsonl",
        seat_credentials: Optional[Dict[str, str]] = None,
        pending_path: Optional[PathLike] = None,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.dlq_path = Path(dlq_path)
        self._seats: Dict[str, str] = {}          # name -> level
        self._status_points: Dict[str, Dict[str, Any]] = {}  # agent -> 最新状态点
        # 内存收件箱：name -> 已投递消息列表（FIFO，进程内语义，见 inbox）
        self._inboxes: Dict[str, List[Dict[str, Any]]] = {}
        # 挂起队列：(target, report) 按入队先后排列（FIFO），目标注册时整批补投。
        # 默认纯进程内存（既有语义零改动）；注入 pending_path 时，队列每次
        # 变更（入队/丢最旧/补投取走）同步整队列落快照，总线重启后启动加载
        # 恢复——未送达报告不再无声丢失（见 _write_pending_snapshot_locked）。
        self._pending: Deque[Tuple[str, Dict[str, Any]]] = deque()
        self._lock = threading.Lock()
        # 挂起队列快照路径（可选）：None = 纯内存零持久化（既有调用方零破坏）。
        self.pending_path: Optional[Path] = (
            Path(pending_path) if pending_path is not None else None
        )
        # 鉴权开关只有「凭证表是否为 None」这一个事实来源（见文件头 AUTH 注释）：
        #   None → 关闭；任何 dict（含空 dict）→ 启用且强制。
        self._auth_enabled: bool = seat_credentials is not None
        self._seat_credentials: Dict[str, str] = _sanitize_seat_credentials(
            seat_credentials
        )
        # 启动加载盘上快照（构造期调用，无并发）：坏 JSON/坏结构抛
        # PendingSnapshotCorrupt 拒绝启动（fail-visible，坏账不静默当空）。
        if self.pending_path is not None:
            self._load_pending_snapshot()

    # ---------------- 鉴权 ----------------

    @property
    def auth_enabled(self) -> bool:
        """鉴权是否启用：仅当构造时传入了非 None 的凭证表。"""
        return self._auth_enabled

    def check_seat_credential(self, seat_name: str, token: str) -> Optional[str]:
        """校验一组席位凭证：通过返回 None，失败返回 AUTH_ERR_* 原因码。

        规则（失败即拒绝 / closed on failure，任何异常输入都落到拒绝分支）：
        - 鉴权未启用：恒返回 None（默认关闭，调用方零改动）；
        - 席位头缺失 → AUTH_ERR_SEAT_MISSING；
        - 席位名未登记 → AUTH_ERR_SEAT_UNKNOWN（**先判存在性再比 token**：
          未登记席位不进入 token 比较，既避免"用 token 结果反推席位是否存在"
          的侧信道，也让审计能把"冒充陌生席位"与"凭证填错"分开聚合）；
        - token 头缺失/空串 → AUTH_ERR_TOKEN_MISSING；
        - token 不一致 → AUTH_ERR_TOKEN_INVALID（hmac.compare_digest 定长比较，
          不用 ==，防时序侧信道；编码成 bytes 以支持非 ASCII token）。
        本方法不记录也不返回任何 token 内容。
        """
        if not self._auth_enabled:
            return None
        seat = seat_name if isinstance(seat_name, str) else ""
        presented = token if isinstance(token, str) else ""
        if not seat:
            return AUTH_ERR_SEAT_MISSING
        if seat not in self._seat_credentials:
            return AUTH_ERR_SEAT_UNKNOWN
        if not presented:
            return AUTH_ERR_TOKEN_MISSING
        expected = self._seat_credentials[seat]
        if not hmac.compare_digest(expected.encode("utf-8"),
                                   presented.encode("utf-8")):
            return AUTH_ERR_TOKEN_INVALID
        return None


    # ---------------- 席位注册 ----------------

    def register(self, name: str, level: str) -> None:
        """注册席位（name, level）。重复注册以最后一次为准。

        注册同时为席位建立内存收件箱（见 inbox），route 投递以收件箱存在为准。
        注册成功后补投该席位此前挂起的未投递消息（FIFO 逐条入收件箱），
        并在账本追加一行动作=redelivered 的留痕（目标、条数、ts）——
        只追加新行，**不改写**既有 routed record（原 delivered=false 如实保留）。
        TODO(领域实现): 席位身份凭证（防注册面伪造），当前骨架仅按名单校验。
        """
        if level not in REPORT_LEVELS:
            raise ValueError(f"非法席位层级: {level}（须为 {REPORT_LEVELS} 之一）")
        with self._lock:
            self._seats[name] = level
            inbox = self._inboxes.setdefault(name, [])
            # 补投必须在锁内并入收件箱：挂起消息先于任何新投递，FIFO 不被插队。
            redelivered = self._take_pending_locked(name)
            inbox.extend(redelivered)
        # 账本写自行加锁（_append 内部持 self._lock），故须在锁外调用；
        # 单条 redelivered 行汇总条数，避免逐条刷账本。
        if redelivered:
            self._append(self.ledger_path, {
                "kind": "redelivered",
                "ts": time.time(),
                "action": "redelivered",
                "target": name,
                "count": len(redelivered),
            })

    def is_registered(self, name: str) -> bool:
        return name in self._seats

    # ---------------- 汇报路由 ----------------

    def route(self, report: Dict[str, Any], expected_target: str = "") -> Dict[str, Any]:
        """路由一条上行汇报。返回 {"ok": bool, "reason": str, ...}。

        校验顺序：
        a) source 必须已注册，伪署名进死信；
        b) report.level 必须是合法层级，且不得低于 source 自身 level（禁止向下汇报）；
        c) level=principal 直发仅允许白名单席位（risk_guard 预警 /
           approval_gate 签准 / tian_feng 归口席），其余席位一律进死信
           并明确拒绝（越级拦截）；
        c2) principal 消息必须带已登记的类别前缀（整改工单 / 升级请示 /
            预警 / 待签），且该类别允许本源席位发送——白名单扩员后
            principal 通道多类并存，须按类别可审计、可分辨性质；
        d) 通过校验的消息追加 jsonl 账本，随后向 expected_target 收件箱
           实际投递（expected_target 为空串或未注册则只记账不投递，
           账本 record 以 delivered 字段如实留痕）；expected_target 非空
           但未注册的消息同时进挂起队列，待该席位 register 时补投
           （见 register / pending_delivery）。死信路径不经过本分支，
           故 DLQ 消息不入挂起队列。
        """
        source = report.get("source")
        level = report.get("level")

        # a) 伪署名：未注册来源一律拒绝并进死信
        if not source or source not in self._seats:
            return self._to_dlq(report, expected_target, f"伪署名：来源席位未注册 source={source!r}")

        # b) 层级合法性 + 禁止向下汇报
        if level not in REPORT_LEVELS:
            return self._to_dlq(report, expected_target, f"非法汇报层级: {level!r}")
        source_level = self._seats[source]
        if REPORT_LEVELS.index(level) < REPORT_LEVELS.index(source_level):
            return self._to_dlq(
                report, expected_target,
                f"禁止向下汇报：source 层级 {source_level} 不得发往更低层级 {level}",
            )

        # c) 越级拦截：principal 直发仅白名单席位
        if level == "principal" and source not in PRINCIPAL_WHITELIST:
            return self._to_dlq(
                report, expected_target,
                f"越级拦截：席位 {source} 层级 {source_level} 不得直发 principal"
                f"（仅 {PRINCIPAL_WHITELIST} 允许）",
            )

        # c2) 类别校验：principal 消息须带已登记类别前缀，且类别允许本源席位
        message_class = classify_message_class(report.get("subject", ""))
        if level == "principal":
            if not message_class:
                return self._to_dlq(
                    report, expected_target,
                    f"principal 消息缺少类别前缀（可审计分类）："
                    f"须为 {sorted(PRINCIPAL_MESSAGE_CLASSES)} 之一",
                )
            allowed_sources = PRINCIPAL_MESSAGE_CLASSES[message_class][1]
            if source not in allowed_sources:
                return self._to_dlq(
                    report, expected_target,
                    f"消息类别越权：类别 {message_class} 仅允许席位 "
                    f"{allowed_sources} 发送，来源 {source}",
                )

        # d) 正常消息追加账本，随后向 expected_target 收件箱实际投递
        delivered = bool(expected_target) and expected_target in self._seats
        record = {
            "kind": "routed",
            "ts": time.time(),
            "expected_target": expected_target,
            "source": source,
            "source_level": source_level,
            # 消息类别随账本留痕，供按类型审计（整改工单 vs 升级请示）
            "message_class": message_class,
            "report": dict(report),
            # 投递留痕：expected_target 为空串或未注册则不投递，账本如实记录
            "delivered": delivered,
        }
        self._append(self.ledger_path, record)
        if delivered:
            # 席位注册只增不删，落账时判定的 delivered 不会与实际投递脱节；
            # 投递失败重试策略挂领域实现（见 inbox docstring）。
            with self._lock:
                self._inboxes.setdefault(expected_target, []).append(dict(report))
        elif expected_target:
            # 目标非空但未注册：挂起等待补投（FIFO）。空串表示「无指定目标」，
            # 无处可投也无处可补，不入队。
            self._enqueue_pending(expected_target, report)
        return {"ok": True, "reason": "", "record_ts": record["ts"]}

    # ---------------- 未投递挂起队列 ----------------

    def pending_delivery(self) -> Dict[str, int]:
        """各目标挂起条数只读快照（target -> 条数）。

        - 返回新建 dict，调用方增删改返回值不影响总线内部挂起队列；
        - 只列出有挂起消息的目标（无挂起的目标不出现，不补 0 行）；
        - 挂起队列默认进程内内存态、不落盘；注入 pending_path 时变更点
          同步落快照（见 _write_pending_snapshot_locked），本方法仍只读。
        """
        with self._lock:
            counts: Dict[str, int] = {}
            for target, _report in self._pending:
                counts[target] = counts.get(target, 0) + 1
            return counts

    def _load_pending_snapshot(self) -> None:
        """启动加载盘上挂起快照进内存队列（构造期调用，无并发）。

        fail-visible 口径（对齐 approval_gate.LedgerCorrupt）：
        - 快照文件不存在：视为首次启动，空队列返回（正常路径，非坏账）；
        - 非法 JSON 或结构不符：抛 PendingSnapshotCorrupt 拒绝启动——
          坏账不静默当空队列，把未知当已知会制造无声漏报。
        """
        try:
            raw = self.pending_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PendingSnapshotCorrupt(
                f"挂起队列快照损坏（非法 JSON）：{self.pending_path} "
                f"第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"
            ) from None
        if not isinstance(data, dict) or not isinstance(data.get("pending"), list):
            raise PendingSnapshotCorrupt(
                f"挂起队列快照结构不符：{self.pending_path} 顶层须为 "
                f'{{"version": ..., "pending": [...]}}'
            )
        entries: Deque[Tuple[str, Dict[str, Any]]] = deque()
        for item in data["pending"]:
            if (not isinstance(item, dict)
                    or not isinstance(item.get("target"), str)
                    or not isinstance(item.get("report"), dict)):
                raise PendingSnapshotCorrupt(
                    f"挂起队列快照条目结构不符：{self.pending_path} "
                    f'条目须为 {{"target": str, "report": dict}}'
                )
            entries.append((item["target"], dict(item["report"])))
        self._pending = entries

    def _write_pending_snapshot_locked(self) -> None:
        """整队列落快照（原子写：tmp + os.replace）；调用方须已持有 self._lock。

        取舍：
        - pending_path 为 None：零持久化（既有语义），直接返回，零 IO；
        - 写失败（建目录/写 tmp/替换失败/序列化失败）：LOG.warning 留痕但
          **不上抛**——快照是兜底，不能反过来打死投递主路径（漏报比重复
          危险，但快照失败再阻断投递就是把兜底变成新故障源）；故障消失后
          下一笔变更点自然恢复落盘；
        - 整队列一个快照：队列有界（PENDING_DELIVERY_MAX），写整份最简单
          且与丢最旧语义天然一致（超限后的快照自动只含最新队列）；
        - 单写者口径：本方法在 self._lock 内串行执行，一个 pending_path
          归一个总线进程所有（与「重启恢复」场景一致），跨进程并发写不
          在本工单范围，故不再叠 file_lock。
        """
        if self.pending_path is None:
            return
        tmp_path = self.pending_path.with_name(self.pending_path.name + ".tmp")
        payload = {
            "version": PENDING_SNAPSHOT_VERSION,
            "pending": [
                {"target": target, "report": dict(report)}
                for target, report in self._pending
            ],
        }
        try:
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            # 耐久契约统一（WO-LEGION-S18-03）：tmp flush+fsync 后再
            # replace，与 WAL/主状态/签准账本同一口径（断电窗口防护）。
            with tmp_path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.pending_path)
        except Exception as exc:  # noqa: BLE001 - 快照兜底不得阻断投递主路径
            logger.warning(
                "挂起队列快照写入失败（投递主路径不受影响）：path=%s error=%s",
                self.pending_path, exc,
            )
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass  # tmp 残留清理失败不影响主路径（下次覆盖写）

    def _enqueue_pending(self, target: str, report: Dict[str, Any]) -> None:
        """挂起一条未投递消息；超限时丢弃最旧并留痕。

        超限策略（closed on failure 语义）：队列达到 PENDING_DELIVERY_MAX 时
        先丢弃队首（最旧）一条再入队新消息，同时 logger.warning 并在账本
        追加一行 action=dropped 留痕（含 target/limit/dropped/ts）——
        宁可让最旧消息显性丢失并留痕，也不静默无界增长撑爆内存；
        被丢弃消息的原始 routed record 保留（delivered=false 仍可审计）。
        注入 pending_path 时，丢最旧+入队在锁内一次落快照（整队列口径，
        超限丢弃与新增同帧同步，见 _write_pending_snapshot_locked）。
        TODO(领域实现): 按优先级淘汰策略（当前仅按入队时间）。
        """
        dropped = 0
        with self._lock:
            while self._pending and len(self._pending) >= PENDING_DELIVERY_MAX:
                self._pending.popleft()
                dropped += 1
            self._pending.append((target, dict(report)))
            # 变更点同步落快照（含丢最旧）：失败仅 warning，不阻断。
            self._write_pending_snapshot_locked()
        if not dropped:
            return
        logger.warning(
            "挂起队列超限：上限 %s，丢弃最旧 %s 条后挂起 target=%s",
            PENDING_DELIVERY_MAX, dropped, target,
        )
        self._append(self.ledger_path, {
            "kind": "pending_delivery",
            "ts": time.time(),
            "action": "dropped",
            "target": target,
            "dropped": dropped,
            "limit": PENDING_DELIVERY_MAX,
            "reason": "pending_delivery_overflow",
        })

    def _take_pending_locked(self, target: str) -> List[Dict[str, Any]]:
        """取走某目标全部挂起消息（FIFO 顺序返回）；调用方须已持有 self._lock。

        取走后内存队列变更，同步落快照（注入 pending_path 时）：已补投的
        消息不得在总线重启后复活成重复投递。
        """
        if not self._pending:
            return []
        kept: Deque[Tuple[str, Dict[str, Any]]] = deque()
        taken: List[Dict[str, Any]] = []
        while self._pending:
            pending_target, pending_report = self._pending.popleft()
            if pending_target == target:
                taken.append(pending_report)
            else:
                kept.append((pending_target, pending_report))
        self._pending = kept
        # 变更点同步落快照：失败仅 warning，不阻断补投主路径。
        self._write_pending_snapshot_locked()
        return taken

    # ---------------- 收件箱 ----------------

    def inbox(self, name: str) -> List[Dict[str, Any]]:
        """读取指定席位收件箱：已投递消息的 FIFO 副本（先投递先读）。

        - 返回内部收件箱的副本，调用方增删返回值不影响总线内部状态；
        - 未注册席位抛 KeyError（对齐 tian_feng._find_issue 的显式抛错风格，
          未注册与空收件箱语义不同：前者是席位不存在，后者是暂无消息）；
        - 收件箱为进程内内存态，不落盘。
        TODO(领域实现): 跨进程投递与投递失败重试策略挂领域实现。
        """
        with self._lock:
            if name not in self._inboxes:
                raise KeyError(f"未注册席位: {name}")
            return [dict(message) for message in self._inboxes[name]]

    # ---------------- 状态点归集 ----------------

    def collect_status_point(self, point: Dict[str, Any]) -> Dict[str, Any]:
        """归集一条状态点：只登记六字段，缺失如实存 None，不造数。

        板块对 OS 是黑盒——只收「谁·在干什么·干到哪·产出在哪」，
        不解析板块内部语义，不造第二账。
        """
        normalized: Dict[str, Any] = {
            field: point.get(field) for field in STATUS_POINT_FIELDS
        }
        agent = normalized.get("agent")
        if not agent:
            # 无归属席位的状态点无法归集到任何人头上，如实拒绝（不代填 agent）。
            return {"ok": False, "reason": "状态点缺失 agent 字段，无法归集"}

        with self._lock:
            self._status_points[agent] = normalized
        return {"ok": True, "agent": agent}

    def board(self, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """归集视图：按 agent 保留最新一条状态点，附过期标注增量字段。

        行级契约（既有键零删改，逐键断言由 tests/test_bus_stale.py 守卫）：
        - 既有六字段 "agent/platform/doing/progress/artifact/ts" 一个不少、
          不改名（STATUS_POINT_FIELDS，逐键从原始状态点重建副本）；
        - 既有增量键 "stale": bool：ts 距 now 严格超过 STATUS_STALE_TTL_S
          （占位 300 秒，待真实心跳基线标定）即 stale；
        - ts 缺失/不可解析：如实 stale=True 并附增量字段 "stale_reason"，
          取值只允许 STALE_REASON_* 常量（ts_missing / ts_unparseable）——
          最小侵入方案：原因随点走，不引入 board 级 data_gaps 结构；
          正常过期不带 stale_reason（与既有语义一致，不新增噪音字段）。
        - now 可注入（缺省取 time.time()）便于测试；既有零参调用方不受影响；
        - 本波只做 stale 标注，不做席位剔除——
          挂账：断连席位剔除策略挂领域实现（对接督办口径后另定）。

        契约版本不在行内：BOARD_CONTRACT_VERSION 挂在 HTTP /board 信封层
        （见 create_app），因为版本是"响应整体"的属性而非单个状态点的属性；
        board() 返回类型与每行键集合保持原样，直接吃本 list 的内部调用方
        （如 os_commander.digest）无需改动。
        """
        moment = time.time() if now is None else now
        with self._lock:
            out: List[Dict[str, Any]] = []
            for point in self._status_points.values():
                row = dict(point)
                stale, reason = _stale_state(row.get("ts"), moment)
                row["stale"] = stale
                if reason:
                    row["stale_reason"] = reason
                out.append(row)
            return out

    # ---------------- 死信回捞 ----------------

    def dead_letters(self) -> List[Dict[str, Any]]:
        """当前死信副本（只读视图，改动返回值不影响死信文件）。"""
        with self._lock:
            return self._read_dlq_locked()

    def requeue_dead_letter(self, index: int, *, by: str) -> Dict[str, Any]:
        """回捞死信：取出第 index 条死信，重新走完整 route 校验。

        - by 仅限中台三席（zhuge_liang/tian_feng/zhao_yun）或 principal，
          其余（含 risk_guard/approval_gate 及未知席位）一律拒绝；
        - 回捞消息重新过 route 四道校验：伪署名、越级照拦，
          再次被拒则重新入死信（回捞仅人工触发，不自动重试，无死循环）；
        - 每次回捞（无论成败）写 bus_ledger.jsonl 审计行（动作=requeue、by、结果）。

        TODO(领域实现): 回捞操作者身份凭证校验（依赖席位凭证，见 register 的 TODO）。
        """
        if by not in REQUEUE_AUTHORIZED:
            result = {
                "ok": False,
                "reason": f"无权回捞：by={by!r} 不在许可名单 {REQUEUE_AUTHORIZED}",
            }
            return self._audit_requeue(index, by, result, report=None)

        with self._lock:
            entries = self._read_dlq_locked()
            valid = isinstance(index, int) and 0 <= index < len(entries)
            entry = entries.pop(index) if valid else None
            if valid:
                self._rewrite_dlq_locked(entries)

        if entry is None:
            result = {
                "ok": False,
                "reason": f"死信序号无效: index={index}（当前共 {len(entries)} 条）",
            }
            return self._audit_requeue(index, by, result, report=None)

        # 锁外重走完整 route 校验（route 内部自行加锁），
        # 被拒消息经 _to_dlq 重新入死信，不会自动再捞。
        route_result = self.route(entry["report"], entry.get("expected_target", ""))
        return self._audit_requeue(index, by, route_result, report=entry["report"])

    def _audit_requeue(self, index: int, by: str, result: Dict[str, Any],
                       report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """回捞审计：无论成败追加一行账本（动作=requeue、by、结果）。"""
        self._append(self.ledger_path, {
            "kind": "requeue",
            "ts": time.time(),
            "action": "requeue",
            "by": by,
            "dlq_index": index,
            "ok": bool(result.get("ok")),
            "reason": result.get("reason", ""),
            "report": dict(report) if report is not None else None,
        })
        out = dict(result)
        out["requeued_by"] = by
        out["dlq_index"] = index
        return out

    def _read_dlq_locked(self) -> List[Dict[str, Any]]:
        """读取死信文件为列表（调用方须已持有 self._lock）。"""
        if not self.dlq_path.exists():
            return []
        lines = self.dlq_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _rewrite_dlq_locked(self, entries: List[Dict[str, Any]]) -> None:
        """回捞取走条目后重写死信文件（调用方须已持有 self._lock）。"""
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
        self.dlq_path.write_text(text, encoding="utf-8")

    # ---------------- 内部：jsonl 追加 ----------------

    def _append(self, path: Path, record: Dict[str, Any]) -> None:
        """追加一行 jsonl；目录不存在则创建。

        锁关系（工位AC 接锁说明）：
        - 进程内互斥由既有 self._lock 保证：所有账本/死信写路径在
          self._lock 临界区内串行，总线状态（席位/状态点/收件箱）与
          账本写共用该锁的既有语义不变；
        - 跨进程行级互斥由 file_lock 边车锁（<账本名>.lock）保证，
          与 S8 工位Z pending_reports 队列同款选择依据：jsonl 只追加
          不重写，行级互斥只需「打开→追加→关闭」临界区排他，边车
          锁文件开销最小且不污染账本本体；
        - file_lock 在 self._lock 内进入：进程内同时至多一个线程处于
          临界区，file_lock 的同进程重入登记表（_HELD_LOCK_PATHS）
          检查-登记不会并发穿透；ledger 与 dlq 为不同路径，互不重入。
        """
        sidecar = path.parent / (path.name + ".lock")
        with self._lock:
            with file_lock(sidecar):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _to_dlq(self, report: Dict[str, Any], expected_target: str,
                reason: str) -> Dict[str, Any]:
        """拒绝条目进死信，附明确拒绝原因。"""
        self._append(self.dlq_path, {
            "kind": "dlq",
            "ts": time.time(),
            "expected_target": expected_target,
            "reason": reason,
            "report": dict(report),
        })
        return {"ok": False, "reason": reason, "dlq": True}


def create_app(bus: MessageBus) -> FastAPI:
    """FastAPI 应用工厂。死信与账本路径由 bus 注入，默认落 data/。

    鉴权契约（详见文件头 AUTH 段）：
    - `bus.auth_enabled` 为 False（构造时 seat_credentials=None）→ 一切照旧，
      中间件直接放行，行为与加鉴权前逐字节一致；
    - 为 True → 除 AUTH_EXEMPT_PATHS（仅 /health）外，所有请求必须携带
      AUTH_HEADER_SEAT + AUTH_HEADER_TOKEN 且校验通过，否则 HTTP 401。
      没有「缺头即放过」的分支，也没有可供调用方关闭鉴权的参数。

    为什么用 ASGI 中间件而不是依赖注入（Depends）：
    依赖注入要求**每条路由自己声明**依赖，漏写一条就是一条 fail-open 的路由，
    而"漏写"恰恰是本工单要消灭的失败模式（新增路由默认无鉴权）。
    中间件是单一收口：默认拒绝、白名单显式放行，新路由天然落在受保护侧，
    符合「启用即强制 / closed on failure」。代价是 /docs 与 /openapi.json
    在启用后也需凭证（浏览器直访会 401）——这是有意为之：接口清单本身
    也属于需要知道的人才知道的信息，且 TestClient/内部调用方不受影响。
    """
    app = FastAPI(title="os-legion-message-bus", version="0.1.0-skeleton")

    @app.middleware("http")
    async def enforce_seat_auth(request: Request, call_next):
        """身份层闸门：未启用直接放行；启用后除白名单外一律先验凭证。"""
        if not bus.auth_enabled:
            return await call_next(request)
        if _normalize_auth_path(request.url.path) in AUTH_EXEMPT_PATHS:
            # 探活放行（取舍见 AUTH_EXEMPT_PATHS 注释）：
            # health 只回服务名与路径，不含账本内容与状态数据。
            return await call_next(request)

        # 两个头都取原始值；缺失等价于空串，统一交给 check_seat_credential
        # 判定（失败即拒绝，不存在"其中一个缺失就跳过校验"的短路）。
        error_code = bus.check_seat_credential(
            request.headers.get(AUTH_HEADER_SEAT, ""),
            request.headers.get(AUTH_HEADER_TOKEN, ""),
        )
        if error_code is None:
            return await call_next(request)

        # 审计留痕只记路径与原因码：席位名是请求方可控输入、token 属机密，
        # 都不进日志（token 明文/前缀/长度一律禁止回显）。
        logger.warning("总线鉴权拒绝：path=%s code=%s",
                       _normalize_auth_path(request.url.path), error_code)
        # 401 是身份层失败，不得混入 200+ok=false 的业务拒绝通道（S10 判例）。
        return JSONResponse(
            status_code=401,
            content={"error_code": error_code,
                     "detail": AUTH_ERR_DETAILS[error_code]},
        )

    @app.post("/route")
    def route(payload: Dict[str, Any]) -> Dict[str, Any]:
        report = payload.get("report") or {}
        expected_target = payload.get("expected_target", "")
        return bus.route(report, expected_target)

    @app.post("/status")
    def status(point: Dict[str, Any]) -> Dict[str, Any]:
        return bus.collect_status_point(point)

    @app.get("/board")
    def board() -> Dict[str, Any]:
        # 契约版本挂信封层：纯增量（既有 "points" 键原样保留），
        # 对只读 points 的消费方零破坏；版本描述的是整个响应而非单条状态点。
        return {"contract_version": BOARD_CONTRACT_VERSION,
                "points": bus.board()}

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "message_bus",
                "ledger": str(bus.ledger_path), "dlq": str(bus.dlq_path)}

    return app
