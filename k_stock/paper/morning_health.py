# -*- coding: utf-8 -*-
"""晨批健康自检（WO-QT-20261020-019）。

背景（2026-10-20 今晨真实事故）：automation ``69b51985``（K-STOCK 每日跑批
07:05）**执行了但失败**——``~/.workbuddy/logs/automation.log`` 记
``2026-10-20T00:35:01Z run failed for 69b51985… [CANCELLED] Automation prompt
interrupted: unknown_interrupted``，而仓内**无任何失败痕迹、无告警**，4d/4e
段未完成、1.3 灰度起算顺延，靠人肉翻日志才发现。本模块把「今天该有的产物
有没有」变成**可机器判定**的红黄绿，缺口显性化。

只读契约（三条硬保证）：
  1. 全程只读：文件一律 ``open(..., 'r')`` / ``open(..., 'rb')``；sqlite 一律
     URI ``mode=ro`` 且额外 ``PRAGMA query_only=ON``（不建库、不建表）。
  2. 不写任何生产账：不碰 paper.db、不改shadow 账本、不追加p0_alert_state。
  3. **DRYRUN 语义**：本模块只判不发。推送（告警到飞书/企微）属WO-QT-20261020-015
     待授权单，本单不接线路径、不留推送开关，避免「工具顺手就把告警发出去了」。

fail-open 禁令（核心设计原则）：**「没采到」永远不等于「正常」**。
任何证据源缺失、解析失败、路径不可达，一律落 red 或 yellow 且附
``evidence`` 说明缺什么，绝不静默返回「一切正常」。判据内部异常同样显式落
finding（``severity='red'`` + ``check_error=True``），不吞异常。

判据清单（每条独立可测，互不短路）：

===  ==================  =====================================================
id   名称                口径
===  ==================  =====================================================
H1   影子晨批心跳        shadow/last_run.log 当日有 ``shadow day <date> done``
H2   各通道最新 run 时效  qd / qpzm / qpzm_us / hk 各源最新 poc 产物日期
H3   D-1 决策快照        当日有快照，**或**有明确灰态说明（灰态≠绿）
H4   段完整性            4d/4e 段产物存在性（灰态允许，但须有 note 证据）
H5   数据断线            已知断线源最新日期超期（阈值可配）
H6   产物新鲜度          三查报告 / 日报当日是否产出
===  ==================  =====================================================

H2 前缀口径对齐 trainee._NON_AI_PREFIXES（读取该常量，不复制字面量）：
前缀即 ``poc_<channel>_``，日期戳从**文件名**解析（``poc_qd_20261011_230702.json``
→ ``20261011``），最新件按 (mtime, 文件名) 排序取——mtime 相等时用文件名破平，
理由与 trainee._newest_by_mtime 一致（glob 顺序是文件系统返回序，非字典序）。

H4 段完整性判据的今晨实证（决定了「无文件但有note 证据」=黄 而非 红）：
今晨 HK 段灰态，**零落盘文件**，只有 stdout note。若按「无文件=红」判，会把
「已知灰态」与「真断线」混为一谈——前者只需记账，后者要救火。故判据分三档：
    -有产物文件 → green
    - 无文件 **但** 有账本/日志 note 证据 → yellow（注明「灰态，零落盘」）
    - 无文件且无任何证据 → red（静默失败嫌疑，正是今晨事故形态）

灰态语义（H3/H4共用）：**灰态无样本**——今天没有可判读的决策样本，是事实陈述
不是健康证明。灰态一律最高只给 yellow，且evidence 必须写明「灰态无样本」，
禁当green。

CLI::

    python morning_health.py --date 20261020            # 人读 Markdown
    python morning_health.py --date 20261020 --json     # 机器读契约 JSON
    python morning_health.py --date 20261020 --out r.md  # 落盘（默认只打印）
    python morning_health.py --date 20261020 --ledger p.json  # 换台账（单测/演练）

通道频次台账（WO-QT-20261020-021 接入，``channel_frequency.json``）：
H2/H4/H5 的时效阈值不再只由代码常量决定，而是先读台账声明。三条硬规则——
  1. **停用是显式声明**：只有台账写 ``enabled=false`` 才算停用，显示
     「已停用（原因）」判黄；台账要求同时给 reason/evidence/approved_by。
  2. **停用≠绿**：停用判黄不判绿——该通道确实没有新数据产出。
  3. **pending_mount≠停用**：已实现但未挂载调度（如 HK）维持红，证据里点明
     「接线缺口不是崩溃」，防重复救火；把它记成停用就是把「没做」说成「不做」。
  台账缺失/坏 JSON → 回落代码默认阈值（更严），并单列 H0 黄 finding 说明台账
  故障，**绝不因台账坏了就把红吃掉**。台账里没登记的通道沿用默认，不静默放宽。

G-01/G-02/CH-08 判据增强（WO-QT-20261020-030，全部无需审批的判据口径项）：

G-01 零买入 vs 断供区分（021 根因表 CH-03 §4）：qd/qpzm 通道「闸门收紧零买入」
是风控正确行为（safe=true 才产出），不是断线。台账 channels 条目新增**可选**
``zero_buy`` 声明（``{declared:true, reason, evidence, approved_by}``），声明后
该通道零产出/超期显示「业务零产出（闸门收紧，非断线）」判黄**不判红**，与
「上游断供」（红）明确分开。未声明的通道沿用现状（超期照旧判红），缺省不放宽。

G-02 补跑件口径（CH-04，终裁已落）：H2 evidence 增列产物 mtime（**留痕用途
不变，不再是判读基准**）。补跑件新鲜度判读基准 = **业务日期**（文件内记录的
应覆盖交易日）——业务日期超期即照常判黄/红，落盘 mtime 新**不豁免**（防陈旧
数据靠补跑「洗白」成新鲜）。文件名/文件内均无业务日期的件维持显式黄态
（不可判定≠绿，不造数）。终裁来源：老田 2026-10-21 07:12 圈选「⑧A」批准
（WO-QT-20261020-068），口径版本 CALIBER_VERSION = "v20261021-user-approved"。

CH-08 内容级检测（假绿根治）：H5 日期判绿只证明「件在且新」，不证明「内容
可用」——实测 xunyou_brief_20261020.json 日期当日但 sections 三段全 DEGRADED。
故对最新产物 JSON 追加内容级扫描：任一 ``*.status=="DEGRADED"`` 或
``cutover_verdict`` 以 ``BLOCK_`` 开头 → 判黄「形式在、内容降级」（证据引用
具体字段），不得再假绿；正常内容仍绿。

退出码：0=green、1=yellow、2=red、3=工具自身失败（bad input / 不可读）。

严重度序：``red > yellow > green``，整体 ``status`` 取所有 finding 里最高级
（同severity 时按 check_id 排序稳定输出）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import logging

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

__all__ = [
    "check_morning",
    "render_markdown",
    "render_human",
    "main",
    "SEVERITY_ORDER",
    "CHANNELS",
    "HealthPaths",
    "normalize_date",
    "load_frequency_ledger",
    "FREQUENCY_LEDGER_PATH",
]

# 严重度序（index 越大越严重；整体 status 取max）
SEVERITY_ORDER = ("green", "yellow", "red")

# 口径版本（终裁登记，WO-QT-20261020-068）。
# 补跑件新鲜度判读基准 = **业务日期**（文件内记录的应覆盖交易日），非落盘
# mtime 实际产出时间。mtime 证据保留仅作留痕，不再参与判读——业务日期超期
# 照常判黄/红，防陈旧数据靠补跑「洗白」成新鲜；无业务日期的件维持显式黄态
# （insufficient/unknown，不造数）。
# 终裁来源：老田 2026-10-21 07:12 圈选「⑧A」批准。
CALIBER_VERSION = "v20261021-user-approved"

# 通道频次台账（WO-QT-20261020-021）。**数据文件不是硬编码**——频次口径要能被
# 显式声明、可被复核、可被老田改；改频次不该改代码。
# 台账缺失/坏 JSON → 一律回落到下面的代码默认值（fail-open：台账坏了不许静默放宽）。
FREQUENCY_LEDGER_PATH = HERE / "channel_frequency.json"

# 频次 → 默认时效阈值（天）。台账显式给了 max_age_days/stale_days 时以台账为准。
# 周频给 7 天（月频给 31），让「按周产出的东西」不因日频阈值被判红——这是**口径
# 修正不是掩盖**：红噪声本身就是要治的病。
_FREQUENCY_STALE_DAYS = {"daily": 1, "trading_day": 2, "weekly": 7, "monthly": 31}

# 通道 → poc 前缀。口径必须与trainee._NON_AI_PREFIXES 对齐，
# 不硬编码完整列表字面量之外的东西；读取时以trainee 常量为准做校验。
CHANNELS = ("qd", "qpzm", "qpzm_us", "hk")

# 通道 → 前缀（trainee._NON_AI_PREFIXES 的拆解：poc_qpzm_us → poc_qpzmus_，
# 注意文件名前缀里下划线被吞了，故显式列出映射而非拼接）
CHANNEL_PREFIXES = {
    "qd": "poc_qd_",
    "qpzm": "poc_qpzm_",
    "qpzm_us": "poc_qpzmus_",
    "hk": "poc_qpzmhk_",
}

# H5 已知断线源：name → (产物 glob 相对路径, 默认超期天数阈值)
# 阈值可由调用方 feeds 覆盖（数据源修好后调宽即可，不必改码）。
# 用 glob 而非固定「latest」文件名：仓内命名是日期戳滚动（xunyou_brief_20261020.json），
# 指向一个并不存在的 `*_latest.json` 等于把「没采到」伪装成断线红，判据失真。
DEFAULT_FEEDS = {
    "板块日线": ("quant_backtest/reports/sector_flow_latest.json", 2),
    "雪球": ("quant_backtest/reports/xunyou_brief_*.json", 2),
}

# H6 产物新鲜度：name → 产物相对路径（glob 时用）
DEFAULT_ARTIFACTS = {
    "xunyou_brief": "quant_backtest/reports/xunyou_brief_*.md",
    "shadow_daily_report": "agent_platform/data/shadow/daily_report_*.md",
}

_DATE_IN_NAME = re.compile(r"(\d{8})")
_DATE_IN_NAME_DASHED = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _date_from_name(name: str) -> str | None:
    """从文件名抠 ``YYYYMMDD``，兼容带横线写法。

    仓内两种命名并存：``poc_qd_20261011_230702.json``（日期戳）与
    ``daily_report_2026-10-19.md``（横线日期）。只认前者会把「有产物」误判成
    「无日期戳不可判定」，白挂一个黄——命名差异不该变成健康缺口。
    """
    m = _DATE_IN_NAME.search(name)
    if m:
        return m.group(1)
    m = _DATE_IN_NAME_DASHED.search(name)
    if m:
        return "".join(m.groups())
    return None


def _sev_rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev)


def _worst(severities) -> str:
    """整体 status = 所有 finding 里最高severity（空表 → green）。"""
    worst = "green"
    for s in severities:
        if _sev_rank(s) > _sev_rank(worst):
            worst = s
    return worst


def normalize_date(date) -> str:
    """归一化日期入参为 ``YYYYMMDD`` 字符串。

    接受 ``'20261020'`` / ``'2026-10-20'`` / ``datetime``。
    其它一律抛 ``ValueError``（closed on failure：坏输入不许悄悄按今天算——
    按今天算会伪造出「今日健康」的假绿）。
    """
    if isinstance(date, datetime):
        return date.strftime("%Y%m%d")
    if not isinstance(date, str):
        raise ValueError(
            "date 必须是 'YYYYMMDD'/'YYYY-MM-DD' 字符串或 datetime，收到 %r" % (date,)
        )
    s = date.strip().replace("-", "").replace("/", "")
    if not _DATE_IN_NAME.fullmatch(s):
        raise ValueError("date 格式非法: %r（期望 YYYYMMDD 或 YYYY-MM-DD）" % (date,))
    # 校验是真日期（20261332 这类要拒）
    datetime.strptime(s, "%Y%m%d")
    return s


def _read_text(path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> str:
    """只读读文本；不存在/不可读抛 ``OSError`` 由上层落finding。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(max_bytes)


