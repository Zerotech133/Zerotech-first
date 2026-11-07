# -*- coding: utf-8 -*-
"""JEV 能力开放层 —— 调用留痕 + 失败溯源 + 能力目录（2026-11-07）。

定位（老田令：把中台变成可对其他团队输出的能力，接口失败可溯源到问题点）：
    1. 能力目录 `/api/jev/capabilities`：对外告知"我有什么能力、怎么调、
       语义与限额"——其他团队从这里发现接口；
    2. 全量调用留痕：/api/jev/* 每次调用记 req_id / 消费方（会话身份或
       anon）/ 端点 / 状态 / 耗时 / 错误分级（client / upstream / internal），
       写独立 sqlite（jev_access.db，与 news.db 解耦，WAL）；
    3. 溯源查询 `/api/jev/trace`：按 req_id / 消费方 / 端点 / 状态 / 时间窗
       检索调用记录，失败行带 error_class + error_detail——"谁的请求、
       打到哪、卡在哪一层"一查便知。

失败分级口径（溯源的关键）：
    client     请求侧问题：参数越界 / 未登录（401 类）/ 无权限——找调用方
    upstream   JEV 依赖上游问题：news.db 不可读 / Ollama / 总线——找中台运维
    internal   JEV 自身缺陷：未捕获异常 / schema 不符——找 JEV 开发（立 BUG 卡）
    unknown    进程中断等无响应场景——查 portal_api 重启日志对时间

留痕纪律：
    - 只在响应完成后异步落一条（trace 表），**绝不影响业务响应路径**：
      记录失败（磁盘满等）打 stderr，不抛错、不改状态码；
    - 不记请求体/响应体（含新闻内容与消费方敏感参数），只记元数据；
    - 查询接口本身也被留痕（self-trace，防溯源接口被滥用无感知）。

新表（jev_access.db，首次调用自动建）：
    trace(id, req_id, ts, actor, endpoint, method, status, ms,
          error_class, error_detail, ref)
      req_id  = 时间戳纳秒 + 随机 4 hex（响应头 X-JEV-Req-Id 同款回显，
                调用方报障时提供此 id 即可秒级定位）
      ref     = 调用方自带标识（X-JEV-Ref 头或 ?ref=，团队自报用途，
                可选——不强制，但对外公告要求带上便于分摊排查）
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCESS_DB = os.path.join(STATION, "portal", "data", "jev_access.db")

_lock = threading.Lock()

CAPABILITIES = [
    {
        "id": "jev_judge",
        "title": "新闻/公告多维判定（能力本体）",
        "endpoint": "GET /api/jev/query?hours=24&bias=bull&code=600519&with_posterior=1",
        "purpose": "对新闻与公告给出结构化判定：方向（利多/利空/中性）×置信×事件类型，"
                   "并附影响标的（companies/stocks）与股价后验（T+1 ret/基准/超额/命中），"
                   "一条新闻的『判断→影响谁→后市验证』完整依据链。",
        "contract": "只读；hours 1..720；schema jev.v2；后验按交易日滞后回填（dec_audit 28k+ 样本）",
        "consumer_hint": "任何需要『这条新闻可信吗、利好谁、验证了没』的团队",
    },
    {
        "id": "jev_source_trust",
        "title": "数据来源可信度（判定的地基）",
        "endpoint": "GET /api/jev/capabilities（本目录）+ 校准报告 latest.json",
        "purpose": "来源不只采信：公告源走 prefilter 白名单（812 样本 0 误杀）、低置信进人工复核队列、"
                   "模型判定带 conf 且 <0.5 自动降权、月度校准（latest.json）对全量判定质量背书。"
                   "判断『这条数据来源是否准确』有整套证据可查。",
        "contract": "复核队列读公开写需会话；校准报告只读；粗筛证据 src=prefilter 可 SQL 回退",
        "consumer_hint": "对数据质量有要求的下游（知识库/研究/合规）",
    },
    {
        "id": "jev_posterior",
        "title": "股价后验依据（判定准不准，市场说了算）",
        "endpoint": "GET /api/jev/query?with_posterior=1（T+1 ret/bench/excess/hit）",
        "purpose": "每条带标的的判定按交易日回填真实收益与超额，判定质量可用后验命中率量化审计"
                   "（dec_audit 135k 判定记录、28k+ 已验证）——不是拍脑袋，是有后验的判读。",
        "contract": "只读；后验滞后 T+1；band 参数可调命中阈值",
        "consumer_hint": "量化/回测/策略团队",
    },
    {
        "id": "jev_target_drill",
        "title": "影响标的下钻（新闻 → 上市公司）",
        "endpoint": "GET /api/jev/target/600519?days=30&limit=20（四面聚合）· GET /api/jev/query?code=600519",
        "purpose": "一只股票一页出：基本面快照（名称/行业/地域/上市日）+ 行情面（近 N 日 "
                   "close/pct_chg/vol/amount/振幅 + 区间涨跌 + form 形态量价：MA/区间位置/连涨跌/"
                   "量比/波动率与人话 label）+ 情绪面（该股全部相关消息的热度/来源/事件/极性，"
                   "含未判定行，独立取数）+ 判定明细（含 prefilter/model 来源标注与 "
                   "evidence_item 溯源路径）+ 后验战绩（该股判定命中率）。query?code= 反查全部相关判定。",
        "contract": "只读；code 6 位或带后缀；days 1..180；limit 1..100；60s 缓存；"
                    "schema jev_target.v2（v1 字段全保留）；block_status 逐块报可用性；"
                    "情绪极性来自 JEV 判定 bias 而非独立情感模型（match_basis 已注明）",
        "consumer_hint": "个股研究/投研工作台/Agent 盘前尽调",
    },
    {
        "id": "jev_wordcloud",
        "title": "bias 词云（其中一个视图）",
        "endpoint": "GET /api/jev/wordcloud?days=90&top_n=30",
        "purpose": "判定的语义快照视图：多空词频对比与事件矩阵——是上面的判读能力的展示面之一，"
                   "不是能力本体。",
        "contract": "只读；days 1..730；top_n 5..80；停用词口径冻结",
        "consumer_hint": "舆情展示/大屏",
    },
    {
        "id": "jev_platform",
        "title": "中台健康总览",
        "endpoint": "GET /api/jev/platform?days=7",
        "purpose": "哨兵四查/队列/吞吐/夜批/runs 时间线——判读供给是否在线一页看。",
        "contract": "只读；days 1..30；60s 缓存，fresh=1 绕过",
        "consumer_hint": "运维面板/值班巡检",
    },
    {
        "id": "jev_review",
        "title": "低置信人工复核（可信度兜底层）",
        "endpoint": "GET/POST /api/jev/review/...（写需门户登录会话）",
        "purpose": "模型拿不准的进人工裁决队列，正确/错误/不确定写回——判定可信度的人机闭环。",
        "contract": "读公开、写须 zb_session；不改 news.jev 原值",
        "consumer_hint": "复核员/质量团队",
    },
    {
        "id": "jev_mcp",
        "title": "MCP 工具（stdio）",
        "endpoint": "tools/jev_mcp.py（jev_query / jev_calibration）",
        "purpose": "Agent 生态进程内接入全部判定能力（含校准报告）。",
        "contract": "只读；数据面同 jev_query",
        "consumer_hint": "MCP 宿主/Agent 团队",
    },
    {
        "id": "jev_interpret",
        "title": "AI 解读结论（数据自动出人话结论）",
        "endpoint": "GET /api/jev/interpret?kind=story|intraday|evidence&code=…&q=…",
        "purpose": "任何一组监测数据，本地模型（qwen3.5）生成四段中文结论——"
                   "态势/验证/风险/建议，告知人这组数据说明了什么；模型不可用"
                   "自动降级规则结论（engine 字段可区分）。",
        "contract": "只读；本地 150s 超时；本地算力不足自动降级云端"
                    "（config/cloud_llm.json 或 JEV_CLOUD_LLM_* 环境变量），"
                    "云端也不可用再降规则结论；engine=local/cloud/rule 可区分；"
                    "同参数结论 5 分钟缓存；facts 附原始摘要",
        "consumer_hint": "所有看数据的页面与下游系统",
    },
    {
        "id": "jev_intraday",
        "title": "日内 5 分钟价格回溯（盘中反应量化）",
        "endpoint": "GET /api/jev/intraday/300750[?date=YYYYMMDD]",
        "purpose": "当天新闻在盘中的价格反应：1 分钟线聚合 5 分钟棒，逐事件对齐"
                   "事件后 5/15/30/60 分钟累计变化（方向归一）——盘前/盘中消息"
                   "市场认不认，回溯可查。",
        "contract": "只读；缺省取最新可用交易日；非交易日如实报 unavailable",
        "consumer_hint": "盘中复盘/消息影响即时验证",
    },
    {
        "id": "jev_story",
        "title": "个股叙事线（判定串联→信源战绩→变化验证闭环）",
        "endpoint": "GET /api/jev/story/300750?days=30",
        "purpose": "把影响一只股票的全部新闻事件串成时间轴（每条带方向判断与"
                   "信源），按信源记账『谁说对最多』（准确率排行），再监控事态"
                   "发生后股价是否真有变化——证实/证伪逐条可查，闭环验证判定。",
        "contract": "只读；code 6 位；days 1..120；schema jev_story.v1",
        "consumer_hint": "个股监控/信源质量审计/策略入场确认",
    },
    {
        "id": "jev_evidence",
        "title": "消息举证链（一组消息的完整证据）",
        "endpoint": "GET /api/jev/evidence?q=关键词或代码&days=14&bucket=day|hour",
        "purpose": "任何一组消息可举证：什么时间发/持续多久/多少条/哪些渠道在扩散/"
                   "涉及哪些标的/逐交易日股价反应/判读后验命中率 + 自动结论 verdict。"
                   "回答『这条消息靠谱吗、谁在推、市场信了没』的证据链。",
        "contract": "只读；q 关键词或 6 位代码；days 1..90；bucket hour 仅限 ≤2 天窗",
        "consumer_hint": "投研举证/舆情审计/合规回溯",
    },
    {
        "id": "jev_evidence_item",
        "title": "单条证据四级溯源（原文→判定→后验→归组）",
        "endpoint": "GET /api/jev/evidence/item?id=wsc:3196404[&code=300750]",
        "purpose": "证据链的末梢：给一个 news id，回四环——①原文本体（标题/正文/"
                   "信源/时间）②判定（方向/置信/事件类型/概率/理由/判定模型与耗时）"
                   "③后验（dec_audit T+1 官方口径，缺失时行情次日兜底并标 proxied）"
                   "④归组（同日同标的已判条数与同向条数）。缺环如实标注，不编造，"
                   "并给可直接粘贴的引用串。",
        "contract": "只读；id 为 news 表主键；四环各自标注 ok/reason；单条不可变→600s 缓存",
        "consumer_hint": "报告引用/合规回溯/质疑某条判定时逐环核对",
    },
    {
        "id": "jev_trace",
        "title": "调用溯源",
        "endpoint": "GET /api/jev/trace?req_id=...&status=fail",
        "purpose": "接口失败排查：按 req_id/消费方/端点查调用留痕，四级分级定位责任层。",
        "contract": "只读；limit 1..200；本接口同样被留痕",
        "consumer_hint": "接入团队报障/中台运维",
    },
]

# 能力分组（2026-11-07 老田令「板块的能力重新解读」）：
# 能力就两条——① 可追溯的证据链 ② 叙事解读；其余是这两条的地基。
_GROUPS = {
    "evidence": ("① 可追溯的证据链",
                 "每条结论都能追到 原文 → 判定 → 后验 → 归组，缺环如实标注"),
    "narrative": ("② 叙事解读",
                  "把一段行情讲明白：形态 / 量价关系 / 消息归因，并出 AI 结论"),
    "base": ("平台底座", "判定、可信度、复核、留痕——上面两条能力的地基"),
}
_GROUP_OF = {
    "jev_evidence": "evidence", "jev_evidence_item": "evidence",
    "jev_posterior": "evidence", "jev_wordcloud": "evidence",
    "jev_target_drill": "evidence", "jev_trace": "evidence",
    "jev_story": "narrative", "jev_intraday": "narrative",
    "jev_interpret": "narrative",
}
for _c in CAPABILITIES:
    _g = _GROUP_OF.get(_c["id"], "base")
    _c["group"] = _g
    _c["group_cn"] = _GROUPS[_g][0]
    _c["group_desc"] = _GROUPS[_g][1]
GROUP_ORDER = ["evidence", "narrative", "base"]
GROUP_META = {k: {"cn": v[0], "desc": v[1]} for k, v in _GROUPS.items()}


# 状态码 → 失败分级（溯源第一层：是谁的问题）
_STATUS_CLASS = {
    400: "client", 401: "client", 403: "client", 404: "client",
    405: "client", 422: "client", 429: "client",
    502: "upstream", 503: "upstream", 504: "upstream",
    500: "internal",
}


def _conn():
    os.makedirs(os.path.dirname(ACCESS_DB), exist_ok=True)
    db = sqlite3.connect(ACCESS_DB, timeout=5)
    db.execute(
        "CREATE TABLE IF NOT EXISTS trace("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "req_id TEXT, ts TEXT, actor TEXT, endpoint TEXT, method TEXT,"
        "status INTEGER, ms INTEGER, error_class TEXT, error_detail TEXT,"
        "ref TEXT)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_trace_ts ON trace(ts)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_trace_req ON trace(req_id)"
    )
    return db


def new_req_id() -> str:
    return "%s-%s" % (dt.datetime.now().strftime("%Y%m%d%H%M%S"),
                      secrets.token_hex(2))


def record(req_id, actor, endpoint, method, status, ms,
           error_detail="", ref=""):
    """响应完成后落一条留痕；任何失败静默（stderr 留信号），绝不抛错。"""
    try:
        with _lock:
            db = _conn()
            try:
                db.execute(
                    "INSERT INTO trace(req_id, ts, actor, endpoint, method,"
                    " status, ms, error_class, error_detail, ref) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (req_id,
                     dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     (actor or "anon")[:120], endpoint[:160], method,
                     int(status) if status else 0, int(ms),
                     _STATUS_CLASS.get(status, "unknown"),
                     (error_detail or "")[:300], (ref or "")[:80]),
                )
                db.commit()
            finally:
                db.close()
    except Exception as e:  # noqa: BLE001 —— 留痕绝不影响业务
        import sys
        print("JEV-ACCESS TRACE FAIL %r" % (e,), file=sys.stderr)


def session_actor(handler) -> str:
    """消费方身份：会话 sub 优先，其次 zb_session 短哈希，无会话记 anon。"""
    try:
        import auth_gateway
        sid = auth_gateway._cookie_value(handler)
        rec = auth_gateway.STORE.get(sid) if sid else None
        if isinstance(rec, dict):
            for key in ("sub", "user", "username", "name"):
                if rec.get(key):
                    return str(rec[key])[:120]
        if sid:
            import hashlib
            digest = hashlib.sha256(
                ("zb_session:" + sid).encode("utf-8", "replace")).hexdigest()
            return "session:" + digest[:8]
    except Exception:  # noqa: BLE001
        pass
    return "anon"


def wrap_dispatch(handler, path, qs_raw, method, fn, *args):
    """包裹任意 jev 分派函数：挂 req_id 响应头 + 留痕。

    fn 的签名形如 fn(handler, path, qs)（GET 系）或 fn(handler, path)（POST 系）。
    状态捕获：实例级包装 send_response（handler 每请求一个实例，零全局污染），
    记录 _jev_access_status 供留痕读取；未捕获（分派器没回响应）按 500。
    """
    req_id = new_req_id()
    ref = (handler.headers.get("X-JEV-Ref") or "")[:80]
    actor = session_actor(handler)
    t0 = time.time()
    status = 0
    detail = ""
    _orig_send = handler.send_response
    _orig_end = handler.end_headers

    def _send_sniff(code, *a, **k):
        nonlocal status
        if not status:  # 首个响应行即最终状态
            try:
                status = int(code)
            except Exception:  # noqa: BLE001
                pass
        return _orig_send(code, *a, **k)

    def _end_sniff(*a, **k):
        try:  # 头已进入发送阶段：此刻补 req_id 头合法且必然生效一次
            handler.send_header = _orig_send_header  # 还原，防递归
            _orig_send_header("X-JEV-Req-Id", req_id)
        except Exception:  # noqa: BLE001
            pass
        return _orig_end(*a, **k)

    _orig_send_header = handler.send_header
    handler.send_response = _send_sniff
    handler.end_headers = _end_sniff
    try:
        fn(handler, *args)
        if not status:
            status = getattr(handler, "_jev_access_status", 0) or 200
    except Exception as e:  # noqa: BLE001 —— 兜底：分派器自身异常也留痕
        status = status or 500
        detail = "%s: %s" % (type(e).__name__, e)
        raise
    finally:
        try:
            handler.send_response = _orig_send
            handler.end_headers = _orig_end
        except Exception:  # noqa: BLE001
            pass
        if not status:
            status = 200 if detail == "" else 500
        record(req_id, actor, path, method, status,
               (time.time() - t0) * 1000.0, detail, ref)
    return req_id


# ── 查询接口 ──────────────────────────────────────────────────────────
def handle_trace(handler, path: str, qs_raw: str) -> None:
    qs = parse_qs(qs_raw or "")
    try:
        hours = int((qs.get("hours") or [24])[-1])
        if not 1 <= hours <= 24 * 30:
            raise ValueError("hours 1..720")
        limit = int((qs.get("limit") or [50])[-1])
        if not 1 <= limit <= 200:
            raise ValueError("limit 1..200")
        req_id = (qs.get("req_id") or [""])[-1].strip()
        actor_q = (qs.get("actor") or [""])[-1].strip()
        status_q = (qs.get("status") or [""])[-1].strip()  # fail|ok|all
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    where, args = ["ts >= ?"], [
        (dt.datetime.now() - dt.timedelta(hours=hours))
        .strftime("%Y-%m-%d %H:%M:%S")]
    if req_id:
        where.append("req_id LIKE ?")
        args.append(req_id + "%")
    if actor_q:
        where.append("actor LIKE ?")
        args.append("%" + actor_q + "%")
    if status_q == "fail":
        where.append("status >= 400")
    elif status_q == "ok":
        where.append("status < 400")
    sql = ("SELECT req_id, ts, actor, endpoint, method, status, ms, "
           "error_class, error_detail, ref FROM trace WHERE "
           + " AND ".join(where) + " ORDER BY id DESC LIMIT ?")
    args.append(limit)
    try:
        with _lock:
            db = _conn()
            try:
                rows = db.execute(sql, args).fetchall()
                total = db.execute(
                    "SELECT COUNT(*) FROM trace WHERE " + " AND ".join(where),
                    args[:-1]).fetchone()[0]
            finally:
                db.close()
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "unavailable",
                       "message": "trace db: %s" % exc}, 503)
        return
    items = [{
        "req_id": r[0], "ts": r[1], "actor": r[2], "endpoint": r[3],
        "method": r[4], "status": r[5], "ms": r[6],
        "error_class": r[7], "error_detail": r[8], "ref": r[9],
    } for r in rows]
    fail_cls = {}
    for it in items:
        if it["status"] >= 400:
            fail_cls[it["error_class"]] = fail_cls.get(it["error_class"], 0) + 1
    handler._json({
        "schema": "jev_trace.v1",
        "ok": True,
        "window_hours": hours,
        "total": total,
        "returned": len(items),
        "fail_class_counts": fail_cls,
        "items": items,
        "usage": "报障时带 req_id 精确定位；error_class: client=调用方 / "
                 "upstream=中台依赖 / internal=中台缺陷 / unknown=无响应",
    })


def handle_capabilities(handler, path: str, qs_raw: str) -> None:
    handler._json({
        "schema": "jev_capabilities.v1",
        "ok": True,
        "announced_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "capabilities": CAPABILITIES,
        "access_note": "HTTP 走门户 8080 同源（浏览器会话）；Agent 走 MCP "
                       "stdio（tools/jev_mcp.py）。建议调用方带 X-JEV-Ref "
                       "头自报团队用途，失败排查按响应头 X-JEV-Req-Id 溯源。",
        "groups": GROUP_META,
        "group_order": GROUP_ORDER,
        "positioning": "两条核心能力：① 可追溯的证据链——任何结论都能一路追到"
                       "原文→判定→后验→归组，缺环如实标注、不编造；② 叙事解读——"
                       "把一段行情讲明白（形态/量价关系/消息归因）并给出 AI 结论，"
                       "本地算力不足时自动降级云端模型，仍不可用则降规则结论。"
                       "判定、可信度、复核、留痕是这两条能力的地基；词云仅为其中一个视图。",
    })
