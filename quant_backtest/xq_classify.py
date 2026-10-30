# -*- coding: utf-8 -*-
"""雪球舆情分类管道 v1（K-ORG-165 P1：水镜情报线治理第一刀）。

设计（2026-10-18 深夜 Hermes 治理提案 P1，老田「下一步」放行）：
  - 原表零改动：分类结果落派生表 xq_status_classified（upsert 幂等，
    重跑零副作用——tushare staging materialize 闸同款哲学）；
  - 增量优先：只标 xq_status 中未标行（新帖优先 ORDER BY created_at
    DESC），存量 14212 行靠每日轮逐步追平；
  - LLM 分工（省 token 防幻觉）：分类+情绪交给 qwen3.5:9b（A0 参谋长
    判读同款，质量已验证）；标的代码用纯正则预提取（不进 prompt）；
  - JSON-only 契约（D1 判例：system 只要求 JSON，与用户消息无散文冲突）；
    解析失败 parse_ok=0 → degraded 状态位落账，重跑自动补（NOT IN 幂等）；
  - fail-visible：模型/网络异常退出码 1，状态日志落盘（ASCII，判例）；
  - 空壳行跳过（纯图帖无文字，分类无意义）。

分类维度（对齐 RiskGuard 事件风险校准取数需求）：
  event_type ∈ {行情观点, 个股研判, 宏观政策, 基金理财, 交易策略, 广告推广, 闲聊杂谈}
  sentiment  ∈ {bullish, bearish, neutral}
  tickers    = 正则提取（6 位 A 股/ETF、5 位港股、常见基金关键词）

用法：
  python xq_classify.py --limit 500        # 增量一轮（每日 18:30 调度用）
  python xq_classify.py --limit 12         # 小批量实弹验证
  python xq_classify.py --status           # 只读看进度
  python xq_classify.py --reticker         # 提取器升级后重算存量 tickers（秒级）
  python xq_classify.py --recontent --limit 1200   # v1.3 语义存量补标
  python xq_classify.py --retry-degraded   # v1.4 degraded 行重标（LLM 截断行捞回，retry_count<3 封顶）
  python xq_classify.py --worker-id w1 --segment A --limit 500   # v1.7 多实例并行（日志分片+奇偶分片）
  python xq_classify.py --order desc --limit 500           # v1.7 status_id 降序吃远端历史存量
  python xq_classify.py --order engagement --limit 500     # v1.7 view_count 降序高互动优先打标
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path
import logging
from tools.safe_urlopen import safe_urlopen

if __package__:
    from .tools.xq_ticker_normalize import normalize_tickers
else:
    # 2026-10-27 GY-01：仓根存在同名 tools 包，`from tools.xq_ticker_normalize` 会
    # 被 pytest importlib 模式解析成非包对象 → ImportError；改为直连同级 tools 目录。
    import os as _os
    import sys as _sys
    _tools_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "tools")
    if _tools_dir not in _sys.path:
        _sys.path.insert(0, _tools_dir)
    from xq_ticker_normalize import normalize_tickers

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "xueqiu" / "xueqiu_info.db"
LOG_DIR = (
    Path(r"C:\aios\automation_runs") / "xq_classify"
)  # 定时任务产物统一根（2026-10-18 老田拍板）
STATUS_LOG = LOG_DIR / "xq_classify_status.log"

# v1.7 multi-instance parallel (2026-10-18): --worker-id shards the status log
# per instance (Ollama par=2, two classify workers run side by side). Empty
# worker id keeps the historical single-file behavior exactly. Path is resolved
# at call time (delayed-eval precedent) so tests / long-running processes see
# the current worker id, not an import-time snapshot.
_WORKER_ID = ""
_WORKER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

MODEL = "qwen3.5:9b"
PROMPT_VERSION = "v1.3"  # v1.3：+ad/edu 出口、tech 定义收紧（实效检验揪出广告污染 98 条，老田拍板 2026-10-18）
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
TIMEOUT_SEC = 60  # think:false 后单条 ~1s；60s 纯兜底
BODY_CHARS = 300  # 正文截断（短文本分类足够）
DEGRADED_MAX_RETRY = 3  # v1.4 degraded 重试封顶：≥3 次不再重试（防死循环耗 token）

# v1.5 速率旋钮（rate-opt 工位 2026-10-18）：环境变量可覆盖 LLM options，
# 未设置时取下面常量。
# 2026-10-24 收口：DEFAULT_NUM_CTX 2048 → 16384。全仓统一 ctx 的理由是
# Ollama 每次 num_ctx 变化都会**整模型重载**（实测 15~46s/次，10-24 一天
# 99 次）；分类任务本身用不了 16K，但它和 M3/报告/平台共用同一个常驻实例，
# 单独要小 ctx 会把模型反复拉进拉出。iGPU/Vulkan + KV q8_0 下 16384 与 4096
# 生成吞吐相同（33.5 vs 33.8 tok/s），故统一小值不必省。
DEFAULT_NUM_CTX = 16384
DEFAULT_NUM_PREDICT = 256


# ── v1.8 错峰闸门 1（K-INTEL-20261020-PEAKSHIFT 备稿，默认关闭零风险）──
# 依据（2026-10-20 晨，T-I6/T-I7）：round26 07:15 被 k266_m3_structure.py 等
# 3 个外部消费者挤停，08/09/10 整点班全 probe busy skip，白天分类产能归零。
# 现网 probe 是 automation agent 的 echo 口径（不在仓内，A1 排查报告实锤），
# 本闸门把它落进脚本：XQ_PEAK_SHIFT=1 时 round 前对 Ollama 做最小占用探测
# （num_predict=1 ping），延迟超阈判 busy → 按让位时长轮询等待重试，busy 期间
# 结构化一行 JSON 记 occupant（netstat 反查 11434 建连 PID+进程名）；等满让位
# 时长仍 busy → 本轮让位跳过（等价历史 skip，调度下轮自愈）。
# 默认值依据：无竞争基线 ~0.7s/条、K-STOCK 守卫 busy 阈 6s 同源对齐；让位
# 1800s ≈ 半个整点班间隔——k266 m3 占用以小时计，等半班仍挤则整班所剩无几，
# 让位比半速硬挤干净。开关默认关闭：env 缺省时 gate 直通，行为零变化。
PEAK_SHIFT_FLAG = "XQ_PEAK_SHIFT"
PEAK_SHIFT_THRESH_S = 6.0  # busy 阈（秒）：与 K-STOCK 守卫 6s 阈对齐
PEAK_SHIFT_WAIT_S = 1800  # 单轮让位等待上限：半个整点班间隔
PEAK_SHIFT_POLL_S = 60  # 等待期重探间隔
PEAK_SHIFT_PROBE_TIMEOUT_S = 10  # 单次探测超时（A1 报告现网 probe 同口径）
PEAK_SHIFT_PING_URL = "http://127.0.0.1:11434/api/generate"


def _peak_shift_conf():
    """延迟求值（_status_log_path 判例）：调用时读 env，测试与长跑进程即时生效。
    非法值回落默认（_resolve_int_knob 同哲学，float 同款处理）。"""
    on = os.environ.get(PEAK_SHIFT_FLAG) == "1"
    thresh, wait_s, poll_s = PEAK_SHIFT_THRESH_S, PEAK_SHIFT_WAIT_S, PEAK_SHIFT_POLL_S
    try:
        thresh = float(os.environ.get("XQ_PEAK_SHIFT_THRESH_S") or thresh)
        wait_s = int(os.environ.get("XQ_PEAK_SHIFT_WAIT_S") or wait_s)
        poll_s = int(os.environ.get("XQ_PEAK_SHIFT_POLL_S") or poll_s)
    except ValueError:
        sys.stderr.write(
            "[xq_classify] WARN invalid XQ_PEAK_SHIFT_* env, fallback defaults\n"
        )
    return on, thresh, wait_s, poll_s


def ollama_probe_busy(thresh_s, timeout_s=PEAK_SHIFT_PROBE_TIMEOUT_S):
    """单次占用探测：最小推理 ping（num_predict=1，think 关），延迟超阈判 busy。
    禁代理开 opener（A1 报告判例：环境代理会污染 127.0.0.1 探测）。
    返回 (busy, latency_s)；探测异常（超时/拒绝）按 busy 计——fail-safe 不硬挤。"""
    # num_ctx 必须与 DEFAULT_NUM_CTX 一致（2026-10-24 收口）：这里原先是 512
    # 的"最小占用"探针，但 ctx 与主调用不一致时，探测本身就会让 Ollama 重载一次
    # 模型（15~46s），既污染延迟读数、又把模型拉成 512 驻留、害下一条正式请求
    # 再重载。统一 16384 后，探针只是"同一实例上的极短请求"，测的才是真占用。
    payload = json.dumps(
        {
            "model": MODEL,
            "prompt": "ping",
            "stream": False,
            "think": False,
            "options": {"num_ctx": DEFAULT_NUM_CTX, "num_predict": 1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        PEAK_SHIFT_PING_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        opener.open(req, timeout=timeout_s).read()
    except Exception:
        return True, min(time.time() - t0, float(timeout_s))
    latency = time.time() - t0
    return latency > thresh_s, latency


def ollama_occupants():
    """11434 消费者定位：netstat -ano 反查 ESTABLISHED 且 Foreign=…:11434 的
    PID（客户端侧；serve 自身 accept 侧 Local=11434 天然排除）→ tasklist 映射
    进程名。解析失败返回 []（留痕空列表，不阻塞让位决策）。"""
    pids = set()
    try:
        ns = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        ).stdout
        for line in ns.splitlines():
            parts = line.split()
            # 列形：Proto Local Foreign State PID
            if (
                len(parts) >= 5
                and parts[0] == "TCP"
                and parts[2].endswith(":11434")
                and parts[3] == "ESTABLISHED"
            ):
                pids.add(parts[4])
    except Exception:
        return []
    if not pids:
        return []
    try:
        tl = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        ).stdout
        return [
            "%s pid=%s" % (row[0], row[1].strip())
            for row in csv.reader(tl.splitlines())
            if len(row) >= 2 and row[1].strip() in pids
        ]
    except Exception:
        return []


def peak_shift_gate(now=None, sleep=None, probe=None, occupants=None):
    """闸门 1（XQ_PEAK_SHIFT=1 才启用，默认直通零变化）：round 前探测让位。
    返回 True=放行开轮；False=让位超时本轮跳过。busy 期间每次重探留痕一行
    JSON（occupant PID + 让位决策），格式贴 guard_aborts.jsonl 结构化判例。
    依赖晚绑定解析（_status_log_path 判例）：测试/长跑可注入或 monkeypatch。"""
    probe = probe or ollama_probe_busy
    occupants = occupants or ollama_occupants
    now = now or time.time
    sleep = sleep or time.sleep
    on, thresh, wait_s, poll_s = _peak_shift_conf()
    if not on:
        return True
    deadline = now() + wait_s
    attempt = 0
    while True:
        attempt += 1
        busy, latency = probe(thresh)
        if not busy:
            log(
                "peak-shift probe ok attempt=%d latency_s=%.1f threshold_s=%.1f"
                % (attempt, latency, thresh)
            )
            return True
        log(
            json.dumps(
                {
                    "gate": "xq_peak_shift",
                    "event": "busy_yield",
                    "attempt": attempt,
                    "latency_s": round(latency, 1),
                    "threshold_s": thresh,
                    "occupant": occupants(),
                    "wait_left_s": max(0, round(deadline - now())),
                },
                ensure_ascii=False,
            )
        )
        if now() >= deadline:
            log(
                "peak-shift wait expired wait_s=%d -> skip round (next shift retries)"
                % wait_s
            )
            return False
        sleep(min(poll_s, max(1, deadline - now())))


def _resolve_int_knob(raw, default: int, name: str) -> int:
    """旋钮解析：None/空串=默认；正整数=覆盖；非法=回落默认+stderr 告警。"""
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip())
        if v <= 0:
            raise ValueError(v)
        return v
    except ValueError:
        sys.stderr.write(
            "[xq_classify] WARN invalid %s=%r, fallback to %d\n" % (name, raw, default)
        )
        return default


def build_llm_options(num_ctx=None, num_predict=None) -> dict:
    """构造 Ollama options（唯一入口，可单测）。

    优先级：显式实参 > 环境变量 XQ_LLM_NUM_CTX / XQ_LLM_NUM_PREDICT > 默认。
    显式实参传 None 表示"未指定"，交由环境变量/默认接管。
    """
    ctx_raw = num_ctx if num_ctx is not None else os.environ.get("XQ_LLM_NUM_CTX")
    pred_raw = (
        num_predict if num_predict is not None else os.environ.get("XQ_LLM_NUM_PREDICT")
    )
    return {
        "temperature": 0.1,
        "num_ctx": _resolve_int_knob(ctx_raw, DEFAULT_NUM_CTX, "XQ_LLM_NUM_CTX"),
        "num_predict": _resolve_int_knob(
            pred_raw, DEFAULT_NUM_PREDICT, "XQ_LLM_NUM_PREDICT"
        ),
    }


EVENT_TYPES = [
    "行情观点",
    "个股研判",
    "宏观政策",
    "基金理财",
    "交易策略",
    "广告推广",
    "闲聊杂谈",
]
SENTIMENTS = ["bullish", "bearish", "neutral"]
CONTENT_TYPES = ["tech", "community", "news", "fundamental", "edu", "ad"]

# v1.4.1 实证别名映射（degraded 病根修复，2026-10-18）：
# qwen3.5:9b 对教学科普/资讯转述/打新类帖倾向输出「内容描述词」而非七选一
# 枚举词——探针实证 136684573 answer 完整合法 JSON、done_reason=stop，
# 病根是 event_type 白名单拒绝而非截断（日志 answer[:80] 恰好显示在
# confidence 处纯系 JSON 长度巧合）；temperature=0.1 下重试输出恒定，
# retry_count 封顶也救不回（team-lead 大 ctx/predict 实证 upgraded=0）。
# parse 侧确定性短路（同「广告推广→ad」判例），别名自带形态信息，覆盖
# content_type；未收录的漂移标签仍走 degraded（宁可降级不乱归类）。
EVENT_TYPE_ALIASES = {
    "投教科普": ("闲聊杂谈", "edu"),  # 教学科普帖 → 杂谈 + edu 形态保真
    "个股资讯": ("个股研判", "news"),  # 事实性转述 → 个股维度 + news 形态
    "港股打新": ("交易策略", "news"),  # 打新认购 → 操作策略 + news 形态
}

CONTENT_TYPE_DESC = {
    "tech": "技术面分析（只收K线/均线/形态/指标/缠论/量价/点位/图形/买卖点的技术性讨论；投教科普不在此类）",
    "community": "社区探讨（散户交流/情绪表达/争论吐槽/求点评/打卡跟风，无论据链）",
    "news": "个股资讯（事实性转述：公告/业绩/订单/政策/新闻，少主观判断）",
    "fundamental": "基本面逻辑（业绩/行业/估值/商业模式/竞争格局论证）",
    "edu": "投教科普（方法论教学：怎么选基金/怎么打新/概念科普/投资入门）",
    "ad": "广告活动（悬赏/竞猜/红包/课程推广/平台活动，直接归此类）",
}

SYSTEM_PROMPT = (
    "你是金融舆情分类器。只输出一行 JSON，不要任何其他文字："
    '{"event_type":"<七选一>","content_type":"<六选一>",'
    '"sentiment":"<三选一>","confidence":<0到1小数>}。'
    "event_type 只能取：" + "、".join(EVENT_TYPES) + "。"
    "content_type 只能取（内容形态，与 event_type 正交）："
    + "；".join("%s=%s" % (k, v) for k, v in CONTENT_TYPE_DESC.items())
    + "。"
    "sentiment 只能取：" + "、".join(SENTIMENTS) + "（对 A股/港股大盘或个股的态度）。"
)

# 标的提取（纯规则）：A股 6 位数字（含前缀提示词）、港股 5 位、ETF/基金名常见词
# v1.1（2026-10-18）：RE_FUND 词干分支后缀改必选、删贪婪中间段——原版把
# 「红利指数基金的四大投」「债券B<br/>1、」等文本碎片整段吞进 tickers（R3 校准取数预演发现）；
# extract_tickers 增加噪音清洗兜底（<br/> 残片/中文虚词/年月形数字）。
# v1.2（2026-10-18，老田拍板「A 股代码提取升级」）：
#   ① 修 \b 病根——Python re 的 \b 按 \w 定边界，中文与数字同为 \w，
#      「贵州茅台600519」中「台|6」之间无边界 → 中文紧贴的代码全量漏提；
#      左界改 (?<![0-9.%])（中文/字母紧贴放行，小数/百分比防误截）。
#   ② 6 位码段白名单（232020 等 23 开头噪音段 52 行实证污染）——只收
#      合法交易所段：沪深主板/科创板/创业板/北交所/场内基金 ETF LOF；
#      20 开头年月形、23 开头编号形自动出局（白名单替代黑名单）。
#   ③ 右界 (?![元块])：金额语义（「150000 元」）不收。
RE_A = re.compile(r"(?<![0-9.%])([0-9]{6})(?![0-9])(?!\s*[元块])")
# 港股 5 位 v1.3：三通道判定（实证 status_id=144250900：tickers=["10676","23200",
# "18000","20000"] 全是金额/点位形 5 位噪音，且港股段 00-99 全合法无法白名单——
# 语境共现 + 后缀/区间硬排除双保险）：
#   ① pfx  「港股00700」「HK:00700」前缀紧邻 → 直接命中（不受共现限制）；
#   ② sfx  「00700.HK」「00700hk」后缀 → 直接命中（小写兼容）；
#   ③ bare 裸 5 位：语境共现（_HK_CONTEXT）或中文紧贴（「腾讯00700」，左界
#      允许 \w 中文；前置字是虚词/动词如「赚了18000」不算紧贴名）才收。
#   ④ ct   雪球 cashtag 形态「$腾讯控股(00700)$」——代码被 $/( 紧邻包裹
#      （实证 171914290/150923887：v1.3 首版 bare 通道误杀此主力形态，补直击）。
# 硬排除（③）：数字区间「18000-20000」（连字符双界）、点位/金额后缀
# 「10676点」「18000 元/块/万/手」——144250900 四个噪音全由此出局。
# v1.6（2026-10-18，xq-quality-dev 刀二，全量 19991 行只读扫描 103 条
# 「收码∧点位语境」实证）：③④ 通道追加两项硬排除——
#   ⑴ 小数前瞻 (?![.．][0-9])：小数点位「触及22669.04点」「报26312.63点」
#      的小数部分把「点」后缀隔断而绕过 v1.3 排除（144660897/140221805/
#      112510264 实证，后者「住房公积金11116.27亿元」经紧贴名通道误收）；
#      港股代码恒为整数，NNNNN.dd 形必属数值，零误杀面。
#   ⑵ 后缀排除集补「关/附」：「险守31000关口」（103804289）「关键点位
#      30500附近」（107875256）——v1.3 观察项「关口」语境落锤。
# v1.6-b2（2026-10-18，刀二补丁2）：③④ 通道后缀排除集改为允许可选
# 「整数」前缀（(?:整数)?[元块万亿手点关附笔币]），堵「31000整数关口」形——
# 数字后紧跟「整」使 v1.6 的 \s*[关附] 失配而整串绕过（103804289 实证）。
# v1.3 观察项「关口」语境第二阶段至此落锤（整数关口/整数关 均出局）。
RE_HK = re.compile(
    r"(?:港股|HK:)\s*(?<![0-9])(?P<pfx>[0-9]{5})(?![0-9])"
    r"|(?<![0-9.])(?P<sfx>[0-9]{5})(?![0-9])\.?[Hh][Kk](?![A-Za-z0-9])"
    # b3: 补「笔」「币」后缀 2026-10-19（量词后缀「2万笔/3万币」→ 20000笔/30000币
    #     形残类出局；版本号保持 v1.6 不动，在途测试锚定 v16）
    r"|(?<![0-9.\-])(?P<bare>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*(?:整数)?[元块万亿手点关附笔币])"
    r"|[(（$](?P<ct>[0-9]{5})(?![0-9\-])(?![.．][0-9])(?!\s*(?:整数)?[元块万亿手点关附笔币])"
)
# 港股语境词（v1.3 bare 通道共现检测，整条 title+desc 匹配一次）。
# 「恒生」不单列——恒生电子(600570) 是 A 股，只收「恒指/恒生指数/恒生科技」。
_HK_CONTEXT_RE = re.compile(
    r"港股|港交所|恒指|恒生指数|恒生科技|国企指数|红筹|(?<![A-Za-z])[Hh][Kk](?![A-Za-z])"
)
# 中文紧贴名的前置字黑名单（v1.3）：「赚了18000」「涨到20000」这类动词+数字
# 不是公司名紧贴形；白名单制不现实（公司名无限），黑名单兜高频虚词/动词。
_ADJ_NOISE = set("了的到至达约近超破剩付花收给缴补返省欠赔是价净均倍赢输")
RE_FUND = re.compile(
    r"([0-9]{6})(?:\.OF| 基金)|((?:沪深300|中证500|中证1000|创业板|科创50|纳斯达克|标普500|恒生科技|红利|白酒|医药|半导体|新能源|军工|黄金|债券|货币)(?:ETF|基金|指数|LOF|联接))"
)
_RE_NOISE = re.compile(r"[<>]|br/|[的了这那哦吧呀]")
# 6 位代码段白名单（v1.2）：命中任一前缀才算场内证券，其余 6 位数字（编号/
# 年月/订单号）一律出局。港股东 5 位不走此表（港股 00-99 段全合法）。
_CODE_SEGS = (
    "600",
    "601",
    "603",
    "605",
    "688",
    "689",  # 沪：主板 + 科创板
    "000",
    "001",
    "002",
    "003",  # 深：主板 + 原中小板
    "300",
    "301",
    "302",  # 创业板
    "430",
    "920",
    "83",
    "87",  # 北交所 / 新三板
    "50",
    "51",
    "52",
    "56",
    "58",
    "15",
    "16",  # 场内基金：ETF / LOF
)
_TICKER_VERSION = "v1.6"  # v1.6-b2（2026-10-18，刀二补丁2）：后缀排除集支持
# 可选「整数」前缀，堵「31000整数关口」形（实证 103804289）；
# v1.3 观察项「关口」语境第二阶段落锤。
# 版本号暂留 v1.6 的理由：v1.6 本刀即「港股指位语境硬排除」，
# 「整数关口」是同一语境漏收的第二条实证，非谱系新版本；且
# tests/test_xq_ticker_extract.py 现有断言锚定 v1.6（该件为他人在途，
# 依并行施工不干预原则不代改）。待该件所有者把断言升至 v1.7 后，
# 本行随之升版，**并必须补跑一次 `--reticker` 全量重算**清除旧
# 版本残留（20000×2 / 31000 等整数关口形）。
# v1.6（2026-10-18，xq-quality-dev 刀二）：裸 5 位/cashtag
# 通道加小数前瞻（NNNNN.dd点/亿 形点位金额出局）+ 后缀排除集
# 补「关/附」（关口/附近）。版本谱系：v1.4 已被 --retry-degraded
# 占用、v1.5 已被速率旋钮占用，ticker 谱系自 v1.3 跳 v1.6。
# （v1.3 2026-10-18：港股裸 5 位改三通道判定——前缀/.HK 后缀
# 直接命中、中文紧贴名命中、裸数字需港股语境共现；加数字区间
# 连字符双界与「点/元/块/万/手」后缀硬排除，144250900 实证。）


def _clean_ticker(code: str) -> bool:
    """噪音清洗（v1.2 白名单制）：6 位纯数字必须命中 _CODE_SEGS 合法段；
    其余（5 位港股、基金词）保留 HTML 残片/中文虚词清洗。"""
    if not code or len(code) > 10 or _RE_NOISE.search(code):
        return False
    if code.isdigit() and len(code) == 6:
        return code.startswith(_CODE_SEGS)
    return True


def set_worker_id(worker_id: str) -> None:
    """Set the sharded-log worker id (empty string = historical behavior)."""
    global _WORKER_ID
    _WORKER_ID = (worker_id or "").strip()


def _status_log_path() -> Path:
    """Resolve the status log path at call time.

    Non-empty worker id -> xq_classify_status_<worker-id>.log in the same
    LOG_DIR (per-instance shard, no interleaved writes between workers);
    empty worker id -> the historical STATUS_LOG constant verbatim.
    """
    if _WORKER_ID:
        return LOG_DIR / ("xq_classify_status_%s.log" % _WORKER_ID)
    return STATUS_LOG


def log(msg: str) -> None:
    """ASCII 安全状态日志（计划任务/自动化排障判例）。"""
    LOG_DIR.mkdir(exist_ok=True)
    line = "%s | %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with _status_log_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xq_classify.py", exc)
    print(line)


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS xq_status_classified (
             status_id TEXT PRIMARY KEY,
             event_type TEXT,
             sentiment TEXT,
             tickers TEXT,
             confidence REAL,
             parse_ok INTEGER,
             model TEXT,
             prompt_version TEXT,
             labeled_at TEXT
           )"""
    )
    con.commit()
    # v1.2 迁移：content_type 列（已存在则跳过；automation 旧进程并发写兼容）
    cols = [r[1] for r in con.execute("PRAGMA table_info(xq_status_classified)")]
    if "content_type" not in cols:
        con.execute("ALTER TABLE xq_status_classified ADD COLUMN content_type TEXT")
        con.commit()
    # v1.4 迁移：retry_count 列（degraded 重标通道计次，幂等）
    if "retry_count" not in cols:
        con.execute(
            "ALTER TABLE xq_status_classified ADD COLUMN retry_count INTEGER DEFAULT 0"
        )
        con.commit()
    if "norm_tickers" not in cols:
        con.execute("ALTER TABLE xq_status_classified ADD COLUMN norm_tickers TEXT")
        con.commit()


