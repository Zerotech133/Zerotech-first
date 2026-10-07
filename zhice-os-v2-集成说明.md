# zhice-os-v2 集成说明

> 部署日期：2026-09-21 ｜ 源码：`E:\zhice-os-v2-windows-20260920` ｜ 本项目：`E:\老田的AI助手`

## 一、系统架构

| 组件 | 说明 | 地址 |
|------|------|------|
| 数据库 | Docker 容器 `zhice-db`（postgres:16），卷 `zhice-os-v2-pgdata` 持久化，restart unless-stopped | localhost:5432 |
| 后端 API | FastAPI（uvicorn） | http://localhost:8102（`/api/v1/health` 200） |
| 调度器 | APScheduler，四市场行情采集（CN/US/HK/CRYPTO） | 后台进程 |
| 前端 | React + Vite dev server | **http://localhost:5175**（必须用 localhost，不能 127.0.0.1） |
| 门户入口 | 已接入本地门户 `ai-portal`（http://localhost:8080）第四张卡片「智策 OS v2」，健康探测走 `/probe/zhice` → API health | http://localhost:8080 |

**登录凭据**：`admin` / `123456`（生产 dump 遗留弱密码，ADMIN 角色；如需暴露到局域网请先改密）。

数据库连接串：`postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2`
当前数据量约 3.6GB（业务数据 + 全量历史行情）。

## 二、启动 / 停止

统一入口：`E:\老田的AI助手\zhice.ps1`

```powershell
# 启动（db → 等就绪 → api/scheduler/前端）
powershell -ExecutionPolicy Bypass -File E:\老田的AI助手\zhice.ps1 start

# 停止
powershell -ExecutionPolicy Bypass -File E:\老田的AI助手\zhice.ps1 stop

# 查看状态（进程 + api-health）
powershell -ExecutionPolicy Bypass -File E:\老田的AI助手\zhice.ps1 status

# 重启
powershell -ExecutionPolicy Bypass -File E:\老田的AI助手\zhice.ps1 restart
```

日志位置（排查问题先看这三个）：
- API：`%TEMP%\zhice-api.log`
- 调度器：`%TEMP%\zhice-scheduler.log`
- 前端：`%TEMP%\zhice-frontend.log`

## 三、环境要点

- Python venv：`E:\zhice-os-v2-windows-20260920\code\.venv`（Python 3.12.10）
- 源码以可编辑模式安装：`pip install -e code --no-deps`
- **czsc 必须钉死 0.10.12**：1.0.1 的 `corr.py` 缺 `single_linear`，会导致 `get_zs_seq` / 缠论中枢构建崩溃
- 凭据在 `E:\zhice-os-v2-windows-20260920\.env.local`（含真实密钥，勿提交勿外传）

## 四、Windows 适配（部署时处理的三个问题）

1. **`-e git+ssh://...` 源码包装不上**（源服务器 SSH 不通）
   → 改为本地源码 `pip install -e code --no-deps`

2. **freeze 清单含 macOS 专用依赖**：pyobjc × 6（`error: PyObjC requires macOS`）、uvloop（Windows 无轮子）
   → 安装时剔除；uvicorn 自动回退 asyncio，功能不受影响

3. **zhice.ps1 必须 UTF-8 带 BOM**：无 BOM 的中文注释会被 PowerShell 5.1 按 ANSI 解析导致语法错误
   → 已用带 BOM 编码写入，后续编辑脚本请保持

## 五、历史行情数据（已导入，行数 100% 命中基准）

| 表 | 行数 |
|----|------|
| v2_bar_minute | 8,887,430 |
| v2_bar_daily | 2,936,223 |
| 其余 7 张（symbol/交易日历等） | 与 `export_manifest.json` 基准一致 |

导入工具：`E:\zhice-os-v2-windows-20260920\scripts\import_market_history.py`（逐表 TRUNCATE → pg_restore --data-only → 行数核对）。
业务表恢复自 prod_data.dump：fill=1324 / position=669。

## 六、Jev 告警降噪网关（P1，2026-09-21 上线）

