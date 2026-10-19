# -*- coding: utf-8 -*-
"""K-ORG-061: L3 知识学习第二批——18 岗补知识沉淀（055 计划 10-21~10-28 窗口前推）。
走 8093 /api/agent/learn 正常链路（chat_auto 提炼+memory 沉淀+attributes.knowledge 登记）。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8093"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOG = open(r"C:\项目\老田的AI助手\data\korg061_learn.log", "a", encoding="utf-8")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def post(path, payload, timeout=300):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with OP.open(req, timeout=timeout) as r:
        return json.load(r)


# 18 岗 × 对口文档（文档池 6 篇，笔记按岗职责提炼各不相同）
LEARNS = [
    ("A0A", "企业分析与尽调知识库.md"),   # 经营与组织理解，服务方案分发判读
    ("DEV2", "AI工程实践知识库.md"),
    ("DEV3", "缠论技术分析知识库.md"),    # 策略研发对口
    ("DEV4", "金融专业知识库.md"),        # 回测判据对口
    ("DEV5", "AI工程实践知识库.md"),
    ("DEV6", "AI工程实践知识库.md"),
    ("DEV7", "AI工程实践知识库.md"),
    ("DEV8", "AI工程实践知识库.md"),
    ("OPS1", "AI工程实践知识库.md"),
    ("DOC1", "AI工程实践知识库.md"),
    ("T2", "金融专业知识库.md"),
    ("N1", "金融专业知识库.md"),
    ("F1", "资本运作知识库.md"),
    ("MKT1", "企业分析与尽调知识库.md"),
    ("PM1", "企业分析与尽调知识库.md"),
    ("DSGN1", "AI工程实践知识库.md"),
    ("DSGN2", "AI工程实践知识库.md"),
    ("QA2", "AI工程实践知识库.md"),
]

ok, fail = [], []
for aid, doc in LEARNS:
    t0 = time.time()
    try:
        resp = post("/api/agent/learn", {"agent_id": aid, "doc": doc})
        if resp.get("digest"):
            ok.append((aid, resp["digest_len"]))
            log("LEARN %s<-%s OK %d字 %.0fs" % (aid, doc, resp["digest_len"], time.time() - t0))
        else:
            fail.append((aid, doc, resp))
            log("LEARN %s<-%s FAIL %s" % (aid, doc, json.dumps(resp, ensure_ascii=False)[:200]))
    except Exception as e:
        fail.append((aid, doc, str(e)))
        log("LEARN %s<-%s EXC %s" % (aid, doc, e))
    time.sleep(2)

log("=== 汇总 === ok=%d/18 fail=%d%s" % (len(ok), len(fail),
    (" " + json.dumps(fail, ensure_ascii=False)[:800]) if fail else ""))
LOG.close()
