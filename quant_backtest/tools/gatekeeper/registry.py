#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据版本注册表：data/gatekeeper_registry.json（版本元数据落数）。

判据版本史 + 每版冻结 sha（复用 voltarget_ab ensure_config 范式）。
freeze = 注册版本并置为 active（幂等：同版本同 sha 重申合法，
同版本不同 sha 拒绝——防篡改/防覆盖历史）。进化改变的是
"证据和判据版本"，不是"现行行为"（方案 §二边界 2）：行为变更
走治理链（预注册→落数→机械触发→拍板）后由 freeze 落新版本。
"""

import datetime
import json
import os

PROJECT_ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DEFAULT_FP = os.path.join(PROJECT_ROOT, "data",
                          "gatekeeper_registry.json")


class Registry:
    """版本注册表（文件契约，跨进程；tmp+os.replace 原子落盘）。"""

    def __init__(self, fp=None):
        self.fp = fp or DEFAULT_FP

    def _load(self):
        try:
            with open(self.fp, encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:
            reg = {}
        if not isinstance(reg.get("versions"), dict):
            reg["versions"] = {}
        return reg

    def _save(self, reg):
        d = os.path.dirname(self.fp)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.fp)

    def freeze(self, ver, sha, note=""):
        """注册/重申版本并置 active；返回该版本元数据。

        同版本号不同 sha → ValueError（历史冻结不可改写）。
        """
        reg = self._load()
        old = reg["versions"].get(ver)
        if old is not None:
            if old.get("sha") != sha:
                raise ValueError(
                    "版本 %s 已冻结为 sha=%s，拒绝改写为 %s（历史不可篡改）"
                    % (ver, old.get("sha"), sha))
            reg["active"] = ver              # 幂等重申
        else:
            reg["versions"][ver] = {
                "sha": sha,
                "frozen_at": datetime.datetime.now().isoformat(
                    timespec="seconds"),
                "note": note,
            }
            reg["active"] = ver
        self._save(reg)
        return reg["versions"][ver]

    def list_versions(self):
        """全部版本元数据 {ver: {sha, frozen_at, note}}。"""
        return dict(self._load()["versions"])

    def active(self):
        """→ (active_ver|None, meta|None)。"""
        reg = self._load()
        ver = reg.get("active")
        if ver is None:
            return None, None
        return ver, reg["versions"].get(ver)

    def verify(self, ver, sha):
        """sha 对账：注册表里的冻结值与当前算出值是否一致（篡改检出）。"""
        meta = self._load()["versions"].get(ver)
        return bool(meta) and meta.get("sha") == sha
