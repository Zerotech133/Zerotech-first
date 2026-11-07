# -*- coding: utf-8 -*-
"""OS 控制台 API 桥（纯标准库）— 127.0.0.1:8091

聚合工作站全量状态供门户页面消费：
  /api/overview  容器矩阵 + 计划任务 + 备份 + 告警 + 磁盘 + 关键端口
  /health        存活探测（watchdog/巡检用）
数据带 20s TTL 缓存：首次聚合约 5-15s（docker/PS 子进程），之后毫秒级。
退出码约定：端口被占用（已有实例）→ 静默 exit 0，配合计划任务重复触发自愈。
"""

# ==== P4 批0：api_common 显式导入（禁 import *，下划线名不导出）====
from api_common import (
    ROOT,
    STATION,
    _portal_token,
    _load_cainiao_env,
    _log_rotate,
    log_exc,
    _back_link_html,
    pill_nav,
    REPORTS_DIR,
    QBT_DIR,
    data_sync_start,
    _read_json,
    CACHE_TTL, build_alerts, _LABELS_FP)
import api_kg
from api_kg import _kg_card_fb_save, _kg_label_save
from api_backtest import strategies_data
# ==== 批0 导入结束 ====
# ==== P4 批1：api_data 显式导入 ====
import api_data  # 模块名绑定：Handler 多继承基类引用 api_data.Handler
from api_data import (
    data_freshness,
    data_sync_status)
# ==== 批1 导入结束 ====
# ==== P4 批2：api_bridge 显式导入 ====
import api_bridge  # 模块名绑定（from-import 不绑模块名，批1教训）
from api_bridge import (
    bridge_table_preview,
    bridge_summary)
# ==== 批2 导入结束 ====
# ==== P4 批5：api_chanlun_pages 显式导入 ====
import api_portfolio  # 模块名绑定（多继承基类引用）
from api_portfolio import (
    _debate_say, _portfolio_set, _council_confirm, _portfolio_undo,
    _pf_csv, _portfolio_adjust, _portfolio_open,
    _council_simrun, _portfolio_close,
    _portfolio_market)
from api_screen import build_screen, _kline_data, _kline_qy, _chips_data
import api_screen  # 模块名绑定（多继承基类引用）
import api_misc  # 模块名绑定（多继承基类引用）
from api_misc import (build_overview,
    build_brief, build_home_cockpit, build_os_cockpit)
import api_chanlun_pages  # 模块名绑定（多继承基类引用）
import api_chanlun_buy  # 🛒 买点实战台（/bt-buy，K-ORG-20261107-BUYDESK）
import api_pages_portal  # B1 页面渲染族 Mixin（A5，2026-10-31）
import api_health_portal  # B2 健康/观测族 Mixin（A5，2026-10-31）
from api_health_portal import _lock  # B2b：锁对象与健康模块共享同一实例
import api_manager_portal  # B3 管理/雇佣族 Mixin（A5，2026-10-31）
from api_health_portal import (_freshness_warmup, _sub_status_key_ok,
                               _sub_status_view)  # B2 函数回流（既有调用/测试不变）
from api_chanlun_pages import (
    _chanlun_pool_push)
# ==== 批5 导入结束 ====
# ==== P4 批6：api_backtest 显式导入 ====
import api_backtest  # 模块名绑定（多继承基类引用）
from api_backtest import (
    fut_page_html,
    sellpoint_page_html,
    _bt_custom_start,
    _bt_custom_status,
    _cbt_start,
    _cbt_status,
    _bt2_start,
    _bt2_status,
    _api_my_strategies,
    _bt2_result,
    _bt2_nl)
# ==== 批6 导入结束 ====
# ==== F1：api_flow 显式导入（动作流中心：统一事件流聚合/预览/重跑）====
import api_flow  # 模块名绑定（多继承基类引用）
from watch_tower_snapshot import SnapshotAggregator
# ==== F1 导入结束 ====
import api_lineage  # 血缘只读 mixin（数据血缘归情报板块，2026-10-17）
import api_intel_evidence  # 新闻/JEV—标的显式代码证据只读 mixin
# ==== M1-M4 平台 mixin 显式导入（统一投影/回放/审批/能力看板/绩效看板）====
import api_unified      # M1/M2 统一投影 + 跨账本回放 + 证据/知识/Skill/使用读侧
import api_replay       # M1 /api/platform/replay
import api_approvals    # M1/M2 /api/platform/approvals
import api_capability   # M3-P5 /api/capability/board
import api_perf         # M4-P5 /api/perf/board + /api/perf/replay
import api_xq_intel     # 两线预案 M-RT3 /api/xq/intel 情报日报出口（2026-10-18）
import api_dataops      # 数据运营指挥台 /api/dataops/overview（采集动线+数据池+管道，2026-10-18）
import api_daily_data   # K-ORG-183 /api/daily/data 多维数据日报聚合出口（2026-10-18）
import api_guojia       # K-ORG-191 /api/guojia/data 郭嘉预判对账司出口（2026-10-18）
# ==== M1-M4 导入结束 ====
import api_execution    # R1 执行健康总线 /api/execution/health（联动层一期 2026-10-18）
import api_huangzhong   # 黄忠验收事件只读投影 /api/huangzhong/events（WO-HZ-HERMES-PHASE2-20261031）
import api_tickets      # 工单只读投影 /api/org/tasks（驾驶舱一期 2026-10-18）
import api_evolution    # 进化舱只读投影 /api/evolution/summary（驾驶舱二期 2026-10-18）
import org_command_api  # K-ORG-190 Agent 指挥台后端（/api/org 等 8 端点，2026-10-19）
import api_gate         # 周一门判只读投影 /api/gate/checkpoint（驾驶舱二期 2026-10-18）
import api_brief        # 复盘简报只读投影 /api/brief/*（联动层二期 R5 2026-10-18）
import api_notify       # K-ORG-201 通知指挥四态队列 mixin（只读，2026-10-19）
import api_news_intel   # K-ORG-207 新闻情报台三栏聚合出口（速报/政策/舆情，只读 2026-10-19）
import api_intel_bus    # 情报总线唯一出口 mixin（六域→总线→量化/飞书双路径 2026-10-19）
import api_zhuge_route  # WO-OS-09 诸葛亮建议引擎只读路由 /api/zhuge/brief（2026-10-21）
import api_zhuge_adoption  # WO-LEGION-ZHUGE-ADOPTROUTE C3 建议采纳率只追加 /api/zhuge/adoption（2026-10-27）
import api_zhuge_review  # WO-LEGION-ZHUGE-REVIEW-P0 诸葛亮 IDE 只读复核 API（2026-10-30）
import api_os_goal      # OS-CLOSE-G0 目标/事项/成果 G0 骨架（/api/os/*，2026-10-24）
from paper_data import paper_snapshot  # WO-INV-PAPER-UI 量化部 paper 只读数据口（2026-10-30）
from pool_data import pool_snapshot  # WO-INV-POOL-UI 司马徽推荐池全池复盘数据口（2026-10-30）






import glob
import hmac
import http.server
import json
import getpass
import os
import re
import subprocess
import sys
import threading
import time

PORT = 8091





_PORTAL_TOKEN = _portal_token()
_WATCH_TOWER_SNAPSHOT = SnapshotAggregator(ROOT, STATION)


def _replay_query_value(query: str):
    """Read replay_at while tolerating an unescaped timezone ``+``.

    ``parse_qs`` follows form encoding and turns a literal plus into a space.
    Only this one parameter is normalized; all other query parameters retain
    standard parsing behavior. An already encoded ``%2B`` remains ``+``.
    """
    from urllib.parse import parse_qs

    value = (parse_qs(query, keep_blank_values=True).get("replay_at") or [None])[0]
    if value is not None and "T" in value and " " in value:
        value = value.replace(" ", "+")
    return value


_load_cainiao_env()

# 变更类 POST 端点（逐一对照 do_POST 路由表；/feishu/event 为飞书回调，
# 不走 portal token，改走 X-Lark-Signature 验签，见 _api_feishu_event）
_MUTATION_POSTS = frozenset((
    "/api/monitor/run",
    "/api/monitor/pool",
    "/api/analysis/submit",  # K-STOCK-ANALYSIS-P0C2 个股分析入队（写队列文件）
    "/api/analysis/review",  # K-STOCK-ANALYSIS-P0F 事件人工复核（写复核状态）
    "/api/data/update-plan",
    "/api/data/update",
    "/api/portfolio_subscribe",
    "/api/portfolio_hire",
    "/api/trader_fire",   # K-STOCK-122 投研团队：组合解聘交易员
    "/api/combo_backtest",  # K-STOCK-275 组合回测任务登记（落盘排队）
    "/api/bt2_run",         # K-STOCK-285b 回测框架 v2 参数化回测（daemon 线程跑引擎）
    "/api/bt2_nl",          # K-STOCK-299 自然语言回测解析（规则+LLM 兜底·只解析不执行）
    "/api/my_strategies",   # K-STOCK-357 我的策略保存/删除（NL 沉淀·单文件 JSON）
    "/chanlun-anno",
    "/api/chanlun/label/save",
    "/api/report_push",
    "/api/sub_toggle",
    "/api/sub_del",
    "/api/sub_test",
    "/api/recv_add",
    "/kg/canvas_edit",
    "/kg/doc/upload",
    "/kg/lexicon/save",
    "/api/chanlun/card/feedback",
    "/api/chanlun/pool/push",
    "/api/portfolio_set",
    "/api/portfolio_undo",
    "/api/portfolio_close",
    "/api/portfolio_adjust",
    "/api/portfolio_open",
    "/api/council/confirm",
    "/api/council/simrun",
    "/api/debate/say",
    "/api/batch-backtest",
    "/api/paper_order",   # K-STOCK v0.2 模拟仓下单（BUY/SELL/双刀执行）
    "/api/paper_stop",    # K-STOCK v0.2 模拟仓止损挂/解
    "/api/manager_hire",  # K-STOCK-137d S-b2 经理雇佣（子进程 manager.py hire）
    "/api/manager_fire",  # K-STOCK-137d S-b2 经理解雇（子进程 manager.py fire）
    "/api/paper_direct_open",  # K-STOCK-137f S-d 轻量直订动线（策略市场直接建仓）
    "/api/onboard",       # K-STOCK-159 S-e 迎宾向导四问落账（建组合+雇佣链）
    "/api/ctl/alert",     # K-ORG-113 Hermes alerts 连接器写面（ack/ignore/ack_all）
    "/api/flow/rerun",    # F1 动作流中心：白名单只读产线任务重跑（schtasks /Run）
    "/api/register",     # K-ORG-190 指挥台写回：新建 Agent（org.json+prompts+审计）
    "/api/project/register",  # K-ORG-190 项目线立案（pending_a0_confirm 暂存+审计）
    "/api/agent/update",      # K-ORG-190 Agent 属性/模型编辑（改 org.json+审计）
    "/api/agent/learn",       # K-ORG-190 学习环（LLM 提炼→knowledge jsonl+审计）
    "/api/sim/confirm",       # WO-BOT-CONFIRM 人工确认（approve/reject；paper-only）
    "/api/jev/review/resolve",  # P142 补登记（L-02）：低置信复核裁决（写 DB）
    "/api/jev/review/reopen",   # P142 补登记（L-02）：legacy 重新打开（写 DB）
    "/api/d1/decision",       # WO-D1-DECISION-20261101：D1 final_decision 落账（paper-only）
    "/api/d1/meeting",        # WO-D1-MEETING-20261101：D1 投研会 create/vote/finalize
    "/api/d1/handoff",        # WO-D1-HANDOFF-20261101：D1 交棒回执 create/ack
))

# 2026-11-01 P142（审计 L-01/L-02 修复）：do_POST 的"早分支"族里含变更端点
# （analysis submit/review、jev review resolve/reopen），历史上它们先于
# :1042 的 _MUTATION_POSTS 鉴权闸门 early-return → 反代/局域网可无 token 写入。
# 这些前缀必须在 do_POST 最前面先过同一 token 闸门；zhuge/m/auth 分支保留
# 各自模块内鉴权，不在此列。
_EARLY_TOKEN_PREFIXES = (
    "/api/analysis/",
    "/api/jev/review/",
    "/api/factor/",   # P1 因子服务：compute 为计算型 POST，须先过 token 闸门
)

# C1a（研发计划 v2）：GET 形式但触发后台动作/子进程的端点——比照变更类
# POST 待遇纳入鉴权。此前局域网攻击者可直接 GET 触发数据同步/预警推送/
# 个股分析子进程（资源滥用 + 伪造飞书推送）。本机判定链复用既有机制：
# 回环直连 8091 或本机浏览器（X-Real-IP=127.0.0.1）直接放行，页面使用
# 不受影响；局域网来源须带 X-Portal-Token（与 POST 同一 token）。
_ACTION_GETS = frozenset((
    "/api/data/sync",    # 手动触发数据同步（data_sync_start 起后台子进程）
    "/api/alert/push",   # 手动触发飞书预警推送（data_sync_start("alertpush")）
    "/sa/start",         # 个股分析任务启动（起后台分析子进程）
))


_ACCESS_LOG = r"C:\Temp\portal_api_access.log"





# ===== P1-7 nlquery force=1 频控（2026-10-08 全检修复）=====
# force=1 关闭只读保护、直接触发 multi_agent LLM 现算（10-30s 任务），
# 未鉴权场景可被无限刷（LLM DoS + prompt injection 攻击面）。
# 简易内存频控：每 IP 每分钟 ≤3 次，超限 429。纯标准库、进程内计数
# （重启清零；多线程下计数近似准确，频控场景足够）。
_NLQ_FORCE_MAX_PER_MIN = 3
_NLQ_FORCE_HITS = {}   # ip -> [timestamp, ...]

# ===== K-STOCK-185b 写端点频控（安全审计 F-008）=====
# _MUTATION_POSTS 全族每 IP 每分钟 ≤12 次，超限 429。防脚本循环刷
# onboard/direct_open/雇佣等写接口（资源滥用与垃圾数据面）。人工操作
# 远达不到该阈值；本机与代理来源同口径计数（X-Real-IP 优先，nginx 注入）。
_WRITE_MAX_PER_MIN = 12
_WRITE_HITS = {}   # (bucket, key) -> [timestamp, ...]


def _rate_hit(key, max_per_min, window_sec=60.0):
    """滑动窗口计数；超限返回 True。进程内内存计数，重启清零（够用）。"""
    import time as _t
    now = _t.time()
    with _lock:
        hits = _WRITE_HITS.setdefault(key, [])
        while hits and now - hits[0] > window_sec:
            hits.pop(0)
        if len(hits) >= max_per_min:
            return True
        hits.append(now)
        return False

_cache = {"t": 0.0, "data": None}

# P1 总览门户（2026-10-17 前端重规划）：/api/health 七口探活 + 五守护任务


# K-STOCK-037 全站超级助手浮动图标（非 _COCKPIT_CSS 页的内联版）
# （K-STOCK-181 复原：f0b9f9b/137f auto 快照裹挟切割时误删本初始化行，
#   而 _portal_page:910 仍引用 → /portal NameError 裸断连，10-12 14:18 定位）


































































































































def _pf_ids_all():
    """K-STOCK-150：订阅清单中全部组合 id 集合（多组合化基础）。"""
    try:
        import subscriptions as _sub
        return {p.get("id") for p in
                (_sub.portfolios().get("portfolios") or [])
                if p.get("id")}
    except Exception:
        return {"pf_001"}


def _pf_valid(pid):
    """K-STOCK-150：pf 参数校验——必须在订阅清单内，防任意串参。"""
    return pid in _pf_ids_all()


# ---------------------------------------------------------------------------
# WO-FS-101（P3.7-lite · R1 客户面订阅端点加固）
# /api/subscription/status 的三个纯函数：掩码 / 字段最小化 / 可选 key 闸。
# 放模块级只为离线单测可直调；不依赖任何私有符号（掩码规则照
# feishu_bot_ws 同口径在本模块内重写，不做跨模块 import）。
# ---------------------------------------------------------------------------



#: 客户面响应白名单（字段最小化的唯一事实来源）
SUB_STATUS_FIELDS = ("plan_id", "plan_name", "start", "end", "status")






import os as _os
import threading as _th
import logging


# PORTAL_NO_WARMUP=1：跳过启动预热（测试环境用——真实 datamart.read
# 冷算 ~100s，会与测试的 sys.modules 补丁窗口产生线程竞态）。
if _os.environ.get("PORTAL_NO_WARMUP") != "1":
    _th.Thread(target=_freshness_warmup, daemon=True).start()


































































































import route_table  # B4a-lite：GET 精确路由表（等价生成）


_DW_BODY_MAX = 1024 * 1024  # /api/dw/query POST body 上限（1MB，防内存 DoS）


def _read_dw_body(handler, max_bytes=None):
    """读 /api/dw/* POST body（有上限）。返回 (ok, bytes, err)。

    2026-11-02 Lead 审查修复：原分派对 Content-Length 无上限/无负值校验，
    异常大请求可致内存压力；现 >1MB 或负值直接 413 fail-visible。
    """
    limit = _DW_BODY_MAX if max_bytes is None else int(max_bytes)
    try:
        ln = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return False, b"", "invalid Content-Length"
    if ln < 0 or ln > limit:
        return False, b"", "body too large"
    body = handler.rfile.read(ln) if ln > 0 else b""
    return True, body, ""


