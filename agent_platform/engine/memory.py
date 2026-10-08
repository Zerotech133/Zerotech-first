# -*- coding: utf-8 -*-
"""四级 namespace 记忆层（K-STOCK-003 §5.2 的本地 PoC 落地版）

gns 格式：domain/* | dept/{dept}/{type} | agent/{id}/{type} | share/*
后端：JSONL 文件（data/memory/<编码后 gns>.jsonl）；接口与未来 Qdrant 后端兼容
权限：注册表 memory_grants {read:[...], write:[...]}，fnmatch 通配校验，越权抛 PermissionDenied
复盘纪律：write 一律追加（append-only），「当时的判断」永远保留原始版本（Zep 时态语义）
"""
import fnmatch
import json
import threading
from pathlib import Path


class PermissionDenied(Exception):
    """Agent 越权读写记忆（防混写三道闸之运行时校验）"""


class MemoryStore:
    def __init__(self, base_dir, grants_by_agent=None, enforce=True):
        self.base = Path(base_dir)
        self.grants = grants_by_agent or {}
        self.enforce = enforce
        self._lock = threading.Lock()
        self.base.mkdir(parents=True, exist_ok=True)

    # ---------- 内部工具 ----------
    @staticmethod
    def _enc(gns):
        """gns → 文件名安全串：/ → __，* → STAR（约定 gns 内不含 __ 与 STAR 字面量）"""
        return gns.replace("/", "__").replace("*", "STAR")

    def _check(self, agent_id, gns, mode):
        if not self.enforce:
            return
        grants = self.grants.get(agent_id, {}).get(mode, [])
        if not any(fnmatch.fnmatch(gns, pat) for pat in grants):
            raise PermissionDenied(
                "[RBAC] %s 尝试 %s %s 被拒；grants=%s" % (agent_id, mode, gns, grants)
            )

    # ---------- 对外接口 ----------
    def write(self, agent_id, gns, payload):
        """追加一条记忆（只追加不覆盖）。payload 建议含 ts/symbol/round/confidence 等溯源字段"""
        self._check(agent_id, gns, "write")
        rec = {"agent_id": agent_id, "gns": gns, "payload": payload}
        p = self._path(gns)
        with self._lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self, agent_id, gns_pattern, limit=100):
        """按通配 pattern 读记忆（如 dept/invest/*）。返回最近 limit 条"""
        self._check(agent_id, gns_pattern, "read")
        pat = self._enc(gns_pattern) + ".jsonl"
        out = []
        for p in sorted(self.base.glob("*.jsonl")):
            if fnmatch.fnmatch(p.name, pat):
                with open(p, "r", encoding="utf-8") as f:
                    out.extend(json.loads(line) for line in f if line.strip())
        return out[-limit:]

    def _path(self, gns):
        return self.base / (self._enc(gns) + ".jsonl")
