# Hermes 净化面板与 pulse/alerts 连接器（K-ORG-113 方案档）

> 2026-10-16 · 触发：老田拍板「中台提供各板块能力，用 Hermes 管理，完成自我净化和对外对接」。
> 性质：**方案+登记档**（K273 边界：Hermes 代码活磁盘不入主仓 git，本档为唯一版本留痕）。
> 上游判例：K-ORG-112（双表面边界与转调配方）——本档即其 §7 分批路线的「二批 pulse + 三批 alerts」+ 新增「净化面板」。

## 1. 定位一句话

**在 Hermes 8787 门面新增三连接器一页面：purify（净化总览·只读本地快照）、pulse（推送调度台→8097）、alerts（告警指挥台→8091 新 handler），全部循 legion.py 配方；执行面零改动，落点与飞书卡同源同账。**

## 2. 摸底事实（2026-10-16 实测，四路并行调研）

| 件 | 事实 |
|---|---|
| 8097 推送中台 | `portal/push_admin_server.py`（纯 stdlib，仅绑 127.0.0.1）：读=`GET /api/state`（heartbeat/tasks/subs/receivers/today/flow/sources/biz 全量）；写=`POST /api/act`（action 分发：set_task_time/run_task/test_sub/toggle_sub/set_sub_receivers/sync_receivers_from_news/add_receiver/remove_receiver/set_biz/regen_apikey）。回环免鉴权。**现有零程序化客户端**（pulse 将是第一个） |
| 8091 门户 API | `portal/portal_api.py`：**无 ctl:* HTTP 端点**。ctl:* 纯逻辑在 `portal/feishu_control.py`（handle_ctl/alert_state/_alert_ack/_alert_ignore/_alert_ack_all），唯一运行入口=飞书 bot WS（feishu_bot_ws.py:565）。写端点惯例=`_MUTATION_POSTS`（portal_api.py:164）+ 本机放行/局域网 X-Portal-Token（`_check_portal_token`:538 → `_is_local_req()` 即放行）+ 写频控 429 |
| 告警落点三件套 | ① `zhice_os_v2.v2_alert`（PG@5446，status OPEN/ACKED/RESOLVED，ack=`UPDATE ... SET status='ACKED', acked_at=now() WHERE alert_id=%s AND status='OPEN'` 幂等）② `jev-eval/gate_ignored.json`（ignore 追加 {alert_id,title,ts}，keep 2000，gate 侧 TTL 14 天按 title 抑制、CRITICAL 豁免）③ `portal/data/control_ops.jsonl`（操作留痕）。OPEN 队列口径=控制卡口径（无时间窗，LIMIT 6+count），**勿用** gate 的 120min 窗 |
| Hermes 配方 | `Hermes/hermes-webui/api/legion.py`（127 行模板：_call 转调+caller 注入+fail-visible 三层+via/latency_ms setdefault）。挂载=`api/routes.py` handle_get 尾部（legion 块 :1806-1813 后）+ handle_post 尾部（:2831-2834 后），懒 import；server.py/鉴权/`__init__` 零改动。8787 无密码（.cred_set=skipped） |
| 净化数据源六族 | ① N1 晨检：`jev-eval/n1_report_<day>.txt` + `alerts/*.flag` + 盲旗 `C:/Temp/n1_blind_*.flag` ② 死信：`jev-eval/push_dead_letter.jsonl`（final:true=永久丢失，无生产读方）③ backlog：`news_collector/news.db` 表 news 字段 jev 空=未判定 + `C:/jev_backfill_task.log` 末行 P2 ④ 备份链：`backups/<stamp>/backup.log` DONE 行 + `C:/Temp/offsite_sync.log` OK 行 ⑤ 健康：`ops/health_latest.json`（health_check.py 四层快照）⑥ watchdog 家族：`C:/Temp/keepalive.log` 等仓外日志 |
| 三断点（面板标红） | AES 密钥 `C:/Users/t/.backup_aes_key` 缺失（异地 .enc 不可解）；backup_daily_regen 新机 DISABLED（脚本丢失只能人工恢复）；死信 final:true 需人工重放 |

