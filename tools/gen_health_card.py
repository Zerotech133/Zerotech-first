#!/usr/bin/env python3
"""gen_health_card.py — 治理健康卡（只读，零第三方依赖）。

聚合治理信号，输出一张机器可消费的 JSON（默认 stdout）与可选的自包含
HTML（--out DIR）。除第 9 条的一次短生成探测外，本脚本只读：不扫描计划任务、
不触发备份、不改仓库。

数据来源（均以实读为准，读不到就报 available=false，绝不静默当 0）：
1. task_exit_audit 的账本/摘要（默认 <repo>/portal/data/task_exit_audit/，
   路径与字段直接取自 agent_platform/tools/task_exit_audit.py）：
   账本 task_exit_ledger.jsonl 每条含 at/name/state/last_result/
   exit_semantics/last_run/next_run；摘要 task_exit_summary.json 含
   generated_at/total_scanned/ok/failed/disabled/never_run/failures。
   本卡按账本落盘时间切分扫描会话、取最后一个会话自行计数，并用摘要做
   一致性交叉核对；
   EXIT_SEMANTICS 通过只读 import 复用（禁止复制一份口径）。
2. 备份仓 <vault-root>/<yyyy-MM-dd>/MANIFEST.txt 与日期目录 mtime；
   manifest 行格式：`<name>  <ts>  <bytes> bytes  sha256=<hex>`；
   并用 `git bundle list-heads` + `git rev-parse HEAD` 检查备份包是否追平
   （落后 >0 黄；落后且备份 >24h 红；无法判定计 null，不误判绿）。
3. 仓库卫生：git status --porcelain 行数、git worktree list 数量、
   .worktrees/ 下的目录数（与 worktree list 交叉核对）。
4. worktree TTL（ROADMAP 规则 9）：git worktree list --porcelain 取关联工作树，
   用「分支是否已合并进 master/main」+「分支最后提交时间」判 7 天 TTL；
   已合并且 >7 天计 violation 判黄（不危险但违反规则），读不到红。
5. 文档索引新鲜度：子进程跑 `python -B tools/gen_docs_index.py --check`
   （20s 超时，另设 3s 设计预算）；退出 0 绿 / 1 黄（索引落后）/
   超时或退出 2 或脚本缺失红。差异行尽量解析，解析不了给原始输出前 200 字并标注未解析。
6. 数据根漂移：<vault-root>/<yyyy-MM-dd>/data_manifest.json（tools/gen_data_manifest.py
   的产物，含 total_files/total_bytes/dirs[]/catalog.sha256/root）按日期目录名倒序
   取最新两期，比对总文件数、总字节、顶层目录集合、catalog.json sha256 与数据根路径。
   只有一份清单 → available=false（不判绿）；两份都读不到/解析失败 → 红；
   文件数变化 >20% 或顶层目录数变化 → 升级为红（这个幅度通常是事故，不是日常增量）。
7. 明文私钥扫描（security.plaintext_keys）：只扫限定范围——<repo>/outputs、
   <repo>/tools、<repo>/portal 递归，加工作区根一级 *.pem/*.key/id_rsa*/.env*；
   跳过 .git/node_modules/.venv/.worktrees（另跳 __pycache__ 等编译缓存）。
   识别只看文件名与内容特征：*.pem/*.key/id_rsa*/.env* 内容含 PRIVATE KEY 字样，
   或任意文件内容含 PEM 私钥起始行；不解析密钥格式、不回显任何内容，命中只报
   「路径 + 字节数 + 是否被 git 跟踪」（ls-files --error-unmatch）。
   命中 ≥1 红；有目录/文件读不到黄（不判绿）；扫描 >3s 计 available=false 红。

8. 整理层新鲜度（data.curated_freshness）：<repo>/quant_backtest/data/tushare_curated/
   是 tools/refresh_curated.py 从 data/tushare/ 派生的整理层（51 张表 + 3 侧车），
   纯派生，自己不会跟着原始层变新。取整理层最旧 parquet 的 mtime 与原始层递归
   最新 parquet 的 mtime 比落后天数，另按 51 个数据集逐个比（报落后的数据集数量），
   判定取两者更差者。只看 mtime（scandir 目录项缓存，不打开文件、不读 footer）。
   预算默认 20s（ZB_HC_CURATED_BUDGET_S 可调）；成功结果缓存 2h
   （ZB_HC_CURATED_CACHE / ZB_HC_CURATED_CACHE_TTL_S），ZB_HC_CURATED_FORCE=1 强制重扫。
   落后 <=2 天绿 / >2 天黄 / >14 天红；整理层不存在或为空 → available=false 黄（不判绿）；
   原始层不存在/无 parquet → available=false 红；**遍历超预算 → 黄（不判绿、也不判红：
   超预算只说明扫描慢，不说明数据过期）；有缓存则用缓存兜底并在 detail 明确标注。**

9. 本地推理能力（ai.inference）：这张卡原来 21 项全在管任务/备份/数据/仓库，
   产品本体（AI 推理）零监控，而推理故障全是静默的——上下文截断不报错、思考
   模型返回空串不报错、通道死了只有调用时才 500、变慢只是「很慢」。本项只测
   qwen3.5:9b 一个模型（全量三模型探针在 tools/probe_llm_capability.py，约 2
   分钟，不进健康卡）：
   ① GET /api/tags（3s 超时）判 Ollama 可达，并确认 9b 已安装；
   ② GET /api/ps（3s 超时）看 9b 是否已驻留——未驻留只判黄并跳过生成，
      绝不为「测一下」把模型冷加载起来（那是几秒到十几秒的额外代价）；
   ③ 已驻留才 POST /api/chat：think=false、num_ctx=16384、num_predict=16、
      temperature=0，问「只回答一个字：好」，20s 超时；记录 message.content
      长度、thinking 长度、/api/ps 里该模型的 context_length、以及
      eval_count÷eval_duration 吞吐。整项预算 30s。
   这是全卡唯一的写动作：那条 16 token 的短生成会在 num_ctx 不匹配时让 Ollama
   以 16384 重载 9b——只观测、不刻意造数，其余一切仍是只读。
   全部请求经 ProxyHandler({}) 直连：系统代理会劫持 127.0.0.1，把「本机没开
   服务」伪装成「代理连不上」。

10. 机器挂起（ops.machine_suspend）：2026-10-24 发现 6 条互不相干的日志在同一
    分钟内集体断档（watchdog 120 分钟、k266_services 100 分钟、portal_api 120
    分钟…）。根因不是调度器、不是某个守护脚本，是**机器在睡 / 掉电**——应用层
    日志醒来就继续写，看不出中间那段空洞，于是「机器没开」被误读成「任务失败」。
    本项读 Windows 系统事件日志（结构化 ID，不解析本地化文案）：
    ① Kernel-General 1 且消息含毫秒增量：系统时间被硬件钟纠正的毫秒数，就是本次
       挂起的真实时长（>60s 才计，避免把普通 NTP 微调算成挂起）——这是唯一能给出
       「睡了多少」的直接读数；
    ② Kernel-Power 42：进入睡眠次数；
    ③ Kernel-Power 41 / EventLog 6008：意外关机（无 BugCheck 的掉电/硬复位）。
    判级：有意外关机 / 今日累计挂起 >60 分钟 → 红；>10 分钟 → 黄；
    **查询失败/权限不足/超时 → 黄（不可判定，非故障；detail 写明原因与重试方式）**；
    无事件也是 0 次（可判绿，但要写明「今日无挂起」）。本项纯只读，不启停服务、不改电源设置。
    （2026-10-24 21:30 修订：本机是开发笔记本，睡眠/断电属使用方式，**改为一律只报黄**；
    2026-10-26 EXEC-R32：与整理层同哲学——查询失败只说明不可判定，不是故障，降黄不判红。）

11. 推理错误率（ai.error_rate）：读 Ollama 自己写的 GIN 访问日志（尾部 8 MB），
    统计最近 60 分钟 2xx/3xx、4xx、5xx 的分布。建项起因：2026-10-24 日志里出现
    每分钟 25–30 次 `POST /api/chat` 404，而业务侧被「降级三态/规则兜底」吞掉，
    健康卡上完全看不见——失败在，信号没了。判级：有真 server 5xx → 红；失败率
    >20% 红、>5% 黄；样本 <20 不判绿（读不到就是没看见）；窗口内 0 请求 → 绿并写明 0。
    2026-10-25 EXEC-R6 口径拆分：GIN 里「客户端主动断开」的 500（前邻
    `aborting completion request due to client closing the connection`，或耗时≈20s——
    旧 news_ai.jev_reason 的 20s 超时/M3 批处理排队所致）单列 `client_aborts`，
    **不计入 server_errors**、不计失败率；无真 5xx 但存在 client_aborts 时判黄提示核对
    调用方超时，避免「客户端自己走了」被当成「服务端坏了」长期误红。

12. 智能体点名册（agent.census）：复用 `tools/agent_census.py --json`（单一真相源，
    本卡不重复实现口径），把四本账（编制/事件/产出/工单）的结论收成一行：
    在册几席、干活几个、有单未动几个、待命几个、静默占比、压单席位。
    判级：有单未动 ≥3 席 → 红；1–2 席或静默占比 ≥70% → 黄；否则绿。
    读不到/子进程失败 → 红（不判绿）。

13. 每日工作项（agent.daily）：复用 `tools/agent_daily.py --json`，
    读 `agent_platform/registry/daily_work.json`（事项目录），按频率判定"今天该做的
    事做了没有"，证据只认三类硬事实：产物文件新鲜度 / 日志成功标记 / 审计账本动作。
    判级：有未交付 → 红；只有待接线 → 黄；全部交付或今日无到期 → 绿。
    没有可检查证据的事**不收录**（type=manual 的标"待接线"，既不算过也不算欠）。

14. 夜间批处理积压（ops.batch_backlog）：读两份日志——白天 `C:/Temp/night_batch.log`
    （START/ROUND/`JEV rc=`/`XQ rc=`）与夜间 `C:/Temp/jev_night.log`（JEV 独占跑批
    START/ROUND/`JEV rc=`），输出两条队列积压、最近 rc、白天/夜间 JEV 吞吐与合并
    日容量、按日容量折算的墙钟 ETA。JEV 容量=白天会话容量+夜间窗口容量；XQ 只在白天。
    积压较上一轮上升 → 红；连续 rc=124 ≥2 轮 → 红；仅 1 轮 rc=124、ETA >48h 或两份
    日志合计 >3h 无新轮次 → 黄；ETA >96h → 红；两日志都读不到/无 ROUND → 红（不判绿）。
    建项起因：2026-10-25 白天 6 轮 JEV rc=124、每轮仍推进 ~100 条；2026-10-26
    EXEC-R32 起纳入夜班 jev_night（此前夜间吞吐不计入，ETA 偏高/误判无新轮次）。

15. 数据底座健康（2026-10-26 OS-FIX-DATAHEALTH）：三项"静默缺口"巡查——
    · data.shibor_lpr_freshness：整理层 `shibor_lpr.parquet` 日期列
      （cal_date/date）max 距今天数，>14 天黄 / >60 天红；文件缺失/读失败/列名
      不匹配 → 黄（不判绿）。起因：文件 mtime 新、内容两个月前，mtime 口径看不见。
    · data.tdx_manifest：`quant_backtest/data/tdx` 用 os.walk 统计体积/文件数
      （10s / 5 万文件预算，超限报未完成不判绿）；>1 GiB 且不在最新
      `<vault>/<date>/data_manifest.json` 的 dirs[].name/dirs[].path/tables/paths
      中 → 黄「未纳管」；<阈值或目录不存在 → 绿/不适用；manifest 不可读 → 黄。
    · data.trade_cal_dedup：整理层 `trade_cal.parquet` 的 cal_date 单列，
      rows/distinct >1.01 → 黄（detail 写 rows/distinct/倍数）；读失败 → 黄。
    三项读 parquet 时惰性 import pyarrow（不可用判黄、不判绿）；其余检查仍零依赖。

用法：
    python -B tools/gen_health_card.py
    python -B tools/gen_health_card.py --vault-root C:/项目/_vault
    python -B tools/gen_health_card.py --out C:/项目/_hc_probe

退出码：0=绿/黄（可容忍），1=红（有硬问题），2=脚本自身错误（参数/写盘）。
阈值：备份 >36h 黄 / >72h 红；审计 >24h 黄 / >72h 红；dirty>0 黄；
工作树目录与 git worktree 不一致黄；worktree 已合并且 >7 天黄；
文档索引落后黄；任一块不可用红；备份落后 HEAD >0 黄 /
落后且备份 >24h 红；追平无法判定计 null（不误判绿）；数据根漂移只有一份清单黄
（不判绿）、两份都读不到红、文件数变化 >20% 或目录数变化红；
明文私钥命中 ≥1 红、有目录/文件读不到黄、扫描超 3s 红；
整理层落后 >2 天黄 / >14 天红、整理层缺失或为空黄（不判绿）、
原始层缺失或遍历超 5s 红。
本地推理：Ollama 不可达红、9b 未安装红、读不到 /api/ps 黄、未驻留黄（不判绿）、
content 为空红、答案不含「好」黄、驻留 ctx≠16384 黄、吞吐 <20 tok/s 黄、
请求 20s 未返回黄；读不到的指标一律写进 detail，绝不静默当 0 也不判绿。
夜间批处理积压（ops.batch_backlog）：白天 night_batch + 夜间 jev_night 合并口径，
积压较上一轮上升红；连续 rc=124 ≥2 轮红；仅 1 轮 rc=124 黄；ETA >48h 黄 / >96h 红；
两份日志合计 >3h 无新轮次黄；两日志都读不到红。
交易日历（2026-10-26 OS-FIX-MEASURE2）：`_is_non_trading_day` 读
`portal/data/trade_cal_open_days.json`（schema=trade_cal_open_days/v1，
open_days=SSE is_open=1 的 YYYY-MM-DD 列表；`ZB_TRADE_CAL` 可覆盖路径；缺失/坏
回退 `weekday>=5`，绝不抛异常）。它只影响 alert_watch 黄项的"可能属预期"文案，
**不参与判绿/判红**——无法证明的日子不拿来给故障开脱。
"""
from __future__ import annotations
import logging

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCHEMA = "governance_health_card/v1"

# 与 task_exit_audit.py 的输出路径保持同源（该文件 DATA_DIR 的相对位置）。
AUDIT_MODULE_REL = Path("agent_platform") / "tools" / "task_exit_audit.py"
AUDIT_DATA_REL = Path("portal") / "data" / "task_exit_audit"
LEDGER_NAME = "task_exit_ledger.jsonl"
SUMMARY_NAME = "task_exit_summary.json"

BACKUP_YELLOW_HOURS = 36.0
BACKUP_RED_HOURS = 72.0
# 备份仍在、只是旧：落后 HEAD 记黄；落后且超过 24h 未重备才红。
CATCHUP_RED_HOURS = 24.0
AUDIT_YELLOW_HOURS = 24.0
AUDIT_RED_HOURS = 72.0

# ROADMAP 规则 9：已合并且无未提交改动的工作树，合并后 7 天内必须 remove。
WORKTREE_TTL_DAYS = 7.0
WORKTREE_TTL_DETAIL_LIMIT = 5
WORKTREE_TTL_ITEM_LIMIT = 20
# 单次 git 调用上限；正常 worktree list/for-each-ref 都是毫秒级。
WORKTREE_TTL_GIT_TIMEOUT_SECONDS = 5.0
# 两项新增检查合计 3s 预算；超出按不可判定处理，绝不拖死健康卡。
WORKTREE_TTL_BUDGET_SECONDS = 3.0
WORKTREE_TTL_BRANCH_CANDIDATES = ("master", "main")

DOCS_INDEX_SCRIPT_REL = Path("tools") / "gen_docs_index.py"
DOCS_CHECK_TIMEOUT_SECONDS = 20.0
DOCS_CHECK_BUDGET_SECONDS = 3.0
DOCS_OUTPUT_EXCERPT_CHARS = 200
# gen_docs_index.py --check 的固定汇总行：
# 差异合计：新增 N / 删除 N / 标题变化 N / 日期变化 N / 节标题变化 N / 节说明变化 N
DOCS_TOTALS_RE = re.compile(
    r"差异合计：新增\s*(?P<added>\d+)\s*/\s*删除\s*(?P<removed>\d+)\s*/"
    r"\s*标题变化\s*(?P<title>\d+)\s*/\s*日期变化\s*(?P<date>\d+)\s*/"
    r"\s*节标题变化\s*(?P<heading>\d+)\s*/\s*节说明变化\s*(?P<note>\d+)"
)
DOCS_DIFF_LABELS = (
    ("added", "新增"),
    ("removed", "删除"),
    ("title", "标题变化"),
    ("date", "日期变化"),
    ("heading", "节标题变化"),
    ("note", "节说明变化"),
)

# 与 tools/backup_bundles.ps1 的 $repos 一一对应（label = bundle 文件名前缀）。
CATCHUP_REPO_DIRS: tuple[tuple[str, str], ...] = (
    ("aios", "老田的AI助手"),
    ("zerobound-kg", "zerobound-kg"),
    ("digital-legion", "digital-legion"),
    ("res-downloader", "res-downloader-py"),
)
CATCHUP_REF_CANDIDATES = ("refs/heads/master", "refs/heads/main", "HEAD")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")

UNKNOWN_SEMANTICS = "未知退出码"
NEVER_RUN_CODE = 267011
RUNNING_CODE = 267009
# 2026-10-25 K-ORG-20261025-TASKMETA：以下返回码不是服务失败，不计入 failed。
# 与 agent_platform/tools/task_exit_audit.py 的 NON_FAILURE_RC 同口径。
NON_FAILURE_TASK_CODES = frozenset({
    3,            # 上游不可信/主动拒跑（exit 3）
    267009,       # 0x41001 上一轮实例仍在跑（IgnoreNew 并发保护）
    267014,       # 0x41306 被强制终止（超时/手动结束/关机打断）
    1073807364,   # 0x40010004 DBG_TERMINATE_PROCESS（Task Scheduler 终止）
    2147946720,   # 0x800710E0 启动器已在运行/启动请求被拒
    3221225786,   # 0xC000013A 任务被取消/强杀
})
# 一次扫描写 42 条账本会跨秒（at 是每条记录落盘时刻）；以 120s 间隔切分扫描批次。
SCAN_SESSION_GAP_SECONDS = 120.0
# 窗口对比的回看小时数（与 task_exit_audit --window 默认口径一致）
AUDIT_WINDOW_HOURS = 24.0
KNOWN_TASK_STATES = {"ready", "running", "disabled", "queued"}
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MANIFEST_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s+(?P<size>\d+)\s+bytes\s+sha256=(?P<sha>[0-9A-Fa-f]{64})\s*$"
)

# 数据根形态清单：备份脚本每天把它写进 <vault>/<yyyy-MM-dd>/，是「某一期」快照。
# <vault>/data_manifest.json 是 gen_data_manifest.py 的默认 --out 位置（下一次
# 默认生成就会覆盖），不是日期快照，不参与两期比对。
DATA_MANIFEST_NAME = "data_manifest.json"
# 文件数变化超过 20%（相对上一期）或顶层目录数变化 → 判红：这个幅度几乎只能是
# 事故（目录被清空/换盘/只挂上一半），而不是日常增量（日常量级是千分之几）。
DATA_DRIFT_FILE_RED_RATIO = 0.20
# detail 里最多点名几个增删目录 / 变化最大的目录。
DATA_DRIFT_DIR_LIMIT = 6
DATA_DRIFT_DIR_CHANGED_LIMIT = 3
# dates 里最多回显几个发现的日期目录。
DATA_DRIFT_DATE_LIMIT = 5
# 2026-10-25 EXEC-R23：数据根里的瞬态/备份代目录不进漂移判断。
# 背景：整理层重建 17:14–17:59 产生 tushare_curated.tmp（+481 MiB，随后消失），
# 结束时旧层留成 tushare_curated.prev（7.18 GiB 回滚代，长期保留）；旧口径把
# 「重建过程中的临时目录」当成「数据根新增目录」→ 假红（触发 dir_count 一票红）。
DATA_DRIFT_EXCLUDE_DIR_NAMES = ("__pycache__", ".pytest_cache")
DATA_DRIFT_EXCLUDE_SUFFIXES = (".tmp", ".prev", ".old")
DATA_DRIFT_ROLLBACK_SUFFIXES = (".prev", ".old")

# --- 明文私钥扫描（security.plaintext_keys）---------------------------------
# 只扫限定范围，不做全盘扫：<repo>/outputs、<repo>/tools、<repo>/portal 递归，
# 加工作区根（<repo> 的父目录）一级的 *.pem / *.key / .env* 文件。
# 识别只看「文件名 + 内容特征」：不解析 PEM/DER、不回显任何文件内容。
PLAINTEXT_KEY_SCAN_REL_DIRS: tuple[Path, ...] = (
    Path("outputs"),
    Path("tools"),
    Path("portal"),
)
# 规定跳过的 4 类；另跳 python/工具链编译缓存——.pyc 是同一源码的副本，
# 不跳会把一个测试夹具按 .py/.pyc 重复计入（纯噪音，见 T9 报告边界说明）。
PLAINTEXT_KEY_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        ".worktrees",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
# 名称命中：*.pem / *.key / id_rsa* / .env*（.env* 按需求单独点名）。
PLAINTEXT_KEY_NAME_SUFFIXES = (".pem", ".key")
# 名称命中的文件用弱特征（内容含 PRIVATE KEY 字样即可，PEM 头/主体都含）；
# 其余任意文件用强特征（必须出现 PEM 私钥起始行）。
PLAINTEXT_KEY_WEAK_MARKER = b"PRIVATE KEY"
PLAINTEXT_KEY_MARKER_RE = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
# 扫描相位超时 → available=false + 红（已扫到的命中仍写进 detail）。
# 2026-10-22 从 3s → 12s → 25s → 45s：实测该扫描要开约 5,000 个文件读 8 KiB，
# 空载 2s，但整卡同时跑 catchup/worktree/docs、机器上还有并行会话时，
# 12s 也会被撞穿并报"扫描未完成"的红——那是**负载噪声**，不是私钥变多。
# 22:20 又撞了一次：冷缓存下 25s 超时，而同一份数据热缓存重跑只要 4.0s。
# 45s 是"宁可慢也不误报"的折中；真要更快，应减少扫描面而不是压预算。
PLAINTEXT_KEY_BUDGET_SECONDS = 45.0
# 单文件只读前 8 KiB：`-----BEGIN … PRIVATE KEY-----` 标记**一定在文件开头**
# （PEM 的头部就是第一行），读 tail 或读全文件都是浪费。2026-10-22 从 512 KiB
# 降到 8 KiB：同样 5,096 个文件的扫描从 >12s 降到 2s 以内，判据强度不变。
# 超过 8 KiB 的文件仍计入 oversized_skipped，detail 会明说"有内容没看到"。
PLAINTEXT_KEY_MAX_CONTENT_BYTES = 8 * 1024
# detail 里最多点名几条命中（其余折叠成计数，避免一行几十个路径）。
PLAINTEXT_KEY_DETAIL_LIMIT = 8
# git 跟踪判定：每条 ls-files 单独限时，整体再封一个 1.5s 顶（命中通常个位数）。
PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS = 2.0
PLAINTEXT_KEY_GIT_BUDGET_SECONDS = 1.5
# 测试夹具识别：只写进 JSON 的 findings[].test_fixture 做 triage，
# 不改变判定（命中 ≥1 仍判红），避免「看着像夹具」成为漏判借口。
PLAINTEXT_KEY_FIXTURE_NAME_PREFIXES = ("test_",)
PLAINTEXT_KEY_FIXTURE_NAME_SUFFIXES = ("_test.py", "_selftest.py", "_test.ps1")
PLAINTEXT_KEY_FIXTURE_DIRS = frozenset({"tests", "test", "fixtures"})

# --- 整理层新鲜度（data.curated_freshness）-----------------------------------
# 两层结构：<RAW_REL> 是写入管线每天写的原始层（228,930 个 parquet），
# <CURATED_REL> 是 tools/refresh_curated.py（merge_parquet.py）产出的整理层
# （51 张表 + 3 个侧车，7.18 GiB，全量约 52 分钟）。整理层是**纯派生**的，
# 自己不会跟着原始层变新——没有这个检查，它一年前建的看起来也"很正常"。
CURATED_REL = Path("quant_backtest") / "data" / "tushare_curated"
RAW_REL = Path("quant_backtest") / "data" / "tushare"
# 落后 ≤2 天绿（跨一个周末不喊狼来了）；>2 天黄（该重建）；>14 天红
# （两周没重建，拿整理层得出的结论已经不可信）。
CURATED_YELLOW_LAG_DAYS = 2.0
CURATED_RED_LAG_DAYS = 14.0
# 实测：228,930 个文件名的 scandir 遍历 0.6–0.9s（只吃目录项缓存里的 mtime，
# 从不打开 parquet、不读 footer）。但 2026-10-25 睡眠恢复后实测 >5s（冷缓存 +
# 杀软抖动），旧逻辑因此误判红。现改为：
#   * 预算默认 20s（ZB_HC_CURATED_BUDGET_S 可调），超预算只判黄，绝不判红；
#   * 成功扫描结果缓存 2h（ZB_HC_CURATED_CACHE / ZB_HC_CURATED_CACHE_TTL_S），
#     新鲜度是「天」尺度，2h 的缓存不会改变判级结论；
#   * ZB_HC_CURATED_FORCE=1 可强制忽略缓存重扫（排障/验证用）。
CURATED_SCAN_BUDGET_SECONDS = float(os.environ.get("ZB_HC_CURATED_BUDGET_S", "20.0"))
CURATED_CACHE_PATH = Path(os.environ.get(
    "ZB_HC_CURATED_CACHE",
    str(Path(tempfile.gettempdir()) / "health_card_curated_raw_cache.json")))
CURATED_CACHE_TTL_SECONDS = float(
    os.environ.get("ZB_HC_CURATED_CACHE_TTL_S", "7200"))
CURATED_FORCE_SCAN = os.environ.get("ZB_HC_CURATED_FORCE", "") == "1"
CURATED_SCAN_DEADLINE_CHECK_EVERY = 4096
CURATED_DETAIL_DATASET_LIMIT = 5

# --- 数据底座健康（data.shibor_lpr_freshness / data.tdx_manifest /
#     data.trade_cal_dedup）——2026-10-26 OS-FIX-DATAHEALTH ----------------------
# 三个"静默缺口"巡查：数据源断供（文件 mtime 新、内容是旧的）、vendor 大目录
# 未纳管（盘上有、备份/manifest 没有）、交易日历重复（同一日期多行，下游按日期
# 聚合时静默乘倍数）。纪律：只读、轻量；读不到 = 黄（不判绿），绝不静默当没事。
SHIBOR_LPR_REL = CURATED_REL / "shibor_lpr.parquet"
SHIBOR_LPR_YELLOW_DAYS = 14.0      # 落后 >14 天黄（该查上游源）
SHIBOR_LPR_RED_DAYS = 60.0         # 落后 >60 天红（两个月断供，结论不可信）
# 2026-10-26 K-DATA-20261026-SHIBOR：上游 shibor_lpr(LPR) 停更于 2026-08-20，
# 改用日频 shibor（银行间拆借）做主新鲜源；优先整理层 shibor.parquet，
# 未重建时回退原始层 data/tushare/shibor/part_*.parquet，最后才回退 LPR。
SHIBOR_DAILY_REL = CURATED_REL / "shibor.parquet"
SHIBOR_DAILY_YELLOW_DAYS = 4.0     # 日频：>4 天黄（含周末/小长假）
SHIBOR_DAILY_RED_DAYS = 8.0        # 日频：>8 天红（连续一周以上断供）
TDX_REL = Path("quant_backtest") / "data" / "tdx"
TDX_UNMANAGED_MIN_BYTES = 1024 ** 3  # >1 GiB 且未纳管才判黄（小目录=不适用）
TDX_WALK_BUDGET_SECONDS = 10.0     # 目录统计预算（实测 1,814 文件 0.13s）
TDX_WALK_MAX_FILES = 50000         # 文件数上限：防病态目录把健康卡拖死
TRADE_CAL_REL = CURATED_REL / "trade_cal.parquet"
TRADE_CAL_DUP_YELLOW_RATIO = 1.01  # 行数/去重日期数 >1.01（>1% 重复）判黄
DATA_MANIFEST_FILE = "data_manifest.json"

