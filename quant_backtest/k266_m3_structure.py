# -*- coding: utf-8 -*-
"""K266 M3 文档结构化影子管线：doc_texts.jsonl → qwen3.5:9b → structure_shadow.jsonl
影子纪律：只落影子不挂图/不进生产，人审抽样后再定入图口径。
新闻批次（src=news，C案合批）：按 [时间][来源] 标题 头切单条，多条合组一次判
（K266-W7 吞吐：新闻 ingest 速率 ≥ 逐判速率，实测积压 1300+ 条；合批摊薄每条
重复的 prompt 重评估），组失败/缺项逐条回退单判；doc_id=批次id+i序号，幂等不变。
防串组闸门：批判实体未命中本条原文但整串命中同批邻条 → 该条降级单判重判。
rel 枚举白名单：落影子前 events.rel 归一（变体映射+白名单外落'其他'）。
用法: python k266_m3_structure.py [--limit N]（默认全量，幂等跳过已判 doc_id）"""

import difflib
import json
import os
import re
import sys
import time
import urllib.request
import requests
import logging

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "data", "k266_docs")
TEXTS = os.path.join(DOCS, "doc_texts.jsonl")
SHADOW = os.path.join(DOCS, "structure_shadow.jsonl")
OLLAMA = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3.5:9b"
# num_ctx 全仓唯一口径（2026-10-24 收口，见 ask_ollama 注释）：主调用与探测共用，
# 任一处写成别的值都会让 Ollama 整模型重载 15~46s。
NUM_CTX = 16384

PROMPT = """你是金融文档结构化引擎。输入一篇文档文本（公告/投关记录/研报/决议等），输出严格 JSON：
{
 "doc_type": "新闻|投关记录|董事会决议|股权激励|收购摘要|业绩公告|研报|其他",
 "title_guess": "文档标题或主题，≤30字",
 "companies": ["提及的上市公司简称或全称，最多8个"],
 "events": [{"subject":"主体方","rel":"供应|合作|投资|收购|减持|增持|回购|融资|质押|担保|中标|投产|业绩|裁决|其他","object":"客体方或产品","date":"YYYY-MM-DD或空","amount":"金额/数量原文或空"}],
 "direction": "利好|利空|中性",
 "direction_why": "判断理由，≤40字",
 "key_numbers": [{"value":"原文数字","context":"数字含义，≤10字"}],
 "tags": ["3-8个主题词，两到四字，用于词云，如:算力,液冷,海外扩产"]
}
规则：只依据文本，不推测；events 最多 6 条；只输出 JSON，不要其他文字。
新闻/快讯（单条 [时间][来源] 标题 或多条合辑）→ doc_type=新闻，tags 概括该条主题（合辑则汇总全批热词）。
用词规范：同一主题只用规范词——算力(不用AI算力/智算)、人工智能(不用AI)、机器人(不用人形/工业机器人)、新能源(不用清洁能源)、涨停(不用触及涨停/涨停潮)、燃料油(不用低硫燃料油)。"""

# K266 热词规范词表（门户 /kg/doc 展示端 import 同一份，改这里两端生效）：
# 只并真同义/明确变体，不并"股价下跌"类主题+方向复合词（防方向词虚胖）。
# 2026-10-08 按全量影子数据（2942 词/4767 计数）核定；新变体按数据补录。
TAG_ALIAS = {
    "AI算力": "算力",
    "智算": "算力",
    "AI": "人工智能",
    "人形机器人": "机器人",
    "工业机器人": "机器人",
    "清洁能源": "新能源",
    "触及涨停": "涨停",
    "涨停潮": "涨停",
    "低硫燃料油": "燃料油",
}