class Handler(api_data.Handler, api_chanlun_pages.Handler, api_chanlun_buy.Handler, api_pages_portal.Handler, api_health_portal.Handler, api_manager_portal.Handler, api_backtest.Handler, api_kg.Handler, api_bridge.Handler, api_portfolio.Handler, api_screen.Handler, api_misc.Handler, api_flow.Handler, api_lineage.Handler, api_intel_evidence.Handler, api_intel_bus.Handler, api_unified.Handler, api_replay.Handler, api_approvals.Handler, api_capability.Handler, api_perf.Handler, api_xq_intel.Handler, api_dataops.Handler, api_daily_data.Handler, api_guojia.Handler, api_execution.Handler, api_huangzhong.Handler, api_tickets.Handler, api_notify.Handler, api_news_intel.Handler, api_zhuge_route.Handler, api_os_goal.Handler, http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # K82 访问日志：一行式留痕（判断「用户是否看过页面」的唯一取证
        # 通道）；独立文件，不与 stderr 错误日志混写；失败不阻断请求。
        # C6b：写前按大小轮转（>50MB → .1），防止无限增长吃满磁盘
        if "/favicon" in fmt:
            return
        try:
            _log_rotate(_ACCESS_LOG)
            with open(_ACCESS_LOG, "a",
                      encoding="utf-8", errors="ignore") as f:
                f.write("%s %s %s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    self.client_address[0], fmt % args))
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)

    def _is_local_req(self):
        """P0-1b/M5（K-ORG-20261031-APIHARDEN）：仅当 TCP peer 为回环才视为本机可信域。

        修复审计 v2 M5：旧逻辑“缺 X-Portal-Proxy 即本机”可被远端伪造头利用；
        现要求 client_address 必须是 127.0.0.1/::1（8091 已绑回环，本机直连满足），
        反代来源还须 X-Real-IP 为回环。合成 stub 无 client_address 时按不可信处理，
        真实 BaseHTTPRequestHandler 一定有该属性。
        """
        addr = getattr(self, "client_address", None)
        peer = addr[0] if isinstance(addr, (tuple, list)) and addr else ""
        if peer not in ("127.0.0.1", "::1"):
            return False
        if not (self.headers.get("X-Portal-Proxy") or "").strip():
            return True   # 本机直连 8091（已绑回环）
        rip = (self.headers.get("X-Real-IP") or "").strip()
        return rip in ("127.0.0.1", "::1")

    def _check_portal_token(self):
        """P0-1b：变更类 POST 鉴权。本机放行；反代+局域网须 token 常量比对。"""
        if self._is_local_req():
            return True
        # --- X1 菜鸟登录校验（cainiao_auth.py，2026-10-09 接线）---
        # 机器通道（X-Portal-Token 常量比对）/ Bearer 菜鸟 accessToken 远程
        # 校验（60s 进程内缓存）；菜鸟不可达按 CAINIAO_AUTH_MODE 降级
        # （默认 degraded=放行，保产线）。未命中则落到原常量比对，兼容。
        import cainiao_auth
        if cainiao_auth.verify_request(self.headers, self.client_address[0],
                                       _PORTAL_TOKEN).ok:
            return True
        got = (self.headers.get("X-Portal-Token") or "").strip()
        return bool(got) and hmac.compare_digest(got, _PORTAL_TOKEN)

    def _token_tag(self):
        """P0-1b 前端联动：仅对本机来源页面注入 token + fetch 拦截，
        页面 JS 发 POST 自动带 X-Portal-Token；局域网来源页面不注入，
        攻击者从页面拿不到 token（token 不经局域网分发）。"""
        if not self._is_local_req():
            return ""
        return (
            '<script>window.__PORTAL_TOKEN=%s;</script>'
            '<script>(function(){var T=window.__PORTAL_TOKEN||"";'
            'if(!T)return;var of=window.fetch;'
            'window.fetch=function(u,o){o=o||{};o.headers=o.headers||{};'
            'if((o.method||"GET").toUpperCase()==="POST"'
            '&&!o.headers["X-Portal-Token"])o.headers["X-Portal-Token"]=T;'
            'return of.call(this,u,o);};})();</script>'
            % json.dumps(_PORTAL_TOKEN))

    def _json(self, obj, code=200):
        # NaN/Inf 递归清洗→null：Python json.dumps 默认输出 NaN 字面量（非标准
        # JSON），浏览器 JSON.parse 直接 SyntaxError→前端渲染链全断（2026-10-06
        # 大屏警告条实案，九维断更日统计值出 NaN）。清洗后前端 .get 容忍 null。
        def _san(o):
            if isinstance(o, float):
                return None if (o != o or o in (float("inf"), float("-inf"))) else o
            if isinstance(o, dict):
                return {k: _san(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_san(v) for v in o]
            return o
        body = json.dumps(_san(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        request_id = getattr(self, "_ifs_request_id", "")
        if request_id:
            self.send_header("X-Request-Id", str(request_id))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)









    # ── L1 金融驾驶舱（任务导向：查一只股票 / 今天能买吗 / 买什么） ──
    _COCKPIT_CSS = """<link rel='stylesheet' href='/static/tokens.css'>
<style>
body{background:var(--bg-page,#0d1117);color:var(--text,#e6edf3);font-family:-apple-system,
'Segoe UI','Microsoft YaHei',sans-serif;max-width:1200px;margin:0 auto;
padding:22px 16px}
h1{font-size:22px;margin:2px 0 0}
a{color:var(--link,#58a6ff);text-decoration:none}
.dim{color:var(--text-dim,#8b949e);font-size:12px;line-height:1.7}
.sec{border:1px solid var(--border,#30363d);border-radius:12px;padding:15px 17px;
background:var(--bg-surface,#0f141c);margin-top:14px}
.sec h2{font-size:15px;margin:0 0 4px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td,th{padding:8px 10px;border-bottom:1px solid var(--border-weak,#21262d);text-align:left}
th{color:var(--text-dim,#8b949e);font-weight:500;font-size:12px}
.gates{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
.gate{border:1px solid var(--border,#30363d);border-radius:10px;padding:12px 13px;
background:var(--bg-raised,#161b22);display:block;color:var(--text,#e6edf3)}
/* K-STOCK-403（10-15）：闸门专用语义色 + 整条横幅。
   🔴 判例：原 .g-on/.g-off 直接复用 --color-ok/--color-err（绿/红），
   与个股信号卡片的绿/红**同一套语义** ⇒ 老田 08:25 看到
   「30%不能买」与「绿色可买入」同时出现，误以为自相矛盾。
   实为两层语义：闸门=「能不能新建仓」（全局），信号=「这只票买不买」（局部）。
   改法：① 闸门改用**专属三色**（琥珀=否决/青=放行/灰蓝=缺维），与信号色系隔离；
        ② 闸门容器加 .gatebar 整条底色，横幅化、视觉主权独立。 */
.gate.g-on{border-color:var(--color-gate-ok,#2f9e8f);
background:linear-gradient(0deg,rgba(47,158,143,.10),rgba(47,158,143,.10)),
var(--bg-raised,#161b22)}
.gate.g-off{border-color:var(--color-gate-bad,#c98a1e);
background:linear-gradient(0deg,rgba(201,138,30,.12),rgba(201,138,30,.12)),
var(--bg-raised,#161b22)}
.gate.g-na{border-color:var(--color-gate-na,#5b7a99);
background:linear-gradient(0deg,rgba(91,122,153,.10),rgba(91,122,153,.10)),
var(--bg-raised,#161b22)}
/* 闸门整条横幅：整行一条，不与下方个股卡片混读 */
.gatebar{border:1px solid var(--border,#30363d);border-radius:12px;
padding:13px 16px;margin:0 0 14px;background:var(--bg-surface,#0f141c)}
.gatebar .gb-title{font-size:15px;font-weight:600;margin:0 0 2px}
.gatebar .gb-note{font-size:11.5px;color:var(--text-dim,#8b949e);margin:0 0 10px;line-height:1.5}
.gatebar .gates{margin-top:0}
.gatebar.gb-on{border-color:var(--color-gate-ok,#2f9e8f)}
.gatebar.gb-off{border-color:var(--color-gate-bad,#c98a1e)}
.gatebar.gb-na{border-color:var(--color-gate-na,#5b7a99)}
/* 闸门卡片内的状态文字同步换色（不硬编码 hex 撞信号色） */
.gate .g-state{font-weight:700}
.gate.g-on .g-state{color:var(--color-gate-ok,#2f9e8f)}
.gate.g-off .g-state{color:var(--color-gate-bad,#c98a1e)}
.gate.g-na .g-state{color:var(--color-gate-na,#5b7a99)}
.tp{display:inline-block;margin:4px 6px 0 0;padding:7px 13px;
border:1px solid var(--border,#30363d);border-radius:16px;font-size:12.5px;
background:var(--bg-raised,#161b22)}
.doc{display:inline-block;margin:7px 12px 0 0;font-size:13.5px}
/* 可折叠区块 */
details.sec{padding:0;overflow:hidden}
details.sec>summary{cursor:pointer;padding:13px 17px;list-style:none;
display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;user-select:none}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::before{content:"\\25B6";font-size:10px;color:var(--text-dim,#8b949e);
transition:transform .15s;flex:none;margin-top:2px}
details.sec[open]>summary::before{transform:rotate(90deg)}
details.sec>summary h2{display:inline;font-size:15px}
details.sec>.sec-body{padding:0 17px 15px;border-top:1px solid var(--border-weak,#21262d)}
/* 双列网格 */
@media(min-width:960px){.duo{display:grid;grid-template-columns:1fr 1fr;
gap:14px;align-items:start}}
/* 吸顶表头 */
	table.sticky th{position:sticky;top:0;background:var(--bg-surface,#0f141c);z-index:1}
	/* 策略动态卡（B 区：注册表驱动，新增策略零改） */
	.sstat{display:block;border:1px solid var(--border,#30363d);border-radius:10px;
	padding:11px 13px;background:var(--bg-raised,#161b22);color:var(--text,#e6edf3)}
	.sstat:hover{border-color:var(--link,#58a6ff)}
	.sstat .st{font-size:12px;color:var(--text-dim,#8b949e);margin-bottom:3px}
	.sstat .sv{font-size:17px;font-weight:700;line-height:1.3}
	.sstat .su{font-size:11px;color:var(--text-dim,#8b949e);margin-top:4px}
	/* 中式红涨绿跌（2026-10-01 全站统一） */
	.up{color:var(--color-up,#f85149)}.dn{color:var(--color-dn,#3fb950)}.mu{color:var(--color-warn,#d29922)}
	/* 分组 pill 导航 */
	.navgrp{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
	.navgrp .gl{font-size:11px;color:var(--text-dim,#8b949e);padding:6px 0}
	/* 长页切割 Tab（U3）：一屏一任务 */
	.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-top:16px;
	position:sticky;top:0;background:var(--bg-page,#0d1117);z-index:5;padding:6px 0 0}
	.tabs button{background:var(--bg-raised,#161b22);border:1px solid var(--border,#30363d);
	border-radius:10px 10px 0 0;color:var(--text-dim,#8b949e);font-size:13px;
	padding:8px 16px;cursor:pointer;font-family:inherit}
	.tabs button:hover{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}
	.tabs button.on{background:var(--bg-surface,#0f141c);color:var(--text,#e6edf3);font-weight:600}
	.tabc{display:none}
	.tabc.on{display:block}
	/* U4-C 表头排序指示 */
	th.sortable{cursor:pointer;user-select:none}
	th.sortable:hover{color:var(--link,#58a6ff)}
	th[data-sc='a']:after{content:' \2191';font-size:10px;color:var(--link,#58a6ff)}
	th[data-sc='d']:after{content:' \2193';font-size:10px;color:var(--link,#58a6ff)}
	/* U4-D 窄屏表格横滚（桌面 overflow 保持默认，不破坏 sticky 吸顶） */
	.tblx{overflow-x:visible}
	@media(max-width:820px){.tblx{overflow-x:auto;
	-webkit-overflow-scrolling:touch}}
	/* U4-F 返回顶部 */
	#_topbtn{position:fixed;right:18px;bottom:18px;width:42px;height:42px;
	border-radius:50%;border:1px solid var(--border,#30363d);background:var(--bg-raised,#161b22);
	color:var(--text-dim,#8b949e);font-size:15px;cursor:pointer;display:none;z-index:99;
	box-shadow:0 2px 10px rgba(0,0,0,.4)}
	#_topbtn.show{display:block}
	#_topbtn:hover{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}
	/* K-STOCK-037 全站超级助手浮动图标（右下角，与回到顶部按钮上下错开） */
	#_hermesfab{position:fixed;right:18px;bottom:70px;width:46px;height:46px;
	border-radius:50%;background:var(--accent,#1f6feb);border:2px solid var(--link,#58a6ff);
	font-size:22px;line-height:42px;text-align:center;z-index:100;
	cursor:pointer;user-select:none;text-decoration:none;
	box-shadow:0 2px 12px rgba(31,111,235,.45)}
	#_hermesfab:hover{background:#388bfd}
	</style>
	<script>
	window._tab=function(btn,id){var w=btn.closest('.tabwrap');
	w.querySelectorAll('.tabs button').forEach(function(b){
	b.classList.toggle('on',b===btn);});
	w.querySelectorAll('.tabc').forEach(function(c){
	c.classList.toggle('on',c.id===id);});
	try{window.dispatchEvent(new Event('resize'));}catch(e){}
	/* U4-B：Tab 状态入 hash（replaceState 不污染后退栈，保留 G6 的 g=） */
	try{var h=(location.hash||'').replace(/^#/,'')
	.replace(/tab=[^&]*/g,'').replace(/&+/g,'&')
	.replace(/^&|&$/g,'');
	history.replaceState(null,'',h?'#'+h+'&tab='+id:'#tab='+id);
	}catch(e){}};
	window._tabTo=function(id){var c=document.getElementById(id);
	if(!c||c.classList.contains('on'))return;
	var w=c.closest('.tabwrap');if(!w)return;
	var btns=w.querySelectorAll('.tabs button');
	for(var i=0;i<btns.length;i++){var oc=btns[i].getAttribute('onclick')||'';
	if(oc.indexOf("'"+id+"'")>=0){btns[i].click();return;}}};
	/* U4 通用初始化：hash 恢复 Tab / 表头排序 / 表格横滚包裹 / 返回顶部 */
	window._u4init=function(){
	try{var m=/(?:^|&)tab=([^&]+)/.exec((location.hash||'').replace(/^#/,''));
	if(m&&window._tabTo)window._tabTo(decodeURIComponent(m[1]));}catch(e){}
	try{var ts=document.querySelectorAll('table');
	for(var i=0;i<ts.length;i++){var t=ts[i];
	if(t.parentNode.className==='tblx')continue;
	var w=document.createElement('div');w.className='tblx';
	t.parentNode.insertBefore(w,t);w.appendChild(t);}}catch(e){}
	try{var ts=document.querySelectorAll('table');
	for(var i=0;i<ts.length;i++){var t=ts[i],r0=t.rows[0];
	if(!r0||!r0.cells.length||r0.cells[0].tagName!=='TH')continue;
	var bad=0;
	for(var j=0;j<r0.cells.length;j++)
	if((r0.cells[j].colSpan||1)>1)bad=1;
	if(bad)continue;
	for(var j=0;j<r0.cells.length;j++){var th=r0.cells[j];
	th.className=(th.className?th.className+' ':'')+'sortable';
	th.title='点击排序';
	th.addEventListener('click',function(){
	var th=this,tb=th.closest('table');
	var ti=Array.prototype.indexOf.call(th.parentNode.cells,th);
	var asc=!(tb.getAttribute('data-sci')==String(ti)
	&&tb.getAttribute('data-sc')==='a');
	var rows=[];
	for(var r=1;r<tb.rows.length;r++)rows.push(tb.rows[r]);
	var val=function(row){
	var s=(row.cells[ti]?row.cells[ti].textContent:'').trim()
	.replace(/[,\uFF0C\\s%+\u2212\u2013\u2014-]/g,'');
	var u=/\u4EBF/.test(s)?1e8:(/\u4E07/.test(s)?1e4:1);
	s=s.replace(/\u4EBF|\u4E07/g,'');
	var n=parseFloat(s);
	return{n:isNaN(n)?null:n*u,s:s};};
	rows.sort(function(a,b){var x=val(a),y=val(b);
	if(x.n!==null&&y.n!==null)return asc?x.n-y.n:y.n-x.n;
	return asc?x.s.localeCompare(y.s,'zh')
	:y.s.localeCompare(x.s,'zh');});
	var host=tb.tBodies[0]||tb;
	for(var k=0;k<rows.length;k++)host.appendChild(rows[k]);
	tb.setAttribute('data-sci',ti);
	tb.setAttribute('data-sc',asc?'a':'d');
	var h0=tb.rows[0];
	for(var q=0;q<h0.cells.length;q++)
	h0.cells[q].removeAttribute('data-sc');
	th.setAttribute('data-sc',asc?'a':'d');});}
	}}catch(e){}
	try{if(!document.getElementById('_topbtn')){
	var b=document.createElement('button');
	b.id='_topbtn';b.type='button';b.title='返回顶部';
	b.appendChild(document.createTextNode('\u25B2'));
	b.onclick=function(){window.scrollTo({top:0,behavior:'smooth'});};
	document.body.appendChild(b);
	window.addEventListener('scroll',function(){
	b.className=(window.scrollY||document.documentElement.scrollTop)
	>600?'show':'';},{passive:true});}}catch(e){}
	/* K-STOCK-037 超级助手浮动图标：全站右下角常驻 */
	try{if(!document.getElementById('_hermesfab')){
	var f=document.createElement('a');
	f.id='_hermesfab';f.href='/hermes/';f.target='_blank';
	f.rel='noopener';f.title='超级助手：对话即调度全部 AI 岗位';
	f.appendChild(document.createTextNode('\U0001F9B8'));
	document.body.appendChild(f);}}catch(e){}};
	document.addEventListener('DOMContentLoaded',window._u4init);
	</script>"""






    # ── K-STOCK-035 总入口门户：哪里是什么、我的入口在哪里 ──
    # ── K-STOCK-113 开发指挥台：dev_events.jsonl 只读投影 ──────────
    _DEV_EVENTS = os.path.join(ROOT, "data", "dev_events.jsonl")
    _DEV_KINDS = ("task_start", "commit", "blocked", "merge_done", "guard_fail")

    # K-STOCK-181: 此处原有错位的 @classmethod（f0b9f9b/137f 切割残留，
    #   原属被搬走的 _audit_cfg(cls)）——classmethod 把类对象绑进 self 形参，
    #   方法体内全部 self._xxx(arg) 变成类直调 → missing xxx 系 TypeError，
    #   /portal 自 10-12 凌晨 137f 起裸断连。装饰器已移除，还原普通方法。





















    # ── K35 三线联动复盘（大盘-板块-个股，影子观察）──










    # 兼容多继承 Handler：返回键必须保持实例方法绑定。
























    def _back_link_html(self):
        """统一返回键（Handler 侧别名，委托模块级实现——个股页等
        既有调用点不改）。"""
        return _back_link_html()

    def _back_bar_html(self):
        """统一返回条（个股全景页等用）：_back_link_html 包容器。
        兜底已从 /bt 升级为 /portal（K-STOCK-035 门户架构）。"""
        return ("<div style='margin:0 0 10px'>" + self._back_link_html()
                + "</div>")











    _KG_CSS = """<style>
.tl{position:relative;margin:12px 0 0;padding-left:20px}
.tl::before{content:'';position:absolute;left:5px;top:6px;bottom:6px;
width:2px;background:#30363d}
.ti{position:relative;margin:0 0 13px}
.ti::before{content:'';position:absolute;left:-19px;top:7px;width:8px;
height:8px;border-radius:50%;background:#58a6ff}
.bdg{display:inline-block;padding:1.5px 9px;border-radius:10px;
font-size:11px;font-weight:600;margin-right:6px}
.bdg.g{background:#2d1416;color:#f85149;border:1px solid #5a2326}
.bdg.b{background:#0d1d33;color:#58a6ff;border:1px solid #1f3a5f}
.bdg.r{background:#12261e;color:#3fb950;border:1px solid #1f4d2b}
.bdg.e{background:#2d1416;color:#f85149;border:1px solid #5a2326}
.bdg.y{background:#2a2111;color:#d29922;border:1px solid #4d3a13}
.bdg.n{background:#161b22;color:#8b949e;border:1px solid #30363d}
.hb{height:10px;border-radius:5px;background:#1f6feb;display:inline-block;
vertical-align:middle}
.src{border-left:2px solid #30363d;margin:4px 0 0 10px;padding:2px 0 2px 12px;
font-size:12px;color:#8b949e}
.kfind{padding:8px 12px;background:#161b22;border:1px solid #30363d;
border-radius:8px;color:#e6edf3;font-size:13.5px;width:250px}
.kfbtn{padding:8px 14px;background:#1f6feb;border:none;border-radius:8px;
color:#fff;font-size:13.5px;cursor:pointer}
</style>"""




























    # ---------------- K-STOCK-137d S-b2 经理实体接线（纯增量，复刻 trainee 模式） ----------------





    # ---------------- K-STOCK-159 S-e 接待式迎宾向导（纯增量） ----------------

    _OB_GREETING_FB = (
        "欢迎光临 K-STOCK 投研交易所！我是值班经理。回答我四个问题，"
        "帮你把服务配置到位：免费还是收费、自动还是手动、"
        "要不要每日盘前汇报与盘后总结、想请哪位资历经理。")






    # WO-OS-09：/api/zhuge/brief 只读端点的非 GET 方法统一 405（BaseClass
    # 默认 501，语义不准；POST 见 do_POST 顶部同款 405 块）
    def _zhuge_brief_method_not_allowed(self):
        self._json({"error": "method not allowed: 本端点只读，仅支持 GET"},
                   405)

    def do_OPTIONS(self):
        if self.path.split("?", 1)[0] == '/api/zhuge/brief':
            self._zhuge_brief_method_not_allowed()
            return
        self.send_error(501)

    def do_PUT(self):
        if self.path.split("?", 1)[0] == '/api/zhuge/brief':
            self._zhuge_brief_method_not_allowed()
            return
        self.send_error(501)

    def do_DELETE(self):
        if self.path.split("?", 1)[0] == '/api/zhuge/brief':
            self._zhuge_brief_method_not_allowed()
            return
        self.send_error(501)

    def do_POST(self):
        """v2.5：标注保存（首个 POST 端点，其余页面均为只读 GET）。"""
        path = self.path.split("?", 1)[0]
        # H1（2026-11-03）：个股分析内部网关（Hermes MCP 专用；loopback + 专属 token，
        # 模块内 fail-closed 鉴权；不走 X-Portal-Token/session，保持工具面独立）。
        if path.startswith('/api/analysis/internal/'):
            import api_analysis_internal
            api_analysis_internal.handle_post(
                self, path, self.path.split("?", 1)[1] if "?" in self.path else "")
            return
        # P142：早分支变更端点先过 token 闸门（本机直连/页面自动带 token；
        # 反代+局域网须 X-Portal-Token）。403 语义与 :1042 闸门保持一致。
        if path.startswith(_EARLY_TOKEN_PREFIXES) and not self._check_portal_token():
            import ifs_envelope  # IFS G2：403 统一 error/v1（WO-IFS-IDEMP-20261104）
            self._json(ifs_envelope.auth_error(
                self, "POST",
                "forbidden: 缺失或不匹配的 X-Portal-Token"
                "（该变更端点需 token；本机页面自动携带，脚本请带"
                " header X-Portal-Token）"), 403)
            return
        if path.startswith('/api/factor/'):
            # P1 因子服务计算型入口（token 闸门之后；worker 子进程 + 缓存）。
            import api_factor
            api_factor.handle_post(self, path)
            return
        if path == '/api/zhuge/review' or path.startswith('/api/zhuge/review/'):
            # P0/运营化：只读复核 + <id>/adoption 子资源（模块内校验 feature flag/
            # 门户 session/正文大小/限额；默认关）
            api_zhuge_review.handle_post(self, path)
            return
        # P1 移动端 /m 家族（2026-10-25）：POST 早分支（m_api.py 独立文件）。
        if path.startswith('/api/m/'):
            import m_api
            m_api.handle_post(self, path)
            return
        if path.startswith('/api/auth/'):
            # Phase A auth-gateway：登录/登出；不要求 X-Portal-Token（登录即认证）。
            import auth_gateway
            auth_gateway.handle_post(self, path)
            return

        if path.startswith('/api/dw/'):
            # S3-06：数据仓统一查询 POST（只读；body 由分派读取后传入模块，
            # 修复 2026-11-02 11-05 runbook 发现的 2 参/3 参不匹配 TypeError）
            _ok, _raw, _err = _read_dw_body(self)
            if not _ok:
                self._json({"ok": False, "error": _err,
                            "reason": "bad_param: POST body 超过 %d 字节上限" % _DW_BODY_MAX},
                           413)
                return
            import api_dw_registry
            api_dw_registry.handle_post(self, path, _raw)
            return
        if path.startswith('/api/os/'):
            self._os_goal_post(path)
            return
        if path.startswith('/api/jev/review/'):
            # 2026-10-30 K-JEV-20261030-QUEUEUI：低置信复核裁决写回
            import api_jev_access, api_jev_review
            api_jev_access.wrap_dispatch(
                self, path, "", "POST", api_jev_review.handle_post, path)
            return
        if path.startswith('/api/customer/'):
            import api_customer
            api_customer.handle_post(self, path)
            return
        if path == '/api/zhuge/brief':
            # WO-OS-09 只读出口：非 GET 显式 405（不落 404 兜底，契约清晰）
            self._zhuge_brief_method_not_allowed()
            return
        # P0-1b：变更类 POST 鉴权闸门（本机放行；局域网须 X-Portal-Token）。
        # /feishu/event 不在 _MUTATION_POSTS，单独走验签（_api_feishu_event）。
        if path in _MUTATION_POSTS and not self._check_portal_token():
            import ifs_envelope  # IFS G2：403 统一 error/v1（WO-IFS-IDEMP-20261104）
            self._json(ifs_envelope.auth_error(
                self, "POST",
                "forbidden: 缺失或不匹配的 X-Portal-Token"
                "（本机浏览器经门户页面操作会自动携带；脚本调用请带"
                " header X-Portal-Token，token 见 PORTAL_API_TOKEN"
                " 或 C:\\Temp\\portal_api_token）"), 403)
            return
        # K-STOCK-185b：写端点频控（鉴权之后、业务之前），超限 429。
        if path in _MUTATION_POSTS:
            _rip = (self.headers.get("X-Real-IP") or "").strip()
            _addr = getattr(self, "client_address", None)  # 探针 stub 可能缺省
            _ck = _rip or (_addr[0] if _addr else "?")
            if _rate_hit(("write", _ck), _WRITE_MAX_PER_MIN):
                self._json({"error": "too many requests: 写端点每分钟限 "
                            f"{_WRITE_MAX_PER_MIN} 次"}, 429)
                return
        # WO-D1-DECISION-20261101：D1 final_decision 写入口（token/频控之后）
        if path == "/api/d1/decision":
            import api_d1_decision
            api_d1_decision.handle_post(self)
            return
        if path == "/api/d1/meeting":
            import api_d1_meeting
            api_d1_meeting.handle_post(self)
            return
        if path == "/api/d1/handoff":
            import api_d1_handoff
            api_d1_handoff.handle_post(self)
            return
        # H3（K-ORG-20261031-APIHARDEN）：analysis 写分支移到 token 闸门+频控之后，
        # 不得再以“早分支”绕过 _MUTATION_POSTS 鉴权（审计 v2 H3）。
        if path.startswith('/api/analysis/'):
            import api_analysis
            api_analysis.handle_post(
                self, path,
                self.path.split("?", 1)[1] if "?" in self.path else "")
            return
        if path.startswith('/api/monitor/'):
            import api_monitor
            api_monitor.handle_post(
                self, path, self.path.split("?", 1)[1] if "?" in self.path else "")
            return
        try:
            if path == "/api/sim/confirm":
                # IFS F4 唯一 A 路径：确认 v2（四 ID 链），执行仍走 trading_bot_flow
                import api_sim_confirm
                api_sim_confirm.handle_post(self)
                return
            if path == "/chanlun-anno":
                # K77 缠论图手工标注增删（body 已在 _chanlun_anno 读）
                self._chanlun_anno(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
                return
            ln = int(self.headers.get("Content-Length") or 0)
            if path == "/api/zhuge/adoption":
                # C3（2026-10-27）：采纳率只追加路由（独立文件 api_zhuge_adoption.py）。
                # sendBeacon 不能带自定义头→沿用模块「匿名但只写专用账本」窄写设计；
                # 正文长度/上限口径对齐既有写端点（负数 400、超 64KiB 413）。
                if ln < 0:
                    self._json({"error": "正文长度不合法"}, 400)
                    return
                if ln > 65536:
                    self._json({"error": "请求正文超过64KiB"}, 413)
                    return
                try:
                    _st, _msg = api_zhuge_adoption.handle_post(
                        self.rfile.read(ln) if ln else b"")
                except Exception:
                    log_exc("zhuge-adoption")
                    _st, _msg = 500, "internal: 采纳率账本写入失败"
                self._json({"ok": _st == 200, "message": _msg}, _st)
                return
            if path in {'/api/data/update-plan','/api/data/update'}:
                if ln < 0:
                    self._json({'error':'正文长度不合法'},400)
                    return
                if ln > 65536:
                    self._json({'error':'请求正文超过64KiB'},413)
                    return
                raw = self.rfile.read(ln) if ln else b''
                self._warehouse_update_request('POST',path,raw=raw)
                return
            raw = self.rfile.read(ln) if ln > 0 else b""
            if path == "/api/audit_chat":
                # K-STOCK-114e 独立安全审计 AI 对话（独立模型绑定 AUDIT_MODEL）
                self._api_audit_chat(raw)
                return
            if path in ("/api/preview", "/api/register", "/api/project/preview",
                        "/api/project/register", "/api/agent/update",
                        "/api/agent/learn"):
                # K-ORG-190 Agent 指挥台写回链（org.html 两段式：预览→落盘）
                # 学习环 /api/agent/learn 属 P2（K-ORG-185 在途）——显式拒绝不假绿
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
                    return
                try:
                    if path == "/api/preview":
                        out = org_command_api.api_preview(body)
                    elif path == "/api/register":
                        out = org_command_api.api_register(body)
                    elif path == "/api/project/preview":
                        out = org_command_api.api_project_preview(body)
                    elif path == "/api/project/register":
                        out = org_command_api.api_project_register(body)
                    elif path == "/api/agent/update":
                        out = org_command_api.api_agent_update(body)
                    else:
                        out = org_command_api.api_agent_learn(body)
                except Exception:
                    log_exc("org-command " + path)
                    out = {"ok": False, "error": "org-command internal (see err log)"}
                self._json(out, 200 if out.get("ok") else 400)
                return
            if path == "/api/flow/rerun":
                # F1 动作流中心：白名单产线任务重跑（body: {"task": "..."}）
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    body = {}
                self._flow_rerun(body)
                return
            if path == "/api/ai_duty":
                # K-STOCK-352 AI 值班经理（paper 接待大厅 · 有问必答）：复用审计模型回退链
                self._api_ai_duty(raw)
                return
            if path == "/api/paper_order":
                # K-STOCK v0.2 模拟仓下单（manual BUY/SELL 或 double_knife 执行）
                self._api_paper_order(raw)
                return
            if path == "/api/paper_stop":
                # K-STOCK v0.2 模拟仓止损挂/解
                self._api_paper_stop(raw)
                return
            if path == "/api/manager_hire":
                # K-STOCK-137d S-b2 经理雇佣（子进程 manager.py hire --json）
                self._api_manager_hire(raw)
                return
            if path == "/api/manager_fire":
                # K-STOCK-137d S-b2 经理解雇（子进程 manager.py fire --json）
                self._api_manager_fire(raw)
                return
            if path == "/api/paper_direct_open":
                # K-STOCK-137f S-d 轻量直订动线：策略市场「直接建仓」
                self._api_paper_direct_open(raw)
                return
            if path == "/api/onboard":
                # K-STOCK-159 S-e 迎宾向导四问落账（建组合+雇佣链）
                self._api_onboard(raw)
                return
            if path == "/api/ctl/alert":
                # K-ORG-113 Hermes alerts 连接器写面（与飞书控制卡同源同落点）
                self._api_ctl_alert(raw)
                return
            if path == "/feishu/event":
                # K250 飞书事件回调（卡按钮点击 / url_verification 握手）
                self._api_feishu_event(raw)
                return
            if path == "/kg/doc/upload":
                # K266 M2 文档上传入口：body=文件字节，query name=文件名。
                # 后缀白名单 png/jpg/jpeg/pdf；落 inbox/docs/pending/
                # up_<时间戳>_<安全名>，由 k266_doc_ingest.py 统一摄取
                # （sha256 去重 → pypdf/OCR 文本化 → doc_texts.jsonl）。
                from urllib.parse import parse_qs as _pqu
                qs = _pqu(self.path.split("?", 1)[1]) \
                    if "?" in self.path else {}
                name = (qs.get("name") or [""])[0].strip()
                name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)[:80]
                if not name:
                    self._json({"error": "缺 name 参数"}, 400)
                    return
                if not name.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".pdf", ".txt", ".md")):
                    self._json({"error": "仅支持 png/jpg/jpeg/pdf/txt/md"}, 400)
                    return
                if not raw:
                    self._json({"error": "空文件"}, 400)
                    return
                pend = os.path.join(STATION, "inbox", "docs", "pending")
                os.makedirs(pend, exist_ok=True)
                fn = "up_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), name)
                with open(os.path.join(pend, fn), "wb") as f:
                    f.write(raw)
                self._json({"ok": True, "saved": fn, "size": len(raw)})
                return
            if path == "/kg/lexicon/save":
                # K263-W7 词表保存：三组可编辑（panic/hope/word_alias），
                # stop/tag_words 保持现值（页面只读）；原子写 lexicon.json，
                # guba_sentiment mtime 懒加载热生效
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import guba_sentiment as _gs
                _gs._apply_lexicon(force=True)
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                panic = sorted({str(w).strip() for w in rec.get("panic", [])
                                if str(w).strip()})
                hope = sorted({str(w).strip() for w in rec.get("hope", [])
                               if str(w).strip()})
                # stop 可选：不传保持现值；传了则全量替换（空表拒绝）
                stop = sorted({str(w).strip() for w in rec.get("stop", _gs.STOP)
                               if str(w).strip()})
                alias = {}
                for k, v in (rec.get("word_alias") or {}).items():
                    k, v = str(k).strip(), str(v).strip()
                    if k and v and k != v:
                        alias[k] = v
                if not panic or not hope or not stop:
                    self._json({"error": "情绪词/停用词表不可为空"}, 400)
                    return
                out = {"stop": stop, "panic": panic, "hope": hope,
                       "tag_words": _gs.TAG_WORDS, "word_alias": alias}
                fp = _gs._LEX_FP
                tmp = fp + ".tmp"
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=1)
                os.replace(tmp, fp)
                self._json({"ok": True, "panic": len(panic),
                            "hope": len(hope), "alias": len(alias),
                            "stop": len(stop)})
                return
            if path == "/api/chanlun/label/save":
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_kg_label_save(rec))
            elif path == "/api/report_push":
                # K244 分析师报告一键推飞书（生成 + push_feishu）
                self._api_report_push(self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
            elif path == "/api/sub_toggle":
                # K245 订阅启用/停用
                self._api_sub_toggle(self.path.split("?", 1)[1]
                                     if "?" in self.path else "")
            elif path == "/api/sub_del":
                # K245 删除订阅
                self._api_sub_del(self.path.split("?", 1)[1]
                                  if "?" in self.path else "")
            elif path == "/api/sub_test":
                # K245 试推某订阅（force 忽略开关）
                self._api_sub_test(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
            elif path == "/api/recv_add":
                # K245 添加/更新接收人
                self._api_recv_add(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
            elif path == "/kg/canvas_edit":
                # K231 画布编辑器写入（op=save 建实体/改属性 | op=link 连边）
                # 白名单 + upsert 幂等 + canvas_edit_log.jsonl 审计在数据层
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import kg_portal
                if rec.get("op") == "save":
                    self._json(kg_portal.canvas_save(
                        rec.get("label"), rec.get("key"),
                        rec.get("props")))
                elif rec.get("op") == "link":
                    self._json(kg_portal.canvas_link(
                        rec.get("src"), rec.get("dst"),
                        rec.get("rel"), rec.get("props")))
                else:
                    self._json({"error": "未知 op"}, 400)
            elif path == "/api/chanlun/card/feedback":
                # K29 推演卡三键校对反馈（对/偏/错）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_kg_card_fb_save(rec))
            elif path == "/api/chanlun/pool/push":
                # 工作台手动推池：分析台检出信号 → 缠论信号池（source=manual）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_chanlun_pool_push(rec))
            elif path == "/api/portfolio_set":
                # K45 策略卡「模拟建仓」：capital/start_date 即时生效
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_set(rec))
            elif path == "/api/portfolio_undo":
                # K46 撤销上一笔配置变更（弹栈恢复）
                self._json(_portfolio_undo())
            elif path == "/api/portfolio_close":
                # K50-D 手动平仓（单只/清仓）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_close(rec))
            elif path == "/api/portfolio_adjust":
                # K51-A 手动加/减仓
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_adjust(rec))
            elif path == "/api/portfolio_open":
                # K51-D 手动建仓
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_open(rec))
            elif path == "/api/council/confirm":
                # K64d 投委会预警半自动执行：人工确认→平仓/减仓，忽略→出队
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_council_confirm(rec))
            elif path == "/api/council/simrun":
                # K69 模拟投委会预演：后台启动/轮询（start/status）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_council_simrun(rec))
            elif path == "/api/debate/say":
                # K74 老板插话：主持人+三操盘手 LLM 回应，追加留痕
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_debate_say(rec))
            elif path == "/api/portfolio_subscribe":
                # K-STOCK-122 M3 订阅/退订：{strategy, action}
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                try:
                    import subscriptions as _sub
                    self._json(_sub.subscribe(
                        str(rec.get("strategy") or ""),
                        str(rec.get("action") or "toggle")))
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/portfolio_hire":
                # K-STOCK-122 M4 分支①：组合聘交易员 {trader}
                # 真雇佣走 k_stock/paper/portfolio.py（hires.json 落
                # source=<pf_id> 条目 + live/training 校验）；再绑定组合侧。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                try:
                    import subprocess
                    # K-STOCK-150：接受 pf 参数（默认 pf_001 向后兼容），
                    # 仅接受订阅清单内组合，拒绝任意串参。
                    _pfid = str(rec.get("pf") or "pf_001")
                    if not _pf_valid(_pfid):
                        self._json({"ok": False,
                                    "error": "portfolio not found: %s"
                                             % _pfid}, 400)
                        return
                    _pfpy = (r"C:\Program Files\Python311\python.exe"
                             if os.path.exists(r"C:\Program Files"
                                               r"\Python311\python.exe")
                             else sys.executable)
                    _script = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "k_stock", "paper", "portfolio.py")
                    r = subprocess.run(
                        [_pfpy, "-X", "utf8", _script,
                         "hire", "--pf", _pfid,
                         "--trader", str(rec.get("trader") or ""), "--json"],
                        capture_output=True, timeout=60)
                    out = r.stdout.decode("utf-8", "replace").strip()
                    data = json.loads(out.splitlines()[-1]) if out else {}
                    if data.get("ok"):
                        import subscriptions as _sub
                        _sub.bind_trader(str(rec.get("trader") or ""),
                                         pid=_pfid)
                        self._json({"ok": True, "note": data.get("note"),
                                    "hired": True, "portfolio": _pfid})
                    else:
                        self._json({"ok": False,
                                    "error": data.get("error") or out[-200:]
                                    or "hire 失败"})
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/trader_fire":
                # K-STOCK-122 投研团队：组合解聘交易员 {trader}
                # 真解雇走 trainee.py fire（hires.json 行 status=ended）；
                # 组合侧解绑同步执行。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                _tid = str(rec.get("trader") or "")
                try:
                    import subprocess
                    # K-STOCK-150：接受 pf 参数（默认 pf_001 向后兼容），
                    # 仅接受订阅清单内组合。
                    _pfid = str(rec.get("pf") or "pf_001")
                    if not _pf_valid(_pfid):
                        self._json({"ok": False,
                                    "error": "portfolio not found: %s"
                                             % _pfid}, 400)
                        return
                    _pfpy = (r"C:\Program Files\Python311\python.exe"
                             if os.path.exists(r"C:\Program Files"
                                               r"\Python311\python.exe")
                             else sys.executable)
                    _script = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "k_stock", "paper", "trainee.py")
                    r = subprocess.run(
                        [_pfpy, "-X", "utf8", _script,
                         "fire", _tid, "--source", _pfid, "--json"],
                        capture_output=True, timeout=60)
                    out = r.stdout.decode("utf-8", "replace").strip()
                    data = json.loads(out.splitlines()[-1]) if out else {}
                    if data.get("ok"):
                        # K-STOCK-122 审查修复：仅当解聘的正是当前绑定
                        # 交易员时才解绑——此前无条件 unbind，解聘旁观者
                        # （如 taishan）会误清 chaser 的组合绑定。
                        # K-STOCK-150：解绑判定改按目标组合 _pfid。
                        import subscriptions as _sub
                        _pf_now = {}
                        for _p in (_sub.portfolios().get("portfolios") or []):
                            if _p.get("id") == _pfid:
                                _pf_now = _p
                                break
                        if (_pf_now.get("trader") or "") == _tid:
                            _sub.unbind_trader(pid=_pfid)
                        self._json({"ok": True, "fired": _tid,
                                    "portfolio": _pfid})
                    else:
                        self._json({"ok": False,
                                    "error": data.get("error") or out[-200:]
                                    or "fire 失败"})
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/combo_backtest":
                # K-STOCK-275 组合回测任务登记：校验 → 落盘排队（cbt_jobs.jsonl）。
                # 开闸（10-16）前不跑批，如实返回 queued；引擎侧经
                # cbt_queue_state.json 覆盖进度，本路由不改。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"ok": False, "error": "bad json"}, 400)
                    return
                self._json(_cbt_start(rec))
            elif path == "/api/bt2_run":
                # K-STOCK-285b 回测框架 v2：参数化回测（缠论等机会/量子双刀日内纪律），
                # daemon 线程跑 backtest_engine_v2，进度落盘 state json。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"ok": False, "error": "bad json"}, 400)
                    return
                self._json(_bt2_start(rec))
            elif path == "/api/bt2_nl":
                # K-STOCK-299 自然语言回测：nl_backtest 双轨解析（规则+本地 LLM 兜底），
                # 只解析回填表单不执行——执行仍走 /api/bt2_run 双重校验。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"ok": False, "error": "bad json"}, 400)
                    return
                self._json(_bt2_nl(rec))
            elif path == "/api/my_strategies":
                # K-STOCK-357 我的策略：POST save/delete（_MUTATION_POSTS 守卫已覆盖）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"ok": False, "error": "bad json"}, 400)
                    return
                self._json(_api_my_strategies(rec))
            elif path == "/api/batch-backtest":
                # K255 勾选股票自定义批次回测：写批次 JSONL → 引擎子进程异步跑
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_bt_custom_start(rec))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)

    def do_GET(self):
        raw = self.path
        path = raw.split("?", 1)[0]
        qs = raw.split("?", 1)[1] if "?" in raw else ""
        if path.startswith('/api/factor/'):
            # P1 因子服务只读目录/健康 + 计算型 POST（独立文件 api_factor.py）。
            # GET 只读；POST 在 do_POST 的 token 闸门后分派。
            import api_factor
            api_factor.handle_get(self, path, qs)
            return
        if path.startswith('/api/data/catalog/'):
            # 数据打通 2026-11-05：catalog 258 表实时元数据/样本只读通道。
            # data 已在 nginx 公开族 B 白名单 → 无需改 nginx；8091 由 SYSTEM 托管，
            # 代码落地后待宿主自然重启生效（WO-OS-09 判例），本轮不重启。
            import api_catalog_data
            api_catalog_data.handle_get(self, path, qs)
            return
        if path.startswith("/api/data/governance/"):
            # P0-E 2026-11-06：治理数据只读通道（SLA/上游健康度/lifecycle/源登记）。
            # data 已在 nginx 公开族 B 白名单 → 无需改 nginx；待 8091 自然重启生效（WO-OS-09）。
            from api_data_governance import handle_get as _gov_get
            _gov_get(self, path, qs)
            return
        if path == '/api/zhuge/review/status' or path.startswith('/api/zhuge/review/'):
            # P0：只读复核状态/结果（模块内校验 feature flag/session）
            api_zhuge_review.handle_get(self, path)
            return
        if path == '/api/dev/legion':
            # 2026-10-28 LS-03：研发军团作战屏只读 API（独立文件 api_dev_legion.py）
            import api_dev_legion
            api_dev_legion.handle_get(self, path, qs)
            return
        if path.startswith('/api/a2a/'):
            # W3 A2A Agent Card discovery（独立文件 api_a2a.py；8091 重启后生效）
            import api_a2a
            api_a2a.handle_get(self, path, qs)
            return
        if path.startswith('/api/hq/'):
            # HQ-06 只读指挥部 API（独立文件 api_hq.py，2026-11-03；8091 重启后生效）
            import api_hq
            api_hq.handle_get(self, path, qs)
            return
        if path == '/api/learning/conclusions':
            # W3-3b 学习结论只读 API（独立文件 api_learning_conclusions.py，2026-11-03；8091 重启后生效）
            import api_learning_conclusions
            api_learning_conclusions.handle_get(self, path, qs)
            return
        if path == '/api/learning/tool-intel':
            # W3-4 工具情报库只读 API（独立文件 api_learning_tool_intel.py，2026-11-04；8091 重启后生效）
            import api_learning_tool_intel
            api_learning_tool_intel.handle_get(self, path, qs)
            return
        # P1 移动端 /m 家族（2026-10-25）：新 handler 独立文件 m_api.py，
        # 本处仅做早分支路由（portal_api 只改不扩）。
        if path.startswith('/api/m/'):
            import m_api
            m_api.handle_get(self, path, qs)
            return
        # P2 外部分享短链 /s/<share_token>（2026-10-25）：校验后 302 到 /m 页面。
        if path.startswith('/s/'):
            import m_api
            m_api.handle_share(self, path)
            return
        if path.startswith('/api/os/'):
            self._os_goal_get(path, qs)
            return
        if path.startswith('/api/kb_corpus/'):
            # 语料知识库只读入口（独立文件 api_kb_corpus.py，2026-10-30）
            import api_kb_corpus
            api_kb_corpus.handle_get(self, path, qs)
            return
        if path.startswith('/api/dw/'):
            # 数据仓统一取数（WP-I3，独立文件 api_dw_registry.py，2026-11-02）
            import api_dw_registry
            api_dw_registry.handle_get(self, path, qs)
            return
        if path == '/api/kb/qa':
            # 知识库问答 HTTP（L2 批准 2026-11-01；独立文件 api_kb_qa.py，预接线已测试）
            import api_kb_qa
            api_kb_qa.handle_get(self, path, qs)
            return
        if path.startswith('/api/analysis/internal/'):
            # H1（2026-11-03）：个股分析内部只读网关（Hermes MCP；loopback + 专属 token）
            import api_analysis_internal
            api_analysis_internal.handle_get(self, path, qs)
            return
        if path.startswith('/api/monitor/'):
            # K-ORG-20261107-GUARDDECK：看护驾驶舱（独立文件 api_guard_deck.py，
            # 须在 api_monitor 的 503 兜底前分派——monitor 引擎缺失不连坐看护面）
            if path.startswith('/api/monitor/guard/'):
                import api_guard_deck
                api_guard_deck.handle_get(self, path, qs)
                return
            import api_monitor
            api_monitor.handle_get(self, path, qs)
            return
        if path.startswith('/api/analysis/'):
            # K-STOCK-ANALYSIS-P0C2 个股分析只读入口（独立文件 api_analysis.py）
            import api_analysis
            api_analysis.handle_get(self, path, qs)
            return
        if path.startswith('/api/commercial/'):
            # P2.1 商业化只读入口（套餐/租户/用量/审计/健康，独立文件 api_commercial.py）
            import api_commercial
            api_commercial.handle_get(self, path, qs)
            return
        if path.startswith('/api/customer/'):
            # P2.4 客户门户（登录/分析/报告下载/历史/配额，独立文件 api_customer.py）
            import api_customer
            api_customer.handle_get(self, path, qs)
            return
        if path == '/api/bff/quant/sso':
            import auth_gateway
            auth_gateway.handle_get(self, path)
            return
        if path == '/api/bff/identity/me' or path.startswith('/api/auth/'):
            # Phase A auth-gateway（2026-10-30）：登录态 / 登出 / 当前 SessionContext。
            # 与只读 BFF 分离；认证端点单独模块，不混入 /api/bff/ 只读面。
            import auth_gateway
            auth_gateway.handle_get(self, path)
            return
        if path.startswith('/api/bff/'):
            # 三系统只读 BFF（2026-10-30）：智策健康 + 菜鸟只读统计 + 契约入口。
            # 只挂 GET；写方法由 do_POST/do_OPTIONS 统一处理，不在此开写口。
            import api_zhice_bff
            api_zhice_bff.handle_get(self, path, qs)
            return
        if path.startswith('/api/jev/review/'):
            # 2026-10-30 K-JEV-20261030-QUEUEUI：低置信复核只读列表/汇总
            import api_jev_access, api_jev_review
            api_jev_access.wrap_dispatch(
                self, path, qs, "GET", api_jev_review.handle_get, path, qs)
            return
        if path == '/api/jev/platform':
            # 2026-11-07 情报中台复盘建设：中台健康聚合（哨兵四查/队列/吞吐/
            # 夜批/runs 时间线）；独立文件 api_jev_platform.py；只读 mode=ro；
            # 须在 /api/jev/ 兜底前。
            import api_jev_access, api_jev_platform
            api_jev_access.wrap_dispatch(
                self, path, qs, "GET", api_jev_platform.handle_get, path, qs)
            return
        if path == '/api/jev/wordcloud':
            # 2026-11-07 WO-INTEL-WORDC1-JEV-20261107-001：JEV 判定加权词云
            # （独立文件 api_jev_wordcloud.py；只读 mode=ro；须在 /api/jev/ 兜底前）
            import api_jev_access, api_jev_wordcloud
            api_jev_access.wrap_dispatch(
                self, path, qs, "GET", api_jev_wordcloud.handle_get, path, qs)
            return
        if path.startswith('/api/jev/'):
            # 2026-10-26 JEVAPI：JEV 判定只读查询（独立文件 api_jev.py；Hermes MCP 消费）
            # 标的三面聚合（2026-11-07 目录卡 jev_target_drill 聚合形态）
            if path.startswith('/api/jev/target/'):
                import api_jev_access, api_jev_target
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_target.handle_get, path, qs)
                return
            # AI 解读结论（2026-11-07：老田令——数据要出结论告知人）
            if path == '/api/jev/interpret':
                import api_jev_access, api_jev_interpret
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_interpret.handle_get, path, qs)
                return
            # 日内 5 分钟回溯（2026-11-07：老田令盘中反应量化）
            if path.startswith('/api/jev/intraday/'):
                import api_jev_access, api_jev_intraday
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_intraday.handle_get, path, qs)
                return
            # 个股叙事线（2026-11-07：判定串联+信源战绩+变化验证）
            if path.startswith('/api/jev/story/'):
                import api_jev_access, api_jev_story
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_story.handle_get, path, qs)
                return
            # 消息举证链（2026-11-07：老田令举证级溯源）
            if path == '/api/jev/evidence':
                import api_jev_access, api_jev_evidence
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_evidence.handle_get, path, qs)
                return
            # 能力开放层端点（2026-11-07）：目录 + 溯源（先于兜底）
            if path == '/api/jev/capabilities':
                import api_jev_access
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET",
                    api_jev_access.handle_capabilities, path, qs)
                return
            if path == '/api/jev/trace':
                import api_jev_access
                api_jev_access.wrap_dispatch(
                    self, path, qs, "GET", api_jev_access.handle_trace, path, qs)
                return
            import api_jev_access, api_jev
            api_jev_access.wrap_dispatch(
                self, path, qs, "GET", api_jev.handle_get, path, qs)
            return
        if path == '/api/intel/evidence-targets':
            self._intel_evidence_targets(qs)
            return
        if path == '/api/intel/bus':
            # 情报总线唯一出口（六域→总线→量化/飞书双路径，只读 2026-10-19）
            self._intel_bus(qs)
            return
        if path == '/api/notify/queue':
            # K-ORG-201 通知指挥四态队列（三本账只读聚合，零写入 2026-10-19）
            self.notify_queue(qs)
            return
        if path == '/api/zhuge/brief':
            # WO-OS-09 诸葛亮建议引擎只读出口（六源聚合→规则草案，零写操作
            # 2026-10-21；非 GET 由 do_POST/do_OPTIONS 统一 405）
            self._zhuge_brief(qs)
            return
        if path == '/api/news/intel':
            # K-ORG-207 新闻情报台三栏聚合出口（速报/政策/舆情，只读零参数）
            self._news_intel(qs)
            return
        if path == '/api/kg/lineage':
            # 数据血缘只读端点（情报板块 /lineage.html 数据面）
            self._lineage(qs)
            return
        if path == '/api/perf/board':
            # M4-P5 投研绩效看板（perf_ledger 只读投影）
            self._perf_board(qs)
            return
        if path == '/api/perf/replay':
            # M4-P5 四环全链回放（影子→Paper→成交→绩效）
            self._perf_replay(qs)
            return
        if path == '/api/xq/intel':
            # 两线预案 M-RT3 雪球情报日报出口（automation_runs 只读投影）
            self._xq_intel(qs)
            return
        if path == '/api/xq/intel/dates':
            # 近 14 日有产物日报日期导航（门户日期快捷 chips 消费，只读零参数）
            self._xq_intel_dates(qs)
            return
        if path == '/api/xq/intel/ticker':
            # 雪球情报标的下钻（近 N 日日报按 code 纯文件聚合，只读 2026-10-18）
            self._xq_intel_ticker(qs)
            return
        if path == '/api/xq/intel/coverage':
            # 分类覆盖进度卡（coverage_snapshot.json 纯文件读，只读零参数 2026-10-18）
            self._xq_intel_coverage(qs)
            return
        if path == '/api/daily/data':
            # K-ORG-183 多维数据日报聚合出口（五节独立 fail-visible，零参数 2026-10-18）
            self._daily_data(qs)
            return
        if path == '/api/guojia/data':
            # K-ORG-191 郭嘉预判对账司出口（summary 快照投影，零参数 2026-10-18）
            self._guojia_data(qs)
            return
        if path == '/api/guojia/drill':
            # K-ORG-193 郭嘉智能下钻（排行/个股/板块三模式，账本+parquet 纯文件只读）
            self._guojia_drill(qs)
            return
        if path == '/api/dataops/overview':
            # 数据运营指挥台（采集动线+数据池+管道调度聚合，只读）
            self._dataops(qs)
            return
        if path == '/api/dataops/map':
            # v3 指挥操作系统：全局拓扑（节点+边+实时健康）
            self._dataops_map()
            return
        if path == '/api/dataops/entity':
            # v3 指挥操作系统：单实体统一下钻 schema
            self._dataops_entity(qs)
            return
        if path == '/api/capability/board':
            # M3-P5 能力看板（产物登记仓+审批注册表只读聚合）
            self._capability_board()
            return
        if path == '/api/platform/observe':
            # M5 刀4 观察季看板（影子账本/巡检/eval/perf/日报 只读聚合）
            self._platform_observe(qs)
            return
        if path == '/api/execution/health':
            # R1 执行健康总线（Windows 计划任务只读投影，联动层一期 2026-10-18）
            self._execution_health(qs)
            return
        if path == '/api/huangzhong/events':
            # 黄忠验收事件只读投影（WO-HZ-HERMES-PHASE2-20261031，Hermes Tile 数据面）
            self._huangzhong_events(qs)
            return
        if path == '/api/org/tasks':
            # 工单只读投影（驾驶舱一期复盘舱数据面，2026-10-18）
            self._org_tasks(qs)
            return
        if path == '/api/evolution/summary':
            # 进化舱只读投影（告警闭环率+影子统计+eval 占位，2026-10-18）
            try:
                self._json(api_evolution.load_evolution())
            except Exception as e:  # fail-visible：恒 200 携错误体
                self._json({"ok": False, "available": False,
                            "errors": [repr(e)]})
            return
        if path == '/api/gate/checkpoint':
            # 周一门判只读投影（驾驶舱二期复盘舱数据面，2026-10-18）
            try:
                self._json(api_gate.load_gate())
            except Exception as e:  # fail-visible：恒 200 携错误体
                self._json({"ok": False, "available": False,
                            "errors": [repr(e)]})
            return
        if path == '/api/brief/latest':
            # 复盘简报最新一条（联动层二期 R5 复盘回流，2026-10-18）
            try:
                self._json(api_brief.load_brief_latest())
            except Exception as e:  # fail-visible：恒 200 携错误体
                self._json({"ok": False, "available": False,
                            "errors": [repr(e)]})
            return
        if path == '/api/brief/list':
            # 复盘简报历史列表（append-only 可回看，2026-10-18）
            try:
                _lp = dict(p.split('=', 1) for p in qs.split('&') if '=' in p)
                try:
                    _lim = min(int(_lp.get('limit', '20')), 50)
                except ValueError:
                    _lim = 20
                self._json(api_brief.load_brief_list(limit=max(_lim, 1)))
            except Exception as e:  # fail-visible：恒 200 携错误体
                self._json({"ok": False, "items": [],
                            "errors": [repr(e)]})
            return
        if path in ('/api/platform/approvals', '/api/platform/replay',
                    '/api/platform/trace'):
            # M1/M2 平台读面（审批注册表/统一投影/回放/证据/知识/
            # Skill/使用账本——api_unified/api_replay/api_approvals 各 mixin）
            if path == '/api/platform/approvals':
                self._platform_approvals(qs)
            elif path == '/api/platform/replay':
                self._platform_replay(qs)
            else:
                self._platform_replay(qs)
            return
        if path in ('/api/unified/evidence', '/api/unified/knowledge',
                    '/api/unified/skills', '/api/unified/usage',
                    '/api/unified/replay'):
            # M1/M2 统一投影读侧四端点 + 跨账本串线回放
            if path == '/api/unified/replay':
                self._unified_replay(qs)
            elif path == '/api/unified/evidence':
                self._unified_evidence(qs)
            elif path == '/api/unified/knowledge':
                self._unified_knowledge()
            elif path == '/api/unified/skills':
                self._unified_skills()
            else:
                self._unified_usage(qs)
            return
        if path == '/api/data/daily' or path.startswith('/api/data/runs/'):
            self._warehouse_update_request('GET',path,qs)
            return
        if path in ('/api/flow/stats', '/api/flow/events', '/api/flow/artifact'):
            # F1 动作流中心：统计/事件流/产物预览（全部只读聚合）
            if path == '/api/flow/stats':
                self._flow_stats(qs)
            elif path == '/api/flow/events':
                self._flow_events(qs)
            else:
                self._flow_artifact(qs)
            return
        # C1a：GET 动作端点鉴权闸门（与写端点同规则）。本机（回环直连/
        # 本机浏览器）放行；反代+局域网须 X-Portal-Token，缺失/不匹配
        # 返回 403，防止匿名 GET 触发后台子进程与飞书推送。
        if path in _ACTION_GETS and not self._check_portal_token():
            import ifs_envelope  # IFS G2：403 统一 error/v1（WO-IFS-IDEMP-20261104）
            self._json(ifs_envelope.auth_error(
                self, "GET",
                "forbidden: 缺失或不匹配的 X-Portal-Token"
                "（本机浏览器经门户页面操作直接放行；脚本调用请带"
                " header X-Portal-Token，token 见 PORTAL_API_TOKEN"
                " 或 C:\\Temp\\portal_api_token）"), 403)
            return
        if path == "/":
            # 根路径引导：8091 是 API+页面后端，首页在 nginx 8080——
            # 直接访问 8091/ 曾得 404 白页误判「网络不通」，302 到真首页
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8080/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/api/overview":
            with _lock:
                now = time.time()
                if _cache["data"] is None or now - _cache["t"] > CACHE_TTL:
                    _cache["data"] = build_overview()
                    _cache["t"] = now
                data = _cache["data"]
            self._json(data)
        elif path == "/health":
            self._json({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        elif path == "/api/watch-tower/strategy-pool/membership":
            # Independent read-only projection; no trusted production source is
            # configured, and offline receipts must not become live membership.
            from strategy_pool_projection import ProjectionQueryError, project_membership
            try:
                self._json(project_membership(qs))
            except ProjectionQueryError as exc:
                self._json({"error": "invalid_strategy_pool_query", "message": str(exc)}, 400)
        elif path in ("/api/watch-tower/snapshot", "/api/watchtower/overview"):
            # Watch Tower snapshot v1; compatibility alias remains read-only.
            # replay_at is deliberately parsed here so malformed user input is
            # a stable 400 rather than an internal server error.
            from watch_tower_snapshot import ReplayArgumentError, parse_replay_at
            replay_value = _replay_query_value(qs)
            try:
                replay_at = parse_replay_at(replay_value)
                snapshot = _WATCH_TOWER_SNAPSHOT.snapshot(replay_at=replay_at)
                replay_partial = bool(snapshot.get("replay") and any(
                    item.get("error_code") for item in snapshot.get("sources", [])
                    if item.get("required")))
                self._json(snapshot, 206 if replay_partial else 200)
            except ReplayArgumentError as exc:
                self._json({"error": "invalid_replay_at", "message": str(exc)}, 400)
        elif path in route_table._GET_EXACT_1:
            getattr(self, route_table._GET_EXACT_1[path])()
        elif path == "/api/audit_status":
            # M6 最小接线（2026-10-08 定位拍板 C 混合态）：审计线状态面，
            # 只读、无鉴权、全体 unverified（拍板原文，鉴权/对账/调度缓行）。
            # 数据层 quant_backtest/ai_operator/portal_status.py（docs 台账投影，
            # CLI 可自测 python -m ai_operator.portal_status）。
            import sys as _sysa
            if QBT_DIR not in _sysa.path:
                _sysa.path.insert(0, QBT_DIR)
            try:
                from ai_operator.portal_status import status as _aud
                self._json(_aud())
            except Exception as _e:  # 数据层异常也如实暴露（fail loud）
                self._json({"ok": False, "verified": False,
                            "error": repr(_e)}, 500)
        elif path == "/api/ctl/alerts":
            # K-ORG-113 Hermes alerts 连接器只读面：OPEN 队列（控制卡口径，
            # 无时间窗）。复用 feishu_control.alert_state，与飞书卡同源。
            import feishu_control
            al, err = feishu_control.alert_state()
            if al is None:
                self._json({"ok": False, "error": "告警库不可达：%s" % err}, 502)
            else:
                self._json(dict({"ok": True}, **al))
        elif path == "/api/duty_brief":
            # K-STOCK-364 值班经理盘前简报（只读·复用当日上下文·零模型调用；
            # ?snapshot=1 由 9:15 出件任务调用：现算+原子落盘当日快照）
            self._api_duty_brief(qs)
        elif path in route_table._GET_EXACT_2:
            getattr(self, route_table._GET_EXACT_2[path])()
        elif path == "/api/minutes_inbox":
            # K-STOCK-146 S-c 纪要收件箱只读投影（纯只读直读
            # minutes_inbox.json；?mid= 可选按经理过滤，created_at 倒序≤50）
            self._api_minutes_inbox(qs)
        elif path == "/api/paper_status":
            # K-STOCK 模拟仓只读投影（2026-10-09 前端规划 P2）：
            # 数据层 k_stock/paper/portal_status.py（纯标准库 sqlite ro，零 pandas），
            # CLI 自测 python k_stock/paper/portal_status.py。
            import sys as _sysp
            _ksdir = api_portfolio._REPO_ROOT / "k_stock"
            _ksdir = str(_ksdir)
            if _ksdir not in _sysp.path:
                _sysp.path.insert(0, _ksdir)
            try:
                from paper.portal_status import status as _paper
                data = _paper()
                data["desk"] = self._paper_desk()   # v0.2 交易台动态块（AI/双刀最近产物）
                try:  # K-STOCK-045e G3：US 独立账本投影（USD 原币；失败降级不拖主投影）
                    from paper.portal_status import us_status as _us
                    data["us"] = _us()
                except Exception as _e2:
                    data["us"] = {"ok": False, "error": repr(_e2)}
                try:  # H 批：HK 独立账本投影（HKD 原币灰位；失败降级不拖主投影）
                    from paper.portal_status import hk_status as _hk
                    data["hk"] = _hk()
                except Exception as _e2b:
                    data["hk"] = {"ok": False, "error": repr(_e2b)}
                try:  # 077q：选票通道（pick_bridge）投影（10-16 实弹；失败降级）
                    from paper.portal_status import pick_status as _pick
                    data["pick"] = _pick()
                except Exception as _e3:
                    data["pick"] = {"ok": False, "error": repr(_e3)}
                try:  # 077w：端3 评判 Agent（judge）投影（失败降级）
                    from paper.portal_status import judge_status as _judge
                    data["judge"] = _judge()
                except Exception as _e4:
                    data["judge"] = {"ok": False, "error": repr(_e4)}
                try:  # 077w：端1 策略源影子路由建议（strategist，失败降级）
                    from paper.portal_status import strategist_status as _strat
                    data["strategist"] = _strat()
                except Exception as _e5:
                    data["strategist"] = {"ok": False, "error": repr(_e5)}
                try:  # 077w K-STOCK-101 M3：商业页面投影（tiers+traders 战绩）
                    from paper.portal_status import market_status as _mkt
                    data["market"] = _mkt()
                except Exception as _e6:
                    data["market"] = {"ok": False, "error": repr(_e6)}
                self._json(data)
            except Exception as _e:  # 数据层异常也如实暴露（fail loud）
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/plan_review":
            # K-STOCK-163 今日交易计划审批单只读投影：?pf_id=（缺省 pf_001），
            # 直读 k_stock/data/plan_review/<YYYYMMDD>_<pf>.json；无则 review=null。
            # 纯只读文件投影（零子进程/零写），不入 _MUTATION_POSTS/_ACTION_GETS 守卫。
            try:
                from urllib.parse import parse_qs as _pqs
                _q = _pqs(qs)
                _pf = (_q.get("pf_id") or ["pf_001"])[0].strip()
                # K-STOCK-184：参数校验失败属客户端错误，显式 400（原先抛
                # ValueError 落兜底 except 变 500，语义错误且污染监控）。
                if not re.match(r"^[A-Za-z0-9_]{1,40}$", _pf):
                    self._json({"ok": False,
                                "error": "pf_id 非法（仅字母数字下划线）"}, 400)
                    return
                _fp = os.path.join(api_portfolio._PAPER_DATA_DIR,
                                   "plan_review",
                                   time.strftime("%Y%m%d") + "_" + _pf + ".json")
                _rev = None
                if os.path.exists(_fp):
                    with open(_fp, encoding="utf-8") as _fh:
                        _rev = json.load(_fh)
                self._json({"ok": True, "pf_id": _pf, "review": _rev})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/paper_history":
            # K-STOCK v0.2 历史回看（只读）：?day=YYYY-MM-DD，缺省=最新快照日
            import sys as _sysp
            _ksdir = str(api_portfolio._REPO_ROOT / "k_stock")
            if _ksdir not in _sysp.path:
                _sysp.path.insert(0, _ksdir)
            try:
                from urllib.parse import parse_qs as _pqs
                _q = _pqs(qs)
                from paper.portal_status import history as _hist, snapshot_days as _days
                _days_l = _days()
                _day = (_q.get("day") or [""])[0].strip() \
                    or (_days_l[-1] if _days_l else "")
                self._json(_hist(_day))
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/combo_backtest_jobs":
            # K-STOCK-275 组合回测队列只读投影（任务清单/队列位置/引擎进度覆盖）
            self._json(_cbt_status())
        elif path == "/api/ops_today":
            # K-STOCK-281 每日运营真实状态（数据新鲜度/买卖点/执行状态；按数据日缓存幂等）
            import sys as _sys281
            _qbt_dir = str(api_portfolio._REPO_ROOT / "quant_backtest")
            if _qbt_dir not in _sys281.path:
                _sys281.path.insert(0, _qbt_dir)
            from ops_signal_today import compute as _ops_compute
            self._json(_ops_compute())
        elif path == "/api/bt2_jobs":
            # K-STOCK-285b 回测框架 v2 任务清单（进度/结果/错误合并投影）
            self._json(_bt2_status())
        elif path == "/api/my_strategies":
            # K-STOCK-357 我的策略列表（GET 只读；写走 POST+守卫）
            self._json(_api_my_strategies({"action": "list"}))
        elif path == "/api/bt2_job":
            # K-STOCK-285b 单任务全量账本（?id=，done 后可取）
            from urllib.parse import parse_qs as _pqs
            _q = {k: v[0] for k, v in _pqs(qs).items()} if qs else {}
            self._json(_bt2_result(_q.get("id")))
        elif path == "/api/bt_daily_ledger":
            # K-STOCK-277 逐日回测账本只读投影（回测中心 bt_view.html 数据源）
            try:
                _led_dir = os.path.join(STATION, "quant_backtest",
                                        "data", "daily_ledger")
                _files = sorted(glob.glob(os.path.join(_led_dir,
                                                       "ledger_*.json")))
                if not _files:
                    self._json({"ok": False,
                                "error": "no ledger yet (run chanlun_daily_ledger.py)"}, 404)
                    return
                _latest = _files[-1]
                with open(_latest, encoding="utf-8") as _f:
                    _payload = json.load(_f)
                _payload["ok"] = True
                _payload["source_file"] = os.path.basename(_latest)
                self._json(_payload)
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/strategy_market":
            # K-STOCK-122 M1 策略市场：12 策略卡全量（strategies.json 直读）
            try:
                import strategy_market as _sm
                self._json(_sm.market())
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/market_performance":
            # K-STOCK-132：绩效账本只读投影；缺失/损坏明确返回，不降级成空数据。
            try:
                import market_performance as _mp
                self._json(_mp.project())
            except FileNotFoundError as _e:
                self._json({"ok": False, "error_code": "ledger_unavailable",
                            "error": str(_e)}, 503)
            except ValueError as _e:
                self._json({"ok": False, "error_code": "ledger_invalid",
                            "error": str(_e)}, 500)
            except Exception as _e:
                self._json({"ok": False, "error_code": "ledger_error",
                            "error": repr(_e)}, 500)
        elif path == "/api/portfolios":
            # K-STOCK-122 M3 组合列表（订阅集，纯只读）
            try:
                import subscriptions as _sub
                self._json(_sub.portfolios())
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/portfolio_status":
            # K-STOCK-122 模拟仓侧「我的组合」投影（只读）：
            # 组合策略集 + 在职交易员 + 其 pending/持仓 + 最新净值
            # K-STOCK-150 多组合化：枚举订阅清单全部组合，逐组合投影
            # （traders/pending/positions/equity）。
            # K-STOCK-166 清债第二批：顶层 portfolio/traders/pending/
            # positions/equity deprecated 兼容字段删除，响应收敛为
            # {ok, portfolios}（D1 已删旧面板消费方，paper.html 只读数组）。
            try:
                import json as _j2
                import sqlite3 as _sq2
                subs_p = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "data", "strategy_subscriptions.json")
                hires_p = os.path.join(
                    api_portfolio._PAPER_DIR, "hires.json")
                _all_pfs = []
                if os.path.exists(subs_p):
                    with open(subs_p, encoding="utf-8") as _f:
                        _all_pfs = (_j2.load(_f).get("portfolios") or [])
                _hires = []
                if os.path.exists(hires_p):
                    with open(hires_p, encoding="utf-8") as _f:
                        _hires = (_j2.load(_f).get("hires") or [])
                con = _sq2.connect(
                    f"file:{api_portfolio._PAPER_DB}?mode=ro", uri=True)
                con.row_factory = _sq2.Row
                pf_rows = []
                try:
                    for _p in _all_pfs:
                        _pid = _p.get("id")
                        traders = [h.get("trader") for h in _hires
                                   if h.get("source") == _pid
                                   and h.get("status") == "active"]
                        pend, poss, equities = [], [], []
                        for t in traders:
                            pend += [dict(r) for r in con.execute(
                                "SELECT stock,side,qty,plan_date,status,"
                                "ref FROM orders WHERE account_id=? AND "
                                "ref LIKE ? AND status='pending' "
                                "ORDER BY id DESC LIMIT 20",
                                (t, "poc_%s%%" % _pid))]
                            poss += [dict(r) for r in con.execute(
                                "SELECT stock,qty,avg_cost FROM positions "
                                "WHERE account_id=?", (t,))]
                            r = con.execute(
                                "SELECT date,total FROM equity_marks "
                                "WHERE account_id=? ORDER BY date DESC "
                                "LIMIT 1", (t,)).fetchone()
                            if r:
                                equities.append({"account": t, "date": r["date"],
                                                 "total": r["total"]})
                        pf_rows.append({
                            "id": _pid, "name": _p.get("name"),
                            "strategies": _p.get("strategies") or [],
                            "trader": _p.get("trader"),
                            "note": _p.get("note"),
                            "traders": traders, "pending": pend,
                            "positions": poss, "equities": equities,
                            "equity": ({"date": max(x["date"] for x in equities),
                                        "total": sum(x["total"] for x in equities)}
                                       if equities else None)})
                finally:
                    con.close()
                self._json({"ok": True, "portfolios": pf_rows})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/trader_roster":
            # K-STOCK-122 投研团队：操盘手团队名册（trainee.py board 成绩
            # + hires.json 在职标记 + 经理等级标签），纯只读。
            try:
                import json as _j3
                hires_p = os.path.join(
                    api_portfolio._PAPER_DIR, "hires.json")
                r = self._paper_run_json(
                    [self._PAPER_PY,
                     os.path.join(self._PAPER_DIR, "trainee.py"),
                     "board", "--json"], timeout=90)
                active = set()
                if os.path.exists(hires_p):
                    # K-STOCK-150：在职标记按全部组合 id 集合过滤，
                    # 不再钉死 pf_001（pf_002 的 taishan 同样计入）。
                    _pf_ids = _pf_ids_all()
                    with open(hires_p, encoding="utf-8") as _f:
                        active = {h.get("trader") for h in
                                  (_j3.load(_f).get("hires") or [])
                                  if h.get("source") in _pf_ids
                                  and h.get("status") == "active"}
                _GRADE = {"live": "正式", "training": "实习",
                          "design": "储备", "ops": "运营台"}
                # K-STOCK-167 C3 文案收编：擅长/风格文案改由档案投影驱动，
                # 从 trainees/*.json 读真档案 desc，前端不再内置硬编码。
                _faces = {}
                _tdir = os.path.join(api_portfolio._PAPER_DIR, "trainees")
                for _fn in sorted(glob.glob(os.path.join(_tdir, "*.json"))):
                    try:
                        with open(_fn, encoding="utf-8") as _f:
                            _x = _j3.load(_f)
                        _faces[_x.get("id")] = _x.get("desc")
                    except Exception:
                        continue
                rows = []
                for t in (r.get("rows") or []):
                    t = dict(t)
                    t["grade"] = _GRADE.get(t.get("status"), "储备")
                    t["hired_pf"] = t.get("id") in active
                    t["desc"] = _faces.get(t.get("id"))
                    rows.append(t)
                rows.sort(key=lambda x: (not x.get("hired_pf"),
                                         x.get("status") != "live",
                                         -(x.get("cum_pct") or 0)))
                self._json({"ok": True, "rows": rows})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/trader_detail":
            # K-STOCK-122 投研团队：交易员下钻——成绩行 + 净值曲线 + 近期成交
            from urllib.parse import parse_qs as _pqs4
            _tid = (_pqs4(qs).get("id") or [""])[0].strip()
            try:
                import sqlite3 as _sq3
                r = self._paper_run_json(
                    [self._PAPER_PY,
                     os.path.join(self._PAPER_DIR, "trainee.py"),
                     "board", "--json"], timeout=90)
                row = next((x for x in (r.get("rows") or [])
                            if x.get("id") == _tid), None)
                if not row:
                    self._json({"ok": False, "error": "no trader: %s" % _tid})
                    return
                con = _sq3.connect(
                    f"file:{api_portfolio._PAPER_DB}?mode=ro", uri=True)
                con.row_factory = _sq3.Row
                eq, recent, n_filled = [], [], 0
                try:
                    eq = [dict(x) for x in con.execute(
                        "SELECT date,total FROM equity_marks WHERE "
                        "account_id=? ORDER BY date DESC LIMIT 15",
                        (_tid,))][::-1]
                    recent = [dict(x) for x in con.execute(
                        "SELECT stock,side,qty,filled_price,fill_date "
                        "FROM orders WHERE account_id=? AND "
                        "status='filled' ORDER BY id DESC LIMIT 8",
                        (_tid,))]
                    n_filled = con.execute(
                        "SELECT COUNT(*) FROM orders WHERE account_id=? "
                        "AND status='filled'", (_tid,)).fetchone()[0]
                finally:
                    con.close()
                self._json({"ok": True, "trader": row, "equity": eq,
                            "recent": recent, "filled_total": n_filled})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/review_minutes":
            # K-STOCK-122 M5 复盘会纪要投影（只读）：?date=YYYY-MM-DD 缺省最新
            try:
                from urllib.parse import parse_qs as _pqs3
                _d = (_pqs3(qs).get("date") or [""])[0].strip()
                # K-STOCK-184 安全校验：date 严格 YYYY-MM-DD。裸 join 曾可被
                # 反斜杠穿越 / 盘符绝对路径覆写读盘上任意 .json（含飞书密钥）。
                if _d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _d):
                    self._json({"ok": False,
                                "error": "date 非法（YYYY-MM-DD）"}, 400)
                    return
                import glob as _glob
                mdir = os.path.join(
                    api_portfolio._PAPER_DATA_DIR, "review_minutes")
                fp = (os.path.join(mdir, "%s.json" % _d) if _d
                      else max(_glob.glob(os.path.join(mdir, "*.json")),
                               default=None))
                if not fp or not os.path.exists(fp):
                    self._json({"ok": False, "error": "无纪要文件"})
                    return
                with open(fp, encoding="utf-8") as _f:
                    self._json(json.load(_f))
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/strategy_detail":
            # K-STOCK-122 M2 下钻一层：?id=xxx 静态档案+动态命中样本
            from urllib.parse import parse_qs as _pqs2
            _sid = (_pqs2(qs).get("id") or [""])[0].strip()
            try:
                import strategy_market as _sm
                self._json(_sm.detail(_sid),
                           200 if _sid else 400)
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path.startswith("/static/"):
            # KG-V1 静态资源（白名单）：本地 echarts / 全站 tokens.css
            fn = path.split("/", 2)[2]
            _CT = {"echarts.min.js": "application/javascript",
                   "g6.min.js": "application/javascript",
                   "nav.js": "application/javascript",
                   "tokens.css": "text/css",
                   "mobile_table.css": "text/css",
                   "shortscreen.js": "application/javascript",
                   "firstscreen.js": "application/javascript",
                   "cainiao.js": "application/javascript",
                   "china_geo.json": "application/json"}
            if fn in _CT:
                fp = os.path.join(ROOT, "static", fn)
                if os.path.exists(fp):
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        _CT[fn] + "; charset=utf-8")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length",
                                     str(os.path.getsize(fp)))
                    self.end_headers()
                    with open(fp, "rb") as f:
                        self.wfile.write(f.read())
                    return
            self.send_error(404)
        elif path == "/api/brief":
            # 首页四板块卡的动态小字（盘前倾向/机会数/今日情报数）
            self._json(build_brief())
        elif path == "/api/strategies":
            # 策略资产注册表（K36）：首页组合区/策略中心同源
            self._json(strategies_data())
        elif path == "/api/alerts":
            # K39 今日预警速览：各策略产线最新预警值聚合（量化域首页）
            self._json(build_alerts())
        elif path == "/api/home":
            # K-STOCK-167 门面「此刻区」数据源（自 home_server/8094 收编，
            # 原服务退役；nginx /api/ 反代直达）
            self._json(build_home_cockpit())
        elif path == "/api/os":
            # K-STOCK-260 个人 OS 指挥大屏聚合（采集/Agent/量化/入库四域，
            # 纯文件+sqlite 轻量读；nginx /api/ 反代直达）
            self._json(build_os_cockpit())
        elif path == "/api/screen":
            # K44 数据大屏：策略×市场×行业×账本成绩 聚合
            self._json(build_screen())
        elif path == "/api/portfolio_csv":
            # K51-B 交易明细导出 CSV（?sid= 单账户，缺省全部）
            from urllib.parse import parse_qs as _pqc
            sid = (_pqc(qs).get("sid") or [""])[0]
            body = _pf_csv(sid)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="portfolio_trades_%s.csv"'
                             % (sid or "all"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/portfolio_market":
            # K52-D 手动建仓标的搜索（?q= code/name 关键词）
            self._json(_portfolio_market(qs))
        elif path == "/api/chanlun/kline":
            # 缠论 P1 数据底座：多周期 K 线 JSON（1d/60m/30m）
            from urllib.parse import parse_qs as _pqk
            qp = _pqk(qs)
            import sys as _sysk
            if QBT_DIR not in _sysk.path:
                _sysk.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline
            self._json(load_kline(
                (qp.get("code") or [""])[0],
                (qp.get("period") or ["1d"])[0],
                (qp.get("limit") or ["300"])[0]))
        elif path == "/api/chanlun/structure":
            # 缠论 P2+P3：包含处理+分型+笔/线段/中枢+三类买卖点（复用 P1 数据底座）
            from urllib.parse import parse_qs as _pqst
            qp2 = _pqst(qs)
            import sys as _sysst
            if QBT_DIR not in _sysst.path:
                _sysst.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline as _lk
            from chanlun_signal import analyze as _az
            r2 = _lk((qp2.get("code") or [""])[0],
                     (qp2.get("period") or ["1d"])[0],
                     (qp2.get("limit") or ["500"])[0])
            if not r2.get("ok"):
                self._json(r2)
            else:
                st = _az(r2["bars"])
                r2["fractals"] = st["fractals"]
                r2["merged_n"] = st["merged_n"]
                r2["bis"] = st["bis"]
                r2["segments"] = st["segments"]
                r2["pivots"] = st["pivots"]
                r2["signals"] = st["signals"]
                # P7：日线信号 best-effort 附 60m 次级别共振影子标注
                # （缺数据 mt=None；60m/30m 请求不做下钻）
                if r2.get("period") == "1d" and st["signals"]:
                    try:
                        from chanlun_mtf import confluence as _cf
                        rs = _lk(r2.get("code"), "60m", 1000)
                        sub_bars = rs["bars"] if rs.get("ok") else []
                        # P8-b：大盘环境（CN 上证 / CRYPTO BTC；US/HK 无指数）
                        from chanlun_flow import REGIME_INDEX as _RIDX, \
                            regime_at as _reg
                        idx_bars = []
                        if r2.get("market") in _RIDX:
                            ri = _lk(_RIDX[r2["market"]], "1d", 500)
                            idx_bars = ri["bars"] if ri.get("ok") else []
                        for _s in st["signals"]:
                            _s["mt"], _s["mtv"] = _cf(
                                st["bis"], _s, sub_bars)
                            if idx_bars:
                                _s["reg"], _s["reg_ev"] = _reg(
                                    r2["market"], _s["dt"], idx_bars)
                    except Exception as _e:
                        r2["mt_warn"] = str(_e)[:100]
                self._json(r2)
        elif path == "/api/chanlun/buy/sectors":
            # 🛒 买点实战台：今日买点按板块归并（只读，进程内 1 小时缓存）
            self._chanlun_buy_sectors(qs)
        elif path == "/api/chanlun/pool/heat":
            # K-ORG-20261107-CHANDECK：市场信号热度（只读扫 chanlun_pool.jsonl
            # 最新 detect_date，六类买卖点计数；流式逐行，不整库载入）
            import json as _jheat
            from collections import Counter as _Cnt
            _pool_p = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
            _det, _cnt = None, _Cnt()
            try:
                with open(_pool_p, encoding="utf-8") as _f:
                    for _ln in _f:
                        try:
                            _r = _jheat.loads(_ln)
                        except ValueError:
                            continue
                        _d = _r.get("detect_date")
                        if _d and (not _det or _d > _det):
                            _det = _d
            except OSError:
                pass
            if _det:
                try:
                    with open(_pool_p, encoding="utf-8") as _f:
                        for _ln in _f:
                            try:
                                _r = _jheat.loads(_ln)
                            except ValueError:
                                continue
                            if _r.get("detect_date") == _det:
                                _cnt[_r.get("type") or "?"] += 1
                except OSError:
                    pass
            self._json({"ok": True, "detect_date": _det,
                        "counts": dict(_cnt)})
        elif path == "/api/chanlun/backtest":
            # 缠论 P4：三类买卖点事件统计 + 策略回测（理论价口径）
            from urllib.parse import parse_qs as _pqst
            qp3 = _pqst(qs)
            import sys as _sysst
            if QBT_DIR not in _sysst.path:
                _sysst.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline as _lk3
            from chanlun_backtest import analyze as _azbt
            r3 = _lk3((qp3.get("code") or [""])[0],
                      (qp3.get("period") or ["1d"])[0],
                      (qp3.get("limit") or ["300"])[0])
            if not r3.get("ok"):
                self._json(r3)
            else:
                st3 = _azbt(r3["bars"])
                r3.pop("bars", None)
                r3.pop("fractals", None)
                r3.pop("bis", None)
                r3.pop("segments", None)
                r3.pop("pivots", None)
                r3.pop("signals", None)
                r3["n_signals"] = len(st3["signals"])
                r3["events"] = st3["events"]
                r3["by_type"] = st3["by_type"]
                r3["stats"] = st3["stats"]
                self._json(r3)
        elif path == "/kg":
            # M3-W2 情报探索页（HTML，服务端渲染：概览/个股全景/游资归因）
            self._kg_page(qs)
        elif path == "/kg/company":
            # KG-O 公司本体全景页（中心辐射六扇区 + 直方图 + 关系统计）
            self._kg_company_page(qs)
        elif path == "/kg/map":
            # M3-W3 游资地图页（全景/共现/动向流 + 席位风格画像）
            self._kg_map_page(qs)
        elif path == "/kg/shadow":
            # M3-W4 影子面板页（真假信号按日对照，口径透明）
            self._kg_shadow_page(qs)
        elif path in route_table._GET_EXACT_3:
            getattr(self, route_table._GET_EXACT_3[path])()
        elif path == "/kg/doc":
            # K266 M4 文档智能中心（词云三跳+上传）
            self._kg_doc_page(qs)
        elif path in route_table._GET_EXACT_4:
            getattr(self, route_table._GET_EXACT_4[path])()
        elif path == "/kg/events":
            # K266 事件图谱查询面板（态势感知 + 市场Tab + G6 + 下钻原文）
            self._kg_events_page(qs)
        elif path == "/kg/events/situ" or path.startswith(
                ("/kg/events/graph", "/kg/events/list",
                 "/kg/events/search", "/kg/events/doc/")):
            # K266 事件图谱 JSON 端点（数据层 kg_events_portal.py，纯查询）
            from urllib.parse import parse_qs as _pqev, unquote as _uqev
            import sys as _syev
            if QBT_DIR not in _syev.path:
                _syev.path.insert(0, QBT_DIR)
            import kg_events_portal as _KE
            if path == "/kg/events/situ":
                self._json(_KE.api("situ"))
            elif path.startswith("/kg/events/doc/"):
                self._json(_KE.api("doc", _uqev(path[len("/kg/events/doc/"):])))
            else:
                self._json(_KE.api(path.split("/")[3], None, {
                    k: v[0] for k, v in _pqev(qs).items() if v}))
        elif path == "/static/g6.min.js":
            # K234 G6 本地静态（零 CDN 依赖）
            import os as _os6
            fp = _os6.path.join(_os6.path.dirname(
                _os6.path.abspath(__file__)), "static", "g6.min.js")
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self._json({"error": "static not found"}, 404)
        elif path == "/bridge":
            # K56-B1 投研驾驶舱：顶板五仪（数据/运行/预警/图台账/账户）
            self._bridge_page(qs)
        elif path == "/bridge/act":
            # K56-B3 指挥台两步确认页（白名单动作：三动作/同步/扫描）
            self._bridge_act_page(qs)
        elif path == "/council":
            # K55-C4 投委会留痕页（三张 jsonl 浏览+检索+影子统计/转正门槛）
            self._council_page(qs)
        elif path == "/sim":
            # K68 三操盘手模拟锦标赛（六本账战绩榜，只读 reports）
            self._sim_page(qs)
        elif path == "/debate":
            # K74 模拟辩论室：五幕辩论回放 + 老板插话
            self._debate_page(qs)
        elif path == "/api/chanlun/label/load":
            # v2.5 读已存标注（同 code+period 最新一条，供回填续标）
            from urllib.parse import parse_qs as _pql
            qp = _pql(qs)
            code = (qp.get("code") or [""])[0]
            period = (qp.get("period") or [""])[0]
            hit = None
            try:
                with open(_LABELS_FP, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if r.get("code") == code and r.get("period") == period:
                            hit = r          # 取最后一条
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            self._json(hit or {"none": True})
        elif path.startswith("/kg/stock/") and "format=json" not in qs:
            # K-STOCK-051 统一股票详情卡（046 §5.1 五区制，唯一样式）：
            # 浏览器默认 HTML；?format=json 走下方 JSON 端点保兼容既有调用方
            from urllib.parse import unquote as _uqsc
            _parts_sc = path.split("/", 3)
            _code_sc = _uqsc(_parts_sc[3]) if len(_parts_sc) > 3 and _parts_sc[3] else ""
            if not _code_sc:
                self._json({"ok": False, "error": "missing code"})
            else:
                try:
                    import stock_card as _stock_card
                    self._html(_stock_card.render_card(_code_sc))
                except Exception as _esc_err:  # noqa: BLE001
                    self._json({"ok": False, "error": f"stock_card: {_esc_err}"})
        elif path in ("/kg/stats", "/kg/sources", "/kg/overview",
                      "/kg/ontology") or path.startswith(
                ("/kg/stock/", "/kg/hotmoney/", "/kg/trace/",
                 "/kg/objectview/",                   # L2 扇区体检 JSON
                 "/kg/company360/", "/kg/csearch/", "/kg/cexpand/", "/kg/ctimeline/", "/kg/chist/",
                 "/kg/cask/",
                 "/kg/timeline/", "/kg/batch/",   # K262 作战地图
                 "/kg/records/",                   # K262 战绩层（四源聚合）
                 "/kg/guba/",                      # K263 股吧舆论
                 "/kg/gx/",                        # K261 L2 图分析
                 "/kg/chist/")):
            # M3-W1 知识图谱 JSON 端点（数据层 quant_backtest/kg_portal.py，纯查询不写库）
            #   /kg/stats /kg/sources /kg/overview /kg/stock/{code} /kg/hotmoney/{name} /kg/trace/{code}?days=N
            #   K226 画布：/kg/csearch/{q} /kg/cexpand/{Label|key}
            from urllib.parse import parse_qs as _pqkg, unquote as _uqkg
            import sys as _sykg
            if QBT_DIR not in _sykg.path:
                _sykg.path.insert(0, QBT_DIR)
            import kg_portal
            if path == "/kg/stats":
                self._json(kg_portal.api("stats"))
            elif path == "/kg/sources":
                # G7 来源评级（Admiralty A-F + 断流红点）
                self._json(kg_portal.api("sources"))
            elif path == "/kg/overview":
                # KG-V4 集团总览层（G6 画布数据：超节点+桥接边）
                self._json(kg_portal.api("overview"))
            else:
                parts = path.split("/", 3)   # ["", "kg", kind, arg]
                qp = _pqkg(qs, keep_blank_values=True)
                days = (qp.get("days") or [None])[0]
                per = (qp.get("per") or [None])[0]
                # arg 段可缺省（/kg/ontology 等无参 kind，越界即崩线程断连）
                kind = parts[2]
                arg = _uqkg(parts[3]) if len(parts) > 3 else None
                if kind == "ctimeline":
                    try:
                        result = kg_portal.api(
                            kind, arg, per=(qp.get("to") or [None])[0],
                            scope={"from": (qp.get("from") or [None])[0],
                                   "limit": (qp.get("limit") or [None])[0]})
                        self._json(result)
                    except ValueError as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                    except Exception:
                        self._json({"ok": False, "error": "图谱查询失败"}, 500)
                elif kind in ("gx", "cexpand"):
                    scope = {}
                    types = (qp.get("types") or [""])[0].strip()
                    at = (qp.get("at") or [""])[0].strip()
                    if types:
                        scope["types"] = [t.strip() for t in types.split(",")]
                    if at:
                        scope["at"] = at
                    try:
                        self._json(kg_portal.api(kind, arg, days, per,
                                                 scope=scope or None))
                    except ValueError as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                    except Exception:
                        self._json({"ok": False, "error": "图谱查询失败"}, 500)
                else:
                    self._json(kg_portal.api(kind, arg, days, per))
        elif path in route_table._GET_EXACT_5:
            getattr(self, route_table._GET_EXACT_5[path])()
        elif path == "/intel":
            self._html_file(os.path.join(ROOT, "intel_workbench.html"))
        elif path == "/intel-dossier":
            # K-MDC-R03 批次①b：案卷五屏评审路由（骨架态——空态不接真源，
            # 老田 2026-10-23 批准挂载；合并自 K-INTEL-20261022-FE）
            self._html_file(os.path.join(ROOT, "intel_dossier_skeleton.html"))
        elif path == "/api/intel-graph/dossier":
            # K-MDC-E2/#2：第六屏数据端点转生产（只读，A4/A5 影子+D2 规则实时构建；
            # 老田 2026-10-23 下一步方案#2 批准；8091 不重启，随冷启动生效）
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = (qs.get("code") or [""])[0]
            sys.path.insert(0, os.path.join(ROOT, "..", "quant_backtest"))
            sys.path.insert(0, ROOT)
            from api_intel_graph import dossier
            try:
                self._json(dossier(code))
            except Exception as error:  # 失败关闭: 端点异常返回结构化错误而非 500 白页
                self._json({"error": f"dossier_unavailable: {error}"})
        elif path == "/notify-command":
            # K-ORG-201 通知指挥只读投影页（FB-C 2026-10-19 个人 OS 入口三件套：
            # api_common.py intel 域导航「📨 通知指挥」落地页，零参数只读 GET）
            self._html_file(os.path.join(ROOT, "static",
                                          "notify_command.html"))
        elif path == "/ops-board":
            # K-ORG-202 运维对账只读投影页（FB-M 2026-10-20 飞书中台 P1：
            # 死信 / blocked 折叠对账 / 四账本来源计数 / 子源缺口 / 服务在役表，
            # 零参数只读 GET，零写操作）
            self._html_file(os.path.join(ROOT, "static",
                                          "ops_board.html"))
        elif path == "/api/subscription/status":
            # P2.5 客户订阅面（FD-A 2026-10-20）｜WO-FS-101（P3.7-lite R1
            # 加固）：订阅账本只读摘要出口，响应先过白名单最小化再回吐。
            # ?open_id= 必填（缺失 400）；可选 key 闸——环境变量
            # PORTAL_SUB_STATUS_KEY 非空时要求请求头 X-Portal-Key 常量相等，
            # 不等/缺失 401 {"error":"unauthorized"}；未配置时不设闸，行为与
            # 加固前一致（既有调用方零破坏）。
            # 零写参数——本端点不接收任何写参数，写操作仍只在
            # subscription_store 模块内（M3 收银台另批）。
            # open_id 只回掩码；金额类字段、内部 reason 一律不透出。
            # get_subscriber_summary 自身 closed on failure（异常返回
            # {"error": ...} 不抛），此处不造数，只做字段最小化。
            from urllib.parse import parse_qs as _pq_sub
            if not _sub_status_key_ok(self.headers.get("X-Portal-Key")):
                self._json({"ok": False, "error": "unauthorized"}, 401)
            else:
                _oid = (_pq_sub(qs, keep_blank_values=True)
                        .get("open_id") or [""])[0].strip()
                if not _oid:
                    self._json({"ok": False, "error": "missing open_id"}, 400)
                else:
                    import subscription_store as _sub_store
                    self._json(_sub_status_view(
                        _sub_store.get_subscriber_summary(_oid)))
        elif path == "/api/comm/ticket":
            # K4.4 工单详情只读端点（WO-FS-154，卡片下钻用）。
            # 数据源 agent_platform/bridge_inbox（inbox/processed）+ 订阅账本。
            # 安全口径：
            # - id 必须是 lg-[a-z0-9]{12}（服务端生成的高熵 id），不接受路径穿越；
            # - 返回体 open_id 一律掩码，不含聊天原文（inbox 里本就没有原文）；
            # - 只读，无写参数；读不到就如实报 not_found，不造数据。
            import re as _re_t
            from urllib.parse import parse_qs as _pq_t
            _q_t = _pq_t(qs, keep_blank_values=True)
            _tid = (_q_t.get("id") or [""])[0].strip()
            if not _re_t.match(r"^lg-[a-z0-9]{6,24}$", _tid):
                self._json({"ok": False, "error": "bad_id"}, 400)
            else:
                try:
                    from pathlib import Path as _P_t
                    import json as _j_t
                    _root = _P_t(__file__).resolve().parent.parent / "agent_platform" / "bridge_inbox"
                    _rec = None
                    _state = None
                    for _sub, _st in (("inbox", "inbox"), ("processed", "processed")):
                        _f = _root / _sub / ("%s.json" % _tid)
                        if _f.exists():
                            _rec = _j_t.loads(_f.read_text(encoding="utf-8"))
                            _state = _st
                            break
                    if _rec is None:
                        self._json({"ok": False, "error": "not_found"}, 404)
                    else:
                        _pl = _rec.get("payload") or {}
                        _kind = _rec.get("kind") or ""
                        _kind_label = {"task_request": "派单请求", "feedback": "投诉反馈",
                                       "question": "咨询提问"}.get(_kind, _kind)
                        _intent = _pl.get("intent") or ""
                        _intent_label = {"stock_consult": "个股咨询", "watch_setup": "盯票设置",
                                         "subscription_qa": "订阅查询", "billing": "账单/发票",
                                         "complaint": "投诉", "feed": "内容投喂",
                                         "smalltalk": "闲聊", "consult": "咨询（军师）",
                                         "unclear": "待澄清"}.get(_intent, _intent or "—")
                        _route_desc = {"stock_consult": "司马懿席（盯盘官）",
                                       "watch_setup": "许褚营 G1 触发器",
                                       "complaint": "田丰归口（最高优先级）"}.get(_intent, "客服席位")
                        _oid = str(_pl.get("open_id") or "")
                        _sub_info = {"ok": False}
                        try:
                            import subscription_store as _ss_t
                            _sum = _ss_t.get_subscriber_summary(_oid) or {}
                            _subs = _sum.get("subscriptions") or []
                            _plan = (_subs[-1].get("plan_id") if _subs else "")
                            _end = ((_subs[-1].get("end") or "")[:10] if _subs else "")
                            _qmap = _sum.get("daily_quota_by_plan") or {}
                            _used = _sum.get("today_used_by_prefix") or {}
                            _sub_info = {
                                "ok": True, "has_active": bool(_subs),
                                "plan_id": _plan, "end": _end,
                                "used_today": sum(int(v or 0) for v in _used.values()),
                                "quota": int(_qmap.get(_plan) or 0),
                            }
                        except Exception:
                            _sub_info = {"ok": False}
                        _tl = [{"label": "已提交", "time": str(_rec.get("submitted_at") or "")[:19]}]
                        if _state == "inbox":
                            _tl.append({"label": "已转派 · 排队处理中", "time": "",
                                        "pending": True})
                        else:
                            _tl.append({"label": "已处理完成", "time": ""})
                        _hint = ("预计 %s 分钟内回复。若超时，可在飞书回复「催一下」加急。"
                                 % (_pl.get("deadline_min") or 5)) \
                            if _state == "inbox" else "本工单已处理完成。"
                        self._json({
                            "ok": True,
                            "ticket": {
                                "dispatch_id": _rec.get("id"),
                                "state": _state,
                                "kind": _kind, "kind_label": _kind_label,
                                "intent": _intent, "intent_label": _intent_label,
                                "code": _pl.get("code") or "",
                                "route_desc": _route_desc,
                                "deadline_min": _pl.get("deadline_min") or 5,
                                "submitted_at": str(_rec.get("submitted_at") or "")[:19],
                                "open_id_masked": (_oid[:6] + "**" + _oid[-2:]) if len(_oid) > 8 else "**",
                            },
                            "timeline": _tl,
                            "next_hint": _hint,
                            "subscription": _sub_info,
                        })
                except Exception:
                    self._json({"ok": False, "error": "ticket_unavailable"}, 200)

        elif path == "/api/comm/interest":
            # K4.2 商机信号只读出口（WO-FS-148-9，2026-10-21）。
            # 数据源 comm_interest 聚合表（只存行为统计，无原文）。
            #
            # 口径：
            # - ?open_id= 可选。带则只回该客户 stats_7d + 个性化引导文案，
            #   不带则回 7 日窗商机信号列表（按星级/被闸/咨询排序）。
            # - 鉴权复用 sub_admin 同一把钥匙（PORTAL_SUB_ADMIN_KEY），
            #   不再新增密钥变量：本端点与 /api/subscription/list 同属
            #   内部域批量面，密钥分裂只会增加运维成本。
            # - 未配密钥时 auth_mode="none" 照实自述（裸奔可见）。
            # - 401 在读画像库之前短路；open_id 对外掩码（本端点服务内部
            #   商务面展示，不需要明文回填，故与 list 的「不掩码」策略不同）。
            # - 只读：不接受任何写参数，POST 不路由到本分支。
            _want_cm = (os.environ.get("PORTAL_SUB_ADMIN_KEY") or "").strip()
            _auth_cm = "key" if _want_cm else "none"
            _got_cm = (self.headers.get("X-Portal-Key") or "").strip()
            _ok_cm = True
            if _want_cm:
                _ok_cm = bool(_got_cm) and hmac.compare_digest(
                    _got_cm.encode("utf-8"), _want_cm.encode("utf-8"))
            if not _ok_cm:
                self._json({"ok": False, "error": "unauthorized"}, 401)
            else:
                try:
                    from urllib.parse import parse_qs as _pq_cm
                    _q_cm = _pq_cm(qs, keep_blank_values=True)
                    _oid_cm = (_q_cm.get("open_id") or [""])[0].strip()
                    import comm_interest as _ci_cm
                    if _oid_cm:
                        _st_cm = _ci_cm.stats_7d(_oid_cm)
                        _gd_cm = None
                        try:
                            _gd_cm = _ci_cm.guide_text(_oid_cm)
                        except Exception:  # noqa: BLE001
                            _gd_cm = None
                        self._json({"ok": True, "auth_mode": _auth_cm,
                                    "open_id": _oid_cm,
                                    "stats": _st_cm, "guide": _gd_cm})
                    else:
                        _sig_cm = _ci_cm.signals(limit=50)
                        self._json({"ok": True, "auth_mode": _auth_cm,
                                    "count": len(_sig_cm),
                                    "signals": _sig_cm})
                except Exception:  # noqa: BLE001
                    self._json({"ok": False, "auth_mode": _auth_cm,
                                "error": "interest_unavailable",
                                "signals": []}, 200)

        elif path == "/api/subscription/list":
            # P2.6 订阅批量列表（FE-B 2026-10-20）：订阅者全集只读出口。
            # ?limit= 可选（默认 50，钳制 [1,200]；非法值回落默认 50），
            # 零写参数——本端点不接收任何写参数，写操作仍只在
            # subscription_store 模块内（M3 收银台另批）。
            #
            # WO-FS-112（P3.8-lite · M-B 2026-10-20）鉴权加固（批量枚举面）：
            # - 可选 key 闸，默认关：环境变量 PORTAL_SUB_ADMIN_KEY 非空时，
            #   要求请求头 X-Portal-Key 与之常量相等（hmac.compare_digest，
            #   不用 == 比较密钥）；缺失/不等 → 401 {"error":"unauthorized"}，
            #   且在触碰订阅账本之前短路——响应体既无任何行数据，也不含期望
            #   密钥的任何片段。未配置该变量时不开闸，本端点行为与加固前
            #   完全一致（?limit= 语义、错误结构均不变），既有调用方零破坏。
            #   请求头名与 /api/subscription/status 保持一致，便于前端统一传。
            # - 鉴权状态自述（不可省）：响应顶层恒带 auth_mode，"key" 表示
            #   闸已开、"none" 表示当前无鉴权运行（任何人可读）。未配置密钥
            #   时也照实回 "none"，让「裸奔运行」在运维面一眼可见，不假装
            #   已加固。该字段是追加，ok / count / subscribers 一概不动。
            # - 不掩码 open_id（有意为之，非遗漏）：本端点服务
            #   sub_admin.html 这一内部运维/商务面，功能上需要复制 open_id
            #   去做逐个查询与开通，掩码会直接废掉该能力。故 list 走「内部
            #   域端点」的防护策略——靠 key 闸与网络边界，而非字段遮蔽；
            #   status 是客户自查端点，故做白名单最小化。两者防护策略不同是
            #   有意的，不是遗漏。
            # - 最小化收敛：store 若回传内部 reason / 异常明细（error 文本、
            #   traceback 等），一律不透出，统一替换为稳定结构
            #   {"ok": False, "error": "store_unavailable"}。
            # list_subscribers 自身 closed on failure（store 缺失/异常返回
            # 稳定错误结构，不抛），此处不吞错不造数。
            #
            # WO-FS-131（P3.10-lite · O-A 2026-10-20）分页接线：
            # - ?offset= 可选（默认 0）。缺失 / 非数字 / 负数 / 浮点串一律归
            #   0，不报 4xx：旧调用方只传 limit，补 offset 后不能因一个拼错的
            #   参数把整页数据变成错误面；归 0 的最坏后果只是重复第一页，而
            #   响应里的 offset 如实回吐生效值，调用方可自检。
            # - 成功响应只追加 next_offset：has_more 为真时给出确定性下一页
            #   起点（= 生效 offset + 本页行数），否则不给该键（缺键即「没有
            #   下一页」，与 None 同义）。不给 None 是有意的——既有精确等值
            #   契约测试断言「成功载荷 == store 原样 + auth_mode」，多一个恒
            #   在的 None 键会把它变成纯噪音断言；缺键同样无法诱导调用方自
            #   行推算 offset。
            # - ok / count / subscribers / offset / has_more 一个不改，401 短
            #   路仍在读账本之前。
            _want_adm = (os.environ.get("PORTAL_SUB_ADMIN_KEY") or "").strip()
            _auth_mode = "key" if _want_adm else "none"
            _got_adm = (self.headers.get("X-Portal-Key") or "").strip()
            _auth_ok = True
            if _want_adm:
                _auth_ok = bool(_got_adm) and hmac.compare_digest(
                    _got_adm.encode("utf-8"), _want_adm.encode("utf-8"))
            if not _auth_ok:
                # 短路：密钥未通过时不读账本，错误体与 status 端点同形
                self._json({"ok": False, "error": "unauthorized"}, 401)
            else:
                from urllib.parse import parse_qs as _pq_subl
                _q_subl = _pq_subl(qs, keep_blank_values=True)
                _raw = (_q_subl.get("limit") or [""])[0].strip()
                _limit = 50
                if _raw:
                    try:
                        _limit = int(_raw)
                    except ValueError:
                        _limit = 50
                # offset：缺失 / 非数字 / 负数 / 浮点串（"3.7"）一律归 0，
                # 不抛不 4xx（见上方分页接线说明）。
                _raw_off = (_q_subl.get("offset") or [""])[0].strip()
                _offset = 0
                if _raw_off:
                    try:
                        _offset = int(_raw_off)
                    except ValueError:
                        _offset = 0
                    if _offset < 0:
                        _offset = 0
                import subscription_store as _sub_store
                _res = _sub_store.list_subscribers(limit=_limit,
                                                  offset=_offset)
                if not isinstance(_res, dict) or _res.get("ok") is not True:
                    # closed on failure：上游错误文本/堆栈一律归并，不透出
                    self._json({"ok": False, "error": "store_unavailable",
                                "auth_mode": _auth_mode})
                else:
                    _view = dict(_res)
                    _view["auth_mode"] = _auth_mode
                    if _view.get("has_more") is True:
                        # 下一段起点由服务端定：生效 offset + 本页行数
                        _view["next_offset"] = _offset + int(
                            _view.get("count") or 0)
                    self._json(_view)
        elif path == "/my-subscription":
            # P2.5 客户订阅只读投影页（FD-A 2026-10-20）：订阅状态 / 当日
            # 额度余量 / 账单占位，照 notify-command/ops-board 判例，
            # 零参数只读 GET，零写操作。
            self._html_file(os.path.join(ROOT, "static",
                                          "my_subscription.html"))
        elif path == "/comm/ticket":
            # K4.4 工单详情页（卡片「查看详情」按钮落点）
            self._html_file(os.path.join(ROOT, "static", "comm_ticket.html"))
        elif path in route_table._GET_EXACT_6:
            getattr(self, route_table._GET_EXACT_6[path])()
        elif path.startswith("/gzh/view/"):
            # 草稿全文预览（gzh_writer 生成的 preview.html，白名单防穿越）
            import re as _re2
            from urllib.parse import unquote as _uq
            fn = _uq(path[len("/gzh/view/"):])
            if _re2.match(r"^[^/\\]+\.preview\.html$", fn):
                self._html_file(os.path.join(STATION, "gzh", "drafts", fn))
            else:
                self._json({"error": "bad file"}, 400)
        elif path in route_table._GET_EXACT_7:
            getattr(self, route_table._GET_EXACT_7[path])()
        elif path.startswith("/vd/file/"):
            # 成片附件（mp4/wav/srt/json，白名单防穿越）
            import re as _re2
            from urllib.parse import unquote as _uq
            rest = _uq(path[len("/vd/file/"):])
            m = _re2.match(r"^([^/\\]+)/(video\.mp4|audio\.wav|"
                           r"subs\.srt|script\.json)$", rest)
            if not m:
                self._json({"error": "bad file"}, 400)
                return
            ct = {"video.mp4": "video/mp4", "audio.wav": "audio/wav",
                  "subs.srt": "application/octet-stream; charset=utf-8",
                  "script.json": "application/json; charset=utf-8"}[m.group(2)]
            self._send_media(os.path.join(STATION, "video", "output",
                                          m.group(1), m.group(2)), ct)
        elif path == "/sa":
            # 个股分析极简页（一个输入框 → 行情+新闻+JEV+展望；
            # ?q= 预填并自动开始，供金融驾驶舱/首页直达）
            from urllib.parse import parse_qs, unquote as _uq
            q = (parse_qs(qs).get("q") or [""])[0]
            self._sa_page(_uq(q))
        elif path == "/sa/start":
            from urllib.parse import parse_qs, unquote as _uq
            q = (parse_qs(qs).get("q") or [""])[0]
            self._sa_start(_uq(q))
        elif path == "/sa/result":
            from urllib.parse import parse_qs
            jid = (parse_qs(qs).get("job") or [""])[0]
            self._sa_result(jid)
        elif path == "/sa/workbench":
            self._html_file(os.path.join(ROOT, "sa_workbench.html"))
        elif path == "/sa/monitor":
            self._html_file(os.path.join(ROOT, "sa_monitor.html"))
        elif path == "/bt-signals":
            # 飞书卡片「明日信号」按钮目标页：全池 4 策略持仓信号
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "signals.html"),
                            nav_key="signals")
        elif path == "/pool-watch":
            # 许褚营·双池触发监控台（WO-QT-20261020-078）：选票池×触发
            # 事件。每请求尽力同步刷新（读 todays_picks + signal_pool
            # 纯读渲染，廉价）；任何异常降级旧静态文件（再失败由
            # _html_file 出「暂无报告」），影子期红线常驻页面内。
            try:
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import pool_watch_page as _pwp
                self._html(_pwp.render_default())
            except Exception:
                self._html_file(os.path.join(STATION, "quant_backtest",
                                             "reports", "pool_watch.html"))
        elif path == "/quant-entry":
            # 量化交易板块入口（WO-QT-20261021-087）：四市场总表
            # （今日多空·策略货架·股票池·Agent 席）+ 席位门户。每请求
            # 尽力同步刷新（只读聚合盘上产物，纯渲染层，零写）；任何
            # 异常降级静态报告文件（再失败由 _html_file 出「暂无报告」），
            # 影子期红线常驻页面内。缺源格显式「待接」不造数。
            try:
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import quant_entry_page as _qep
                self._html(_qep.render_default())
            except Exception:
                self._html_file(os.path.join(STATION, "quant_backtest",
                                             "reports", "quant_entry.html"))
        elif path == "/bt-positions":
            # 盘前作战计划卡「持仓跟踪」按钮目标页：纸面持仓账本
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "positions.html"),
                            nav_key="positions")
        elif path in route_table._GET_EXACT_8:
            getattr(self, route_table._GET_EXACT_8[path])()
        elif path == "/bt-opt":
            # 飞书卡片「寻优榜单」按钮目标页：全池参数寻优 Top5
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "opt.html"),
                            nav_key="opt")
        elif path in ("/qd", "/qd/"):
            # K-STOCK-027 影子层/展示层分离：渲染委托 qd_view.py（纯函数，
            # 可离线单测 python qd_view.py），本分支只做 I/O 副作用——
            # 读扫描缓存 + 无缓存时后台 spawn venv 扫描（stdout 落文件）
            import subprocess as _sp
            import qd_view as _qd_view
            cache_p = os.path.join(ROOT, "qd_scan_cache.json")
            log_p = os.path.join(ROOT, "qd_scan.log")
            snap_qd = _read_json(cache_p) if os.path.exists(cache_p) else None
            if snap_qd is None:
                # 首次：后台拉起扫描（venv 解释器；长跑纪律输出落文件）
                # WO-QT-20261020-064/CH-02：原硬编码 C:/Users/Administrator/...
                # 在本机（C:/Users/t）必然 FileNotFoundError——页面手动刷新也炸。
                # K-RELEASE-20261106（2026-11-07）：原「按 envs 目录判定、否则回落
                # Windows 解释器」在 Linux 上必然落回 C:/Users/... 路径；且 Popen
                # 无条件传 creationflags（Windows 专用）→ ValueError，实测线上
                # /qd 首次访问 500/连接重置、nginx 502。修法：一律用当前进程
                # 解释器（portal_api 本就跑在该 venv 内），平台参数按 os.name 分派，
                # 并把后台 spawn 做成 fail-soft：扫描起不来页面仍返回占位页。
                VENV_PY = sys.executable
                SCAN_PY = os.path.join(ROOT, "qd_scanner.py")
                if not os.path.exists(log_p) or time.time() - os.path.getmtime(log_p) > 1800:
                    _kw = {"stdin": _sp.DEVNULL}
                    if os.name == "nt":
                        _kw["creationflags"] = 0x00000008 | 0x00000200
                    else:
                        _kw["start_new_session"] = True  # POSIX 等价脱离终端
                    try:
                        with open(log_p, "ab") as lf:
                            _kw.update({"stdout": lf, "stderr": lf})
                            _sp.Popen([VENV_PY, SCAN_PY], **_kw)
                    except (OSError, ValueError) as _e:
                        # 后台扫描不可用 ≠ 页面不可用；留痕但不升级成 500。
                        with open(log_p, "ab") as lf:
                            lf.write(("spawn skipped: %s: %s\n"
                                      % (type(_e).__name__, _e)).encode("utf-8"))
                self._html(_qd_view.render_placeholder(log_p, self._COCKPIT_CSS))
                return
            # K-STOCK-028：当日基础数据快照（qd_daily_snapshot.py 14:55 产出）
            # 传入渲染——尾盘候选行据此补齐名称/涨跌/市值/换手/技术状态
            base_p = os.path.join(ROOT, "qd_daily_data", "snap_%s.json"
                                  % time.strftime("%Y%m%d"))
            base_qd = _read_json(base_p) if os.path.exists(base_p) else None
            self._html(_qd_view.render_workspace(snap_qd, self._COCKPIT_CSS,
                                                 base_qd))
        elif path == "/bt-news":
            self._bt_news(qs)
        elif path == "/bt":
            # K237 选股中心总目录：三产线选股中心导航——青萍之末的池子
            # 已迁 /bt-qpzm（多维加权选股工作台，四市场模块卡同源注入），
            # 本页只做产线导航与池子摘要
            import html as _h_bt
            e = _h_bt.escape
            snap = _read_json(os.path.join(REPORTS_DIR,
                                           "rps_latest.json")) or {}
            # K-STOCK-022b：双刀卡片动态统计（读 /qd/ 同源扫描缓存，缺省空）
            qd_snap = _read_json(os.path.join(ROOT, "qd_scan_cache.json"))
            res = snap.get("res", [])

            def _act(r):
                return str(r.get("action", "")).replace("（环境闸门）", "")
            n = len(res)
            nb = sum(1 for r in res if _act(r) == "买入")
            nw = sum(1 for r in res if _act(r) == "观察")

            def _card(href, border, icon, name, desc, stat):
                return ("<a href='%s' style='display:block;border:1px "
                        "solid %s;border-radius:12px;background:var(--gray-2);"
                        "padding:16px 18px;text-decoration:none;"
                        "color:var(--gray-7)'>"
                        "<div style='font-size:17px;font-weight:700'>"
                        "%s %s</div>"
                        "<div class='dim' style='font-size:12.5px;"
                        "margin:6px 0 10px'>%s</div>"
                        "<div style='font-size:13px'><b>%s</b>"
                        "<span style='float:right;color:%s;font-size:"
                        "12.5px'>进入选股中心 →</span></div></a>"
                        % (href, border, icon, name, desc, stat, border))
            cards = "".join([
                _card("/bt-buy", "#f85149", "🛒", "买点实战台 · 决策流",
                      "只做 1买/2买/3买（三卖不碰）· 环境分子决定动不动手 ·"
                      " 板块聚集 · 持仓盯市 · 绝对/超额双口径战绩",
                      "实战入口 · 一屏走完买入决策"),
                _card("/bt-qpzm", "#58a6ff", "🌊", "青萍之末 · 选股中心",
                      "四市场自选池 · 多维加权评分（技术.35/JEV.25/"
                      "RPS.25/环境.15）· 环境闸门一票否决",
                      "%d 只在池 · 买入 %d · 观察 %d" % (n, nb, nw)),
                _card("/bt-chanlun", "#e3b341", "🧭", "缠论 · 选股中心",
                      "多级别结构信号 · 三类买卖点 · 预警池 · 关键位标注"
                      "专项", "结构信号每日盘后生成"),
                _card("/qd/", "#3fb950", "⚔", "量子双刀 · 选股中心",
                      "双日十字星+MACD 多头 · 八重过滤+Regime 分层"
                      "（研究态：T-009 回测验证中）",
                      "当日信号 %d · 近5日 %d" % (
                          len((qd_snap or {}).get("signals_now", [])),
                          len((qd_snap or {}).get("signals_recent", [])))),
                # K-STOCK-033：AI 市场联动卡（聘请 AI 做商业化）
                _card("/aim", "#d29922", "🤖", "AI 市场",
                      "可聘 AI 岗位货架 · 田丰 · 参谋长 + 投研部 alpha 团队"
                      "（T1/T2/N1/F1/D1）+ 专项 AI · 本地模型零 token 成本",
                      "11 个岗位在岗 · 按岗聘请"),
            ])
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>选股中心 · 多产线</title>"
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("bt")
                    + "<h1>🎯 选股中心 · 多产线</h1><div class='dim'>"
                    "多 Agent 平台：各产线出的票归各自选股中心 · 快照 "
                    + e(str(snap.get("latest", "—"))) + "</div>"
                    + "<div style='display:grid;grid-template-columns:"
                    "repeat(auto-fit,minmax(280px,1fr));gap:12px;"
                    "margin:16px 0'>" + cards + "</div>"
                    + "<div class='dim' style='margin-top:10px'>历史量化"
                    "工具（四策略回测/寻优/板块/报告）保留于上方导航与各"
                    "产线工作台内 · 纸面研究不构成投资建议</div>"
                    "</body></html>")
            self._html(body)
        elif path.startswith("/bt/m/"):
            # 市场分域页（A股/美股/港股/加密，五块结构）
            # K237：跑批产物内旧称「量化中心」动态更名「选股中心」
            fn = {"cn": "bt_m_cn.html", "us": "bt_m_us.html",
                  "hk": "bt_m_hk.html", "crypto": "bt_m_crypto.html"}.get(
                      path[len("/bt/m/"):])
            if fn:
                fp = os.path.join(STATION, "quant_backtest",
                                  "reports", fn)
                try:
                    html_m = open(fp, encoding="utf-8").read().replace(
                        "量化中心", "选股中心")
                    self._html(html_m)
                except OSError:
                    self._html_file(fp)
            else:
                self._json({"error": "bad market"}, 400)
        elif path == "/premarket":
            # 盘前六层分析（tushare+JEV+zhice-db）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports", "premarket.html"))
        elif path == "/bt-tbe":
            # K154 tbe/pbe 影子分层完整报表（chanlun_tbe_stats.py 生成）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports",
                "chanlun_tbe_report.html"))
        elif path == "/col-audit":
            # K176 数据源列对账摘要（source_col_audit 防线报告，取最新）
            _cands = glob.glob(os.path.join(
                STATION, "quant_backtest", "reports",
                "col_audit_summary_*.html"))
            _latest = max(_cands, key=os.path.getmtime, default=None)
            if _latest:
                self._html_file(_latest)
            else:
                self._html(
                    "<body>%s<h1>📋 列对账</h1><div class='dim'>摘要不存在"
                    "——先跑 quant_backtest/source_col_audit.py"
                    "</div></body>" % pill_nav())
        elif path == "/fut":
            # K162-E F4 期货量化面板（FutQuant 每日 09:30 刷新数据）
            # K-STOCK-057 壳对齐全站 tokens（弃手写暗色 body）
            self._html(
                "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>期货量化</title>"
                + self._COCKPIT_CSS + "</head><body>"
                "<div style='max-width:1180px;margin:0 auto'>"
                + pill_nav("fut") + fut_page_html()
                + "</div></body></html>")
        elif path == "/sellpoint":
            # K208 SellPoint AI 卖点研判面板（SellPointAI 每日刷新数据）
            # K-STOCK-057 壳对齐全站 tokens（弃手写暗色 body）
            self._html(
                "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>SellPoint AI</title>"
                + self._COCKPIT_CSS + "</head><body>"
                "<div style='max-width:1180px;margin:0 auto'>"
                + pill_nav("sellpoint") + sellpoint_page_html()
                + "</div></body></html>")
        elif path == "/deliverables":
            # K-STOCK-057 对外快照档案（X5 公网快照落盘 deliverables/）：
            # 只读索引 + ?f= 单文件原文（basename 防穿越，仅 md/txt）
            import glob as _glob
            import html as _hes
            from urllib.parse import parse_qs as _pqs2, quote as _urlq

            def _he_es(s):
                return _hes.escape(s or "")

            ddir = os.path.join(os.path.dirname(ROOT), "deliverables")
            _fn = (_pqs2(qs).get("f") or [""])[0] if qs else ""
            if _fn:
                _bn = os.path.basename(_fn)
                if not _bn.endswith((".md", ".txt")):
                    return self._json({"error": "only md/txt"}, 403)
                _fp = os.path.join(ddir, _bn)
                if not os.path.isfile(_fp):
                    return self._json({"error": "not found"}, 404)
                with open(_fp, encoding="utf-8", errors="replace") as f:
                    return self._html(
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width,"
                        "initial-scale=1'><title>📄 " + _he_es(_bn)
                        + " · 青萍之末</title>"
                        + self._COCKPIT_CSS + "</head><body>"
                        + pill_nav("deliverables")
                        + "<h1>📄 %s</h1>"
                        "<pre style='white-space:pre-wrap;font-size:13px;"
                        "line-height:1.75'>%s</pre></body></html>"
                        % (_he_es(_bn),
                           _he_es(f.read())))
            files = []
            for fp in sorted(_glob.glob(os.path.join(ddir, "*.md")),
                             reverse=True):
                try:
                    st = os.stat(fp)
                    files.append((os.path.basename(fp), st.st_size,
                                  time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(st.st_mtime))))
                except OSError:
                    continue
            rows = "".join(
                "<tr><td><a href='/deliverables?f=%s'>%s</a></td>"
                "<td class='num'>%.1f KB</td><td class='dim'>%s</td></tr>"
                % (_urlq(n), _he_es(n), sz / 1024, mt)
                for n, sz, mt in files)
            self._html(
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>📦 对外快照 · 青萍之末</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("deliverables")
                + "<h1>📦 对外快照档案</h1>"
                "<div class='dim'>X5 公网快照落盘（deliverables/）· "
                "只读 · 共 %d 份</div>"
                "<div class='sec' style='margin-top:12px'>"
                "<table><tr><th>文件</th><th>大小</th><th>时间</th></tr>%s"
                "</table></div></body></html>"
                % (len(files), rows or
                    "<tr><td colspan='3' class='dim'>"
                    "暂无快照</td></tr>"))
        elif path == "/afterhours":
            # 盘后复盘（收盘全景/赚钱效应/预判对照）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports", "afterhours.html"))
        elif path.startswith("/bt/strategy/"):
            # 策略库纵切页（宽度/自选状态/回测/寻优）
            sn = path[len("/bt/strategy/"):]
            if sn in ("trend_follow", "ma_cross", "rsi_reversal",
                      "boll_breakout"):
                self._html_file(os.path.join(
                    STATION, "quant_backtest", "reports",
                    "bt_strat_%s.html" % sn))
            else:
                self._json({"error": "bad strategy"}, 400)
        elif path == "/api/research":
            # P-B2 个股按需深调：默认读当日缓存；force=1 重算（阻塞 10-90s）
            from urllib.parse import parse_qs as _pqr
            qp3 = _pqr(qs)
            import sys as _sysr
            if QBT_DIR not in _sysr.path:
                _sysr.path.insert(0, QBT_DIR)
            from research_agent import research_for_stock
            _rc = (qp3.get("c") or [""])[0]
            if not _rc:
                self._json({"ok": False, "err": "missing c"}, 400)
                return
            _rf = (qp3.get("force") or ["0"])[0] == "1"
            try:
                self._json(research_for_stock(
                    _rc, force=_rf, read_only=not _rf))
            except Exception as _re3:
                log_exc("GET /api/research")  # C2批1
                self._json({"ok": False, "err": str(_re3)[:200]}, 500)
        elif path == "/api/nlquery":
            # P-E 多 agent 自然语言查数：默认读当日缓存；force=1 现算（10-30s）
            from urllib.parse import parse_qs as _pqn, unquote as _uqn
            qp_nl = _pqn(qs)
            import sys as _sysn
            if QBT_DIR not in _sysn.path:
                _sysn.path.insert(0, QBT_DIR)
            from multi_agent import answer as _ma_answer
            _q = _uqn((qp_nl.get("q") or [""])[0])
            if not _q:
                self._json({"ok": False, "err": "missing q"}, 400)
                return
            _nf = (qp_nl.get("force") or ["0"])[0] == "1"
            # P1-7：force=1 频控（每 IP 每分钟 ≤3 次，超限 429）
            if _nf:
                _ip = self.client_address[0]
                _now = time.time()
                _hits = [t for t in _NLQ_FORCE_HITS.get(_ip, [])
                         if _now - t < 60]
                if len(_hits) >= _NLQ_FORCE_MAX_PER_MIN:
                    self._json({"ok": False, "err": "force=1 触发频控："
                                "每 IP 每分钟最多 %d 次，请稍后再试"
                                % _NLQ_FORCE_MAX_PER_MIN}, 429)
                    return
                _hits.append(_now)
                _NLQ_FORCE_HITS[_ip] = _hits
            try:
                self._json(_ma_answer(_q, force=_nf, read_only=not _nf))
            except Exception as _nle:
                log_exc("GET /api/nlquery")  # C2批1
                self._json({"ok": False, "err": str(_nle)[:200]}, 500)
        elif path == "/nl":
            # P-E 自然语言查数页：输入问句 → multi_agent 多 agent 答案
            self._nl_page(qs)
        elif path == "/bt/analyst-report":
            # K244 青萍之末分析师报告页（事实聚合 + LLM 大白话 + 飞书一键）
            self._analyst_report_page(qs)
        elif path == "/bt/subscribe":
            # K245 订阅推送管理页（订阅清单 + 接收人 + 试推）
            self._subscribe_page(qs)
        elif path == "/btview":
            # K-STOCK-277 回测中心（逐日账本/月历热力/AI决策流水，展示借智策 OS v2 形态）
            self._html_file(os.path.join(ROOT, "bt_view.html"))
        elif path == "/bt2view":
            # K-STOCK-285b 回测框架 v2（参数表单+两策略对照+逐日日历复盘）
            self._html_file(os.path.join(ROOT, "bt2_view.html"))
        elif path == "/flow":
            # F1 动作流中心（统一事件流聚合/预览/重跑 + JEV 健康条）
            self._html_file(os.path.join(ROOT, "flow.html"))
        elif path == "/paper":
            # K-STOCK 模拟仓前端（K279 产出 paper.html；nginx 8080 同文件
            # 静态直出，此处读盘直出保双口径一致——K-STOCK-042 修正过时占位）
            _pf = os.path.join(ROOT, "paper.html")
            if os.path.exists(_pf):
                try:
                    with open(_pf, encoding="utf-8") as f:
                        self._html(f.read())
                except Exception as e:   # 读盘异常兜底占位，不让线程裸断连
                    self._html("<!DOCTYPE html><html><head><meta charset="
                               "'utf-8'></head><body>" + pill_nav("paper") +
                               "<div class='sec'>前端文件读取异常：%s</div>"
                               "</body></html>" % e)
            else:
                self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                           "<title>模拟仓 · 建设中</title>" + self._COCKPIT_CSS
                           + "</head><body>" + pill_nav("paper")
                           + "<h1>🧾 模拟仓（K-STOCK 线）</h1><div class='sec'>"
                           "建设中：数据层已就绪（/api/paper_status）。</div>"
                           "</body></html>")
        elif path == "/bt/stock":
            # 个股全景页（行情+策略信号+综合分分解+JEV下钻+AI展望）
            self._bt_stock_page(qs)
        elif path == "/bt/report":
            # 研报本地全文页（结构化库直读，零外链）
            self._bt_report_page(qs)
        elif path == "/bt/ind_drill":
            # 行业下钻页：成分股涨跌全量 + 主力资金个股明细
            self._bt_ind_drill_page(qs)
        elif path == "/bt/lhb":
            # 龙虎榜下钻页：当日上榜全量 + 频次 + 板块扎堆
            self._bt_lhb_page(qs)
        elif path == "/bt/seat":
            # 坐席下钻页：席位近60日上榜明细 + 常做票
            self._bt_seat_page(qs)
        elif path == "/api/audit_recent":
            # K-STOCK-114e 快捷审计素材：最近一次提交 diff（截断 12k）
            try:
                r = subprocess.run(
                    ["git", "log", "-1", "--format=%h %s%n%an %ad",
                     "--date=iso"], cwd=ROOT, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=10)
                d = subprocess.run(
                    ["git", "show", "--format=", "--unified=3", "HEAD"],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15)
                diff = (d.stdout or "")[:12000]
                self._json({"ok": True, "head": (r.stdout or "").strip(),
                            "diff": diff,
                            "truncated": len(d.stdout or "") > 12000})
            except Exception as _ae:
                self._json({"ok": False, "error": repr(_ae)}, 500)
        elif path in route_table._GET_EXACT_9:
            getattr(self, route_table._GET_EXACT_9[path])()
        elif path == "/approvals":
            # M1 审批注册表页（数据走 /api/platform/approvals 只读面）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "approvals.html"))
        elif path == "/capability":
            # M3-P5 能力看板页（数据走 /api/capability/board）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "capability.html"))
        elif path == "/perf":
            # M4-P5 投研绩效看板页（数据走 /api/perf/board 与 /api/perf/replay）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "perf.html"))
        elif path == "/api/acad1/pool":
            # WO-INV-POOL-UI：ACAD1 推荐池全池复盘只读数据口
            # ACAD1-FULLVIEW（2026-11-04）：仅透传 query 供 ?view=full 只读聚合视图；
            # 缺省行为不变（单日视图），不改路由集合。
            self._json(pool_snapshot(query=self.path.split("?", 1)[1]
                                     if "?" in self.path else ""))
        elif path == "/acad1/pool":
            # WO-INV-POOL-UI：ACAD1 推荐池全池复盘只读页
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "acad1_pool.html"))
        elif path == "/api/quant/paper":
            # WO-INV-PAPER-UI：量化部 paper 组合只读数据口（不接实盘）
            self._json(paper_snapshot())
        elif path == "/quant/paper":
            # WO-INV-PAPER-UI：量化部 paper 组合只读页（不接实盘）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "quant_paper.html"))
        elif path == "/api/d1/research":
            # WO-D1-RESEARCH：D1 投研工作台只读数据口（池内研究，不管理策略）
            # IFS F1b：叠统一 envelope + request_id（WO-IFS-ENVELOPE-20261104）
            import d1_data
            import ifs_envelope
            self._json(ifs_envelope.ok_payload(self, "d1.research.get",
                                               d1_data.d1_snapshot()))
        elif path == "/api/d1/decisions":
            # WO-D1-UI-20261101：D1 正式裁决只读列表（页面展示/回填）
            import api_d1_decision
            api_d1_decision.handle_get(self, self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
        elif path == "/api/d1/handoffs":
            # WO-D1-HANDOFF-20261101：D1 交棒回执只读列表
            import api_d1_handoff
            api_d1_handoff.handle_get(self, self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
        elif path == "/api/d1/watch-plans":
            # IFS F3：handoff v2 → watch_plan 只读视图（不触发、不执行）
            import api_d1_handoff
            api_d1_handoff.handle_get(self, self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
        elif path == "/api/d1/training/episodes" or path.startswith("/api/d1/training/episodes/"):
            # IFS F5：D1 training episodes 只读（不训练/不写盘；缺数据诚实 data_unavailable）
            import api_d1_training
            api_d1_training.handle_get(self, self.path.split("?", 1)[1]
                                       if "?" in self.path else "")
        elif path == "/api/d1/meeting":
            # WO-D1-MEETING-20261101：D1 投研会读取（无 id 时 summary+list）
            import api_d1_meeting
            api_d1_meeting.handle_get(self, self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
        elif path in ("/d1/research", "/d1_research.html"):
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "d1_research.html"))
        elif path in ("/d1/war-room", "/d1_war_room.html"):
            # WO-WARROOM-UI：量化作战台整合版（策略池/投研/盯盘/模拟交易）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "d1_war_room.html"))
        elif path in ("/d1/training", "/d1_training.html"):
            # IFS F6b：D1 训练面板只读页（数据走 /api/d1/training/episodes，只读不训练）
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "d1_training.html"))
        elif path == "/api/war-room/snapshot":
            # WO-WARROOM-LIVE：作战台实时数据聚合（D1/池/paper/确认/盯盘）
            # IFS F1d：统一 envelope + request_id（WO-IFS-WARROOM-ENVELOPE-20261104）
            import war_room_data
            snapshot_payload, snapshot_status = war_room_data.snapshot_envelope(self)
            self._json(snapshot_payload, snapshot_status)
        elif path == "/api/sim/confirmations":
            # IFS F4：人工确认待办（v2 四 ID 视图，paper-only）
            import api_sim_confirm
            self._json(api_sim_confirm.list_payload(
                self.path.split("status=")[-1]
                if "status=" in self.path else "pending"))
        elif path in ("/sim/confirm", "/sim_confirm"):
            self._html_file(os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "sim_confirm.html"))
        elif path == "/api/devops":
            # K-STOCK-113 开发指挥台数据口：dev_events.jsonl 只读投影
            self._json(self._devops_data())
        elif path in route_table._GET_EXACT_10:
            getattr(self, route_table._GET_EXACT_10[path])()
        elif path == "/portal":
            # K-STOCK-035 总入口门户：哪里是什么、我的入口在哪里
            # K-STOCK-181 fail-loud 化：此分支异常曾以「裸断连+stderr 无处寻」
            # 形态存在 6 小时（_HERMES_UP_CACHE NameError），不进 C:\Temp 日志
            try:
                self._portal_page()
            except Exception:
                log_exc("portal-page")
                try:
                    self._json({"ok": False, "error": "portal internal (see err log)"}, 500)
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        elif path in route_table._GET_EXACT_11:
            getattr(self, route_table._GET_EXACT_11[path])()
        elif path == "/api/org":
            # K-ORG-190 Agent 指挥台数据面（org.html bundle，此前 404 断粮已修）
            try:
                self._json(org_command_api.api_org())
            except Exception:
                log_exc("org-command api_org")
                self._json({"ok": False, "error": "org internal (see err log)"})
        elif path == "/api/org/meta":
            # K-ORG-190 指挥台元数据面（模型档位表+档位策略+技能索引，前端下拉/校验用）
            try:
                self._json(org_command_api.api_meta())
            except Exception:
                log_exc("org-command api_meta")
                self._json({"ok": False, "error": "org meta internal (see err log)"})
        elif path == "/api/agent/knowledge":
            try:
                from urllib.parse import parse_qs as _pqs_k
                q = _pqs_k(qs, keep_blank_values=True)
                self._json(org_command_api.api_agent_knowledge(q))
            except Exception:
                log_exc("org-command knowledge")
                self._json({"ok": False, "error": "knowledge internal"})
        elif path in route_table._GET_EXACT_12:
            getattr(self, route_table._GET_EXACT_12[path])()
        elif path == "/algo":
            # K-STOCK-087 算法商品市场：试看样例+本地卡密订阅解锁
            self._algo_market_page(qs)
        elif path == "/bt-qpzm" or path.startswith("/bt-qpzm/"):
            # 策略市场 1 号工作台：青萍之末 · 多维加权选股
            # K-STOCK-032 多页面联动：/bt-qpzm 主页驾驶舱 +
            # /bt-qpzm/{res,rps,hub,batch,spec} 子页
            self._qpzm_page(path[len("/bt-qpzm"):].strip("/"))
        elif path in route_table._GET_EXACT_13:
            getattr(self, route_table._GET_EXACT_13[path])()
        elif path == "/bt-buy":
            # 🛒 买点实战台：只做 1买/2买/3买（三卖不碰），实战决策流一屏到底
            self._chanlun_buy_page(qs)
        elif path == "/bt-chanlun-pool":
            # 缠论扫描 → 预警池 → 模拟交易（影子观察；?m=US|HK 切市场池链）
            self._chanlun_pool_page(qs)
        elif path == "/chanlun-png":
            # K72: 当日缠论结构图 PNG（{c}_1d_*.png 最新 / 现场兜底生成）
            self._chanlun_png(qs)
        elif path == "/chanlun-anno":
            # 缠论图手工标注（GET 列表 / POST 增删，出图时叠加）
            self._chanlun_anno(qs)
        elif path in route_table._GET_EXACT_14:
            getattr(self, route_table._GET_EXACT_14[path])()
        elif path == "/bt-linkage":
            # 大盘-板块-个股 三线联动复盘（TDX 880xxx 板块口径，影子观察）
            self._linkage_page(qs)
        elif path == "/bt-linkage-png":
            from urllib.parse import parse_qs as _pql
            self._linkage_png(_pql(qs))
        elif path in route_table._GET_EXACT_15:
            getattr(self, route_table._GET_EXACT_15[path])()
        elif path == "/api/kline":
            # S1 个股K线 JSON（四市场日线+MA+A股结构信号+JEV标注）
            from urllib.parse import parse_qs as _pqk, unquote as _uqk
            qp = _pqk(qs)
            try:
                self._json(_kline_data(
                    _uqk((qp.get("c") or [""])[0]).strip()[:24],
                    (qp.get("n") or ["250"])[0]))
            except Exception as ex:
                log_exc("GET /api/kline")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/kline_qy":
            # K200 季/年线 JSON（深历史 datamart 两段拼接 + 服务端聚合结构；
            # 懒加载独立端点，前端点击季/年按钮才拉取）
            from urllib.parse import parse_qs as _pqkq, unquote as _uqkq
            qp = _pqkq(qs)
            try:
                self._json(_kline_qy(
                    _uqkq((qp.get("c") or [""])[0]).strip()[:24]))
            except Exception as ex:
                log_exc("GET /api/kline_qy")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/analyst-report":
            # K244 青萍之末分析师报告（事实聚合 + LLM 大白话，异步用）
            self._api_analyst_report(qs)
        elif path == "/api/chips":
            # S5 筹码分布瀑布 JSON（cyq_chips 单日按需+parquet 缓存）
            from urllib.parse import parse_qs as _pqc, unquote as _uqc
            qp = _pqc(qs)
            try:
                self._json(_chips_data(
                    _uqc((qp.get("c") or [""])[0]).strip()[:24],
                    (qp.get("d") or [""])[0],
                    (qp.get("x") or [""])[0]))
            except Exception as ex:
                log_exc("GET /api/chips")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/bridge/summary":
            # K56-B1 驾驶舱聚合（五仪数据一次拉齐，只读）
            try:
                self._json(bridge_summary())
            except Exception as ex:
                log_exc("GET /api/bridge/summary")  # C2批1
                self._json({"error": str(ex)}, 500)
        elif path == "/api/bridge/table_preview":
            # K56 数据穿透：白名单表最新 n 行（驾驶舱/数据仓 🔍 展开）
            from urllib.parse import parse_qs as _pqs2
            _q2 = _pqs2(qs or "")
            _tb2 = (_q2.get("table") or [""])[0].strip()[:40]
            try:
                _nn2 = int((_q2.get("n") or ["8"])[0])
            except Exception:
                _nn2 = 8
            self._json(bridge_table_preview(_tb2, _nn2))
        elif path == "/api/data/freshness":
            # 数据底座时点校对（表/最新/期望/滞后/状态）
            try:
                self._json(data_freshness())
            except Exception as ex:
                log_exc("GET /api/data/freshness")  # C2批1
                self._json({"error": str(ex)}, 500)
        elif path == "/api/data/sync":
            # 手动触发同步（数据仓页按钮）：?src=tdx|tushare|kg|catalog
            from urllib.parse import parse_qs as _pqs
            src = (_pqs(qs).get("src") or [""])[0]
            self._json(data_sync_start(src))
        elif path == "/api/data/job":
            # 同步任务轮询：运行中? + 日志尾部
            from urllib.parse import parse_qs as _pqj
            src = (_pqj(qs).get("src") or [""])[0]
            self._json(data_sync_status(src))
        elif path == "/api/batch-backtest":
            # K255 自定义批次回测轮询：报告就绪则整体带出（前端 3s 轮询）
            self._json(_bt_custom_status())
        elif path == "/api/alert/push":
            # 手动触发飞书预警推送（/intel 卡「立即推送」按钮，零JS）
            # 复用 data_sync 单实例后台模式；触发后跳回 /intel 看状态
            r = data_sync_start("alertpush")
            if "error" in r and r.get("error") == "bad src":
                self._json(r, 500)
            else:
                self.send_response(302)
                self.send_header("Location", "/intel")
                self.send_header("Content-Length", "0")
                self.end_headers()
        elif path == "/screen":
            # K56-B4 数据大屏：统一 serve screen.html 文件版（与 nginx 8080
            # 同源；旧内嵌 _screen_page 保留作文件缺失时的兜底）
            try:
                _sp = os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "screen.html")
                with open(_sp, "r", encoding="utf-8") as _sf:
                    self._html(_sf.read())
            except Exception:
                log_exc("GET /screen 文件版读取失败，兜底内嵌渲染")  # C2批1
                self._screen_page()
        elif path in route_table._GET_EXACT_16:
            getattr(self, route_table._GET_EXACT_16[path])()
        elif path == "/bt-rps":
            # 旧入口 → A股市场页（保留飞书旧卡片按钮可用）
            self.send_response(301)
            self.send_header("Location", "/bt/m/cn")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/bt-report/img/"):
            # 报告页图表 PNG（matplotlib 生成，白名单防穿越）
            import re as _re2
            fn = path[len("/bt-report/img/"):]
            if _re2.fullmatch(r"[0-9A-Za-z_.\-]+\.png", fn):
                p = os.path.join(STATION, "quant_backtest", "reports",
                                 "charts", fn)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self._json({"error": "not found"}, 404)
        elif path == "/bt-report":
            # 飞书卡片「回测报告」按钮目标页：quant_backtest/reports/report.html
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "report.html"),
                            nav_key="report")
        elif path.startswith("/bt-report/"):
            # 按次快照页（卡片按钮直达该次报告）：白名单字符校验防路径穿越
            fn = path[len("/bt-report/"):]
            import re as _re
            if _re.fullmatch(r"[0-9A-Za-z_.\-]+\.html", fn):
                self._html_file(os.path.join(STATION, "quant_backtest",
                                             "reports", "snap", fn))
            else:
                self._json({"error": "bad name"}, 400)
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    try:
        # P0-1a（2026-10-08 全检修复）：绑定收紧为 127.0.0.1。
        # 原 0.0.0.0 使局域网任意设备可直连 8091 调全部写端点（与 SYSTEM
        # 计划任务叠加构成 LAN→SYSTEM 提权链）。收绑定后局域网只能经
        # ai-portal nginx(8080) 反代进入；Docker Desktop 的
        # host.docker.internal 由用户态代理转发，宿主机侧仍落在 127.0.0.1，
        # 故 nginx.conf 无需改动反代目标（nginx.conf 中已注明此机制）。
        # 生效需重启 portal API；重启后验证：
        #   docker exec ai-portal curl -s -o /dev/null -w '%{http_code}' \
        #     http://host.docker.internal:8091/health   # 期望 200
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        raise SystemExit(0)  # 已有实例在跑，静默退场（计划任务重复触发场景）

    def _warm_freshness():
        # 预热数据时点（15 表现算 ~25-45s）：避免重启后首个用户请求
        # 撞上冷算，超过 nginx /api/ 40s 代理超时
        try:
            _freshness_warmup()
            print("[warmup] freshness ok", flush=True)
        except Exception as ex:
            log_exc("warmup:freshness 预热失败")  # C2批1
            print("[warmup] freshness fail:", ex, flush=True)

    threading.Thread(target=_warm_freshness, daemon=True).start()
    srv.serve_forever()
