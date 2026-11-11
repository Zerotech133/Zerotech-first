# -*- coding: utf-8 -*-
"""模型管理控制台 API（10-18 · 纯标准库 · 127.0.0.1:8098）

对齐 portal 家族模式（8091 控制台桥 / 8096 军团大屏）。
端口 8098：8097 早被 10-18 06:00 早班批处理的不明 python 服务占用（探活响应
非本项目格式，按"杀进程前先辨认"纪律避让不杀）。
  GET  /                    管理页面（model_admin.html）
  GET  /api/models          三通道状态：本地 Ollama 模型库 + 云端智谱 + NovaMax 实例
  GET  /api/agents          全军团 34 岗绑定聚合（org.json 主源 + team_* 投影）
  POST /api/binding         改绑定 {agent_id, binding, note?}——org+team 双写，先备份
  POST /api/route           SmartRouter 决策 {task, agent_id?, grade?} → {binding, reason}
  POST /api/auto_assign     全军团自动适配 {apply: bool}——预览或落盘
  GET  /health              存活探测（含 svc 字段供真伪判别）
退出码约定：端口被占用（已有实例）→ 静默 exit 0（计划任务重复触发自愈，同 8091）。
"""
import json
import os
import shutil
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _audit_cfg_path():
    """审计模型配置：vault 优先（2026-11-10 L2 迁移），兼容旧 portal 明文。"""
    p = os.environ.get("AUDIT_AI_CONFIG_PATH") or r"C:\项目\_vault\secrets\portal_audit_ai_config.json"
    if Path(p).exists():
        return Path(p)
    return ROOT / "portal" / "audit_ai_config.json"
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

import router as router_mod  # noqa: E402
import logging

ORG = ROOT / "agent_platform" / "registry" / "org.json"
REG = ROOT / "agent_platform" / "registry"
BACKUP_DIR = ROOT / "agent_platform" / "registry" / "binding_backups"
PAGE = ROOT / "portal" / "model_admin.html"

_valid_models_cache = {"ts": 0.0, "names": []}


def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 本地/云端通道探测

