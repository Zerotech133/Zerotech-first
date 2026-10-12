# -*- coding: utf-8 -*-
"""RAG 持久化索引管理命令。

``status`` 和 ``verify`` 只读磁盘，不调用 Ollama；``rebuild`` 通过
``RagWebService`` 建立索引，再用 ``KnowledgeBase.save`` 落盘。调用方可以
注入 client/service/kb 替身，因而可在离线环境运行测试和巡检。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .knowledge_base import KnowledgeBase
    from .rag_web_backend import RagWebService
except ImportError:
    from knowledge_base import KnowledgeBase
    from rag_web_backend import RagWebService


SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown"}


def _supported_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def _json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class IndexManager:
    """管理一个知识文档目录对应的 JSON 索引。"""

    def __init__(
        self,
        docs_dir: str | Path = "knowledge_docs",
        index_path: str | Path = "rag_index.json",
        *,
        top_k: int = 4,
        embed_model: str = "bge-m3",
        chat_model: str = "qwen3.5:9b",
        client: Any = None,
        service_factory: Callable[..., Any] = RagWebService,
        kb_factory: Callable[..., Any] = KnowledgeBase,
    ) -> None:
        self.docs_dir = Path(docs_dir)
        self.index_path = Path(index_path)
        self.top_k = top_k
        self.embed_model = embed_model
        self.chat_model = chat_model
        self.client = client
        self.service_factory = service_factory
        self.kb_factory = kb_factory

    def _new_kb(self):
        return self.kb_factory(
            client=self.client,
            top_k=self.top_k,
            embed_model=self.embed_model,
            chat_model=self.chat_model,
        )

    def _new_service(self):
        return self.service_factory(
            docs_dir=self.docs_dir,
            top_k=self.top_k,
            embed_model=self.embed_model,
            chat_model=self.chat_model,
            client=self.client,
        )

    def _read_index(self):
        kb = self._new_kb()
        records = kb.load(self.index_path)
        if not isinstance(records, list):
            raise ValueError("索引 records 必须是列表")
        return kb, records

    def _document_files(self) -> list[Path]:
        return _supported_files(self.docs_dir)

    def status(self) -> dict[str, Any]:
        """返回索引和文档的只读概况，不访问 Ollama。"""
        errors: list[str] = []
        files = self._document_files()
        if not self.docs_dir.is_dir():
            errors.append(f"知识库目录不存在：{self.docs_dir}")
        elif not files:
            errors.append("知识库没有支持的文档（.txt/.md/.markdown）")

        result: dict[str, Any] = {
            "action": "status",
            "ok": not errors,
            "docs_dir": str(self.docs_dir),
            "index": str(self.index_path),
            "documents": len(files),
            "records": 0,
            "index_version": 0,
            "updated_at": None,
            "errors": errors,
        }
        if not self.index_path.is_file():
            result["ok"] = False
            result["errors"].append(f"索引文件不存在：{self.index_path}")
            return result
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            _, records = self._read_index()
            if isinstance(payload, dict):
                result["index_version"] = int(payload.get("index_version", 0))
                result["updated_at"] = payload.get("updated_at")
            result["records"] = len(records)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["ok"] = False
            result["errors"].append(f"索引文件无法读取：{exc}")
        result["ok"] = not result["errors"]
        return result

    def _canonical_source(self, source: Any) -> Path:
        candidate = Path(str(source))
        if not candidate.is_absolute():
            candidate = self.docs_dir / candidate
        return candidate.resolve()

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))

    def verify(self) -> dict[str, Any]:
        """校验索引结构、文档来源和当前文档 SHA-256。"""
        result = self.status()
        result["action"] = "verify"
        errors = list(result["errors"])
        if errors:
            result["ok"] = False
            result["errors"] = errors
            return result

        try:
            _, records = self._read_index()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["ok"] = False
            result["errors"] = [f"索引结构无效：{exc}"]
            return result

        docs = {
            path.resolve(): path.read_text(encoding="utf-8")
            for path in self._document_files()
        }
        indexed_sources: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        for number, record in enumerate(records):
            if not isinstance(record, dict):
                errors.append(f"第 {number + 1} 条索引记录不是对象")
                continue
            source = record.get("source")
            if not source:
                errors.append(f"第 {number + 1} 条索引记录缺少 source")
                continue
            source_path = self._canonical_source(source)
            indexed_sources[source_path].append(record)
            embedding = record.get("embedding")
            if not isinstance(embedding, list) or not embedding or not all(
                self._is_number(value) for value in embedding
            ):
                errors.append(f"索引记录 {source} 的 embedding 无效")
            if not record.get("document_hash"):
                errors.append(f"索引记录 {source} 缺少 document_hash")

        for source_path, text in docs.items():
            records_for_source = indexed_sources.get(source_path, [])
            if not records_for_source:
                errors.append(f"文档未入索引：{source_path.relative_to(self.docs_dir.resolve()).as_posix()}")
                continue
            expected_hash = self._new_kb()._document_hash(text)
            bad_hash = [
                record
                for record in records_for_source
                if record.get("document_hash") != expected_hash
            ]
            if bad_hash:
                errors.append(f"文档哈希不一致：{source_path.relative_to(self.docs_dir.resolve()).as_posix()}")

        for source_path in sorted(set(indexed_sources) - set(docs)):
            errors.append(f"索引包含不存在的文档：{source_path}")

        result["ok"] = not errors
        result["errors"] = errors
        result["records"] = len(records)
        return result

    def rebuild(self) -> dict[str, Any]:
        """通过 RagWebService 建索引并持久化；失败时不吞掉中文诊断。"""
        files = self._document_files()
        if not self.docs_dir.is_dir():
            return self._failure("rebuild", f"知识库目录不存在：{self.docs_dir}")
        if not files:
            return self._failure("rebuild", "知识库没有支持的文档（.txt/.md/.markdown）")
        try:
            service = self._new_service()
            count = service.ensure_index()
            kb = getattr(service, "_kb", None)
            if kb is None or not hasattr(kb, "save"):
                raise RuntimeError("RagWebService 未提供可持久化的 KnowledgeBase")
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            kb.save(self.index_path)
            verified = self.verify()
            verified.update({"action": "rebuild", "records": count})
            return verified
        except Exception as exc:
            return self._failure("rebuild", f"索引重建失败：{exc}")

    def _failure(self, action: str, message: str) -> dict[str, Any]:
        return {
            "action": action,
            "ok": False,
            "docs_dir": str(self.docs_dir),
            "index": str(self.index_path),
            "documents": len(self._document_files()),
            "records": 0,
            "index_version": 0,
            "updated_at": None,
            "errors": [message],
        }


def run_status(manager: IndexManager) -> dict[str, Any]:
    return manager.status()


def run_verify(manager: IndexManager) -> dict[str, Any]:
    return manager.verify()


def run_rebuild(manager: IndexManager) -> dict[str, Any]:
    return manager.rebuild()


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--docs-dir", default="knowledge_docs")
    parser.add_argument("--index-path", "--index", dest="index_path", default="rag_index.json")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--embed-model", default="bge-m3")
    parser.add_argument("--chat-model", default="qwen3.5:9b")
    parser.add_argument("--json", action="store_true", dest="json_output")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 RAG 持久化索引")
    parser.add_argument("--json", action="store_true", dest="json_global")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "verify", "rebuild"):
        subparser = subparsers.add_parser(command, help=f"{command} RAG 索引")
        _add_common_options(subparser)
    return parser


def _print_human(result: Mapping[str, Any]) -> None:
    mark = "✓" if result.get("ok") else "✗"
    print(f"{mark} {result.get('action', 'index')}: {'索引正常' if result.get('ok') else '索引异常'}")
    labels = {
        "docs_dir": "知识目录",
        "index": "索引文件",
        "documents": "文档数",
        "records": "索引记录数",
        "index_version": "索引版本",
        "updated_at": "更新时间",
    }
    for key in ("docs_dir", "index", "documents", "records", "index_version", "updated_at"):
        if key in result and result[key] is not None:
            print(f"  {labels[key]}：{result[key]}")
    for error in result.get("errors", []):
        print(f"  错误：{error}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manager = IndexManager(
        args.docs_dir,
        args.index_path,
        top_k=args.top_k,
        embed_model=args.embed_model,
        chat_model=args.chat_model,
    )
    result = {
        "status": run_status,
        "verify": run_verify,
        "rebuild": run_rebuild,
    }[args.command](manager)
    if args.json_output or args.json_global:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
