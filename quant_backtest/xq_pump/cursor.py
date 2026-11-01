# -*- coding: utf-8 -*-
"""增量断点管理：按 (type, key) 维度持久化游标，纯 JSON，可配置根。

游标语义（沿用原 xq_collect_1000 实证）：
- posts：max_id（按状态 ID 翻页，永不过期）；exhausted=该类目已到底
- hot：榜单每日变，主要靠 seen 去重做增量；可选 since_id 推进
- timeline(user)：last_id

断点用独立 JSON 文件（每 type 一个），原子写（.tmp + os.replace），
避免原 seed_cursor 整文件读改写在并行时的互踩问题。
"""

from __future__ import annotations

import json
import os
from typing import Dict


class CursorStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, ctype: str) -> str:
        return os.path.join(self.root, "%s_cursor.json" % ctype)

    def load_type(self, ctype: str) -> Dict[str, dict]:
        p = self._path(ctype)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            out: Dict[str, dict] = {}
            for k, v in (d or {}).items():
                if isinstance(v, dict):
                    out[str(k)] = {
                        "max_id": v.get("max_id"),
                        "since_id": v.get("since_id"),
                        "last_id": v.get("last_id"),
                        "exhausted": bool(v.get("exhausted")),
                    }
            return out
        except Exception:
            return {}

    def save_type(self, ctype: str, state: Dict[str, dict]) -> None:
        tmp = self._path(ctype) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, self._path(ctype))

    def mark_exhausted(self, ctype: str, key: str) -> None:
        st = self.load_type(ctype)
        cur = st.get(key) or {}
        cur["exhausted"] = True
        st[key] = cur
        self.save_type(ctype, st)