def norm_tags(tags):
    """tags 同义归一：变体并到规范词，去空串，归一后去重防同文档重复计数。"""
    out, seen = [], set()
    for t in tags:
        t = TAG_ALIAS.get(t, t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# rel 枚举白名单（K266-W7 人审结论加固）：与 PROMPT 同步维护。模型实际会
# 游离出 150+ 变体（全量影子统计），落影子前统一归一，防图谱边类型脏。
REL_WHITELIST = {
    "供应",
    "合作",
    "投资",
    "收购",
    "减持",
    "增持",
    "回购",
    "融资",
    "质押",
    "担保",
    "中标",
    "投产",
    "业绩",
    "裁决",
    "其他",
}

REL_ALIAS = {
    # 资本类
    "并购": "投资",
    "被投资": "投资",
    "跟投": "投资",
    "增资": "融资",
    "增发": "融资",
    "IPO": "融资",
    "发行": "融资",
    "发行股份": "融资",
    "发行可转债": "融资",
    "募集配套资金": "融资",
    "赎回": "回购",
    "解押": "质押",
    "拟减持": "减持",
    # 供应链类
    "订单": "供应",
    "获得订单": "供应",
    "采购": "供应",
    "交付": "供应",
    "出口": "供应",
    "进口": "供应",
    "进出口": "供应",
    # 合作类
    "客户": "合作",
    "研发": "合作",
    "合作研发": "合作",
    "合资": "合作",
    # 裁决/监管类
    "处罚": "裁决",
    "制裁": "裁决",
    "制裁调整": "裁决",
    "调查": "裁决",
    "诉讼": "裁决",
    "管制": "裁决",
    "警告": "裁决",
    "垄断": "裁决",
    "监管": "裁决",
    "监管批准": "裁决",
    # 业绩/评价类
    "评级": "业绩",
    "评级调整": "业绩",
    "目标": "业绩",
    "指引": "业绩",
    "预测": "业绩",
    "收入预测": "业绩",
    "收入": "业绩",
    "营收": "业绩",
    "分红": "业绩",
    "数据统计": "业绩",
}


# 市场表现类概念词：rel 是具体公司动作但 object 是这类词 → 实为"股价波动"
# 事件而非公司动作（人审实锤：减持→股市表现 / 业绩→市场反应），落其他。
_CONCEPT_OBJ = re.compile(r"股价|市场份额|市场表现|市场反应|市场走势|股市|异动|涨跌")


def norm_events(evs):
    """events rel 归一：白名单直过、同义变体映射、其余落'其他'
    （'股价波动/政策发布/任职'等非公司动作类 & 模型乱用一律入其他桶）。
    只管 rel 槽位，subject/object/date/amount 不动。非 dict 元素剔除。"""
    out = []
    for ev in evs:
        if not isinstance(ev, dict):
            continue
        rel = str(ev.get("rel") or "").strip()
        if not rel:
            rel = "其他"
        rel = REL_ALIAS.get(rel, rel)
        if rel not in REL_WHITELIST:
            rel = "其他"
        if rel != "其他" and _CONCEPT_OBJ.search(str(ev.get("object") or "")):
            rel = "其他"
        ev["rel"] = rel
        out.append(ev)
    return out


# 防串组闸门用：泛词/概念词不参与闸门（邻条命中属正常，不构成串组证据）
GENERIC_ENTS = {
    "公司",
    "董事会",
    "监事会",
    "股东大会",
    "中国",
    "证监会",
    "上交所",
    "深交所",
    "股权",
    "股份",
    "股票",
    "债券",
    "可转债",
    "员工持股",
    "未知",
    "未知主体",
    "无",
    "自身股票",
    "股票市场",
    "营收",
    "利润",
    "净利润",
    "高管变动",
    "监管争议",
    "资金募集",
    "榜单发布",
    "政治竞选",
    "第三季度财报",
    "退休",
    "增持",
    "回购",
}


def cross_gate(p, item, sib_text):
    """防串组闸门（K266-W7 人审结论加固）：判定实体不在本条原文（精确或
    模糊≥2字命中）但整串出现在同批邻条 → 疑似串组，返回触发实体名，否则 None。
    实锤案例：companies 串入邻条公告主体（智度股份）、events.object 串入邻条
    标题（北洋瑞恒部分股权）。短词泛词不参与，防误触发白缴单判。"""
    ents = [str(c) for c in (p.get("companies") or [])]
    for ev in p.get("events") or []:
        if isinstance(ev, dict):
            ents += [ev.get("subject"), ev.get("object")]
    for e in ents:
        e = (e or "").strip()
        if len(e) < 3 or e in GENERIC_ENTS:
            continue
        own = (
            e in item
            or difflib.SequenceMatcher(None, e, item, autojunk=False)
            .find_longest_match(0, len(e), 0, len(item))
            .size
            >= 2
        )
        if not own and e in sib_text:
            return e
    return None


def load_done():
    done = set()
    err_cnt = {}
    if os.path.exists(SHADOW):
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("raw_ok"):  # ERR 记录不算已判，下轮自动重试
                        done.add(rec["doc_id"])
                    else:
                        err_cnt[rec["doc_id"]] = err_cnt.get(rec["doc_id"], 0) + 1
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/k266_m3_structure.py", exc)
    # 钉子户防御：ERR≥3（如超长条目独占 GPU 也判不完）视为放弃，防每轮
    # 空转 600s 看门狗超时（2026-10-08 i11 三连败实测）
    for did, n in err_cnt.items():
        if n >= 3:
            done.add(did)
    return done


NEWS_HEAD = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\[[^\]]+\] .+")