def _local_models():
    """Ollama /api/tags → [{name,size_gb}]"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open("http://127.0.0.1:11434/api/tags", timeout=4) as r:
            ms = json.load(r).get("models", [])
        return {"ok": True, "models": [
            {"name": m["name"], "size_gb": round(m["size"] / 1e9, 1),
             "modified": m.get("modified_at", "")[:10]} for m in ms]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "err": str(e)}


def _loaded_models():
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open("http://127.0.0.1:11434/api/ps", timeout=3) as r:
            return [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def _cloud_status():
    """云端通道：key 配置态 + 熔断器状态（不发真请求，毫秒级）"""
    key_file = _audit_cfg_path()
    has_key = False
    key_src = ""
    try:
        if key_file.exists() and json.loads(
                key_file.read_text(encoding="utf-8")).get("zhipu_key"):
            has_key, key_src = True, "audit_ai_config.json zhipu_key"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_model_admin.py", exc)
    if not has_key:
        import os
        if os.environ.get("GLM_API_KEY"):
            has_key, key_src = True, "GLM_API_KEY 环境变量"
    return {"ok": has_key, "key_src": key_src,
            "breaker": "closed" if router_mod.CLOUD_HEALTH.available else "open(冷却中)",
            "models": [{"id": k, **v} for k, v in router_mod.CLOUD_MODELS.items()]}


def _nova_status():
    """NovaMax 双实例探活（1234 视觉 / 1238 结构化）"""
    out = []
    for port, model in router_mod.SPECIAL_ROUTES.items():
        p = port.split("/")[1]
        alive = False
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open("http://127.0.0.1:%s/health" % p, timeout=2) as r:
                alive = (r.status == 200)
        except Exception:  # noqa: BLE001
            alive = False
        out.append({"port": p, "desc": model, "alive": alive})
    return out


# ---------------------------------------------------------------- 绑定聚合与写回

def _iter_agent_dicts():
    """产出 (source_file, dept, agent_dict)——org.json chief/部门档案 + 全 team_*.json"""
    try:
        org = json.loads(ORG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        org = {}
    chief = org.get("chief")
    if chief and chief.get("agent_id"):
        yield ORG, "chief", chief
    for d in org.get("departments", []):
        for ap in d.get("agent_profiles", []):
            yield ORG, d.get("dept", ""), ap
    for f in sorted(REG.glob("team_*.json")):
        try:
            team = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        dept = team.get("dept", "")
        for a in team.get("agents", []):
            yield f, dept, a


def _org_name_map():
    """agent_id → 中文名（org.json attributes 外的 name 字段/hanta），尽力而为"""
    names = {}
    try:
        org = json.loads(ORG.read_text(encoding="utf-8"))
        if org.get("chief", {}).get("name"):
            names[org["chief"]["agent_id"]] = org["chief"]["name"]
        for d in org.get("departments", []):
            for ap in d.get("agent_profiles", []):
                if ap.get("name"):
                    names[ap["agent_id"]] = ap["name"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_model_admin.py", exc)
    return names


def _hr_cn_names():
    """agent_id → 中文名（hr/<id>/resume.yaml 的 cn_name；10-18 全员三国名）"""
    names = {}
    hr = ROOT / "agent_platform" / "hr"
    if not hr.exists():
        return names
    for f in hr.glob("*/resume.yaml"):
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("cn_name:"):
                    names[f.parent.name] = s.split(":", 1)[1].strip()
                    break
        except Exception:  # noqa: BLE001
            continue
    return names


def _dept_titles():
    """dept → 官署名号（org.json cn_title / chief.office_title；10-18 授号）"""
    titles = {}
    try:
        org = json.loads(ORG.read_text(encoding="utf-8"))
        if org.get("chief", {}).get("office_title"):
            titles["chief"] = org["chief"]["office_title"]
        for d in org.get("departments", []):
            if d.get("cn_title"):
                titles[d.get("dept", "")] = d["cn_title"]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_model_admin.py", exc)
    return titles


def roster():
    """全军团绑定聚合：同 agent 多源时 org.json 优先，标注 sources"""
    names = _org_name_map()
    cn_names = _hr_cn_names()
    titles = _dept_titles()
    merged = {}
    for f, dept, a in _iter_agent_dicts():
        aid = a.get("agent_id")
        if not aid:
            continue
        entry = merged.setdefault(aid, {
            "agent_id": aid, "role_profile": a.get("role_profile", ""),
            "grade": a.get("grade", ""), "binding": a.get("llm_binding", ""),
            "note": a.get("binding_note", ""), "sources": [], "dept": dept,
            "name": names.get(aid, "")})
        entry["sources"].append(f.name)
        if f.name == "org.json" or (a.get("llm_binding") and not entry["binding"]):
            entry["binding"] = a.get("llm_binding", entry["binding"])
            entry["note"] = a.get("binding_note", entry["note"])
            entry["grade"] = a.get("grade", entry["grade"])
            entry["dept"] = dept or entry["dept"]
    for entry in merged.values():
        entry["cn_name"] = cn_names.get(entry["agent_id"], "")
        entry["dept_title"] = titles.get(entry.get("dept", ""), "")
    return sorted(merged.values(), key=lambda x: (x["grade"], x["agent_id"]))


def _backup_registry(tag):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / ("%s_%s" % (ts, tag))
    dst.mkdir()
    for f in [ORG] + list(REG.glob("team_*.json")):
        shutil.copy2(f, dst / f.name)
    return dst


def set_binding(agent_id, binding, note=None):
    """改绑定：org.json 与 team_*.json 里同名 agent 全部双写；先备份，写后校验

    修复（10-18）：旧版在内存解析树上改 a["llm_binding"] 后、写盘前又
    json.loads 重读磁盘——重读的 data 不含内存改动，写回的是原样内容
    （备份建了、返回 ok、实际零变更）。现改为重读后在 data 上重新定位落地。
    """
    if not binding or "/" not in binding:
        return {"ok": False, "err": "binding 需带前缀（ollama/ glm/ nova/）"}
    backup = _backup_registry("set_%s" % agent_id)
    targets = {}  # f -> 命中该 agent 的文件集合
    for f, _dept, a in _iter_agent_dicts():
        if a.get("agent_id") == agent_id:
            targets.setdefault(f, True)
    if not targets:
        return {"ok": False, "err": "未找到 agent %s" % agent_id, "backup": str(backup)}
    touched = []
    for f in targets:
        data = json.loads(f.read_text(encoding="utf-8"))
        nodes = []
        if f.name == "org.json":
            c = data.get("chief", {})
            if c.get("agent_id") == agent_id:
                nodes.append(c)
            for d in data.get("departments", []):
                for ap in d.get("agent_profiles", []):
                    if ap.get("agent_id") == agent_id:
                        nodes.append(ap)
        else:
            for a2 in data.get("agents", []):
                if a2.get("agent_id") == agent_id:
                    nodes.append(a2)
        if not nodes:
            continue
        for a2 in nodes:
            a2["llm_binding"] = binding
            if note:
                a2["binding_note"] = note
        json.dumps(data, ensure_ascii=False)  # 写前校验可序列化
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1 if f.name == "org.json" else 2),
                     encoding="utf-8")
        touched.append(f)
        _log("binding 写回 %s (%s=%s)" % (f.name, agent_id, binding))
    return {"ok": True, "touched": [f.name for f in touched], "backup": str(backup)}


# ---------------------------------------------------------------- 后勤卡（10-18：盯"静默死亡"类故障）

SFT_PATH = Path(r"C:\项目\蒸馏\output\sft_dataset.jsonl")
_GLM_PROBE = {"ts": 0.0, "ok": None, "note": ""}
GLM_ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"


def _glm_key():
    """包年 key 优先（与 _cloud_status/引擎 GLMCloudLLM 同口径）"""
    try:
        kf = _audit_cfg_path()
        if kf.exists():
            zk = json.loads(kf.read_text(encoding="utf-8")).get("zhipu_key", "")
            if zk:
                return zk
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_model_admin.py", exc)
    import os
    return os.environ.get("GLM_API_KEY", "")


def _glm_probe(max_age=600):
    """云端额度探测（缓存 10 分钟；429 不耗额度，探测无成本）"""
    if time.time() - _GLM_PROBE["ts"] < max_age and _GLM_PROBE["ok"] is not None:
        return dict(_GLM_PROBE)
    import re
    import urllib.error
    import urllib.request
    out = {"ts": time.time(), "ok": False, "note": "key 未配置"}
    key = _glm_key()
    if key:
        body = json.dumps({"model": "glm-5.3",
                           "messages": [{"role": "user", "content": "ping"}],
                           "max_tokens": 1, "stream": False}).encode()
        req = urllib.request.Request(GLM_ENDPOINT, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            op.open(req, timeout=20).read()
            out.update(ok=True, note="额度可用")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", detail)
            out.update(ok=False, note="HTTP %s%s" % (
                e.code, "｜额度重置 " + m.group(1) if m else "｜" + detail[:80]))
        except Exception as e:  # noqa: BLE001
            out.update(ok=False, note=str(e)[:80])
    _GLM_PROBE.update(out)
    return dict(out)


def _logistics():
    """军团后勤：SFT 蒸馏产出、云端额度、磁盘——静默故障预警位"""
    sft = {"rows": None, "mtime": None, "fresh_min": None}
    try:
        st = SFT_PATH.stat()
        age_min = (time.time() - st.st_mtime) / 60
        with SFT_PATH.open("rb") as f:
            rows = sum(1 for _ in f)
        sft = {"rows": rows, "mtime": time.strftime("%H:%M", time.localtime(st.st_mtime)),
               "fresh_min": round(age_min, 1)}
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_model_admin.py", exc)
    disk = shutil.disk_usage("C:\\")
    return {"sft": sft, "glm": _glm_probe(),
            "disk": {"free_gb": round(disk.free / 1e9), "pct": round(disk.used / disk.total * 100)},
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


# ---------------------------------------------------------------- 服务总控（10-18：一页管全部 Agent 服务）

SVC_FILE = ROOT / "portal" / "services.json"
_svc_cache = {"ts": 0.0, "data": None}


def _services_def():
    """服务注册表（缓存 30s）"""
    if _svc_cache["data"] is None or time.time() - _svc_cache["ts"] > 30:
        try:
            _svc_cache["data"] = json.loads(
                SVC_FILE.read_text(encoding="utf-8")).get("services", [])
            _svc_cache["ts"] = time.time()
        except Exception as e:  # noqa: BLE001
            _log("services.json 读取失败: %s" % e)
            _svc_cache["data"] = []
    return _svc_cache["data"]


def _ps_query(patterns):
    """一次 PowerShell 调用查多组命令行正则 → {pattern: [pid,...]}（正则需已转义）"""
    if not patterns:
        return {}
    arr = ", ".join("'" + p.replace("'", "''") + "'" for p in patterns)
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          "$out=@{};"
          "foreach($p in @(%s)){$out[$p]=@(Get-CimInstance Win32_Process | "
          "Where-Object {$_.CommandLine -match $p} | "
          "Select-Object -ExpandProperty ProcessId);};"
          "$out | ConvertTo-Json -Compress" % arr)
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        # 单 pattern 时 ConvertTo-Json 退化成单对象
        if isinstance(data, dict) and all(not isinstance(v, list) for v in data.values()) and data:
            data = {k: ([v] if v else []) for k, v in data.items()}
        out = {}
        for p in patterns:
            v = data.get(p, [])
            out[p] = v if isinstance(v, list) else ([v] if v else [])
        return out
    except Exception as e:  # noqa: BLE001
        _log("ps_query 失败: %s" % e)
        return {p: [] for p in patterns}


def _probe_one(p):
    """单探测项 → (ok, detail)"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if p["type"] == "http":
            with op.open(p["url"], timeout=3) as r:
                return (r.status == 200, "HTTP %s" % r.status)
        return False, "unknown probe type"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:60]