## 3. 设计（循 K-ORG-112 §3 三原则）

### 3.1 原则对照

- **门面只转调不复制逻辑**：pulse/alerts 纯 HTTP 转调；alerts 的业务逻辑复用 feishu_control.py（纯逻辑层，HTTP 壳在 portal_api.py）——8091 侧新增代码属执行面自身开 HTTP 面，不算门面复制。
- **双表面同源同落点**：Hermes 的 ack/ignore 与飞书卡按钮写同一 v2_alert + gate_ignored.json + control_ops.jsonl，operator 归因 `hermes-console`，禁止第二本账。
- **调度本体不进门面**：purify 只读现成快照文件（原则 3 的「Hermes 只读状态」），零写端点、零定时器；一切改时点动作转调 8097（其白名单/正则校验原样生效）。

### 3.2 purify 连接器（api/purify.py，纯只读）

`GET /api/purify/overview` 汇聚六族（全 stdlib，路径绝对定位，单条失败降级 null 不拖垮全局）：

| key | 口径 |
|---|---|
| n1 | 当日 n1_report 文件名/存在性 + 尾部摘要行 + alerts/*.flag 计数 + 盲旗计数（>0 红） |
| dead_letter | 总行数/今日新增/final:true 计数/最近 5 条 {ts,level,title,err 摘要}（final>0 红） |
| backlog | news.db 只读连接 count(jev 空) + jev_backfill_task.log 末 P2 行（left>150 红） |
| backup | 最新 backup.log 的 DONE 行 + offsite_sync.log 末 OK 行（当日无 DONE 红） |
| health | health_latest.json 原样透传 + 生成时间（stale>30min 黄） |
| breakpoints | aes_key_missing / regen_disabled / blind_flags / dead_letter_final 四布尔（任一真=面板顶部红条） |

### 3.3 pulse 连接器（api/pulse.py → 8097）

- `GET /api/pulse/state` → 转调 `8097 GET /api/state`
- `POST /api/pulse/act` → 转调 `8097 POST /api/act`（body setdefault caller=hermes-pulse；action 白名单透传，校验靠 8097 自身白名单）
- `_VIA="hermes-pulse"`，超时 10s，fail-visible 中文报错（同 legion）

### 3.4 alerts 链路（8091 新 handler + api/alerts.py）

**8091 侧**（portal_api.py，主仓代码进 git）：
- `GET /api/ctl/alerts` → `feishu_control.alert_state()`（OPEN 队列 + count，无窗口径）
- `POST /api/ctl/alert` body `{action: ack|ignore|ack_all, alert_id?, operator?}` → feishu_control 新增薄封装 `ctl_http()`（复用 _alert_ack/_alert_ignore/_alert_ack_all + 写 control_ops.jsonl，operator 默认 hermes-console）
- POST 登记 `_MUTATION_POSTS`（本机放行；Hermes 同机转调免 token，与 legion 配方一致）

**Hermes 侧**（api/alerts.py）：
- `GET /api/alerts/queue` → 8091 `/api/ctl/alerts`
- `POST /api/alerts/act` → 8091 `/api/ctl/alert`（caller=hermes-alerts）
- `_VIA="hermes-alerts"`

### 3.5 指挥台页面（static/console.html，零侵入主应用）

循 mission-control.html 独立页模式（_serve_static 直接供给，不动 index.html/panels.js/i18n 九语言）。三区：
1. **净化总览**：断点红条 + 六族卡片（/api/purify/overview，10s 轮询）
2. **推送调度**：任务时点表（可改）+ 立即执行 + 今日计量（/api/pulse/state，5s 轮询）
3. **告警队列**：OPEN 列表 + ack/ignore/ack_all 按钮（/api/alerts/queue，10s 轮询；操作走 /api/alerts/act）

## 4. 改动面清单

| 文件 | 动作 | 仓内 git |
|---|---|---|
| Hermes/hermes-webui/api/purify.py | 新建（约 180 行） | 否（K273） |
| Hermes/hermes-webui/api/pulse.py | 新建（约 90 行，抄 legion） | 否 |
| Hermes/hermes-webui/api/alerts.py | 新建（约 100 行，抄 legion） | 否 |
| Hermes/hermes-webui/api/routes.py | GET/POST 尾部各加 3 段懒 import 挂载 | 否 |
| Hermes/hermes-webui/static/console.html | 新建（自包含单页） | 否 |
| portal/feishu_control.py | 新增 ctl_http() 薄封装（约 40 行） | 是 |
| portal/portal_api.py | 新增 2 路由 + _MUTATION_POSTS 登记 | 是 |
| portal/push_admin_server.py | --bg 分离从文件句柄重定向改全 DEVNULL（K271 判例） | 是 |
| ops/launch_push_admin.py | 新建（8097 幂等守护拉起器：socket 探测+PATH 重建+DETACHED/DEVNULL，约 60 行） | 是 |
| 计划任务「AI工作站推送中台守护8097」 | 注册（每 5min，User=t/S4U/Limited/IgnoreNew/2min 时限，UAC 提权 Register-ScheduledTask；旧机看门狗未迁移的补位项） | 否（注册体） |

## 5. 验收标准（连接器交付必检项，K-ORG-112 §6 升格条款）

1. py_compile 全绿（4 个新/改 py + portal_api.py + feishu_control.py）
2. 重启 8787（杀进程 + start_hermes_bg.py 拉起）与 8091（杀进程，5min 看门狗或手动拉）——改码生效条件=重启
3. e2e 归因探针（curl 显式 UTF-8）：
   - `/api/purify/overview` 返回六族 + 断点布尔
   - `/api/pulse/state` 返回 8097 全量（via=hermes-pulse）
   - `/api/alerts/queue` 返回 OPEN 队列；`/api/alerts/act` ack 一条后 v2_alert status='ACKED' 且 control_ops.jsonl 现 operator=hermes-console 归因行
4. console.html 三区在浏览器实测
5. 判例回写 MEMORY.md

## 5a. 验收实录（2026-10-16 18:10 终态，全绿）

1. py_compile 6 文件全绿（purify/pulse/alerts/routes + portal_api/feishu_control）
2. e2e 探针：purify/overview 200 六族全出（backlog 1285 pending、死信 282/46 final、regen_disabled=True）；**pulse/state 200**（via=hermes-pulse，heartbeat=True，tasks=5，subs=5）；alerts/queue 200（total=447 OPEN）；alerts/act 幂等 ack 200（0 行=已确认）；console.html 200；control_ops.jsonl 归因行 operator=hermes-alerts（17:06/17:18/18:09 三条）
3. **8097 守护换血**：计划任务 18:05:53 首触拉起 pid 47768（S4U 环境），跨 156s 死亡窗口存活；18:10:24 二触幂等（pid 不变，探测即退）
4. **8787 守护链自愈实证**：pid 45892 意外死亡后 HermesWebUI-Guard 18:07:41 自动拉起 pid 38508（任务注册路径 C:\aios\Hermes\... 经 junction 指向本仓同一物理目录）
5. 判例已回写 MEMORY.md（2026-10-16 K-ORG-113 节）

## 6. 判例链

K-ORG-112（总纲/配方/分批）、K271（幂等守护/重启生效）、K273（Hermes 不入库边界）、K-ORG-109（caller 归因）、K-STOCK-395（飞书侧姊妹边界）；8097 API 面=push_admin_server.py L323-531；8091 鉴权=portal_api.py L538 本机放行。
