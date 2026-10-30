# -*- coding: utf-8 -*-
"""api_backtest 拆分 B5（2026-10-30 P71）：JSON API（sa_result/sa_start/algo_activate）。

自 portal/api_backtest.py Handler 方法纯移动（方法体零改动，签名不变，首参 self）。
"""
import os

from api_common import ROOT, _read_json

from api_backtest import SA_BUSY, SA_JOBS, _sa_worker


def _sa_result(self, jid):
        j = SA_JOBS.get(jid)
        if not j:
            self._json({"error": "job not found"}, 404)
            return
        self._json({"status": j["status"], "d": j.get("d")})


def _sa_start(self, q):
        q = (q or "").strip()[:20]
        if not q:
            self._json({"error": "请输入股票"}, 400)
            return
        if SA_BUSY["v"]:
            self._json({"error": "正在分析上一只，请稍候"}, 429)
            return
        import threading
        import uuid
        jid = uuid.uuid4().hex[:12]
        SA_BUSY["v"] = True
        SA_JOBS[jid] = {"status": "running"}
        threading.Thread(target=_sa_worker, args=(jid, q),
                         daemon=True).start()
        self._json({"job": jid})


def _algo_activate(self, key):
        """卡密激活：合法且未激活的 key 写入 activated_keys（本机解锁）。
        返回 (ok, msg)。"""
        import json as _json
        import time as _t
        fp = os.path.join(ROOT, "licenses.json")
        lic = _read_json(fp) or {}
        key = (key or "").strip().upper()
        if not key:
            return False, "请输入卡密"
        today = _t.strftime("%Y-%m-%d")
        rec = next((r for r in lic.get("licenses", [])
                    if str(r.get("key", "")).upper() == key), None)
        if rec is None:
            return False, "卡密不存在"
        if str(rec.get("expiry", "")) < today:
            return False, "卡密已过期"
        act = lic.setdefault("activated_keys", [])
        if key in act:
            return True, "该卡已激活过（本机已解锁）"
        act.append(key)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                _json.dump(lic, f, ensure_ascii=False, indent=2)
        except Exception:
            return False, "写入卡密库失败（文件被占用？）"
        return True, "激活成功 · 本机已解锁对应商品"