def split_news_items(text):
    """新闻批次文本 → 单条列表（按 [时间][来源] 标题 头切分，正文跟随其行）。"""
    items, cur = [], None
    for line in text.splitlines():
        if NEWS_HEAD.match(line):
            if cur:
                items.append("\n".join(cur))
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        items.append("\n".join(cur))
    return [t for t in (s.strip() for s in items) if t]


# ── K266-W7 新闻合批判定 ──────────────────────────────────────────────
# 实测（2026-10-08 23:30 探针，8 条真实待判）：单判 5.5s/条，每条重复评估
# ~650tok PROMPT 是纯开销；合批一次出 JSON 数组，探针实测 ~70-80tok/s 生成。
# 输出预算按探针：单条实际 ~250tok（模型沿用基础 schema 带 key_numbers，
# 不强省——销量/回购金额等确有价值）；K=3 → 输出 ~750tok + prompt 800tok
# + 组正文 ~500tok ≈ 2050 < num_ctx 16384 ✓（K=8 实测 1800 截断翻车）。
# 2026-11-04 交互延迟优化：BATCH_K 6→3、num_predict 1700→900、CHAR_CAP 1100→600。
# 依据：np=1 下请求串行，K=6 单次生成 1500tok≈45s（33tok/s），人类聊天插队
# 平均要等半个 batch（~22s）；K=3 单请求≈23s，等待减半。代价仅 prompt 前缀
# prefill 重复（800tok@2637tok/s≈0.3s/组，可忽略），总吞吐基本持平。
# 回滚：k266_m3_structure.py.bak-batch3-20261104（原值 K=6 / 1700 / 1100）。
# 解析带截断兜底：num_predict 仍不够时只丢尾部截断条目（回退单判），不废整组。
# 影子记录带 batched:true 供人审抽样区分口径。
BATCH_K = 3
BATCH_CHAR_CAP = 600
BATCH_ITEM_TRUNC = 400
BATCH_PROMPT = (
    PROMPT
    + """

=== 新闻合批模式 ===
输入是 N 条编号新闻（每条以 [n] 行开头，n=1..N），逐条独立判定，互不混串。
输出严格 JSON 数组（不要其他文字），第 n 个元素对应第 n 条新闻：
[{"n":1,"doc_type":"新闻","title_guess":"...","companies":["..."],"events":[...],"direction":"...","direction_why":"...","key_numbers":[...],"tags":["..."]},
 {"n":2,"doc_type":"新闻",...}, ...]
规则：doc_type 恒为 "新闻"；events 每条新闻最多 2 条、companies 最多 5 个、
key_numbers 最多 2 条；title_guess/direction_why ≤25字；tags 3-5 个。"""
)


