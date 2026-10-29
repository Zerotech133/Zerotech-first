# -*- coding: utf-8 -*-
"""KM 知识库飞书联动枢纽（127.0.0.1:8099）。

职能：本地知识目录 → 飞书摘要卡 定时推送 + 每日统计展示。
  - 库管理：页面增删改知识库（目录路径 + 分类方式 + 目标 + 发送时间）
  - 定时推送：到点扫描库增量（对比快照）→ 摘要卡 → send_card 按映射发群/个人
  - 统计：km_stats.jsonl 每次推送一行（新增/更新/总量/发送结果），页面聚合
    今日统计与 14 日趋势；快速分类（按扩展名或一级子目录计数）

架构沿 publish_schedule_server.py（8095）已验证配方：
  15s tick + 当日去重、原子写、RLock、心跳真实化、body 上限、仅绑回环。
目标注册表复用 news_config.push_targets（单一事实源），km_config.extra_targets
可追加（如新群），不改动既有推送配置。

保活：并入计划任务「AI工作站CowAgent看门狗」（ops/keepalive_9899.py，8099 探针）。
日志：work/km_hub.out（服务 stdout）/ work/km_stats.jsonl（统计流水）。
"""

import argparse
import logging
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
NEWS = os.path.join(os.path.dirname(HERE), "news_collector")
sys.path.insert(0, HERE)
sys.path.insert(0, NEWS)

PORT = 8099
WORK = os.path.join(HERE, "work")
CONFIG_PATH = os.path.join(WORK, "km_config.json")
SNAP_DIR = os.path.join(WORK, "km_snapshots")
STATS_PATH = os.path.join(WORK, "km_stats.jsonl")
LOG_PATH = os.path.join(
    WORK, "km_hub.out"
)  # 本服务日志尾巴（log() 写入处）；旧读 publish_watchdog.log 属跨服务串台，K-STOCK-394 纠偏
NEWS_CFG = os.path.join(NEWS, "news_config.json")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
BODY_CAP = 65536
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "$RECYCLE.BIN"}

_lock = threading.RLock()  # 配置/状态/fired 写锁
_run_lock = threading.Lock()  # 推送串行化（一次只跑一个库的扫描+发送）
_state = {"running": False, "last_tick": 0.0, "fired": {}, "last_results": {}}


# ---------- 基础设施 ----------


def atomic_write_json(path, obj):
    import tempfile

    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "km_feishu_hub.py", exc
            )
        raise


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"enabled": True, "libraries": [], "extra_targets": []}


def save_config(cfg):
    atomic_write_json(CONFIG_PATH, cfg)


def known_targets():
    """目标注册表：news_config.push_targets（权威） + km_config.extra_targets。"""
    targets = []
    try:
        with open(NEWS_CFG, encoding="utf-8") as f:
            targets = list(json.load(f).get("push_targets", []))
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "km_feishu_hub.py", exc
        )
    cfg = load_config()
    names = {t.get("name") for t in targets}
    for t in cfg.get("extra_targets", []):
        if t.get("name") not in names:
            targets.append(t)
    return targets


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(WORK, "km_hub.out"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "km_feishu_hub.py", exc
        )
    print(line, end="", flush=True)


# ---------- 库扫描（增量 + 分类） ----------


def _walk_files(root, exts, max_files):
    files = {}
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if exts and os.path.splitext(fn)[1].lower() not in exts:
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            files[rel] = [int(st.st_mtime), st.st_size]
            if len(files) >= max_files:
                truncated = True
                return files, truncated
    return files, truncated


def _snapshot_path(lib_name):
    return os.path.join(
        SNAP_DIR, hashlib.md5(lib_name.encode("utf-8"), usedforsecurity=False).hexdigest() + ".json"
    )