# --- 本地推理能力（ai.inference）---------------------------------------------
# 2026-10-24 建：本卡原来 21 项全在管任务/备份/数据/仓库，**产品本体（AI 推理）
# 零监控**。而推理故障全是静默的：上下文截断不报错、思考模型返回空串不报错、
# 通道死了只有调用时才 500、变慢只是「很慢」。
# 只测 9b 一个模型、只发一条 num_predict=16 的短请求（整项 ≤30s）；全量三模型
# 探针是 tools/probe_llm_capability.py 的事（约 2 分钟），不塞进每日健康卡。
AI_OLLAMA_HOST = "http://127.0.0.1:11434"
AI_MODEL = "qwen3.5:9b"
AI_MODEL_SHORT = AI_MODEL.split(":", 1)[-1]  # 绿卡 detail 里的短名：9b
AI_PROMPT = "只回答一个字：好"
AI_EXPECTED_ANSWER = "好"
AI_NUM_CTX = 16384
AI_NUM_PREDICT = 16
# 三跳各自封顶：3 + 3 + 20 = 26s，留 4s 余量给 HTTP/JSON 开销，整项预算 30s。
AI_TAGS_TIMEOUT_SECONDS = 3.0
AI_PS_TIMEOUT_SECONDS = 3.0
AI_CHAT_TIMEOUT_SECONDS = 45.0
AI_BUDGET_SECONDS = 60.0
# 9b（Q4_K_M，约 5.5 GiB 显存）正常 30–60 tok/s；<20 基本是掉到 CPU 或显存不足。
# num_predict 只有 16，单次 eval 样本小，阈值取 20 而不是 5（探针脚本）：健康卡里
# 这一项是「每天都看」的，宁可把「明显变慢」提前亮出来，也不要等它慢到掉底。
AI_MIN_TOKS_PER_SECOND = 20.0
# detail / JSON 只留答案开头几十字，不把整段生成塞进卡片。
AI_CONTENT_EXCERPT_CHARS = 40


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> dt.datetime | None:
    """解析 ISO 8601；无时区按本机时区处理。解析失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def _age_hours(timestamp: dt.datetime | None, now: dt.datetime) -> float | None:
    if timestamp is None:
        return None
    return round((now - timestamp).total_seconds() / 3600.0, 2)


def _short_exc(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:500]


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, f"文件不存在: {path}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, _short_exc(exc)
    if not isinstance(payload, dict):
        return None, f"顶层不是 JSON 对象: {path}"
    return payload, None


def _load_audit_semantics(repo_root: Path) -> tuple[dict[int, str] | None, str | None]:
    """只读 import task_exit_audit 的 EXIT_SEMANTICS；禁止落 .pyc。"""
    module_path = repo_root / AUDIT_MODULE_REL
    if not module_path.is_file():
        return None, f"未找到审计模块: {module_path}"
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "_health_card_task_exit_audit", str(module_path)
        )
        if spec is None or spec.loader is None:
            return None, "无法构造 import spec"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        raw = getattr(module, "EXIT_SEMANTICS", None)
        if not isinstance(raw, dict):
            return None, "审计模块缺少 EXIT_SEMANTICS"
        return {int(key): str(value) for key, value in raw.items()}, None
    except Exception as exc:  # import 失败不致命，退化判定并标黄
        return None, _short_exc(exc)
    finally:
        sys.dont_write_bytecode = previous


# ---------------------------------------------------------------------------
# 1) 计划任务审计
# ---------------------------------------------------------------------------
def _empty_task_audit(audit_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "root": str(audit_root),
        "ledger_path": str(audit_root / LEDGER_NAME),
        "summary_path": str(audit_root / SUMMARY_NAME),
        "ledger_lines": None,
        "ledger_records": None,
        "ledger_parse_errors": None,
        "ledger_parse_error_samples": [],
        "undated_records": None,
        "ledger_sessions": None,
        "window_hours": None,
        "window_baseline_at": None,
        "new_failures": [],
        "persisting_failures": [],
        "fixed_in_window": [],
        "last_scan_at": None,
        "age_hours": None,
        "clock_skew": None,
        "total_scanned": None,
        "ok": None,
        "failed": None,
        "disabled": None,
        "never_run": None,
        "running": None,
        "unknown_exit_codes": None,
        "unknown_states": None,
        "failures": None,
        "unknown_exit_names": [],
        "unknown_state_names": [],
        "counts_source": None,
        "summary_available": False,
        "summary_error": None,
        "summary_generated_at": None,
        "summary_time_delta_seconds": None,
        "summary_matches_ledger": None,
        "summary_count_drift": None,
        "semantics_source": None,
        "semantics_error": None,
        "thresholds_hours": {
            "yellow": AUDIT_YELLOW_HOURS,
            "red": AUDIT_RED_HOURS,
        },
    }


def collect_task_audit(
    audit_root: Path,
    now: dt.datetime,
    semantics: dict[int, str] | None,
    semantics_error: str | None,
) -> dict[str, Any]:
    result = _empty_task_audit(audit_root)
    result["semantics_source"] = (
        "import:" + str(AUDIT_MODULE_REL).replace("\\", "/")
        if semantics is not None
        else "ledger_text_fallback"
    )
    result["semantics_error"] = semantics_error

    summary, summary_error = _read_json(audit_root / SUMMARY_NAME)
    result["summary_available"] = summary is not None
    result["summary_error"] = summary_error
    if summary is not None:
        result["summary_generated_at"] = summary.get("generated_at")

    # 2026-10-28 OS-FIX-L1-AUDIT：canonical 摘要已按任务级语义把 DeadlineWatch/x5/
    # PytestWeekly/ObservePoint 等单列 known_baseline（不计 failed）；健康卡若继续
    # 从 ledger 重算会把它们再当失败（实测 9 vs 5）。这里消费摘要语义名单。
    _summary_semantic_skip: set[str] = set()
    if isinstance(summary, dict) and summary.get("canonical", True) is not False:
        for _key in ("known_baseline_tasks", "alert_found_tasks"):
            for _item in summary.get(_key) or []:
                if isinstance(_item, dict) and _item.get("name"):
                    _summary_semantic_skip.add(str(_item["name"]))

    ledger_path = audit_root / LEDGER_NAME
    if not ledger_path.is_file():
        result["reason"] = f"账本不存在: {ledger_path}"
        return result

    records: list[tuple[dt.datetime, dict[str, Any]]] = []
    parse_errors: list[dict[str, Any]] = []
    undated = 0
    non_empty = 0
    try:
        with ledger_path.open("rb") as handle:
            for lineno, raw in enumerate(handle, 1):
                line = raw.strip()
                if not line:
                    continue
                non_empty += 1
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    parse_errors.append({"line": lineno, "error": _short_exc(exc)})
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as exc:
                    parse_errors.append({"line": lineno, "error": _short_exc(exc)})
                    continue
                if not isinstance(record, dict):
                    parse_errors.append({"line": lineno, "error": "记录不是 JSON 对象"})
                    continue
                at = _parse_iso(record.get("at"))
                if at is None:
                    undated += 1
                    continue
                records.append((at, record))
    except OSError as exc:
        result["reason"] = f"读取账本失败: {_short_exc(exc)}"
        return result

    result["ledger_lines"] = non_empty
    result["ledger_records"] = len(records)
    result["ledger_parse_errors"] = len(parse_errors)
    result["ledger_parse_error_samples"] = parse_errors[:10]
    result["undated_records"] = undated

    if not records:
        reason = "账本中没有带合法 at 的记录"
        if parse_errors:
            reason += f"（{len(parse_errors)} 行解析失败）"
        if undated:
            reason += f"（{undated} 条缺 at）"
        result["reason"] = reason
        return result

    # 一次扫描的 42 条记录可能跨秒落盘；按时间排序后以间隔切分扫描会话，
    # 取最后一个会话作为「最近一次扫描」，而不是按 at 精确相等分组。
    ordered = sorted(records, key=lambda item: item[0])
    sessions: list[list[tuple[dt.datetime, dict[str, Any]]]] = []
    for item in ordered:
        if (
            sessions
            and (item[0] - sessions[-1][-1][0]).total_seconds()
            <= SCAN_SESSION_GAP_SECONDS
        ):
            sessions[-1].append(item)
        else:
            sessions.append([item])
    batch = [record for _, record in sessions[-1]]
    latest_at = sessions[-1][-1][0]
    result["ledger_sessions"] = len(sessions)

    # ---- 窗口对比（2026-10-23 接入）--------------------------------------
    # 只报"现在几个失败"是**快照**，回答不了"在变好还是变坏"。
    # 这里把最新一次扫描的失败集合与窗口起点那次做差集，给出
    # 新增 / 持续 / 窗口内已修复 —— 与 task_exit_audit.window_diff 同口径。
    def _fail_names(recs) -> set[str]:
        names = set()
        for _at, rec in recs:
            state_key = str(rec.get("state") or "").lower()
            code = _as_int(rec.get("last_result"))
            if (state_key == "disabled" or code in (0, NEVER_RUN_CODE)
                    or code in NON_FAILURE_TASK_CODES):
                continue
            if str(rec.get("name") or "?") in _summary_semantic_skip:
                continue
            names.add(str(rec.get("name") or "?"))
        return names

    cutoff = latest_at - dt.timedelta(hours=AUDIT_WINDOW_HOURS)
    prior = [s for s in sessions[:-1] if s[-1][0] <= cutoff]
    base_session = prior[-1] if prior else (sessions[0] if len(sessions) > 1 else None)
    now_fail = _fail_names(sessions[-1])
    base_fail = _fail_names(base_session) if base_session else set()
    result["window_hours"] = AUDIT_WINDOW_HOURS
    result["window_baseline_at"] = (
        base_session[-1][0].isoformat(timespec="seconds") if base_session else None)
    result["new_failures"] = sorted(now_fail - base_fail)
    result["persisting_failures"] = sorted(now_fail & base_fail)
    result["fixed_in_window"] = sorted(base_fail - now_fail)

    ok = failed = disabled = never_run = running = 0
    unknown_exit = 0
    unknown_states = 0
    failures: list[dict[str, Any]] = []
    unknown_exit_names: list[str] = []
    unknown_state_names: list[str] = []

    for record in batch:
        name = str(record.get("name") or "?")
        state = str(record.get("state") or "")
        state_key = state.lower()
        code = _as_int(record.get("last_result"))
        semantics_text = str(record.get("exit_semantics") or "")

        if state_key not in KNOWN_TASK_STATES:
            unknown_states += 1
            if len(unknown_state_names) < 20:
                unknown_state_names.append(f"{name}({state or '空'})")
        if code == RUNNING_CODE:
            running += 1

        if state_key == "disabled":
            disabled += 1
            continue
        if code == 0:
            ok += 1
            continue
        if code == NEVER_RUN_CODE:
            never_run += 1
            continue
        if code in NON_FAILURE_TASK_CODES:  # 跑批中/被终止/取消/上游拒跑：单列不计失败
            continue
        if name in _summary_semantic_skip:  # 任务级 by-design（known_baseline/alert_found）
            continue

        # 与 task_exit_audit 同口径：非 0、非 267011、非 Disabled 即失败
        failed += 1
        if len(failures) < 30:
            failures.append(
                {
                    "name": name,
                    "last_result": code if code is not None else record.get("last_result"),
                    "exit_semantics": semantics_text,
                    "last_run": record.get("last_run") or "",
                }
            )
        if semantics is not None:
            is_unknown = code not in semantics
        else:
            is_unknown = code is None or semantics_text in ("", UNKNOWN_SEMANTICS)
        if is_unknown:
            unknown_exit += 1
            if len(unknown_exit_names) < 20:
                unknown_exit_names.append(f"{name}({code})")

    result["available"] = True
    result["last_scan_at"] = _iso(latest_at)
    result["age_hours"] = _age_hours(latest_at, now)
    result["clock_skew"] = bool(
        result["age_hours"] is not None and result["age_hours"] < -0.1
    )
    result["total_scanned"] = len(batch)
    result["ok"] = ok
    result["failed"] = failed
    result["disabled"] = disabled
    result["never_run"] = never_run
    result["running"] = running
    result["unknown_exit_codes"] = unknown_exit
    result["unknown_states"] = unknown_states
    result["failures"] = failures
    result["unknown_exit_names"] = unknown_exit_names
    result["unknown_state_names"] = unknown_state_names
    result["counts_source"] = "ledger_latest_batch"

    if summary is not None:
        summary_at = _parse_iso(summary.get("generated_at"))
        delta = (
            abs((summary_at - latest_at).total_seconds())
            if summary_at is not None
            else None
        )
        result["summary_time_delta_seconds"] = round(delta, 1) if delta is not None else None
        drift: dict[str, dict[str, Any]] = {}
        for key, ledger_value in (
            ("total_scanned", len(batch)),
            ("ok", ok),
            ("failed", failed),
            ("disabled", disabled),
            ("never_run", never_run),
        ):
            if summary.get(key) != ledger_value:
                drift[key] = {"summary": summary.get(key), "ledger": ledger_value}
        result["summary_count_drift"] = drift
        result["summary_matches_ledger"] = bool(
            not drift and delta is not None and delta <= 120
        )
    return result


# ---------------------------------------------------------------------------
# 2) 备份
# ---------------------------------------------------------------------------
def _valid_date_dir(name: str) -> bool:
    if not DATE_DIR_RE.fullmatch(name):
        return False
    try:
        dt.date.fromisoformat(name)
    except ValueError:
        return False
    return True


def _empty_backup(vault_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "vault_root": str(vault_root),
        "latest_date": None,
        "dir": None,
        "dir_mtime": None,
        "manifest_path": None,
        "manifest_mtime": None,
        "age_hours": None,
        "clock_skew": None,
        "manifest_entries": None,
        "manifest_malformed": None,
        "manifest_malformed_samples": [],
        "manifest_missing": None,
        "size_mismatch": None,
        "missing_files": [],
        "size_mismatch_files": [],
        "manifest_bytes": None,
        "total_bytes": None,
        "total_human": None,
        "files_count": None,
        "bundle_files": [],
        "sha256_verified": False,
        "catchup": _empty_catchup(),
        "thresholds_hours": {
            "yellow": BACKUP_YELLOW_HOURS,
            "red": BACKUP_RED_HOURS,
        },
    }


def collect_backup(vault_root: Path, now: dt.datetime) -> dict[str, Any]:
    result = _empty_backup(vault_root)
    try:
        if not vault_root.is_dir():
            result["reason"] = f"备份根不存在或不是目录: {vault_root}"
            return result
        candidates = [
            entry
            for entry in vault_root.iterdir()
            if entry.is_dir() and _valid_date_dir(entry.name)
        ]
    except OSError as exc:
        result["reason"] = f"读取备份根失败: {_short_exc(exc)}"
        return result

    if not candidates:
        result["reason"] = f"未找到 yyyy-MM-dd 备份目录: {vault_root}"
        return result

    latest = max(candidates, key=lambda entry: entry.name)
    result["latest_date"] = latest.name
    result["dir"] = str(latest)
    try:
        stat = latest.stat()
    except OSError as exc:
        result["reason"] = f"读取备份目录属性失败: {_short_exc(exc)}"
        return result
    mtime = dt.datetime.fromtimestamp(stat.st_mtime).astimezone()
    result["dir_mtime"] = _iso(mtime)
    result["age_hours"] = _age_hours(mtime, now)
    result["clock_skew"] = bool(
        result["age_hours"] is not None and result["age_hours"] < -0.1
    )

    manifest = latest / "MANIFEST.txt"
    result["manifest_path"] = str(manifest)
    if not manifest.is_file():
        result["reason"] = f"MANIFEST.txt 不存在: {manifest}"
        return result
    try:
        manifest_stat = manifest.stat()
        result["manifest_mtime"] = _iso(
            dt.datetime.fromtimestamp(manifest_stat.st_mtime).astimezone()
        )
        raw = manifest.read_bytes()
    except OSError as exc:
        result["reason"] = f"读取 MANIFEST.txt 失败: {_short_exc(exc)}"
        return result

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    entries: dict[str, int] = {}
    malformed: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        # 注释行：# 开头是备份脚本写的头/尾摘要（`# vault backup <time>` /
        # `# vault <dir>` / `# summary ok=..`），不是条目，不能算格式异常。
        # 与 tools/verify_backups.py 的口径保持一致（它同样跳过 # 行）。
        if stripped.startswith("#"):
            continue
        match = MANIFEST_RE.match(stripped)
        if match is None:
            malformed.append({"line": lineno, "text": stripped[:120]})
            continue
        name = match.group("name")
        if name in (".", "..") or "/" in name or "\\" in name:
            malformed.append({"line": lineno, "text": f"非法文件名 {name!r}"})
            continue
        if name in entries:
            malformed.append({"line": lineno, "text": f"重复条目 {name!r}"})
            continue
        entries[name] = int(match.group("size"))

    result["manifest_entries"] = len(entries)
    result["manifest_malformed"] = len(malformed)
    result["manifest_malformed_samples"] = malformed[:10]
    result["bundle_files"] = sorted(
        name for name in entries if name.lower().endswith(".bundle")
    )

    if not entries:
        result["reason"] = f"MANIFEST 无可解析条目: {manifest}"
        return result

    total_bytes = 0
    files_count = 0
    try:
        for path in latest.rglob("*"):
            if path.is_file():
                total_bytes += path.stat().st_size
                files_count += 1
    except OSError as exc:
        result["reason"] = f"统计备份体积失败: {_short_exc(exc)}"
        return result

    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for name, declared in entries.items():
        path = latest / name
        if not path.is_file():
            missing.append(name)
            continue
        try:
            actual = path.stat().st_size
        except OSError:
            missing.append(name)
            continue
        if actual != declared:
            mismatches.append({"name": name, "manifest": declared, "actual": actual})

    result["available"] = True
    result["manifest_bytes"] = sum(entries.values())
    result["manifest_missing"] = len(missing)
    result["size_mismatch"] = len(mismatches)
    result["missing_files"] = missing[:20]
    result["size_mismatch_files"] = mismatches[:20]
    result["total_bytes"] = total_bytes
    result["total_human"] = _human_bytes(total_bytes)
    result["files_count"] = files_count
    return result


# ---------------------------------------------------------------------------
# 2c) 数据根漂移（两期 data_manifest.json 对比）
# ---------------------------------------------------------------------------
def _delta_or_none(latest: Any, previous: Any) -> int | None:
    """新值 - 旧值；任一侧不是整数就返回 None（不拿 0 冒充差值为 0）。"""
    if not isinstance(latest, int) or not isinstance(previous, int):
        return None
    return latest - previous


def _norm_path_str(value: Any) -> str | None:
    """规范化路径比较口径：统一斜杠、去尾斜杠、忽略大小写；非字符串返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("\\", "/").rstrip("/")
    return text.casefold() or None


def _pct_text(delta: int | None, previous: int | None) -> str | None:
    if delta is None or not isinstance(previous, int) or previous == 0:
        return None
    return "%+.2f%%" % (delta / previous * 100.0)


def _signed_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0 B"
    return ("+" if value > 0 else "-") + _human_bytes(abs(value))


def _manifest_dir_entry(entry: Any) -> tuple[str, dict[str, Any]] | None:
    """归一化 dirs[] 的一条；name 缺失/不是字符串返回 None（由调用方计为读不懂）。

    字段名容忍两种口径：files（gen_data_manifest.py 实际产物）/ file_count
    （任务书里的叫法），避免上游改名直接把整项判成不可读。
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    files = _as_int(entry.get("files"))
    if files is None:
        files = _as_int(entry.get("file_count"))
    return name.strip(), {
        "files": files,
        "bytes": _as_int(entry.get("bytes")),
        # scanned=false 只在扫描撞时间预算时出现（该目录文件数/字节为 null）。
        "scanned": entry.get("scanned") is not False,
    }


def _load_data_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读一份 data_manifest.json 并归一化成可比对的最小结构。

    结构不认识就返回 None + 原因（调用方判红），绝不把读不懂的清单当「一致」。
    """
    payload, error = _read_json(path)
    if payload is None:
        return None, error

    raw_dirs = payload.get("dirs")
    if not isinstance(raw_dirs, list):
        return None, f"不是 data_manifest（缺 dirs 数组）: {path}"

    dirs: dict[str, dict[str, Any]] = {}
    unparsed = 0
    for entry in raw_dirs:
        normalized = _manifest_dir_entry(entry)
        if normalized is None:
            unparsed += 1
            continue
        name, info = normalized
        dirs.setdefault(name, info)

    scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    root_files = scan.get("root_files") if isinstance(scan.get("root_files"), dict) else {}

    total_files = _as_int(payload.get("total_files"))
    total_bytes = _as_int(payload.get("total_bytes"))
    totals_source = "declared"
    if total_files is None or total_bytes is None:
        # 兼容只给 dirs 的旧口径：能加就加；有条目未扫到（files/bytes 为 null）
        # 就加不出总量 —— 直接判不可读，不做「部分之和冒充总量」。
        part_files = [info["files"] for info in dirs.values()]
        part_bytes = [info["bytes"] for info in dirs.values()]
        if any(not isinstance(value, int) for value in part_files + part_bytes):
            return None, f"清单缺总量字段，且 dirs 有条目未扫到，总量不可用: {path}"
        if total_files is None:
            total_files = sum(part_files) + (_as_int(root_files.get("files")) or 0)
            totals_source = "derived"
        if total_bytes is None:
            total_bytes = sum(part_bytes) + (_as_int(root_files.get("bytes")) or 0)
            totals_source = "derived"

    raw_catalog = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}
    catalog_declared = isinstance(payload.get("catalog"), dict)
    sha = raw_catalog.get("sha256")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha.strip()):
        sha = None
    present: bool | None
    if isinstance(raw_catalog.get("present"), bool):
        present = bool(raw_catalog.get("present"))
    elif catalog_declared:
        present = True
    else:
        # 整段 catalog 缺失：不是「catalog.json 不存在」，是「清单没记」。
        present = None

    partial = bool(scan.get("partial")) or unparsed > 0
    if any(info["scanned"] is False for info in dirs.values()):
        partial = True

    return {
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "schema": payload.get("schema"),
        "root": payload.get("root"),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "totals_source": totals_source,
        "dir_count": len(dirs),
        "dirs": dirs,
        "dirs_unparsed": unparsed,
        "partial": partial,
        "catalog": {
            "present": present,
            "size": _as_int(raw_catalog.get("size")),
            "sha256": sha.strip().lower() if sha else None,
        },
    }, None


def _discover_data_manifests(
    vault_root: Path,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """按日期目录名倒序返回含 data_manifest.json 的期次（新→旧）。

    只认 <vault>/<yyyy-MM-dd>/data_manifest.json。返回 (期次列表, 扫过的日期目录数, 错误)。
    """
    try:
        if not vault_root.is_dir():
            return [], 0, f"备份根不存在或不是目录: {vault_root}"
        date_dirs = sorted(
            (
                entry
                for entry in vault_root.iterdir()
                if entry.is_dir() and _valid_date_dir(entry.name)
            ),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError as exc:
        return [], 0, f"读取备份根失败: {_short_exc(exc)}"

    found: list[dict[str, Any]] = []
    for entry in date_dirs:
        manifest = entry / DATA_MANIFEST_NAME
        if manifest.is_file():
            found.append({"date": entry.name, "path": manifest})
    return found, len(date_dirs), None


def _is_transient_data_dir(name: str) -> bool:
    """漂移判断要排除的目录：重建/轮转瞬态与备份代（不按数据资产对待，EXEC-R23）。"""
    lowered = name.strip().lower()
    if lowered in DATA_DRIFT_EXCLUDE_DIR_NAMES:
        return True
    return lowered.endswith(DATA_DRIFT_EXCLUDE_SUFFIXES)


def _split_drift_dirs(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """把清单 dirs 拆成（参与漂移的目录, 被排除的目录）并标注排除原因。

    排除 *.tmp/*.prev/*.old/__pycache__/.pytest_cache：重建瞬态或回滚代不是数据
    资产（2026-10-25 EXEC-R23 假红修复）。返回的 excluded 按名字排序，kind ∈
    {transient(tmp/old), rollback(prev/old), cache}。
    """
    kept: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    for name in sorted(manifest.get("dirs") or {}):
        info = manifest["dirs"][name]
        if not _is_transient_data_dir(name):
            kept[name] = info
            continue
        lowered = name.strip().lower()
        if lowered in DATA_DRIFT_EXCLUDE_DIR_NAMES:
            kind = "cache"
        elif lowered.endswith(DATA_DRIFT_ROLLBACK_SUFFIXES):
            kind = "rollback"
        else:
            kind = "transient"
        excluded.append({"name": name, "kind": kind,
                         "files": info.get("files"),
                         "bytes": info.get("bytes")})
    return kept, excluded


def _adjusted_drift_totals(
    manifest: dict[str, Any], excluded: list[dict[str, Any]],
) -> dict[str, int | None]:
    """manifest 总量扣掉被排除目录的 files/bytes（扣不动就返回 None，不硬算）。"""
    files = manifest.get("total_files")
    total_bytes = manifest.get("total_bytes")
    ex_files = sum(item["files"] for item in excluded
                   if isinstance(item.get("files"), int))
    ex_bytes = sum(item["bytes"] for item in excluded
                   if isinstance(item.get("bytes"), int))
    adj_files = files - ex_files if isinstance(files, int) else None
    adj_bytes = total_bytes - ex_bytes if isinstance(total_bytes, int) else None
    if adj_files is not None and adj_files < 0:
        adj_files = None          # 总量字段与 dirs 不自洽：宁可判不可比
    if adj_bytes is not None and adj_bytes < 0:
        adj_bytes = None
    return {"files": adj_files, "bytes": adj_bytes}


def _empty_data_root_drift(vault_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "vault_root": str(vault_root),
        "manifest_name": DATA_MANIFEST_NAME,
        "date_dirs": None,
        "manifests_found": None,
        "dates": [],
        "latest_date": None,
        "previous_date": None,
        "manifests": [],
        "counts": {
            "latest": {"files": None, "bytes": None, "dirs": None},
            "previous": {"files": None, "bytes": None, "dirs": None},
            "delta": {"files": None, "bytes": None, "dirs": None},
        },
        "file_delta_ratio": None,
        "dirs_added": [],
        "dirs_removed": [],
        "dirs_changed": [],
        "roots": {"latest": None, "previous": None},
        "root_changed": None,
        "catalog": {
            "latest": {"present": None, "size": None, "sha256": None},
            "previous": {"present": None, "size": None, "sha256": None},
            "changed": None,
            "status": None,
        },
        "triggers": [],
        "level": None,
        "status": None,
        "thresholds": {"file_red_ratio": DATA_DRIFT_FILE_RED_RATIO},
        "counts_raw": None,
        "excluded": {
            "latest": [],
            "previous": [],
            "bytes_latest": None,
            "bytes_previous": None,
            "rollback_bytes_latest": None,
            "policy": "排除 *.tmp/*.prev/*.old/__pycache__/.pytest_cache："
                      "重建瞬态与回滚代不进漂移判断，回滚代体积单列",
        },
    }


def _catalog_change(latest: dict[str, Any], previous: dict[str, Any]) -> str:
    """catalog.json 的两期关系：same / changed / absent_both / unknown。"""
    latest_sha, previous_sha = latest.get("sha256"), previous.get("sha256")
    if latest_sha and previous_sha:
        return "same" if latest_sha == previous_sha else "changed"
    latest_present, previous_present = latest.get("present"), previous.get("present")
    if latest_present is False and previous_present is False:
        return "absent_both"
    if latest_present is not None and previous_present is not None:
        return "changed" if latest_present != previous_present else "unknown"
    return "unknown"


def collect_data_root_drift(vault_root: Path) -> dict[str, Any]:
    """比对 _vault 下最近两份 <date>/data_manifest.json，判断数据根是否漂移。

    判不了就说判不了：只有一份清单、扫描未完成（scan.partial）、清单读不到，
    一律走 available=false 且计数置 null，绝不把「没得比」当「一致」。
    """
    result = _empty_data_root_drift(vault_root)
    found, date_dirs, discover_error = _discover_data_manifests(vault_root)
    result["date_dirs"] = date_dirs
    result["manifests_found"] = len(found)
    result["dates"] = [item["date"] for item in found[:DATA_DRIFT_DATE_LIMIT]]

    if discover_error:
        result["reason"] = discover_error
        result["status"] = "unreadable"
        result["level"] = "red"
        return result
    if not found:
        result["reason"] = (
            "未找到任何 %s（扫过 %s 个日期目录），数据根形态没有被记录"
            % (DATA_MANIFEST_NAME, date_dirs)
        )
        result["status"] = "no_manifest"
        result["level"] = "red"
        return result

    result["latest_date"] = found[0]["date"]
    if len(found) < 2:
        result["reason"] = "只有 1 份 %s（%s），至少要两份才能比对漂移" % (
            DATA_MANIFEST_NAME,
            found[0]["date"],
        )
        result["status"] = "insufficient_history"
        result["level"] = "yellow"
        return result

    result["previous_date"] = found[1]["date"]
    loaded: list[dict[str, Any]] = []
    for item in found[:2]:
        manifest, load_error = _load_data_manifest(item["path"])
        loaded.append(
            {
                "date": item["date"],
                "path": str(item["path"]),
                "manifest": manifest,
                "error": load_error,
            }
        )
        result["manifests"].append(
            {
                "date": item["date"],
                "path": str(item["path"]),
                "readable": manifest is not None,
                "error": load_error,
                "generated_at": manifest["generated_at"] if manifest else None,
            }
        )

    unreadable = [
        "%s（%s）" % (item["date"], item["error"] or "原因未知")
        for item in loaded
        if item["manifest"] is None
    ]
    if unreadable:
        result["status"] = "unreadable"
        result["level"] = "red"
        scope = (
            "最近两份清单都读不到/解析失败"
            if len(unreadable) == len(loaded)
            else "有一份清单读不到，两期无法比对"
        )
        result["reason"] = scope + "：" + "；".join(unreadable)
        return result

    latest: dict[str, Any] = loaded[0]["manifest"]  # type: ignore[assignment]
    previous: dict[str, Any] = loaded[1]["manifest"]  # type: ignore[assignment]

    incomplete = []
    for item in loaded:
        manifest = item["manifest"]
        if manifest["partial"]:
            detail = "scan.partial=true" if manifest["dirs_unparsed"] == 0 else "有目录条目读不懂"
            incomplete.append("%s（%s）" % (item["date"], detail))
    if incomplete:
        result["status"] = "incomplete_scan"
        result["level"] = "yellow"
        result["reason"] = (
            "清单记录的扫描不完整：" + "、".join(incomplete) + "，总量与目录集合不可比"
        )
        return result

    # 2026-10-25 EXEC-R23：先剔除瞬态/回滚代目录再比对，避免重建 .tmp/.prev 假红。
    latest_dirs, latest_excluded = _split_drift_dirs(latest)
    previous_dirs, previous_excluded = _split_drift_dirs(previous)
    latest_adj = _adjusted_drift_totals(latest, latest_excluded)
    previous_adj = _adjusted_drift_totals(previous, previous_excluded)
    rollback_items = [item for item in latest_excluded if item["kind"] == "rollback"]
    result["excluded"] = {
        "latest": latest_excluded,
        "previous": previous_excluded,
        "bytes_latest": sum(item["bytes"] for item in latest_excluded
                            if isinstance(item.get("bytes"), int)),
        "bytes_previous": sum(item["bytes"] for item in previous_excluded
                              if isinstance(item.get("bytes"), int)),
        "rollback_bytes_latest": sum(item["bytes"] for item in rollback_items
                                     if isinstance(item.get("bytes"), int)),
        "policy": "排除 *.tmp/*.prev/*.old/__pycache__/.pytest_cache："
                  "重建瞬态与回滚代不进漂移判断，回滚代体积单列",
    }
    added = sorted(set(latest_dirs) - set(previous_dirs))
    removed = sorted(set(previous_dirs) - set(latest_dirs))

    changed_dirs: list[dict[str, Any]] = []
    for name in sorted(set(latest_dirs) & set(previous_dirs)):
        new_info = latest_dirs[name]
        old_info = previous_dirs[name]
        if new_info["files"] == old_info["files"] and new_info["bytes"] == old_info["bytes"]:
            continue
        changed_dirs.append(
            {
                "name": name,
                "files": {"previous": old_info["files"], "latest": new_info["files"]},
                "bytes_delta": _delta_or_none(new_info["bytes"], old_info["bytes"]),
            }
        )

    def _magnitude(item: dict[str, Any]) -> int:
        files = item.get("files") or {}
        delta = _delta_or_none(files.get("latest"), files.get("previous"))
        if delta is None:
            delta = item.get("bytes_delta") if isinstance(item.get("bytes_delta"), int) else None
        return -1 if delta is None else abs(delta)

    changed_dirs.sort(key=_magnitude, reverse=True)

    # 总量同样用「扣除排除目录」后的口径；扣不动就判不可比（不把不自洽当一致）。
    latest_files, latest_bytes = latest_adj["files"], latest_adj["bytes"]
    previous_files, previous_bytes = previous_adj["files"], previous_adj["bytes"]
    latest_dir_count = len(latest_dirs) if latest_dirs else None
    previous_dir_count = len(previous_dirs) if previous_dirs else None
    if (not isinstance(latest_files, int) or not isinstance(previous_files, int)
            or not isinstance(latest_bytes, int) or not isinstance(previous_bytes, int)):
        result["status"] = "unreadable"
        result["level"] = "red"
        result["reason"] = ("排除瞬态/回滚代目录后的总量不可计算"
                            "（manifest 总量与 dirs 不自洽），不判绿")
        return result

    result["counts"] = {
        "latest": {"files": latest_files, "bytes": latest_bytes, "dirs": latest_dir_count},
        "previous": {
            "files": previous_files,
            "bytes": previous_bytes,
            "dirs": previous_dir_count,
        },
        "delta": {
            "files": _delta_or_none(latest_files, previous_files),
            "bytes": _delta_or_none(latest_bytes, previous_bytes),
            "dirs": _delta_or_none(latest_dir_count, previous_dir_count),
        },
    }
    result["counts_raw"] = {
        "latest": {"files": latest["total_files"], "bytes": latest["total_bytes"],
                   "dirs": latest["dir_count"]},
        "previous": {"files": previous["total_files"], "bytes": previous["total_bytes"],
                     "dirs": previous["dir_count"]},
    }
    result["dirs_added"] = added
    result["dirs_removed"] = removed
    result["dirs_changed"] = changed_dirs[:DATA_DRIFT_DIR_LIMIT]

    catalog_status = _catalog_change(latest["catalog"], previous["catalog"])
    result["catalog"] = {
        "latest": latest["catalog"],
        "previous": previous["catalog"],
        "changed": catalog_status == "changed",
        "status": catalog_status,
    }

    latest_root, previous_root = latest["root"], previous["root"]
    result["roots"] = {"latest": latest_root, "previous": previous_root}
    root_changed = bool(
        _norm_path_str(latest_root)
        and _norm_path_str(previous_root)
        and _norm_path_str(latest_root) != _norm_path_str(previous_root)
    )
    result["root_changed"] = root_changed

    ratio: float | None = None
    file_surge = False
    if isinstance(previous_files, int) and isinstance(latest_files, int):
        if previous_files == 0:
            # 分母为 0：0 → 非 0 的变化率是无穷大，用标志位判红（不写 inf 进 JSON）。
            file_surge = latest_files != 0
        else:
            ratio = abs(latest_files - previous_files) / previous_files
            file_surge = ratio > DATA_DRIFT_FILE_RED_RATIO
    dir_count_changed = (
        isinstance(latest_dir_count, int)
        and isinstance(previous_dir_count, int)
        and latest_dir_count != previous_dir_count
    )
    result["file_delta_ratio"] = round(ratio, 6) if ratio is not None else None
    result["triggers"] = [
        name
        for name, flag in (("file_ratio", file_surge), ("dir_count", dir_count_changed))
        if flag
    ]

    changed = bool(
        latest_files != previous_files
        or latest_bytes != previous_bytes
        or latest_dir_count != previous_dir_count
        or added
        or removed
        or catalog_status == "changed"
        or root_changed
    )

    result["available"] = True
    if changed:
        major = bool(result["triggers"])
        result["status"] = "major_drift" if major else "drift"
        result["level"] = "red" if major else "yellow"
    elif catalog_status == "unknown":
        # 总量与目录集合都一致，但 catalog 指纹读不到：「一致」只能说到这一步。
        result["status"] = "catalog_unknown"
        result["level"] = "yellow"
    else:
        result["status"] = "consistent"
        result["level"] = "green"
    return result


def _catalog_drift_text(catalog: dict[str, Any]) -> str:
    status = catalog.get("status")
    latest = catalog.get("latest") or {}
    previous = catalog.get("previous") or {}
    new_sha = _short_sha(latest.get("sha256"))
    old_sha = _short_sha(previous.get("sha256"))
    if status == "same":
        return "catalog.json sha256 未变（%s）" % new_sha
    if status == "changed" and latest.get("sha256") and previous.get("sha256"):
        size_delta = _delta_or_none(latest.get("size"), previous.get("size"))
        extra = "，大小 %s 字节" % _signed_bytes(size_delta) if size_delta else ""
        return "catalog.json 已被换（%s → %s%s）" % (old_sha, new_sha, extra)
    if status == "changed" and latest.get("present") and previous.get("present") is False:
        return "catalog.json 新出现（%s），上期没有" % new_sha
    if status == "changed" and previous.get("present") and latest.get("present") is False:
        return "catalog.json 消失了（上期 %s，本期没有）" % old_sha
    if status == "changed":
        return "catalog.json 指纹变了（%s → %s）" % (old_sha, new_sha)
    if status == "absent_both":
        return "catalog.json 两期都不存在"
    return "catalog.json 指纹无法判定（清单缺 sha256）"


def _data_root_drift_excluded_note(drift: dict[str, Any]) -> str:
    """漂移 detail 的排除说明：瞬态/回滚代不进判断，回滚代体积单列（EXEC-R23）。"""
    excluded = drift.get("excluded") or {}
    items = excluded.get("latest") or []
    if not items:
        return ""
    shown = []
    for item in items[:DATA_DRIFT_DIR_LIMIT]:
        size = item.get("bytes")
        shown.append("%s(%s, %s)" % (
            item.get("name"), item.get("kind"),
            _human_bytes(size) if isinstance(size, int) else "—"))
    text = "已排除瞬态/备份代目录 %d 个：%s" % (len(items), "、".join(shown))
    if len(items) > DATA_DRIFT_DIR_LIMIT:
        text += " 等"
    rollback_bytes = excluded.get("rollback_bytes_latest")
    if isinstance(rollback_bytes, int) and rollback_bytes > 0:
        text += "；回滚代(.prev/.old) %s 单列，不计入漂移" % _human_bytes(rollback_bytes)
    return text


def _data_root_drift_detail(drift: dict[str, Any]) -> str:
    status = drift.get("status")
    if not drift.get("available"):
        tail = "；不判绿" if drift.get("level") == "yellow" else ""
        return "数据根漂移不可判定：" + (drift.get("reason") or "未知原因") + tail

    counts = drift.get("counts") or {}
    latest = counts.get("latest") or {}
    previous = counts.get("previous") or {}
    delta = counts.get("delta") or {}
    span = "%s → %s" % (drift.get("previous_date"), drift.get("latest_date"))
    catalog_text = _catalog_drift_text(drift.get("catalog") or {})
    roots = drift.get("roots") or {}
    if drift.get("root_changed"):
        root_text = "数据根路径变化：%s → %s" % (roots.get("previous"), roots.get("latest"))
    elif roots.get("latest"):
        root_text = "数据根路径未变"
    else:
        root_text = "数据根路径未记录"

    if status == "consistent":
        excluded_note = _data_root_drift_excluded_note(drift)
        text = "数据根无漂移：%s 文件 %s、%s、目录 %s 一致；%s；%s" % (
            span,
            _fmt_num(latest.get("files")),
            _human_bytes(latest.get("bytes")),
            _fmt_num(latest.get("dirs")),
            catalog_text,
            root_text,
        )
        return text + ("；" + excluded_note if excluded_note else "")

    pct = _pct_text(delta.get("files"), previous.get("files"))
    if delta.get("files") == 0:
        file_text = "文件 %s（未变）" % _fmt_num(latest.get("files"))
    else:
        file_text = "文件 %s → %s（%s%s）" % (
            _fmt_num(previous.get("files")),
            _fmt_num(latest.get("files")),
            _signed_int(delta.get("files")),
            "，" + pct if pct else "",
        )
    if delta.get("bytes") == 0:
        bytes_text = "字节 %s（未变）" % _human_bytes(latest.get("bytes"))
    else:
        bytes_text = "字节 %s（%s → %s）" % (
            _signed_bytes(delta.get("bytes")),
            _human_bytes(previous.get("bytes")),
            _human_bytes(latest.get("bytes")),
        )
    if delta.get("dirs") == 0:
        dir_text = "目录 %s（未变）" % _fmt_num(latest.get("dirs"))
    else:
        dir_text = "目录 %s → %s（%s）" % (
            _fmt_num(previous.get("dirs")),
            _fmt_num(latest.get("dirs")),
            _signed_int(delta.get("dirs")),
        )

    bits = ["%s %s" % (span, file_text), bytes_text, dir_text]
    added = drift.get("dirs_added") or []
    if added:
        bits.append(
            "新增目录 %s%s"
            % (
                "、".join(added[:DATA_DRIFT_DIR_LIMIT]),
                " 等 %d 个" % len(added) if len(added) > DATA_DRIFT_DIR_LIMIT else "",
            )
        )
    removed = drift.get("dirs_removed") or []
    if removed:
        bits.append(
            "删除目录 %s%s"
            % (
                "、".join(removed[:DATA_DRIFT_DIR_LIMIT]),
                " 等 %d 个" % len(removed) if len(removed) > DATA_DRIFT_DIR_LIMIT else "",
            )
        )
    changed_dirs = drift.get("dirs_changed") or []
    if changed_dirs:
        shown = []
        for item in changed_dirs[:DATA_DRIFT_DIR_CHANGED_LIMIT]:
            files = item.get("files") or {}
            shown.append(
                "%s %s→%s"
                % (item.get("name"), _fmt_num(files.get("previous")), _fmt_num(files.get("latest")))
            )
        bits.append("文件数变化最大的目录 " + "、".join(shown))
    bits.extend([catalog_text, root_text])

    excluded_note = _data_root_drift_excluded_note(drift)
    if status == "major_drift":
        triggers = []
        if "file_ratio" in (drift.get("triggers") or []):
            triggers.append(
                "文件数变化 %s 超过 %g%% 阈值"
                % (pct or "上期为 0", DATA_DRIFT_FILE_RED_RATIO * 100)
            )
        if "dir_count" in (drift.get("triggers") or []):
            triggers.append(
                "顶层目录数 %s → %s"
                % (_fmt_num(previous.get("dirs")), _fmt_num(latest.get("dirs")))
            )
        text = "数据根剧烈漂移（判红）：" + "；".join(bits) + "；触发：" + "、".join(triggers)
        return text + ("；" + excluded_note if excluded_note else "")
    if status == "catalog_unknown":
        text = "数据根总量一致（%s 文件 %s、%s、目录 %s），但 %s；不判绿" % (
            span,
            _fmt_num(latest.get("files")),
            _human_bytes(latest.get("bytes")),
            _fmt_num(latest.get("dirs")),
            catalog_text,
        )
        return text + ("；" + excluded_note if excluded_note else "")
    text = "数据根漂移（判黄）：" + "；".join(bits)
    return text + ("；" + excluded_note if excluded_note else "")


# ---------------------------------------------------------------------------
# 2d) 整理层新鲜度（data.curated_freshness）
# ---------------------------------------------------------------------------
def _empty_curated_freshness(curated_root: Path, raw_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "status": None,
        "level": None,
        "curated_root": str(curated_root),
        "raw_root": str(raw_root),
        "curated_files": None,
        "curated_oldest": None,
        "curated_newest": None,
        "raw_files": None,
        "raw_newest": None,
        "lag_days": None,
        "datasets_compared": None,
        "datasets_without_raw": None,
        "stale_datasets": None,
        "expired_datasets": None,
        "worst_dataset_lag_days": None,
        "stale_dataset_names": [],
        "scan_seconds": None,
        "timed_out": False,
        "cache_used": False,
        "cache_age_seconds": None,
        "thresholds": {
            "yellow_lag_days": CURATED_YELLOW_LAG_DAYS,
            "red_lag_days": CURATED_RED_LAG_DAYS,
            "budget_seconds": CURATED_SCAN_BUDGET_SECONDS,
        },
        "suggested_command": (
            "python -B tools/refresh_curated.py --dry-run "
            "→ python -B tools/refresh_curated.py（暂存不替换）"
        ),
    }


def _mtime_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return _iso(dt.datetime.fromtimestamp(timestamp).astimezone())


def _curated_cache_load(any_age: bool = False) -> dict[str, Any] | None:
    """读原始层扫描缓存；any_age=False 时只认 TTL 内的新鲜缓存。"""
    try:
        payload = json.loads(CURATED_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        ts = float(payload.get("ts"))
        raw_files = int(payload.get("raw_files") or 0)
        raw_newest_raw = payload.get("raw_newest")
        raw_newest = float(raw_newest_raw) if raw_newest_raw is not None else None
        per_dataset = {
            str(key): float(value)
            for key, value in dict(payload.get("per_dataset") or {}).items()
        }
    except (TypeError, ValueError):
        return None
    if raw_newest is None or not per_dataset:
        return None
    age = max(0.0, time.time() - ts)
    if not any_age and age > CURATED_CACHE_TTL_SECONDS:
        return None
    return {
        "per_dataset": per_dataset,
        "raw_newest": raw_newest,
        "raw_files": raw_files,
        "age_seconds": round(age, 1),
    }


def _curated_cache_save(
    per_dataset: dict[str, float], raw_newest: float | None, raw_files: int
) -> None:
    """扫描成功即写缓存；写失败不影响判级（缓存是加速器，不是事实源）。"""
    try:
        payload = {
            "ts": time.time(),
            "raw_newest": raw_newest,
            "raw_files": int(raw_files),
            "per_dataset": {key: value for key, value in per_dataset.items()},
            "source": "gen_health_card.collect_curated_freshness",
        }
        CURATED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CURATED_CACHE_PATH.with_name(CURATED_CACHE_PATH.name + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, CURATED_CACHE_PATH)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)


def _scan_raw_parquet_mtimes(
    raw_root: Path, deadline: float
) -> tuple[dict[str, float], float | None, int, bool]:
    """遍历原始层，返回（每个一级目录下最新 parquet mtime, 全局最新, 文件数, 超时）。

    只 scandir + 目录项缓存里的 mtime：不打开 parquet、不读 footer、不解析行数。
    数据集键取 raw_root 下的第一级目录名（与整理层的 <数据集>.parquet 同名）。
    原始层根下散落的 parquet 只计入全局最新，不冒充数据集。
    """
    per_dataset: dict[str, float] = {}
    newest: float | None = None
    count = 0
    timed_out = False
    judged = 0
    stack: list[tuple[str, str]] = [(str(raw_root), "")]
    while stack:
        # 目录级判一次（原始层 56 个一级目录，这次 monotonic 调用可忽略），
        # 目录内部每 4096 个目录项再判一次，避免单个巨型目录拖过预算。
        if time.monotonic() > deadline:
            timed_out = True
            break
        directory, dataset = stack.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            # 单个目录读不到不判死：少算一个数据集，比整项不可判定更接近事实。
            continue
        with entries:
            for entry in entries:
                judged += 1
                if (
                    judged % CURATED_SCAN_DEADLINE_CHECK_EVERY == 0
                    and time.monotonic() > deadline
                ):
                    timed_out = True
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, dataset or entry.name))
                        continue
                    if not entry.name.endswith(".parquet"):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                count += 1
                mtime = stat.st_mtime
                if newest is None or mtime > newest:
                    newest = mtime
                if not dataset:
                    continue
                current = per_dataset.get(dataset)
                if current is None or mtime > current:
                    per_dataset[dataset] = mtime
        if timed_out:
            break
    return per_dataset, newest, count, timed_out


def _curated_lag_days(raw_mtime: float | None, curated_mtime: float | None) -> float | None:
    """原始层最新写入 − 整理层产物时间，单位天。负数=整理层比原始层还新。"""
    if raw_mtime is None or curated_mtime is None:
        return None
    return round((raw_mtime - curated_mtime) / 86400.0, 2)


def collect_curated_freshness(curated_root: Path, raw_root: Path) -> dict[str, Any]:
    """量整理层落后原始层多少天：整理层是纯派生的，不会自己变新。

    口径（两套一起给，判定取更差的那个）：
    * 简化口径：整理层最旧 parquet mtime vs 原始层递归最新 parquet mtime；
    * 逐数据集：51 个数据集各自「本数据集整理产物 mtime vs 原始层同名目录最新」，
      报落后的数据集数量（能指出是哪几张表旧了）。
    判定：落后 ≤2 天绿；>2 天黄（该重建）；>14 天红（结论不可信）。
    整理层不存在 → available=false + 黄（不判绿）；原始层不存在/扫不出 parquet → 红；
    遍历超预算 → 黄（不判绿、不判红：超预算只说明扫描慢；有缓存则用缓存兜底并标注）。
    """
    result = _empty_curated_freshness(curated_root, raw_root)
    started = time.monotonic()
    deadline = started + CURATED_SCAN_BUDGET_SECONDS

    if not raw_root.is_dir():
        result["reason"] = f"原始层不存在或不是目录: {raw_root}"
        result["status"] = "raw_missing"
        result["level"] = "red"
        return result
    if not curated_root.is_dir():
        result["reason"] = (
            f"整理层不存在（{curated_root}）：它是 tools/refresh_curated.py 的派生产物，"
            "原始层不受影响，但读整理层的人手里没有新数据"
        )
        result["status"] = "missing"
        result["level"] = "yellow"
        return result

    # 整理层只有 50 来个条目，单独扫：不要为它递归，也别把侧车文件当表。
    curated_mtimes: dict[str, float] = {}
    try:
        with os.scandir(curated_root) as entries:
            for entry in entries:
                if not entry.name.endswith(".parquet"):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    curated_mtimes[entry.name[: -len(".parquet")]] = entry.stat(
                        follow_symlinks=False
                    ).st_mtime
                except OSError:
                    continue
    except OSError as exc:
        result["reason"] = f"读不到整理层目录: {_short_exc(exc)}"
        result["status"] = "unreadable"
        result["level"] = "yellow"
        return result

    if not curated_mtimes:
        result["reason"] = (
            f"整理层目录里没有 parquet（{curated_root}）：重建没落盘或只落了侧车"
        )
        result["status"] = "empty"
        result["level"] = "yellow"
        return result

    per_dataset: dict[str, float] = {}
    raw_newest: float | None = None
    raw_files = 0
    timed_out = False
    cache_used = False
    cache_age_s: float | None = None

    cached = None if CURATED_FORCE_SCAN else _curated_cache_load()
    if cached:
        per_dataset = cached["per_dataset"]
        raw_newest = cached["raw_newest"]
        raw_files = cached["raw_files"]
        cache_age_s = cached["age_seconds"]
        cache_used = True
    else:
        per_dataset, raw_newest, raw_files, timed_out = _scan_raw_parquet_mtimes(
            raw_root, deadline
        )
        if timed_out:
            # 超预算只说明扫描慢，不说明数据过期：旧逻辑判红会制造假红
            # （2026-10-25 睡眠恢复后实测）。有缓存（哪怕过期）就用缓存兜底，
            # 但明确标注 timed_out 且不判绿；没缓存则判黄，绝不判红。
            stale_cached = _curated_cache_load(any_age=True)
            if stale_cached:
                per_dataset = stale_cached["per_dataset"]
                raw_newest = stale_cached["raw_newest"]
                raw_files = stale_cached["raw_files"]
                cache_age_s = stale_cached["age_seconds"]
                cache_used = True
            else:
                result["scan_seconds"] = round(time.monotonic() - started, 3)
                result["timed_out"] = True
                result["reason"] = (
                    f"遍历原始层超过 {CURATED_SCAN_BUDGET_SECONDS:g}s 预算：本次结果"
                    "不采信，但**不判红**（超预算只说明扫描慢，不说明数据过期）；"
                    "可稍后复跑，或设 ZB_HC_CURATED_FORCE=1 强制重扫"
                )
                result["status"] = "timeout"
                result["level"] = "yellow"
                return result
        else:
            _curated_cache_save(per_dataset, raw_newest, raw_files)

    result["scan_seconds"] = round(time.monotonic() - started, 3)
    result["timed_out"] = timed_out or (cache_used and timed_out)
    result["cache_used"] = cache_used
    result["cache_age_seconds"] = cache_age_s
    if raw_newest is None or not raw_files:
        result["raw_files"] = raw_files
        result["reason"] = f"原始层没有任何 parquet，无从比对（{raw_root}）"
        result["status"] = "raw_empty"
        result["level"] = "red"
        return result

    curated_oldest = min(curated_mtimes.values())
    curated_newest = max(curated_mtimes.values())
    lag_days = _curated_lag_days(raw_newest, curated_oldest)

    compared: list[tuple[float, str]] = []
    without_raw: list[str] = []
    for name, mtime in curated_mtimes.items():
        raw_mtime = per_dataset.get(name)
        dataset_lag = _curated_lag_days(raw_mtime, mtime)
        if dataset_lag is None:
            without_raw.append(name)
            continue
        compared.append((dataset_lag, name))
    compared.sort(key=lambda item: item[0], reverse=True)

    stale = [(lag, name) for lag, name in compared if lag > CURATED_YELLOW_LAG_DAYS]
    expired = [(lag, name) for lag, name in compared if lag > CURATED_RED_LAG_DAYS]
    worst_dataset_lag = compared[0][0] if compared else None

    if not compared:
        # 一个同名数据集都对不上：两层结构脱节（原始层被改名/清空过）。
        # 这时简化口径可能仍然"看起来不落后"，恰恰是最危险的假绿，所以判红。
        result["curated_files"] = len(curated_mtimes)
        result["raw_files"] = raw_files
        result["datasets_compared"] = 0
        result["datasets_without_raw"] = len(without_raw)
        result["lag_days"] = lag_days
        result["reason"] = (
            "整理层 %d 个数据集在原始层里找不到任何同名目录（两层结构对不上），"
            "不能只靠总体 mtime 判绿" % len(curated_mtimes)
        )
        result["status"] = "no_matching_dataset"
        result["level"] = "red"
        return result

    # 判定取「简化口径」与「最差数据集」两者中更差的一个：简化口径能抓到
    # 「原始层新增了数据集、整理层根本没有」，逐数据集能抓到「个别表很久没重建」。
    candidates = [value for value in (lag_days, worst_dataset_lag) if value is not None]
    worst_lag = max(candidates)

    result.update(
        {
            "available": True,
            "curated_files": len(curated_mtimes),
            "curated_oldest": _mtime_iso(curated_oldest),
            "curated_newest": _mtime_iso(curated_newest),
            "raw_files": raw_files,
            "raw_newest": _mtime_iso(raw_newest),
            "lag_days": lag_days,
            "datasets_compared": len(compared),
            "datasets_without_raw": len(without_raw),
            "stale_datasets": len(stale),
            "expired_datasets": len(expired),
            "worst_dataset_lag_days": worst_dataset_lag,
            "stale_dataset_names": [name for _, name in stale][:CURATED_DETAIL_DATASET_LIMIT],
        }
    )
    if worst_lag > CURATED_RED_LAG_DAYS:
        result["status"] = "expired"
        result["level"] = "red"
    elif worst_lag > CURATED_YELLOW_LAG_DAYS:
        result["status"] = "stale"
        result["level"] = "yellow"
    else:
        result["status"] = "fresh"
        result["level"] = "green"
    if cache_used and timed_out and result["level"] == "green":
        # 缓存兜底可以给结论，但扫描已超预算时不判绿：避免用"看起来绿"掩盖
        # 冷缓存/杀软导致的性能退化（2026-10-25 假红改假绿同样不可取）。
        result["status"] = "fresh_cached_slow"
        result["level"] = "yellow"
    return result


def _curated_lag_text(lag_days: float | None) -> str:
    if lag_days is None:
        return "—"
    if lag_days <= 0:
        return "0 天（原始层最新写入不晚于整理层，当前不落后）"
    return "%.2f 天" % lag_days


def _curated_freshness_detail(block: dict[str, Any]) -> str:
    if not block:
        return "整理层新鲜度检查未运行（data 块缺失）"
    if not block.get("available"):
        text = "整理层新鲜度不可判定：" + (block.get("reason") or "未知原因")
        if block.get("level") == "yellow":
            # 缺件不等于坏掉：判黄不判绿，并把重建命令直接给出来。
            text += "；不判绿"
            if block.get("status") in ("missing", "empty"):
                text += "；建议：" + str(block.get("suggested_command"))
        return text

    bits = [
        "整理层最旧 %s / 最新 %s（%s 个 parquet）"
        % (
            block.get("curated_oldest"),
            block.get("curated_newest"),
            _fmt_num(block.get("curated_files")),
        ),
        "原始层最新 %s（递归 %s 个 parquet）"
        % (block.get("raw_newest"), _fmt_num(block.get("raw_files"))),
        "落后 " + _curated_lag_text(block.get("lag_days")),
    ]
    compared = block.get("datasets_compared")
    stale = block.get("stale_datasets")
    if compared is not None:
        dataset_text = "逐数据集 %s/%s 落后（>%g 天）" % (
            _fmt_num(stale),
            _fmt_num(compared),
            CURATED_YELLOW_LAG_DAYS,
        )
        names = block.get("stale_dataset_names") or []
        if names:
            dataset_text += "：" + "、".join(names)
            if (stale or 0) > len(names):
                dataset_text += " 等 %d 个" % stale
        bits.append(dataset_text)
    if block.get("datasets_without_raw"):
        bits.append("原始层缺对应目录 %s 个" % _fmt_num(block.get("datasets_without_raw")))
    if block.get("cache_used"):
        bits.append(
            "原始层扫描结果来自缓存（%.0f 分钟前），本次未重扫"
            % ((block.get("cache_age_seconds") or 0) / 60.0)
        )
    if block.get("timed_out"):
        bits.append("原始层遍历超预算：用缓存兜底且不判绿（超预算≠数据过期，也不判红）")

    status = block.get("status")
    if status == "expired":
        head = "整理层已明显过期（判红，拿它得出的结论不可信）："
        tail = "；建议：%s，校验通过后由人决定是否 --replace" % block.get("suggested_command")
    elif status == "stale":
        head = "整理层落后（判黄，该重建了）："
        tail = "；建议：%s" % block.get("suggested_command")
    else:
        head = "整理层不落后："
        # 绿也给一条节奏建议：本项一旦转黄就是"该重建了"，提前把命令写在这里。
        tail = "；建议保持每周一次重建（先 python -B tools/refresh_curated.py --dry-run；见 SOP §1.5）"
    return head + "；".join(bits) + tail


# ---------------------------------------------------------------------------
# 2e) 数据底座健康（2026-10-26 OS-FIX-DATAHEALTH）
#     ① shibor_lpr 断供 ② tdx vendor 未纳管 ③ trade_cal 重复
# ---------------------------------------------------------------------------
def _normalize_day(value: Any) -> str | None:
    """日期值归一 → YYYY-MM-DD；支持 int 20260820 / str 20260820 / ISO / date。"""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return "%s-%s-%s" % (text[:4], text[4:6], text[6:])
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        day = text[:10]
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            return None
        return day
    return None


def _read_parquet_column(
    path: Path, columns: tuple[str, ...]
) -> tuple[list[Any] | None, str]:
    """惰性 import pyarrow，只读一列 → (values, "")；失败 → (None, 原因)。

    健康卡主流程仍是零第三方依赖；只有本组数据底座检查需要 parquet 元数据。
    pyarrow 不可用 / schema 读失败 / 列名不匹配都返回原因（调用方判黄、不判绿）
    ——绝不静默跳过，也不读全表（只看单列，正常 <1s）。
    """
    try:
        import pyarrow.parquet as parquet  # 惰性：仅本组检查需要
    except Exception as exc:  # noqa: BLE001
        return None, "pyarrow 不可用（%s），无法读 parquet 单列" % _short_exc(exc)
    try:
        schema = parquet.read_schema(str(path))
    except Exception as exc:  # noqa: BLE001
        return None, "读 parquet schema 失败：%s" % _short_exc(exc)
    for column in columns:
        if column not in schema.names:
            continue
        try:
            table = parquet.read_table(str(path), columns=[column])
        except Exception as exc:  # noqa: BLE001
            return None, "读 parquet 列 %s 失败：%s" % (column, _short_exc(exc))
        return table.column(column).to_pylist(), ""
    return None, "列名不匹配（期望 %s；实际 %s）" % (
        "/".join(columns), ",".join(schema.names[:8]) or "空")


def _empty_shibor_lpr_freshness(path: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "status": None,
        "level": None,
        "path": str(path),
        "source": None,
        "sources_tried": [],
        "column": None,
        "rows": None,
        "max_date": None,
        "lag_days": None,
        "scan_seconds": None,
        "thresholds": {
            "yellow_days": SHIBOR_LPR_YELLOW_DAYS,
            "red_days": SHIBOR_LPR_RED_DAYS,
        },
    }


def _read_parquet_max_day(path: Path):
    """读单文件日期列 → (max_day, rows, error)。"""
    values, error = _read_parquet_column(path, ("cal_date", "date"))
    if error:
        return None, 0, error
    values = values or []
    days = [day for day in (_normalize_day(value) for value in values) if day]
    if not days:
        return None, len(values), "无有效日期值（列存在但全为空/不可解析）"
    return max(days), len(values), ""


def _rate_level(lag_days: int, source: str) -> tuple[str, str]:
    """利率新鲜度判级（纯函数，便于反例测试）。

    shibor 日频：>4 天黄 / >8 天红；lpr 历史（上游停更）：>14 黄 / >60 红。
    """
    if str(source).startswith("shibor"):
        yellow, red = SHIBOR_DAILY_YELLOW_DAYS, SHIBOR_DAILY_RED_DAYS
    else:
        yellow, red = SHIBOR_LPR_YELLOW_DAYS, SHIBOR_LPR_RED_DAYS
    if lag_days > red:
        return "red", "stale"
    if lag_days > yellow:
        return "yellow", "stale"
    return "green", "fresh"


def _shibor_daily_raw_dir(curated_root: Path) -> Path:
    """整理层目录 → 原始层 shibor 目录（<data>/tushare/shibor）。"""
    return Path(curated_root).parent / "tushare" / "shibor"


def collect_shibor_lpr_freshness(
    curated_root: Path, now: dt.datetime
) -> dict[str, Any]:
    """利率数据新鲜度（2026-10-26 K-DATA-20261026-SHIBOR 改造）。

    优先级：整理层 shibor.parquet → 原始层 shibor/part_*.parquet（取 mtime 最新）
    → shibor_lpr.parquet（历史 LPR，仅存证）。
    只读单文件单列；全缺失 / 读失败 / 无有效日期 → 黄（不判绿）。
    """
    block = _empty_shibor_lpr_freshness(SHIBOR_DAILY_REL)
    started = time.monotonic()
    try:
        candidates: list[tuple[str, Path | None]] = [
            ("shibor-curated", curated_root / SHIBOR_DAILY_REL.name),
            ("shibor-raw", None),
            ("lpr", curated_root / SHIBOR_LPR_REL.name),
        ]
        for source, path in candidates:
            block["sources_tried"].append(source)
            if source == "shibor-raw":
                raw_dir = _shibor_daily_raw_dir(curated_root)
                if not raw_dir.is_dir():
                    continue
                parts = sorted(raw_dir.glob("part_*.parquet"),
                               key=lambda item: item.stat().st_mtime)
                if not parts:
                    continue
                path = parts[-1]
            elif path is None or not path.is_file():
                continue
            max_day, rows, error = _read_parquet_max_day(path)
            if error or not max_day:
                block.update(path=str(path), source=source, rows=rows,
                             reason=error or "无有效日期",
                             status="unreadable", level="yellow")
                return block
            lag_days = (now.date() - dt.date.fromisoformat(max_day)).days
            level, status = _rate_level(lag_days, source)
            block.update(available=True, source=source, path=str(path),
                         column="cal_date/date", rows=rows, max_date=max_day,
                         lag_days=lag_days, level=level, status=status,
                         scan_seconds=round(time.monotonic() - started, 3))
            return block
        block.update(reason="shibor.parquet / 原始层 shibor / shibor_lpr 均缺失",
                     status="missing", level="yellow")
        return block
    except Exception as exc:  # noqa: BLE001
        block.update(reason="检查异常：%s" % _short_exc(exc),
                     status="error", level="yellow")
        return block


def _shibor_lpr_detail(block: dict[str, Any]) -> str:
    if not block:
        return "Shibor 新鲜度检查未运行（data 块缺失）"
    if not block.get("available"):
        return "Shibor/利率数据源不可判定：%s；判黄（不判绿，不静默当没事）" % (
            block.get("reason") or "未知原因")
    source = block.get("source") or "?"
    label = {"shibor-curated": "shibor 日频(整理层)",
             "shibor-raw": "shibor 日频(原始层)",
             "lpr": "LPR(历史，上游停更)"}.get(source, source)
    if source.startswith("shibor"):
        yellow, red = SHIBOR_DAILY_YELLOW_DAYS, SHIBOR_DAILY_RED_DAYS
    else:
        yellow, red = SHIBOR_LPR_YELLOW_DAYS, SHIBOR_LPR_RED_DAYS
    bits = [
        "%s 最新日期 %s（%s 行）" % (label, block.get("max_date"),
                                 _fmt_num(block.get("rows"))),
        "距今 %s 天" % _fmt_num(block.get("lag_days")),
        "阈值 >%g 天黄 / >%g 天红" % (yellow, red),
    ]
    level = block.get("level")
    if level == "red":
        return ("利率源已断供（判红，拿该源得出的结论已不可信）：" + "；".join(bits)
                + "；处置：查 tushare_sync --only shibor / 整理层重建是否漏了 shibor")
    if level == "yellow":
        return "利率源落后（判黄，该查上游源了）：" + "；".join(bits)
    suffix = "；LPR 上游停更于 2026-08-20（仅存证，不再作新鲜源）" if source.startswith("shibor") else ""
    return "利率源新鲜：" + "；".join(bits) + suffix


def _walk_tree_stats(root: Path) -> tuple[int, int, bool, str]:
    """os.walk 统计目录 → (文件数, 字节数, 是否完整遍历, 未完成原因)。"""
    files = 0
    total = 0
    deadline = time.monotonic() + TDX_WALK_BUDGET_SECONDS
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in filenames:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)
                if files >= TDX_WALK_MAX_FILES:
                    return (files, total, False,
                            "达到 %d 文件上限，计数为下限" % TDX_WALK_MAX_FILES)
            if time.monotonic() > deadline:
                return (files, total, False,
                        "遍历超过 %gs 预算，计数为下限" % TDX_WALK_BUDGET_SECONDS)
    except OSError as exc:
        return files, total, False, "遍历中断：%s" % _short_exc(exc)
    return files, total, True, ""


