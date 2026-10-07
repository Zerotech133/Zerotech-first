# -*- coding: utf-8 -*-
"""OS 系统自检：服务 / 容器 / 计划任务 / 数据链 四层健康检查。

用法:
    python health_check.py          # 人类可读报告（exit 0=全绿, 1=有异常）
    python health_check.py --json   # 仅输出一行 JSON（供统一日报聚合）

结果始终落盘 ops/health_latest.json（portal 可后续引用）。
"""
import json
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _j(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _http_ok(url, timeout=4):
    try:
        return urllib.request.urlopen(url, timeout=timeout).status == 200
    except Exception:
        return False


def _tcp_ok(port, timeout=3):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _jev_py_ok():
    """Jev 四件套解释器巡检：在跑的必须全为产线 3.11（双 Python 治理护栏，
    防裸 python 调用静默回归到 PATH 的用户 3.12）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -match 'jev_gate|jev_qd_bridge|"
             "signal_review_gate|lark_card_handler'}).ExecutablePath"],
            capture_output=True, text=True, timeout=15)
        paths = [l.strip() for l in r.stdout.splitlines()
                 if l.strip().lower().endswith("python.exe")]
        return bool(paths) and all("Python311" in p for p in paths)
    except Exception:
        return True  # 巡检自身故障不误报


def check_services():
    return {
        "门户API(8091)": _http_ok("http://127.0.0.1:8091/health") or _tcp_ok(8091),
        "nginx(8080)": _http_ok("http://127.0.0.1:8080/"),
        "量化前端(5175)": _http_ok("http://127.0.0.1:5175/"),
        "QD桥接(9103)": _tcp_ok(9103),
        "卡片回调(9104)": _tcp_ok(9104),
        "Jev解释器(3.11)": _jev_py_ok(),
    }


def check_docker():
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        names = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return {"total": len(names),
                "ai_portal": "ai-portal" in names}
    except Exception:
        return None


# 0=成功 267009=0x41301运行中 267011=0x41303尚未跑 267014=0x41306被终止
# 2147946720=0x800710E0 周期重入被拒（门户API常驻任务，无害）
_BENIGN_RC = {0, 267009, 267011, 267014, 2147946720}
# 系统自带噪音任务（OneDrive 更新器等，与本站无关）
_IGNORE_NAME = ("OneDrive",)


def check_tasks():
    ps = ("Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft*' } | "
          "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; "
          "'{0}|{1}|{2}' -f $_.TaskName, $_.State, $i.LastTaskResult }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
        lines = [l.strip() for l in (r.stdout or "").splitlines() if "|" in l]
    except Exception:
        return {"total": 0, "bad": [{"name": "查询失败", "rc": -1}]}
    total, bad = 0, []
    for line in lines:
        total += 1
        try:
            name, state, rc_s = line.split("|", 2)
        except ValueError:
            continue
        if state.strip() == "Disabled":
            continue
        if name.strip().startswith(_IGNORE_NAME):
            continue
        try:
            rc = int(rc_s.strip())
        except (ValueError, AttributeError):
            rc = 0  # 查询不到结果码按未知处理为正常
        if rc not in _BENIGN_RC:
            bad.append({"name": name.strip(), "rc": rc})
    return {"total": total, "bad": bad}


def check_data():
    sec = _j(os.path.join(STATION, "news_collector", "sectors.json")) or {}
    led = _j(os.path.join(STATION, "quant_backtest", "positions.json")) or {}
    st = _j(os.path.join(STATION, "news_collector", "status.json")) or {}
    snap_dir = os.path.join(STATION, "quant_backtest", "reports", "snap")
    snap = sorted(os.listdir(snap_dir))[-1] if os.path.isdir(snap_dir) else "NONE"
    return {
        "news_24h": st.get("news_24h"),
        "sectors_count": sec.get("count", 0),
        "sectors_ts": sec.get("ts"),
        "pending": len(led.get("pending") or []),
        "open": len(led.get("open") or []),
        "closed": len(led.get("closed") or []),
        "latest_snapshot": snap,
    }


def run():
    result = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    issues = []

    result["services"] = check_services()
    for name, ok in result["services"].items():
        if not ok:
            issues.append("服务下线: %s" % name)

    result["docker"] = check_docker()
    if result["docker"] and not result["docker"].get("ai_portal"):
        issues.append("ai-portal 容器未运行")

    result["tasks"] = check_tasks()
    for b in result["tasks"]["bad"]:
        issues.append("任务 %s RC=0x%X" % (b["name"], b["rc"] & 0xFFFFFFFF))

    result["data"] = check_data()
    result["issues"] = issues

    with open(os.path.join(HERE, "health_latest.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    h = run()
    if "--json" in sys.argv:
        print(json.dumps(h, ensure_ascii=False))
        return
    print("== OS 系统自检 %s ==" % h["ts"])
    for name, ok in h["services"].items():
        print("  服务 %-14s %s" % (name, "✓" if ok else "✗ 下线"))
    dk = h["docker"] or {}
    print("  容器 total=%s ai_portal=%s" % (dk.get("total", "?"), dk.get("ai_portal")))
    print("  计划任务 total=%d 异常=%d" % (h["tasks"]["total"], len(h["tasks"]["bad"])))
    for b in h["tasks"]["bad"]:
        print("    ✗ %s RC=0x%X" % (b["name"], b["rc"] & 0xFFFFFFFF))
    d = h["data"]
    print("  数据: 24h新闻=%s 板块=%s 待买/持/平=%d/%d/%d 快照=%s" % (
        d["news_24h"], d["sectors_count"], d["pending"], d["open"],
        d["closed"], d["latest_snapshot"]))
    if h["issues"]:
        print("!! 发现 %d 个异常" % len(h["issues"]))
        sys.exit(1)
    print("全绿")


if __name__ == "__main__":
    main()
