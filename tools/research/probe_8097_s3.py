# -*- coding: utf-8 -*-
"""S3 诊断探针：8097 推送中台「带钥只读」实证 + 与底层存储对账。

背景（K-ORG-20261107-GATESAFEURL 后续 S3）：
  /health 只回 {"ok":true}，不校验 DB —— 进程活着 ≠ 数据活着。
  /api/state、/api/decisions 无钥 403，此前无法确认真实数据面。

本脚本做三件事，且**绝不打印任何密钥明文**：
  1) 从 portal/work/push_secrets.json 取钥（env 优先，与 server 同规则），
     逐端点试推，只回报「哪个 header 拿到了 200」；
  2) 打印各端点响应结构（顶层 key / 列表长度），不打印记录内容；
  3) 直读 portal/work/notification_decisions.sqlite3 做同名计数对账，
     证明 API 返回的是「实时存储」而非陈旧缓存。

用法：
  python tools/research/probe_8097_s3.py            # 全量
  python tools/research/probe_8097_s3.py --json     # 机器可读

退出码：0 全绿；2 有端点未授权/失败；3 对账不一致。
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PORTAL = os.path.join(REPO, "portal")
SECRETS = os.path.join(PORTAL, "work", "push_secrets.json")
DEC_DB = os.path.join(PORTAL, "work", "notification_decisions.sqlite3")
BASE = "http://127.0.0.1:8097"

# (路径, 候选 (header, secrets 中的键 或 环境变量名))
CANDIDATES = {
    "/api/state": [("X-Push-Admin-Key", ("env", "PUSH_ADMIN_KEY"), ("file", "push_admin_key"))],
    "/api/targets": [("X-Push-Admin-Key", ("env", "PUSH_ADMIN_KEY"), ("file", "push_admin_key"))],
    "/api/decisions": [
        ("X-Decision-Key", ("env", "PUSH_DECISION_KEY"), ("file", "decision_key_admin_ui")),
        ("X-Decision-Key", ("env", "PUSH_DECISION_CLIENT_KEY"), ("file", "decision_key_client")),
        ("X-Decision-Key", ("env", "PUSH_DECISION_HERMES_KEY"), ("file", "decision_key_hermes")),
    ],
    "/api/decision-groups": [
        ("X-Decision-Key", ("env", "PUSH_DECISION_KEY"), ("file", "decision_key_admin_ui")),
    ],
}

OPEN_ENDPOINTS = ["/health", "/api/public/ops"]


def _secrets():
    try:
        with open(SECRETS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _resolve(spec, secrets):
    kind, name = spec
    if kind == "env":
        return (os.environ.get(name) or "").strip()
    return str(secrets.get(name) or "").strip()


def _count(obj):
    """/api/decisions 回的是 {decisions:[...]} 而非裸 list——按主表键取条数。"""
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for k in ("decisions", "groups", "targets", "items", "rows"):
            if isinstance(obj.get(k), list):
                return len(obj[k])
    return None


def _get(path, headers=None, timeout=25.0):
    req = urllib.request.Request(BASE + path, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)


def _shape(obj, depth=0):
    """只回结构：dict→keys，list→len + 首元素 keys。"""
    if isinstance(obj, dict):
        out = {"__type": "dict", "keys": sorted(obj.keys())}
        return out
    if isinstance(obj, list):
        first = obj[0] if obj else None
        return {
            "__type": "list",
            "len": len(obj),
            "item_keys": sorted(first.keys()) if isinstance(first, dict) else None,
        }
    return {"__type": type(obj).__name__}


def probe_authed(path, secrets, report):
    for header, env_spec, file_spec in CANDIDATES.get(path, []):
        key = _resolve(env_spec, secrets) or _resolve(file_spec, secrets)
        if not key:
            report.append({"path": path, "header": header, "status": None,
                           "note": "key 缺失（%s/%s）" % (env_spec[1], file_spec[1])})
            continue
        st, raw = _get(path, {header: key})
        rec = {"path": path, "header": header, "status": st,
               "key_len": len(key), "key_src": "env" if _resolve(env_spec, secrets) else "file"}
        if st == 200:
            try:
                obj = json.loads(raw)
                rec["shape"] = _shape(obj)
                rec["count"] = _count(obj)
            except ValueError:
                rec["shape"] = {"__type": "non-json"}
            report.append(rec)
            return True
        rec["body_head"] = raw[:120]
        report.append(rec)
    return False


def probe_open(path, report):
    st, raw = _get(path)
    rec = {"path": path, "header": None, "status": st}
    try:
        rec["shape"] = _shape(json.loads(raw))
    except ValueError:
        rec["shape"] = {"__type": "non-json"}
    report.append(rec)
    return st == 200


def db_truth():
    if not os.path.exists(DEC_DB):
        return {"exists": False}
    out = {"exists": True, "path": os.path.relpath(DEC_DB, REPO)}
    try:
        db = sqlite3.connect("file:%s?mode=ro" % DEC_DB.replace("\\", "/"), uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        out["decisions_total"] = db.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        out["decisions_last30"] = db.execute(
            "SELECT COUNT(*) c FROM (SELECT 1 FROM decisions ORDER BY created_at DESC LIMIT 30)"
        ).fetchone()["c"]
        row = db.execute("SELECT MAX(created_at) m FROM decisions").fetchone()
        out["latest_created_at"] = row["m"] if row else None
        try:
            out["announcement_approvals"] = db.execute(
                "SELECT COUNT(*) c FROM announcement_approvals").fetchone()["c"]
        except sqlite3.Error:
            out["announcement_approvals"] = None
        db.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    secrets = _secrets()
    report = []
    ok_all = True
    for p in OPEN_ENDPOINTS:
        ok_all &= probe_open(p, report)
    for p in CANDIDATES:
        ok_all &= probe_authed(p, secrets, report)

    truth = db_truth()
    payload = {"base": BASE, "secrets_present": bool(secrets),
               "secret_keys_count": len(secrets), "probes": report, "db": truth}

    # 对账：/api/decisions 返回的条数 vs DB 中最近 30 条
    api_len = None
    for r in report:
        if r.get("path") == "/api/decisions" and r.get("status") == 200:
            api_len = r.get("count")
    mismatch = None
    if api_len is not None and truth.get("decisions_last30") is not None:
        mismatch = api_len != truth["decisions_last30"]
    payload["reconcile"] = {"api_decisions_len": api_len,
                            "db_last30": truth.get("decisions_last30"),
                            "match": (None if mismatch is None else not mismatch)}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("== 8097 S3 探针 ==")
        print("secrets_present=%s keys=%d" % (payload["secrets_present"], payload["secret_keys_count"]))
        for r in report:
            st = r.get("status")
            extra = ""
            if r.get("shape"):
                sh = r["shape"]
                extra = " n=%s keys=%s" % (r.get("count"), sh.get("keys") or sh.get("item_keys"))
            if r.get("note"):
                extra += " note=%s" % r["note"]
            print("  %-22s %-18s -> %s%s" % (r["path"], r.get("header") or "(open)", st, extra))
        print("== DB 直读 ==")
        for k, v in truth.items():
            print("  %-22s %s" % (k, v))
        rc = payload["reconcile"]
        print("== 对账 ==")
        print("  api=%s db_last30=%s match=%s" % (rc["api_decisions_len"], rc["db_last30"], rc["match"]))

    if not ok_all:
        return 2
    if mismatch:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