def _latest_data_manifest(
    vault_root: Path,
) -> tuple[Path | None, dict[str, Any] | None, str]:
    """取 <vault>/<YYYY-MM-DD>/data_manifest.json 里最新一份可读的。"""
    try:
        date_dirs = sorted(
            (entry for entry in vault_root.iterdir()
             if entry.is_dir()
             and re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.name)),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError as exc:
        return None, None, "_vault 不可读：%s" % _short_exc(exc)
    errors: list[str] = []
    for date_dir in date_dirs:
        candidate = date_dir / DATA_MANIFEST_FILE
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append("%s 读取失败：%s" % (candidate, _short_exc(exc)))
            continue
        if isinstance(payload, dict):
            return candidate, payload, ""
        errors.append("%s 顶层不是对象" % candidate)
    reason = "未找到可读的 data_manifest.json"
    if errors:
        reason += "（最近错误：%s）" % "；".join(errors[-2:])
    return None, None, reason


def _manifest_managed_field(
    manifest: dict[str, Any], name: str
) -> tuple[str | None, dict[str, Any] | None]:
    """data_manifest 是否已纳管某顶层目录 → (命中的字段, 对应条目 dict|None)。"""
    entries = manifest.get("dirs")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "") == name:
                return "dirs[].name", entry
            path = str(entry.get("path") or "").replace("\\", "/").rstrip("/")
            if path.split("/")[-1] == name:
                return "dirs[].path", entry
    for key in ("tables", "paths"):
        value = manifest.get(key)
        if isinstance(value, dict):
            for key_name in value:
                if name in str(key_name).replace("\\", "/").split("/"):
                    return key, None
        elif isinstance(value, list):
            for item in value:
                if name in str(item).replace("\\", "/").strip("/").split("/"):
                    return key, None
    return None, None


def _empty_tdx_manifest(tdx_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "status": None,
        "level": None,
        "tdx_root": str(tdx_root),
        "files": None,
        "bytes": None,
        "walk_note": "",
        "truncated": False,
        "manifest_path": None,
        "managed_by": None,
        "manifest_files": None,
        "manifest_bytes": None,
        "min_bytes": TDX_UNMANAGED_MIN_BYTES,
    }


def collect_tdx_manifest(repo_root: Path, vault_root: Path) -> dict[str, Any]:
    """TDX vendor 目录纳管巡查：>1 GiB 且不在 data_manifest → 黄「未纳管」。

    2026-10-26 OS-FIX-DATAHEALTH：tdx 约 21 GiB 的 vendor 原始目录，
    backup_daily 按设计把它排除出每日 tar 集（可重建），但既有健康卡既不看
    目录体积、也不看它是否在 data_manifest 里——"盘上有、备份无"完全不可见。
    判定：目录不存在或 <1 GiB → 绿/不适用；>1 GiB 且未纳管 → 黄；
    manifest 不可读 / 遍历未完成 → 黄（不判绿）。
    """
    tdx_root = repo_root / TDX_REL
    block = _empty_tdx_manifest(tdx_root)
    try:
        if not tdx_root.is_dir():
            block.update(available=True, status="missing", level="green",
                         reason="目录不存在（不适用）")
            return block
        files, total, complete, walk_note = _walk_tree_stats(tdx_root)
        block.update(available=True, files=files, bytes=total,
                     truncated=not complete, walk_note=walk_note)
        if not complete:
            block.update(status="unknown", level="yellow",
                         reason="目录遍历未完成（%s），计数 %s / %s 为下限"
                         % (walk_note, _fmt_num(files), _human_bytes(total)))
            return block
        if total <= TDX_UNMANAGED_MIN_BYTES:
            block.update(status="not_applicable", level="green",
                         reason="小于 %s 阈值，不适用"
                         % _human_bytes(TDX_UNMANAGED_MIN_BYTES))
            return block
        manifest_path, manifest, manifest_error = _latest_data_manifest(vault_root)
        block["manifest_path"] = str(manifest_path) if manifest_path else None
        if manifest is None:
            block.update(status="unknown", level="yellow",
                         reason="无法判定纳管：%s（不判绿）"
                         % (manifest_error or "data_manifest 不可读"))
            return block
        field, entry = _manifest_managed_field(manifest, "tdx")
        if field:
            block.update(status="managed", level="green", managed_by=field)
            if isinstance(entry, dict):
                block["manifest_files"] = _as_int(entry.get("files"))
                block["manifest_bytes"] = _as_int(entry.get("bytes"))
        else:
            block.update(status="unmanaged", level="yellow",
                         reason=">%s 且不在最新 data_manifest 的 dirs/tables/paths"
                         % _human_bytes(TDX_UNMANAGED_MIN_BYTES))
        return block
    except Exception as exc:  # noqa: BLE001
        block.update(reason="检查异常：%s" % _short_exc(exc),
                     status="error", level="yellow")
        return block


def _tdx_manifest_detail(block: dict[str, Any]) -> str:
    if not block:
        return "TDX vendor 纳管检查未运行（data 块缺失）"
    status = block.get("status")
    if status == "missing":
        return "TDX vendor 目录不存在（不适用）"
    if block.get("bytes") is None:
        return "TDX vendor 不可判定：%s；判黄（不判绿）" % (
            block.get("reason") or "未知原因")
    size_text = "%s 文件 / %s" % (_fmt_num(block.get("files")),
                                 _human_bytes(block.get("bytes")))
    head = "TDX vendor（%s）" % size_text
    if status == "unknown":
        return head + "；无法判定纳管：%s；判黄（不判绿）" % (
            block.get("reason") or "未知原因")
    if status == "not_applicable":
        return head + "；< %s，不适用（小目录不判未纳管）" % (
            _human_bytes(TDX_UNMANAGED_MIN_BYTES))
    if status == "managed":
        text = head + "；已纳管（data_manifest %s" % (block.get("managed_by") or "")
        if block.get("manifest_files") is not None:
            text += "：%s 文件 / %s" % (_fmt_num(block.get("manifest_files")),
                                       _human_bytes(block.get("manifest_bytes")))
        text += "）"
        return text + "；注意 backup_daily 仍按 vendor 排除出 tar 集（可重建）"
    if status == "unmanaged":
        return (head + "；未纳管（>%s 且不在最新 data_manifest）："
                "盘上有、备份/清单没有 → 判黄，需裁决纳入清单或明确接受可重建"
                % _human_bytes(TDX_UNMANAGED_MIN_BYTES))
    return head + "；" + str(block.get("reason") or "")


def _empty_trade_cal_dedup(path: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "status": None,
        "level": None,
        "path": str(path),
        "column": None,
        "rows": None,
        "distinct": None,
        "ratio": None,
        "scan_seconds": None,
        "thresholds": {"dup_yellow_ratio": TRADE_CAL_DUP_YELLOW_RATIO},
    }


def collect_trade_cal_dedup(curated_root: Path) -> dict[str, Any]:
    """交易日历去重巡查：rows/distinct > 1.01 → 黄（detail 写 rows/distinct/倍数）。

    2026-10-26 OS-FIX-DATAHEALTH：整理层 trade_cal.parquet 40,542 行只有
    13,514 个 distinct cal_date（×3 重复，多交易所/重复 merge 常见）。下游若按
    日期聚合会静默乘倍数，而既有健康卡完全看不见。只读 cal_date 单列；
    读失败/无有效日期 → 黄（不判绿）。
    """
    path = curated_root / Path(TRADE_CAL_REL).name
    block = _empty_trade_cal_dedup(path)
    started = time.monotonic()
    try:
        if not path.is_file():
            block.update(reason="文件缺失：%s" % path,
                         status="missing", level="yellow")
            return block
        values, error = _read_parquet_column(path, ("cal_date", "date"))
        block["scan_seconds"] = round(time.monotonic() - started, 3)
        if error:
            block.update(reason=error, status="unreadable", level="yellow")
            return block
        values = values or []
        days = [day for day in (_normalize_day(value) for value in values) if day]
        block.update(rows=len(values), distinct=len(set(days)))
        if not values or not days:
            block.update(reason="无有效 cal_date 值", status="invalid", level="yellow")
            return block
        ratio = len(values) / float(len(set(days)))
        block.update(available=True, column="cal_date/date", ratio=round(ratio, 4))
        if ratio > TRADE_CAL_DUP_YELLOW_RATIO:
            block.update(status="duplicated", level="yellow")
        else:
            block.update(status="ok", level="green")
        return block
    except Exception as exc:  # noqa: BLE001
        block.update(reason="检查异常：%s" % _short_exc(exc),
                     status="error", level="yellow")
        return block


def _trade_cal_dedup_detail(block: dict[str, Any]) -> str:
    if not block:
        return "交易日历去重检查未运行（data 块缺失）"
    if not block.get("available"):
        return "交易日历去重不可判定：%s；判黄（不判绿）" % (
            block.get("reason") or "未知原因")
    bits = [
        "trade_cal.parquet %s 行 / %s 个去重日期（×%s）"
        % (_fmt_num(block.get("rows")), _fmt_num(block.get("distinct")),
           _fmt_num(block.get("ratio"))),
        "阈值 >%g 判黄（>%g%% 重复）"
        % (TRADE_CAL_DUP_YELLOW_RATIO, (TRADE_CAL_DUP_YELLOW_RATIO - 1) * 100),
    ]
    if block.get("level") == "yellow":
        return ("交易日历重复（判黄，按日期聚合会静默乘倍数）：" + "；".join(bits)
                + "；重复通常来自多 exchange 行或重复 merge，处置在整理层生成侧")
    return "交易日历无重复：" + "；".join(bits)


