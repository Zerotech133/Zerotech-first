"""统一导入：公开网页文本及本地文件的独立持久化任务。"""

from __future__ import annotations

import hashlib
import html.parser
import http.client
import ipaddress
import socket
import ssl
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import text

from common import db
from common.paths import DATA_DIR

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])
ingest_router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])
MAX_FILE = 50 * 1024 * 1024
MAX_PAGE = 2 * 1024 * 1024
SUFFIX_TYPES = {
    ".txt": "text",
    ".md": "text",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
}


class LinkInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class IngestLinksInput(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)


class _VisibleText(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def _public_address(host: str) -> str:
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("域名解析失败") from exc
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not ips or not all(ip.is_global for ip in ips):
        raise ValueError("不允许访问非公网地址")
    return str(ips[0])


def _fetch_public_text(url: str) -> str:
    for _ in range(4):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("仅支持公开 HTTP(S) 网页")
        if parsed.port not in (None, 80 if parsed.scheme == "http" else 443):
            raise ValueError("不允许非标准端口")
        address = _public_address(parsed.hostname)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # 连接经过验证的 IP，同时保留原主机名用于 TLS SNI/证书验证。
        class PinnedConnection(
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        ):
            def connect(self) -> None:
                self.sock = socket.create_connection((address, port), timeout=5)
                if parsed.scheme == "https":
                    self.sock = ssl.create_default_context().wrap_socket(
                        self.sock, server_hostname=parsed.hostname
                    )

        conn = PinnedConnection(parsed.hostname, port, timeout=5)
        try:
            conn.request(
                "GET",
                (parsed.path or "/") + ("?" + parsed.query if parsed.query else ""),
                headers={
                    "Host": parsed.netloc,
                    "User-Agent": "osint-video-public-text/1.0",
                    "Accept": "text/html",
                },
            )
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("重定向缺少目标")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"公开网页 HTTP {response.status}")
            content_type = response.getheader("Content-Type", "").lower()
            if not (
                content_type.startswith("text/html")
                or content_type.startswith("text/plain")
            ):
                raise ValueError("目标不是文本网页")
            raw = response.read(MAX_PAGE + 1)
            if len(raw) > MAX_PAGE:
                raise ValueError("网页超过 2 MiB")
            charset = response.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            if content_type.startswith("text/plain"):
                return body.strip()
            parser = _VisibleText()
            parser.feed(body)
            return "\n".join(parser.parts).strip()
        finally:
            conn.close()
    raise ValueError("网页重定向次数过多")


def _create(source_type: str, name: str, url: str | None = None) -> dict:
    with db.transaction() as conn:
        row = (
            conn.execute(
                text("""INSERT INTO osint_import_task (source_type, source_name, source_url, status)
            VALUES (:type, :name, :url, 'processing') RETURNING id"""),
                {"type": source_type, "name": name, "url": url},
            )
            .mappings()
            .one()
        )
    return _get(row["id"])


def _update(
    task_id: int,
    status: str,
    *,
    content: str | None = None,
    path: str | None = None,
    digest: str | None = None,
    error: str | None = None,
) -> dict:
    db.execute(
        """UPDATE osint_import_task SET status=:status, content_text=:content,
        file_path=:path, sha256=:digest, error_message=:error, updated_at=now() WHERE id=:id""",
        {
            "id": task_id,
            "status": status,
            "content": content,
            "path": path,
            "digest": digest,
            "error": error,
        },
    )
    return _get(task_id)


def _get(task_id: int) -> dict:
    rows = db.fetch_all(
        """SELECT id, source_type, source_name, source_url, status, sha256,
        content_text, error_message, created_at, updated_at FROM osint_import_task WHERE id=:id""",
        {"id": task_id},
    )
    if not rows:
        raise HTTPException(404, "导入任务不存在")
    item = rows[0]
    return {
        **item,
        "created_at": item["created_at"].isoformat(),
        "updated_at": item["updated_at"].isoformat(),
    }


def _ok(item: dict) -> dict:
    return {"code": 0, "data": item, "message": "ok"}


def _ingest_task(item: dict) -> dict:
    return {
        "task_id": str(item["id"]),
        "source_name": item["source_name"],
        "source_type": item["source_type"],
        "state": item["status"],
        "error": item.get("error_message"),
        "created_at": item["created_at"].isoformat()
        if hasattr(item["created_at"], "isoformat")
        else item["created_at"],
    }


def _process_link(task_id: int, url: str) -> dict:
    try:
        content = _fetch_public_text(url)
        if not content:
            raise ValueError("公开页面没有可读取文本（可能需要登录或由脚本渲染）")
        return _update(task_id, "completed", content=content)
    except (ValueError, OSError, TimeoutError, UnicodeError) as exc:
        return _update(task_id, "failed", error=str(exc)[:500])


def _process_links(items: list[tuple[int, str]]) -> None:
    # 限制并发，避免 20 个网络请求串行阻塞；每条任务独立保存结果。
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda entry: _process_link(*entry), items))


