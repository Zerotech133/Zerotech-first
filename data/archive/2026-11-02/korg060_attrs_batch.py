# -*- coding: utf-8 -*-
"""K-ORG-060: L3 属性补全第二批——19 岗 experience+specialties（达成 29/29 全量目标）。
走 8093 /api/agent/update 正常链路（审计留痕+自动重烘）。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8093"
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOG = open(r"C:\项目\老田的AI助手\data\korg060_attrs.log", "a", encoding="utf-8")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with OP.open(req, timeout=timeout) as r:
        return json.load(r)


ATTRS = {
    # ---- 工程研发中心（charter 精准对应） ----
    "DEV2": {"experience": "模型训练/评估工程师（20 年档），模型训练与评估独立交付，执行版本化保护律（新版本登记 domain/model_registry）",
             "specialties": ["模型训练", "模型评估", "版本登记"]},
    "DEV3": {"experience": "量化策略研发（20 年档），strategies.json 注册制新增，回测判据前置自检——判据不过即终止留档",
             "specialties": ["策略研发", "回测判据", "策略注册"]},
    "DEV4": {"experience": "回测工程师（20 年档），BacktestEngine 执行与冻结判据复核，Go/No-Go 结论独立表态",
             "specialties": ["回测执行", "判据复核", "Go/No-Go 表态"]},
    "DEV5": {"experience": "前端架构师（30 年档），tokens.css 设计语言作者位，门户/大屏版面架构宪法执行，改动必过 node 校验",
             "specialties": ["前端架构", "tokens 纪律", "大屏开发", "版面宪法"]},
    "DOC1": {"experience": "技术文档工程师（20 年档），docs/plans 计划谱系与 INDEX 索引总账维护，判例（撞号/行尾/回滚死引用）入册沉淀",
             "specialties": ["计划谱系", "判例沉淀", "索引总账"]},
    "DEV6": {"experience": "后端开发（20 年档），常规功能/接口/脚本独立交付主力，疑难上报 DEV1",
             "specialties": ["后端交付", "接口开发", "脚本开发"]},
    "DEV7": {"experience": "后端开发（10 年档），标准执行/批量修改/格式化任务，产出交 E2+ 复核",
             "specialties": ["标准执行", "批量修改", "格式化"]},
    "DEV8": {"experience": "前端开发（20 年档），常规页面/图表交付主力，tokens 纪律同 DEV5，疑难上报 DEV5",
             "specialties": ["前端页面", "图表开发", "tokens 纪律"]},
    # ---- 合规法务 / 市场 / 产品设计 / 质量 ----
    "LEG1": {"experience": "资深法务（30 年档·独立表态·一票否决权），政府材料与宣发文稿发布前三态审查（通过/修改后通过/驳回），四条对外口径裁定权，爬虫采集合规红线",
             "specialties": ["发布前审查", "口径裁定", "采集合规", "政府材料"]},
    "MKT1": {"experience": "市场运营（20 年档），统一口号纪律执法，宣发文案/视频脚本，开源社区高 star 项目对比报告，对外定位口径维护",
             "specialties": ["宣发文案", "社区运营", "口号纪律", "定位口径"]},
    "PM1": {"experience": "资深产品经理（30 年档），需求池维护与版本规划，验收口径（acceptance_criteria）定义人，项目部门交付定义人",
             "specialties": ["需求池", "版本规划", "验收口径", "交付定义"]},
    "DSGN1": {"experience": "UI/UX 设计师（30 年档），tokens.css 设计语言作者位，Demo 先行流程（老田拍板后再怼生产），风格一致化巡查（悬空 tokens 盘点）",
              "specialties": ["tokens 治理", "Demo 先行", "风格巡查"]},
    "DSGN2": {"experience": "UI 执行（10 年档），切图/图标/样式批量执行，产出交 DSGN1 审定",
              "specialties": ["切图", "图标", "样式执行"]},
    "QA2": {"experience": "测试工程师（10 年档），用例执行/证据采集/回归跑批，结论交 QA1 裁定",
            "specialties": ["用例执行", "证据采集", "回归跑批"]},
    # ---- 投研部（无 charter·按选股团队通用人设保守撰写） ----
    "T1": {"experience": "投研部选股团队成员（成长档），参与多模型投票选股与盘后复盘，具备独立判断记录",
           "specialties": ["选股投票", "盘面复盘"]},
    "T2": {"experience": "投研部选股团队成员（成长档），多模型投票与候选池交叉验证",
           "specialties": ["选股投票", "交叉验证"]},
    "N1": {"experience": "投研部选股团队校验岗，候选票数据核验与投票一致性检查",
           "specialties": ["数据核验", "一致性检查"]},
    "F1": {"experience": "投研部选股团队成员，金融数据分析与候选筛选",
           "specialties": ["金融数据", "候选筛选"]},
    "D1": {"experience": "投研部技术分析岗，缠论结构识别与技术面研判辅助",
           "specialties": ["缠论分析", "技术面研判"]},
    # ---- 交易部 ----
    "TR1": {"experience": "交易执行员（10 年档），消费策略产出经 paper 链下单，成交/滑点写 feedback，复盘数据供策略部",
            "specialties": ["paper 下单", "滑点记录", "交易复盘"]},
}

ok, fail = [], []
for aid, at in ATTRS.items():
    try:
        resp = post("/api/agent/update",
                    {"agent_id": aid, "experience": at["experience"],
                     "specialties": at["specialties"]})
        if resp.get("ok") or resp.get("updated"):
            ok.append(aid)
            log("UPDATE %s OK %s" % (aid, resp.get("changed")))
        else:
            fail.append((aid, resp))
            log("UPDATE %s FAIL %s" % (aid, json.dumps(resp, ensure_ascii=False)[:200]))
    except Exception as e:
        fail.append((aid, str(e)))
        log("UPDATE %s EXC %s" % (aid, e))
    time.sleep(0.5)

log("=== 汇总 === ok=%d fail=%d%s" % (len(ok), len(fail),
    (" " + json.dumps(fail, ensure_ascii=False)[:600]) if fail else ""))
LOG.close()