def ask_ollama(
    text, prompt=PROMPT, num_predict=600, trunc=1200, timeout=280, hard_deadline=600
):
    """三试重试 + 硬超时看门狗。

    requests 的 timeout 语义：stream=False 时是"整个生成完成"的时限（首字节=
    完整响应）。GPU 被抢占/长文档下生成可超时（2026-10-08 实测 17:03 起挂死
    18h）。故整条调用放工作线程跑，join 超过 hard_deadline 即放弃 → judge 落
    ERR 下一条；abort 事件通知 worker 在重试检查点自行退出，防僵尸请求继续
    占 Ollama 推理槽挤压后续条目（并发 16k ctx 会把层挤到 CPU，全体变慢）。
    合批调用传 BATCH_PROMPT/更大 num_predict/trunc（K266-W7）。
    """
    import threading

    box = {}
    abort = threading.Event()

    def _worker():
        body = {
            "model": MODEL,
            "think": False,
            "stream": False,
            # num_ctx 统一 16384（2026-10-24 收口）：全仓共用一个 ctx，
            # 避免 Ollama 因 num_ctx 变化整模型重载（实测 15~46s/次，10-24
            # 一天 99 次）。原先钉 4096 的依据是 2026-10-08 的"8192 卡死
            # 240s+"对照实验——那是 CPU 推理 + f16 KV cache 时代；iGPU/Vulkan
            # + KV q8_0 后复测 16384 与 4096 吞吐相同（33.5 vs 33.8 tok/s）。
            # 正文截断（trunc/BATCH_CHAR_CAP）保持不变：加大 ctx 只放宽上限，
            # 不改批次口径。num_predict 仍防病态长生成。
            "options": {"num_ctx": NUM_CTX, "num_predict": num_predict},
            "messages": [
                {
                    "role": "user",
                    "content": prompt + "\n\n=== 文档文本（截断）===\n" + text[:trunc],
                }
            ],
        }
        for attempt in (1, 2, 3):  # 并发抢占下偶发空输出/无 JSON，三试
            if abort.is_set():
                return  # 看门狗已放弃，勿再发起新请求
            try:
                r = requests.post(OLLAMA, json=body, timeout=timeout)
                r.raise_for_status()
                box["last"] = r.json()["message"]["content"]
                if "{" in box["last"]:
                    return
            except Exception as e:
                box["last"] = str(e)[:120]
            if abort.is_set():
                return
            time.sleep(2)
        box.setdefault("last", "")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(hard_deadline)
    if t.is_alive():
        abort.set()  # 当前阻塞中的请求 ≤timeout 内自行退出，不再 3 连击
        raise TimeoutError("ollama hard timeout %ss（慢流/队列饿死）" % hard_deadline)
    return box.get("last", "")