**评测结论**（30 题中文财经 × 3 遍，见 `jev-eval\results\`）：准确率 93.3%，noul/choice 100%、score 75%，无高置信答错 → 通过上线门槛。

**架构**（零侵入）：`jev-eval\jev_gate.py` 每 60s 轮询 v2_alert 的 OPEN 告警（2h 窗口）→ Jev 判紧急度（noul 二分）+ 类别（choice 四分）→ 紧急度 ≥0.8 推企业微信，其余抑制。CRITICAL 直推不过 Jev（兜底通道不依赖外部模型）。已处理告警记 `gate_seen.json` 防重推。

- 凭据：`jev-eval\jev-gate.env.ps1`（zhice.ps1 start 自动加载；ALERT_WEBHOOK_URL 未填时只判不推）
- 日志：`%TEMP%\zhice-jev-gate.log`
- 手动：`python jev-eval\jev_gate.py --dry`（查链路）/ `--once`（单轮实跑）
- Jev 挂了 → 当轮跳过不误推；评测脚本：`python jev-eval\jev_eval.py --repeats 3`

## 七、Jev 信号复核门（阶段 1 · 影子模式，2026-09-21）

**架构**（学自开源 buberlo/jev-trader："Jev 只拥有判断，不拥有系统"）：
读 `v2_candidate_pool/v2_candidate_asset` 候选 + `v2_concept_membership` 概念重叠 + `v2_position` 持仓 → 组装 <400 token 快照 → 一次 Jev 调用五原子判断（news_risk / regime_fit / concept_overlap 用 noul，signal_quality / crowding 用 choice——只用评测 100% 的题型）→ 影子决策落 `jev-eval\review_data\triples.jsonl`。

**影子模式铁律**：只标注不拦截，不碰 zhice 任何表；Jev 失败标 unreviewed 信号照发；公告源 Tushare anns_d 探测失败自动降级"无数据"模式。

**校准闭环**：`--backfill` 用 v2_bar_daily 回填 5 日真实收益 → `--report` 输出分组收益差。

**⚠️ 2026-09-22 回放结论（replay-20260922-030434.jsonl，174 池 1488 样本）——决策规则已反转**：
- 旧规则（quality 高=好、regime 弱=拦）PASS 组跑输基线 -0.57pp：quality 判定与 5 日收益**负相关**（high 组 -0.88% vs low 组 **+2.92%**），regime 同样反向
- **新规则 v2**：quality=low → **FOCUS（临界信号加分）**；hot/news 高置信 → REJECT（样本内有效）；regime 不做门；overlap 降 WATCH 标注
- 重算：FOCUS +2.30pp / PASS -0.51pp。**单段行情拟合风险**：8 月中-9 月中一段行情内均值回归占优，low 组优势需 9/16 起前向数据复现验证——复现前不进入任何执行链路
- 回放工具：`python jev-eval\replay_review.py`（防未来函数：公告/持仓/regime 均以 pool_date 为时点截断）

**阶段 2（半自动）判据已更新**：FOCUS 组前向收益复现 >+1pp 且样本 ≥30（影子期新数据为准，回放数据不作为放行依据）。

- 启动：zhice.ps1 start 自动常驻（10 分钟轮询，**每日 20:05 后自动回填到期收益**——无需手动记得跑 --backfill）；手动 `python jev-eval\signal_review_gate.py --once / --backfill / --report`
- 数据：`jev-eval\review_data\`（triples.jsonl 三元组流 / seen.json / regime.json 每日缓存）
- 影子期建议 2-4 周，期间每周末跑一次 `--backfill && --report`

## 八、QuantDinger 部署与 Jev⇄QD 桥（2026-09-22）

**QuantDinger（★11.7k 开源量化平台）**：`E:\老田的AI助手\quantdinger\`，Docker Compose 预构建镜像，10 容器全部 healthy。前端 http://localhost:8888（admin / Qd#LaoTian2026），API 127.0.0.1:15000，DB 15432 / Redis 16379（避开 zhice-db:5432 与 NewQuant:6380）。注意：**QD 无 A 股市场**（仅 US/HK/Crypto/Forex/Futures/MOEX）。

**桥 `jev-eval\jev_qd_bridge.py`**（每小时一轮，zhice.ps1 start 常驻）：
- **正向 sync**：triples.jsonl 中 decision=FOCUS 的候选 → QD 自选股（HK→HKStock / US→USStock / CRYPTO→Crypto；CN 无对应市场跳过，闭环仍在 zhice）。幂等（qd_pushed.json 记已推集合）。首推 2026-09-22：30 只（HK 6 + US 19 + Crypto 5）。
- **反向 review**：拉 QD 自选股 → 逐票 Jev 复核（keep noul + crowding choice）→ 落 qd_review.jsonl（影子，不删 QD 数据）。24h 内不重复复核。keep_p<0.35 或 hot≥0.7 → WARN 标注。
- 环境变量可覆盖：QD_BASE_URL / QD_USER / QD_PASSWORD；JEV_API_KEY 复用 jev-gate.env.ps1。
- **2026-09-22 快照增强**：反向复核已接入 zhice-db `v2_bar_daily` 近 6 日行情（QD 的 price 接口仅当日 ticker 且 Crypto 数据源未配，故走 zhice 库）。效果：crowding 从全 normal(0.9+) 真实分化——XRP/AMD/DOGE 三只近 5 日大涨票命中 hot WARN（AMD 0.98 / DOGE 0.76 / XRP 0.70），keep_p 分布 0.43-0.65。`--review --force` 可跳过 24h 去重重跑。

**门户第五张卡片**（🎯 QuantDinger）：portal/index.html + /probe/quantdinger。⚠️ 接入时发现门户 5 个探测因 Docker DNS 把 host.docker.internal 解析为 IPv6 早已全部失效，已改写死 192.168.65.254 修复（见 portal/nginx.conf 头注释）。

## 九、待办

- [ ] 9/28 20:35 计划任务 `JevFOCUS首期报告` 自动弹窗首期报告（已空跑验证：PS5.1 BOM + Join-Path 兼容修复；20:05 常驻门自动回填 → 20:35 报告弹窗，链路见 jev-eval\remind_report.ps1）

- [ ] 企业微信 alertmanager webhook key（拿到后填入 jev-gate.env.ps1 即启用推送）
- [ ] Jev key 已出现在命令行历史，建议去 console.typesafe.ai/keys 轮换