def _create_ingest_link(url: str) -> tuple[dict, bool]:
    parsed = urlsplit(url)
    name = (parsed.hostname or url)[:255]
    with db.transaction() as conn:
        # 同一 URL 的并发请求串行化，避免检查后插入的竞争条件。
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:url, 0))"),
            {"url": url},
        )
        existing = conn.execute(
            text(
                "SELECT id FROM osint_import_task WHERE source_type='link' AND source_url=:url ORDER BY id DESC LIMIT 1"
            ),
            {"url": url},
        ).scalar_one_or_none()
        if existing is not None:
            task_id, created = existing, False
        else:
            task_id = conn.execute(
                text("""INSERT INTO osint_import_task (source_type, source_name, source_url, status)
                VALUES ('link', :name, :url, 'processing') RETURNING id"""),
                {"name": name, "url": url},
            ).scalar_one()
            created = True
    return _get(task_id), created


@router.post("/links")
def import_link(payload: LinkInput) -> dict:
    url = payload.url.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "仅支持 HTTP(S) 网页链接")
    item = _create("link", parsed.hostname, url)
    try:
        content = _fetch_public_text(url)
        if not content:
            raise ValueError("公开页面没有可读取文本（可能需要登录或由脚本渲染）")
        item = _update(item["id"], "completed", content=content)
    except (ValueError, OSError, TimeoutError, UnicodeError) as exc:
        item = _update(item["id"], "failed", error=str(exc)[:500])
    return _ok(item)


async def _import_file(file: UploadFile) -> dict:
    name = Path((file.filename or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if not name or suffix not in SUFFIX_TYPES:
        raise HTTPException(400, "不支持的文件类型；仅支持视频、文本、PDF、图片")
    kind = SUFFIX_TYPES[suffix]
    item = _create(kind, name[:255])
    target = DATA_DIR / "imports" / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    size = 0
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE:
                    raise ValueError("文件超过 50 MiB")
                digest.update(chunk)
                output.write(chunk)
        if not size:
            raise ValueError("文件为空")
        content = None
        if kind == "text":
            if size > MAX_PAGE:
                raise ValueError("文本超过 2 MiB")
            content = target.read_text(encoding="utf-8-sig").strip()
        elif kind == "pdf":
            pages = PdfReader(str(target)).pages
            if len(pages) > 100:
                raise ValueError("PDF 超过 100 页")
            content = "\n".join(page.extract_text() or "" for page in pages).strip()
        if content and len(content.encode("utf-8")) > MAX_PAGE:
            raise ValueError("提取文本超过 2 MiB")
        if kind in {"text", "pdf"} and not content:
            raise ValueError("文件没有可提取文本")
        status = "completed" if content else "pending_processing"
        note = "视频/图片已保存，尚未配置自动转写/OCR 处理器" if not content else None
        return _update(
            item["id"],
            status,
            content=content,
            path=str(target),
            digest=digest.hexdigest(),
            error=note,
        )
    except (ValueError, OSError, UnicodeError, PdfReadError) as exc:
        target.unlink(missing_ok=True)
        return _update(item["id"], "failed", error=str(exc)[:500])
    finally:
        await file.close()


@router.post("/files")
async def import_files(files: list[UploadFile] = File(...)) -> dict:
    if not 1 <= len(files) <= 20:
        raise HTTPException(400, "每次上传 1～20 个文件")
    items = [await _import_file(file) for file in files]
    return {"code": 0, "data": {"items": items}, "message": "ok"}


@router.get("/{task_id}")
def get_import(task_id: int) -> dict:
    return _ok(_get(task_id))


@ingest_router.post("/links")
def ingest_links(payload: IngestLinksInput, background_tasks: BackgroundTasks) -> dict:
    accepted, duplicated = 0, 0
    items = []
    queued = []
    for raw_url in payload.urls:
        url = raw_url.strip()
        try:
            parsed = urlsplit(url)
            if (
                len(url) > 2048
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)
            ):
                raise ValueError("仅支持标准端口的 HTTP(S) 网页链接")
            item, created = _create_ingest_link(url)
            accepted += int(created)
            duplicated += int(not created)
            if created:
                queued.append((item["id"], url))
            items.append(_ingest_task(item))
        except ValueError as exc:
            # 非法输入只影响自身，不阻止其它链接投递。
            items.append(
                {
                    "task_id": "",
                    "source_name": url[:255],
                    "source_type": "link",
                    "state": "failed",
                    "error": str(exc),
                    "created_at": "",
                }
            )
    if queued:
        background_tasks.add_task(_process_links, queued)
    return _ok({"accepted": accepted, "duplicated": duplicated, "items": items})


@ingest_router.post("/files")
async def ingest_files(files: list[UploadFile] = File(...)) -> dict:
    result = await import_files(files)
    items = [_ingest_task(item) for item in result["data"]["items"]]
    return _ok({"accepted": len(items), "duplicated": 0, "items": items})


@ingest_router.get("/tasks")
def list_ingest_tasks(cursor: int = 0, size: int = 20) -> dict:
    if cursor < 0 or not 1 <= size <= 100:
        raise HTTPException(400, "非法分页参数")
    rows = db.fetch_all(
        """SELECT id, source_name, source_type, status, error_message, created_at
        FROM osint_import_task ORDER BY id DESC LIMIT :limit OFFSET :offset""",
        {"limit": size + 1, "offset": cursor},
    )
    items = [_ingest_task(row) for row in rows[:size]]
    return _ok(
        {
            "items": items,
            "next_cursor": str(cursor + size) if len(rows) > size else None,
        }
    )