def parse_json(raw):
    """容错：剥 ```json 围栏，取最外层 {}"""
    t = re.sub(r"```(?:json)?|```", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError("no json object")
    obj = json.loads(m.group(0))
    # 规范化关键字段
    for k, dflt in (
        ("companies", []),
        ("events", []),
        ("key_numbers", []),
        ("tags", []),
    ):
        if not isinstance(obj.get(k), list):
            obj[k] = dflt
    obj.setdefault("doc_type", "其他")
    obj.setdefault("direction", "中性")
    return obj


def _scan_json_objects(s):
    """扫出字符串内所有顶层完整 {...} 的原文切片（字符串态感知，容忍尾截断）。"""
    objs, depth, start, instr, esc = [], 0, None, False, False
    for i, ch in enumerate(s):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                objs.append(s[start : i + 1])
                start = None
            elif depth < 0:
                break
    return objs


def parse_json_array(raw):
    """合批输出解析：按 n 对齐成 {n: obj}。

    路径①剥围栏取最外层 [...] 直接 loads；失败（num_predict 尾截断/
    夹杂噪声）走路径②——逐个扫出完整对象解析，尾部残缺条目自然丢弃，
    对应新闻由调用方回退单判补上，宁慢勿错。整体无对齐项时抛错（整组回退）。
    """
    t = re.sub(r"```(?:json)?|```", "", raw).strip()
    arr = None
    m = re.search(r"\[[\s\S]*\]", t)
    if m:
        try:
            v = json.loads(m.group(0))
            arr = v if isinstance(v, list) else None
        except ValueError:
            arr = None
    if arr is None:
        arr = []
        for obj_src in _scan_json_objects(t):
            try:
                obj = json.loads(obj_src)
                if isinstance(obj, dict):
                    arr.append(obj)
            except ValueError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/k266_m3_structure.py", exc)
    out = {}
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        try:
            n = int(obj.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= 99:
            continue
        for k, dflt in (
            ("companies", []),
            ("events", []),
            ("tags", []),
            ("key_numbers", []),
        ):
            if not isinstance(obj.get(k), list):
                obj[k] = dflt
        obj.setdefault("doc_type", "新闻")
        obj.setdefault("direction", "中性")
        out[n] = obj
    if not out:
        raise ValueError("no aligned items")
    return out


# ── 错峰闸门 2（K-INTEL-20261020-PEAKSHIFT 备稿，默认关闭零风险）──
# 依据（2026-10-20 晨，T-I6/T-I7）：雪球分类 round26 07:15 被 m3（PID 8200）等
# 挤停，08/09/10 整点班全 probe busy skip——白天分类产能归零、pending 反弹 18,875。
# K266_PEAK_SHIFT=1 时，m3 长跑避开雪球分类整点班窗口（08/09/10 整点 ± 缓冲，
# 默认 20 分钟）：窗口内批判定前暂歇到窗口结束，留痕一行 JSON；睡眠分片间插
# ollama_lock.heartbeat 防 20 分钟陈锁被抢（STALE_S=1200）。默认关闭直通。
CLASSIFY_HOURS = (8, 9, 10)  # 雪球分类整点班时点（T-I6 实证 08/09/10 班）
PEAK_SHIFT_BUFFER_M = 20  # 窗口缓冲分钟（env K266_PEAK_SHIFT_BUFFER_M 可覆盖）
PEAK_SHIFT_CHUNK_S = 300  # 睡眠分片秒数（≤ STALE_S/4，保心跳裕量）


def classify_shift_wait_end(now_t=None, buffer_m=None):
    """雪球分类班窗口判定纯函数：在窗口内返回距窗口结束秒数，否则 None。
    半开区间 [整点-缓冲, 整点+缓冲)，端点值 0 不返回（防尾窗空转一行）。"""
    t = now_t or time.localtime()
    buf = PEAK_SHIFT_BUFFER_M if buffer_m is None else buffer_m
    minutes = t.tm_hour * 60 + t.tm_min + t.tm_sec / 60.0
    for h in CLASSIFY_HOURS:
        c = h * 60
        if c - buf <= minutes < c + buf:
            return (c + buf - minutes) * 60.0
    return None


def yield_to_classify_shifts(
    log=print, heartbeat=None, sleep=None, now=None, localtime=None
):
    """闸门 2（K266_PEAK_SHIFT=1 才启用，默认直通零变化）：落在分类班窗口
    → 暂歇到窗口结束再继续批判定；每窗口留痕一行 JSON。心跳分片防陈锁。
    依赖晚绑定解析：测试/长跑可注入 time 家族替身。"""
    sleep = sleep or time.sleep
    now = now or time.time
    localtime = localtime or time.localtime
    if os.environ.get("K266_PEAK_SHIFT") != "1":
        return
    try:
        buf = int(os.environ.get("K266_PEAK_SHIFT_BUFFER_M") or PEAK_SHIFT_BUFFER_M)
    except ValueError:
        buf = PEAK_SHIFT_BUFFER_M
    while True:
        remain = classify_shift_wait_end(localtime(), buf)
        if remain is None:
            return
        log(
            json.dumps(
                {
                    "gate": "k266_peak_shift",
                    "event": "yield_window",
                    "window_hours": list(CLASSIFY_HOURS),
                    "buffer_m": buf,
                    "remain_s": round(remain),
                },
                ensure_ascii=False,
            )
        )
        end = now() + remain
        while now() < end:
            sleep(min(PEAK_SHIFT_CHUNK_S, max(1.0, end - now())))
            if heartbeat:
                heartbeat()


# ── 闸门 3（2026-11-04 K266-PROBE-YIELD，默认关闭零风险）──
# 依据（2026-11-04 20:20 实测）：k266 长跑期间人类交互被挤到 6.6~20.2s（空闲基线
# 0.34~0.42s）。已有两层让位都盖不住这个场景——闸门 2 只挡 08/09/10 整点班；
# ollama_lock 租约（300s）只管得住"拿锁的人"，而人类交互不拿锁，只有延迟探测
# 能感知。故 K266_PROBE_YIELD=1 时：每 PROBE_YIELD_EVERY_S 至多探一次
# （num_predict=1 ping，与主调用同 NUM_CTX，不触发重载），延迟超阈判 busy →
# 交还锁（让 xq/JEV 能进）→ 睡 POLL 重探，累计让位超 BUDGET_S → 本轮收工
# （k266_services 5 分钟一轮自愈，影子是增量的，收工不丢进度）。
# BUDGET 取 900s 而非常用的 1800s：k266 是增量补积压，收工代价只是一轮重来；
# 让位超过一刻钟等于把长跑挂死，不如交还调度、下一 tick 再抢。
# 探测口径照抄 xq_classify.ollama_probe_busy（禁代理 opener / 异常按 busy）。
PROBE_YIELD_FLAG = "K266_PROBE_YIELD"
PROBE_YIELD_THRESH_S = 6.0  # busy 阈（秒）：与 xq / K-STOCK 守卫 6s 同源
PROBE_YIELD_POLL_S = 60  # busy 后重探间隔
PROBE_YIELD_BUDGET_S = 900  # 单轮累计让位上限：够骑过一轮人类对话，不把长跑挂死
PROBE_YIELD_EVERY_S = 60  # 两次探测最小间隔：把探针开销摊到 ~1 次/分钟
PROBE_YIELD_TIMEOUT_S = 10  # 单次探测超时
PROBE_PING_URL = "http://127.0.0.1:11434/api/generate"
_PROBE_STATE = {"last": -1e9, "used": 0.0}  # 进程级：上次探测时刻 / 已让位秒数
_STOP = {"on": False}  # 闸门 3 判定本轮收工（退出判读循环，不写影子）


def _probe_conf():
    """延迟求值（_peak_shift_conf 判例）：调用时读 env；非法值回落默认。"""
    on = os.environ.get(PROBE_YIELD_FLAG) == "1"
    thresh, poll_s = PROBE_YIELD_THRESH_S, PROBE_YIELD_POLL_S
    budget_s, every_s = PROBE_YIELD_BUDGET_S, PROBE_YIELD_EVERY_S
    try:
        thresh = float(os.environ.get("K266_PROBE_YIELD_THRESH_S") or thresh)
        poll_s = int(os.environ.get("K266_PROBE_YIELD_POLL_S") or poll_s)
        budget_s = int(os.environ.get("K266_PROBE_YIELD_BUDGET_S") or budget_s)
        every_s = int(os.environ.get("K266_PROBE_YIELD_EVERY_S") or every_s)
    except ValueError:
        sys.stderr.write(
            "[k266] WARN invalid K266_PROBE_YIELD_* env, fallback defaults\n"
        )
    return on, thresh, poll_s, budget_s, every_s


def ollama_probe_busy(thresh_s, timeout_s=PROBE_YIELD_TIMEOUT_S):
    """单次占用探测：最小推理 ping（num_predict=1，think 关），延迟超阈判 busy。
    禁代理开 opener（A1 报告判例：环境代理会污染 127.0.0.1 探测）。
    返回 (busy, latency_s)；探测异常（超时/拒绝）按 busy 计——fail-safe 不硬挤。"""
    payload = json.dumps(
        {
            "model": MODEL,
            "prompt": "ping",
            "stream": False,
            "think": False,
            "options": {"num_ctx": NUM_CTX, "num_predict": 1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        PROBE_PING_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        opener.open(req, timeout=timeout_s).read()
    except Exception:
        return True, min(time.time() - t0, float(timeout_s))
    latency = time.time() - t0
    return latency > thresh_s, latency


def probe_yield_gate(lock=None, probe=None, sleep=None, now=None, log=print, state=None):
    """闸门 3（K266_PROBE_YIELD=1 才启用，默认直通零变化）。
    返回 True=继续判读；False=让位预算耗尽，本轮收工（调度下轮自愈）。
    节流：距上次探测不足 EVERY_S 直接放行，探针开销摊到 ~1 次/分钟。
    busy：交还锁 → 睡 POLL → 重抢锁（抢不到说明别人在跑正式活，本轮收工）→ 重探。
    依赖晚绑定解析（_peak_shift_conf 判例）：测试与长跑可注入替身。"""
    state = _PROBE_STATE if state is None else state
    probe = probe or ollama_probe_busy
    sleep = sleep or time.sleep
    now = now or time.time
    on, thresh, poll_s, budget_s, every_s = _probe_conf()
    if not on:
        return True
    if now() - state["last"] < every_s:
        return True
    while True:
        state["last"] = now()
        busy, latency = probe(thresh)
        if not busy:
            return True
        if state["used"] + poll_s > budget_s:
            log(
                json.dumps(
                    {
                        "gate": "k266_probe_yield",
                        "event": "wait_expired",
                        "latency_s": round(latency, 2),
                        "used_s": round(state["used"]),
                        "budget_s": budget_s,
                    },
                    ensure_ascii=False,
                )
            )
            return False
        log(
            json.dumps(
                {
                    "gate": "k266_probe_yield",
                    "event": "busy_yield",
                    "latency_s": round(latency, 2),
                    "thresh_s": thresh,
                    "poll_s": poll_s,
                    "used_s": round(state["used"]),
                    "budget_left_s": round(budget_s - state["used"]),
                },
                ensure_ascii=False,
            )
        )
        if lock is not None:
            try:
                lock.release()
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "quant_backtest/k266_m3_structure.py", exc
                )
        sleep(poll_s)
        state["used"] += poll_s
        if lock is not None:
            try:
                if not lock.acquire("k266_m3", wait_s=600):
                    log(
                        json.dumps(
                            {
                                "gate": "k266_probe_yield",
                                "event": "lock_lost",
                                "used_s": round(state["used"]),
                            },
                            ensure_ascii=False,
                        )
                    )
                    return False
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "silent-except %s: %s", "quant_backtest/k266_m3_structure.py", exc
                )


def main():
    limit = 10**9
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    # 错峰闸门 2：启动即落分类班窗口则先暂歇（默认关闭直通；抢锁前让位免占坑）
    yield_to_classify_shifts()
    # Ollama 错峰锁：与 news 域 JEV 回填互斥（wait_s 覆盖其 1800s deadline 上限；
    # 正常退出/异常走 atexit 释放，强杀靠锁模块 20 分钟陈旧回收）
    _root = os.path.dirname(BASE)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import atexit
    import ollama_lock

    atexit.register(ollama_lock.release)
    if not ollama_lock.acquire("k266_m3", wait_s=1900):
        print("ollama busy: JEV 回填持锁超 1900s，本轮让位（调度 5 分钟自愈）")
        return
    done = load_done()
    n_ok = n_err = 0

    def judge(did, name, rec, text):
        """判一篇（整文档或单条新闻）并落影子。"""
        nonlocal n_ok, n_err
        yield_to_classify_shifts(log=print, heartbeat=ollama_lock.heartbeat)
        # 闸门 3：人类交互探测让位（默认关闭直通）；预算耗尽→本轮收工，不写影子
        if not probe_yield_gate(lock=ollama_lock):
            _STOP["on"] = True
            return
        try:
            parsed = parse_json(ask_ollama(text))
            parsed["tags"] = norm_tags(parsed.get("tags", []))  # 源头归一，展示端兜底
            parsed["events"] = norm_events(parsed.get("events", []))
            out = {
                "doc_id": did,
                "name": name,
                "src": rec.get("src"),
                "kind": rec.get("kind"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pv": 1,
                "raw_ok": True,
                "chars": len(text),
                **parsed,
            }
            flag = ""
        except Exception as e:
            out = {
                "doc_id": did,
                "name": name,
                "src": rec.get("src"),
                "kind": rec.get("kind"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pv": 1,
                "raw_ok": False,
                "chars": len(text),
                "err": f"{type(e).__name__}: {str(e)[:120]}",
            }
            parsed, flag = {}, " ERR"
        with open(SHADOW, "a", encoding="utf-8") as fo:
            fo.write(json.dumps(out, ensure_ascii=False) + "\n")
        if out["raw_ok"]:
            n_ok += 1
        else:
            n_err += 1
        p = out if out["raw_ok"] else {}
        print(
            f"[{n_ok + n_err}] {did} {str(name)[:28]}{flag} "
            f"{p.get('doc_type', '')} {p.get('direction', '')} "
            f"tags={','.join(p.get('tags', [])[:5])}",
            flush=True,
        )
        ollama_lock.heartbeat()
        # 租约制（K-ORG-20261025-LOCK）：握满 300s 交还一手，防长跑把 JEV 饿死
        if not ollama_lock.tick("k266_m3"):
            print("[lock] tick 未抢回锁（重试 6 次），继续跑并告警", flush=True)

    def judge_news_batch(items):
        """合批判一组新闻（K266-W7）：一次 LLM 出 JSON 数组逐条落影子；
        组失败/模型漏项逐条回退单判（ERR 纪律与单判完全一致）。"""
        nonlocal n_ok, n_err
        yield_to_classify_shifts(log=print, heartbeat=ollama_lock.heartbeat)
        # 闸门 3：合批调用同口径让位（抢不到锁/预算耗尽→整组不判，下轮重来）
        if not probe_yield_gate(lock=ollama_lock):
            _STOP["on"] = True
            return
        try:
            batch_text = "\n".join(
                "[%d] %s" % (i, it[3][:BATCH_ITEM_TRUNC])
                for i, it in enumerate(items, 1)
            )
            by_n = parse_json_array(
                ask_ollama(
                    batch_text,
                    prompt=BATCH_PROMPT,
                    num_predict=900,
                    trunc=BATCH_CHAR_CAP + BATCH_ITEM_TRUNC + 64,
                )
            )
        except Exception:
            by_n = {}  # 组级失败：整组回退，不落 ERR（回退单判自会记）
        for i, (did_i, name_i, rec, item) in enumerate(items, 1):
            p = by_n.get(i)
            if not p:
                judge(did_i, name_i, rec, item)
                continue
            p.pop("n", None)
            g = cross_gate(
                p, item, "".join(it[3] for j, it in enumerate(items, 1) if j != i)
            )
            if g:
                print(f"[gate] {did_i} 疑似串组({g})→单判", flush=True)
                judge(did_i, name_i, rec, item)
                continue
            p["tags"] = norm_tags(p.get("tags", []))  # 源头归一，与单判同口径
            p["events"] = norm_events(p.get("events", []))
            out = {
                "doc_id": did_i,
                "name": name_i,
                "src": rec.get("src"),
                "kind": rec.get("kind"),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pv": 1,
                "raw_ok": True,
                "chars": len(item),
                "batched": True,
                **p,
            }
            with open(SHADOW, "a", encoding="utf-8") as fo:
                fo.write(json.dumps(out, ensure_ascii=False) + "\n")
            n_ok += 1
            print(
                f"[{n_ok + n_err}] {did_i} {str(name_i)[:28]} "
                f"{p.get('direction', '')} tags={','.join(p.get('tags', [])[:5])}"
                f" (batch{len(items)})",
                flush=True,
            )
            ollama_lock.heartbeat()
            # 租约制（K-ORG-20261025-LOCK）：组间让位，JEV 回填才有窗口
            if not ollama_lock.tick("k266_m3"):
                print("[lock] tick 未抢回锁（重试 6 次），继续跑并告警", flush=True)

    # 合批缓冲：攒够 BATCH_K 条或 BATCH_CHAR_CAP 字即冲一组（跨批次文件混组
    # 无妨——每条自带 doc_id/name；极限 600+末条最长 400(BATCH_ITEM_TRUNC)
    # =1000 字 < trunc 1064，预算已留）
    news_buf, news_chars = [], 0

    def flush_news():
        nonlocal news_chars
        if not news_buf or _STOP["on"]:
            return  # 闸门 3 收工：残组不判（缓冲由 TEXTS 重建，幂等不丢）
        items = news_buf[:]
        news_buf.clear()
        news_chars = 0
        judge_news_batch(items)

    with open(TEXTS, encoding="utf-8") as f:
        for line in f:
            if _STOP["on"] or n_ok + n_err >= limit:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            did = rec.get("doc_id")
            if not did or did in done or rec.get("status") != "ok":
                continue
            # 新闻批次（C案合批）：切单条入缓冲，攒组一次判；批次级不落影子
            # （防 tags 双计）；doc_id=批次id+i序号，幂等口径不变
            if rec.get("src") == "news" or str(rec.get("name", "")).startswith("news_"):
                for idx, item in enumerate(split_news_items(rec.get("text", "")), 1):
                    if _STOP["on"] or n_ok + n_err >= limit:
                        break
                    did_i = f"{did}i{idx:02d}"
                    if did_i in done:
                        continue
                    news_buf.append(
                        (did_i, "%s#%02d" % (rec.get("name", ""), idx), rec, item)
                    )
                    news_chars += len(item)
                    if len(news_buf) >= BATCH_K or news_chars >= BATCH_CHAR_CAP:
                        flush_news()
                continue
            judge(did, rec.get("name"), rec, rec.get("text", ""))
    flush_news()  # 收尾残组
    print(
        f"done ok={n_ok} err={n_err} shadow={SHADOW}"
        + (" stop=probe_yield" if _STOP["on"] else "")
    )


if __name__ == "__main__":
    main()