# ---------------------------------------------------------------------------
# 3) 仓库卫生
# ---------------------------------------------------------------------------
def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _git_run(
    repo: Path, args: list[str], timeout: float = 60.0
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.quotepath=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _empty_repo(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "branch": None,
        "dirty_files": None,
        "staged_files": None,
        "untracked_files": None,
        "conflicted_files": None,
        "worktrees": None,
        "linked_worktrees": None,
        "worktrees_source": "git worktree list",
        "worktree_dirs": None,
        "worktree_dirs_consistent": None,
        "worktree_dir_exists": None,
        "worktrees_root": str(repo_root / ".worktrees"),
    }


def collect_repo(repo_root: Path, now: dt.datetime) -> dict[str, Any]:
    del now  # 保留统一签名；仓库块不依赖当前时间
    result = _empty_repo(repo_root)
    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result
    except OSError as exc:
        result["reason"] = f"读取仓库根失败: {_short_exc(exc)}"
        return result

    try:
        probe = _git_run(repo_root, ["rev-parse", "--is-inside-work-tree"])
    except FileNotFoundError:
        result["reason"] = "找不到 git 可执行文件"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git 探测失败: {_short_exc(exc)}"
        return result
    if probe.returncode != 0:
        result["reason"] = (
            "不是 git 工作树: " + (_decode(probe.stderr).strip()[:300] or "git 返回非零")
        )
        return result

    try:
        status = _git_run(repo_root, ["status", "--porcelain=v1"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git status 失败: {_short_exc(exc)}"
        return result
    if status.returncode != 0:
        result["reason"] = "git status 失败: " + (
            _decode(status.stderr).strip()[:300] or "git 返回非零"
        )
        return result

    lines = [line for line in _decode(status.stdout).splitlines() if line.strip()]
    result["dirty_files"] = len(lines)
    result["untracked_files"] = sum(1 for line in lines if line.startswith("??"))
    result["staged_files"] = sum(
        1 for line in lines if not line.startswith("??") and line[0] not in (" ", "?")
    )
    result["conflicted_files"] = sum(1 for line in lines if line[:2] in CONFLICT_CODES)

    try:
        worktree_output = _git_run(repo_root, ["worktree", "list", "--porcelain"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git worktree list 失败: {_short_exc(exc)}"
        return result
    if worktree_output.returncode != 0:
        result["reason"] = "git worktree list 失败: " + (
            _decode(worktree_output.stderr).strip()[:300] or "git 返回非零"
        )
        return result
    worktree_paths = [
        line[len("worktree ") :]
        for line in _decode(worktree_output.stdout).splitlines()
        if line.startswith("worktree ")
    ]
    result["worktrees"] = len(worktree_paths)
    result["linked_worktrees"] = max(0, len(worktree_paths) - 1)

    worktrees_root = repo_root / ".worktrees"
    result["worktree_dir_exists"] = worktrees_root.is_dir()
    if worktrees_root.is_dir():
        try:
            result["worktree_dirs"] = sum(
                1 for child in worktrees_root.iterdir() if child.is_dir()
            )
        except OSError as exc:
            result["reason"] = f"读取 .worktrees 失败: {_short_exc(exc)}"
            return result
    else:
        result["worktree_dirs"] = 0
    result["worktree_dirs_consistent"] = (
        result["worktree_dirs"] == result["linked_worktrees"]
    )

    try:
        branch = _git_run(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode == 0:
            result["branch"] = _decode(branch.stdout).strip() or None
    except (OSError, subprocess.TimeoutExpired):
        result["branch"] = None

    result["available"] = True
    return result


# ---------------------------------------------------------------------------
# 3b) worktree TTL（ROADMAP 规则 9）
# ---------------------------------------------------------------------------
def _parse_worktree_porcelain(text: str) -> list[dict[str, Any]]:
    """解析 `git worktree list --porcelain`；空行分块，返回按出现顺序的条目。"""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            entries.append(current)
            current = None

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            current = {
                "path": value,
                "head": None,
                "branch": None,
                "detached": False,
            }
            continue
        if current is None:
            continue
        if key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value
        elif key == "detached":
            current["detached"] = True
        elif key in ("bare", "locked", "prunable"):
            # 只记录存在性；locked/prunable 的说明文本不参与判定。
            current[key] = value or True
    flush()
    return entries


def _worktree_display_path(path: str, repo_root: Path) -> str:
    """尽量显示相对 repo_root 的 posix 路径；跨盘等无法相对时回退原路径。"""
    try:
        relative = os.path.relpath(path, str(repo_root))
    except (OSError, ValueError):
        return path.replace("\\", "/")
    return Path(relative).as_posix()


def _empty_worktree_ttl(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "target_ref": None,
        "age_source": "branch_commit_time",
        "threshold_days": WORKTREE_TTL_DAYS,
        "duration_seconds": None,
        "budget_seconds": WORKTREE_TTL_BUDGET_SECONDS,
        "main_worktree": None,
        "worktrees_total": None,
        "linked_worktrees": None,
        "merged_worktrees": None,
        "violations": None,
        "dirty_expired": None,
        "unknown": None,
        "status": None,
        "level": None,
        "expired": [],
        "dirty_expired_worktrees": [],
        "unknown_worktrees": [],
        "items": [],
    }


def _worktree_has_changes(
    path_str: str, next_timeout
) -> tuple[bool | None, str | None]:
    """判断一个关联工作树是否还带着未提交改动。

    规则 9 的原文是「分支已合并**并且无未提交改动**的工作树，合并后 7 天内
    必须 remove」。只判「已合并 + 超期」会把还带着未提交工作的树报成“该回收”，
    所以超期候选必须实测脏不脏。

    返回 (是否脏, 失败原因)：无法判定时返回 (None, 原因)，**绝不冒充干净**。
    """
    raw = (path_str or "").strip().strip('"')
    if not raw:
        return None, "工作树路径为空"
    path = Path(raw)
    if not path.is_dir():
        return None, f"工作树目录不存在: {raw}"
    try:
        proc = _git_run(path, ["status", "--porcelain"], timeout=next_timeout())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git status 失败: {_short_exc(exc)}"
    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip()[:200] or "git 返回非零"
        return None, "git status 失败: " + detail
    return bool(_decode(proc.stdout).strip()), None


def _branch_commit_times(
    repo_root: Path, timeout: float
) -> tuple[dict[str, int] | None, str | None]:
    """一次性取所有本地分支的 tip 提交时间（committerdate unix 秒）。

    选「分支最后提交时间」而不是目录 mtime：worktree 目录 mtime 会因增删
    顶层文件而漂移（哪怕没有提交），无法代表「合并后过了多久」。
    """
    try:
        proc = _git_run(
            repo_root,
            ["for-each-ref", "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads"],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git for-each-ref 失败: {_short_exc(exc)}"
    if proc.returncode != 0:
        return None, "git for-each-ref 失败: " + (
            _decode(proc.stderr).strip()[:300] or "git 返回非零"
        )
    refs: dict[str, int] = {}
    for line in _decode(proc.stdout).splitlines():
        name, sep, raw = line.partition("\t")
        if not sep or not name:
            continue
        try:
            refs[name] = int(raw.strip())
        except ValueError:
            continue
    return refs, None


def collect_worktree_ttl(repo_root: Path, now: dt.datetime) -> dict[str, Any]:
    """统计「已合并进 master/main + 分支最后提交 >7 天」的关联工作树。

    只统计 `git worktree list` 中除主树（第一条）外的关联工作树；
    detached HEAD / 分支引用缺失 / 取不到提交时间的树计 unknown，不冒充绿。
    """
    result = _empty_worktree_ttl(repo_root)
    started = time.monotonic()
    deadline = started + WORKTREE_TTL_BUDGET_SECONDS

    def next_timeout() -> float:
        """把剩余预算分给每一次 git 调用，避免 4-5 次调用各自超时拖过 3s。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            raise subprocess.TimeoutExpired("git", WORKTREE_TTL_BUDGET_SECONDS)
        return min(WORKTREE_TTL_GIT_TIMEOUT_SECONDS, remaining)

    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result

        try:
            probe = _git_run(
                repo_root,
                ["rev-parse", "--is-inside-work-tree"],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git 探测失败: {_short_exc(exc)}"
            return result
        if probe.returncode != 0:
            result["reason"] = "不是 git 工作树: " + (
                _decode(probe.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        target_ref: str | None = None
        for candidate in WORKTREE_TTL_BRANCH_CANDIDATES:
            probe = _git_run(
                repo_root,
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"],
                timeout=next_timeout(),
            )
            if probe.returncode == 0:
                target_ref = candidate
                break
        if target_ref is None:
            result["reason"] = (
                "找不到本地分支 master/main，无法判定工作树是否已合并"
            )
            return result
        result["target_ref"] = target_ref

        try:
            listing = _git_run(
                repo_root,
                ["worktree", "list", "--porcelain"],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git worktree list 失败: {_short_exc(exc)}"
            return result
        if listing.returncode != 0:
            result["reason"] = "git worktree list 失败: " + (
                _decode(listing.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        try:
            merged_proc = _git_run(
                repo_root,
                [
                    "branch",
                    "--merged",
                    target_ref,
                    "--format=%(refname:short)",
                ],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git branch --merged 失败: {_short_exc(exc)}"
            return result
        if merged_proc.returncode != 0:
            result["reason"] = "git branch --merged 失败: " + (
                _decode(merged_proc.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        refs, refs_error = _branch_commit_times(repo_root, next_timeout())
        if refs is None:
            result["reason"] = refs_error or "无法读取分支提交时间"
            return result

        entries = _parse_worktree_porcelain(_decode(listing.stdout))
        if not entries:
            result["reason"] = "git worktree list 未返回任何工作树"
            return result

        # 第一条是主树；规则 9 只约束可回收的关联工作树。
        main_entry = entries[0]
        linked = entries[1:]
        result["main_worktree"] = _worktree_display_path(
            str(main_entry.get("path") or ""), repo_root
        )

        merged_set = {
            line.strip()
            for line in _decode(merged_proc.stdout).splitlines()
            if line.strip()
        }
        now_timestamp = now.timestamp()
        expired: list[dict[str, Any]] = []
        dirty_expired: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        merged_count = 0

        for entry in linked:
            raw_branch = str(entry.get("branch") or "")
            branch = (
                raw_branch[len("refs/heads/") :]
                if raw_branch.startswith("refs/heads/")
                else (raw_branch or None)
            )
            display = _worktree_display_path(str(entry.get("path") or ""), repo_root)
            merged: bool | None = None
            age_days: float | None = None
            last_commit_at: str | None = None
            status = "unknown"
            unknown_reason: str | None = None
            item_dirty: bool | None = None

            if entry.get("detached") or not branch:
                unknown_reason = "detached HEAD（无分支可判）"
            elif branch not in refs:
                unknown_reason = "分支引用不存在或读不到提交时间"
            else:
                merged = branch in merged_set
                timestamp = refs[branch]
                last_commit_at = _iso(
                    dt.datetime.fromtimestamp(timestamp).astimezone()
                )
                age_days = round((now_timestamp - timestamp) / 86400.0, 2)
                if merged:
                    merged_count += 1
                    if age_days > WORKTREE_TTL_DAYS:
                        # 规则 9 还要求「无未提交改动」——超期候选实测脏不脏，
                        # 脏的树不能建议回收（它的改动还没进任何提交）。
                        dirty, dirty_reason = _worktree_has_changes(
                            str(entry.get("path") or ""), next_timeout
                        )
                        if dirty is None:
                            unknown_reason = dirty_reason or "无法读取工作树状态"
                            status = "unknown"
                        elif dirty:
                            status = "expired_dirty"
                            item_dirty = True
                        else:
                            status = "expired"
                    else:
                        status = "merged_fresh"
                else:
                    status = "unmerged"

            item: dict[str, Any] = {
                "path": display,
                "branch": branch,
                "head": _short_sha(entry.get("head")),
                "merged": merged,
                "age_days": age_days,
                "last_commit_at": last_commit_at,
                "status": status,
            }
            if item_dirty is not None:
                item["uncommitted_changes"] = item_dirty
            if unknown_reason is not None:
                item["unknown_reason"] = unknown_reason
                unknown.append(item)
            elif status == "expired":
                expired.append(item)
            elif status == "expired_dirty":
                dirty_expired.append(item)
            if len(items) < WORKTREE_TTL_ITEM_LIMIT:
                items.append(item)

        result["worktrees_total"] = len(entries)
        result["linked_worktrees"] = len(linked)
        result["merged_worktrees"] = merged_count
        result["violations"] = len(expired)
        result["dirty_expired"] = len(dirty_expired)
        result["unknown"] = len(unknown)
        result["expired"] = expired[:WORKTREE_TTL_DETAIL_LIMIT]
        result["dirty_expired_worktrees"] = dirty_expired[:WORKTREE_TTL_DETAIL_LIMIT]
        result["unknown_worktrees"] = unknown[:WORKTREE_TTL_DETAIL_LIMIT]
        result["items"] = items
        result["available"] = True
        if expired:
            result["status"] = "expired"
            result["level"] = "yellow"
        elif dirty_expired:
            # 已合并 + 超期，但仍带着未提交改动：不能建议 remove（规则 9 的
            # 前提是「无未提交改动」），必须人先决定这些改动去哪。判黄不判绿。
            result["status"] = "expired_dirty"
            result["level"] = "yellow"
        elif unknown:
            result["status"] = "unknown"
            # 未知态单列、判黄：不危险，但也不能冒充绿。
            result["level"] = "yellow"
        else:
            result["status"] = "current"
            result["level"] = "green"
    except FileNotFoundError:
        result["reason"] = "找不到 git 可执行文件"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git 探测失败: {_short_exc(exc)}"
        return result
    finally:
        result["duration_seconds"] = round(time.monotonic() - started, 3)

    elapsed = result["duration_seconds"] or 0.0
    if elapsed > WORKTREE_TTL_BUDGET_SECONDS:
        result["reason"] = (
            f"检查耗时 {elapsed:.2f}s 超出 {WORKTREE_TTL_BUDGET_SECONDS:g}s 预算，结果不采信"
        )
        result["available"] = False
        result["status"] = None
        result["level"] = None
        result["worktrees_total"] = None
        result["linked_worktrees"] = None
        result["merged_worktrees"] = None
        result["violations"] = None
        result["unknown"] = None
        result["expired"] = []
        result["unknown_worktrees"] = []
        result["items"] = []
    return result


# ---------------------------------------------------------------------------
# 3c) 文档索引新鲜度（gen_docs_index.py --check）
# ---------------------------------------------------------------------------
def _empty_docs_index(repo_root: Path) -> dict[str, Any]:
    script = repo_root / DOCS_INDEX_SCRIPT_REL
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "script": str(script),
        "command": None,
        "exit_code": None,
        "status": None,
        "level": None,
        "parsed": False,
        "diffs": None,
        "diffs_total": None,
        "duration_seconds": None,
        "timeout_seconds": DOCS_CHECK_TIMEOUT_SECONDS,
        "budget_seconds": DOCS_CHECK_BUDGET_SECONDS,
        "timed_out": False,
        "output_excerpt": "",
        "output_truncated": False,
    }


def _docs_diff_text(diffs: dict[str, int]) -> str:
    parts = [
        f"{label} {diffs.get(key, 0)}" for key, label in DOCS_DIFF_LABELS
    ]
    return " / ".join(parts)


# --- 警报监控存活（ops.alert_watch_liveness）---------------------------------
# 2026-10-22 建：`AlertWatch` 每 30 分钟跑一次，但它的状态文件从 10-18 起就没再
# 更新过——连续 4 天失败，而账本上只体现为「又一个 rc=1」。
# 那次失败的**原因是正确的**：上游 6 个源的完成时间证据缺失（premarket 无
# trade_date、chanlun 无 completed_at、nine_dim 的 generated_at 无时区），
# 所以脚本拒绝基于不可信数据报警。问题不在脚本，在于**没人看得见它停了**。
# 这一项只做一件事：把「最后一次成功写入状态」的时刻量出来，停摆多久一眼可见。
ALERT_STATE_REL = "news_collector/outbox/alert_watch_state.json"
ALERT_RED_HOURS = 26.0     # 状态文件 >26h 没成功写 → 红（旧口径，心跳缺失/过期时回退）
ALERT_YELLOW_HOURS = 2.0   # >2h 说明已经连续几轮没写状态

# 执行心跳（2026-10-25 EXEC-R8）：alert_watch 每 5 分钟写一次（含 rc=3 正确拒跑）。
# 状态文件衡量"最后一次成功"，心跳衡量"本体是否还在跑"——优先用心跳判活：
#   <15 分钟 → 绿（detail 说明本体在跑/上游不可信）；缺失或过期 → 回退状态文件旧口径。
ALERT_HEARTBEAT_PATH = os.environ.get(
    "ALERT_WATCH_HEARTBEAT",
    os.path.join(r"C:\Temp", "alert_watch_heartbeat.json"))
ALERT_HEARTBEAT_FRESH_MIN = 15.0


def _empty_alert_heartbeat() -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "path": ALERT_HEARTBEAT_PATH,
        "age_min": None,
        "ts": None,
        "rc": None,
        "upstream_ok": None,
        "routes_count": None,
        "note": None,
        "fresh_min": ALERT_HEARTBEAT_FRESH_MIN,
    }


def collect_alert_heartbeat() -> dict[str, Any]:
    """读 alert_watch 执行心跳（只读文件，不发请求）。"""
    result = _empty_alert_heartbeat()
    path = Path(ALERT_HEARTBEAT_PATH)
    try:
        stat = path.stat()
    except FileNotFoundError:
        result["reason"] = f"心跳文件不存在: {path}"
        return result
    except OSError as exc:
        result["reason"] = f"读不到心跳文件: {_short_exc(exc)}"
        return result
    result["available"] = True
    result["age_min"] = round((time.time() - stat.st_mtime) / 60.0, 1)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        result["ts"] = data.get("ts")
        result["rc"] = data.get("rc")
        result["upstream_ok"] = data.get("upstream_ok")
        result["routes_count"] = data.get("routes_count")
        result["note"] = data.get("note")
    return result


def _empty_alert_watch(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "path": str(Path(repo_root) / ALERT_STATE_REL),
        "age_hours": None,
        "last_write": None,
        "state_date": None,
        "state_routes": None,
        "red_hours": ALERT_RED_HOURS,
        "yellow_hours": ALERT_YELLOW_HOURS,
    }


def _collect_alert_state(repo_root: Path) -> dict[str, Any]:
    """量「警报监控最后一次成功写状态」距今多久（只读文件，不发请求）。"""
    result = _empty_alert_watch(repo_root)
    path = Path(result["path"])
    started = time.monotonic()
    try:
        stat = path.stat()
    except FileNotFoundError:
        result["reason"] = f"状态文件不存在: {path}"
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result
    except OSError as exc:
        result["reason"] = f"读不到状态文件: {_short_exc(exc)}"
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result

    age_hours = (time.time() - stat.st_mtime) / 3600.0
    result["age_hours"] = round(age_hours, 2)
    result["last_write"] = _iso(dt.datetime.fromtimestamp(stat.st_mtime).astimezone())
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        result["state_date"] = data.get("date")
        routes = data.get("routes")
        result["state_routes"] = len(routes) if isinstance(routes, dict) else None
    result["available"] = True
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def collect_alert_watch(repo_root: Path) -> dict[str, Any]:
    """状态文件读数 + 执行心跳（2026-10-25 EXEC-R8）。"""
    result = _collect_alert_state(repo_root)
    result["heartbeat"] = collect_alert_heartbeat()
    return result


def _alert_watch_state_brief(watch: dict[str, Any]) -> str:
    """状态文件一行简报（不含"停摆"判词，供心跳新鲜时说明"最后成功"用）。"""
    age = watch.get("age_hours")
    last = watch.get("last_write") or "?"
    date = watch.get("state_date") or "?"
    routes = watch.get("state_routes")
    text = f"最后成功写状态 {last}（状态内日期 {date}"
    if routes is not None:
        text += f"，{routes} 条路由"
    text += "）"
    if age is not None:
        text += f"，距今 {age:.1f} 小时"
    return text


def _alert_watch_detail(watch: dict[str, Any]) -> str:
    text = _alert_watch_state_brief(watch)
    age = watch.get("age_hours")
    if age is not None and age > ALERT_RED_HOURS:
        text += (
            "；警报监控已停摆，期间任何真警报都不会被推送。"
            "注意这是**上游不可信**导致的正确拒跑（6 个源的完成时间证据缺失），"
            "不是脚本崩溃——处置方向是补齐生产者时间戳或调整轮询与新鲜窗口"
        )
    return text


# --- 交易日历（2026-10-26 OS-FIX-MEASURE2）----------------------------------
# 判"周末/非交易日"不能只靠 weekday>=5：A 股法定节假日（春节/国庆/元旦等）在
# 工作日也休市，调休日又可能周末开市。日历产物 =
#   portal/data/trade_cal_open_days.json
#   schema=trade_cal_open_days/v1 · open_days=SSE is_open=1 的 YYYY-MM-DD 列表
#   （由 TuShare trade_cal 生成；gitignored 运行时数据，默认随仓库路径分发）
# 纪律：只读；缺失/坏/空 → 回退 weekday>=5（旧口径），绝不抛异常——健康卡不能
# 因为日历文件坏了就整卡崩。测试用 ZB_TRADE_CAL 覆盖路径（见 tools/tests）。
TRADE_CAL_ENV = "ZB_TRADE_CAL"
TRADE_CAL_SCHEMA = "trade_cal_open_days/v1"
TRADE_CAL_DEFAULT_PATH = (Path(__file__).resolve().parents[1]
                          / "portal" / "data" / "trade_cal_open_days.json")


def _trade_cal_path() -> Path:
    """日历路径：ZB_TRADE_CAL 覆盖 > 仓库默认 portal/data/trade_cal_open_days.json。"""
    override = os.environ.get(TRADE_CAL_ENV, "").strip()
    return Path(override) if override else TRADE_CAL_DEFAULT_PATH


def _load_trade_cal_open_days() -> set[str] | None:
    """读交易日历 → {YYYY-MM-DD}；不可用/坏/空返回 None（调用方回退周末口径）。"""
    try:
        data = json.loads(_trade_cal_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    days = data.get("open_days")
    if not isinstance(days, list) or not days:
        return None
    normalized = set()
    for item in days:
        text = str(item or "").strip()
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            normalized.add(text)
    return normalized or None


def _is_non_trading_day(now: dt.datetime,
                        open_days: set[str] | None = None) -> bool:
    """周末/非交易日判定（2026-10-26 OS-FIX-MEASURE2：接入 A 股交易日历）。

    优先读 `portal/data/trade_cal_open_days.json`（`ZB_TRADE_CAL` 可覆盖）：
    当日不在 open_days → 非交易日（覆盖周末 + 法定节假日/调休）。
    日历缺失/坏/空 → 回退 weekday>=5（旧口径），绝不抛异常（日历坏不能拖垮整卡）。
    本函数只用来给"上游不可信"的 detail 追加"可能属预期"，**不用于判绿/判红**
    ——无法证明的日子不能拿来给故障开脱。
    """
    days = _load_trade_cal_open_days() if open_days is None else open_days
    if not days:
        return now.weekday() >= 5
    return now.strftime("%Y-%m-%d") not in days


def _alert_watch_level(watch: dict[str, Any],
                       now: dt.datetime | None = None) -> tuple[str, str]:
    """ops.alert_watch_liveness 判级（2026-10-25 EXEC-R8；2026-10-26 统一测量修订）。

    优先级：
      1) 执行心跳新鲜（<15min）→ 本体在跑：
         · rc=3 或 upstream_ok=False → **yellow**：本体在跑、脚本按设计拒跑，
           不是停摆；但上游 6 源 stale → 告警内容不可用，处置在数据上游。
           周末/非交易日追加「可能属预期，交易日需补源时间戳」。
         · rc=0 且 upstream_ok=True → green（上游校验通过）；
         · rc 非 0/3 → yellow（本体在跑但本轮脚本自身失败）。
      2) 心跳缺失/过期 → 回退旧状态文件口径（>26h red / >2h yellow / else green），
         detail 标注心跳过期/缺失——过期样本仍会红（反例保留）。

    2026-10-26 口径修订：此前心跳新鲜 + rc=3 判**绿**，把「本体在跑」当成了
    「告警面健康」；实际上 6 个源全 stale，告警内容不可用——绿是误报，
    改为黄（黄 = 有已知问题但不阻断；红留给真故障）。
    """
    now = now or dt.datetime.now()
    hb = watch.get("heartbeat") or {}
    hb_age = hb.get("age_min")
    hb_fresh = (hb.get("available") and hb_age is not None
                and hb_age <= ALERT_HEARTBEAT_FRESH_MIN)
    if hb_fresh:
        rc = hb.get("rc")
        upstream_ok = hb.get("upstream_ok")
        if rc == 3 or upstream_ok is False:
            state_age = watch.get("age_hours")
            waited = f"{state_age:.1f} 小时" if state_age is not None else "未知时长"
            detail = (
                f"本体在跑（心跳 {hb_age:.1f} 分钟前，rc={rc}）、脚本按设计正确拒跑——"
                "不是停摆、不是崩溃；但上游不可信已 "
                f"{waited}（6 源 stale）→ 告警内容不可用，"
                "处置方向是补齐生产者时间戳或调整轮询与新鲜窗口"
            )
            if _is_non_trading_day(now):
                detail += "。当前为周末/非交易日：可能属预期，交易日需补源时间戳"
            return "yellow", detail
        if rc == 0 and upstream_ok is None:
            # 2026-10-26 OS-FIX-MEASURE2 / OS-FIX-ALERT2 联动：alert_watch 在
            # 非交易日按设计跳过上游校验时写 _write_heartbeat(0, None, 0,
            # "non_trading_day…")。该组合本身即"非交易日静默"的生产者契约，
            # 不是"未记录校验结果"的未知态 → 绿（预期内）；detail 写明交易日恢复。
            note = str(hb.get("note") or "").strip()
            detail = ("非交易日静默（预期）：无 EOD 源预期，按设计跳过；"
                      "交易日恢复校验")
            if note:
                detail += "（心跳备注：%s）" % note
            return "green", detail + "；" + _alert_watch_state_brief(watch)
        if rc in (0, None):
            verified = "上游校验通过" if upstream_ok is True else "未记录上游校验结果"
            return (
                "green",
                f"本体在跑（心跳 {hb_age:.1f} 分钟前，rc={rc}，{verified}）；"
                + _alert_watch_state_brief(watch),
            )
        # 心跳新鲜但 rc 非 0/3（1=脚本失败、2=错误…）→ 本体在跑但本轮自身异常，黄。
        return (
            "yellow",
            f"本体在跑但本轮退出码异常（rc={rc}，心跳 {hb_age:.1f} 分钟前）——"
            + str(hb.get("note") or "") + "；" + _alert_watch_state_brief(watch),
        )
    hb_note = (
        f"执行心跳过期（{hb_age:.1f} 分钟前 > {ALERT_HEARTBEAT_FRESH_MIN:.0f} 分钟）"
        if (hb.get("available") and hb_age is not None)
        else "执行心跳缺失（" + str(hb.get("reason") or "未采集") + "）"
    )
    detail = _alert_watch_detail(watch)
    if not watch.get("available"):
        return "red", f"警报监控状态不可判定：{watch.get('reason') or '未知'}；{hb_note}"
    age = watch.get("age_hours")
    if age is None:
        return "red", f"警报监控状态时间不可读：{detail}；{hb_note}"
    if age > ALERT_RED_HOURS:
        return "red", f"{detail}；{hb_note}"
    if age > ALERT_YELLOW_HOURS:
        return "yellow", f"{detail}；{hb_note}"
    return "green", f"{detail}；{hb_note}"


# --- 任务"动作起不来"统计（ops.task_launch_failures）-------------------------
# 2026-10-22 建：`task_exit_audit` 采的是 LastTaskResult 的**一次快照**，而
# QuantWatchdog 每分钟跑一次、每次都因路径乱码起不来——账本把它记成
# 「267009 任务正在运行」，看起来完全正常。快照看不见"每一次都失败"。
# 这一项换成**窗口统计**：读 TaskScheduler/Operational 的 ID 201（任务完成），
# 取 EventData 里的 TaskName / ResultCode（结构化字段，不解析本地化文案），
# 统计最近 24 小时每个任务的非零次数。
#
# 重点盯 `0x8007000x` 家族（十进制 2147942401/2/3）——它们表示**动作根本没起来**
# （无效功能 / 文件未找到 / 路径未找到），与"脚本跑了但报错"性质完全不同。
TASK_LAUNCH_TIMEOUT_SECONDS = 30.0
TASK_LAUNCH_RED_COUNT = 3          # 30 分钟内同一任务 >=3 次启动失败 -> 红
TASK_LAUNCH_RECENT_MINUTES = 30.0  # 判级只看最近这么久（窗口总量仍照实报）
# 2026-10-23 重大更正：Task Scheduler 的 201 事件把**进程退出码**塞进 HRESULT 形态
# （`0x80070000 | exitCode`），于是 exit=3 与 ERROR_PATH_NOT_FOUND 编码完全相同。
# 实测 11/11：Last Result 1/2/3 ↔ 事件码 0x80070001/2/3。
# 所以本检查**只信 `schtasks` 的 Last Result**，事件码一律不作为判据。
# 代价：2026-10-22 那晚把"任务退出 3（按设计拒跑）"误读成"CreateProcess 失败"，
# 白追了六个假设、误停了一个正常任务。详见 重大更正_退出码不是错误码_2026-10-23.md。
TASK_LAUNCH_EXIT_CODE_MAX = 0xFFFF
# PowerShell 的 `-File` 指向不存在的脚本时返回 -196608（= 0xFFFD0000）。
TASK_LAUNCH_POWERSHELL_MISSING = {-196608, 4294770688}
# 0x800710E0 = 任务已在运行/启动请求被拒；schtasks 可能返回有符号负数，
# 必须按无符号形态归一后再比对（否则同一码被误报为“未知返回码”）。
TASK_LAUNCH_ALREADY_RUNNING = {2147946720, -2147020576}
# Windows/Office/OneDrive 等系统任务不属于平台健康面；不得混入未知码统计。
TASK_LAUNCH_SYSTEM_PREFIXES = (
    "microsoft\\windows\\", "microsoft\\office\\", "microsoftedge",
    "onedrive", "microsoft\\",
)
# 调度器自身的状态码，不是任务失败。
TASK_LAUNCH_SCHED_STATES = {
    267009: "任务正在运行",
    267011: "任务尚未运行",
    267014: "任务被强制终止",
    1073807364: "任务被终止(0x40010004)",
}

# 2026-10-23：本采集器已改为读 `schtasks` 的 Last Result，
# 不再解析事件日志——原先那套 wevtutil 常量与时间解析函数一并删除。
# 保留这段注释是为了让后来者知道"为什么这里没有事件查询代码"：
# 事件码会把退出码编码成 0x80070000|N，无法与真正的 Win32 错误区分。


def _empty_task_launch() -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "events_scanned": None,
        "nonzero_total": None,
        "launch_failures": [],
        "launch_failure_total": None,
        "other_nonzero": [],
        "duration_seconds": None,
        "red_count": TASK_LAUNCH_RED_COUNT,
    }


def collect_task_launch_failures() -> dict[str, Any]:
    """检查「任务到底有没有真的跑起来」——只读 `schtasks`，不信事件码。

    为什么要分三类而不是只数"非零"：
      · 退出码 1..65535：**进程跑过了**，返回的是它自己的语义（例如 exit 3 = 主动拒跑）
      · -196608 / 0xFFFD0000：PowerShell 找不到 `-File` 脚本 —— 进程起来了但目标不存在
      · 其它：可能是真正的启动失败，需要人看
    2026-10-22 那晚把第一类当成第三类，代价是 4.5 小时 + 九个探针 + 一次错误停用。
    """
    result = _empty_task_launch()
    cmd = ["schtasks", "/query", "/fo", "CSV", "/nh", "/v"]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=TASK_LAUNCH_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"schtasks 调用失败: {_short_exc(exc)}"
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    if proc.returncode != 0 or not proc.stdout.strip():
        result["reason"] = ("schtasks 返回非零或无输出: "
                            + (_decode(proc.stderr).strip()[:160] or "无 stderr"))
        return result

    # schtasks 的 CSV 是 GBK 输出；先用 utf-8 试，失败回落 gbk。
    text = proc.stdout.decode("utf-8", "replace")
    if "\\" not in text[:200] and "\ufffd" in text[:2000]:
        text = proc.stdout.decode("gbk", "replace")

    # schtasks /v 会对某些任务输出多行（多触发器），按任务名去重取最后一次运行。
    best: dict[str, list[str]] = {}
    for line in text.splitlines():
        cells = line.strip().strip('"').split('","')
        if len(cells) < 8:
            continue
        name = cells[1].strip().lstrip("\\")
        if not name:
            continue
        prev = best.get(name)
        # "2026/10/23 6:41:01" 这种格式可以直接字符串比大小（同宽度可在补齐后比较）
        if prev is None or cells[5].strip() > prev[5].strip():
            best[name] = cells

    dt.datetime.now(dt.timezone.utc)
    scanned = 0
    issues: list[dict[str, Any]] = []      # 动作起来了但目标不存在
    unknown: list[dict[str, Any]] = []     # 未知返回码，需人看
    exits: list[dict[str, Any]] = []       # 跑了但返回非零（业务失败）
    for name, cells in best.items():
        status = cells[3].strip()
        last_run_text = cells[5].strip()
        try:
            code = int(cells[6])
        except ValueError:
            continue
        scanned += 1
        low_name = name.lower()
        if any(low_name.startswith(p) for p in TASK_LAUNCH_SYSTEM_PREFIXES):
            continue
        if code == 0:
            continue
        code_u = code & 0xFFFFFFFF if code < 0 else code
        if code_u in TASK_LAUNCH_ALREADY_RUNNING or code in TASK_LAUNCH_ALREADY_RUNNING:
            item = {"task": name, "code": code, "status": status,
                    "last_run": last_run_text, "minutes_ago": None,
                    "count": 1, "recovered": False, "recent": 0,
                    "meaning": "任务已在运行/启动请求被拒（非失败）"}
            continue
        minutes_ago = None
        try:
            last_run = dt.datetime.strptime(last_run_text, "%Y/%m/%d %H:%M:%S")
            minutes_ago = round((dt.datetime.now() - last_run).total_seconds() / 60.0, 1)
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)
        item = {"task": name, "code": code, "status": status,
                "last_run": last_run_text, "minutes_ago": minutes_ago,
                "count": 1, "recovered": False,
                "recent": (1 if minutes_ago is not None
                           and minutes_ago <= TASK_LAUNCH_RECENT_MINUTES else 0)}
        if code in TASK_LAUNCH_SCHED_STATES:
            item["meaning"] = TASK_LAUNCH_SCHED_STATES[code] + "（非失败）"
            continue
        if code in TASK_LAUNCH_POWERSHELL_MISSING:
            item["meaning"] = "PowerShell 找不到 -File 脚本（动作起来了但目标不存在）"
            issues.append(item)
        elif 0 < code <= TASK_LAUNCH_EXIT_CODE_MAX:
            item["meaning"] = f"退出码 {code}（进程跑过了，这是它自己的返回值）"
            exits.append(item)
        else:
            item["meaning"] = f"未知返回码 {code}（需人看，不一定是启动失败）"
            unknown.append(item)

    for bucket in (issues, unknown, exits):
        bucket.sort(key=lambda x: (x.get("status") == "Disabled",
                                   -(x.get("recent") or 0),
                                   x.get("minutes_ago") or 0))
    result["events_scanned"] = scanned
    result["launch_failures"] = issues
    result["launch_failure_total"] = len(issues)
    result["launch_failure_recent"] = sum(
        1 for x in issues if x.get("recent") and x.get("status") != "Disabled")
    result["launch_failure_active"] = sum(
        1 for x in issues if x.get("status") != "Disabled")
    result["unknown_codes"] = unknown[:12]
    result["other_nonzero"] = exits[:12]
    result["nonzero_total"] = len(issues) + len(unknown) + len(exits)
    result["available"] = True
    return result


def _task_launch_detail(info: dict[str, Any]) -> str:
    issues = [x for x in (info.get("launch_failures") or [])
              if x.get("status") != "Disabled"]
    disabled = [x for x in (info.get("launch_failures") or [])
                if x.get("status") == "Disabled"]
    unknown = info.get("unknown_codes") or []
    exits = info.get("other_nonzero") or []

    parts = []
    if issues:
        parts.append("动作起来了但目标不存在：" + "、".join(
            "%s（rc=%d）" % (x["task"], x["code"]) for x in issues[:3]))
    if unknown:
        # 同一秒出现的一批通常是休眠/关机打断，不是逐个故障——这个提示很关键。
        stamps = {x.get("last_run") for x in unknown if x.get("last_run")}
        hint = ""
        if len(stamps) == 1 and len(unknown) > 2:
            hint = "（且全部落在同一秒 %s，多半是休眠/关机打断，不是 %d 个独立故障）" % (
                next(iter(stamps)), len(unknown))
        parts.append("未知返回码 %d 个%s：%s" % (
            len(unknown), hint,
            "、".join("%s(%d)" % (x["task"][-18:], x["code"]) for x in unknown[:3])))
    if exits:
        parts.append("退出码非零 %d 个（进程跑过了，是业务返回值）：%s" % (
            len(exits),
            "、".join("%s=%d" % (x["task"][-18:], x["code"]) for x in exits[:4])))
    if disabled:
        parts.append("已停用不计：%s" % "、".join(x["task"] for x in disabled[:3]))

    head = "扫描 %d 个任务（口径=schtasks Last Result；事件码不作为判据）" % (
        info.get("events_scanned") or 0)
    if not parts:
        return head + "；无异常"
    return head + "。" + "；".join(parts)


def _task_launch_level(info: dict[str, Any]) -> tuple[str, str]:
    """ops.task_launch_failures 判级（2026-10-26 统一测量口径）。

    采集不可用（典型：沙箱/权限会话里 `schtasks` 报错 → available=False）
    是**测量缺口**，不是任务故障：判黄 N/A、不计红，需管理员会话复核。
    只有两种情形判红：ops 块缺失（采集器未运行），或真正拿到 available=True
    且有「动作起来了但目标不存在」的启动失败。
    """
    if not info:
        return "red", "任务启动失败统计未运行（ops 块缺失）"
    if not info.get("available"):
        return (
            "yellow",
            "N/A（不可采集：schtasks 不可用——沙箱/权限会话；不计红，"
            "需管理员会话复核）：" + str(info.get("reason") or "未知"),
        )
    # 2026-10-23 口径（旧口径把"退出码 N"误当"启动失败"，已废弃）：
    #   red    = 动作起来了但目标不存在（如 PowerShell -File 脚本缺失）且任务在启用中
    #   yellow = 有未知返回码，需人看一眼（同一秒的一批通常是休眠/关机打断）
    #   green  = 两者皆无；"退出码非零"只在 detail 里陈述，不驱动颜色——
    #            那属于业务失败，归 task_audit.failures 管，不在这里重复计数
    issues = [x for x in (info.get("launch_failures") or [])
              if x.get("status") != "Disabled"]
    unknown = [x for x in (info.get("unknown_codes") or [])
               if x.get("status") != "Disabled"]
    detail = _task_launch_detail(info)
    if issues:
        return "red", detail
    if unknown:
        return "yellow", detail
    return "green", detail


def _summary_age_hours(audit: dict[str, Any]) -> float | None:
    """账本摘要有多大年纪（小时）。

    摘要里的 failed 是**扫描那一刻**的 LastTaskResult 快照，不会自己更新。
    隔了半天再看，其中一些失败可能早已自愈——把年龄写进结论，读的人才不会
    把陈旧数字当成现状（2026-10-22：01:55 的快照到 17:25 已有两条过期）。
    """
    stamp = audit.get("last_scan_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        when = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.astimezone()
    return max(0.0, (dt.datetime.now(when.tzinfo) - when).total_seconds() / 3600.0)


def collect_docs_index(repo_root: Path) -> dict[str, Any]:
    """用子进程跑 `gen_docs_index.py --check`；0=绿、1=黄、其余/超时=红。"""
    result = _empty_docs_index(repo_root)
    script = repo_root / DOCS_INDEX_SCRIPT_REL
    result["script"] = str(script)
    if not script.is_file():
        result["reason"] = f"找不到索引脚本: {script}"
        return result

    command = [sys.executable, "-B", str(script), "--check"]
    result["command"] = command
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=DOCS_CHECK_TIMEOUT_SECONDS,
            check=False,
            cwd=str(repo_root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["timed_out"] = True
        result["reason"] = f"gen_docs_index.py --check 超时（>{DOCS_CHECK_TIMEOUT_SECONDS:g}s）"
        return result
    except (OSError, ValueError) as exc:
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["reason"] = f"无法执行 gen_docs_index.py --check: {_short_exc(exc)}"
        return result

    elapsed = round(time.monotonic() - started, 3)
    result["duration_seconds"] = elapsed
    result["exit_code"] = proc.returncode
    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    excerpt = stdout.strip()
    if not excerpt:
        excerpt = stderr.strip()
    result["output_excerpt"] = excerpt[:DOCS_OUTPUT_EXCERPT_CHARS]
    result["output_truncated"] = len(excerpt) > DOCS_OUTPUT_EXCERPT_CHARS

    match = DOCS_TOTALS_RE.search(stdout)
    if match is not None:
        diffs = {key: int(value) for key, value in match.groupdict().items()}
        result["parsed"] = True
        result["diffs"] = diffs
        result["diffs_total"] = sum(diffs.values())

    if elapsed > DOCS_CHECK_BUDGET_SECONDS:
        result["reason"] = (
            f"--check 耗时 {elapsed:.2f}s 超出 {DOCS_CHECK_BUDGET_SECONDS:g}s 预算，结果不采信"
        )
        # 不可判定时计数一律置 null，不用半截结果冒充可用。
        result["parsed"] = False
        result["diffs"] = None
        result["diffs_total"] = None
        return result
    if proc.returncode == 0:
        result["available"] = True
        result["status"] = "fresh"
        result["level"] = "green"
        return result
    if proc.returncode == 1:
        # 退出 1 是「索引落后」的业务信号，不是读不到：不判红，交给人重生成。
        result["available"] = True
        result["status"] = "stale"
        result["level"] = "yellow"
        return result

    result["reason"] = f"gen_docs_index.py --check 退出码 {proc.returncode}"
    if result["output_excerpt"]:
        result["reason"] += "；输出：" + result["output_excerpt"]
    return result


# ---------------------------------------------------------------------------
# 2b) 备份是否追平 HEAD（bundle 与仓库 HEAD 对比，只读）
# ---------------------------------------------------------------------------
def _short_sha(sha: Any) -> str:
    return sha[:8] if isinstance(sha, str) and sha else "-"


def _empty_catchup() -> dict[str, Any]:
    return {
        "available": False,
        "complete": False,
        "reason": None,
        "checked_date": None,
        "status": None,
        "level": None,
        "behind_count": None,
        "unknown_count": None,
        "max_behind_commits": None,
        "backup_age_hours": None,
        "thresholds_hours": {"red": CATCHUP_RED_HOURS},
        "repos": [],
        "entries": [],
        "behind": [],
        "unknown": [],
    }


def _discover_catchup_repos(repo_root: Path) -> list[dict[str, Any]]:
    """四个备份仓固定在工作区（repo_root 的父目录）下；缺目录也要列出来。

    缺目录不能静默略过：略过会让"3 个仓追平"冒充"全部追平"。缺的那个会以
    unknown 计入，从而让 catchup 不判绿。
    """
    workspace = repo_root.parent
    repos: list[dict[str, Any]] = []
    for label, dirname in CATCHUP_REPO_DIRS:
        path = workspace / dirname
        if label == "aios" and not path.is_dir() and repo_root.name in (
            "老田的AI助手",
            "aios",
        ):
            path = repo_root
        repos.append({"label": label, "path": path, "exists": path.is_dir()})
    return repos


def _bundle_for_label(bundle_files: list[str], label: str) -> str | None:
    prefix = label + "-"
    for name in bundle_files:
        if name.startswith(prefix) and name.lower().endswith(".bundle"):
            return name
    return None


def _parse_bundle_heads(text: str) -> dict[str, str]:
    heads: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not SHA_RE.match(parts[0]):
            continue
        heads[" ".join(parts[1:])] = parts[0].lower()
    return heads


def _pick_bundle_ref(
    heads: dict[str, str], branch: str | None
) -> tuple[str | None, str | None, str | None]:
    """当前分支 → master → main → HEAD → 唯一引用；返回 (ref, sha, note)。"""
    candidates: list[str] = []
    if branch and branch not in ("HEAD", ""):
        candidates.append("refs/heads/" + branch)
        candidates.extend(CATCHUP_REF_CANDIDATES)
    else:
        # detached HEAD：bundle 的 HEAD 行才是备份时真正检出的提交。
        candidates.append("HEAD")
        candidates.extend(
            ref for ref in CATCHUP_REF_CANDIDATES if ref != "HEAD"
        )
    seen: set[str] = set()
    for ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        if ref in heads:
            return ref, heads[ref], None
    if len(heads) == 1:
        ref, sha = next(iter(heads.items()))
        return ref, sha, f"bundle 缺少期望分支引用，退化为唯一引用 {ref}"
    return (
        None,
        None,
        "bundle 缺少期望分支引用（现有: %s）"
        % (", ".join(sorted(heads)) or "none"),
    )


def collect_catchup(
    repo_root: Path, backup: dict[str, Any], now: dt.datetime
) -> dict[str, Any]:
    """用最新备份里的 bundle 逐仓比较 HEAD；结果可供 _evaluate 判黄/红。"""
    del now  # 落后时长用备份目录 age_hours，不另算当前时间
    result = _empty_catchup()
    result["backup_age_hours"] = backup.get("age_hours")
    if not backup.get("available"):
        result["reason"] = "备份不可用：" + (backup.get("reason") or "未知原因")
        return result
    bundle_files = backup.get("bundle_files") or []
    if not bundle_files:
        result["reason"] = "最新 MANIFEST 没有 *.bundle 条目"
        return result
    if shutil.which("git") is None:
        result["reason"] = "找不到 git 可执行文件"
        return result

    latest_dir = Path(backup["dir"]) if backup.get("dir") else None
    repos = _discover_catchup_repos(repo_root)
    if latest_dir is None:
        result["reason"] = "无法定位最新备份目录"
        return result
    result["checked_date"] = backup.get("latest_date")
    result["repos"] = [
        {
            "label": spec["label"],
            "path": str(spec["path"]),
            "exists": spec["exists"],
        }
        for spec in repos
    ]

    for spec in repos:
        label = spec["label"]
        repo_path: Path = spec["path"]
        entry: dict[str, Any] = {
            "label": label,
            "date": backup.get("latest_date"),
            "repo": str(repo_path),
            "bundle": None,
            "bundle_name": None,
            "ref": None,
            "ref_note": None,
            "bundle_sha": None,
            "bundle_sha_short": None,
            "head_sha": None,
            "head_sha_short": None,
            "match": None,
            "behind_commits": "unknown",
            "status": "unknown",
            "reason": None,
        }
        branch: str | None = None

        if not spec["exists"]:
            entry["reason"] = f"仓目录不存在: {repo_path}"
        else:
            try:
                probe = _git_run(repo_path, ["rev-parse", "--is-inside-work-tree"])
            except (OSError, subprocess.TimeoutExpired) as exc:
                entry["reason"] = f"git 探测失败: {_short_exc(exc)}"
            else:
                if probe.returncode != 0:
                    entry["reason"] = "不是 git 工作树: " + (
                        _decode(probe.stderr).strip()[:200] or "git 返回非零"
                    )
                else:
                    try:
                        head_proc = _git_run(repo_path, ["rev-parse", "HEAD"])
                        branch_proc = _git_run(
                            repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        entry["reason"] = f"git rev-parse 失败: {_short_exc(exc)}"
                    else:
                        if head_proc.returncode == 0:
                            lines = _decode(head_proc.stdout).strip().splitlines()
                            if lines:
                                entry["head_sha"] = lines[0].lower()
                                entry["head_sha_short"] = _short_sha(lines[0])
                        else:
                            entry["reason"] = "git rev-parse HEAD 失败: " + (
                                _decode(head_proc.stderr).strip()[:200]
                                or "git 返回非零"
                            )
                        if branch_proc.returncode == 0:
                            branch = _decode(branch_proc.stdout).strip() or None

        bundle_name = _bundle_for_label(bundle_files, label)
        if bundle_name is None:
            if entry["reason"] is None:
                entry["reason"] = f"MANIFEST 里没有 {label}-*.bundle"
        else:
            bundle_path = latest_dir / bundle_name
            entry["bundle_name"] = bundle_name
            entry["bundle"] = str(bundle_path)
            if not bundle_path.is_file():
                if entry["reason"] is None:
                    entry["reason"] = "MANIFEST 列了 bundle 但文件不存在"
            elif entry["reason"] is None:
                try:
                    heads_proc = _git_run(
                        repo_path, ["bundle", "list-heads", str(bundle_path)]
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    entry["reason"] = f"git bundle list-heads 失败: {_short_exc(exc)}"
                else:
                    if heads_proc.returncode != 0:
                        entry["reason"] = "git bundle list-heads 失败: " + (
                            _decode(heads_proc.stderr).strip()[:240]
                            or "git 返回非零"
                        )
                    else:
                        heads = _parse_bundle_heads(_decode(heads_proc.stdout))
                        if not heads:
                            entry["reason"] = "bundle 里解析不到任何引用"
                        else:
                            ref, bundle_sha, ref_note = _pick_bundle_ref(
                                heads, branch
                            )
                            entry["ref"] = ref
                            entry["ref_note"] = ref_note
                            entry["bundle_sha"] = bundle_sha
                            entry["bundle_sha_short"] = _short_sha(bundle_sha)
                            if bundle_sha is None or entry["head_sha"] is None:
                                entry["reason"] = ref_note or "缺少 bundle/HEAD 的 SHA"
                            elif bundle_sha == entry["head_sha"]:
                                entry["match"] = True
                                entry["behind_commits"] = 0
                                entry["status"] = "current"
                            else:
                                entry["match"] = False
                                exists = _git_run(
                                    repo_path,
                                    ["cat-file", "-e", bundle_sha + "^{commit}"],
                                )
                                if exists.returncode != 0:
                                    entry["reason"] = (
                                        "bundle 提交 %s 不在本仓对象库，无法计算落后数"
                                        % _short_sha(bundle_sha)
                                    )
                                else:
                                    count_proc = _git_run(
                                        repo_path,
                                        ["rev-list", "--count", bundle_sha + "..HEAD"],
                                    )
                                    if count_proc.returncode != 0:
                                        entry["reason"] = (
                                            "git rev-list --count 失败: "
                                            + (
                                                _decode(count_proc.stderr).strip()[:200]
                                                or "git 返回非零"
                                            )
                                        )
                                    else:
                                        try:
                                            count = int(
                                                _decode(count_proc.stdout)
                                                .strip()
                                                .splitlines()[0]
                                            )
                                        except (ValueError, IndexError):
                                            entry["reason"] = "git rev-list 输出无法解析"
                                        else:
                                            entry["behind_commits"] = count
                                            if count > 0:
                                                entry["status"] = "behind"
                                            else:
                                                # SHA 不同但计数为 0：不是"落后"
                                                # （典型是 bundle 比 HEAD 还新/已分叉），
                                                # 记 unknown，绝不冒充 current。
                                                entry["status"] = "unknown"
                                                entry["reason"] = (
                                                    "bundle 提交与 HEAD 不同且 "
                                                    "rev-list 计数为 0（非落后/可能分叉）"
                                                )

        if entry["status"] == "behind":
            result["behind"].append(entry)
        elif entry["status"] == "unknown":
            result["unknown"].append(entry)
        result["entries"].append(entry)

    behind_counts = [
        entry["behind_commits"]
        for entry in result["behind"]
        if isinstance(entry["behind_commits"], int)
    ]
    result["behind_count"] = len(result["behind"])
    result["unknown_count"] = len(result["unknown"])
    result["max_behind_commits"] = max(behind_counts) if behind_counts else None
    result["complete"] = bool(result["entries"]) and not result["unknown"]

    age = backup.get("age_hours")
    if result["behind"]:
        result["status"] = "behind"
        result["level"] = (
            "red"
            if isinstance(age, (int, float)) and age > CATCHUP_RED_HOURS
            else "yellow"
        )
        # 已经判出"落后"就是可判定；即使另有仓 unknown，这项也要报黄/红。
        result["available"] = True
    elif result["unknown"] or not result["entries"]:
        # 没有落后、但还有仓判不了 → 整项计 null，绝不冒充绿。
        result["status"] = "unknown"
        result["level"] = None
        result["available"] = False
        if not result["reason"]:
            determinable = [
                entry
                for entry in result["entries"]
                if entry["status"] in ("current", "behind")
            ]
            result["reason"] = (
                "部分仓无法判定" if determinable else "所有仓都无法判定"
            )
    else:
        result["status"] = "current"
        result["level"] = "green"
        result["available"] = True
    return result


def _catchup_behind_text(catchup: dict[str, Any]) -> str:
    parts = []
    for entry in catchup.get("behind") or []:
        parts.append(
            "%s 落后 %s 个提交（backup %s < head %s）"
            % (
                entry["label"],
                entry["behind_commits"],
                entry["bundle_sha_short"],
                entry["head_sha_short"],
            )
        )
    unknown = catchup.get("unknown") or []
    if unknown:
        parts.append("另有 %d 个仓无法判定" % len(unknown))
    return "；".join(parts) or "无法判定"


def _worktree_ttl_detail(ttl: dict[str, Any]) -> str:
    days = ttl.get("threshold_days") or WORKTREE_TTL_DAYS
    expired = ttl.get("expired") or []
    dirty = ttl.get("dirty_expired_worktrees") or []
    parts: list[str] = []
    if expired:
        bits = [
            "%s（%s，%.1f 天）"
            % (item["path"], item.get("branch") or "?", item.get("age_days") or 0.0)
            for item in expired
        ]
        text = "%d 个已合并且超过 %g 天未清理：%s" % (
            ttl.get("violations") or 0,
            days,
            "、".join(bits),
        )
        hidden = (ttl.get("violations") or 0) - len(bits)
        if hidden > 0:
            text += "；另有 %d 个未列出" % hidden
        parts.append(text)
    if dirty:
        # 规则 9 的前提是「无未提交改动」。这些树已合并、已超期，但还带着
        # 没进任何提交的改动，直接 remove 会丢工作 —— 必须人来决定去向。
        dbits = [
            "%s（%s，%.1f 天）"
            % (item["path"], item.get("branch") or "?", item.get("age_days") or 0.0)
            for item in dirty
        ]
        text = "%d 个已合并且超过 %g 天，但仍持有未提交改动，不可自动回收：%s" % (
            ttl.get("dirty_expired") or 0,
            days,
            "、".join(dbits),
        )
        hidden = (ttl.get("dirty_expired") or 0) - len(dbits)
        if hidden > 0:
            text += "；另有 %d 个未列出" % hidden
        parts.append(text)
    detail = "；".join(parts)
    if ttl.get("unknown"):
        detail += "；另有 %d 个无法判定" % ttl["unknown"]
    if expired:
        return detail + "（规则 9，需 git worktree remove）"
    return detail + "（规则 9 前提是「无未提交改动」：先决定这些改动的去向，再回收）"


def _worktree_ttl_unknown_detail(ttl: dict[str, Any]) -> str:
    unknown = ttl.get("unknown_worktrees") or []
    bits = [
        "%s（%s）" % (item["path"], item.get("unknown_reason") or "未知")
        for item in unknown
    ]
    detail = "%d 个关联工作树无法判定是否已合并/年龄：%s" % (
        ttl.get("unknown") or 0,
        "、".join(bits),
    )
    hidden = (ttl.get("unknown") or 0) - len(bits)
    if hidden > 0:
        detail += "；另有 %d 个未列出" % hidden
    return detail + "；不判绿"


def _docs_index_detail(freshness: dict[str, Any]) -> str:
    if freshness.get("status") == "stale":
        if freshness.get("parsed"):
            diffs = freshness.get("diffs") or {}
            return (
                "DOCS_INDEX 落后：%s（差异合计 %d 处）；"
                "运行 python -B tools/gen_docs_index.py 重生成"
                % (_docs_diff_text(diffs), freshness.get("diffs_total") or 0)
            )
        excerpt = (freshness.get("output_excerpt") or "（无输出）").replace("\n", " ")
        return "DOCS_INDEX 落后（退出码 1，未解析）；原始输出前 200 字：%s" % excerpt
    duration = freshness.get("duration_seconds")
    suffix = "，%.2fs" % duration if isinstance(duration, (int, float)) else ""
    return "DOCS_INDEX 与生成结果一致（退出码 0%s）" % suffix


# ---------------------------------------------------------------------------
# 明文私钥扫描（security.plaintext_keys）
# ---------------------------------------------------------------------------
# 只看「文件名 + 内容特征」，不解析密钥格式、不打印文件内容；命中只回显
# 「路径 + 字节数 + 是否被 git 跟踪」。判定口径在 _evaluate 里，采集侧只报事实。
def _empty_plaintext_keys(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "workspace_root": str(repo_root.parent),
        "scan_dirs": [str(repo_root / rel) for rel in PLAINTEXT_KEY_SCAN_REL_DIRS],
        "missing_dirs": [],
        "scanned_files": 0,
        "scanned_bytes": 0,
        "oversized_skipped": 0,
        "findings": [],
        "skipped_dirs": [],
        "unreadable_files": [],
        "git_checked": False,
        "budget_seconds": PLAINTEXT_KEY_BUDGET_SECONDS,
        "max_content_bytes": PLAINTEXT_KEY_MAX_CONTENT_BYTES,
        "elapsed_ms": None,
        "over_budget": None,
    }


def _plaintext_key_display_path(path: Path, repo_root: Path) -> str:
    """命中路径：仓内给相对 POSIX 路径，仓外（工作区根）给绝对路径。"""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _plaintext_key_name_rule(name: str) -> str | None:
    """按文件名判定「弱特征」候选：*.pem / *.key、id_rsa*、.env*。"""
    lower = name.lower()
    if lower.endswith(PLAINTEXT_KEY_NAME_SUFFIXES):
        return "name"
    if lower.startswith(".env"):
        return "env"
    if lower.startswith("id_rsa"):
        return "id_rsa"
    return None


def _looks_like_test_fixture(path: Path) -> bool:
    """triage 用：test_* / *_test.py / _selftest.py 或 tests/fixtures 目录下的命中。"""
    name = path.name.lower()
    if any(name.startswith(prefix) for prefix in PLAINTEXT_KEY_FIXTURE_NAME_PREFIXES):
        return True
    if any(name.endswith(suffix) for suffix in PLAINTEXT_KEY_FIXTURE_NAME_SUFFIXES):
        return True
    return any(
        parent.name.lower() in PLAINTEXT_KEY_FIXTURE_DIRS
        for parent in list(path.parents)[:4]
    )


def _scan_for_plaintext_keys(repo_root: Path, result: dict[str, Any]) -> None:
    """限定范围扫描：只累计计数/命中/跳过项，判定留给 _evaluate。"""
    deadline = time.monotonic() + PLAINTEXT_KEY_BUDGET_SECONDS
    findings: list[dict[str, Any]] = result["findings"]
    skipped_dirs: list[str] = result["skipped_dirs"]
    unreadable_files: list[str] = result["unreadable_files"]
    timeout_reason = "扫描超时（>%gs，已扫到的命中仍列在 detail）" % (
        PLAINTEXT_KEY_BUDGET_SECONDS
    )

    def _mark_timeout() -> bool:
        if time.monotonic() <= deadline:
            return False
        result["available"] = False
        result["reason"] = timeout_reason
        return True

    def _display(path: Path) -> str:
        return _plaintext_key_display_path(path, repo_root)

    def _record(path: Path, size: int, rule: str) -> None:
        findings.append(
            {
                "path": _display(path),
                "bytes": size,
                # 只报「是否被 git 跟踪」；内容一个字节都不落进 JSON。
                "in_git": None,
                "rule": rule,
                "test_fixture": _looks_like_test_fixture(path),
            }
        )

    def _inspect(path: Path, size: int) -> None:
        rule = _plaintext_key_name_rule(path.name)
        if rule is None and size > PLAINTEXT_KEY_MAX_CONTENT_BYTES:
            result["oversized_skipped"] += 1
            return
        try:
            with path.open("rb") as handle:
                content = handle.read(PLAINTEXT_KEY_MAX_CONTENT_BYTES + 1)
        except OSError:
            unreadable_files.append(_display(path))
            return
        result["scanned_files"] += 1
        result["scanned_bytes"] += len(content)
        if rule is not None:
            # 名称命中：内容含 PRIVATE KEY 字样即算（真 PEM 的头和主体都含）。
            if PLAINTEXT_KEY_WEAK_MARKER in content:
                _record(path, size, rule)
            return
        if PLAINTEXT_KEY_MARKER_RE.search(content):
            _record(path, size, "marker")

    def _walk(root: Path) -> bool:
        """递归扫一个限定目录；False = 已超时中止。"""
        stack = [root]
        while stack:
            current = stack.pop()
            if _mark_timeout():
                return False
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if _mark_timeout():
                            return False
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in PLAINTEXT_KEY_SKIP_DIRS:
                                    continue
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                _inspect(
                                    Path(entry.path),
                                    entry.stat(follow_symlinks=False).st_size,
                                )
                        except OSError:
                            # 单个条目 stat 失败：记文件级不可读，不中断整目录
                            unreadable_files.append(_display(Path(entry.path)))
            except OSError:
                # 目录读不到（ACL 拒绝等）：记下来，最后判黄而不是判绿
                skipped_dirs.append(_display(current))
        return True

    for rel_dir in PLAINTEXT_KEY_SCAN_REL_DIRS:
        root = repo_root / rel_dir
        try:
            exists = root.is_dir()
        except OSError:
            exists = False
        if not exists:
            result["missing_dirs"].append(_display(root))
            continue
        if not _walk(root):
            return

    # 工作区根一级：只看候选名（*.pem/*.key/id_rsa*/.env*），不递归、不读其它文件。
    workspace = repo_root.parent
    if workspace == repo_root:
        return
    try:
        with os.scandir(workspace) as entries:
            for entry in entries:
                if _mark_timeout():
                    return
                if _plaintext_key_name_rule(entry.name) is None:
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    _inspect(Path(entry.path), entry.stat(follow_symlinks=False).st_size)
                except OSError:
                    unreadable_files.append(_display(Path(entry.path)))
    except OSError:
        skipped_dirs.append(_display(workspace))


def _fill_git_state(repo_root: Path, findings: list[dict[str, Any]]) -> bool:
    """给命中补 in_git（True/False/None）；只读 git，失败一律留 None=未知。"""
    if not findings:
        return False
    deadline = time.monotonic() + PLAINTEXT_KEY_GIT_BUDGET_SECONDS
    try:
        probe = _git_run(
            repo_root,
            ["rev-parse", "--is-inside-work-tree"],
            timeout=PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0 or _decode(probe.stdout).strip() != "true":
        return False
    checked = False
    for finding in findings:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        rel = str(finding.get("path") or "")
        if not rel or Path(rel).is_absolute():
            continue  # 仓外文件不做 git 判定，保持 None
        try:
            proc = _git_run(
                repo_root,
                ["ls-files", "--error-unmatch", "--", rel],
                timeout=max(0.2, min(PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS, remaining)),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        checked = True
        if proc.returncode == 0:
            finding["in_git"] = True
        elif proc.returncode == 1:
            finding["in_git"] = False
        # 其它退出码（非仓库/异常）保持 None，不冒充「未跟踪」
    return checked


def collect_plaintext_keys(repo_root: Path) -> dict[str, Any]:
    """明文私钥采集：范围/识别/性能上限见模块 docstring 第 7 条。"""
    started = time.monotonic()
    result = _empty_plaintext_keys(repo_root)
    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result
    except OSError as exc:
        result["reason"] = "读取仓库根失败: " + _short_exc(exc)
        return result
    result["available"] = True
    try:
        _scan_for_plaintext_keys(repo_root, result)
    except Exception as exc:  # 兜底：体检项不许把整张卡带崩，读不到就判不可用（红）
        result["available"] = False
        result["reason"] = "扫描异常: " + _short_exc(exc)
    result["git_checked"] = _fill_git_state(repo_root, result["findings"])
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    result["over_budget"] = result["elapsed_ms"] > PLAINTEXT_KEY_BUDGET_SECONDS * 1000.0
    return result


def _git_state_text(in_git: Any) -> str:
    if in_git is True:
        return "已跟踪"
    if in_git is False:
        return "未跟踪"
    return "未知"


def _plaintext_keys_detail(keys: dict[str, Any]) -> str:
    """detail 只写路径 / 字节数 / git 状态；绝不写文件内容。"""
    findings = keys.get("findings") or []
    skipped_dirs = keys.get("skipped_dirs") or []
    unreadable_files = keys.get("unreadable_files") or []
    bits: list[str] = []
    if not keys.get("available"):
        bits.append("明文私钥扫描未完成：" + (keys.get("reason") or "未知原因"))
    if findings:
        shown = findings[:PLAINTEXT_KEY_DETAIL_LIMIT]
        hidden = len(findings) - len(shown)
        items = "、".join(
            "%s（%s，git:%s）"
            % (item["path"], _human_bytes(item["bytes"]), _git_state_text(item.get("in_git")))
            for item in shown
        )
        bits.append(
            "发现 %d 个明文私钥%s：%s"
            % (len(findings), "（另有 %d 个未列出）" % hidden if hidden > 0 else "", items)
        )
    elif not keys.get("available"):
        bits.append(
            "已扫 %d 个文件（%s），扫描不完整，不能判定无明文私钥"
            % (
                keys.get("scanned_files") or 0,
                _human_bytes(keys.get("scanned_bytes") or 0),
            )
        )
    else:
        bits.append(
            "已扫 %d 个文件（%s），未见明文私钥"
            % (
                keys.get("scanned_files") or 0,
                _human_bytes(keys.get("scanned_bytes") or 0),
            )
        )
    if skipped_dirs:
        bits.append(
            "%d 个目录读不到：%s" % (len(skipped_dirs), "、".join(skipped_dirs[:3]))
        )
    if unreadable_files:
        bits.append(
            "%d 个文件读不到：%s"
            % (len(unreadable_files), "、".join(unreadable_files[:3]))
        )
    if skipped_dirs or unreadable_files:
        bits.append("有内容没看到，不判绿")
    if keys.get("oversized_skipped"):
        bits.append(
            "%d 个 >%s 的文件未读内容"
            % (
                keys["oversized_skipped"],
                _human_bytes(PLAINTEXT_KEY_MAX_CONTENT_BYTES),
            )
        )
    return "；".join(bits)


# ---------------------------------------------------------------------------
# 4) 本地推理能力（ai.inference）
# ---------------------------------------------------------------------------
# 采集侧只报事实（可达/已装/已驻留/ctx/content 长度/吞吐），判级口径在 _evaluate。
# 全部请求 build_opener(ProxyHandler({})) 直连：系统代理会把 127.0.0.1 也接管，
# 「服务没开」于是长出「代理连不上」的假原因（仓里多份判例，见 tools/qa_run.py）。
def _empty_ai_inference() -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "host": AI_OLLAMA_HOST,
        "model": AI_MODEL,
        "reachable": None,
        "installed": None,
        "installed_models": None,
        "ps_ok": None,
        "ps_error": None,
        "resident": None,
        "ctx": None,
        "expected_ctx": AI_NUM_CTX,
        "chat_ok": None,
        "chat_error": None,
        "content_excerpt": None,
        "content_len": None,
        "thinking_len": None,
        "answer_matches": None,
        "eval_count": None,
        "eval_duration_seconds": None,
        "toks_per_second": None,
        "done_reason": None,
        "timed_out": False,
        "elapsed_seconds": None,
        "budget_seconds": AI_BUDGET_SECONDS,
        "over_budget": False,
    }


def _ai_http_json(
    url: str, timeout: float, payload: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """直连 Ollama 取/送 JSON：返回 (对象, 错误文案, 是否超时)。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # 4xx/5xx：通道在、调用失败（模型没拉/显存爆/参数不认）。读不到就说读不到。
        return None, "HTTP %s %s" % (exc.code, exc.reason), False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
            return None, "%gs 未返回" % timeout, True
        return None, "%s: %s" % (type(exc).__name__, reason), False
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, "响应不是 JSON: " + _short_exc(exc), False
    if not isinstance(parsed, dict):
        return None, "响应不是 JSON 对象（%s）" % type(parsed).__name__, False
    return parsed, None, False


def _ai_model_entry(models: Any, model: str) -> dict[str, Any] | None:
    """在 /api/tags 或 /api/ps 的 models[] 里找同名条目。"""
    if not isinstance(models, list):
        return None
    for entry in models:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == model or entry.get("model") == model:
            return entry
    return None


def collect_ai_inference() -> dict[str, Any]:
    """探测一次本地推理（口径与阈值见模块 docstring 第 9 条）。"""
    started = time.monotonic()
    result = _empty_ai_inference()

    def finish() -> dict[str, Any]:
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        result["over_budget"] = result["elapsed_seconds"] > AI_BUDGET_SECONDS
        return result

    tags, tags_error, tags_timeout = _ai_http_json(
        AI_OLLAMA_HOST + "/api/tags", AI_TAGS_TIMEOUT_SECONDS
    )
    if tags is None:
        result["reason"] = "Ollama 不可达（%s）：%s" % (AI_OLLAMA_HOST, tags_error)
        result["timed_out"] = tags_timeout
        return finish()
    result["reachable"] = True
    models = tags.get("models")
    result["installed_models"] = len(models) if isinstance(models, list) else None
    result["installed"] = _ai_model_entry(models, AI_MODEL) is not None

    ps, ps_error, ps_timeout = _ai_http_json(
        AI_OLLAMA_HOST + "/api/ps", AI_PS_TIMEOUT_SECONDS
    )
    if ps is None:
        # tags 刚通过，说明服务活着；只是驻留状态读不到 —— 不知道就不判绿。
        result["ps_ok"] = False
        result["ps_error"] = ps_error
        result["timed_out"] = ps_timeout
        return finish()
    result["ps_ok"] = True
    # tags/ps 都通了：探测本身已经跑起来。后面生成失败是「通道活着但调用失败」，
    # 不能再退回 available=false（那会被判成「Ollama 不可达」，掩盖真因）。
    result["available"] = True
    resident = _ai_model_entry(ps.get("models"), AI_MODEL)
    result["resident"] = resident is not None
    if resident is None:
        # 关键取舍：不为「测一下」把 5.5 GiB 的模型冷加载起来，那会把健康卡拖慢
        # 十几秒，而健康卡是要每天每分钟看的。
        result["reason"] = "未驻留，跳过生成以免冷加载拖慢健康卡"
        return finish()

    ctx = resident.get("context_length")
    result["ctx"] = ctx if isinstance(ctx, int) else None

    payload = {
        "model": AI_MODEL,
        "stream": False,
        # 思考模型不关思考时 message.content 恒为空串——这是 2026-10-23 审计里
        # 「调用成功但答案空白」的根因，所以必须显式关掉并单独记录 thinking 长度。
        "think": False,
        "messages": [{"role": "user", "content": AI_PROMPT}],
        "options": {
            "num_ctx": AI_NUM_CTX,
            "num_predict": AI_NUM_PREDICT,
            "temperature": 0,
        },
    }
    chat, chat_error, chat_timeout = _ai_http_json(
        AI_OLLAMA_HOST + "/api/chat", AI_CHAT_TIMEOUT_SECONDS, payload
    )
    result["timed_out"] = chat_timeout
    result["chat_error"] = chat_error
    if chat is None:
        return finish()

    result["chat_ok"] = True
    message = chat.get("message") if isinstance(chat.get("message"), dict) else {}
    content = str(message.get("content") or "")
    thinking = str(message.get("thinking") or "")
    result["content_len"] = len(content)
    result["content_excerpt"] = content.strip()[:AI_CONTENT_EXCERPT_CHARS]
    result["thinking_len"] = len(thinking)
    result["answer_matches"] = AI_EXPECTED_ANSWER in content
    eval_count = chat.get("eval_count")
    eval_duration_ns = chat.get("eval_duration")
    if isinstance(eval_count, int):
        result["eval_count"] = eval_count
    if isinstance(eval_duration_ns, (int, float)) and eval_duration_ns > 0:
        seconds = float(eval_duration_ns) / 1e9
        result["eval_duration_seconds"] = round(seconds, 3)
        if isinstance(eval_count, int) and eval_count > 0:
            result["toks_per_second"] = round(eval_count / seconds, 1)
    result["done_reason"] = chat.get("done_reason")
    return finish()


def _ai_inference_not_resident_detail(ai: dict[str, Any]) -> str:
    return (
        "%s 未驻留，跳过生成以免冷加载拖慢健康卡"
        "（/api/tags 可读，已装 %s 个模型；ctx 与吞吐本轮无样本，不判绿）"
        % (AI_MODEL, _fmt_num(ai.get("installed_models")))
    )


def _ai_inference_verdict(ai: dict[str, Any]) -> tuple[str, str]:
    """已驻留且已发过生成请求后的事实判定；detail 必须带真实数字。"""
    if ai.get("timed_out"):
        # 2026-10-24 修订：NUM_PARALLEL 已降为 1（qwen35 架构不支持并行），
        # 请求是**严格串行**的——批处理（M3 合批一次 15~33s）在跑时，
        # 这条 16 token 的探针只能排队。所以超时≠通道坏，判黄并写清原因；
        # 超时阈值同步提到 45s，避免把"排队"误报成"卡死"。
        return "yellow", (
            "%s 已驻留，但 /api/chat %gs 未返回（NUM_PARALLEL=1 串行，"
            "多半排在批处理之后；不能据此判定通道卡死）"
            % (AI_MODEL, AI_CHAT_TIMEOUT_SECONDS)
        )
    if ai.get("chat_ok") is not True:
        return "red", "调用 /api/chat 失败：%s（%s 已驻留，不是冷加载问题）" % (
            ai.get("chat_error") or "未知原因",
            AI_MODEL,
        )

    content_len = ai.get("content_len") or 0
    thinking_len = ai.get("thinking_len") or 0
    eval_count = ai.get("eval_count")
    eval_text = eval_count if eval_count is not None else "—"
    if not content_len:
        # 空串是这类故障里最贵的一种：HTTP 200、done_reason=stop，调用方拿到空答案，
        # 不抛错，静默污染下游判定。thinking 长度直接指认「思考没关」这一类。
        return "red", (
            "message.content 为空（thinking %d 字符，eval_count=%s，done_reason=%s）："
            "思考模型未关思考（think=false 未生效）或 num_predict=%d 被思考吃光"
            % (thinking_len, eval_text, ai.get("done_reason") or "—", AI_NUM_PREDICT)
        )

    level = "green"
    problems: list[str] = []
    if not ai.get("answer_matches"):
        level = "yellow"
        problems.append(
            "答案不切题（%d 字：「%s」），质量退化"
            % (content_len, ai.get("content_excerpt") or "")
        )
    ctx = ai.get("ctx")
    if ctx is None:
        level = "yellow"
        problems.append("/api/ps 未给出 context_length，ctx 不可判定（不判绿）")
    elif ctx != AI_NUM_CTX:
        level = "yellow"
        problems.append(
            "ctx 实际 %d ≠ %d，num_ctx 漂移会触发全模型重载" % (ctx, AI_NUM_CTX)
        )
    toks = ai.get("toks_per_second")
    if toks is None:
        level = "yellow"
        problems.append(
            "吞吐不可读（eval_count=%s，eval_duration 缺失），不判绿" % eval_text
        )
    elif toks < AI_MIN_TOKS_PER_SECOND:
        level = "yellow"
        problems.append(
            "吞吐 %.1f tok/s < %g（eval_count=%s）"
            % (toks, AI_MIN_TOKS_PER_SECOND, eval_text)
        )

    if ai.get("over_budget"):
        problems.append(
            "整项 %.1fs 超 %gs 预算" % (ai.get("elapsed_seconds") or 0.0, AI_BUDGET_SECONDS)
        )
        if level == "green":
            level = "yellow"
    if problems:
        return level, "；".join(problems)
    return "green", "%s ok · ctx=%d · %.1f tok/s" % (
        AI_MODEL_SHORT,
        AI_NUM_CTX,
        toks,
    )


# ---------------------------------------------------------------------------
# 4b) 夜间批处理积压（ops.batch_backlog，2026-10-25 EXEC-R13）
# ---------------------------------------------------------------------------
# 背景：2026-10-25 白天 night_batch 连续 6 轮 JEV rc=124 1620s + XQ rc=124 1800s，
# 每轮仍推进 ~100 条——"有进展但清不动"在卡上完全看不见。本项只读
# C:\Temp\night_batch.log 尾部，解析 START/ROUND/子任务 rc 行，输出两条队列积压、
# 最近两轮 rc、每轮墙钟/吞吐、按当前吞吐预计清完小时。
# 判级：积压比上一轮上升 → 红（只降不升是目标）；连续 rc=124 ≥2 轮 → 红；
#      仅 1 轮 rc=124 或 ETA >48h → 黄；ETA >96h / 日志 >3h 无新轮 → 红/黄。
BATCH_LOG_PATH = os.environ.get("NIGHT_BATCH_LOG", r"C:\Temp\night_batch.log")
JEV_NIGHT_LOG_PATH = os.environ.get("JEV_NIGHT_LOG", r"C:\Temp\jev_night.log")
BATCH_LOG_TAIL_BYTES = 256 * 1024
BATCH_RECENT_ROUNDS = 2
BATCH_TIMEOUT_STREAK_RED = 2
BATCH_ETA_YELLOW_HOURS = 48.0
BATCH_ETA_RED_HOURS = 96.0
BATCH_STALE_YELLOW_MIN = 180.0
_BATCH_ROUND_RE = re.compile(
    r"\]\s+ROUND\s+(\d+)\s*\|\s*剩余\s*JEV=(-?\d+)\s*雪球=(-?\d+)")
_BATCH_RC_RE = re.compile(r"\]\s+(JEV|XQ)\s+rc=(-?\d+)\s+([\d.]+)s")
_BATCH_START_RE = re.compile(r"\]\s+START\b")
# 夜班 jev_night.log（2026-10-26 EXEC-R32 纳入 ETA）：
#   START 行：`START 计划窗口 10-25 23:59 → 10-26 07:00 | 待判 JEV=17659 | 批=600 截止=1500s`
#   ROUND 行：`ROUND 3 | 剩余 JEV=17091`
#   rc 行：`JEV rc=0 1516s BACKFILL judged=143/600 left=17091 1515.5s deadline_hit=1 …`
_NIGHT_START_RE = re.compile(
    r"\]\s+START\s+计划窗口\s+(\d{2})-(\d{2})\s+(\d{2}:\d{2})"
    r"\s*→\s*(\d{2})-(\d{2})\s+(\d{2}:\d{2})")
_NIGHT_ROUND_RE = re.compile(r"\]\s+ROUND\s+(\d+)\s*\|\s*剩余\s+JEV=(-?\d+)")
_NIGHT_RC_RE = re.compile(
    r"\]\s+JEV\s+rc=(-?\d+)\s+([\d.]+)s\s+BACKFILL\s+judged=(\d+)/"
    r"(\d+)\s+left=(-?\d+)\s+([\d.]+)s")
NIGHT_DEFAULT_WINDOW_HOURS = 9.0   # jev_night 缺省计划窗口（22:00→07:00）


def _empty_batch_backlog() -> dict[str, Any]:
    return {
        "available": False, "reason": None, "path": BATCH_LOG_PATH,
        "sources": None, "day": None, "night": None, "active_source": None,
        "log_age_min": None, "rounds_seen": None, "session_rounds": None,
        "jev_backlog": None, "xq_backlog": None,
        "recent_rc": [], "timeout_streak": None,
        "jev_delta": None, "xq_delta": None,   # 正=下降(有进展)，负=上升(恶化)
        "round_hours": None, "eta_jev_hours": None, "eta_xq_hours": None,
        "eta_hours": None, "eta_serial_hours": None, "eta_basis": None,
        "throughput": None, "last_round_at": None,
        "unfinished_last_round": None,
        # 2026-10-26 EXEC-R32：白天+夜班合并容量（XQ 只在白天）
        "daily_jev_capacity": None, "day_jev_capacity": None,
        "night_capacity_per_night": None, "night_window_hours": None,
        "night_jev_per_min": None, "day_session_hours": None,
    }


def _read_batch_log_tail(path: Path, max_bytes: int = BATCH_LOG_TAIL_BYTES) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _collect_day_batch_backlog(log_path: str | None = None) -> dict[str, Any]:
    """读白天 night_batch 日志尾部，量两条队列积压 / 最近两轮 rc / 吞吐（内部用）。"""
    path = Path(log_path or BATCH_LOG_PATH)
    result = _empty_batch_backlog()
    result["path"] = str(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        result["reason"] = f"日志不存在: {path}"
        return result
    except OSError as exc:
        result["reason"] = f"读不到日志: {_short_exc(exc)}"
        return result
    result["log_age_min"] = round((time.time() - stat.st_mtime) / 60.0, 1)
    text = _read_batch_log_tail(path)
    if not text:
        result["reason"] = f"日志为空/读不到: {path}"
        return result

    rounds: list[dict[str, Any]] = []
    session = 0
    for line in text.splitlines():
        if _BATCH_START_RE.search(line):
            session += 1
            continue
        m = _BATCH_ROUND_RE.search(line)
        if m:
            at = None
            if line.startswith("[") and len(line) > 21:
                try:
                    at = dt.datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    at = None
            rounds.append({
                "round": int(m.group(1)),
                "jev": int(m.group(2)),
                "xq": int(m.group(3)),
                "at": at,
                "session": session if session else 1,
                "rc": {},
                "sec": {},
            })
            continue
        mr = _BATCH_RC_RE.search(line)
        if mr and rounds:
            rounds[-1]["rc"][mr.group(1)] = int(mr.group(2))
            try:
                rounds[-1]["sec"][mr.group(1)] = float(mr.group(3))
            except (TypeError, ValueError) as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)
    result["rounds_seen"] = len(rounds)
    if not rounds:
        result["available"] = False
        result["reason"] = "日志里没有 ROUND 行（可能尚未运行）"
        return result
    result["available"] = True
    last = rounds[-1]
    result["jev_backlog"] = last["jev"]
    result["xq_backlog"] = last["xq"]
    result["last_round_at"] = last["at"].strftime("%Y-%m-%d %H:%M:%S") if last["at"] else None
    # 只用最近一次 START 之后的轮次：ROUND 编号每次 START 从 1 重来，
    # 跨会话相减会把「停机间隔+另一队列占用的时间」摊进轮时长（旧口径 428.5h 病根）。
    sess = [r for r in rounds if r["session"] == last["session"]]
    result["session_rounds"] = len(sess)
    result["recent_rc"] = [
        {"round": r["round"], "jev_rc": r["rc"].get("JEV"),
         "xq_rc": r["rc"].get("XQ")}
        for r in sess[-BATCH_RECENT_ROUNDS:]
    ]
    # 连续超时轮数：从尾部往前数；末轮可能"已 ROUND 但子任务还没收口"，跳过它再数。
    streak = 0
    for r in reversed(sess):
        rcs = list(r["rc"].values())
        if not rcs:
            continue
        if any(rc == 124 for rc in rcs):
            streak += 1
        else:
            break
    result["timeout_streak"] = streak
    result["unfinished_last_round"] = not last["rc"]
    if len(sess) >= 2:
        prev = sess[-2]
        result["jev_delta"] = prev["jev"] - last["jev"]
        result["xq_delta"] = prev["xq"] - last["xq"]

    # ---- ETA（2026-10-25 EXEC-R23 口径修正）--------------------------------
    # 旧口径 = 积压 /（每轮净下降）× 平均轮墙钟，且窗口跨会话 → 把停机间隔与
    # 「另一队列占用的时间」都摊进分母，JEV ETA 被放大 4 倍（428.5h vs ~100h）。
    # 新口径：各队列只用**自己的运行时长**（该队列 rc 行里的秒数）算吞吐：
    #   吞吐(条/分) = 该队列每轮净下降 / 该队列运行分钟
    #   ETA = 该队列积压 / 吞吐(条/小时)
    # 会话内取最近 ≤4 对「相邻已收口轮次」；无配对时用末个已收口轮与当前积压做 1 对。
    done = [r for r in sess if r["rc"]]
    pairs = list(zip(done, done[1:]))[-4:]
    if not pairs and done and done[-1] is not last:
        pairs = [(done[-1], last)]

    def _queue_eta(queue: str, key: str) -> tuple[float | None, float | None, float | None]:
        drops: list[float] = []
        secs: list[float] = []
        for first, second in pairs:
            if queue not in first["rc"]:
                continue
            runtime = first["sec"].get(queue)
            drop = first[key] - second[key]
            if not isinstance(runtime, (int, float)) or runtime <= 0:
                continue
            drops.append(drop)
            secs.append(float(runtime))
        if not drops or sum(secs) <= 0:
            return None, None, None
        drop_per_round = sum(drops) / len(drops)
        per_min = drop_per_round / (sum(secs) / len(secs)) * 60.0
        eta = None
        if drop_per_round > 0 and last[key] > 0:
            rate_per_h = per_min * 60.0
            if rate_per_h > 0:
                eta = round(last[key] / rate_per_h, 1)
        return drop_per_round, per_min, eta

    jev_drop_avg, jev_per_min, eta_jev = _queue_eta("JEV", "jev")
    xq_drop_avg, xq_per_min, eta_xq = _queue_eta("XQ", "xq")
    round_hours_list = [
        (second["at"] - first["at"]).total_seconds() / 3600.0
        for first, second in pairs if first["at"] and second["at"]
    ]
    result["round_hours"] = (round(sum(round_hours_list) / len(round_hours_list), 2)
                             if round_hours_list else None)
    # 白天会话有效时长（估算）：平均每轮墙钟 × 本轮次轮数（供日容量折算）
    result["day_session_hours"] = (
        round(result["round_hours"] * len(sess), 2)
        if (result["round_hours"] and sess) else None)
    result["throughput"] = {
        "basis": "各队列自身运行时长（JEV/XQ rc 秒数；不含轮间隔/等待/停机）",
        "jev_per_round": round(jev_drop_avg, 1) if jev_drop_avg is not None else None,
        "jev_per_min": round(jev_per_min, 2) if jev_per_min is not None else None,
        "xq_per_round": round(xq_drop_avg, 1) if xq_drop_avg is not None else None,
        "xq_per_min": round(xq_per_min, 2) if xq_per_min is not None else None,
        "samples": len(pairs),
    }
    result["eta_jev_hours"] = eta_jev
    result["eta_xq_hours"] = eta_xq
    etas = [value for value in (eta_jev, eta_xq) if value is not None]
    result["eta_hours"] = max(etas) if etas else None
    result["eta_serial_hours"] = (round(eta_jev + eta_xq, 1)
                                  if (eta_jev is not None and eta_xq is not None)
                                  else None)
    result["eta_basis"] = ("按各队列自身运行时长计吞吐（已剔除轮间隔/另一队列等待/停机）；"
                           "eta_hours=max(两队列)，eta_serial_hours=两队列串行合计")
    return result


def _empty_jev_night() -> dict[str, Any]:
    return {
        "available": False, "reason": None, "path": JEV_NIGHT_LOG_PATH,
        "log_age_min": None, "window_hours": None, "rounds_seen": None,
        "session_rounds": None, "completed": 0, "judged_total": 0,
        "wall_total_s": 0.0, "jev_per_min": None, "capacity_per_night": None,
        "last_round_at": None, "jev_backlog_last": None, "jev_delta": None,
        "timeout_streak": None, "recent_rc": [], "last_open": None,
    }


def _parse_jev_night_log(log_path: str | None = None) -> dict[str, Any]:
    """读夜班 jev_night.log：JEV 独占吞吐/窗口/最近 rc（2026-10-26 EXEC-R32）。

    2026-10-24 起夜间由 jev_night 独跑（M3 暂停、窗口 22:00→07:00）；此前健康卡
    只读白天 night_batch.log，夜班吞吐不计入 → ETA 偏高、夜跑时误判"无新轮次"。
    本函数只读日志尾部（START 计划窗口 / ROUND / `JEV rc=… judged=… left=…`），
    取最近一次会话最近 ≤4 个已收口轮次算吞吐（合计 judged/合计墙钟，保守），
    容量 = 条/分 × 60 × 计划窗口小时（AST/部署窗口变化时随日志自动跟随）。
    """
    path = Path(log_path or JEV_NIGHT_LOG_PATH)
    result = _empty_jev_night()
    result["path"] = str(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        result["reason"] = f"夜班日志不存在: {path}"
        return result
    except OSError as exc:
        result["reason"] = f"读不到夜班日志: {_short_exc(exc)}"
        return result
    result["log_age_min"] = round((time.time() - stat.st_mtime) / 60.0, 1)
    text = _read_batch_log_tail(path)
    if not text:
        result["reason"] = f"夜班日志为空/读不到: {path}"
        return result

    rounds: list[dict[str, Any]] = []
    session = 0
    window_hours = None
    for line in text.splitlines():
        if _BATCH_START_RE.search(line):
            session += 1
            m = _NIGHT_START_RE.search(line)
            if m:
                year = dt.date.today().year
                if line.startswith("[") and len(line) > 5:
                    try:
                        year = int(line[1:5])
                    except ValueError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)
                try:
                    start = dt.datetime(year, int(m.group(1)), int(m.group(2)),
                                        *[int(x) for x in m.group(3).split(":")])
                    end = dt.datetime(year, int(m.group(4)), int(m.group(5)),
                                      *[int(x) for x in m.group(6).split(":")])
                    if end <= start:
                        end += dt.timedelta(days=1)
                    window_hours = round(
                        (end - start).total_seconds() / 3600.0, 2)
                except (TypeError, ValueError) as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)
            continue
        m = _NIGHT_ROUND_RE.search(line)
        if m:
            at = None
            if line.startswith("[") and len(line) > 21:
                try:
                    at = dt.datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    at = None
            rounds.append({"round": int(m.group(1)), "left": int(m.group(2)),
                           "at": at, "session": session if session else 1,
                           "rc": None, "judged": None, "wall_s": None,
                           "left_after": None})
            continue
        m = _NIGHT_RC_RE.search(line)
        if m and rounds:
            rounds[-1]["rc"] = int(m.group(1))
            try:
                rounds[-1]["wall_s"] = float(m.group(6))
            except (TypeError, ValueError):
                rounds[-1]["wall_s"] = float(m.group(2))
            rounds[-1]["judged"] = int(m.group(3))
            rounds[-1]["left_after"] = int(m.group(5))

    result["rounds_seen"] = len(rounds)
    if not rounds:
        result["reason"] = "夜班日志里没有 ROUND 行（可能尚未开跑）"
        return result
    last = rounds[-1]
    result["available"] = True
    result["window_hours"] = window_hours or NIGHT_DEFAULT_WINDOW_HOURS
    result["jev_backlog_last"] = last["left"]
    result["last_open"] = last["rc"] is None
    result["last_round_at"] = (last["at"].strftime("%Y-%m-%d %H:%M:%S")
                               if last["at"] else None)
    sess = [r for r in rounds if r["session"] == last["session"]]
    result["session_rounds"] = len(sess)
    recent = [r for r in sess if r["rc"] is not None][-4:]
    result["completed"] = len(recent)
    result["recent_rc"] = [{"round": r["round"], "jev_rc": r["rc"],
                            "judged": r["judged"]} for r in recent]
    judged_total = sum(r["judged"] for r in recent if r["judged"])
    wall_total = sum(r["wall_s"] for r in recent if r["wall_s"])
    result["judged_total"] = judged_total
    result["wall_total_s"] = round(wall_total, 1)
    if wall_total > 0 and judged_total > 0:
        per_min = judged_total / wall_total * 60.0
        result["jev_per_min"] = round(per_min, 2)
        result["capacity_per_night"] = round(
            per_min * 60.0 * result["window_hours"], 1)
    deltas = [
        a["left_after"] - b["left_after"]
        for a, b in zip(sess, sess[1:])
        if a.get("left_after") is not None and b.get("left_after") is not None
    ]
    result["jev_delta"] = (round(sum(deltas) / len(deltas), 1)
                           if deltas else None)
    streak = 0
    for r in reversed(sess):
        if r["rc"] is None:
            continue
        if r["rc"] == 124:
            streak += 1
        else:
            break
    result["timeout_streak"] = streak
    return result


def collect_batch_backlog(log_path: str | None = None,
                          night_log_path: str | None = None) -> dict[str, Any]:
    """白天 night_batch + 夜班 jev_night 合并：积压/最近 rc/日容量/ETA。

    2026-10-26 EXEC-R32：此前只读 night_batch.log，夜间 jev_night 吞吐不计入 →
    ETA 偏高（实测 73.6h）且夜跑时卡片误判"日志 3h 无新轮次"。现在两份日志都读：
      · JEV 日容量 = 白天会话容量（条/分×白天会话时长）+ 夜班窗口容量（条/分×窗口）;
      · XQ 只在白天跑，容量只算白天;
      · 积压/最近 rc 取更新鲜的一份（夜班更鲜则用夜班 left/rc，XQ 仍取白天）;
      · 判级阈值不变（48h/96h），两日志都不可用才 available=false。
    """
    day = _collect_day_batch_backlog(log_path)
    night = _parse_jev_night_log(night_log_path)
    day_ok = bool(day.get("available"))
    night_ok = bool(night.get("available"))

    if not day_ok and not night_ok:
        result = _empty_batch_backlog()
        result["day"] = {"available": False,
                         "reason": day.get("reason"),
                         "path": day.get("path")}
        result["night"] = night
        result["reason"] = ("白天/夜班两份日志都不可用：%s；%s"
                            % (day.get("reason") or "未知",
                               night.get("reason") or "未知"))
        return result

    result = day if day_ok else _empty_batch_backlog()
    if not day_ok:
        result["available"] = True
        result["path"] = night.get("path")
        result["jev_backlog"] = night.get("jev_backlog_last")
        result["xq_backlog"] = None
        result["recent_rc"] = night.get("recent_rc") or []
        result["timeout_streak"] = night.get("timeout_streak")
        result["last_round_at"] = night.get("last_round_at")
        result["log_age_min"] = night.get("log_age_min")
        result["active_source"] = "night"
        result["unfinished_last_round"] = night.get("last_open")
    else:
        result["active_source"] = "day"
        day_age = day.get("log_age_min")
        night_age = night.get("log_age_min")
        if night_ok:
            result["log_age_min"] = min(
                a for a in (day_age, night_age) if a is not None)
            if night_age is not None and (day_age is None or night_age < day_age):
                # 夜班更新鲜：JEV 积压/近况取夜班（XQ 只有白天有数据）
                result["jev_backlog"] = night.get("jev_backlog_last")
                result["recent_rc"] = (night.get("recent_rc")
                                       or result.get("recent_rc"))
                result["timeout_streak"] = night.get("timeout_streak")
                result["last_round_at"] = night.get("last_round_at")
                result["active_source"] = "night"
                result["unfinished_last_round"] = night.get("last_open")
                if night.get("jev_delta") is not None:
                    result["jev_delta"] = night.get("jev_delta")

    # 容量折算（XQ 只在白天）
    day_hours = day.get("day_session_hours") if day_ok else None
    tp = (day.get("throughput") or {}) if day_ok else {}
    day_jev_cap = None
    day_xq_cap = None
    if day_hours and day_hours > 0:
        if tp.get("jev_per_min") is not None:
            day_jev_cap = round(tp["jev_per_min"] * 60.0 * day_hours, 1)
        if tp.get("xq_per_min") is not None:
            day_xq_cap = round(tp["xq_per_min"] * 60.0 * day_hours, 1)
    night_cap = night.get("capacity_per_night") if night_ok else None
    daily_jev = (day_jev_cap or 0) + (night_cap or 0)
    result["day_jev_capacity"] = day_jev_cap
    result["night_capacity_per_night"] = night_cap
    result["daily_jev_capacity"] = round(daily_jev, 1) if daily_jev > 0 else None
    result["day_session_hours"] = day_hours
    result["night_window_hours"] = (night.get("window_hours")
                                    if night_ok else None)
    result["night_jev_per_min"] = (night.get("jev_per_min")
                                   if night_ok else None)
    result["throughput"] = tp

    jev_backlog = result.get("jev_backlog")
    xq_backlog = result.get("xq_backlog")
    eta_jev = None
    if isinstance(jev_backlog, int) and jev_backlog > 0 and daily_jev > 0:
        eta_jev = round(jev_backlog / (daily_jev / 24.0), 1)
    eta_xq = None
    if (isinstance(xq_backlog, int) and xq_backlog > 0
            and isinstance(day_xq_cap, (int, float)) and day_xq_cap > 0):
        eta_xq = round(xq_backlog / (day_xq_cap / 24.0), 1)
    result["eta_jev_hours"] = eta_jev
    result["eta_xq_hours"] = eta_xq
    etas = [value for value in (eta_jev, eta_xq) if value is not None]
    result["eta_hours"] = max(etas) if etas else None
    result["eta_serial_hours"] = (round(eta_jev + eta_xq, 1)
                                  if (eta_jev is not None
                                      and eta_xq is not None) else None)
    result["eta_basis"] = (
        "日容量=白天会话 JEV %s 条/天（%s 条/分×%sh）+ 夜班窗口 JEV %s 条/夜"
        "（%s 条/分×%sh）；ETA=积压÷(日容量/24) 墙钟小时；"
        "XQ 仅白天容量 %s 条/天"
        % (day_jev_cap, tp.get("jev_per_min"), day_hours,
           night_cap, night.get("jev_per_min") if night_ok else None,
           result["night_window_hours"], day_xq_cap))
    result["sources"] = {
        "day": {"available": day_ok,
                "reason": None if day_ok else day.get("reason"),
                "path": day.get("path"),
                "log_age_min": day.get("log_age_min"),
                "jev_capacity": day_jev_cap,
                "xq_capacity": day_xq_cap},
        "night": {"available": night_ok,
                  "reason": None if night_ok else night.get("reason"),
                  "path": night.get("path"),
                  "log_age_min": night.get("log_age_min"),
                  "window_hours": (night.get("window_hours")
                                   if night_ok else None),
                  "jev_per_min": (night.get("jev_per_min")
                                  if night_ok else None),
                  "capacity_per_night": night_cap},
    }
    result["available"] = True
    return result


def _machine_suspend_level(sus: dict[str, Any]) -> tuple[str, str]:
    """ops.machine_suspend 判级（2026-10-26 EXEC-R32）。

    采集器未运行（ops 块缺失）= 卡自己没看，不判绿 → 红（与其它块一致）；
    查询失败/权限不足/超时 = **不可判定≠故障** → 黄（detail 写明原因与重试方式）；
    真读到数据时沿用 2026-10-24 开发机定调：有挂起/意外关机 → 黄，否则绿
    （生产机要恢复红级时再改这里，阈值常量保留）。
    """
    if not sus:
        return "red", "机器挂起检查未运行（ops 块缺失，采集器未跑；不判绿）"
    if not sus.get("available"):
        return ("yellow",
                "机器挂起统计不可判定（查询失败/权限不足/超时，非故障）：%s；"
                "重试：在有系统事件日志读权限的会话重跑 "
                "python -B tools/gen_health_card.py"
                % str(sus.get("reason") or "未知原因"))
    detail = _machine_suspend_detail(sus) + "（开发机特性，不计入产品判据）"
    if (sus.get("suspend_seconds") or 0) > 0 or sus.get("unexpected_shutdowns"):
        return "yellow", detail
    return "green", _machine_suspend_detail(sus)


def _batch_backlog_detail(bl: dict[str, Any]) -> str:
    if not bl.get("available"):
        return "夜间批处理积压不可判定：" + str(bl.get("reason") or "未知")
    bits = ["JEV 积压 %s · 雪球 %s" % (bl.get("jev_backlog"), bl.get("xq_backlog"))]
    recent = bl.get("recent_rc") or []
    if recent:
        def _rc_text(value):
            return "未收口" if value is None else value
        bits.append("最近 rc：" + "，".join(
            "R%s JEV=%s/XQ=%s" % (item.get("round"), _rc_text(item.get("jev_rc")),
                                  _rc_text(item.get("xq_rc"))) for item in recent))
    if bl.get("unfinished_last_round"):
        bits.append("最近一轮未收口（进行中或意外退出，无 END/rc 行）")
    if bl.get("timeout_streak"):
        bits.append("连续 rc=124 轮数 %d" % bl["timeout_streak"])
    jd, xd = bl.get("jev_delta"), bl.get("xq_delta")
    if jd is not None or xd is not None:
        bits.append("较上轮变化 JEV=%s 雪球=%s（正=下降）"
                    % ("—" if jd is None else jd, "—" if xd is None else xd))
    tp = bl.get("throughput") or {}
    if tp.get("samples"):
        parts = []
        if tp.get("jev_per_round") is not None:
            parts.append("JEV +%s/轮（%s 条/分）"
                         % (tp["jev_per_round"], tp.get("jev_per_min")))
        if tp.get("xq_per_round") is not None:
            parts.append("XQ +%s/轮（%s 条/分）"
                         % (tp["xq_per_round"], tp.get("xq_per_min")))
        if parts:
            bits.append("每轮吞吐 " + "、".join(parts)
                        + "（%s 对样本）" % tp.get("samples"))
    sources = bl.get("sources") or {}
    if sources:
        def _src_text(source: dict[str, Any]) -> str:
            return ("OK" if source.get("available")
                    else "缺(%s)" % str(source.get("reason") or "?")[:36])
        bits.append("数据来源：白天 %s、夜班 %s%s"
                    % (_src_text(sources.get("day") or {}),
                       _src_text(sources.get("night") or {}),
                       "；当前以夜班为准" if bl.get("active_source") == "night"
                       else ""))
    if bl.get("daily_jev_capacity") is not None:
        bits.append("合并日容量 JEV≈%s 条/天（白天 %s / 夜班 %s 条/夜）"
                    % (bl.get("daily_jev_capacity"),
                       bl.get("day_jev_capacity"),
                       bl.get("night_capacity_per_night")))
    if bl.get("eta_hours") is not None:
        eta_text = "ETA 口径：%s；JEV %s h / 雪球 %s h" % (
            bl.get("eta_basis") or "按各队列自身运行时长",
            bl.get("eta_jev_hours"), bl.get("eta_xq_hours"))
        if bl.get("eta_serial_hours") is not None:
            eta_text += "；串行合计 %s h" % bl["eta_serial_hours"]
        bits.append(eta_text + "（瓶颈取 max = %s h）" % bl["eta_hours"])
    else:
        bits.append("预计清完小时：不可计算（无净下降/无配对轮次）")
    if bl.get("log_age_min") is not None:
        bits.append("日志 %.0f 分钟前更新" % bl["log_age_min"])
    return "；".join(bits)


# ── Hermes 推送健康（hermes.push，2026-10-26 K-ORG-20261026-HERMESFIX）──────
# 背景：push_log 连续 18 次 ok=false 却没有任何健康项报警；人工查会话发现 agent
# 其实已回复，是读流/预算设计缺陷（SSE 吃光预算、state 兜底没机会跑）。此处把
# 「最近成功时间 + 7 天成功率 + DLQ 积压」做成可判定指标，防止再次静默失败。
HERMES_PUSH_LOG = os.environ.get("HERMES_PUSH_LOG") or (
    r"C:\aios\zerobound-kg\deliverables\daily_brief\push_log.jsonl")
HERMES_PUSH_DLQ = os.environ.get("HERMES_PUSH_DLQ") or (
    r"C:\aios\zerobound-kg\deliverables\daily_brief\push_dlq.jsonl")


def _read_jsonl_tail(path: str, max_lines: int = 500) -> list:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-max_lines:]
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _parse_ts_local(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _collect_hermes_push() -> dict:
    recs = _read_jsonl_tail(HERMES_PUSH_LOG)
    dlq = _read_jsonl_tail(HERMES_PUSH_DLQ)
    now = dt.datetime.now()
    last_ok = None
    for r in recs:
        ts = _parse_ts_local(r.get("ts"))
        if r.get("ok") is True and ts is not None and (last_ok is None or ts > last_ok):
            last_ok = ts
    win = []
    for r in recs:
        ts = _parse_ts_local(r.get("ts"))
        if ts is not None and (now - ts).total_seconds() <= 7 * 86400:
            win.append(r)
    ok7 = sum(1 for r in win if r.get("ok") is True)
    return {
        "available": bool(recs),
        "last_ok": last_ok.isoformat(timespec="seconds") if last_ok else None,
        "last_ok_age_h": round((now - last_ok).total_seconds() / 3600.0, 1) if last_ok else None,
        "win7_total": len(win),
        "win7_ok": ok7,
        "win7_rate": round(ok7 / len(win), 3) if win else None,
        "last": (recs[-1] if recs else {}),
        "dlq_count": len(dlq),
        "log_path": HERMES_PUSH_LOG,
    }


def _hermes_push_level(rec: dict) -> tuple[str, str]:
    if not rec.get("available"):
        return "red", "Hermes 推送日志不存在/为空：%s" % rec.get("log_path")
    age = rec.get("last_ok_age_h")
    rate = rec.get("win7_rate")
    total = rec.get("win7_total") or 0
    dlq = rec.get("dlq_count") or 0
    parts = ["最近成功 %s" % ("从未成功" if age is None else "%.1fh 前" % age)]
    if total:
        parts.append("7d 成功率 %.0f%% (%d/%d)" % ((rate or 0) * 100, rec.get("win7_ok", 0), total))
    last = rec.get("last") or {}
    if last:
        parts.append("最近一次 ok=%s elapsed=%ss" % (last.get("ok"), last.get("elapsed_s")))
    parts.append("DLQ %d 条" % dlq)
    detail = " | ".join(parts)
    if age is None or age > 48:
        return "red", detail
    if age > 36 or (total >= 3 and (rate or 0) < 0.7) or dlq >= 3:
        return "yellow", detail
    return "green", detail


# 2026-10-26 BACKLOGDUTY：白天让位期豁免。
# 设计背景：07:00-21:50 是 M3 独占窗口，JEV 小时轮按设计「拿不到锁就让位」，
# 期间新闻照常流入 → 积压自然上涨，这是工作模式不是故障。夜间 21:50-07:00 仍严格判红。
BACKLOG_DAYTIME_EXEMPT_ENV = "ZB_HC_BACKLOG_DAYTIME_EXEMPT"
NIGHT_WINDOW_START = dt.time(21, 50)
NIGHT_WINDOW_END = dt.time(7, 0)


def _in_night_window(now: dt.datetime | None = None) -> bool:
    """21:50-07:00 为 JEV 夜间独占窗口（跨零点）。"""
    n = now or dt.datetime.now()
    t = n.time()
    return t >= NIGHT_WINDOW_START or t < NIGHT_WINDOW_END


def _backlog_daytime_exempt(now: dt.datetime | None = None) -> bool:
    """白天让位期是否豁免（env=0 可关闭豁免）。"""
    if os.environ.get(BACKLOG_DAYTIME_EXEMPT_ENV) == "0":
        return False
    return not _in_night_window(now)


def _batch_backlog_level(bl: dict[str, Any],
                         now: dt.datetime | None = None) -> tuple[str, str]:
    """ops.batch_backlog 判级（2026-10-25 EXEC-R13，R23/R32 修 ETA 口径）。

    red    ：积压较上一轮上升（任一队列 delta<0），或连续 rc=124 ≥2 轮；
             或 ETA >96h（2026-10-26 EXEC-R32：ETA=积压÷(白天+夜班合并日容量/24)，
             已纳入夜班 jev_night 吞吐，不再按白天单口径虚高）。
    yellow ：仅最近 1 轮 rc=124；ETA >48h；两份日志合计 >3h 无新 ROUND（可能收工/失联）。
    green  ：无超时、积压只降不升、ETA ≤48h。
    两日志都读不到 / 无 ROUND → red（不判绿）。detail 给出数据来源、合并日容量与
    JEV/雪球分队列 ETA、串行合计。
    """
    detail = _batch_backlog_detail(bl)
    if not bl.get("available"):
        return "red", detail
    jd, xd = bl.get("jev_delta"), bl.get("xq_delta")
    if (jd is not None and jd < 0) or (xd is not None and xd < 0):
        if _backlog_daytime_exempt(now):
            return ("yellow",
                    "白天让位期积压上涨（M3 独占、JEV 按设计让位；21:50 夜跑清账，非故障）；"
                    + detail)
        return "red", "积压较上一轮上升；" + detail
    streak = int(bl.get("timeout_streak") or 0)
    if streak >= BATCH_TIMEOUT_STREAK_RED:
        return "red", "连续 %d 轮 rc=124；" % streak + detail
    if streak == 1:
        return "yellow", "最近 1 轮 rc=124；" + detail
    jev_bl = bl.get("jev_backlog")
    if jev_bl is None:
        jev_bl = bl.get("jev_left")
    xq_bl = bl.get("xq_backlog")
    if xq_bl is None:
        xq_bl = bl.get("xq_left")
    try:
        jev_n = int(jev_bl or 0)
        xq_n = int(xq_bl or 0)
    except (TypeError, ValueError):
        jev_n = xq_n = 0
    if jev_n == 0 and xq_n == 0:
        return "green", "两队列积压均为 0；" + detail
    eta = bl.get("eta_hours")
    if eta is None:
        return "yellow", "积压 %d/%d 且预计清完不可计算（容量未知）；" % (
            jev_n, xq_n) + detail
    if eta > BATCH_ETA_RED_HOURS:
        return "red", "预计清完 %.1fh > %.0fh；" % (eta, BATCH_ETA_RED_HOURS) + detail
    if eta > BATCH_ETA_YELLOW_HOURS:
        return ("yellow",
                "预计清完 %.1fh > %.0fh；" % (eta, BATCH_ETA_YELLOW_HOURS) + detail)
    age = bl.get("log_age_min")
    if age is not None and age > BATCH_STALE_YELLOW_MIN and (jev_n + xq_n) > 0:
        return "yellow", "日志 %.0f 分钟无新轮次（可能已收工/失联）；" % age + detail
    return "green", detail


# ---------------------------------------------------------------------------
# 5) 机器挂起（ops.machine_suspend）
# ---------------------------------------------------------------------------
# 只认 Windows 系统事件日志里的结构化 ID，不解析本地化文案。
# 关键洞察：Kernel-General 1 的"时间被硬件钟纠正 +N ms"里那个 N，就是挂起时长——
# 机器睡眠期间硬件时钟在走、系统时钟不走，醒来后被纠正，增量即真实空洞。
# 其它日志（应用侧守护）醒来就接着写，只能看到"少了几行"，给不出时长。
SUSPEND_QUERY_TIMEOUT_SECONDS = 25.0
SUSPEND_RED_SECONDS = 3600.0     # 今日累计挂起 >60 分钟 -> 红
SUSPEND_YELLOW_SECONDS = 600.0   # >10 分钟 -> 黄
SUSPEND_MIN_EVENT_SECONDS = 60.0  # 单次纠正 <60s 视为普通对时，不算挂起

_SUSPEND_PS = (
    "Get-WinEvent -FilterHashtable @{LogName='System'; Id=1,41,42,6008;"
    "StartTime=(Get-Date).Date} -ErrorAction SilentlyContinue |"
    "Select-Object Id,@{n='TimeCreated';"
    "e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}},Message |"
    "ConvertTo-Json -Compress -Depth 3"
)


def _empty_machine_suspend() -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "date": None,
        "suspend_count": None,
        "suspend_seconds": None,
        "largest_suspend_seconds": None,
        "sleep_events": None,
        "unexpected_shutdowns": [],
        "duration_seconds": None,
    }


def _suspend_delta_seconds(message: str) -> float | None:
    """从 Kernel-General 时间纠正文案里取毫秒增量（本地化无关：只认数字+ms）。

    「时间增量更改为 …：600437 ms」/「Time changed … by 600437 ms」都能命中。
    取所有候选里绝对值最大的一个（一条消息可能同时含旧值/新值/增量）。
    """
    best = None
    for match in re.finditer(r"(-?\d[\d,]*)\s*ms", message or ""):
        try:
            value = abs(float(match.group(1).replace(",", "")))
        except ValueError:
            continue
        if best is None or value > best:
            best = value
    return None if best is None else best / 1000.0


def collect_machine_suspend() -> dict[str, Any]:
    """统计今日挂起次数/累计时长/意外关机（只读系统事件日志）。"""
    result = _empty_machine_suspend()
    result["date"] = dt.date.today().isoformat()
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SUSPEND_PS],
            capture_output=True, timeout=SUSPEND_QUERY_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = "powershell 调用失败: " + _short_exc(exc)
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        return result
    result["duration_seconds"] = round(time.monotonic() - started, 3)

    raw = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:160]
        result["reason"] = "事件查询失败(rc=%s): %s" % (proc.returncode, err or "无 stderr")
        return result
    if not raw:
        # 今天一条相关事件都没有：合法状态（没睡过），计 0 而不是"读不到"。
        result.update(available=True, suspend_count=0, suspend_seconds=0.0,
                      largest_suspend_seconds=0.0, sleep_events=0)
        return result

    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        result["reason"] = "事件输出不是 JSON: " + _short_exc(exc)
        return result
    events = parsed if isinstance(parsed, list) else [parsed]

    suspends: list[float] = []
    sleep_events = 0
    shutdowns: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            event_id = int(ev.get("Id"))
        except (TypeError, ValueError):
            continue
        when = str(ev.get("TimeCreated") or "")
        if event_id == 42:
            sleep_events += 1
        elif event_id in (41, 6008):
            # 41=内核电源"未正常关机"，6008=上一次关闭是意外的。同一件事常两条都记。
            if not any(s["id"] == event_id for s in shutdowns):
                shutdowns.append({"id": event_id, "at": when})
        elif event_id == 1:
            delta = _suspend_delta_seconds(str(ev.get("Message") or ""))
            if delta is not None and delta >= SUSPEND_MIN_EVENT_SECONDS:
                suspends.append(delta)

    result.update(
        available=True,
        suspend_count=len(suspends),
        suspend_seconds=round(sum(suspends), 1),
        largest_suspend_seconds=round(max(suspends), 1) if suspends else 0.0,
        sleep_events=sleep_events,
        unexpected_shutdowns=shutdowns,
    )
    return result


def _machine_suspend_detail(sus: dict[str, Any]) -> str:
    seconds = sus.get("suspend_seconds") or 0.0
    count = sus.get("suspend_count") or 0
    bits = []
    if count:
        bits.append("今日挂起 %d 次共 %.1f 分钟（最长 %.1f）"
                    % (count, seconds / 60.0, (sus.get("largest_suspend_seconds") or 0.0) / 60.0))
    else:
        bits.append("今日无挂起记录")
    bits.append("睡眠事件 %s" % _fmt_num(sus.get("sleep_events")))
    shutdowns = sus.get("unexpected_shutdowns") or []
    if shutdowns:
        # 41 与 6008 是同一次事故的两条证据（内核电源 + 事件日志），不是两次事故。
        ids = "、".join(sorted({str(s.get("id")) for s in shutdowns}))
        first_at = shutdowns[0].get("at") or "?"
        bits.append("意外关机（ID %s，最近 %s）" % (ids, first_at))
    else:
        bits.append("无意外关机")
    return "；".join(bits)


# ---------------------------------------------------------------------------
# 6) 推理错误率（ai.error_rate）
# ---------------------------------------------------------------------------
# 2026-10-24 建：ollama 日志里出现过每分钟 25–30 次 `POST /api/chat` 404，
# 而业务侧被"降级三态/规则兜底"吞掉，健康卡上完全看不出来。
# 本项只读 Ollama 自己写的 GIN 访问日志，统计最近一小时 4xx/5xx 占比。
AI_ERROR_WINDOW_MINUTES = 60.0
AI_ERROR_YELLOW_RATIO = 0.05
AI_ERROR_RED_RATIO = 0.20
AI_ERROR_MIN_SAMPLES = 20
# GIN 行格式：`[GIN] 2026/10/24 - 21:04:39 | 200 |  1.2ms | 127.0.0.1 | POST "/api/chat"`
_GIN_RE = re.compile(
    r"\[GIN\]\s+(\d{4})/(\d{2})/(\d{2})\s+-\s+(\d{2}):(\d{2}):(\d{2})\s*\|\s*(\d{3})\s*\|"
)
# 2026-10-25 EXEC-R6：GIN 行尾补充「耗时 | 客户端 | 方法 "路径"」，用于把
# 「客户端主动断开」的 500 从 server_errors 里摘出来（旧 news_ai 20s 超时特征）。
_GIN_TAIL_RE = re.compile(
    r"\|\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|µs|μs|us|s)\s*\|[^|]*\|\s*(\w+)\s+\"([^\"]*)\""
)
# tuned.log 在 500 行前一两行会写这句：客户端 20s 超时主动断连的铁证。
_CLIENT_ABORT_MARKER = "client closing the connection"
# 兜底判据：耗时 ≈ 旧 jev_reason 的 20s 超时（±1.5s 容差，只对 5xx 生效）。
_CLIENT_ABORT_DURATION_S = 20.0
_CLIENT_ABORT_DURATION_TOL_S = 1.5


def _parse_gin_duration_s(line: str) -> float | None:
    """从 GIN 行取耗时秒数；兼容 Go 复合时长（1m0s / 1h2m3.5s）。

    2026-10-26 AIERR2：Ollama 对 >=60s 的请求写 `1m0s` 这类 Go 复合格式，
    旧正则只认 ms/µs/s，导致 60s 的客户端超时被误判 server_error（一票红）。
    """
    if not line:
        return None
    m = re.search(
        r"\|\s*((?:[0-9]+(?:\.[0-9]+)?)(?:h|ms|µs|μs|us|m|s)"
        r"(?:[0-9]+(?:\.[0-9]+)?(?:h|ms|µs|μs|us|m|s))*)\s*\|",
        line,
    )
    if not m:
        return None
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001,
             "µs": 0.000001, "μs": 0.000001, "us": 0.000001}
    total = 0.0
    found = False
    for num, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)(h|ms|µs|μs|us|m|s)", m.group(1)):
        found = True
        total += float(num) * units[unit]
    return total if found else None


