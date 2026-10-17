"""链接任务模型 + 状态机定义（架构 §2.1）。

状态集::

    pending → parsed → downloaded → decrypted → extracted → done
    任意处理态 → failed（可重试）→ dead（重试超限死信）
    pending → waiting_cookie（Cookie 过期挂起）→（人工更新）→ pending

重试策略：指数退避 5min / 30min / 2h；``error_code=COOKIE_EXPIRED`` 不计次。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import hashlib
import re


# ---------------------------------------------------------------------------
# 状态机常量（与 store/schema.sql 的 osint_link_task.status 对齐）
# ---------------------------------------------------------------------------
class LinkStatus:
    """链接任务状态常量（字符串值即库中存储值）。"""

    PENDING = "pending"            # 已入队待解析
    PARSED = "parsed"              # D 解析成功（拿到 mediaUrl/decode_key）
    DOWNLOADED = "downloaded"      # 分片下载完成（md5 校验）
    DECRYPTED = "decrypted"        # ISAAC64 解密 + ffprobe 校验通过
    EXTRACTED = "extracted"        # 抽取管线完成
    DONE = "done"                  # 终态：合规入库完成
    FAILED = "failed"              # 可重试失败（等 next_retry_at 回 pending）
    DEAD = "dead"                  # 死信（重试超限，人工处置）
    WAITING_COOKIE = "waiting_cookie"  # Cookie 过期挂起（人工更新后自动回 pending）


class ErrorCode:
    """失败/挂起原因码（osint_link_task.error_code）。"""

    COOKIE_EXPIRED = "COOKIE_EXPIRED"  # 401：不计入重试次数
    PARSE_FAIL = "PARSE_FAIL"          # 解析链失败
    DOWNLOAD_FAIL = "DOWNLOAD_FAIL"    # 下载失败（分片/校验）
    DECRYPT_FAIL = "DECRYPT_FAIL"      # 解密失败（ISAAC64/ffprobe）
    EXTRACT_FAIL = "EXTRACT_FAIL"      # 抽取管线失败（ffmpeg/ASR/OCR）
    EXTRACT_HARD_FAIL = "EXTRACT_HARD_FAIL"  # 抽取硬失败（文件损坏等，不重试）


class Route:
    """采集路由。"""

    D = "D"        # 主力：纯 Python 解析链（ARM/Docker）
    B = "B"        # 兜底：Go Headless 采集机
    AUTO = "AUTO"  # D 失败自动转 B


#: 合法状态全集（入参校验用）
ALL_STATUSES: frozenset[str] = frozenset(
    {
        LinkStatus.PENDING, LinkStatus.PARSED, LinkStatus.DOWNLOADED,
        LinkStatus.DECRYPTED, LinkStatus.EXTRACTED, LinkStatus.DONE,
        LinkStatus.FAILED, LinkStatus.DEAD, LinkStatus.WAITING_COOKIE,
    }
)

#: 合法路由全集
ALL_ROUTES: frozenset[str] = frozenset({Route.D, Route.B, Route.AUTO})

#: 状态机合法流转表：{当前状态: {可去状态, ...}}
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    LinkStatus.PENDING: frozenset(
        {LinkStatus.PARSED, LinkStatus.FAILED, LinkStatus.DEAD, LinkStatus.WAITING_COOKIE}
    ),
    LinkStatus.WAITING_COOKIE: frozenset({LinkStatus.PENDING}),  # 仅人工更新 Cookie 后恢复
    LinkStatus.PARSED: frozenset(
        {LinkStatus.DOWNLOADED, LinkStatus.FAILED, LinkStatus.DEAD}
    ),
    LinkStatus.DOWNLOADED: frozenset(
        {LinkStatus.DECRYPTED, LinkStatus.FAILED, LinkStatus.DEAD}
    ),
    LinkStatus.DECRYPTED: frozenset(
        {LinkStatus.EXTRACTED, LinkStatus.FAILED, LinkStatus.DEAD}
    ),
    LinkStatus.EXTRACTED: frozenset({LinkStatus.DONE, LinkStatus.FAILED, LinkStatus.DEAD}),
    LinkStatus.FAILED: frozenset(
        {LinkStatus.PENDING, LinkStatus.DEAD}  # 退避到期回 pending；超限进 dead
    ),
    LinkStatus.DEAD: frozenset(),   # 死信终态，仅人工处置
    LinkStatus.DONE: frozenset(),   # 正常终态
}

#: 指数退避表：第 N 次重试的等待秒数（架构 §2.1：5min/30min/2h，其后封顶 2h）
RETRY_BACKOFF_SECONDS: tuple[int, ...] = (5 * 60, 30 * 60, 2 * 3600)

#: 默认最大重试次数（超过即死信）
DEFAULT_MAX_RETRY: int = 3

#: 允许查询过滤的状态白名单（API 入参校验）
QUERYABLE_STATUSES: frozenset[str] = ALL_STATUSES


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
# sph 分享链接常见尾巴：?scene=xxx / #rd 等跟踪参数，规范化后再哈希，保证同一视频投递多次幂等
_URL_TRACKING_RE = re.compile(r"([?&#])(scene|ch_id|from|share_type)=[^&#]*")


def normalize_url(url: str) -> str:
    """规范化 sph 分享链接：去首尾空白、去常见跟踪参数、统一小写 host 部分。

    仅用于 url_hash 去重，不改变原始 ``sph_url`` 的存储。
    """
    cleaned = url.strip()
    cleaned = _URL_TRACKING_RE.sub("", cleaned)
    cleaned = cleaned.rstrip("#&?")
    return cleaned


def compute_url_hash(url: str) -> str:
    """计算规范化链接的 SHA1（40 位十六进制），作为投递幂等去重键。"""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    """当前 UTC 时间（带 tzinfo，与 PG TIMESTAMPTZ 交互统一时区）。"""
    return datetime.now(timezone.utc)


def isoformat_z(dt: datetime | None) -> str | None:
    """datetime → ISO 8601 UTC 字符串（API 出参统一格式）；None 透传。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 领域模型
