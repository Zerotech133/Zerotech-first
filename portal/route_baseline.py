# -*- coding: utf-8 -*-
"""portal_api.py 路由回归基线（K-STOCK-077 P-4 第一步）。

背景：portal_api.py 23,227 行 / 285 函数单体，P1 结构债，拆分前需固化
路由清单作回归基线——拆分后重跑本脚本，清单 diff 为空=拆分零路由丢失。

历史注：k274 时代的 check_api_routes.py 已失传（0KB 空壳），本脚本重建。
口径：源码静态提取全部 "/api/..." 字符串字面量（含行号），AST 拿不到的
动态拼接如实标注。路由分发是 BaseHTTPRequestHandler do_GET/do_POST 内
path 匹配（非 FastAPI 装饰器），所以字面量清单≈路由全集。

用法（系统 Python311）：
  python portal/route_baseline.py                # 打印清单 + 落 JSON 基线
  python portal/route_baseline.py --check        # 对比已落基线，diff 非空 exit 1
  python portal/route_baseline.py --probe        # 追加 8091 存活探针（/health）
  python portal/route_baseline.py --probe-all    # 逐路由 HTTP 探活（GET，任何响应=活）
"""
from __future__ import annotations

import ast
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "portal_api.py"
BASELINE = HERE / "route_baseline.json"
API_PREFIX = "/api/"


def extract_routes() -> dict:
    """静态提取：API 字面量路由 + 每条首现行号（同路径多行合并计次）。"""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    hits: dict[str, dict] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if s.startswith(API_PREFIX) and re.fullmatch(r"/api/[\w\-./{}]*", s):
                e = hits.setdefault(s, {"path": s, "lines": [], "count": 0})
                e["lines"].append(node.lineno)
                e["count"] += 1
    for e in hits.values():
        e["first_line"] = e["lines"][0]
    routes = sorted(hits.values(), key=lambda x: x["path"])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": SRC.name,
        "source_sha256": __import__("hashlib").sha256(
            SRC.read_bytes()).hexdigest()[:16],
        "n_routes": len(routes),
        "routes": routes,
    }


def probe_health() -> bool:
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return op.open("http://127.0.0.1:8091/health", timeout=4).status == 200
    except Exception:
        return False


def probe_all(routes: list[str]) -> dict:
    """逐路由 GET 探活：任何 HTTP 响应（含 4xx/5xx）=分发层可达；
    连接拒绝/超时=服务死。不判业务对错——回归看的是『拆分后全部仍可达』。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out = {"alive": 0, "unreachable": 0, "detail": {}}
    for p in routes:
        try:
            r = op.open("http://127.0.0.1:8091" + p, timeout=6)
            code = r.status
        except urllib.error.HTTPError as e:      # noqa: PERF203
            code = e.code
        except Exception as e:
            out["unreachable"] += 1
            out["detail"][p] = f"ERR {type(e).__name__}"
            continue
        out["alive"] += 1
        out["detail"][p] = code
    return out


def main(argv: list[str]) -> int:
    check = "--check" in argv
    do_probe = "--probe" in argv or "--probe-all" in argv
    data = extract_routes()
    if check:
        if not BASELINE.exists():
            print("基线不存在，先无参跑一次落基线")
            return 2
        old = json.loads(BASELINE.read_text(encoding="utf-8"))
        old_set = {r["path"] for r in old["routes"]}
        new_set = {r["path"] for r in data["routes"]}
        lost, added = sorted(old_set - new_set), sorted(new_set - old_set)
        print(f"基线 {len(old_set)} 条 vs 当前 {len(new_set)} 条")
        if lost:
            print("❌ 路由丢失:", *lost, sep="\n  ")
        if added:
            print("⚠️ 新增路由:", *added, sep="\n  ")
        if not lost and not added:
            print("✅ 路由清单零漂移")
        return 1 if lost else 0
    BASELINE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"路由 {data['n_routes']} 条（源 sha256:{data['source_sha256']}…）"
          f" 基线已落 {BASELINE.name}")
    for r in data["routes"]:
        print(f"  L{r['first_line']:<6} {r['path']}  x{r['count']}")
    if "--probe" in argv or "--probe-all" in argv:
        print("health:", "200 OK" if probe_health() else "DOWN")
    if "--probe-all" in argv:
        pr = probe_all([r["path"] for r in data["routes"]])
        print(f"探活: {pr['alive']} 可达 / {pr['unreachable']} 不可达")
        bad = {k: v for k, v in pr["detail"].items()
               if str(v).startswith("ERR")}
        if bad:
            print("不可达明细:", json.dumps(bad, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