def _empty_ai_error_rate() -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "source": None,
        "window_minutes": AI_ERROR_WINDOW_MINUTES,
        "total": None,
        "ok": None,
        "client_errors": None,
        "server_errors": None,
        "client_aborts": None,
        "ratio": None,
    }


def collect_ai_error_rate(repo_root: Path, now: dt.datetime | None = None) -> dict[str, Any]:
    """统计最近一小时 Ollama 访问日志里的 HTTP 状态分布（只读，最多读尾部 8 MB）。

    2026-10-25 EXEC-R6：客户端主动断开的 500（server 端日志出现
    "client closing the connection"，或耗时≈20s 的 /api/chat 500）单列
    client_aborts，不计 server_errors、不计失败率（ratio 只算真错误）。
    """
    result = _empty_ai_error_rate()
    now = now or dt.datetime.now()
    candidates = [
        repo_root / "automation_runs" / "ollama_guard" / "serve_tuned.log",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "tuned.log",
    ]
    cutoff = now - dt.timedelta(minutes=AI_ERROR_WINDOW_MINUTES)
    total = ok = client_err = server_err = client_abort = 0
    used = None
    first_when = None
    last_when = None
    for path in candidates:
        try:
            if not path.exists():
                continue
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 8 * 1024 * 1024))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        hits = 0
        # 逐行扫：500 前一两行若出现客户端断连标记，则该 500 归 client_abort。
        pending_abort_marker = False
        for line in tail.splitlines():
            if _CLIENT_ABORT_MARKER in line:
                pending_abort_marker = True
                continue
            match = _GIN_RE.search(line)
            if not match:
                continue
            try:
                when = dt.datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3)),
                    int(match.group(4)), int(match.group(5)), int(match.group(6)),
                )
            except ValueError:
                pending_abort_marker = False
                continue
            if when < cutoff:
                pending_abort_marker = False
                continue
            status = int(match.group(7))
            hits += 1
            total += 1
            first_when = when if first_when is None else min(first_when, when)
            last_when = when if last_when is None else max(last_when, when)
            if 200 <= status < 400:
                ok += 1
            elif 400 <= status < 500:
                client_err += 1
            elif status >= 500:
                duration_s = _parse_gin_duration_s(line)
                # 2026-10-26 AIERR：500 且耗时 >= 客户端超时线(20s) 一律归 client_abort。
                # 原 |duration-20|<=1.5 只认"恰好 20s"，45s 的客户端放弃会被误判 server_error
                # 一票红（实测 13:51 M3 满载时 health probe 超时即此类）。
                looks_like_timeout = (
                    duration_s is not None
                    and duration_s >= _CLIENT_ABORT_DURATION_S
                )
                if pending_abort_marker or looks_like_timeout:
                    client_abort += 1
                else:
                    server_err += 1
            pending_abort_marker = False
        if hits and used is None:
            used = path.name
    if total == 0:
        result.update(available=True, source=used, total=0, ok=0,
                      client_errors=0, server_errors=0, client_aborts=0,
                      ratio=0.0, covered_minutes=0.0)
        return result
    # 失败率只算「客户端错误 + 真服务端错误」；client_aborts 是调用方自己超时走了。
    bad = client_err + server_err
    covered = None
    if first_when is not None and last_when is not None:
        covered = round((last_when - first_when).total_seconds() / 60.0, 1)
    result.update(
        available=True, source=used, total=total, ok=ok,
        client_errors=client_err, server_errors=server_err,
        client_aborts=client_abort,
        ratio=round(bad / total, 4),
        covered_minutes=covered,
    )
    return result