def _load_snapshot(lib_name):
    try:
        with open(_snapshot_path(lib_name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scan_lib(lib, commit=True):
    """扫描库目录，与快照对比得增量。commit=True 时写回新快照。

    返回 {added, changed, removed, total, categories, truncated}。
    首次扫描（无快照）且 commit=True → 全量作为基线，added 记 0。"""
    root = lib["path"]
    exts = {e.lower() for e in (lib.get("exts") or [])}
    files, truncated = _walk_files(root, exts, int(lib.get("max_files", 20000)))
    prev = _load_snapshot(lib["name"])
    first = prev is None
    prev = prev or {}
    added = sorted(set(files) - set(prev))
    changed = sorted(k for k in set(files) & set(prev) if files[k] != prev[k])
    removed = sorted(set(prev) - set(files))
    if commit:
        atomic_write_json(_snapshot_path(lib["name"]), files)
    if first and commit:
        added, changed = [], []  # 基线：首扫不报历史存量
    # 分类计数（新增+更新参与"今日动过"的展示）
    cats = {}
    for rel in added + changed:
        if lib.get("category_by") == "subdir":
            key = rel.split("/", 1)[0] if "/" in rel else "(根目录)"
        else:
            key = os.path.splitext(rel)[1].lower() or "(无扩展名)"
        cats[key] = cats.get(key, 0) + 1
    cats = dict(sorted(cats.items(), key=lambda kv: -kv[1])[:12])
    return {
        "added": added,
        "changed": changed,
        "removed": len(removed),
        "total": len(files),
        "categories": cats,
        "truncated": truncated,
    }


# ---------- 卡片与推送 ----------


def build_card(lib, res, trigger):
    n_add, n_chg = len(res["added"]), res.get("n_changed", len(res["changed"]))
    # K-PUSH-008：v3 统一卡（有更新=蓝域资讯，无更新=绿域例行）
    domain = "news" if (n_add or n_chg) else "daily"
    head = "**新增 %d · 更新 %d · 库存量 %d**%s" % (
        n_add,
        n_chg,
        res["total"],
        "（文件数超上限，未全扫）" if res.get("truncated") else "",
    )
    if n_add + n_chg == 0:
        head += "\n本次无新增/更新。"
    extra = [{"tag": "markdown", "content": head}]
    if res["categories"]:
        cat_md = " ｜ ".join(
            "**%s** ×%d" % (k, v) for k, v in res["categories"].items()
        )
        extra.append({"tag": "hr"})
        extra.append({"tag": "markdown", "content": "**分类** " + cat_md})
    items = res["added"][:10] + res["changed"][:5]
    if items:
        lines = "\n".join("- " + i for i in items)
        extra.append({"tag": "hr"})
        extra.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {"tag": "plain_text", "content": "本次变更清单（前 15）"}
                },
                "border": {"color": "grey"},
                "padding": "8px 12px",
                "elements": [{"tag": "markdown", "content": lines}],
            }
        )
    from feishu_card_v2 import build_v3

    card, _audit = build_v3(
        domain,
        "%s · 知识库摘要" % lib["name"],
        source="老田的AI工作站 · KM 知识库联动 · %s · 路径 %s"
        % ("手动" if trigger == "manual" else "定时", lib["path"]),
        extra_els=extra,
    )
    return card