# ---------------------------------------------------------------------------
@dataclass
class LinkTask:
    """链接任务领域对象（osint_link_task 表行）。

    ``id``/``created_at`` 等库生成字段在新建（未落库）时为 None。
    """

    sph_url: str                                   # 原始分享链接
    url_hash: str                                  # SHA1(规范化sph_url)，投递去重
    status: str = LinkStatus.PENDING               # 当前状态
    route: str = Route.D                           # D / B / AUTO
    video_id: str | None = None                    # 解析成功后回填
    parse_result: dict[str, Any] | None = None     # {mediaUrl, urlToken, decode_key, author, ...}
    file_md5: str | None = None
    raw_path: str | None = None                    # 明文 mp4 落盘路径
    retry_count: int = 0
    max_retry: int = DEFAULT_MAX_RETRY
    next_retry_at: datetime | None = None
    error_code: str | None = None
    error_msg: str | None = None
    priority: int = 5                              # 数字小先采
    created_by: str = "feishu"                     # feishu / manual / watcher / api
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # -------------------------------------------------- 便捷判断 --
    @property
    def is_terminal(self) -> bool:
        """是否终态（done / dead）。"""
        return self.status in (LinkStatus.DONE, LinkStatus.DEAD)

    def can_transition_to(self, new_status: str) -> bool:
        """状态机校验：当前状态是否允许流转到 new_status。"""
        return new_status in ALLOWED_TRANSITIONS.get(self.status, frozenset())


def row_to_task(row: dict[str, Any]) -> LinkTask:
    """数据库行 dict → LinkTask（parse_result 反序列化）。"""
    return LinkTask(
        id=row["id"],
        sph_url=row["sph_url"],
        url_hash=row["url_hash"],
        status=row["status"],
        route=row.get("route", Route.D),
        video_id=row.get("video_id"),
        parse_result=row.get("parse_result") or None,
        file_md5=row.get("file_md5"),
        raw_path=row.get("raw_path"),
        retry_count=int(row.get("retry_count", 0) or 0),
        max_retry=int(row.get("max_retry", DEFAULT_MAX_RETRY) or DEFAULT_MAX_RETRY),
        next_retry_at=row.get("next_retry_at"),
        error_code=row.get("error_code"),
        error_msg=row.get("error_msg"),
        priority=int(row.get("priority", 5) or 5),
        created_by=row.get("created_by") or "feishu",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


__all__ = [
    "LinkStatus", "ErrorCode", "Route", "ALL_STATUSES", "ALL_ROUTES",
    "ALLOWED_TRANSITIONS", "RETRY_BACKOFF_SECONDS", "DEFAULT_MAX_RETRY",
    "QUERYABLE_STATUSES", "LinkTask", "row_to_task",
    "normalize_url", "compute_url_hash", "utc_now", "isoformat_z",
]