def services_status():
    """全部服务状态聚合。on=全探测项在线 / partial=部分 / off=全离线"""
    defs = _services_def()
    proc_pats = {}
    for s in defs:
        for p in s.get("probe", []):
            if p["type"] == "process":
                proc_pats[p["match"]] = []
    pid_map = _ps_query(list(proc_pats)) if proc_pats else {}
    out = []
    for s in defs:
        probes = s.get("probe", [])
        results, details = [], []
        for p in probes:
            if p["type"] == "http":
                ok, d = _probe_one(p)
            else:  # process
                pids = pid_map.get(p["match"], [])
                ok, d = bool(pids), ("PID %s" % ",".join(map(str, pids[:4])) if pids else "无进程")
            results.append(ok)
            details.append(d)
        alive = sum(1 for r in results if r)
        status = "on" if alive == len(results) and results else (
            "partial" if alive else "off")
        out.append({"id": s["id"], "name": s["name"], "group": s.get("group", ""),
                    "desc": s.get("desc", ""), "status": status,
                    "alive": alive, "total": len(results),
                    "detail": " | ".join(details), "guard": s.get("guard", ""),
                    "readonly": not ("start" in s or "stop" in s)})
    return {"services": out, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def _popen_detached(exe, args):
    import subprocess
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        subprocess.Popen([exe] + args, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "已拉起(detached)"
    except OSError as e:
        return False, str(e)


def _ps_run_async(file_path):
    """ps1 异步执行（novamax_start 内置 150s 健康等待, 阻塞 HTTP 会超时）"""
    import subprocess
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", file_path], creationflags=flags, close_fds=True)
    return True, "脚本已下发(异步执行, 结果见服务状态刷新)"


def _ps_kill(match):
    """按命令行正则精准杀进程（防误杀：只杀 CommandLine 匹配者）"""
    import subprocess
    m = match.replace("'", "''")
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          "$hits = Get-CimInstance Win32_Process | "
          "Where-Object {$_.CommandLine -match '%s'};"
          "$n=0; foreach($h in $hits){Stop-Process -Id $h.ProcessId -Force; $n++};"
          "Write-Output \"killed=$n\"" % m)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20)
        out = r.stdout.decode("utf-8", "replace").strip()
        return True, out or "无匹配进程"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:80]