def load_frequency_ledger(path=None) -> dict:
    """读通道频次台账。**读不到就回落到空 dict，绝不抛异常、绝不静默放宽。**

    返回结构 ``{"channels": {...}, "feeds": {...}, "artifacts": {...},
    "_source": str|None, "_error": str|None}``。

    fail-open 要点：台账是「豁免凭据」，它坏了若静默按「无豁免」处理是安全的
    （回到更严的默认阈值）；若台账坏了却被当成「全部正常」才是事故。故此处
    **只记 ``_error`` 不抛**——调用方拿不到台账时按默认判，红不会被吃掉。
    """
    p = Path(path) if path else FREQUENCY_LEDGER_PATH
    out = {
        "channels": {},
        "feeds": {},
        "artifacts": {},
        "_source": str(p),
        "_error": None,
    }
    try:
        raw = _read_text(p)
    except OSError as e:
        out["_error"] = "台账不可读: %s（回落默认阈值，不放宽）" % e
        return out
    try:
        obj = json.loads(raw)
    except ValueError as e:
        out["_error"] = "台账坏 JSON: %s（回落默认阈值，不放宽）" % e
        return out
    if not isinstance(obj, dict):
        out["_error"] = "台账根节点非对象（回落默认阈值，不放宽）"
        return out
    for section in ("channels", "feeds", "artifacts"):
        sec = obj.get(section)
        if isinstance(sec, dict):
            out[section] = sec
    return out


def _ledger_entry(ledger: dict, section: str, key: str) -> dict:
    """取台账条目；缺失/非 dict 一律返回 ``{}``（调用方按默认处理）。"""
    sec = (ledger or {}).get(section) or {}
    v = sec.get(key)
    return v if isinstance(v, dict) else {}


def _ledger_disabled(entry: dict) -> tuple[bool, str]:
    """是否**显式停用**，返回 ``(是否停用, 停用说明)``。

    只有 ``enabled is False``（显式布尔）才算停用。``pending_mount=True`` 的
    条目**不算停用**——那是「已实现未接线」，仍按红处理。把两者混同等于把
    「没做」记成「不做」，是掩盖不是修正。
    """
    if entry.get("enabled") is not False:
        return False, ""
    reason = str(entry.get("reason") or entry.get("note") or "未注明原因")
    by = str(entry.get("approved_by") or "").strip()
    return True, "已停用（显式声明）：%s%s" % (
        reason,
        ("；停用拍板人：%s" % by) if by else "",
    )


