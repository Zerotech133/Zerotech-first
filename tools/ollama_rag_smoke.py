#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 Ollama/RAG 运行与 smoke 工具（K-STOCK-160）。

脚本只允许访问回环地址（127.0.0.1/localhost/::1），不访问外网。
默认仅做静态检查；需要连接本机服务或写索引时，显式执行对应命令。

示例：
    python tools/ollama_rag_smoke.py --dry-run preflight
    python tools/ollama_rag_smoke.py status
    python tools/ollama_rag_smoke.py --index work/rag_index.local.json verify
    python tools/ollama_rag_smoke.py --index work/rag_index.local.json rebuild
    python tools/ollama_rag_smoke.py smoke
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown"}
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_WEB_URL = "http://127.0.0.1:8899"
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_DOCS_DIR = "knowledge_docs"
DEFAULT_INDEX_PATH = "work/rag_index.local.json"
CHUNK_SIZE = 500
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _repo_root(value: str | None) -> Path:
    return Path(value).resolve() if value else Path(__file__).resolve().parents[1]


def _path(root: Path, value: str) -> Path:
    target = Path(value)
    return target.resolve() if target.is_absolute() else (root / target).resolve()


def _safe_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError(f"仅允许访问回环地址，收到：{value}")
    if parsed.username or parsed.password:
        raise ValueError("URL 不得包含用户名或密码")
    return value.rstrip("/")


