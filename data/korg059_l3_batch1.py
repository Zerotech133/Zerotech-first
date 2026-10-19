# -*- coding: utf-8 -*-
"""K-ORG-059: L3 运营线第一批——8 岗属性补全 + 9 单知识库学习。
走 8093 /api/agent/update 与 /api/agent/learn 正常链路（审计留痕+自动重烘）。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8093"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOG = open(r"C:\项目\老田的AI助手\data\korg059_learn.log", "a", encoding="utf-8")


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


# ---------- 第一步：属性补全（8 示范岗） ----------
ATTRS = {
    "A0A": {
        "experience": "参谋长办公室助理，长期从事方案分发、会议纪要归集、命令下发跟踪与闭环核销，熟悉全站工单流转与汇报三通道",
        "specialties": ["方案分发", "汇总归集", "命令跟踪", "工单台账"],
    },
    "SEC2": {
        "experience": "研发质量对抗岗，主导走形检查三态判定体系（条目→证据→判定），已完成多个真实研发方案的全量走查与返工闭环，抓获过冒烟测试未发现的深层走形",
        "specialties": ["走形检查", "方案对照", "证据链审查", "返工闭环"],
    },
    "QA1": {
        "experience": "测试裁定岗（30 年档），主测试策略与复核裁定，建立四项证据包复核纪律（stat/逐条实跑/只读grep/标注diff），坚持复核独立性",
        "specialties": ["测试策略", "复核裁定", "证据包审查", "回归验证"],
    },
    "SEC1": {
        "experience": "安全审计岗（合规红线），负责数据采集合规、对外材料红线审查与四条口径裁定支撑",
        "specialties": ["合规红线", "安全审计", "数据采集合规", "口径裁定"],
    },
    "DEV1": {
        "experience": "后端开发（30 年档），Python/FastAPI 为主栈，主导多条产线工单实现，熟悉产码闸门与最少字符修复纪律",
        "specialties": ["Python 后端", "FastAPI", "工单产码", "闸门适配"],
    },
    "ARCH1": {
        "experience": "架构师（30 年档），负责注册表驱动架构、模块切分与三维键位（工作区×项目线×身份）治理设计",
        "specialties": ["架构设计", "模块切分", "注册表驱动", "技术选型"],
    },
    "OPS1": {
        "experience": "运维（30 年档），负责调度链维护、看门狗与探活自动化、故障定位与回滚阀演练",
        "specialties": ["调度运维", "看门狗", "探活", "故障定位"],
    },
    "DATA1": {
        "experience": "数据工程师，负责特征湖构建、数据同步链路与质量校验，熟悉 parquet 湖与同步守卫",
        "specialties": ["数据管道", "特征工程", "数据质量", "parquet 湖"],
    },
}

ok_u, fail_u = [], []
for aid, at in ATTRS.items():
    try:
        resp = post("/api/agent/update",
                    {"agent_id": aid, "experience": at["experience"],
                     "specialties": at["specialties"]})
        if resp.get("ok") or resp.get("updated"):
            ok_u.append((aid, resp.get("changed")))
            log("UPDATE %s OK %s" % (aid, resp.get("changed")))
        else:
            fail_u.append((aid, resp))
            log("UPDATE %s FAIL %s" % (aid, json.dumps(resp, ensure_ascii=False)[:200]))
    except Exception as e:
        fail_u.append((aid, str(e)))
        log("UPDATE %s EXC %s" % (aid, e))

# ---------- 第二步：知识学习（9 单，串行） ----------
LEARNS = [
    ("SEC1", "法律专业知识库.md"),
    ("LEG1", "企业分析与尽调知识库.md"),
    ("QA1", "AI工程实践知识库.md"),
    ("DEV1", "AI工程实践知识库.md"),
    ("ARCH1", "AI工程实践知识库.md"),
    ("DATA1", "AI工程实践知识库.md"),
    ("D1", "缠论技术分析知识库.md"),
    ("T1", "金融专业知识库.md"),
    ("TR1", "金融专业知识库.md"),
]

ok_l, fail_l = [], []
for aid, doc in LEARNS:
    t0 = time.time()
    try:
        resp = post("/api/agent/learn", {"agent_id": aid, "doc": doc})
        if resp.get("digest"):
            ok_l.append((aid, doc, resp["digest_len"]))
            log("LEARN %s<-%s OK %d字 %.0fs" % (aid, doc, resp["digest_len"], time.time() - t0))
        else:
            fail_l.append((aid, doc, resp))
            log("LEARN %s<-%s FAIL %s" % (aid, doc, json.dumps(resp, ensure_ascii=False)[:200]))
    except Exception as e:
        fail_l.append((aid, doc, str(e)))
        log("LEARN %s<-%s EXC %s" % (aid, doc, e))
    time.sleep(2)

log("=== 汇总 === update ok=%d fail=%d | learn ok=%d fail=%d"
    % (len(ok_u), len(fail_u), len(ok_l), len(fail_l)))
if fail_u:
    log("update failures: " + json.dumps(fail_u, ensure_ascii=False)[:800])
if fail_l:
    log("learn failures: " + json.dumps(fail_l, ensure_ascii=False)[:800])
LOG.close()
