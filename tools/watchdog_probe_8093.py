# -*- coding: utf-8 -*-
"""8093 看门狗固定探针（K-ORG-114 · 哨薄壳化判例 K-STOCK-197~199）。

背景：看门狗 automation 原靠 LLM 遵循 prompt 里的探活 URL——10:41 实测
LLM 即兴没带 ?caller=watchdog-8093（observe 账本记 null，归因率提升通道
失效）。判例「代理即兴补跑=比迁移丢步更隐蔽的回归」：把 URL/口径固化进
脚本（唯一事实源），automation prompt 降级为「运行本脚本并按输出写日志」，
LLM 只负责调度与落日志，不再自由发挥 HTTP 细节。

输出（stdout 单行 JSON，供 automation 判读）：
    {"ok": true, "status": 200, "elapsed": 0.05, "detail": "在编29岗"}
    {"ok": false, "status": null, "elapsed": 5.0, "detail": "ConnectError: ..."}
退出码：0 存活 / 1 失败（automation 可直接按退出码分支）。
"""

import http.client
import json
import sys
import time

HOST, PORT = "127.0.0.1", 8093
# 唯一事实源：探活 URL（含 observe 归因 caller——K-ORG-108/109/114 链）
PATH = "/api/org?caller=watchdog-8093"


def main():
    t0 = time.time()
    try:
        c = http.client.HTTPConnection(HOST, PORT, timeout=5)
        c.request("GET", PATH)
        r = c.getresponse()
        body = r.read().decode("utf-8", "replace")  # 全读（2048 截断致 json.loads 失败判例）
        ok = r.status == 200
        detail = body
        if ok:
            try:
                d = json.loads(body)
                # /api/org 响应顶层 {bundle, org, audit_tail}——岗位数在 org 内,
                # 结构不硬编码,只报顶层键(判读看 ok 字段足够)
                detail = "keys=%s" % ",".join(sorted(d)[:4])
            except ValueError:
                detail = body[:60]
        print(json.dumps({"ok": ok, "status": r.status,
                          "elapsed": round(time.time() - t0, 2),
                          "detail": detail}, ensure_ascii=False))
        sys.exit(0 if ok else 1)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "status": None,
                          "elapsed": round(time.time() - t0, 2),
                          "detail": "%s: %s" % (type(e).__name__, e)},
                         ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