def _ai_error_detail(err: dict[str, Any]) -> str:
    total = err.get("total") or 0
    if total == 0:
        return "近 %d 分钟无 API 请求（日志可读，计数 0）" % AI_ERROR_WINDOW_MINUTES
    bits = [
        "近 %d 分钟 %d 次请求：2xx/3xx %d · 4xx %d · 5xx %d"
        % (AI_ERROR_WINDOW_MINUTES, total, err.get("ok") or 0,
           err.get("client_errors") or 0, err.get("server_errors") or 0),
    ]
    covered = err.get("covered_minutes")
    if covered is not None and covered < AI_ERROR_WINDOW_MINUTES - 5:
        bits.append("日志尾部仅覆盖 %.1f 分钟（8MB 截断，早段请求不可见）" % covered)
    client_aborts = err.get("client_aborts") or 0
    if client_aborts:
        bits.append("客户端主动断连 %d（不计失败率，原 500 口径）" % client_aborts)
    ratio = err.get("ratio") or 0.0
    if ratio > 0:
        bits.append("失败率 %.1f%%" % (ratio * 100))
    return "；".join(bits)


# ---------------------------------------------------------------------------
# 7) 智能体点名册（agent.census）
# ---------------------------------------------------------------------------
# 2026-10-24 建：军团有 60 席，但"谁在干活"要看四本账（org.json 编制 /
# llm_tool_audit 事件 / checkpoints.traces 产出 / tasks.json 工单），合不起来
# 就看不出"组织在转、业务空转"。本项直接复用 tools/agent_census.py 的结论，
# 只读、不重复实现口径（单一真相源）。
AGENT_CENSUS_TIMEOUT_SECONDS = 30.0
AGENT_CENSUS_RED_OPEN = 3        # 有单未动的席位数 ≥ 此值判红
AGENT_CENSUS_YELLOW_SILENT = 0.70


