# -*- coding: utf-8 -*-
"""阚泽部 · 会话记忆（K2.2，WO-FS-148-4）。

一句话：让「刚才那只票」能被听懂。

设计
----
- 存储：`portal/work/comm_memory/{open_id_md5}.jsonl`，每行一轮
  {ts, role, text_md5, text_preview, code, intent}
  原文只留 40 字 preview（够代词消解与排障，不做全文画像——
  与 customer_interest 隐私红线一致：画像层不存原文）。
- 窗口：默认最近 8 轮（COMM_MEMORY_TURNS 可覆盖）。
- 代词消解：`resolve_reference(text, open_id)` —— 文本含
  刚才/那只/这个/它 等指代且无 6 位代码时，回填最近一次出现的代码。
- 失败即诚实：写盘失败返回 False + 原因，不静默（禁吞异常判例）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

DEFAULT_TURNS = 8
PREVIEW_CHARS = 40

_PRONOUN_RE = re.compile(r"(刚才|刚刚|那只|这只|那个|这个|上一只|它|上次说)")
_CODE_RE = re.compile(r"\b(\d{6})\b")


def _base_dir():
    p = os.environ.get("COMM_MEMORY_DIR")
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "work" / "comm_memory"


def _mask_key(open_id):
    """open_id 作文件名前先 md5（避免明文落到目录名，判例同留痕口径）。"""
    return hashlib.md5((open_id or "").encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def _path_for(open_id):
    return _base_dir() / ("%s.jsonl" % _mask_key(open_id))


def _md5(s):
    return hashlib.md5((s or "").encode("utf-8", "replace"), usedforsecurity=False).hexdigest()


def remember(open_id, role, text, intent=None, code=None):
    """追加一轮。→ (ok, error)。写失败如实返回，不阻断消息链。"""
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,  # customer / bot
        "text_md5": _md5(text),
        "text_preview": (text or "")[:PREVIEW_CHARS],
        "code": code or "",
        "intent": intent or "",
    }
    try:
        p = _path_for(open_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True, None
    except OSError as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def _load(open_id, turns=None):
    n = turns or int(os.environ.get("COMM_MEMORY_TURNS", DEFAULT_TURNS))
    p = _path_for(open_id)
    if not p.exists():
        return []
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue  # 单行损坏跳过（rest of file 仍可用）
    except OSError:
        return []
    return rows[-n:]


def recall(open_id, turns=None):
    """最近 N 轮（时间正序）。坏行跳过不炸整个会话。"""
    return _load(open_id, turns)


def last_code(open_id):
    """最近一次出现的 6 位代码（无则空串）。"""
    for row in reversed(_load(open_id)):
        if row.get("code"):
            return row["code"]
    return ""


def resolve_reference(text, open_id):
    """代词消解：→ {text, injected_code, from_turns}

    规则：文本含指代词 且 自身无 6 位代码 → 用最近一次代码回填到
    entities 层（不改原文本，只附加 code，回执时可按需引用）。
    """
    t = (text or "").strip()
    own = _CODE_RE.search(t)
    if own:
        return {"text": t, "injected_code": own.group(1), "from_turns": 0}
    if not _PRONOUN_RE.search(t):
        return {"text": t, "injected_code": "", "from_turns": 0}
    code = last_code(open_id)
    if not code:
        return {"text": t, "injected_code": "", "from_turns": 0}
    return {"text": t, "injected_code": code, "from_turns": 1}


def clear(open_id):
    """清空会话记忆（客户可自查/自清，透明化口径）。→ (ok, error)。"""
    p = _path_for(open_id)
    try:
        if p.exists():
            p.unlink()
        return True, None
    except OSError as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)