def push_lib(lib, trigger):
    """扫描 → 发卡 → 统计留痕。返回结果 dict（也写入 _state.last_results）。"""
    name = lib["name"]
    with _run_lock:
        err = None
        sent_ok, sent_targets = 0, []
        try:
            res = scan_lib(lib, commit=True)
            res["n_changed"] = len(res["changed"])
            n_dyn = len(res["added"]) + res["n_changed"]
            skip = (
                bool(lib.get("skip_empty", True))
                and trigger == "schedule"
                and n_dyn == 0
            )
            if skip:
                sent_targets = ["(无变更，跳过发送)"]
            else:
                # 配额生效（仅非 internal 套餐；internal 永不拦）
                try:
                    sys.path.insert(0, os.path.dirname(HERE))
                    import push_flow

                    _, _, _, qblk = push_flow.quota_check()
                except Exception:
                    qblk = False
                if qblk:
                    err = "日配额已用尽，跳过发送（quota_block）"
                    sent_targets = ["(配额拦截)"]
                    try:  # 拦截也留痕（ok=false，不计费）
                        push_flow.record(
                            kind="quota_block:km_card",
                            channel="feishu",
                            target=name,
                            count=1,
                            ok=False,
                        )
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "silent-except %s: %s", "km_feishu_hub.py", exc
                        )
                else:
                    from feishu_card_v2 import send_card

                    card = build_card(lib, res, trigger)
                    tmap = {t["name"]: t for t in known_targets()}
                    for tn in lib.get("targets", []):
                        t = tmap.get(tn)
                        if not t:
                            continue
                        try:
                            # K-STOCK-414：send_card 返回 (ok, why) 二元组，
                            # 误用单值接收时非空元组恒真→失败恒计成功（污染计费）
                            ok, _why = send_card(
                                card,
                                receive_id=t["id"],
                                receive_id_type=t["type"],
                                kind="km_card",
                            )
                        except Exception as e:  # noqa: BLE001
                            ok = False
                            print("km_card send fail %s: %r" % (tn, e), file=sys.stderr)
                        if ok:
                            sent_ok += 1
                            sent_targets.append(tn)
                    # 统一发送流水（商业化计费依据；fail-safe）
                    try:
                        push_flow.record(
                            kind="km_card",
                            channel="feishu",
                            target=",".join(sent_targets) or "none",
                            count=1,
                            ok=sent_ok > 0,
                        )
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "silent-except %s: %s", "km_feishu_hub.py", exc
                        )
        except Exception as e:  # noqa: BLE001
            err = "%s" % e
            res = {
                "added": [],
                "changed": [],
                "n_changed": 0,
                "total": 0,
                "categories": {},
                "truncated": False,
                "removed": 0,
            }
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "lib": name,
            "trigger": trigger,
            "added": len(res["added"]),
            "changed": res["n_changed"],
            "total": res["total"],
            "sent_ok": sent_ok,
            "sent_targets": sent_targets,
            "error": err,
        }
        try:
            os.makedirs(WORK, exist_ok=True)
            with open(STATS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "km_feishu_hub.py", exc
            )
        result = {
            "ts": row["ts"],
            "trigger": trigger,
            "added": row["added"],
            "changed": row["changed"],
            "sent_ok": sent_ok,
            "sent_targets": sent_targets,
            "error": err,
            "ok": err is None
            and (
                sent_ok > 0
                or "(无变更" in "".join(sent_targets)
                or not lib.get("targets")
            ),
        }
        with _lock:
            _state["last_results"][name] = result
        log(
            "[km] %s %s 新增%d 更新%d 发送%d/%d %s"
            % (
                name,
                trigger,
                row["added"],
                row["changed"],
                sent_ok,
                len(lib.get("targets", [])),
                err or "ok",
            )
        )
        return result


# ---------- 调度 ----------


def scheduler_loop():
    while True:
        try:
            with _lock:
                _state["last_tick"] = time.time()
            cfg = load_config()
            if not cfg.get("enabled", True):
                time.sleep(15)
                continue
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            day = now.strftime("%Y-%m-%d")
            for lib in cfg.get("libraries", []):
                if not lib.get("enabled", True):
                    continue
                if hhmm not in (lib.get("times") or []):
                    continue
                key = "%s|%s|%s" % (day, lib["name"], hhmm)
                with _lock:
                    if key in _state["fired"]:
                        continue
                    _state["fired"][key] = time.time()
                    if len(_state["fired"]) > 60:
                        for k in sorted(_state["fired"])[:-60]:
                            _state["fired"].pop(k, None)
                threading.Thread(
                    target=push_lib, args=(lib, "schedule"), daemon=True
                ).start()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(15)


def heartbeat_ok():
    return time.time() - _state["last_tick"] < 30


def next_fires(cfg):
    now = datetime.now()
    out = []
    for lib in cfg.get("libraries", []):
        if not lib.get("enabled", True):
            continue
        for t in sorted(lib.get("times") or []):
            fire = datetime.strptime(
                "%s %s" % (now.strftime("%Y-%m-%d"), t), "%Y-%m-%d %H:%M"
            )
            if fire >= now:
                out.append((fire.strftime("%m-%d %H:%M"), lib["name"]))
    return sorted(out)