def service_action(svc_id, action):
    """start/stop/restart 一个服务；动作定义在 services.json，代码不含具体命令"""
    s = next((x for x in _services_def() if x["id"] == svc_id), None)
    if not s:
        return {"ok": False, "err": "未知服务 %s" % svc_id}
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "err": "非法动作 %s" % action}
    if action == "start" and "start" not in s:
        return {"ok": False, "err": "%s 不支持 start（%s）" % (svc_id, s.get("guard", "只读监护"))}
    if action == "stop" and "stop" not in s:
        return {"ok": False, "err": "%s 不支持 stop（%s）" % (svc_id, s.get("guard", "只读监护"))}

    steps = [("stop" if action == "restart" else action)]
    if action == "restart":
        if "stop" not in s or "start" not in s:
            return {"ok": False, "err": "restart 需要 start+stop 均定义"}
        steps = ["stop", "start"]
    msgs = []
    for act in steps:
        spec = s[act]
        if spec["mode"] == "popen":
            ok, msg = _popen_detached(spec["exe"], spec["args"])
        elif spec["mode"] == "ps1":
            ok, msg = _ps_run_async(spec["file"])
        elif spec["mode"] == "kill":
            ok, msg = _ps_kill(spec["match"])
        else:
            ok, msg = False, "未知 mode %s" % spec["mode"]
        msgs.append(msg)
        if not ok:
            return {"ok": False, "err": msg}
        if act == "stop" and action == "restart":
            time.sleep(2)  # 端口释放窗口
    _log("service %s %s: %s" % (svc_id, action, "; ".join(msgs)))
    return {"ok": True, "msg": "; ".join(msgs)}


