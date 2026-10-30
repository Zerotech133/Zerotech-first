# -*- coding: utf-8 -*-
"""把 rag_index_corpus.json 打包成门户 handler 可直接加载的二进制（无 numpy 依赖）。

产物：
  C:\\项目\\知识库\\50_RAG\\packed\\docs.json   [{id, source, text}]
  C:\\项目\\知识库\\50_RAG\\packed\\emb.f32     float32 little-endian 行优先 (N x dim)
  C:\\项目\\知识库\\50_RAG\\packed\\meta.json    {dim, rows, generated_at}
"""
from __future__ import annotations

import json
import sys
from array import array
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
SRC = KB / "50_RAG" / "rag_index_corpus.json"
OUT = KB / "50_RAG" / "packed"


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    records = data.get("records") or data.get("docs") or []
    if not records:
        print("no records", file=sys.stderr)
        return 2
    docs = []
    emb = array("f")
    dim = None
    for i, r in enumerate(records):
        text = str(r.get("text") or "")
        source = str(r.get("source") or "")
        vec = r.get("embedding") or []
        if not vec:
            continue
        dim = len(vec)
        docs.append({"id": i, "source": source, "text": text})
        emb.extend(float(x) for x in vec)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "docs.json").write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    with (OUT / "emb.f32").open("wb") as f:
        emb.tofile(f)
    meta = {"dim": dim, "rows": len(docs), "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": str(SRC), "emb_bytes": (OUT / "emb.f32").stat().st_size}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