def stats_digest():
    today = datetime.now().strftime("%Y-%m-%d")
    days = []
    for i in range(13, -1, -1):
        from datetime import timedelta

        days.append((datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"))
    rows = []
    try:
        with open(STATS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "km_feishu_hub.py", exc
        )
    today_agg = {}
    for r in rows:
        if r.get("date") == today:
            a = today_agg.setdefault(r["lib"], {"added": 0, "sent": 0, "runs": 0})
            a["added"] += r.get("added", 0)
            a["sent"] += r.get("sent_ok", 0)
            a["runs"] += 1
    trend = {}
    for r in rows:
        if r.get("date") in days:
            a = trend.setdefault(r["date"], {"added": 0, "sent": 0})
            a["added"] += r.get("added", 0)
            a["sent"] += r.get("sent_ok", 0)
    trend = [
        {
            "date": d,
            "added": trend.get(d, {}).get("added", 0),
            "sent": trend.get(d, {}).get("sent", 0),
        }
        for d in days
    ]
    return today_agg, trend


def tail_log(n=15):
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


# ---------- HTTP ----------


def esc(s):
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/state":
            cfg = load_config()
            today, trend = stats_digest()
            with _lock:
                lasts = dict(_state["last_results"])
            libs = []
            for lib in cfg.get("libraries", []):
                libs.append(
                    {
                        k: lib.get(k)
                        for k in (
                            "name",
                            "path",
                            "times",
                            "targets",
                            "exts",
                            "category_by",
                            "skip_empty",
                            "enabled",
                        )
                    }
                )
                libs[-1]["last"] = lasts.get(lib["name"])
            self._json(
                {
                    "heartbeat": heartbeat_ok(),
                    "enabled": cfg.get("enabled", True),
                    "libs": libs,
                    "next": next_fires(cfg),
                    "targets": known_targets(),
                    "today": today,
                    "trend": trend,
                    "running": _state["running"],
                    "log_tail": tail_log(),
                }
            )
        elif p == "/health":
            self._json({"ok": True, "ts": datetime.now().isoformat(timespec="seconds")})
        elif p == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/act":
            return self._json({"ok": False, "error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not (0 < n <= BODY_CAP):
                return self._json({"ok": False, "error": "body 大小非法"}, 400)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, OSError) as e:
            return self._json({"ok": False, "error": "请求体解析失败: %s" % e}, 400)
        try:
            self._json(dispatch(req))
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": "%s" % e}, 500)


def _validate_lib(lib, cfg):
    name = (lib.get("name") or "").strip()
    if not name or len(name) > 40:
        return "库名必填且 ≤40 字"
    if any(
        l["name"] == name
        for l in cfg.get("libraries", [])
        if l["name"] != lib.get("_orig_name")
    ):
        return "库名已存在：%s" % name
    path = (lib.get("path") or "").strip()
    if not os.path.isdir(path):
        return "目录不存在：%s" % path
    times = lib.get("times") or []
    if any(not TIME_RE.match(t) for t in times):
        return "时间格式非法（需 HH:MM）：%s" % times
    tnames = {t["name"] for t in known_targets()}
    bad = [t for t in (lib.get("targets") or []) if t not in tnames]
    if bad:
        return "目标不存在（需先在 extra_targets 或 news_config 定义）：%s" % bad
    for e in lib.get("exts") or []:
        if not EXT_RE.match(e):
            return "扩展名格式非法（需 .xxx）：%s" % e
    if lib.get("category_by") not in (None, "", "ext", "subdir"):
        return "category_by 只能是 ext 或 subdir"
    return None


def dispatch(req):
    act = req.get("action")
    cfg = load_config()
    libs = cfg.setdefault("libraries", [])

    if act == "add_lib":
        lib = req.get("lib") or {}
        err = _validate_lib(lib, cfg)
        if err:
            return {"ok": False, "error": err}
        lib.pop("_orig_name", None)
        lib.setdefault("enabled", True)
        lib.setdefault("category_by", "ext")
        lib.setdefault("skip_empty", True)
        libs.append(lib)
        save_config(cfg)
        res = scan_lib(lib, commit=True)  # 基线扫描：首扫记 0 增量
        return {"ok": True, "baseline": {"total": res["total"]}, "config": cfg}

    if act == "update_lib":
        lib = req.get("lib") or {}
        lib["_orig_name"] = req.get("orig_name")
        err = _validate_lib(lib, cfg)
        if err:
            return {"ok": False, "error": err}
        lib.pop("_orig_name", None)
        libs[:] = [l for l in libs if l["name"] != req.get("orig_name")]
        libs.append(lib)
        save_config(cfg)
        return {"ok": True, "config": cfg}

    if act == "remove_lib":
        name = req.get("name")
        libs[:] = [l for l in libs if l["name"] != name]
        save_config(cfg)
        try:
            os.remove(_snapshot_path(name))
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "km_feishu_hub.py", exc
            )
        return {"ok": True, "config": cfg}

    if act == "toggle_lib":
        for l in libs:
            if l["name"] == req.get("name"):
                l["enabled"] = not l.get("enabled", True)
        save_config(cfg)
        return {"ok": True, "config": cfg}

    if act == "toggle":
        cfg["enabled"] = not cfg.get("enabled", True)
        save_config(cfg)
        return {"ok": True, "config": cfg}

    if act == "add_target":
        t = req.get("target") or {}
        if (
            not t.get("name")
            or not t.get("id")
            or t.get("type") not in ("chat_id", "open_id")
        ):
            return {"ok": False, "error": "target 需含 name/id/type(chat_id|open_id)"}
        if any(x["name"] == t["name"] for x in known_targets()):
            return {"ok": False, "error": "目标名已存在：%s" % t["name"]}
        cfg.setdefault("extra_targets", []).append(t)
        save_config(cfg)
        return {"ok": True, "config": cfg}

    if act == "run_now":
        name = req.get("name")
        lib = next((l for l in libs if l["name"] == name), None)
        if not lib:
            return {"ok": False, "error": "库不存在：%s" % name}
        if _state["running"]:
            return {"ok": False, "error": "已有推送在进行"}
        _state["running"] = True

        def worker():
            try:
                push_lib(lib, "manual")
            finally:
                _state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    if act == "run_all":
        if _state["running"]:
            return {"ok": False, "error": "已有推送在进行"}
        enabled = [l for l in libs if l.get("enabled", True)]
        _state["running"] = True

        def worker():
            try:
                for l in enabled:
                    push_lib(l, "manual")
            finally:
                _state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "count": len(enabled)}

    return {"ok": False, "error": "unknown action: %s" % act}


# ---------- 页面 ----------

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>知识库飞书联动</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--dim:#8b949e;--line:#30363d;--blue:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922}
body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{max-width:1080px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--dim);font-size:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:14px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:normal;font-size:12px}
button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer}
button:hover{border-color:var(--blue)}button.primary{background:#1f6feb;color:#fff;border-color:#1f6feb}
button.danger{color:var(--red)}button:disabled{opacity:.5;cursor:default}
input,select{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:5px 8px;font-size:12px;width:100%;box-sizing:border-box}
.grid{display:grid;grid-template-columns:1fr 2fr 1fr 1fr 1fr auto;gap:8px;align-items:end}
.tag{display:inline-block;background:#1c2a44;color:var(--blue);border-radius:4px;padding:1px 6px;font-size:11px;margin:1px}
.ok{color:var(--green)}.err{color:var(--red)}.warn{color:var(--amber)}.dim{color:var(--dim)}
.bar{background:var(--blue);border-radius:2px 2px 0 0;min-height:2px}
.bars{display:flex;align-items:flex-end;gap:6px;height:80px}
.bars>div{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%}
.bars span{font-size:10px;color:var(--dim)}
#msg{position:fixed;top:12px;right:12px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;font-size:13px;display:none;box-shadow:0 4px 14px rgba(0,0,0,.45)}
</style></head><body><div class="wrap">
<h1>📚 知识库 × 飞书联动</h1><div class="sub" id="sub">加载中…</div>

<div class="card"><h2>库管理 <span class="dim" style="font-weight:normal">（目录新增文件 → 定时摘要卡发到目标）</span></h2>
<table id="libs"><thead><tr><th>库 / 路径</th><th>分类</th><th>发送时间</th><th>目标</th><th>最近结果</th><th>操作</th></tr></thead><tbody></tbody></table>
<h2 style="margin-top:14px">添加库</h2>
<div class="grid">
<input id="f_name" placeholder="库名（如：缠论笔记）">
<input id="f_path" placeholder="目录路径，如 E:\\老田的AI助手\\portal\\docs">
<input id="f_times" placeholder="时间 HH:MM，逗号分隔">
<input id="f_targets" placeholder="目标，逗号分隔（见下方目标表）">
<input id="f_exts" placeholder="扩展名过滤，如 .md,.txt（空=全部）">
<div><button class="primary" onclick="addLib()">添加</button></div>
</div>
<div style="margin-top:6px"><label style="font-size:12px">分类方式 <select id="f_cat" style="width:auto"><option value="ext">按扩展名</option><option value="subdir">按一级子目录</option></select>
　<label style="font-size:12px"><input type="checkbox" id="f_skip" checked> 无变更时跳过发送</label></div>
</div>

<div class="card"><h2>发送目标 <span class="dim" style="font-weight:normal">（个人/群 → 由 news_config.push_targets 权威定义，此处可追加）</span></h2>
<table id="targets"></table>
<div style="margin-top:8px;display:grid;grid-template-columns:1fr 2fr 1fr auto;gap:8px">
<input id="t_name" placeholder="目标名（如：项目群）">
<input id="t_id" placeholder="oc_ 开头的 chat_id（群）或 ou_ 开头的 open_id（个人）">
<select id="t_type"><option value="chat_id">群 chat_id</option><option value="open_id">个人 open_id</option></select>
<button onclick="addTarget()">追加目标</button></div></div>

<div class="card"><h2>今日统计 <span id="today_total" class="dim" style="font-weight:normal"></span></h2>
<table id="today"></table></div>

<div class="card"><h2>近 14 天趋势 <span class="dim" style="font-weight:normal">（柱=新增文件数，数字=发送成功条数）</span></h2>
<div class="bars" id="trend"></div></div>

<div class="card"><h2>巡检日志（尾部）</h2><pre id="log" style="font-size:11px;white-space:pre-wrap;max-height:200px;overflow:auto;color:var(--dim)"></pre></div>
</div><div id="msg"></div>
<script>
const $=id=>document.getElementById(id);
function msg(t,bad){const m=$('msg');m.textContent=t;m.style.display='block';m.style.color=bad?'var(--red)':'var(--green)';setTimeout(()=>m.style.display='none',4000)}
async function act(body){try{const r=await fetch('/api/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!d.ok)msg(d.error||'失败',true);return d}catch(e){msg('请求失败：'+e,true);return{ok:false}}}
async function refresh(){try{const d=await(await fetch('/api/state')).json();
$('sub').textContent='调度器 '+(d.heartbeat?'✅ 运行中':'❌ 心跳丢失')+' · 总开关 '+(d.enabled?'启用':'已停用')+(d.running?' · 推送进行中…':'');
$('libs').querySelector('tbody').innerHTML=d.libs.map(l=>{
 const last=l.last;const ls=!last?'<span class=dim>未运行</span>':
  (last.ok?'<span class=ok>✔</span> ':'<span class=err>✘ '+esc(last.error||'')+'</span> ')+
  '新增'+last.added+' 更新'+last.changed+' → '+esc((last.sent_targets||[]).join(','))+
  ' <span class=dim>'+esc(last.ts)+'</span>';
 return '<tr><td><b>'+esc(l.name)+'</b>'+(l.enabled?'':' <span class=warn>停用</span>')+'<br><span class=dim style="font-size:11px">'+esc(l.path)+'</span></td>'
 +'<td>'+(l.category_by==='subdir'?'子目录':'扩展名')+'</td>'
 +'<td>'+esc((l.times||[]).join('、')||'<span class=dim>未排程</span>')+'</td>'
 +'<td>'+(l.targets||[]).map(t=>'<span class=tag>'+esc(t)+'</span>').join('')+'</td>'
 +'<td style="font-size:11px">'+ls+'</td>'
 +'<td><button onclick="act({action:\'run_now\',name:\''+esc(l.name)+'\'}).then(refresh)">推送</button> '
 +'<button onclick="act({action:\'toggle_lib\',name:\''+esc(l.name)+'\'}).then(refresh)">'+(l.enabled?'停用':'启用')+'</button> '
 +'<button class=danger onclick="if(confirm(\\'删除库 '+esc(l.name)+' ？（不影响目录文件）\\'))act({action:\\'remove_lib\\',name:\\''+esc(l.name)+'\\'}).then(refresh)">删除</button></td></tr>'}).join('')
 ||'<tr><td colspan=6 class=dim>暂无库，请在下方添加</td></tr>';
$('targets').innerHTML='<thead><tr><th>名称</th><th>类型</th><th>ID</th></tr></thead><tbody>'+
 d.targets.map(t=>'<tr><td><b>'+esc(t.name)+'</b></td><td>'+(t.type==='chat_id'?'群':'个人')+'</td><td class=dim style="font-size:11px">'+esc(t.id)+'</td></tr>').join('')+'</tbody>';
const te=Object.entries(d.today||{});$('today_total').textContent='（共 '+te.reduce((a,[,v])=>a+v.added,0)+' 条新增 · '+te.reduce((a,[,v])=>a+v.sent,0)+' 条发送）';
$('today').innerHTML='<thead><tr><th>库</th><th>新增</th><th>发送</th><th>推送次数</th></tr></thead><tbody>'+
 (te.map(([k,v])=>'<tr><td>'+esc(k)+'</td><td><b>'+v.added+'</b></td><td>'+v.sent+'</td><td>'+v.runs+'</td></tr>').join('')||'<tr><td colspan=4 class=dim>今日暂无推送记录</td></tr>')+'</tbody>';
const mx=Math.max(1,...d.trend.map(t=>t.added));
$('trend').innerHTML=d.trend.map(t=>'<div title="'+t.date+' 新增'+t.added+' 发送'+t.sent+'"><span>'+t.sent+'</span><div class=bar style="height:'+Math.round(t.added/mx*60)+'px"></div><span>'+t.date.slice(5)+'</span></div>').join('');
$('log').textContent=(d.log_tail||[]).join('\\n');
}catch(e){$('sub').textContent='状态获取失败：'+e}}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
async function addLib(){const sp=t=>t.split(',').map(s=>s.trim()).filter(Boolean);
 const lib={name:$('f_name').value.trim(),path:$('f_path').value.trim(),times:sp($('f_times').value),targets:sp($('f_targets').value),exts:sp($('f_exts').value).map(e=>e[0]==='.'?e:'.'+e),category_by:$('f_cat').value,skip_empty:$('f_skip').checked};
 if(!lib.name||!lib.path)return msg('库名与路径必填',true);
 const d=await act({action:'add_lib',lib});if(d.ok){msg('已添加，基线存量 '+d.baseline.total+' 个文件');['f_name','f_path','f_times','f_targets','f_exts'].forEach(i=>$(i).value='');refresh()}}
async function addTarget(){const d=await act({action:'add_target',target:{name:$('t_name').value.trim(),id:$('t_id').value.trim(),type:$('t_type').value}});
 if(d.ok){msg('目标已追加');$('t_name').value='';$('t_id').value='';refresh()}else msg(d.error||'失败',true)}
refresh();setInterval(refresh,5000);
</script></body></html>"""


# ---------- main ----------


def main():
    parser = argparse.ArgumentParser(description="KM 知识库飞书联动枢纽")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--bg",
        action="store_true",
        help="脱离会话后台驻留（DETACHED_PROCESS，日志到 work/km_hub.out）",
    )
    args = parser.parse_args()

    if args.bg:
        out = open(os.path.join(WORK, "km_hub.out"), "ab")
        subprocess_detached(args.port, out)
        print(
            "[ok] 已后台驻留：http://127.0.0.1:%d/（日志 work/km_hub.out）" % args.port
        )
        return 0

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(SNAP_DIR, exist_ok=True)
    # 崩溃取证：stdout/stderr 改行缓冲（此前块缓冲在进程异常死亡时丢失启动行与异常栈，
    # km_hub.out 长期停留在旧内容即此因）；faulthandler 直写 fd，硬崩留痕 km_hub_fault.log。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(line_buffering=True)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s", "km_feishu_hub.py", exc
            )
    try:
        import faulthandler

        faulthandler.enable(open(os.path.join(WORK, "km_hub_fault.log"), "ab"))
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "silent-except %s: %s", "km_feishu_hub.py", exc
        )
    print(
        "[start] PID=%s %s port=%d"
        % (os.getpid(), datetime.now().strftime("%m-%d %H:%M:%S"), args.port)
    )
    if not os.path.exists(CONFIG_PATH):
        save_config(load_config())
    threading.Thread(target=scheduler_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print("[失败] 端口 %d 占用: %s" % (args.port, e), file=sys.stderr)
        return 1
    print("[ok] KM 知识库飞书联动：http://127.0.0.1:%d/" % args.port)
    cfg = load_config()
    print(
        "[ok] 已注册库：%s（enabled=%s）"
        % ([l["name"] for l in cfg.get("libraries", [])], cfg.get("enabled", True))
    )
    try:
        srv.serve_forever()
    finally:
        print(
            "[exit] PID=%s %s"
            % (os.getpid(), datetime.now().strftime("%m-%d %H:%M:%S"))
        )
    return 0


def subprocess_detached(port, out):
    import subprocess

    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--port", str(port)],
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=out,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )


if __name__ == "__main__":
    sys.exit(main())