def extract_tickers(title: str, desc: str) -> list:
    text = (title or "") + " " + (desc or "")
    out = []
    for m in RE_A.findall(text):
        code = m if isinstance(m, str) else m[0]
        if code not in out and _clean_ticker(code):
            out.append(code)
    hk_ctx = bool(_HK_CONTEXT_RE.search(text))  # v1.3：港股语境共现整条只查一次
    for m in RE_HK.finditer(text):
        code = m.group("pfx") or m.group("sfx") or m.group("ct")  # 直击通道
        if not code:
            code = m.group("bare")
            if not hk_ctx:  # 裸 5 位：无语境时仅「中文紧贴名」形放行
                prev = text[m.start() - 1] if m.start() > 0 else ""
                if prev not in _ADJ_NOISE and "\u4e00" <= prev <= "\u9fff":
                    pass  # 紧贴名（腾讯00700）
                else:
                    continue  # 孤立裸数字，无语境不出局
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
            out.append(code)
    for m in RE_FUND.findall(text):
        code = m[0] if m[0] else m[1]
        if code and code not in out and len(out) < 8 and _clean_ticker(code):
            out.append(code)
    return out[:8]


def classify_one(con: sqlite3.Connection, row: tuple) -> bool:
    """单条标注：成功(含 degraded)返回 True（落账），硬异常返回 False。"""
    status_id, title, desc, category = row
    body = ((title or "") + "\n" + (desc or "")).strip()[: BODY_CHARS + 60]
    payload = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": body or "（无文字内容）"},
            ],
            "stream": False,
            "think": False,  # 判例：qwen3.5 思考块吃满小 ctx 会把答案挤空（探针实证 0.7s vs 34.8s/失控分钟级）
            "options": build_llm_options(),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        t0 = time.time()
        resp = safe_urlopen(req, timeout=TIMEOUT_SEC)
        answer = json.loads(resp.read())["message"]["content"].strip()
        # 防御：老版客户端/模型把思考块写进 content 时剥离
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
        dt = time.time() - t0
    except Exception as e:
        log("HARD-FAIL %s: %s" % (status_id, str(e)[:120]))
        return False

    parse_ok = 0
    event_type = sentiment = content_type = None
    confidence = None
    try:
        m = re.search(r"\{.*\}", answer, re.S)
        obj = json.loads(m.group(0)) if m else {}
        if (
            obj.get("content_type") in CONTENT_TYPES
        ):  # v1.2 内容形态（缺失不降级 parse_ok，可 --recontent 补）
            content_type = obj["content_type"]
        if obj.get("event_type") in EVENT_TYPES:
            event_type = obj["event_type"]
        elif obj.get("event_type") in EVENT_TYPE_ALIASES:
            # v1.4.1 别名短路：漂移描述词 → 合法枚举 + 形态派生（实证驱动；
            # 放在 content_type 解析之后——别名形态语义强制覆盖模型值）
            event_type, content_type = EVENT_TYPE_ALIASES[obj["event_type"]]
            # v1.4.1 兜底留痕（fail-visible 判例）：别名命中写 stderr 一条，
            # 供审计命中率/发现新漂移标签；不进 STATUS_LOG（避免自动化日志噪音）
            sys.stderr.write(
                "[xq_classify] ALIAS-FALLBACK %s: event_type=%r -> %r/%r\n"
                % (status_id, obj["event_type"], event_type, content_type)
            )
        if obj.get("sentiment") in SENTIMENTS:
            sentiment = obj["sentiment"]
        conf = obj.get("confidence")
        if isinstance(conf, (int, float)) and 0 <= conf <= 1:
            confidence = round(float(conf), 2)
        parse_ok = 1 if (event_type and sentiment) else 0
    except Exception:
        parse_ok = 0
    # v1.3 规则短路（优先于 LLM）：event_type=广告推广 → content_type 强制 'ad'
    # 实效检验实证：v1.2 四类无出口，98 条广告帖被迫污染 fundamental/community
    if event_type == "广告推广":
        content_type = "ad"
    if not parse_ok:
        log("DEGRADED %s: unparseable=%r" % (status_id, answer[:80]))

    # v1.4.2 DB 写段异常隔离（15:24 静默死亡实证：LLM 成功后 DB 段抛异常会
    # 直接穿出循环，round/retry 轮无收行死亡）——DB 故障与硬失败同语义：
    # 计 hard 不杀轮，stderr 留诊断，下一轮幂等重跑。
    try:
        # v1.4.3 刀 A：INSERT OR REPLACE 未列出的列按 DEFAULT 清零（删旧插新
        # 语义）——显式带旧 retry_count 进 REPLACE，防封顶计数被偷清。
        # 归零语义由 retry_degraded 的 upgraded 分支显式执行（docstring 承诺），
        # classify_one 单体只负责「不改别人的计次」。
        old_retry = con.execute(
            "SELECT retry_count FROM xq_status_classified WHERE status_id=?",
            (str(status_id),),
        ).fetchone()
        tickers = json.dumps(extract_tickers(title, desc), ensure_ascii=False)
        norm_tickers = json.dumps(
            normalize_tickers(tickers), ensure_ascii=False, separators=(",", ":")
        )
        con.execute(
            """INSERT OR REPLACE INTO xq_status_classified
               (status_id, event_type, sentiment, tickers, confidence,
                parse_ok, model, prompt_version, labeled_at, content_type,
                retry_count, norm_tickers)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(status_id),
                event_type,
                sentiment,
                tickers,
                confidence,
                parse_ok,
                MODEL,
                PROMPT_VERSION,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                content_type,
                (old_retry[0] or 0) if old_retry else 0,
                norm_tickers,
            ),
        )
    except Exception as e:
        sys.stderr.write(
            "[xq_classify] DB-FAIL %s: %s: %s\n"
            % (status_id, type(e).__name__, str(e)[:120])
        )
        traceback.print_exc()
        return False
    if parse_ok:
        log(
            "OK %s cat=%s %s/%s | %s conf=%s %.1fs"
            % (
                status_id,
                category,
                event_type,
                content_type or "-",
                sentiment,
                confidence,
                dt,
            )
        )
    return True


def recontent_run(limit: int) -> int:
    """v1.2 存量补标：对已分类但 content_type 为空的行重跑 LLM 只补内容形态列。

    判例：lineage 诚实——prompt_version 标 'v1.2-re'（event_type 系 v1 判定，
    content_type 系 v1.2 补判），两段 lineage 不混淆。
    """
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        """SELECT c.status_id, s.title, s.desc
           FROM xq_status_classified c JOIN xq_status s ON s.status_id = c.status_id
           WHERE c.parse_ok = 1 AND c.content_type IS NULL LIMIT ?""",
        (limit,),
    ).fetchall()
    log("recontent start n=%d" % len(rows))
    if not rows:
        log("recontent end rc=0 nothing-to-backfill")
        con.close()
        return 0
    ok = hard = consec = 0
    for status_id, title, desc in rows:
        body = ((title or "") + "\n" + (desc or "")).strip()[: BODY_CHARS + 60]
        payload = json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": body or "（无文字内容）"},
                ],
                "stream": False,
                "think": False,
                "options": build_llm_options(),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            resp = safe_urlopen(req, timeout=TIMEOUT_SEC)
            answer = json.loads(resp.read())["message"]["content"].strip()
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
        except Exception as e:
            hard += 1
            consec += 1
            log("RE-HARD-FAIL %s: %s" % (status_id, str(e)[:120]))
            if consec >= 8:
                log("RE-ABORT consecutive-fail=%d" % consec)
                break
            continue
        consec = 0
        try:
            m = re.search(r"\{.*\}", answer, re.S)
            obj = json.loads(m.group(0)) if m else {}
            ct = (
                obj.get("content_type")
                if obj.get("content_type") in CONTENT_TYPES
                else None
            )
        except Exception:
            ct = None
        con.execute(
            "UPDATE xq_status_classified SET content_type=?, prompt_version=?, labeled_at=? WHERE status_id=?",
            (
                ct,
                "v1.2-re",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(status_id),
            ),
        )
        ok += 1
        con.commit()
    con.commit()
    n_left = con.execute(
        "SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok=1 AND content_type IS NULL"
    ).fetchone()[0]
    con.close()
    # v1.2.1 刀 B：手动通道硬失败全量可见——hard>0 报 1，与「recontent 是
    # 有人盯着跑的补标」语义对齐（run() 的 consec>=8 属长跑自动通道让路，
    # 口径不同不对齐；2026-10-18 主会话裁决）。
    rc = 1 if hard > 0 else 0
    log("recontent end rc=%d ok=%d hard=%d left=%d" % (rc, ok, hard, n_left))
    return rc


def retry_degraded(limit: int) -> int:
    """v1.4 DEGRADED 行重标通道：parse_ok=0（LLM 输出截断/不可解析）的行
    既有 run() 的 NOT IN 幂等够不着（已落账过），此通道单独捞回重跑。

    口径：parse_ok=0 AND COALESCE(retry_count,0)<3；复用 classify_one 单行
    分类（不复制逻辑），INSERT OR REPLACE 原地翻新不新增行——成功的升级为
    正常分类行（retry_count 归零），仍截断的保持 degraded 并累加
    retry_count，封顶后不再入选。硬失败（模型/网络）不计次（属瞬态故障，
    连续熔断由 consec 闸兜底），下一轮可再来。
    """
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        """SELECT c.status_id, c.retry_count, s.title, s.desc, s.category
           FROM xq_status_classified c JOIN xq_status s ON s.status_id = c.status_id
           WHERE c.parse_ok = 0 AND COALESCE(c.retry_count, 0) < ?
           ORDER BY c.labeled_at LIMIT ?""",
        (DEGRADED_MAX_RETRY, limit),
    ).fetchall()
    log(
        "retry-degraded start candidates=%d limit=%d cap=%d"
        % (len(rows), limit, DEGRADED_MAX_RETRY)
    )
    if not rows:
        log("retry-degraded end rc=0 nothing-to-retry")
        con.close()
        return 0
    upgraded = still = hard = consec = 0
    # v1.4.2 轮级兜底（15:24 静默死亡实证）：循环/收尾任何异常都不得吞掉
    # end 收行——收行是让路探测与巡检的消费契约；异常走 stderr 全栈 +
    # RETRY-ABORT-ERROR 新行型（不改既有行格式），degraded_left=-1 表异常取数。
    try:
        for status_id, old_retry, title, desc, category in rows:
            if not classify_one(con, (status_id, title, desc, category)):
                hard += 1
                consec += 1
                log("RETRY-HARD-FAIL %s" % status_id)
                if consec >= 8:
                    log(
                        "RETRY-ABORT %d consecutive hard-fails (model/network busy?)"
                        % consec
                    )
                    break
                con.commit()
                continue
            consec = 0
            cur = con.execute(
                "SELECT parse_ok FROM xq_status_classified WHERE status_id=?",
                (str(status_id),),
            ).fetchone()
            if cur and cur[0] == 1:
                upgraded += 1  # 升级：显式归零（v1.4.3 刀 A 后 REPLACE 不再隐式清零）
                con.execute(
                    "UPDATE xq_status_classified SET retry_count=0 WHERE status_id=?",
                    (str(status_id),),
                )
            else:
                still += 1  # 仍截断：保持 degraded，累加计次
                con.execute(
                    "UPDATE xq_status_classified SET retry_count=? WHERE status_id=?",
                    ((old_retry or 0) + 1, str(status_id)),
                )
            con.commit()
        con.commit()
        n_left = con.execute(
            "SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok=0"
        ).fetchone()[0]
    except Exception as e:
        traceback.print_exc()
        log("RETRY-ABORT-ERROR %s: %s" % (type(e).__name__, str(e)[:120]))
        n_left = -1
    con.close()
    log(
        "retry-degraded end rc=0 upgraded=%d still_degraded=%d hard_fail=%d degraded_left=%d"
        % (upgraded, still, hard, n_left)
    )
    return 0


def build_pending_query(order: str = "asc", segment: str = "") -> str:
    """Build the pending-fetch SQL (single '?' placeholder for LIMIT).

    v1.7 multi-instance parallel knobs, default behavior unchanged:
      - order="asc"       -> the historical SQL verbatim (created_at DESC,
                             newest first) -- the default, zero drift;
      - order="desc"      -> status_id DESC (older backlog first);
      - order="engagement"-> view_count DESC via JOIN (high-engagement rows
                             labeled first; NULL view_count sorts last on
                             DESC in SQLite, so bare rows are not stranded);
      - segment="A"|"B"   -> even/odd status_id tail filter so two workers
                             split the pending queue without overlap
                             (CAST on status_id only; created_at stays an
                             ISO text column and is never CASTed per the
                             project iron rule).

    Pure string builder, no DB access -- unit-testable in isolation.
    """
    if order == "engagement":
        sql = (
            "SELECT s.status_id, s.title, s.desc, s.category\n"
            "FROM xq_status s\n"
            "LEFT JOIN xq_status_classified c ON c.status_id = s.status_id\n"
            "WHERE c.status_id IS NULL\n"
            "  AND NOT ((s.title IS NULL OR s.title='')\n"
            "           AND (s.desc IS NULL OR s.desc=''))"
        )
    else:
        sql = (
            "SELECT s.status_id, s.title, s.desc, s.category\n"
            "FROM xq_status s\n"
            "WHERE s.status_id NOT IN\n"
            "      (SELECT status_id FROM xq_status_classified)\n"
            "  AND NOT ((s.title IS NULL OR s.title='')\n"
            "           AND (s.desc IS NULL OR s.desc=''))"
        )
    if segment == "A":
        sql += "\n  AND (CAST(s.status_id AS INTEGER) % 2) = 0"
    elif segment == "B":
        sql += "\n  AND (CAST(s.status_id AS INTEGER) % 2) = 1"
    if order == "desc":
        sql += "\nORDER BY s.status_id DESC"
    elif order == "engagement":
        sql += "\nORDER BY s.view_count DESC"
    else:
        sql += "\nORDER BY s.created_at DESC"
    sql += "\nLIMIT ?"
    return sql


def run(limit: int, order: str = "asc", segment: str = "") -> int:
    """Consume one round of pending rows (v1.7: + order/segment knobs).

    Default order="asc" / segment="" reproduces the historical behavior
    exactly (same SQL shape, same log line types); non-default values only
    add one extra "round params" log line for parallel-run observability.
    """
    if not peak_shift_gate():  # v1.8 错峰闸门 1：默认关闭时直通零变化
        return 0
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(build_pending_query(order, segment), (limit,)).fetchall()
    log("round start pending_sampled=%d limit=%d" % (len(rows), limit))
    if order != "asc" or segment:
        log("round params order=%s segment=%s" % (order, segment or "-"))
    if not rows:
        log(
            "round end rc=0 all-caught-up total=%d"
            % con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
        )
        con.close()
        return 0
    ok = hard = consec = 0
    # v1.4.2 轮级兜底（与 retry_degraded 同刀）：异常不吞 round end 收行，
    # ABORT-ERROR 新行型留因，rc=1（异常按需关注语义复用）。
    try:
        for i, row in enumerate(rows):
            if classify_one(con, row):
                ok += 1
                consec = 0
            else:
                hard += 1
                consec += 1
                if consec >= 8:
                    # 熔断看「连续失败」而非累计占比：前期成功抬高占比分母会让
                    # 后期超时空转数小时（07-18 实证：385 ok 后每条 60s 超时，
                    # 旧条件需再熬 200 条才触发）
                    log(
                        "ABORT %d consecutive hard-fails (model/network busy?)" % consec
                    )
                    break
            con.commit()
            if (i + 1) % 25 == 0:
                log("progress %d/%d" % (i + 1, len(rows)))
        total_c = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
    except Exception as e:
        traceback.print_exc()
        log("ABORT-ERROR %s: %s" % (type(e).__name__, str(e)[:120]))
        total_c = -1
        rc = 1
    else:
        rc = 1 if consec >= 8 else 0
    log(
        "round end rc=%d labeled_this=%d hard_fail=%d derived_total=%d"
        % (rc, ok, hard, total_c)
    )
    con.close()
    return rc


def show_status() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH.as_posix(), uri=True)
    src = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    try:
        c = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
        ok = con.execute(
            "SELECT COUNT(*) FROM xq_status_classified WHERE parse_ok=1"
        ).fetchone()[0]
        deg = c - ok
        dist = con.execute(
            """SELECT event_type, sentiment, COUNT(*) FROM xq_status_classified
               WHERE parse_ok=1 GROUP BY event_type, sentiment
               ORDER BY 3 DESC LIMIT 10"""
        ).fetchall()
    except sqlite3.OperationalError:
        print("derived table not created yet; source=%d" % src)
        return 0
    print(
        "source=%d classified=%d (ok=%d degraded=%d) pending=%d"
        % (src, c, ok, deg, src - c)
    )
    for r in dist:
        print("  %-8s %-8s %d" % r)
    con.close()
    return 0


def reticker() -> int:
    """一次性重算：对已分类行按新正则重提取 tickers（不走 LLM，秒级）。

    tickers 是纯规则派生列，修复正则后历史行原地重算即可对齐新逻辑；
    只更新 xq_status_classified.tickers（派生表自身），原表零改动哲学不受影响。
    v1.2（2026-10-18）：段白名单 + 中文紧贴左界修复后重算，
    total=2012 changed=144 kept=1868 now_with_ticker=619，232020 污染 52→0。
    """
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    ensure_table(con)
    rows = con.execute(
        "SELECT status_id, (SELECT s.title FROM xq_status s WHERE s.status_id=c.status_id),"
        " (SELECT s.desc FROM xq_status s WHERE s.status_id=c.status_id), tickers"
        " FROM xq_status_classified c"
    ).fetchall()
    changed = kept = 0
    for status_id, title, desc, old in rows:
        new = json.dumps(extract_tickers(title, desc), ensure_ascii=False)
        if new != (old or "[]"):
            con.execute(
                "UPDATE xq_status_classified SET tickers=? WHERE status_id=?",
                (new, status_id),
            )
            changed += 1
        else:
            kept += 1
    con.commit()
    n_t = con.execute(
        "SELECT COUNT(*) FROM xq_status_classified WHERE tickers != '[]'"
    ).fetchone()[0]
    log(
        "reticker done total=%d changed=%d kept=%d now_with_ticker=%d version=%s"
        % (len(rows), changed, kept, n_t, _TICKER_VERSION)
    )
    con.close()
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Argparse builder (v1.7: extracted for testability; defaults unchanged)."""
    ap = argparse.ArgumentParser(description="雪球舆情分类管道 v1")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--status", action="store_true")
    ap.add_argument(
        "--reticker",
        action="store_true",
        help="v1.1 按新正则重算已分类行的 tickers（不过 LLM）",
    )
    ap.add_argument(
        "--recontent",
        action="store_true",
        help="v1.2 对已分类但 content_type 为空的行重跑 LLM 补内容形态",
    )
    ap.add_argument(
        "--retry-degraded",
        action="store_true",
        help="v1.4 重跑 degraded（parse_ok=0）行：成功升级，仍截断累加 "
        "retry_count，>=3 次不再重试",
    )
    # v1.7 multi-instance parallel knobs (all default = historical behavior)
    ap.add_argument(
        "--worker-id",
        default="",
        dest="worker_id",
        metavar="ID",
        help="v1.7 多实例并行：非空时状态日志写分片文件 "
        "xq_classify_status_<ID>.log（同目录）；空 = 完全现状",
    )
    ap.add_argument(
        "--order",
        default="asc",
        choices=("asc", "desc", "engagement"),
        help="v1.7 取数顺序：asc=现状（created_at 降序新帖优先）；"
        "desc=status_id 降序吃远端历史；engagement=view_count "
        "降序高互动优先",
    )
    ap.add_argument(
        "--segment",
        default="",
        choices=("A", "B"),
        help="v1.7 奇偶分片：A=status_id 偶数尾，B=奇数尾；"
        "双实例各吃一半互不重叠；缺省 = 不分片",
    )
    return ap


def main() -> int:
    ap = build_arg_parser()
    a = ap.parse_args()
    if a.worker_id:
        if not _WORKER_ID_RE.match(a.worker_id.strip()):
            ap.error("--worker-id allows [A-Za-z0-9._-] only, max 64 chars")
        set_worker_id(a.worker_id)
    if a.status:
        return show_status()
    if a.reticker:
        return reticker()
    if a.recontent:
        return recontent_run(a.limit)
    if a.retry_degraded:
        return retry_degraded(a.limit)
    return run(a.limit, order=a.order, segment=a.segment)


if __name__ == "__main__":
    sys.exit(main())