# ---------------------------------------------------------------- HTTP 层

class H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(
            obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                self._send(PAGE.read_bytes(), ctype="text/html; charset=utf-8")
            except OSError:
                self._send({"err": "model_admin.html 缺失"}, 500)
            return
        if self.path == "/health":
            self._send({"ok": True, "svc": "model_admin", "port": 8098,
                        "ts": time.time()})
            return
        if self.path == "/api/models":
            self._send({"local": _local_models(), "loaded": _loaded_models(),
                        "cloud": _cloud_status(), "nova": _nova_status(),
                        "local_roles": router_mod.LOCAL_ROLES,
                        "special": router_mod.SPECIAL_ROUTES})
            return
        if self.path == "/api/agents":
            self._send({"agents": roster(), "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            return
        if self.path == "/api/logistics":
            self._send(_logistics())
            return
        if self.path == "/api/services":
            self._send(services_status())
            return
        self._send({"err": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/route":
            b = self._body()
            reg = next((a for a in roster()
                        if a["agent_id"] == (b.get("agent_id") or "")), None)
            self._send(router_mod.route(
                b.get("task", ""), agent_id=b.get("agent_id"),
                grade=b.get("grade") or (reg or {}).get("grade"),
                task_type=b.get("task_type"),
                respect_binding=(b.get("respect_binding")
                                 if "respect_binding" in b
                                 else (reg or {}).get("binding"))))
            return
        if self.path == "/api/binding":
            b = self._body()
            self._send(set_binding(b.get("agent_id", ""), b.get("binding", ""),
                                   b.get("note")))
            return
        if self.path == "/api/auto_assign":
            b = self._body()
            apply_mode = bool(b.get("apply"))
            rs = roster()
            write_fn = (lambda aid, nb, note: set_binding(aid, nb, note)) if apply_mode else None
            out = router_mod.auto_assign(rs, apply=apply_mode, write_fn=write_fn)
            self._send({"applied": apply_mode, "plan": out,
                        "changed": sum(1 for o in out if o["changed"])})
            return
        if self.path == "/api/service":
            b = self._body()
            self._send(service_action(str(b.get("id", "")), str(b.get("action", ""))))
            return
        self._send({"err": "not found"}, 404)


class _Srv(ThreadingHTTPServer):
    # Windows 下默认 allow_reuse_address=True 会双绑定抢请求（10-18 实踩：
    # 两实例并存、请求随机分落）——必须显式关掉，重复启动真正报错退出
    allow_reuse_address = False


def main():
    try:
        srv = _Srv(("127.0.0.1", 8098), H)
    except OSError:
        _log("8098 已被占用（已有实例在跑），静默退出")
        sys.exit(0)
    _log("model admin api :8098 (/ /api/models /api/agents /api/binding /api/route /api/auto_assign)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