def _supported_files(docs_dir: Path) -> list[Path]:
    if not docs_dir.is_dir():
        return []
    return sorted(
        p for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_source(source: str, docs_dir: Path) -> str:
    """兼容 KnowledgeBase 产生的绝对 source 和本脚本产生的相对 source。"""
    source_path = Path(source)
    try:
        return source_path.resolve().relative_to(docs_dir.resolve()).as_posix()
    except ValueError:
        return source_path.as_posix().replace("\\", "/")


def _registry() -> dict[int, str]:
    """静态读取 tools/check_ports.py 的 REGISTRY，不触发端口探测。"""
    registry_path = Path(__file__).resolve().with_name("check_ports.py")
    tree = ast.parse(registry_path.read_text(encoding="utf-8"), filename=str(registry_path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REGISTRY" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return {int(port): str(name) for port, name in value.items()}
    raise ValueError(f"未找到端口注册表：{registry_path}")


def _port_guard() -> dict[str, Any]:
    registry = _registry()
    required = {8899: "本地 Ollama Web 聊天", 11434: "Ollama"}
    missing = [port for port in required if port not in registry]
    return {
        "ok": not missing,
        "required": required,
        "registered": {str(port): registry.get(port) for port in required},
        "missing": missing,
        "registry_path": str(Path(__file__).resolve().with_name("check_ports.py")),
    }


def _doc_status(docs_dir: Path) -> dict[str, Any]:
    files = _supported_files(docs_dir)
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in files:
        stat = path.stat()
        total_bytes += stat.st_size
        rows.append({
            "source": path.relative_to(docs_dir).as_posix(),
            "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
            "sha256": _sha256(path),
        })
    return {
        "path": str(docs_dir),
        "exists": docs_dir.is_dir(),
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": rows,
    }


def _index_status(index_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(index_path), "exists": index_path.is_file()}
    if not index_path.is_file():
        return result
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result.update({"valid_json": False, "error": str(exc)})
        return result
    records = payload.get("records", []) if isinstance(payload, dict) else []
    sources = {_relative_source(str(item.get("source", "")), index_path.parent) for item in records if isinstance(item, dict)}
    result.update({
        "valid_json": isinstance(payload, dict),
        "schema_version": payload.get("version") if isinstance(payload, dict) else None,
        "index_version": payload.get("index_version") if isinstance(payload, dict) else None,
        "record_count": len(records) if isinstance(records, list) else 0,
        "source_count": len(sources),
    })
    return result


def _verify_index(docs_dir: Path, index_path: Path) -> dict[str, Any]:
    docs = _supported_files(docs_dir)
    result: dict[str, Any] = {
        "ok": docs_dir.is_dir() and bool(docs),
        "docs_dir": str(docs_dir),
        "index_path": str(index_path),
        "errors": [],
        "warnings": [],
    }
    if not docs_dir.is_dir():
        result["errors"].append("知识库目录不存在")
        return result
    for path in docs:
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result["errors"].append(f"文档不可读 {path.name}: {exc}")
    if not docs:
        result["errors"].append("没有 .txt/.md/.markdown 文档")
    if not index_path.is_file():
        result["warnings"].append("索引文件不存在；8899 首次 RAG 请求会在内存中重建")
        result["ok"] = not result["errors"]
        return result
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result["errors"].append(f"索引不是有效 JSON: {exc}")
        result["ok"] = False
        return result
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        result["errors"].append("索引缺少 records 列表")
        result["ok"] = False
        return result
    expected = {p.relative_to(docs_dir).as_posix(): _sha256(p) for p in docs}
    seen: dict[str, set[str]] = {}
    for number, record in enumerate(records):
        if not isinstance(record, dict):
            result["errors"].append(f"records[{number}] 不是对象")
            continue
        source = _relative_source(str(record.get("source", "")), docs_dir)
        digest = str(record.get("document_hash", ""))
        seen.setdefault(source, set()).add(digest)
        if source not in expected:
            result["errors"].append(f"索引来源不存在: {source}")
        elif digest != expected[source]:
            result["errors"].append(f"文档哈希不匹配: {source}")
        if not isinstance(record.get("embedding"), list) or not record.get("embedding"):
            result["errors"].append(f"缺少向量: {source}")
    for source in expected:
        if source not in seen:
            result["errors"].append(f"文档未进入索引: {source}")
    result["record_count"] = len(records)
    result["source_count"] = len(seen)
    result["ok"] = not result["errors"]
    return result


def _http_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 10) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with safe_urlopen(request, timeout=timeout) as response:
        body = response.read()
        return response.status, json.loads(body.decode("utf-8")) if body else None


def _embed(base_url: str, model: str, chunks: list[str]) -> list[list[float]]:
    status, payload = _http_json(f"{base_url}/api/embed", {"model": model, "input": chunks}, timeout=120)
    if status >= 400 or not isinstance(payload, dict):
        raise RuntimeError(f"Ollama embed 返回异常：HTTP {status}")
    vectors = payload.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(chunks):
        raise RuntimeError(f"向量数量不匹配：{len(vectors) if isinstance(vectors, list) else 0} != {len(chunks)}")
    return vectors


def _rebuild(docs_dir: Path, index_path: Path, ollama_url: str, model: str) -> dict[str, Any]:
    files = _supported_files(docs_dir)
    if not docs_dir.is_dir() or not files:
        raise ValueError("知识库目录不存在或没有支持的文档")
    chunks: list[tuple[str, str, str, int]] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source = path.relative_to(docs_dir).as_posix()
        chunks.extend((source, digest, text[start:start + CHUNK_SIZE], number)
                      for number, start in enumerate(range(0, len(text), CHUNK_SIZE))
                      if text[start:start + CHUNK_SIZE])
    vectors = _embed(ollama_url, model, [item[2] for item in chunks])
    previous_version = 0
    if index_path.is_file():
        try:
            old = json.loads(index_path.read_text(encoding="utf-8"))
            previous_version = int(old.get("index_version", 0)) if isinstance(old, dict) else 0
        except (OSError, ValueError, json.JSONDecodeError):
            previous_version = 0
    records = [
        {"source": source, "index": number, "text": text, "document_hash": digest, "embedding": vector}
        for (source, digest, text, number), vector in zip(chunks, vectors)
    ]
    payload = {
        "version": 2,
        "index_version": previous_version + 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(docs_dir),
        "embed_model": model,
        "chunk_size": CHUNK_SIZE,
        "records": records,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(index_path), "record_count": len(records), "index_version": payload["index_version"]}


def _tcp_up(url: str, timeout: float = 1.0) -> bool:
    parsed = urllib.parse.urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _run_start(args: argparse.Namespace, root: Path, dry_run: bool) -> dict[str, Any]:
    command = [sys.executable, "-m", "ollama_service.launcher"]
    if dry_run:
        return {"ok": True, "dry_run": True, "command": command, "message": "将执行启动前自检并启动 8899"}
    import subprocess
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=180)
    online = _tcp_up(args.web_url)
    return {"ok": completed.returncode == 0 and online, "returncode": completed.returncode,
            "stdout": completed.stdout[-2000:], "stderr": completed.stderr[-2000:], "web_online": online}


def _run_smoke(args: argparse.Namespace, root: Path, docs_dir: Path, index_path: Path, dry_run: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {"preflight": {"ok": _port_guard()["ok"], "ports": _port_guard()}}
    checks["docs"] = _doc_status(docs_dir)
    checks["index"] = _index_status(index_path)
    if dry_run:
        checks["http"] = {"ok": True, "dry_run": True, "planned": ["GET /", "GET /api/tags", "POST /api/rag"]}
        return {"ok": all(bool(item.get("ok", True)) for item in checks.values()), "dry_run": True, "checks": checks}
    checks["web_root"] = _live_get(args.web_url + "/")
    checks["ollama_tags"] = _live_get(args.ollama_url + "/api/tags")
    if checks["docs"].get("file_count", 0):
        checks["rag"] = _live_rag(args.web_url, args.question)
    else:
        checks["rag"] = {"ok": False, "error": "知识库没有支持文档，跳过 /api/rag"}
    required = ("preflight", "docs", "web_root", "ollama_tags", "rag")
    return {
        "ok": all(bool(checks[name].get("ok", checks[name].get("exists", False))) for name in required),
        "checks": checks,
    }


def _live_get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with safe_urlopen(request, timeout=10) as response:
            raw = response.read()
            content_type = response.headers.get_content_type()
            body = None
            if content_type == "application/json" and raw:
                body = json.loads(raw.decode("utf-8"))
            return {
                "ok": response.status == 200,
                "status": response.status,
                "content_type": content_type,
                "body_keys": sorted(body) if isinstance(body, dict) else [],
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _live_rag(web_url: str, question: str) -> dict[str, Any]:
    request = urllib.request.Request(web_url + "/api/rag", data=json.dumps({"message": question, "top_k": 1}, ensure_ascii=False).encode(), method="POST")
    request.add_header("Content-Type", "application/json")
    try:
        with safe_urlopen(request, timeout=180) as response:
            lines = [json.loads(line.decode("utf-8")) for line in response if line.strip()]
        kinds = ["sources" if "sources" in row else "done" if row.get("done") else "message" for row in lines]
        return {"ok": response.status == 200 and "sources" in kinds and "done" in kinds, "status": response.status, "events": kinds}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _emit(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", False) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地 Ollama/RAG 运行与 smoke 检查")
    parser.add_argument("--repo-root", help="仓库根目录，默认按脚本位置推断")
    parser.add_argument("--docs-dir", default=DEFAULT_DOCS_DIR)
    parser.add_argument("--index", default=DEFAULT_INDEX_PATH)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--web-url", default=DEFAULT_WEB_URL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--question", default="只返回一句 OK")
    parser.add_argument("--dry-run", action="store_true", help="不连本机服务、不启动进程、不写文件")
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "status", "verify", "rebuild", "start", "smoke"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    try:
        args.ollama_url = _safe_url(args.ollama_url)
        args.web_url = _safe_url(args.web_url)
        root = _repo_root(args.repo_root)
        docs_dir = _path(root, args.docs_dir)
        index_path = _path(root, args.index)
        if args.command == "preflight":
            registry = _port_guard()
            result = {"ok": registry["ok"] and sys.version_info >= (3, 10), "dry_run": args.dry_run,
                      "python": sys.version.split()[0], "repo_root": str(root), "docs": _doc_status(docs_dir), "ports": registry,
                      "network_policy": "仅回环地址"}
        elif args.command == "status":
            result = {"ok": True, "runtime": "内存索引（8899 每次进程内按需更新）", "docs": _doc_status(docs_dir),
                      "index": _index_status(index_path), "ports": _port_guard(), "dry_run": args.dry_run}
        elif args.command == "verify":
            result = {"ok": True, "dry_run": args.dry_run, "planned": "验证文档 UTF-8、索引来源/哈希/向量和端口注册"} if args.dry_run else _verify_index(docs_dir, index_path)
        elif args.command == "rebuild":
            result = {"ok": True, "dry_run": True, "planned": {"docs": str(docs_dir), "index": str(index_path), "embed_model": args.embed_model}}
            if not args.dry_run:
                result = _rebuild(docs_dir, index_path, args.ollama_url, args.embed_model)
        elif args.command == "start":
            result = _run_start(args, root, args.dry_run)
        else:
            result = _run_smoke(args, root, docs_dir, index_path, args.dry_run)
        return _emit(result, args.as_json)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        return _emit({"ok": False, "error": str(exc), "dry_run": args.dry_run}, args.as_json)


if __name__ == "__main__":
    raise SystemExit(main())