def _empty_agent_census() -> dict[str, Any]:
    return {
        "available": False, "reason": None, "generated_at": None,
        "summary": None, "open_agents": [],
    }


def collect_agent_census(repo_root: Path) -> dict[str, Any]:
    """子进程跑 tools/agent_census.py --json，取汇总 + 有单未动的席位。"""
    result = _empty_agent_census()
    script = repo_root / "tools" / "agent_census.py"
    if not script.exists():
        result["reason"] = "tools/agent_census.py 不存在"
        return result
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(script), "--json"],
            capture_output=True, timeout=AGENT_CENSUS_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = "调用失败: " + _short_exc(exc)
        return result
    raw = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not raw:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:160]
        result["reason"] = "退出码 %s：%s" % (proc.returncode, err or "无输出")
        return result
    try:
        card = json.loads(raw)
    except ValueError as exc:
        result["reason"] = "输出不是 JSON: " + _short_exc(exc)
        return result
    summary = card.get("summary") or {}
    result.update(
        available=True,
        generated_at=card.get("generated_at"),
        summary=summary,
        open_agents=[r for r in (card.get("rows") or [])
                     if r.get("level") == "有单未动"][:6],
    )
    return result


def _agent_census_detail(cen: dict[str, Any]) -> str:
    s = cen.get("summary") or {}
    bits = ["在册 %s" % _fmt_num(s.get("在册")),
            "干活 %s" % _fmt_num(s.get("干活中")),
            "有单未动 %s" % _fmt_num(s.get("有单未动")),
            "待命 %s" % _fmt_num(s.get("在岗待命")),
            "静默 %s（%.0f%%）" % (_fmt_num(s.get("静默")),
                                 (s.get("静默占比") or 0) * 100)]
    if s.get("在手工单"):
        bits.append("在手工单 %s" % _fmt_num(s.get("在手工单")))
    names = [r.get("agent") for r in (cen.get("open_agents") or [])]
    if names:
        bits.append("压单席位：" + "、".join(n for n in names if n))
    return "；".join(bits)


# ---------------------------------------------------------------------------
# 8) 每日工作项（agent.daily）
# ---------------------------------------------------------------------------
# 2026-10-24 建：配合 agent_platform/registry/daily_work.json（事项目录）。
# "谁在干活"（agent.census）之外还要回答"该干的干了没有"——没有事的组织只会
# 巡检和催办（当天实测：351 条催办 vs 1 单派工）。本项复用 tools/agent_daily.py
# --json，不重复实现判定口径。
# 2026-10-26 HCTIMEOUT: 476 张工单后 agent_daily 实测 32.4s（order 证据逐分支 git log），
# 30s 超时会让 agent.daily 常态红；放宽到 120s，仍远小于整卡执行预算。
AGENT_DAILY_TIMEOUT_SECONDS = 120.0


def _empty_agent_daily() -> dict[str, Any]:
    return {"available": False, "reason": None, "generated_at": None,
            "summary": None, "missed": []}


# 2026-10-26 JEVCALHC：JEV 判读质量（账本由 JevDriftDaily 07:10 P4 生成）
JEV_CALIB_MIN_SAMPLES = 200
JEV_CALIB_EDGE_PP = 0.02
JEV_CALIB_RED_RATE = 0.48


def collect_jev_calibration(repo_root: Path | None = None) -> dict[str, Any]:
    """读 agent_platform/data/jev_audit/latest.json（只读派生账本报告）。"""
    repo_root = repo_root or Path(__file__).resolve().parents[1]
    path = repo_root / "agent_platform" / "data" / "jev_audit" / "latest.json"
    out: dict[str, Any] = {"available": False, "path": str(path), "reason": None,
                           "generated_at": None, "directional": None,
                           "baseline_bull": None, "edge_vs_baseline": None,
                           "band_pct": None, "neutral_hit": None,
                           "neutral_highvol": None, "bear_usable": None,
                           "bull_usable": None, "conf_decision_weight": None,
                           "data_through": None, "latest_trade_day": None,
                           "stale_trade_days": None, "n_days": None,
                           "wilson_lower": None, "wilson_lower_gt_baseline": None,
                           "gate_pass": None, "low_signal_event_types": []}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        out["reason"] = "读不到 %s: %s" % (path.name, _short_exc(exc))
        return out
    try:
        data = json.loads(raw)
    except ValueError as exc:
        out["reason"] = "JSON 解析失败: %s" % _short_exc(exc)
        return out
    if not isinstance(data, dict):
        out["reason"] = "报告结构异常"
        return out
    d = data.get("directional") or {}
    a = data.get("assessment") or {}
    out.update(
        available=True,
        generated_at=data.get("generated_at"),
        directional={"n": d.get("n"), "rate": d.get("rate")},
        baseline_bull=((d.get("baseline_all_bull") or {}).get("rate")),
        edge_vs_baseline=(a.get("directional") or {}).get("edge_vs_baseline"),
        band_pct=(a.get("neutral") or {}).get("band_pct"),
        neutral_hit=(a.get("neutral") or {}).get("band_hit_rate"),
        neutral_highvol=(a.get("neutral") or {}).get("highvol_share"),
        bear_usable=(a.get("bear") or {}).get("usable"),
        bull_usable=(a.get("bull") or {}).get("usable"),
        conf_decision_weight=(a.get("confidence") or {}).get("decision_weight"),
        data_through=data.get("data_through"),
        latest_trade_day=data.get("latest_trade_day"),
        stale_trade_days=data.get("stale_trade_days"),
        n_days=data.get("n_trade_days"),
        wilson_lower=(a.get("directional") or {}).get("wilson95_lower"),
        wilson_lower_gt_baseline=(
            (a.get("directional") or {}).get("wilson95_lower") is not None
            and ((a.get("directional") or {}).get("wilson95_lower")
                 > ((d.get("baseline_all_bull") or {}).get("rate") or 1.0))),
        gate_pass=(a.get("directional") or {}).get("gate_pass"),
        low_signal_event_types=(a.get("low_signal_event_types") or []),
    )
    return out


def _jev_calibration_level(cal: dict[str, Any]) -> tuple[str, str]:
    """判级：账本缺失/样本不足黄；方向准确率崩塌红；有 edge 绿；无增量黄。"""
    if not cal.get("available"):
        return "yellow", "判读账本暂不可用（JevDriftDaily 07:10 P4 生成）：" + str(cal.get("reason") or "")
    d = cal.get("directional") or {}
    n = int(d.get("n") or 0)
    rate = d.get("rate")
    base = cal.get("baseline_bull")
    band = cal.get("band_pct")
    bits = ["账本方向类 n=%d" % n]
    if rate is not None:
        bits.append("方向准确率 %.1f%%" % (rate * 100))
    if base is not None:
        bits.append("全判 bull 基线 %.1f%%" % (base * 100))
    if band is not None:
        bits.append("neutral 带宽 %.1f%%（命中 %.0f%%，高波动 %.0f%%）" % (
            band, (cal.get("neutral_hit") or 0) * 100, (cal.get("neutral_highvol") or 0) * 100))
    if cal.get("bear_usable") is False:
        bits.append("bear 仅风险标签")
    if cal.get("conf_decision_weight") is False:
        bits.append("conf 不入决策")
    nd = cal.get("n_days")
    if nd is not None:
        bits.append("覆盖 %d 个交易日" % nd)
    dth = cal.get("data_through")
    if dth:
        bits.append("数据截至 %s%s" % (
            dth, "（落后 %s 个交易日）" % cal.get("stale_trade_days")
            if cal.get("stale_trade_days") else ""))
    low = cal.get("low_signal_event_types") or []
    if low:
        bits.append("低信号事件：%s" % "、".join(low))
    detail = "；".join(bits)
    if n == 0:
        return "yellow", "账本尚无后验样本（T+1 数据未回填）；" + detail
    if n < JEV_CALIB_MIN_SAMPLES:
        return "yellow", "样本不足（%d < %d 条）；" % (n, JEV_CALIB_MIN_SAMPLES) + detail
    stale = cal.get("stale_trade_days")
    if stale is not None and stale > 1:
        return "yellow", "校准数据落后 %d 个交易日（data_through=%s）；" % (
            stale, dth) + detail
    if rate is not None and base is not None and rate < base - 0.05:
        return "red", "方向准确率 %.1f%% 显著低于基线 %.1f%%（模型退化）；" % (
            rate * 100, base * 100) + detail
    if rate is not None and base is None and rate < JEV_CALIB_RED_RATE:
        return "red", "方向准确率低于 %.0f%%（模型退化）；" % (JEV_CALIB_RED_RATE * 100) + detail
    gate = cal.get("gate_pass")
    if gate is True:
        return "green", "通过门控（edge +%.1fpp、Wilson 下界 %.3f > 基线、%d 个交易日）；" % (
            (cal.get("edge_vs_baseline") or 0) * 100,
            cal.get("wilson_lower") or 0.0, nd or 0) + detail
    if gate is None and (cal.get("edge_vs_baseline") or 0) >= JEV_CALIB_EDGE_PP:
        return "green", "相对基线有增量（+%.1fpp）；" % (
            (cal.get("edge_vs_baseline") or 0) * 100) + detail
    if cal.get("wilson_lower") is not None and cal.get("wilson_lower_gt_baseline") is False:
        return "yellow", "edge 未过门控（Wilson 下界 %.3f ≤ 基线）；" % (
            cal.get("wilson_lower") or 0.0) + detail
    return "yellow", "无显著增量 edge（+%.1fpp < %.0fpp 门槛或交易日不足）；" % (
        (cal.get("edge_vs_baseline") or 0) * 100, JEV_CALIB_EDGE_PP * 100) + detail


def collect_agent_daily(repo_root: Path) -> dict[str, Any]:
    result = _empty_agent_daily()
    script = repo_root / "tools" / "agent_daily.py"
    if not script.exists():
        result["reason"] = "tools/agent_daily.py 不存在"
        return result
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(script), "--json", "--no-ledger"],
            capture_output=True, timeout=AGENT_DAILY_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = "调用失败: " + _short_exc(exc)
        return result
    raw = (proc.stdout or b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not raw:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:160]
        result["reason"] = "退出码 %s：%s" % (proc.returncode, err or "无输出")
        return result
    try:
        card = json.loads(raw)
    except ValueError as exc:
        result["reason"] = "输出不是 JSON: " + _short_exc(exc)
        return result
    missed = [r for r in (card.get("rows") or []) if r.get("status") == "未交付"]
    result.update(available=True, generated_at=card.get("generated_at"),
                  summary=card.get("summary") or {}, missed=missed[:8])
    return result


def _agent_daily_detail(d: dict[str, Any]) -> str:
    s = d.get("summary") or {}
    bits = ["事项 %s" % _fmt_num(s.get("总事项")),
            "交付 %s" % _fmt_num(s.get("已交付")),
            "未交付 %s" % _fmt_num(s.get("未交付")),
            "不适用 %s" % _fmt_num(s.get("不适用")),
            "待接线 %s" % _fmt_num(s.get("待接线"))]
    miss = d.get("missed") or []
    if miss:
        bits.append("欠账：" + "、".join("%s(%s)" % (m.get("id"), m.get("agent")) for m in miss))
    return "；".join(bits)


# ---------------------------------------------------------------------------
# 状态判定
# ---------------------------------------------------------------------------
def _evaluate(card: dict[str, Any]) -> tuple[list[dict[str, str]], str, list[str]]:
    checks: list[dict[str, str]] = []

    def add(check_id: str, level: str, detail: str) -> None:
        checks.append({"id": check_id, "level": level, "detail": detail})

    audit = card["task_audit"]
    if not audit["available"]:
        add("task_audit.readable", "red", "计划任务审计不可用：" + (audit["reason"] or "未知原因"))
    else:
        add(
            "task_audit.readable",
            "green",
            f"账本可读，最后一个扫描会话 {audit['total_scanned']} 个任务 @ {audit['last_scan_at']}",
        )
        if audit["failed"]:
            # 摘要是一次**快照**：账本里记的是扫描那一刻各任务的 LastTaskResult。
            # 隔了半天再看，有些失败可能已经自愈（例如 AgentLegionHeartbeat 10-21
            # 推理超时 rc=2，10-22 08:46 已 status=ok），但摘要不会自己更新。
            # 所以把"这份摘要多大年纪"直接写在结论里，避免把陈旧数字当现状。
            age = _summary_age_hours(audit)
            age_note = ""
            if age is not None:
                age_note = f"；摘要生成于 {age:.1f} 小时前"
                if age >= 6:
                    age_note += "，其中的失败可能已自愈，需重跑 task_exit_audit --scan 复核"
            # 2026-10-23：从"快照"升级为"窗口对比"——只报"现在几个失败"回答不了
            # "在变好还是变坏"。三类计数直接回答，且已用真实账本验证过
            # （fixed 里确实有 AgentLegionHeartbeat 与 QuantWatchdog 两个已修好的）。
            window_note = ""
            new_n = len(audit.get("new_failures") or [])
            fix_n = len(audit.get("fixed_in_window") or [])
            keep_n = len(audit.get("persisting_failures") or [])
            # 2026-10-25 EXEC-R23：把失败任务名+退出码直接写进 detail（旧版只有计数，
            # 还得自己翻 summary 才知道是哪 3 个）。上限 6 条，避免 detail 过长。
            fail_bits = [
                "%s=%s" % (item.get("name"), item.get("last_result"))
                for item in (audit.get("failures") or [])[:6]
                if item.get("name")
            ]
            names_note = ("；明细：" + "、".join(fail_bits)) if fail_bits else ""
            if audit.get("window_baseline_at"):
                window_note = (
                    f"；{audit.get('window_hours', 24):g}h 窗口内："
                    f"新增 {new_n} / 持续 {keep_n} / **已修复 {fix_n}**")
                if new_n:
                    window_note += "（新增：" + "、".join(
                        (audit.get("new_failures") or [])[:3]) + "）"
                if fix_n:
                    window_note += "（已修复：" + "、".join(
                        (audit.get("fixed_in_window") or [])[:3]) + "）"
            add(
                "task_audit.failures",
                "red",
                f"{audit['failed']} 个任务真失败（口径同 task_exit_audit；跑批中/被终止/取消/上游拒跑已单列不计）"
                + names_note + age_note + window_note,
            )
        else:
            add("task_audit.failures", "green", "无失败任务")

        unknown_total = (audit["unknown_exit_codes"] or 0) + (audit["unknown_states"] or 0)
        if unknown_total:
            add(
                "task_audit.unknown",
                "yellow",
                f"未知退出码 {audit['unknown_exit_codes']} 个 / 未知状态 {audit['unknown_states']} 个",
            )
        else:
            add("task_audit.unknown", "green", "未知退出码 0 / 未知状态 0")

        age = audit["age_hours"]
        if age is None:
            add("task_audit.freshness", "yellow", "无法计算最近扫描距今时长")
        elif age > AUDIT_RED_HOURS:
            add("task_audit.freshness", "red", f"审计已 {age:.1f}h 未更新（>{AUDIT_RED_HOURS:g}h）")
        elif age > AUDIT_YELLOW_HOURS:
            add(
                "task_audit.freshness",
                "yellow",
                f"审计已 {age:.1f}h 未更新（>{AUDIT_YELLOW_HOURS:g}h）",
            )
        else:
            add("task_audit.freshness", "green", f"审计距今 {age:.1f}h")

        if audit["clock_skew"]:
            add("task_audit.clock_skew", "yellow", "账本 at 在未来，可能存在时钟偏差")

        integrity_bits = []
        if audit["ledger_parse_errors"]:
            integrity_bits.append(f"{audit['ledger_parse_errors']} 行无法解析")
        if audit["undated_records"]:
            integrity_bits.append(f"{audit['undated_records']} 条缺 at")
        if integrity_bits:
            add("task_audit.ledger_integrity", "yellow", "账本不完整：" + "、".join(integrity_bits))
        else:
            add("task_audit.ledger_integrity", "green", "账本全部可解析")

        if not audit["summary_available"]:
            add(
                "task_audit.summary_sync",
                "yellow",
                "摘要不可读（仅账本）: " + (audit["summary_error"] or "未知原因"),
            )
        elif audit["summary_matches_ledger"]:
            add("task_audit.summary_sync", "green", "摘要与账本同步")
        else:
            bits = []
            if audit["summary_time_delta_seconds"] is not None:
                bits.append(f"时间差 {audit['summary_time_delta_seconds']:.0f}s")
            drift = audit["summary_count_drift"] or {}
            if drift:
                bits.append(
                    "计数漂移 "
                    + ", ".join(
                        f"{key}:摘要={value['summary']}/账本={value['ledger']}"
                        for key, value in list(drift.items())[:3]
                    )
                )
            add(
                "task_audit.summary_sync",
                "yellow",
                "账本与摘要不一致：" + ("；".join(bits) or "字段缺失"),
            )

        if audit["semantics_error"]:
            add(
                "task_audit.semantics",
                "yellow",
                "无法复用 task_exit_audit.EXIT_SEMANTICS，未知码退化判定: "
                + audit["semantics_error"],
            )
        else:
            add("task_audit.semantics", "green", "未知码判定复用 EXIT_SEMANTICS")

    backup = card["backup"]
    if not backup["available"]:
        add("backup.readable", "red", "备份不可用：" + (backup["reason"] or "未知原因"))
    else:
        add(
            "backup.readable",
            "green",
            f"最近备份 {backup['latest_date']}，清单 {backup['manifest_entries']} 条，{backup['total_human']}",
        )
        age = backup["age_hours"]
        if age is None:
            add("backup.freshness", "red", "无法计算备份距今时长")
        elif age > BACKUP_RED_HOURS:
            add("backup.freshness", "red", f"备份已 {age:.1f}h 未更新（>{BACKUP_RED_HOURS:g}h）")
        elif age > BACKUP_YELLOW_HOURS:
            add(
                "backup.freshness",
                "yellow",
                f"备份已 {age:.1f}h 未更新（>{BACKUP_YELLOW_HOURS:g}h）",
            )
        else:
            add("backup.freshness", "green", f"备份距今 {age:.1f}h")

        if backup["clock_skew"]:
            add("backup.clock_skew", "yellow", "备份目录 mtime 在未来，可能存在时钟偏差")
        if backup["manifest_missing"]:
            add(
                "backup.integrity",
                "red",
                f"{backup['manifest_missing']} 个清单文件不在目录中: "
                + "、".join(backup["missing_files"][:5]),
            )
        elif backup["manifest_malformed"] or backup["size_mismatch"]:
            bits = []
            if backup["manifest_malformed"]:
                bits.append(f"清单 {backup['manifest_malformed']} 行格式异常")
            if backup["size_mismatch"]:
                bits.append(f"{backup['size_mismatch']} 个文件大小与清单不符")
            add("backup.integrity", "yellow", "；".join(bits))
        else:
            add("backup.integrity", "green", "清单条目齐全、大小一致（未重算 sha256）")

        catchup = backup.get("catchup") or {}
        if catchup.get("available"):
            if catchup.get("status") == "current":
                add(
                    "backup.catchup",
                    "green",
                    "%d 个仓的 bundle 均追平 HEAD"
                    % len(catchup.get("entries") or []),
                )
            elif catchup.get("status") == "behind":
                level = catchup.get("level")
                if level not in ("yellow", "red"):
                    level = "yellow"
                detail = _catchup_behind_text(catchup)
                age = catchup.get("backup_age_hours")
                if level == "red" and isinstance(age, (int, float)):
                    detail = "备份已 %.1fh 未更新且落后：%s" % (age, detail)
                add("backup.catchup", level, detail)
            else:
                add("backup.catchup", "yellow",
                    "无法判定备份追平情况（catchup status=%s）：%s"
                    % (catchup.get("status") or "unknown",
                       str(catchup.get("reason") or catchup.get("error")
                           or "无 bundle/仓目录缺失")))

    data_drift = (card.get("data") or {}).get("root_drift") or {}
    if not data_drift:
        add("data.root_drift", "red", "数据根漂移检查未运行（data 块缺失）")
    else:
        # level 由采集侧给全（绿/黄/红）；缺失按红兜底，绝不冒充绿。
        drift_level = data_drift.get("level")
        add(
            "data.root_drift",
            drift_level if drift_level in ("green", "yellow", "red") else "red",
            _data_root_drift_detail(data_drift),
        )

    curated = (card.get("data") or {}).get("curated_freshness") or {}
    if not curated:
        add("data.curated_freshness", "red", "整理层新鲜度检查未运行（data 块缺失）")
    else:
        # 同上：等级由采集侧给全；采集侧没给等级就是不可判定，按红兜底。
        curated_level = curated.get("level")
        add(
            "data.curated_freshness",
            curated_level if curated_level in ("green", "yellow", "red") else "red",
            _curated_freshness_detail(curated),
        )

    # 数据底座健康三项（2026-10-26 OS-FIX-DATAHEALTH）：与 curated_freshness 并列，
    # 等级同样由采集侧给全；缺失/无等级一律红兜底（绝不静默当绿）。
    for check_id, block_key, detail_fn in (
        ("data.shibor_lpr_freshness", "shibor_lpr_freshness", _shibor_lpr_detail),
        ("data.tdx_manifest", "tdx_manifest", _tdx_manifest_detail),
        ("data.trade_cal_dedup", "trade_cal_dedup", _trade_cal_dedup_detail),
    ):
        block = (card.get("data") or {}).get(block_key) or {}
        if not block:
            add(check_id, "red", "数据底座健康检查未运行（data 块缺失）：" + block_key)
            continue
        block_level = block.get("level")
        add(
            check_id,
            block_level if block_level in ("green", "yellow", "red") else "red",
            detail_fn(block),
        )

    repo = card["repo"]
    if not repo["available"]:
        add("repo.readable", "red", "仓库卫生不可用：" + (repo["reason"] or "未知原因"))
    else:
        add(
            "repo.readable",
            "green",
            f"仓库可读 @ {repo['branch'] or '(detached)'}",
        )
        if repo["conflicted_files"]:
            add("repo.conflicts", "red", f"{repo['conflicted_files']} 个冲突文件")
        if repo["dirty_files"]:
            add(
                "repo.dirty",
                "yellow",
                f"{repo['dirty_files']} 个未提交变更（未跟踪 {repo['untracked_files']}）",
            )
        else:
            add("repo.dirty", "green", "工作区干净")
        if repo["worktree_dirs_consistent"]:
            add(
                "repo.worktrees",
                "green",
                f".worktrees 目录 {repo['worktree_dirs']} 与 git worktree 关联数 {repo['linked_worktrees']} 一致",
            )
        else:
            add(
                "repo.worktrees",
                "yellow",
                f".worktrees 目录 {repo['worktree_dirs']} 与 git worktree 关联数 {repo['linked_worktrees']} 不一致",
            )

    ttl = repo.get("worktree_ttl") or {}
    if not ttl.get("available"):
        add(
            "repo.worktree_ttl",
            "red",
            "worktree TTL 不可用：" + (ttl.get("reason") or "未知原因"),
        )
    elif ttl.get("violations"):
        add("repo.worktree_ttl", "yellow", _worktree_ttl_detail(ttl))
    elif ttl.get("dirty_expired"):
        # 超期但仍持有未提交改动：判黄（不是绿，规则 9 没满足；也不是红，
        # 因为没有东西坏掉）。detail 明确写"不可自动回收"，避免照抄 remove。
        add("repo.worktree_ttl", "yellow", _worktree_ttl_detail(ttl))
    elif ttl.get("unknown"):
        add("repo.worktree_ttl", "yellow", _worktree_ttl_unknown_detail(ttl))
    else:
        add(
            "repo.worktree_ttl",
            "green",
            "关联工作树 %d 个，无「已合并且超过 %g 天」项（年龄按分支最后提交时间）"
            % (
                ttl.get("linked_worktrees") or 0,
                ttl.get("threshold_days") or WORKTREE_TTL_DAYS,
            ),
        )

    docs_freshness = (card.get("docs") or {}).get("index_freshness") or {}
    if not docs_freshness.get("available"):
        add(
            "docs.index_freshness",
            "red",
            "文档索引检查不可用：" + (docs_freshness.get("reason") or "未知原因"),
        )
    elif docs_freshness.get("status") == "stale":
        add("docs.index_freshness", "yellow", _docs_index_detail(docs_freshness))
    elif docs_freshness.get("status") == "fresh":
        add("docs.index_freshness", "green", _docs_index_detail(docs_freshness))
    else:
        add(
            "docs.index_freshness",
            "yellow",
            "文档索引检查状态未知：" + str(docs_freshness.get("status") or "null"),
        )

    watch = (card.get("ops") or {}).get("alert_watch") or {}
    if not watch:
        add("ops.alert_watch_liveness", "red", "警报监控存活检查未运行（ops 块缺失）")
    else:
        # 2026-10-25 EXEC-R8：优先看执行心跳（本体是否在跑）；心跳缺失/过期回退旧
        # 状态文件口径（>26h 红）。判级逻辑集中在 _alert_watch_level，便于反例测试。
        level, detail = _alert_watch_level(watch)
        add("ops.alert_watch_liveness", level, detail)

    launch = (card.get("ops") or {}).get("task_launch_failures") or {}
    # 2026-10-26 统一测量：判级集中在 _task_launch_level（N/A 不判红），
    # 便于反例测试；此处只负责把 level/detail 挂到 checks。
    launch_level, launch_detail = _task_launch_level(launch)
    add("ops.task_launch_failures", launch_level, launch_detail)

    batch = (card.get("ops") or {}).get("batch_backlog") or {}
    if not batch:
        add("ops.batch_backlog", "red", "夜间批处理积压检查未运行（ops 块缺失）")
    else:
        # 2026-10-25 EXEC-R13：积压趋势/连续 rc=124 判级集中在 _batch_backlog_level，
        # 便于反例测试；读不到/无 ROUND 一律红（不判绿）。
        level, detail = _batch_backlog_level(batch)
        add("ops.batch_backlog", level, detail)

    # ---------------------------------------------------------------------------
    # 6) Hermes 推送健康（hermes.push，2026-10-26 K-ORG-20261026-HERMESFIX）
    #    JEV→Hermes 一切集成的必经通道：agent 回执失败必须可判定，不能只看 task rc=0。
    # ---------------------------------------------------------------------------
    try:
        _hp = _collect_hermes_push()
        _hp_level, _hp_detail = _hermes_push_level(_hp)
        add("hermes.push", _hp_level, _hp_detail)
    except Exception as _exc:  # noqa: BLE001
        add("hermes.push", "yellow", "Hermes 推送健康检查异常：%r" % (_exc,))

    # ---------------------------------------------------------------------------
    # 6.1) 治理 AI 建议健康（governance.ai_advice，2026-10-26 GOVAI-2）
    #      judge 逻辑在 gov_ai.health_status（单一真源）；此处以子进程只读调用，
    #      任何异常一律黄（不红、不阻塞整张卡）。
    # ---------------------------------------------------------------------------
    try:
        import subprocess as _sp
        import os as _os
        _gov_py = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "agent_platform", "tools", "gov_ai.py")
        _gp = _sp.run([sys.executable, _gov_py, "--health-status", "--json"],
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=90,
                      env=dict(_os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
        _gobj = json.loads(_gp.stdout.strip()) if (_gp.stdout or "").strip() else {}
        _gl = str(_gobj.get("level") or "yellow")
        _gd = str(_gobj.get("detail") or "治理 AI 健康检查无输出")
        add("governance.ai_advice", _gl if _gl in ("green", "yellow", "red") else "yellow",
            _gd[:300])
    except Exception as _exc:  # noqa: BLE001
        add("governance.ai_advice", "yellow", "治理 AI 健康检查未运行：%r" % (_exc,))

    # ---------------------------------------------------------------------------
    # 6.2) 经验库消费健康（governance.experience_library，2026-10-26 P1）
    #      判级：有签字且全部 verified=绿；有条目但存在未验签=黄；空库=红。
    # ---------------------------------------------------------------------------
    try:
        import os as _os2
        _repo2 = _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__)))
        _ap2 = _os2.path.join(_repo2, "agent_platform")
        if _ap2 not in sys.path:
            sys.path.insert(0, _ap2)
        from engine import experience_store as _exp
        _es = _exp.public_summary()
        _total = int(_es.get("entries_total") or 0)
        _verified = int(_es.get("verified_total") or 0)
        if _total == 0:
            _el, _ed = "red", "经验库为空：签字链尚未入库任何判例"
        elif _verified == _total:
            _el = "green"
            _ed = ("经验库 %d 条全部 verified；最近签字 %s；supervision=%d"
                   % (_total, _es.get("latest_signed_at") or "?", 
                      int((_es.get("domains") or {}).get("supervision", {}).get("entries", 0))))
        else:
            _el = "yellow"
            _ed = "经验库 %d 条中 verified %d（存在未验签）" % (_total, _verified)
        add("governance.experience_library", _el, _ed[:300])
    except Exception as _exc:  # noqa: BLE001
        add("governance.experience_library", "yellow",
            "经验库消费检查未运行：%r" % (_exc,))

    keys = (card.get("security") or {}).get("plaintext_keys") or {}
    if not keys:
        add("security.plaintext_keys", "red", "明文私钥扫描未运行（security 块缺失）")
    else:
        findings = keys.get("findings") or []
        # 命中里是否**全部**是测试夹具：仓里有两个按设计嵌入假私钥字样的自测文件
        # （tools/_scan_secrets_selftest.py、portal/test_auto_commit_secret.py）。
        # 原规则「命中 ≥1 即红」会让这两条永久染红整张卡——红信号被训练成噪音，
        # 真出事时反而没人看。2026-10-22 改：只命中夹具 → 绿（detail 说明是夹具）；
        # 只要有一个非夹具命中 → 红。绝不放宽「真凭据即红」。
        fixture_only = bool(findings) and all(f.get("test_fixture") for f in findings)
        if not keys.get("available"):
            # 扫描没跑完（超时/异常）：等于没看全，判红。
            key_level = "red"
        elif findings and not fixture_only:
            key_level = "red"
        elif fixture_only:
            key_level = "green"
        elif keys.get("skipped_dirs") or keys.get("unreadable_files"):
            # 有目录/文件读不到：等于没看全，不判绿。
            key_level = "yellow"
        else:
            key_level = "green"
        add("security.plaintext_keys", key_level, _plaintext_keys_detail(keys))

    inference = (card.get("ai") or {}).get("inference") or {}
    if not inference:
        add("ai.inference", "red", "本地推理检查未运行（ai 块缺失）")
    elif not inference.get("available"):
        add(
            "ai.inference",
            "red",
            "本地推理不可达：" + str(inference.get("reason") or "未知原因"),
        )
    elif inference.get("installed") is False:
        # 服务活着但模型不在：调用方才 500，健康卡不能因为 /api/tags 能连就判绿。
        add(
            "ai.inference",
            "red",
            "Ollama 可达（已装 %s 个模型）但没有 %s：agent 调用会直接 500"
            % (_fmt_num(inference.get("installed_models")), AI_MODEL),
        )
    elif inference.get("ps_ok") is False:
        add(
            "ai.inference",
            "yellow",
            "读不到 /api/ps，无法判定 %s 是否驻留：%s"
            % (AI_MODEL, inference.get("ps_error") or "未知原因"),
        )
    elif not inference.get("resident"):
        add("ai.inference", "yellow", _ai_inference_not_resident_detail(inference))
    else:
        ai_level, ai_detail = _ai_inference_verdict(inference)
        add("ai.inference", ai_level, ai_detail)

    err = (card.get("ai") or {}).get("error_rate") or {}
    if not err:
        add("ai.error_rate", "red", "推理错误率检查未运行（ai 块缺失）")
    elif not err.get("available"):
        add("ai.error_rate", "red",
            "推理错误率不可用：" + str(err.get("reason") or "未知原因"))
    else:
        # 2026-10-25 EXEC-R6 口径拆分：真 server_errors>0 仍「一票红」（保留严格）；
        # client_aborts（客户端主动断连：旧 20s 超时 / M3 批处理排队）不计失败率，
        # 只进 detail；无真错误但仍有 client_aborts 时判黄，提示核对调用方超时，
        # 不静默判绿（残留断连说明修复未覆盖全部调用方）。
        detail = _ai_error_detail(err)
        ratio = err.get("ratio") or 0.0
        total = err.get("total") or 0
        client_aborts = err.get("client_aborts") or 0
        if (err.get("server_errors") or 0) > 0:
            add("ai.error_rate", "red", detail)
        elif total < AI_ERROR_MIN_SAMPLES:
            add("ai.error_rate", "yellow",
                detail + "（样本 <%d，不判绿）" % AI_ERROR_MIN_SAMPLES)
        elif ratio > AI_ERROR_RED_RATIO:
            add("ai.error_rate", "red", detail)
        elif ratio > AI_ERROR_YELLOW_RATIO:
            add("ai.error_rate", "yellow", detail)
        elif client_aborts > 0:
            add("ai.error_rate", "yellow",
                detail + "（仅客户端主动断连，不计失败率；建议核对调用方超时设置）")
        elif (err.get("covered_minutes") is not None
              and err["covered_minutes"] < AI_ERROR_WINDOW_MINUTES - 5):
            add("ai.error_rate", "yellow",
                detail + "（窗口覆盖不足，不判绿）")
        else:
            add("ai.error_rate", "green", detail)

    cen = (card.get("agent") or {}).get("census") or {}
    if not cen:
        add("agent.census", "red", "智能体点名册未运行（agent 块缺失）")
    elif not cen.get("available"):
        add("agent.census", "red",
            "智能体点名册不可用：" + str(cen.get("reason") or "未知原因"))
    else:
        # 判级只认两件事：有没有人"名下有单却不动"，以及整体静默比例。
        s = cen.get("summary") or {}
        detail = _agent_census_detail(cen)
        open_count = s.get("有单未动") or 0
        silent_ratio = s.get("静默占比") or 0.0
        if open_count >= AGENT_CENSUS_RED_OPEN:
            add("agent.census", "red", detail)
        elif open_count > 0 or silent_ratio >= AGENT_CENSUS_YELLOW_SILENT:
            add("agent.census", "yellow", detail)
        else:
            add("agent.census", "green", detail)

    daily = (card.get("agent") or {}).get("daily") or {}
    if not daily:
        add("agent.daily", "red", "每日工作项检查未运行（agent 块缺失）")
    elif not daily.get("available"):
        add("agent.daily", "red",
            "每日工作项不可用：" + str(daily.get("reason") or "未知原因"))
    else:
        # 有欠账 → 红（该交的没交）；只有"待接线" → 黄（没判据，不算过）。
        s = daily.get("summary") or {}
        detail = _agent_daily_detail(daily)
        if (s.get("未交付") or 0) > 0:
            add("agent.daily", "red", detail)
        elif (s.get("待接线") or 0) > 0:
            add("agent.daily", "yellow", detail)
        else:
            add("agent.daily", "green", detail)

    calib = collect_jev_calibration()
    cal_level, cal_detail = _jev_calibration_level(calib)
    add("jev.calibration", cal_level, cal_detail)

    sus = (card.get("ops") or {}).get("machine_suspend") or {}
    # 2026-10-26 EXEC-R32：判级集中到 _machine_suspend_level——查询失败/权限不足/
    # 超时=不可判定≠故障 → 黄（detail 写明原因与重试方式）；只有真读到挂起/意外关机
    # 数据才按 10-24 开发机定调报黄，否则绿。绝不因查询失败染红整张卡。
    sus_level, sus_detail = _machine_suspend_level(sus)
    add("ops.machine_suspend", sus_level, sus_detail)

    rank = {"green": 0, "yellow": 1, "red": 2}
    status = max((check["level"] for check in checks), key=lambda level: rank[level])
    reasons = [
        f"[{check['level']}] {check['detail']}"
        for check in checks
        if check["level"] != "green"
    ]
    return checks, status, reasons