def _ledger_stale_days(
    entry: dict, fallback: int, *, fallback_is_explicit: bool = False
) -> int:
    """台账声明的时效阈值；优先级从高到低：

    1. 台账**显式数字**（``stale_days`` / ``max_age_days``）——最具体的声明，最高优先；
    2. 调用方**显式传入**的阈值（``fallback_is_explicit=True``）——注入即覆盖，
       不能被台账的 frequency 静默压掉（否则「调宽阈值看降级」这类用法会失灵）；
    3. 台账 ``frequency`` 折算（weekly=7 / monthly=31……）——让周频数据源不被
       日频阈值误判红；
    4. ``fallback``（代码默认值，最严）。

    第2 条与第 1 条的次序是本函数存在的关键：台账是「常态口径」，调用方显式
    入参是「本次覆盖」，覆盖不该被常态悄悄吃掉。
    """
    for k in ("stale_days", "max_age_days"):
        v = entry.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    if fallback_is_explicit:
        return fallback
    freq = str(entry.get("frequency") or "").strip().lower()
    if freq in _FREQUENCY_STALE_DAYS:
        return _FREQUENCY_STALE_DAYS[freq]
    return fallback


def _open_ro(path: Path) -> sqlite3.Connection:
    """sqlite 只读打开：URI ``mode=ro`` + ``query_only=ON`` 双保险。

    库不存在时**打开即失败**（不新建文件）—— 库缺失要落 finding，不许当空账本。
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.execute("PRAGMA query_only=ON")
    return con


@dataclass
class HealthPaths:
    """全部可读路径（注入点：单测传tmp 目录，生产传默认值）。"""

    repo_root: Path = field(default_factory=lambda: REPO_ROOT)
    shadow_dir: Path | None = None
    kstock_data: Path | None = None
    reports_dir: Path | None = None
    automation_runs: Path | None = None
    paper_db: Path | None = None
    automation_log: Path | None = None

    def __post_init__(self):
        r = Path(self.repo_root)
        self.repo_root = r
        if self.shadow_dir is None:
            self.shadow_dir = r / "agent_platform" / "data" / "shadow"
        if self.kstock_data is None:
            self.kstock_data = r / "k_stock" / "data"
        if self.reports_dir is None:
            self.reports_dir = r / "quant_backtest" / "reports"
        if self.automation_runs is None:
            self.automation_runs = r / "automation_runs"
        if self.paper_db is None:
            self.paper_db = Path(self.kstock_data) / "paper.db"
        if self.automation_log is None:
            self.automation_log = Path.home() / ".workbuddy" / "logs" / "automation.log"


@dataclass
class _F:
    """一条 finding。字段即契约（JSON 直出）。"""

    check_id: str
    severity: str
    title: str
    evidence: str
    action: str
    check_error: bool = False

    def to_dict(self) -> dict:
        d = {
            "check_id": self.check_id,
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "action": self.action,
        }
        if self.check_error:
            d["check_error"] = True
        return d


def _err(check_id: str, title: str, exc: Exception, action: str) -> _F:
    """判据内部异常 → 显式 red finding（fail-visible，绝不静默 pass）。"""
    return _F(
        check_id=check_id,
        severity="red",
        title=title,
        evidence="判据执行异常 %s: %s（证据不可得≠健康，按红处理）"
        % (type(exc).__name__, exc),
        action=action,
        check_error=True,
    )


# ---------------------------------------------------------------------------
# H1 影子晨批心跳
# ---------------------------------------------------------------------------

_SHADOW_DONE = re.compile(r"shadow\s+day\s+(\d{4}-\d{2}-\d{2})\s+done")


def _check_h1(paths: HealthPaths, date: str) -> _F:
    """last_run.log 当日有完成记录。缺=红；只有昨日及更早=黄。"""
    log = Path(paths.shadow_dir) / "last_run.log"
    title = "H1 影子晨批心跳"
    action = "查 automation 69b51985 调度状态与 ~/.workbuddy/logs/automation.log，确认影子段是否真跑"
    if not log.exists():
        return _F(
            "H1",
            "red",
            title,
            "last_run.log 不存在: %s（无心跳证据=调度静默失败嫌疑）" % log,
            action,
        )
    try:
        text = _read_text(log)
    except OSError as e:
        return _err("H1", title, e, "修复 last_run.log 可读性: %s" % e)

    done_dates = _SHADOW_DONE.findall(text)
    if not done_dates:
        return _F(
            "H1",
            "red",
            title,
            "last_run.log 无任何 'shadow day <date> done' 完成记录（len=%d 字节）；"
            "最近内容首行: %r" % (len(text), text.splitlines()[:1]),
            "确认影子跑批是否中途崩溃；无 done 行即未完成，不可当正常" + action,
        )
    last = done_dates[-1]
    if last == _iso(date):
        return _F(
            "H1",
            "green",
            title,
            "last_run.log 记录当日完成: shadow day %s done（累计 %d 条 done 行）"
            % (last, len(done_dates)),
            "无需动作",
        )
    age = _days_between(last, date)
    if age < 0:
        # 目标日是历史回看日，账本最新心跳比目标日新——不能按「无当日心跳」罚，
        # 但last_run.log 是滚动覆盖的单文件，历史日的 done 行可能已被后续跑批冲掉，
        # 故按「不可回溯核验」给黄并说明，而不是绿。
        return _F(
            "H1",
            "yellow",
            title,
            "目标日 %s 为历史回看日；last_run.log 最新完成日 %s 晚于目标日 %d 天，"
            "该文件为滚动覆盖，历史日心跳不可回溯核验（不可核验≠绿）"
            % (_iso(date), last, -age),
            "历史日回看请另用 shadow_ledger.jsonl 的 date 字段交叉核对；"
            "滚动日志不保留历史属已知限制",
        )
    if age <= 1:
        return _F(
            "H1",
            "yellow",
            title,
            "last_run.log 最新完成日为 %s，距目标日 %d 天（无当日心跳）" % (last, age),
            "确认今日影子段是否漏跑；连续 2 日无心跳按红处理并升级 " + action,
        )
    return _F(
        "H1",
        "red",
        title,
        "last_run.log 最新完成日为 %s，距目标日 %d 天（>1 日阈值）" % (last, age),
        "影子链已停摆，按红升级: " + action,
    )


# ---------------------------------------------------------------------------
# H2 各通道最新 run 时效
# ---------------------------------------------------------------------------


def _iso(date: str) -> str:
    """'20261020' → '2026-10-20'（用于比对 last_run.log 那种带横线的日期）。"""
    return "%s-%s-%s" % (date[0:4], date[4:6], date[6:8])


def _days_between(older_iso: str, newer: str) -> int:
    """older 与 newer（均 YYYYMMDD）相差天数；older 晚于 newer 时返回负数。"""
    a = datetime.strptime(older_iso, "%Y-%m-%d")
    b = datetime.strptime(_iso(newer), "%Y-%m-%d")
    return (b - a).days


def _newest_prefix_file(data_dir: Path, prefix: str) -> Path | None:
    """按 (mtime_ns, 文件名) 取该前缀最新件。

    mtime 相等时用文件名破平——glob 顺序是文件系统返回序（NTFS 非字典序），
    max() 遇并列返回首个，会随机选中。与 trainee._newest_by_mtime 同因同解。
    """
    if not data_dir.exists():
        return None
    cands = [p for p in data_dir.glob(prefix + "*.json") if p.is_file()]
    if not cands:
        return None
    return max(cands, key=lambda p: (p.stat().st_mtime_ns, p.name))


def _verify_prefix_alignment() -> str | None:
    """校验本模块的CHANNEL_PREFIXES 与 trainee._NON_AI_PREFIXES 口径一致。

    不一致就返回说明文本（落 finding 证据），避免两处口径悄悄漂移。
    读不到 trainee 模块（如裁剪部署）返回 None，不因此误报红。
    """
    try:
        sys.path.insert(0, str(HERE))
        import trainee  # noqa: WPS433

        declared = tuple(trainee._NON_AI_PREFIXES)
    except Exception:
        return None
    mine = tuple(CHANNEL_PREFIXES[c] for c in CHANNELS) + ("poc_pf_", "poc_adapter_")
    missing = [p for p in mine if p not in declared]
    if missing:
        return (
            "本模块前缀 %s 未出现在 trainee._NON_AI_PREFIXES=%s，"
            "口径已漂移需对齐" % (list(missing), list(declared))
        )
    return None


def _zero_buy_decl(entry: dict) -> dict | None:
    """取台账 ``zero_buy`` 声明（G-01）。未声明或非 dict/无 declared → None。

    zero_buy 语义：通道在途但上游零买入（闸门收紧是风控正确行为，safe=true 才
    产出），不是断线。声明后零产出/超期判黄不判红；**未声明沿用现状判红**，
    缺省不放宽。
    """
    zb = entry.get("zero_buy")
    if isinstance(zb, dict) and zb.get("declared"):
        return zb
    return None


def _zero_buy_evidence(zb: dict) -> str:
    """把 zero_buy 声明拼成证据串（声明必须留痕：reason/evidence/拍板人）。"""
    return "声明：%s；证据：%s%s" % (
        zb.get("reason") or "未注明原因",
        zb.get("evidence") or "未附证据",
        ("；拍板人：%s" % zb["approved_by"]) if zb.get("approved_by") else "",
    )


def _check_h2(
    paths: HealthPaths,
    date: str,
    stale_days: int = 1,
    ledger: dict | None = None,
    stale_days_explicit: bool = False,
) -> list[_F]:
    """各通道最新 poc 产物日期。缺=红；>stale_days=黄；>2*stale_days=红。

    台账接入（WO-QT-20261020-021）：
      · 台账标 ``enabled=false`` 的通道 → 显示「已停用（原因）」，severity=yellow。
        **不是红**（有据可依的决策状态，不是断线事故）；**也不是绿**（该通道确实
        没有新数据产出，停用≠健康，守住 fail-open 禁令）。
      · 台账标周频/月频的通道 → 按其frequency 折算阈值判时效，不因日频阈值误报红。
      · 台账里没有的通道 → 沿用传入默认 stale_days（不静默放宽）。
      · 台账文件缺失/坏 JSON → load_frequency_ledger 返回空 sections，行为退化为
        「默认阈值」，红不会被吃掉。
      · 调用方**显式**传了 stale_days（如单测「调宽阈值看降级」）→ 以调用方为准，
        台账不得静默压掉（见 _ledger_stale_days 的优先级说明）。
    """
    data_dir = Path(paths.kstock_data)
    out: list[_F] = []
    ledger = ledger or {}
    action = "跑对应通道刷新（如 qd_adapter / qpzm_adapter / hk_market / us_market），确认调度是否派发"

    if not data_dir.exists():
        return [
            _F(
                "H2",
                "red",
                "H2 各通道最新 run 时效",
                "k_stock/data 不存在: %s（四个通道产物全不可得）" % data_dir,
                action,
            )
        ]

    drift = _verify_prefix_alignment()

    for ch in CHANNELS:
        prefix = CHANNEL_PREFIXES[ch]
        cid = "H2.%s" % ch
        title = "H2 通道 %s 最新 run" % ch

        entry = _ledger_entry(ledger, "channels", ch)
        disabled, why = _ledger_disabled(entry)
        if disabled:
            # 显式停用：先落「已停用」，不去 glob 产物（无产物是停用的预期结果，
            # 不是断线）。仍标 yellow 而非 green —— 见函数 docstring 的双重理由。
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "%s；停用是台账显式声明（frequency=%s），非断线事故。"
                    "该通道当前无新产物属预期——停用≠健康，故不判绿"
                    % (why, entry.get("frequency") or "未声明"),
                    "如需恢复，从台账改 enabled=true 并补挂调度面",
                )
            )
            continue

        # G-01：台账 zero_buy 声明（可选）。未声明 → None，行为与旧版完全一致。
        zb = _zero_buy_decl(entry)
        eff_stale = _ledger_stale_days(
            entry, stale_days, fallback_is_explicit=stale_days_explicit
        )

        newest = _newest_prefix_file(data_dir, prefix)
        if newest is None:
            if zb:
                # 闸门收紧零买入：上游 safe=false → adapter 契约性 raise 不落盘，
                # 是风控正确行为不是断线。判黄不判红（G-01），与断供红明确分开。
                out.append(
                    _F(
                        cid,
                        "yellow",
                        title,
                        "业务零产出（闸门收紧，非断线）：通道 %s 无任何 %s*.json "
                        "产物于 %s。台账 zero_buy 显式%s"
                        % (ch, prefix, data_dir, _zero_buy_evidence(zb)),
                        "闸门放开（safe=true）后通道自动恢复产出；挂载调度须另行走"
                        "五要素审批，不在本判据范围",
                    )
                )
                continue
            sev = "red"
            ev = "通道 %s 无任何 %s*.json 产物于 %s（缺=红）" % (ch, prefix, data_dir)
            if drift:
                ev += "；" + drift
            if entry:
                ev += "；台账声明 frequency=%s（期望频次非停用）" % (
                    entry.get("frequency") or "未声明"
                )
            out.append(_F(cid, sev, title, ev, action))
            continue
        m = _date_from_name(newest.name)
        if not m:
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "最新件 %s 文件名内无日期戳，无法判定时效（按不可判定=黄，"
                    "禁当绿）" % newest.name,
                    "修命名规范或补解析口径: " + action,
                )
            )
            continue
        latest = m
        age = _days_between(_iso(latest), date)
        # G-02：evidence 增列落盘 mtime——留痕用途不变（终裁后仅留痕，不再是
        # 判读基准：补跑件新鲜度按业务日期判，mtime 新不豁免）。
        mtime_txt = ""
        try:
            mtime_dt = datetime.fromtimestamp(newest.stat().st_mtime)
            mtime_txt = mtime_dt.strftime("%Y-%m-%d %H:%M")
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/morning_health.py", exc)
        mtime_note = ("；落盘 mtime %s" % mtime_txt) if mtime_txt else ""
        thr_note = (
            "台账 frequency=%s → 阈值 %d 天" % (entry.get("frequency"), eff_stale)
            if entry
            else "阈值 %d 天" % eff_stale
        )
        if age <= 0:
            out.append(
                _F(
                    cid,
                    "green",
                    title,
                    "最新件 %s（日期 %s，当日/更新%s；%s）"
                    % (newest.name, latest, mtime_note, thr_note),
                    "无需动作",
                )
            )
        elif age <= eff_stale:
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "最新件 %s（日期 %s），距 %s 已 %d 天（>0 即黄；%s%s）"
                    % (newest.name, latest, date, age, thr_note, mtime_note),
                    "确认通道是否降频运行；若为已知降频请调台账: " + action,
                )
            )
        elif zb:
            # 闸门收紧零买入（G-01）：超期但台账显式声明零买入是风控正确行为。
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "业务零产出（闸门收紧，非断线）：最新件 %s（业务日期 %s，"
                    "距 %s 已 %d 天%s）。台账 zero_buy 显式%s"
                    % (
                        newest.name,
                        latest,
                        date,
                        age,
                        mtime_note,
                        _zero_buy_evidence(zb),
                    ),
                    "闸门放开（safe=true）后通道自动恢复产出；挂载调度须另行走"
                    "五要素审批，不在本判据范围",
                )
            )
        elif age <= 2 * eff_stale:
            out.append(
                _F(
                    cid,
                    "red",
                    title,
                    "最新件 %s（日期 %s），距 %s 已 %d 天（>2 倍阈值=红；%s%s）"
                    % (newest.name, latest, date, age, thr_note, mtime_note),
                    action,
                )
            )
        else:
            out.append(
                _F(
                    cid,
                    "red",
                    title,
                    "最新件 %s（日期 %s），距 %s 已 %d 天（通道停摆；%s%s）"
                    % (newest.name, latest, date, age, thr_note, mtime_note),
                    action,
                )
            )
    return out


# ---------------------------------------------------------------------------
# H3 D-1 决策快照
# ---------------------------------------------------------------------------

_SNAPSHOT_DDL_HINT = "decision_snapshots"


def _check_h3(paths: HealthPaths, date: str, gray_note: str | None) -> _F:
    """当日有决策快照 **或** 明确灰态说明。

    - 有快照 → green
    - 无快照但有灰态说明 → yellow，且 evidence 必含「灰态无样本」（禁当绿）
    - 零落单且无灰态说明 → red（今晨形态：跑了但什么都没落，也没人说明）
    """
    title = "H3 D-1 决策快照"
    cid = "H3"
    db = Path(paths.paper_db)
    action = "查 07:05 跑批 exec 段是否落单；快照随落单事务同生，落单 0 则快照 0"

    n_snap = None
    if db.exists():
        try:
            con = _open_ro(db)
            try:
                tables = {
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if _SNAPSHOT_DDL_HINT not in tables:
                    # 表未建（D-1 快照功能未落地/未启用）→ 这是配置事实而非断线，
                    # 但「无法判定」不能算绿，故按黄并说明。
                    return _F(
                        cid,
                        "yellow",
                        title,
                        "paper.db 无 %s 表（功能未落地），无法核验当日快照；"
                        "未启用≠已核验通过，按黄处理" % _SNAPSHOT_DDL_HINT,
                        "启用 D-1 快照（decision_snapshot.ensure_table）或在本工具登记豁免口径",
                    )
                n_snap = con.execute(
                    "SELECT COUNT(*) FROM %s WHERE substr(captured_at,1,10)=?"  # nosec B608 - 值参数化/内部标识符（已抽查）
                    % _SNAPSHOT_DDL_HINT,
                    (_iso(date),),
                ).fetchone()[0]
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            return _err(cid, title, e, "修复 paper.db 只读访问: %s" % e)
    else:
        return _err(
            cid,
            title,
            FileNotFoundError("paper.db 不存在: %s" % db),
            "账本缺失不可当干净；先确认账本位置: %s" % db,
        )

    if n_snap and n_snap > 0:
        return _F(
            cid, "green", title, "当日快照 %d 条（%s）" % (n_snap, db.name), "无需动作"
        )

    if gray_note:
        return _F(
            cid,
            "yellow",
            title,
            "当日快照 0 条，但有明确灰态说明：「%s」——灰态无样本，"
            "是事实陈述不是健康证明，最高只给黄" % gray_note,
            "确认灰态是否已记账（灰态 note 落 shadow 账本或 automation 日志），"
            "下个工作日补样本",
        )

    return _F(
        cid,
        "red",
        title,
        "当日快照 0 条且无任何灰态说明：零落单又无人说明，"
        "正是今晨静默失败形态（automation 跑了但失败、仓内无痕迹）",
        action,
    )


# ---------------------------------------------------------------------------
# H4 段完整性（4d / 4e）
# ---------------------------------------------------------------------------

# 段 → (产物前缀, glob)。4d=US/青萍落单段，4e=HK 段
# （段号口径取自 docs/ops/2026-10-10_晨检验收单_042-045全链首验.md 与
#   docs/ops/2026-10-10_晨检实录_0712核对.md）。
# 前缀与 glob 分开存：glob 里带 `_` 的前缀不能靠 replace("*","") 反推
# （会把 `poc_qpzmus_*.json` 削成 `poc_qpzmus_.json` 而永远匹配不到）。
SEGMENTS = {
    "4d": ("poc_qpzmus_", "poc_qpzmus_*.json"),
    "4e": ("poc_qpzmhk_", "poc_qpzmhk_*.json"),
}

# 段 → 台账 channels 里的通道名（台账按通道声明频次/停用，段只是通道的别名）。
# 前缀一一对应，映射显式写出不靠推导。
SEGMENT_CHANNEL = {"4d": "qpzm_us", "4e": "hk"}

# 段 note 证据源（相对 repo_root）——灰态 stdout note 可能落在这里
SEGMENT_NOTE_SOURCES = (
    "agent_platform/data/shadow/last_run.log",
    "agent_platform/data/shadow/shadow_ledger.jsonl",
)


def _segment_note_evidence(paths: HealthPaths, date: str, segment: str) -> str | None:
    """在 note 证据源里找当日含该段标识的记录，找不到返回 None。

    只读扫尾部（最多 64KB）足够覆盖 note——note 是当日追加的，行在文件尾。

    段标识按**词边界**匹配而非子串：账本里 run_id 是十六进制（实测
    ``14dad5866f5a`` 里就含子串 ``4d``），子串匹配会把一行无关落单当成
    「4d 段有note 证据」，把该判红的静默失败洗成黄——这正是本判据要抓的东西，
    不能自己先漏一个。
    """
    token = re.compile(r"(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])" % re.escape(segment))
    for rel in SEGMENT_NOTE_SOURCES:
        p = Path(paths.repo_root) / rel
        if not p.exists():
            continue
        try:
            with open(p, "rb") as f:
                try:
                    f.seek(-65536, os.SEEK_END)
                except OSError:
                    f.seek(0)
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        if not token.search(tail):
            continue
        for line in tail.splitlines():
            if token.search(line) and (_iso(date) in line or date in line):
                return "%s: %s" % (rel, line.strip()[:160])
    return None


def _check_h4(
    paths: HealthPaths, date: str, gray_notes: dict | None, ledger: dict | None = None
) -> list[_F]:
    """4d/4e 段产物存在性，三档：文件 / 无文件但有note / 无文件且无证据。

    台账接入（WO-QT-20261020-021）：段号映射到 channels 里的通道名（4d→qpzm_us、
    4e→hk）。台账标 enabled=false 的段显示「已停用（原因）」判黄；标
    pending_mount 的段**维持红**（已实现未接线 ≠ 不做），并在证据里点明，
    免得看板上「hk 红」被误读成「HK 段崩了」而重复救火。
    """
    data_dir = Path(paths.kstock_data)
    notes = gray_notes or {}
    ledger = ledger or {}
    out: list[_F] = []
    for seg, (prefix, pattern) in SEGMENTS.items():
        cid = "H4.%s" % seg
        title = "H4 段完整性 %s" % seg
        action = "跑该段链路（4d=US/青萍落单段，4e=HK 段）并确认调度派发"

        entry = _ledger_entry(ledger, "channels", SEGMENT_CHANNEL.get(seg, ""))
        disabled, why = _ledger_disabled(entry)
        if disabled:
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "%s；停用是台账显式声明，零落盘属预期——停用≠健康，故不判绿" % why,
                    "如需恢复，从台账改 enabled=true 并补挂调度面",
                )
            )
            continue
        pending = ""
        if entry.get("pending_mount"):
            pending = (
                "；台账标 pending_mount（已实现但未挂载调度）→ 本红是**接线缺口**"
                "不是段崩溃，别重复救火"
            )

        note = notes.get(seg) or _segment_note_evidence(paths, date, seg)

        if data_dir.exists():
            files = sorted(p for p in data_dir.glob(pattern) if p.is_file())
            today_hit = None
            for p in files:
                if _date_from_name(p.name) == date:
                    today_hit = p
                    break
            if today_hit:
                out.append(
                    _F(
                        cid,
                        "green",
                        title,
                        "当日段产物存在: %s" % today_hit.name,
                        "无需动作",
                    )
                )
                continue
            latest = _newest_prefix_file(data_dir, prefix)
            if latest is not None:
                latest_date = _date_from_name(latest.name)
                ev = "无当日产物（%s），最新为 %s（日期 %s）" % (
                    pattern,
                    latest.name,
                    latest_date or "无日期戳",
                )
                sev = "yellow"
                act = "确认该段是否降频/灰态: " + action
                if latest_date is None:
                    ev += "，无法判定时效（按黄）"
                if note:
                    ev += "；有 note 证据：「%s」" % note
                    act += "；灰态零落盘，请确认 note 已记账"
                out.append(_F(cid, sev, title, ev, act))
            else:
                ev = "无任何段产物（%s 于 %s）" % (pattern, data_dir)
                if note:
                    ev += "；有 note 证据：「%s」→ 判黄（灰态零落盘）" % note
                else:
                    ev += "，且无任何 note 证据——静默失败嫌疑" + pending
                out.append(_F(cid, "yellow" if note else "red", title, ev, action))
            continue

        # kstock_data 都不存在 → 无文件，走 note 判定
        out.append(
            _F(
                cid,
                "yellow" if note else "red",
                title,
                "kstock_data 不存在: %s（无段产物）%s"
                % (
                    data_dir,
                    ("；有 note 证据：「%s」" % note)
                    if note
                    else "，且无 note 证据" + pending,
                ),
                action,
            )
        )
    return out


# ---------------------------------------------------------------------------
# H5 数据断线（fail-open 禁令的核心落点）
# ---------------------------------------------------------------------------


def _latest_date_in_json(path: Path) -> str | None:
    """从 JSON 里抠日期：依次试 date/trade_date/asof/generated_at/updated_at。"""
    obj = json.loads(_read_text(path, max_bytes=1 << 20))
    if not isinstance(obj, dict):
        return None
    for k in ("date", "trade_date", "asof", "generated_at", "updated_at"):
        v = obj.get(k)
        if isinstance(v, str):
            m = _DATE_IN_NAME.search(v)
            if m:
                return m.group(1)
    return None


def _content_degradation(path) -> str | None:
    """内容级降级检测（CH-08 假绿根治，WO-QT-20261020-030）。

    日期判绿只证明「件在且新」，不证明「内容可用」——实测 xunyou_brief_20261020.json
    日期当日但 sections 三段全 DEGRADED（pool 档未产出 / evidence 打不开库 /
    posterior combined_verdicts.json 不存在），H5 只看日期 → 假绿。

    只读扫描最新产物 JSON：任一 ``*.status == "DEGRADED"``（并尽量引用同级
    reason/detail）或 ``cutover_verdict`` 以 ``BLOCK_`` 开头 → 返回引用具体字段
    的证据串；无命中返回 None（正常内容仍绿，不放宽）。
    解析失败返回 None——日期判定已把不可解析兜成红，此处不重复报错。
    """
    try:
        obj = json.loads(_read_text(Path(path), max_bytes=1 << 20))
    except (ValueError, OSError):
        return None
    hits: list[str] = []

    def walk(node, prefix: str, depth: int) -> None:
        if len(hits) >= 8 or depth > 8:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                kp = "%s.%s" % (prefix, k) if prefix else str(k)
                if isinstance(v, str):
                    vu = v.strip().upper()
                    if k == "status" and vu == "DEGRADED":
                        reason = node.get("reason") or node.get("detail") or ""
                        hits.append(
                            "%s=DEGRADED%s"
                            % (kp, ("（%s）" % reason) if reason else "")
                        )
                    elif k == "cutover_verdict" and vu.startswith("BLOCK_"):
                        hits.append("%s=%s" % (kp, v.strip()))
                elif isinstance(v, (dict, list)):
                    walk(v, kp, depth + 1)
        elif isinstance(node, list):
            for i, v in enumerate(node[:32]):
                walk(v, "%s[%d]" % (prefix, i), depth + 1)

    walk(obj, "", 0)
    if not hits:
        return None
    return "；".join(hits[:8])


def _feed_latest_date(path: Path, pattern: str) -> tuple[str | None, str, Path | None]:
    """返回 ``(最新日期, 证据串, 最新件路径)``。日期优先取JSON 内容，其次取文件名。

    取 JSON 内容为准、文件名兜底：内容里的 date 是业务日期（权威），
    文件名日期戳可能只是落盘批次（陈旧文件被重写时两者会打架）。
    第三返回值是提供该日期的具体文件（glob 形态下才有意义），供 CH-08
    内容级降级检测定位产物；非 glob 形态返回 path 本身。
    """
    if "*" in pattern:
        hits = sorted(p for p in path.parent.glob(path.name) if p.is_file())
        if not hits:
            return (
                None,
                "产物不存在: %s（glob %s 无匹配）" % (path.parent, pattern),
                None,
            )
        # 先按 JSON 内容判（权威），内容读不出的再按文件名（mtime, 名字）破平取新
        dated = []
        for p in hits:
            try:
                d = _latest_date_in_json(p)
            except (ValueError, OSError):
                d = None
            if d is None:
                d = _date_from_name(p.name)
            if d:
                dated.append((d, p.name))
        if not dated:
            return (
                None,
                "产物 %s 存在但无任何可识别日期（内容与文件名皆无）"
                % [p.name for p in hits[:3]],
                None,
            )
        latest, name = max(dated, key=lambda t: (t[0], t[1]))
        latest_path = path.parent / name
        return (
            latest,
            "最新件 %s（日期 %s，共 %d 个候选）" % (name, latest, len(hits)),
            latest_path,
        )
    return _latest_date_in_json(path), "产物 %s" % path.name, path


def _check_h5(
    paths: HealthPaths, date: str, feeds: dict, ledger: dict | None = None
) -> list[_F]:
    """已知断线源：产物缺失或日期超期 → 红。

    **断线必须显式**：产物不存在 = red（不是「没这项业务」= green）。

    台账接入（WO-QT-20261020-021）：
      · ``enabled=false`` → 「已停用（原因）」判黄（不是红；也不是绿）。
      · 未给 max_age_days 时按 ``frequency`` 折算阈值（weekly=7 等），
        让周频数据源不被日频阈值误判红。
      · 台账缺项 → 沿用 feeds 里代码默认阈值，不静默放宽。
    """
    out: list[_F] = []
    ledger = ledger or {}
    for name, (rel, max_age) in sorted(feeds.items()):
        cid = "H5.%s" % name
        title = "H5 数据源 %s" % name
        p = Path(paths.repo_root) / rel
        action = "恢复采集链路；断线不得静默，看板要显式挂红"

        entry = _ledger_entry(ledger, "feeds", name)
        disabled, why = _ledger_disabled(entry)
        if disabled:
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "%s；停用是台账显式声明，产物不更新属预期——停用≠健康，故不判绿"
                    % why,
                    "如需恢复，从台账改 enabled=true 并补挂采集链路",
                )
            )
            continue
        eff_age = _ledger_stale_days(entry, max_age)

        if not p.exists() and "*" not in rel and not p.parent.exists():
            out.append(
                _F(
                    cid,
                    "red",
                    title,
                    "产物目录不存在: %s（缺=断线红，绝不按「没这项业务」当正常）"
                    % p.parent,
                    action,
                )
            )
            continue
        try:
            latest, ev, latest_path = _feed_latest_date(p, rel)
        except (ValueError, OSError) as e:
            out.append(
                _err(cid, title, e, "修复/登记产物格式: %s（解析失败≠正常）" % e)
            )
            continue
        if latest is None:
            out.append(
                _F(
                    cid,
                    "red",
                    title,
                    "%s；无可判定日期（不可判定≠正常，按红）" % ev,
                    action,
                )
            )
            continue
        age = _days_between(_iso(latest), date)
        if age <= eff_age:
            # CH-08 内容级检测：日期绿只证明「件在且新」，不证明「内容可用」。
            # 业务字段含 DEGRADED/BLOCK_* → 判黄「形式在、内容降级」，不得假绿。
            deg = _content_degradation(latest_path) if latest_path else None
            if deg:
                out.append(
                    _F(
                        cid,
                        "yellow",
                        title,
                        "%s，距 %s %d 天（阈值 %d）——但**形式在、内容降级**"
                        "（不得假绿）：%s" % (ev, date, age, eff_age, deg),
                        "内容降级说明上游产出链有缺口，按黄处置；恢复上游产出后"
                        "本判据自动回绿",
                    )
                )
            else:
                out.append(
                    _F(
                        cid,
                        "green",
                        title,
                        "%s，距 %s %d 天（阈值 %d；内容无降级标记）"
                        % (ev, date, age, eff_age),
                        "无需动作",
                    )
                )
        else:
            out.append(
                _F(
                    cid,
                    "red",
                    title,
                    "%s，距 %s 已 %d 天（超阈值 %d 天）= 断线"
                    % (ev, date, age, eff_age),
                    action,
                )
            )
    return out


# ---------------------------------------------------------------------------
# H6 产物新鲜度
# ---------------------------------------------------------------------------


def _check_h6(paths: HealthPaths, date: str, artifacts: dict) -> list[_F]:
    """三查报告/日报当日是否产出，缺=黄（缺产物不至于要救火，但要有账）。"""
    out: list[_F] = []
    for name, rel in sorted(artifacts.items()):
        cid = "H6.%s" % name
        title = "H6 产物新鲜度 %s" % name
        action = "确认该产物是否仍在产出链路内；若已下线请从本工具清单摘除并记账"
        base = Path(paths.repo_root) / rel
        parent = base.parent
        if not parent.exists():
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "产物目录不存在: %s（不可判定=黄）" % parent,
                    action,
                )
            )
            continue
        try:
            hits = sorted(p for p in parent.glob(base.name) if p.is_file())
        except OSError as e:
            out.append(_err(cid, title, e, action))
            continue
        dated = []
        for p in hits:
            d = _date_from_name(p.name)
            if d:
                dated.append((d, p.name))
        if not dated:
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "目录 %s 下无带日期戳的 %s（不可判定=黄）"
                    % (parent.name, base.name),
                    action,
                )
            )
            continue
        latest_date, latest_name = max(dated, key=lambda t: (t[0], t[1]))
        if latest_date == date:
            out.append(
                _F(cid, "green", title, "当日产物存在: %s" % latest_name, "无需动作")
            )
        else:
            age = _days_between(_iso(latest_date), date)
            out.append(
                _F(
                    cid,
                    "yellow",
                    title,
                    "无当日产物，最新 %s（日期 %s），距 %s %d 天"
                    % (latest_name, latest_date, date, age),
                    action,
                )
            )
    return out


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

_UNSET = object()


def check_morning(
    date="20261020",
    *,
    paths: HealthPaths | None = None,
    stale_days=_UNSET,
    feeds: dict | None = None,
    artifacts: dict | None = None,
    gray_notes: dict | None = None,
    ledger: dict | None = None,
    ledger_path=None,
) -> dict:
    """晨批健康自检。返回 ``{date, status, findings[], summary{}}``。

    ``status`` 取全部 finding 里最高 severity（red > yellow > green）。
    纯只读：本函数不做任何写操作，不推送（DRYRUN，推送属 015 待授权单）。

    参数
    ----
    date:      ``YYYYMMDD`` / ``YYYY-MM-DD`` / ``datetime``，非法抛 ``ValueError``。
    paths:     路径注入（单测用 tmp 目录）。默认指向仓内真实只读路径。
    stale_days: H2/H4 黄态天数的**兜底**阈值（默认 1，红 = >2 倍）。台账按通道声明
                了 frequency/stale_days 时以台账为准；**显式传入本参数则以传入值为准**
                （台账不得静默压掉调用方的本次覆盖）。
    feeds:     H5 数据源 {name: (relpath, max_age_days)}，默认 ``DEFAULT_FEEDS``。
    artifacts: H6 产物 {name: glob}，默认 ``DEFAULT_ARTIFACTS``。
    gray_notes:段灰态说明 {seg: note}，注入即视为有账证据（供人工登记/单测）。
    ledger:    频次台账 dict；缺省自动读 ``channel_frequency.json``。读不到/坏
               JSON 一律退化为「默认阈值」（红不被吃掉）。
    ledger_path:台账路径覆盖（单测注入 tmp 台账用）。
    """
    date = normalize_date(date)
    p = paths or HealthPaths()
    feeds = DEFAULT_FEEDS if feeds is None else feeds
    artifacts = DEFAULT_ARTIFACTS if artifacts is None else artifacts
    if ledger is None:
        ledger = load_frequency_ledger(ledger_path)
    stale_explicit = stale_days is not _UNSET
    eff_stale_default = 1 if stale_days is _UNSET else stale_days

    findings: list[_F] = []
    try:
        findings.append(_check_h1(p, date))
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H1", "H1 影子晨批心跳", e, "人工核查 last_run.log"))

    try:
        findings.extend(
            _check_h2(
                p, date, eff_stale_default, ledger, stale_days_explicit=stale_explicit
            )
        )
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H2", "H2 各通道最新 run 时效", e, "人工核查通道产物"))

    try:
        findings.append(_check_h3(p, date, (gray_notes or {}).get("snapshot")))
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H3", "H3 D-1 决策快照", e, "人工核查 paper.db 快照"))

    try:
        findings.extend(_check_h4(p, date, gray_notes, ledger))
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H4", "H4 段完整性", e, "人工核查 4d/4e 段产物"))

    try:
        findings.extend(_check_h5(p, date, feeds, ledger))
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H5", "H5 数据断线", e, "人工核查数据源链路"))

    try:
        findings.extend(_check_h6(p, date, artifacts))
    except Exception as e:  # noqa: BLE001
        findings.append(_err("H6", "H6 产物新鲜度", e, "人工核查报告产物"))

    # 台账自身状态单列一条 finding：台账坏了必须被看见（fail-visible），
    # 但不因此把整体判红——红是给数据断线的，台账故障是另一类问题（判黄，
    # 证据里写明「已回落默认阈值，未放宽」）。
    if ledger.get("_error"):
        findings.append(
            _F(
                "H0",
                "yellow",
                "H0 通道频次台账",
                "%s；台账路径 %s。已回落代码默认阈值（更严），"
                "本次判据未因台账故障而放宽"
                % (ledger["_error"], ledger.get("_source")),
                "修台账文件；停用声明须带 reason+evidence+approved_by",
            )
        )

    findings.sort(key=lambda f: (f.check_id,))
    status = _worst(f.severity for f in findings)
    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_ORDER}
    return {
        "date": date,
        "status": status,
        "summary": {
            "total": len(findings),
            "red": counts["red"],
            "yellow": counts["yellow"],
            "green": counts["green"],
            "check_errors": sum(1 for f in findings if f.check_error),
        },
        "ledger": {
            "source": ledger.get("_source"),
            "error": ledger.get("_error"),
            "channels": sorted((ledger.get("channels") or {}).keys()),
            "feeds": sorted((ledger.get("feeds") or {}).keys()),
        },
        "findings": [f.to_dict() for f in findings],
        "mode": "dryrun",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
_LABEL = {"green": "绿", "yellow": "黄", "red": "红"}


def render_markdown(result: dict) -> str:
    """渲染给老田看的 Markdown（红黄绿一目了然）。"""
    s = result["summary"]
    lines = [
        "# 晨批健康自检 · %s" % _iso(result["date"]),
        "",
        "> 只读自检 · DRYRUN（不推送，推送属 WO-QT-20261020-015 待授权单）· "
        "生成于 %s" % result["generated_at"],
        "",
        "## 总判：%s %s（%s）"
        % (
            _ICON[result["status"]],
            _LABEL[result["status"]],
            "存在缺口，需处置" if result["status"] != "green" else "全绿",
        ),
        "",
        "- 红 %d · 黄 %d · 绿 %d（共 %d 条判据）"
        % (s["red"], s["yellow"], s["green"], s["total"]),
    ]
    if s["check_errors"]:
        lines.append(
            "- ⚠️ 判据执行异常 %d 条（证据不可得已按红处理，非静默通过）"
            % s["check_errors"]
        )
    lines += ["", "## 明细", ""]

    for sev in ("red", "yellow", "green"):
        group = [f for f in result["findings"] if f["severity"] == sev]
        if not group:
            continue
        lines.append("### %s %s（%d 条）" % (_ICON[sev], _LABEL[sev], len(group)))
        lines.append("")
        for f in group:
            lines.append("- **%s %s**" % (f["check_id"], f["title"]))
            lines.append("  - 证据：%s" % f["evidence"])
            lines.append("  - 建议：%s" % f["action"])
        lines.append("")

    lines += [
        "## 判读原则（写在这里是为了让口径可复核）",
        "",
        "1. **没采到 ≠ 正常**：证据源缺失/解析失败一律落红或黄，绝不静默当绿"
        "（fail-open 禁令）。",
        "2. **灰态 ≠ 绿**：灰态无样本是事实陈述，最高只给黄，且证据须写明"
        "「灰态无样本」。",
        "3. **无文件 ≠ 断线**：灰态段零落盘但有note 证据时判黄；"
        "无文件且无任何证据才判红。",
        "4. **闸门收紧零买入 ≠ 断线**（G-01）：台账 zero_buy 显式声明的通道零产出"
        "判黄「业务零产出（闸门收紧，非断线）」，不判红；未声明沿用现状判红。",
        "5. **补跑件按业务日期判**（G-02 终裁 v20261021-user-approved）：补跑件"
        "新鲜度判读基准=业务日期（文件内记录的应覆盖交易日），落盘 mtime 仅留痕"
        "不豁免——业务日期超期照常判黄/红，防陈旧数据靠补跑「洗白」成新鲜"
        "（老田 2026-10-21 07:12 圈选⑧A）。",
        "6. **形式在、内容降级 ≠ 绿**（CH-08）：H5 对最新产物做内容级检测，"
        "status=DEGRADED / cutover_verdict=BLOCK_* 判黄并引用具体字段。",
        "7. 本工具只判不发，未接推送线路径（属 015 待授权单）。",
        "",
    ]
    return "\n".join(lines)


def render_human(result: dict) -> str:
    """终端紧凑输出。"""
    s = result["summary"]
    out = [
        "晨批健康自检 %s → %s %s（红%d 黄%d 绿%d）  [DRYRUN 只读]"
        % (
            result["date"],
            _ICON[result["status"]],
            _LABEL[result["status"]],
            s["red"],
            s["yellow"],
            s["green"],
        )
    ]
    for f in result["findings"]:
        if f["severity"] == "green":
            continue
        out.append("  %s %-10s %s" % (_ICON[f["severity"]], f["check_id"], f["title"]))
        out.append("      证据: %s" % f["evidence"])
        out.append("      建议: %s" % f["action"])
    if not any(f["severity"] != "green" for f in result["findings"]):
        out.append("  全部判据绿，无缺口。")
    return "\n".join(out)


def main(argv=None) -> int:
    """CLI 入口。退出码 0=green / 1=yellow / 2=red / 3=工具自身失败。"""
    ap = argparse.ArgumentParser(
        prog="morning_health",
        description="晨批健康自检（红黄绿）。只读、DRYRUN、不推送。",
    )
    ap.add_argument(
        "--date",
        default=datetime.now().strftime("%Y%m%d"),
        help="目标日 YYYYMMDD（默认今天）",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON 契约")
    ap.add_argument("--md", action="store_true", help="输出 Markdown")
    ap.add_argument("--out", help="写入指定路径（默认只打印）")
    ap.add_argument("--repo-root", help="仓根目录（默认自动定位）")
    ap.add_argument(
        "--ledger", help="通道频次台账路径（默认同目录 channel_frequency.json）"
    )
    args = ap.parse_args(argv)

    try:
        paths = HealthPaths(repo_root=Path(args.repo_root)) if args.repo_root else None
        result = check_morning(args.date, paths=paths, ledger_path=args.ledger)
    except ValueError as e:
        print("[morning_health] 参数非法: %s" % e, file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(
            "[morning_health] 自检执行失败: %s: %s" % (type(e).__name__, e),
            file=sys.stderr,
        )
        return 3

    if args.json:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    elif args.md:
        text = render_markdown(result)
    else:
        text = render_human(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("[morning_health] 已写入 %s（status=%s）" % (args.out, result["status"]))
    else:
        print(text)
    return _sev_rank(result["status"])


if __name__ == "__main__":
    raise SystemExit(main())