def build_card(
    repo_root: Path,
    vault_root: Path,
    audit_root: Path,
    now: dt.datetime,
    curated_root: Path | None = None,
    raw_root: Path | None = None,
) -> dict[str, Any]:
    semantics, semantics_error = _load_audit_semantics(repo_root)
    backup = collect_backup(vault_root, now)
    backup["catchup"] = collect_catchup(repo_root, backup, now)
    repo_block = collect_repo(repo_root, now)
    repo_block["worktree_ttl"] = collect_worktree_ttl(repo_root, now)
    curated_path = curated_root if curated_root is not None else repo_root / CURATED_REL
    raw_path = raw_root if raw_root is not None else repo_root / RAW_REL
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "status": "green",
        "exit_code": 0,
        "reasons": [],
        "checks": [],
        "task_audit": collect_task_audit(audit_root, now, semantics, semantics_error),
        "backup": backup,
        "data": {
            "root_drift": collect_data_root_drift(vault_root),
            "curated_freshness": collect_curated_freshness(curated_path, raw_path),
            # 数据底座健康（2026-10-26 OS-FIX-DATAHEALTH）：断供 / 未纳管 / 重复
            "shibor_lpr_freshness": collect_shibor_lpr_freshness(curated_path, now),
            "tdx_manifest": collect_tdx_manifest(repo_root, vault_root),
            "trade_cal_dedup": collect_trade_cal_dedup(curated_path),
        },
        "repo": repo_block,
        "docs": {"index_freshness": collect_docs_index(repo_root)},
        "ops": {"alert_watch": collect_alert_watch(repo_root),
                "task_launch_failures": collect_task_launch_failures(),
                "batch_backlog": collect_batch_backlog(),
                "machine_suspend": collect_machine_suspend()},
        "security": {"plaintext_keys": collect_plaintext_keys(repo_root)},
        "ai": {"inference": collect_ai_inference(),
               "error_rate": collect_ai_error_rate(repo_root)},
        "agent": {"census": collect_agent_census(repo_root),
                  "daily": collect_agent_daily(repo_root)},
        "notes": [
            "本脚本只读：不扫描/启停计划任务、不触发备份、不改仓库。",
            "dirty_files 含未跟踪文件；本脚本自身在提交前也会被计入。",
            "备份未重算 sha256（体积可达数百 MiB）；只核对清单条目、文件存在性与大小。",
            "备份追平：用 git bundle list-heads 把 bundle 内分支 SHA 与各仓 HEAD 比较；"
            "落后 >0 黄、落后且 >24h 未重备红；无法判定计 null，不误判绿。",
            "task_audit 失败口径沿用其账本：Disabled 不计、267011 记未运行、其余非零（含 267009/267014）记失败。",
            "未知退出码同时计入 failed（沿用审计口径），此处单独列示以便区分「已知失败」与「未知退出码」。",
            f"阈值：备份 >{BACKUP_YELLOW_HOURS:g}h 黄 / >{BACKUP_RED_HOURS:g}h 红；"
            "备份落后 HEAD >0 黄 / 落后且 >24h 红；"
            f"审计 >{AUDIT_YELLOW_HOURS:g}h 黄 / >{AUDIT_RED_HOURS:g}h 红；"
            "dirty>0 黄；工作树不一致黄；任一块不可用红。",
            "账本按落盘时间切分扫描会话，取最后一个会话；120s 内的连续记录视为同一次扫描。",
            f"worktree TTL（规则 9）：只算关联工作树（排除主树），"
            f"「已合并进 master/main + 分支最后提交 >{WORKTREE_TTL_DAYS:g} 天」黄；"
            "年龄取分支 tip 的 committerdate（目录 mtime 会被增删文件带偏）；"
            "detached/分支缺失计未知并判黄，git 不可用或读不到判红。",
            f"文档索引：子进程跑 python -B tools/gen_docs_index.py --check，"
            f"超时 {DOCS_CHECK_TIMEOUT_SECONDS:g}s、设计预算 {DOCS_CHECK_BUDGET_SECONDS:g}s；"
            "退出 0 绿 / 1 黄（索引落后）/ 超时或退出 2 或脚本缺失红；"
            "两条新检查合计目标 <3s，超预算按不可判定处理。",
            "数据根漂移：按日期目录名倒序取 _vault 下最新两份 <date>/data_manifest.json，"
            "比对总文件数/总字节/顶层目录集合/catalog.json sha256/数据根路径；"
            f"文件数变化 >{DATA_DRIFT_FILE_RED_RATIO * 100:g}% 或顶层目录数变化判红；"
            "只有一份清单、清单读不到、扫描未完成（scan.partial）都计 available=false"
            "（计数 null，不判绿；前两种除「只有一份」外均判红）。",
            "<vault>/data_manifest.json（gen_data_manifest.py 的默认 --out）不是日期快照，"
            "不参与比对，避免拿「实时文件」冒充历史。",
            "明文私钥扫描（security.plaintext_keys）：只扫 <repo>/outputs、<repo>/tools、"
            "<repo>/portal 递归 + 工作区根一级 *.pem/*.key/id_rsa*/.env*；"
            "跳过 .git/node_modules/.venv/.worktrees（另跳 __pycache__ 等编译缓存，"
            "避免 .pyc 把同一测试夹具重复计入）。只看文件名与内容特征，不解析密钥、"
            "不回显任何文件内容：命中只报「路径 + 字节数 + 是否被 git 跟踪」。",
            "明文私钥判定：命中 ≥1 红；扫描范围内有目录/文件读不到黄（不判绿）；"
            "扫描相位 >%gs 计 available=false 并判红（已扫到的命中仍列出）；"
            "单文件最多读 %s，更大的文件记 oversized_skipped 不读内容。"
            % (PLAINTEXT_KEY_BUDGET_SECONDS, _human_bytes(PLAINTEXT_KEY_MAX_CONTENT_BYTES)),
            "整理层新鲜度（data.curated_freshness）：整理层是纯派生产物，不会跟着原始层变新。"
            "口径=整理层最旧 parquet mtime vs 原始层递归最新 parquet mtime，另按 51 个数据集"
            "逐个比一遍（报落后的数据集数量），判定取两者中更差的一个；只看 mtime，"
            "不打开 parquet、不读 footer。"
            f"落后 <={CURATED_YELLOW_LAG_DAYS:g} 天绿 / >{CURATED_YELLOW_LAG_DAYS:g} 天黄 / "
            f">{CURATED_RED_LAG_DAYS:g} 天红（结论不可信）。",
            "整理层不可判定：整理层不存在/为空 → available=false + 黄（不判绿，计数 null）；"
            "原始层不存在、原始层没有 parquet、或遍历超 "
            f"{CURATED_SCAN_BUDGET_SECONDS:g}s 预算 → available=false + 红。"
            "重建命令见 tools/refresh_curated.py（--replace 会永久删除旧整理层，须先校验通过）。",
            "数据源断供（data.shibor_lpr_freshness，2026-10-26 数据底座巡查）：只读整理层"
            "shibor_lpr.parquet 的日期列（cal_date/date），取 max 日期距今天数；"
            f">{SHIBOR_LPR_YELLOW_DAYS:g} 天黄 / >{SHIBOR_LPR_RED_DAYS:g} 天红；"
            "文件缺失/读失败/列名不匹配/无有效日期 → 黄（不判绿）。建项起因："
            "文件 mtime 新、内容是两个月前的，mtime 口径的 curated_freshness 看不见断供。",
            "TDX vendor 纳管（data.tdx_manifest，2026-10-26 数据底座巡查）：用 os.walk 统计"
            "quant_backtest/data/tdx 的文件数/字节数（预算 "
            f"{TDX_WALK_BUDGET_SECONDS:g}s / {TDX_WALK_MAX_FILES} 文件，超限报未完成不判绿）；"
            f"> {_human_bytes(TDX_UNMANAGED_MIN_BYTES)} 且不在最新 <vault>/<date>/data_manifest.json"
            " 的 dirs[].name / dirs[].path / tables / paths 中 → 黄「未纳管」；"
            "< 阈值或目录不存在 → 绿/不适用；manifest 不可读 → 黄（不判绿）。"
            "注意 backup_daily 仍按 vendor 把该目录排除出 tar 集（可重建）——"
            "本项只看 manifest 纳管，不代表已进备份。",
            "交易日历去重（data.trade_cal_dedup，2026-10-26 数据底座巡查）：只读整理层"
            "trade_cal.parquet 的 cal_date 单列，ratio=行数/去重日期数；"
            f"> {TRADE_CAL_DUP_YELLOW_RATIO:g} 判黄（>{(TRADE_CAL_DUP_YELLOW_RATIO - 1) * 100:g}% 重复），"
            "detail 写 rows/distinct/倍数；读失败/无有效日期 → 黄。重复通常来自多 exchange"
            "行或重复 merge，按日期聚合会静默乘倍数。",
            "数据底座三项（shibor_lpr/tdx/trade_cal）读 parquet 时惰性 import pyarrow；"
            "pyarrow 不可用 → 判黄（不可采集，不判绿），其余检查仍零第三方依赖。",
            "本地推理（ai.inference）：只测 qwen3.5:9b，不跑三模型全量探针。"
            f"/api/tags {AI_TAGS_TIMEOUT_SECONDS:g}s 判可达、/api/ps "
            f"{AI_PS_TIMEOUT_SECONDS:g}s 判驻留（未驻留判黄且不发请求，绝不为测而冷加载）；"
            f"已驻留才发一条 think=false / num_ctx={AI_NUM_CTX} / "
            f"num_predict={AI_NUM_PREDICT} / temperature=0 的短生成，"
            f"{AI_CHAT_TIMEOUT_SECONDS:g}s 超时。message.content 为空红（带 thinking 长度）；"
            f"答案不含「{AI_EXPECTED_ANSWER}」黄（不切题=质量退化）；"
            f"/api/ps 的 context_length≠{AI_NUM_CTX} 黄（num_ctx 漂移会触发全模型重载）；"
            f"吞吐 <{AI_MIN_TOKS_PER_SECOND:g} tok/s 黄；请求 {AI_CHAT_TIMEOUT_SECONDS:g}s "
            f"未返回黄。整项预算 {AI_BUDGET_SECONDS:g}s；请求全部经 ProxyHandler({{}}) 直连；"
            "读不到的指标写进 detail，不静默当 0、不判绿。",
            "机器挂起（ops.machine_suspend）：读系统事件日志的 Kernel-General 1"
            "（时间被硬件钟纠正的毫秒数=挂起时长，>60s 才计）、Kernel-Power 42（睡眠次数）、"
            "41/6008（意外关机）。有意外关机 / 累计挂起 >60 分钟红、>10 分钟黄"
            "（2026-10-24 定调开发机一律只报黄）；**查询失败/权限不足/超时 → 黄"
            "（不可判定，非故障；detail 写明原因与重试方式）**。这是全卡唯一能把"
            "「机器没开」与「任务失败」分开的读数——2026-10-24 六条日志同时断档，"
            "应用侧只能看到少了几行，给不出时长。",
            "夜间批处理积压（ops.batch_backlog）：读白天 C:\\Temp\\night_batch.log + "
            "夜间 C:\\Temp\\jev_night.log 两份尾部，解析 ROUND/rc 行；JEV 日容量="
            "白天会话容量+夜间窗口容量（XQ 只在白天）；积压较上一轮上升或连续 rc=124 "
            "≥2 轮 → 红；仅 1 轮 rc=124 / ETA >48h / 两日志合计 >3h 无新轮 → 黄；"
            "ETA >96h 或两日志都读不到 → 红（不判绿）。"
            "对应修复单 EXEC-R13（单实例锁+软预算收口）、EXEC-R32（纳入夜班吞吐）。",
            "任务启动失败统计（ops.task_launch_failures）N/A 口径（2026-10-26 统一测量）："
            "schtasks 不可用（沙箱/权限会话）→ 采集不到 ≠ 任务故障，判黄 N/A、不计红，"
            "detail 写明「需管理员会话复核」；只有 ops 块缺失（采集器未运行）或真正采到 "
            "available=true 且发现「动作起来了但目标不存在」才红。",
            "警报监控存活（ops.alert_watch_liveness）：心跳新鲜（<=15 分钟）且 rc=3 / "
            "upstream_ok=false → 判黄（不是绿）：本体在跑、脚本按设计拒跑不是停摆，"
            "但上游 6 源 stale → 告警内容不可用，处置在数据上游；周末/非交易日追加"
            "「可能属预期，交易日需补源时间戳」。心跳新鲜 + rc=0 且 upstream_ok=None "
            "→ 判绿：alert_watch 非交易日按设计静默跳过（无 EOD 源预期），"
            "detail 写明「非交易日静默（预期）…交易日恢复校验」。rc=0 且 "
            "upstream_ok=true 才按上游校验通过判绿；rc 非 0/3 黄；心跳缺失/过期回退"
            "状态文件旧口径（>26h 红）。非交易日判定读 "
            "portal/data/trade_cal_open_days.json 的 SSE open_days（ZB_TRADE_CAL 可覆盖；"
            "日历缺失/坏回退 weekday>=5）。",
        ],
    }
    checks, status, reasons = _evaluate(card)
    card["checks"] = checks
    card["status"] = status
    card["reasons"] = reasons
    card["exit_code"] = 1 if status == "red" else 0
    return card


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _human_bytes(count: int | None) -> str:
    if count is None:
        return "—"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(count)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[index]}"


def _e(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _signed_int(value: Any) -> str:
    if not isinstance(value, int):
        return "—"
    return ("+%d" % value) if value > 0 else str(value)


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 0:
        return f"未来 {-hours:.1f} 小时（时钟偏差?）"
    if hours < 1:
        return f"{hours * 60:.0f} 分钟前（{hours:.2f}h）"
    return f"{hours:.1f} 小时前"


def _kv(label: str, value: Any) -> str:
    return f'<div class="kv"><span>{_e(label)}</span><b>{_e(value)}</b></div>'


_CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1f23;--muted:#66707a;--line:#e3e6ea;
--green:#1a7f37;--green-bg:#e8f5ec;--yellow:#8a5a00;--yellow-bg:#fff4d6;
--red:#b42318;--red-bg:#fde8e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
main{max-width:1100px;margin:32px auto;padding:0 20px 40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:28px;box-shadow:0 1px 3px rgba(16,24,40,.06)}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
h1{font-size:26px;margin:8px 0 6px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.chip{font-size:13px;font-weight:700;padding:5px 12px;border-radius:999px;letter-spacing:.03em}
.chip.green{color:var(--green);background:var(--green-bg)}
.chip.yellow{color:var(--yellow);background:var(--yellow-bg)}
.chip.red{color:var(--red);background:var(--red-bg)}
.meta{color:var(--muted);font-size:13px;margin:0;word-break:break-all}
.reasons{margin:18px 0 0;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:#fafbfc}
.reasons h2{font-size:13px;margin:0 0 8px;color:var(--muted)}
.reasons ul{margin:0;padding-left:20px}
.reasons li{font-size:13px;margin:4px 0;word-break:break-word}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-top:24px}
.block{border:1px solid var(--line);border-radius:12px;padding:18px}
.block.full{grid-column:1/-1}
.block h2{font-size:16px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:10px;word-break:break-all}
.kv{display:flex;justify-content:space-between;gap:16px;padding:6px 0;
border-bottom:1px dashed var(--line);font-size:14px}
.kv:last-child{border-bottom:0}
.kv span{color:var(--muted);flex:none}
.kv b{font-variant-numeric:tabular-nums;text-align:right;word-break:break-word}
.unavailable{margin:10px 0;padding:12px;border-radius:8px;background:var(--red-bg);
color:var(--red);font-size:13px;word-break:break-word}
.note{margin:12px 0 0;padding:10px 12px;border-radius:8px;background:#fafbfc;
font-size:12px;color:var(--muted);word-break:break-word}
.checks{list-style:none;margin:10px 0 0;padding:0}
.checks li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.checks li:last-child{border-bottom:0}
.dot{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none}
.dot.green{background:var(--green)}.dot.yellow{background:#b7791f}.dot.red{background:var(--red)}
.checks code{color:var(--muted);font-size:12px;margin-right:6px}
details{margin-top:24px}
summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{max-height:420px;overflow:auto;background:#0f1720;color:#d7e2ee;padding:16px;
border-radius:10px;font-size:12px;line-height:1.5}
footer{margin-top:22px;color:var(--muted);font-size:12px}
@media (max-width:600px){main{margin:16px auto}.card{padding:18px}h1{font-size:21px}}
"""


def _render_html(card: dict[str, Any]) -> str:
    status = card["status"]
    labels = {"green": "GREEN · 绿", "yellow": "YELLOW · 黄", "red": "RED · 红"}
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        f"<title>治理健康卡 · {_e(labels[status])} · {_e(card['generated_at'])}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        '<article class="card">',
        "<header>",
        '<div class="eyebrow">零界 · 治理健康卡</div>',
        f'<h1>总体状态 <span class="chip {status}">{_e(labels[status])}</span></h1>',
        f'<p class="meta">生成于 {_e(card["generated_at"])} · 退出码 {card["exit_code"]}'
        f' · 仓库 {_e(card["repo"]["repo_root"])}</p>',
        "</header>",
    ]

    reasons = card.get("reasons") or []
    if reasons:
        parts.append('<div class="reasons"><h2>为何不是绿</h2><ul>')
        parts.extend(f"<li>{_e(reason)}</li>" for reason in reasons)
        parts.append("</ul></div>")

    parts.append('<div class="grid">')

    audit = card["task_audit"]
    parts.append('<section class="block">')
    parts.append("<h2>计划任务审计</h2>")
    parts.append(f'<div class="sub">{_e(audit["ledger_path"])}</div>')
    if audit["available"]:
        parts.extend(
            [
                _kv("最近扫描", audit["last_scan_at"]),
                _kv("距今", _fmt_age(audit["age_hours"])),
                _kv("失败任务", _fmt_num(audit["failed"])),
                _kv("未知退出码", _fmt_num(audit["unknown_exit_codes"])),
                _kv("未知状态", _fmt_num(audit["unknown_states"])),
                _kv(
                    "运行中 / 未运行 / 禁用",
                    f"{_fmt_num(audit['running'])} / {_fmt_num(audit['never_run'])} / {_fmt_num(audit['disabled'])}",
                ),
                _kv("扫描总数", _fmt_num(audit["total_scanned"])),
            ]
        )
        if audit["failures"]:
            names = "、".join(str(item["name"]) for item in audit["failures"])
            parts.append(f'<p class="note">失败：{_e(names)}</p>')
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(audit["reason"])}</p>')
        if audit["summary_available"]:
            parts.append('<p class="note">摘要可读，但账本不可用；本卡不据此判定健康。</p>')
    parts.append("</section>")

    backup = card["backup"]
    parts.append('<section class="block">')
    parts.append("<h2>备份</h2>")
    parts.append(f'<div class="sub">{_e(backup["manifest_path"] or backup["vault_root"])}</div>')
    if backup["available"]:
        integrity = (
            f"缺 {backup['manifest_missing']} / 格式异常 {backup['manifest_malformed']}"
            f" / 大小不符 {backup['size_mismatch']}"
        )
        catchup = backup.get("catchup") or {}
        if catchup.get("available"):
            if catchup.get("status") == "current":
                catchup_text = "%d 个仓均追平 HEAD（%s）" % (
                    len(catchup["entries"]),
                    ", ".join(
                        "%s=%s" % (entry["label"], entry["head_sha_short"])
                        for entry in catchup["entries"]
                    ),
                )
            elif catchup.get("status") == "behind":
                catchup_text = _catchup_behind_text(catchup)
            else:
                catchup_text = "无法判定"
        else:
            catchup_text = "无法判定"
        parts.extend(
            [
                _kv("最近备份", backup["latest_date"]),
                _kv("距今", _fmt_age(backup["age_hours"])),
                _kv("MANIFEST 条目", _fmt_num(backup["manifest_entries"])),
                _kv("目录总体积（含清单）", backup["total_human"]),
                _kv("完整性", integrity),
                _kv("追平 HEAD", catchup_text),
                _kv("目录 mtime", backup["dir_mtime"]),
            ]
        )
        parts.append('<p class="note">未重算 sha256；只核对条目、存在性与大小。</p>')
        if not catchup.get("available"):
            parts.append(
                f'<p class="note">追平检查不可用：{_e(catchup.get("reason"))}</p>'
            )
        elif catchup.get("status") == "unknown":
            parts.append('<p class="note">部分仓无法判定，本项不计绿。</p>')
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(backup["reason"])}</p>')
    parts.append("</section>")

    drift = (card.get("data") or {}).get("root_drift") or {}
    parts.append('<section class="block">')
    parts.append("<h2>数据根漂移</h2>")
    parts.append(f'<div class="sub">{_e(drift.get("vault_root"))}</div>')
    if drift.get("available"):
        counts = drift.get("counts") or {}
        latest_counts = counts.get("latest") or {}
        previous_counts = counts.get("previous") or {}
        delta = counts.get("delta") or {}
        verdicts = {
            "consistent": "无漂移",
            "drift": "漂移（黄）",
            "major_drift": "剧烈漂移（红）",
            "catalog_unknown": "总量一致，catalog 指纹不可判定",
        }
        parts.extend(
            [
                _kv(
                    "对比期",
                    "%s → %s" % (drift.get("previous_date"), drift.get("latest_date")),
                ),
                _kv(
                    "文件数",
                    "%s → %s（%s）"
                    % (
                        _fmt_num(previous_counts.get("files")),
                        _fmt_num(latest_counts.get("files")),
                        _signed_int(delta.get("files")),
                    ),
                ),
                _kv(
                    "总字节",
                    "%s → %s（%s）"
                    % (
                        _human_bytes(previous_counts.get("bytes")),
                        _human_bytes(latest_counts.get("bytes")),
                        _signed_bytes(delta.get("bytes")),
                    ),
                ),
                _kv(
                    "目录数",
                    "%s → %s（%s）"
                    % (
                        _fmt_num(previous_counts.get("dirs")),
                        _fmt_num(latest_counts.get("dirs")),
                        _signed_int(delta.get("dirs")),
                    ),
                ),
                _kv("catalog.json", _catalog_drift_text(drift.get("catalog") or {})),
                _kv("判定", verdicts.get(str(drift.get("status")), str(drift.get("status")))),
            ]
        )
        added = drift.get("dirs_added") or []
        removed = drift.get("dirs_removed") or []
        if added or removed:
            parts.append(
                '<p class="note">新增目录：%s；删除目录：%s</p>'
                % (_e("、".join(added) or "—"), _e("、".join(removed) or "—"))
            )
    else:
        parts.append(f'<p class="unavailable">不可判定：{_e(drift.get("reason"))}</p>')
        if drift.get("dates"):
            parts.append(
                '<p class="note">已发现的清单期次：%s（需要连续两份才能比对）</p>'
                % _e("、".join(str(item) for item in drift["dates"]))
            )
    parts.append("</section>")

    curated = (card.get("data") or {}).get("curated_freshness") or {}
    parts.append('<section class="block">')
    parts.append("<h2>整理层新鲜度</h2>")
    parts.append(f'<div class="sub">{_e(curated.get("curated_root"))}</div>')
    if curated.get("available"):
        verdicts = {
            "fresh": "不落后",
            "stale": "该重建了（黄）",
            "expired": "已过期（红）",
        }
        parts.extend(
            [
                _kv("整理层最旧", curated.get("curated_oldest")),
                _kv("整理层最新", curated.get("curated_newest")),
                _kv("原始层最新", curated.get("raw_newest")),
                _kv("落后", _curated_lag_text(curated.get("lag_days"))),
                _kv(
                    "落后数据集",
                    "%s / %s（阈值 %g 天）"
                    % (
                        _fmt_num(curated.get("stale_datasets")),
                        _fmt_num(curated.get("datasets_compared")),
                        CURATED_YELLOW_LAG_DAYS,
                    ),
                ),
                _kv(
                    "判定",
                    verdicts.get(str(curated.get("status")), str(curated.get("status"))),
                ),
            ]
        )
        names = curated.get("stale_dataset_names") or []
        if names:
            parts.append('<p class="note">落后的数据集：%s</p>' % _e("、".join(names)))
    else:
        parts.append(f'<p class="unavailable">不可判定：{_e(curated.get("reason"))}</p>')
        parts.append(
            '<p class="note">建议：%s</p>' % _e(curated.get("suggested_command"))
        )
    parts.append("</section>")

    repo = card["repo"]
    parts.append('<section class="block">')
    parts.append("<h2>仓库卫生</h2>")
    parts.append(f'<div class="sub">{_e(repo["repo_root"])}</div>')
    if repo["available"]:
        parts.extend(
            [
                _kv("dirty 文件", _fmt_num(repo["dirty_files"])),
                _kv("未跟踪", _fmt_num(repo["untracked_files"])),
                _kv("git worktree 数（含主树）", _fmt_num(repo["worktrees"])),
                _kv(".worktrees 目录数", _fmt_num(repo["worktree_dirs"])),
                _kv("关联工作树一致", "是" if repo["worktree_dirs_consistent"] else "否"),
                _kv("分支", repo["branch"]),
            ]
        )
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(repo["reason"])}</p>')
    ttl = repo.get("worktree_ttl") or {}
    if ttl.get("available"):
        if ttl.get("violations"):
            ttl_text = "超期 %d 个" % ttl["violations"]
        elif ttl.get("unknown"):
            ttl_text = "无法判定 %d 个" % ttl["unknown"]
        else:
            ttl_text = "无超期（关联树 %d 个）" % (ttl.get("linked_worktrees") or 0)
        parts.append(f'<p class="note">worktree TTL：{_e(ttl_text)}</p>')
    else:
        parts.append(
            f'<p class="note">worktree TTL 不可用：{_e(ttl.get("reason"))}</p>'
        )
    parts.append("</section>")

    freshness = (card.get("docs") or {}).get("index_freshness") or {}
    parts.append('<section class="block">')
    parts.append("<h2>文档索引</h2>")
    parts.append(f'<div class="sub">{_e(freshness.get("script"))}</div>')
    if freshness.get("available"):
        parts.append(
            _kv(
                "状态",
                "落后（需重生成）"
                if freshness.get("status") == "stale"
                else "一致",
            )
        )
        if freshness.get("parsed") and freshness.get("diffs") is not None:
            parts.append(_kv("差异", _docs_diff_text(freshness["diffs"])))
        parts.append(
            _kv(
                "退出码 / 耗时",
                "%s / %ss"
                % (freshness.get("exit_code"), freshness.get("duration_seconds")),
            )
        )
    else:
        parts.append(
            f'<p class="unavailable">不可用：{_e(freshness.get("reason"))}</p>'
        )
    parts.append("</section>")

    keys = (card.get("security") or {}).get("plaintext_keys") or {}
    parts.append('<section class="block">')
    parts.append("<h2>明文私钥扫描</h2>")
    parts.append(
        '<div class="sub">%s（+ 工作区根一级 *.pem/*.key/id_rsa*/.env*）</div>'
        % _e("、".join(keys.get("scan_dirs") or []))
    )
    if keys.get("available") is False:
        parts.append(f'<p class="unavailable">不可用：{_e(keys.get("reason"))}</p>')
    else:
        parts.append(_kv("命中", "%d 个明文私钥" % len(keys.get("findings") or [])))
        for item in (keys.get("findings") or [])[:PLAINTEXT_KEY_DETAIL_LIMIT]:
            parts.append(
                '<p class="note">%s（%s，git:%s）</p>'
                % (
                    _e(item.get("path")),
                    _e(_human_bytes(item.get("bytes"))),
                    _e(_git_state_text(item.get("in_git"))),
                )
            )
        parts.append(
            _kv(
                "已扫文件 / 字节",
                "%s / %s"
                % (
                    _fmt_num(keys.get("scanned_files")),
                    _human_bytes(keys.get("scanned_bytes")),
                ),
            )
        )
        if keys.get("elapsed_ms") is not None:
            parts.append(
                _kv(
                    "耗时 / 预算",
                    "%sms / %ss"
                    % (keys.get("elapsed_ms"), keys.get("budget_seconds")),
                )
            )
    if keys.get("skipped_dirs"):
        parts.append(
            '<p class="note">读不到的目录：%s</p>'
            % _e("、".join(keys["skipped_dirs"][:5]))
        )
    if keys.get("unreadable_files"):
        parts.append(
            '<p class="note">读不到的文件：%s</p>'
            % _e("、".join(keys["unreadable_files"][:5]))
        )
    parts.append(
        '<p class="note">只报路径 / 字节数 / git 状态，不回显任何文件内容。</p>'
    )
    parts.append("</section>")

    ai = (card.get("ai") or {}).get("inference") or {}
    parts.append('<section class="block">')
    parts.append("<h2>本地推理</h2>")
    parts.append(
        '<div class="sub">%s · %s（只发一条 num_predict=%d 的短生成，整项 ≤%gs）</div>'
        % (
            _e(ai.get("host")),
            _e(ai.get("model")),
            AI_NUM_PREDICT,
            AI_BUDGET_SECONDS,
        )
    )
    if not ai.get("available"):
        parts.append(f'<p class="unavailable">不可用：{_e(ai.get("reason"))}</p>')
    elif ai.get("installed") is False:
        parts.append(
            '<p class="unavailable">Ollama 可达（已装 %s 个模型），但没有 %s：调用会直接 500</p>'
            % (_fmt_num(ai.get("installed_models")), _e(AI_MODEL))
        )
    elif not ai.get("resident"):
        parts.append(_kv("驻留", "否（未驻留，跳过生成以免冷加载）"))
        parts.append(_kv("已装模型数", _fmt_num(ai.get("installed_models"))))
        parts.append('<p class="note">本轮没有生成样本，ctx 与吞吐不可判定，不判绿。</p>')
    else:
        toks = ai.get("toks_per_second")
        toks_text = "未测到" if toks is None else "%.1f tok/s" % toks
        parts.extend(
            [
                _kv("驻留", "是"),
                _kv("ctx / 期望", "%s / %d" % (_fmt_num(ai.get("ctx")), AI_NUM_CTX)),
                _kv(
                    "content / thinking 字数",
                    "%s / %s"
                    % (
                        _fmt_num(ai.get("content_len")),
                        _fmt_num(ai.get("thinking_len")),
                    ),
                ),
                _kv("答对「%s」" % AI_EXPECTED_ANSWER, "是" if ai.get("answer_matches") else "否"),
                _kv("吞吐", "%s（eval_count=%s）" % (toks_text, _fmt_num(ai.get("eval_count")))),
                _kv(
                    "耗时 / 预算",
                    "%ss / %ss" % (ai.get("elapsed_seconds"), ai.get("budget_seconds")),
                ),
            ]
        )
        if ai.get("content_excerpt"):
            parts.append('<p class="note">返回：%s</p>' % _e(ai["content_excerpt"]))
        if ai.get("chat_error"):
            parts.append(
                '<p class="unavailable">调用失败：%s</p>' % _e(ai["chat_error"])
            )
    parts.append("</section>")

    parts.append('<section class="block full"><h2>判定明细</h2><ul class="checks">')
    for check in card["checks"]:
        parts.append(
            f'<li><span class="dot {check["level"]}"></span><div>'
            f'<code>{_e(check["id"])}</code>{_e(check["detail"])}</div></li>'
        )
    parts.append("</ul></section>")
    parts.append("</div>")

    raw_json = json.dumps(card, ensure_ascii=False, indent=2)
    parts.append(
        f'<details><summary>原始 JSON</summary><pre>{html.escape(raw_json)}</pre></details>'
    )
    parts.append(
        f'<footer>只读生成 · schema {_e(card["schema"])} · '
        "不修改仓库 / 计划任务 / 备份</footer>"
    )
    parts.extend(["</article>", "</main>", "</body>", "</html>"])
    return "\n".join(parts)


def _atomic_write_text(path: Path, text: str) -> None:
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)


def _write_outputs(out_dir: Path, card: dict[str, Any]) -> str | None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not out_dir.is_dir():
            return f"输出路径不是目录: {out_dir}"
        json_text = json.dumps(card, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(out_dir / "health_card.json", json_text)
        _atomic_write_text(out_dir / "health_card.html", _render_html(card))
        return None
    except OSError as exc:
        return f"无法写入输出目录 {out_dir}: {_short_exc(exc)}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_now(text: str) -> dt.datetime:
    parsed = _parse_iso(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"无法解析时间: {text!r}")
    return parsed


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "gen_health_card.py", exc)


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="治理健康卡（只读）：任务审计 + 备份 + 仓库卫生 → JSON/HTML",
        epilog=(
            "退出码：0=绿/黄，1=红，2=脚本自身错误。"
            f"阈值：备份 >{BACKUP_YELLOW_HOURS:g}h 黄 / >{BACKUP_RED_HOURS:g}h 红；"
            f"备份落后 HEAD >0 黄 / 落后且 >{CATCHUP_RED_HOURS:g}h 红；"
            f"审计 >{AUDIT_YELLOW_HOURS:g}h 黄 / >{AUDIT_RED_HOURS:g}h 红。"
            f"整理层落后 >{CURATED_YELLOW_LAG_DAYS:g} 天黄 / >{CURATED_RED_LAG_DAYS:g} 天红。"
        ),
    )
    parser.add_argument("--repo", metavar="PATH", help="仓库根（默认脚本所在仓库）")
    parser.add_argument(
        "--vault-root", metavar="PATH", help="备份仓根（默认 <repo 的父目录>/_vault）"
    )
    parser.add_argument(
        "--task-audit-root",
        metavar="PATH",
        help="task_exit_audit 输出目录（默认 <repo>/portal/data/task_exit_audit）",
    )
    parser.add_argument(
        "--curated-root",
        metavar="PATH",
        help="整理层目录（默认 <repo>/quant_backtest/data/tushare_curated）",
    )
    parser.add_argument(
        "--raw-root",
        metavar="PATH",
        help="原始层目录（默认 <repo>/quant_backtest/data/tushare）",
    )
    parser.add_argument(
        "--out", metavar="DIR", help="可选：写入 health_card.json + health_card.html"
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        type=_parse_now,
        help="用指定时间计算新鲜度（默认当前时间，测试用）",
    )
    args = parser.parse_args(argv)

    script_repo = Path(__file__).resolve().parents[1]
    repo_root = Path(args.repo).expanduser().resolve() if args.repo else script_repo
    vault_root = (
        Path(args.vault_root).expanduser().resolve()
        if args.vault_root
        else repo_root.parent / "_vault"
    )
    audit_root = (
        Path(args.task_audit_root).expanduser().resolve()
        if args.task_audit_root
        else repo_root / AUDIT_DATA_REL
    )
    now = args.now or dt.datetime.now().astimezone()
    curated_root = (
        Path(args.curated_root).expanduser().resolve() if args.curated_root else None
    )
    raw_root = Path(args.raw_root).expanduser().resolve() if args.raw_root else None

    try:
        card = build_card(repo_root, vault_root, audit_root, now, curated_root, raw_root)
    except Exception as exc:  # 兜底：脚本自身错误不给堆栈吓人
        print(f"[内部错误] {_short_exc(exc)}", file=sys.stderr)
        return 2

    if args.out:
        try:
            out_dir = Path(args.out).expanduser().resolve()
            error = _write_outputs(out_dir, card)
        except Exception as exc:  # 含路径解析失败；不给堆栈
            print(
                f"[错误] 无法写入输出目录 {args.out}: {_short_exc(exc)}",
                file=sys.stderr,
            )
            return 2
        if error:
            print(f"[错误] {error}", file=sys.stderr)
            return 2
        print(
            f"[ok] 已写入 {out_dir / 'health_card.json'} / {out_dir / 'health_card.html'}",
            file=sys.stderr,
        )

    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0 if card["status"] != "red" else 1


if __name__ == "__main__":
    sys.exit(main())
