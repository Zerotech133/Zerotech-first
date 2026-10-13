# K-STOCK 项目长期记忆（E:\老田的AI助手）

## 节点链与基线（2026-10-10 下午固化）
- **S3 裁决复算基线 = KGStockAnalysis `72005b4`**（069 三批收编后工作区净）；裁决材料引用此 HEAD
- 节点链：今晚 19:10 OOS 首记 → 10-13 周一五判据（验收器 `k_stock/paper/acceptance_check.py --date 20261013`，清单 docs/ops/2026-10-13_周一撮合验收清单.md 含 §3.3 新增预检）→ 10-14 S3 裁决 → 10-15 双线合流 → ~10-20 前向复算满窗
- RiskGuard 四规则定值：R2 2.5 / R4 **0.40** / R3 3 / R1 LB125-P10+三守卫，calibrated_keys 11 键，enforce=True；每日 19:10 影子批攒 OOS，周六自动 skip R1/R2（哈希判据）
- portal 后验已切**九审可成交口径**（D+2 开盘买入）：引用数字看 pick_verdicts.json `summary.caliber.id`（exec-d2-open-20261010），与旧 close-d0 不可比

## 待老田拍板/输入（截至 10-10 晚更新）
- ~~J-1 jev 451 治本~~ **✅销案（084）**：根因=CF 对大陆出口 geo-block；typesafe 走本机 7890（SG）恢复，news_ai 已接 proxies（TS_PROXY env 可覆盖）+ 本地 9b 回落二级保险（083，盲测 86.7%）
- L6 登录开闸（等 Bearer 凭据）；~~菜鸟 dump 停 10-08~~ **✅解除（10-10 晚核）**：cainiao_inbox 四表 10-10 19:04 刷新，数据实到 10-09（周末无新数据正常），周一预检项已提前验证

## 今晚新增基建（10-10 晚五提交，节点链自动化全覆盖）
- **V6-5 记忆桥已转正**（K276）：cowbridge 生产写上线（index.db messages + daily 层），cowbridge.json shadow_mode=false；回滚=恢复 shadow_mode=true
- **解禁日 10-15 17:30 automation**（ff9fc3c2）：探针 conf_calibration.py 先跑→按 MONOTONIC 与否自动选 B8 解法（conf' 映射 vs 方案甲锚定）→B9/B10/结构化契约（085-P0-A）一次施工+三验收门
- **批 C 10-16 09:30 automation**（48c66a31）：A0 上线+研发部+市场部 M1 注册（施工图=A4 预演文档）
- **菜鸟 ai_team 四坑镜子**（085）：键名反转/双跑已规避；相似度单条件=B10 同病灶；自报置信无校准=086 探针已堵

## 本日新增坑位（复用价值高）
- **PS 5.1 dot-source 磁盘 .txt 静默不执行**（非 .ps1 扩展）：预演注册表部署脚本须"解码→.ps1 副本→dot-source"；N1Check/JEVBackfill 均为注册表部署链（HKLM part1+part2 base64）
- 沙箱拦外网 API：手动验证云侧调用须无沙箱跑，SIGTERM 零输出=沙箱杀
- regen 类脚本输出英文化：中文经 PS 管道捕获 GBK 错位乱码
- 告警检查只查 pending 区，`sent/` 归档（*.done）≠在告（071 实锤误报源）
- **CF geo-block 型 451**：全端点同码+报错在 key 校验之前=edge 层封锁，走海外出口即解（7890 实测）；云请求 timeout 须 ≥25s（代理首建连偶发 >15s，15s 会假回落）
- **automation「ACTIVE」≠「当日触发」**（09:10 cowbridge 漏跑实锤）：判据表挂"待 automation 补全"的行要核产物文件时间戳
- news_collector 跑 requests 用系统 py311（venv/default 无 requests）；工作区并行线施工时只 add 自己的文件（防 078 收编重演）
- KGStockAnalysis venv 已装 pytest（新测试 pytest 风格，unittest 收集为 0）
- **临时产物声明「保留可复用」必须当场入 git/固定数据目录**（10-11 P5c 实锤）：30 份模拟归档+生成脚本口头承诺保留，复查双失→演练不可复现，结论全靠已入档的报告 §6 才没丢；K-STOCK-113 收口时顺手修的 `if not k` 真值歧义也是演练暴露的——演练价值在跑，产物在档
- **一次性提醒/里程碑任务注册后必查三设置**（10-11 K-STOCK-120 审查实锤）：StartWhenAvailable（错过恢复）、LogonType（InteractiveToken=未登录不跑）、DisallowStartIfOnBatteries——一次性任务错过触发=永久静默丢失且不报错；`Get-ScheduledTask`→改 Settings→`Set-ScheduledTask` 热修不伤触发器
- **页面 404 排查先分层：8734/8080 都过 nginx，后端是 8091**（10-11 K-STOCK-122 实锤）：nginx 是白名单逐条 location 无兜底反代，漏注册的路径=空 body 404，极易误判为后端 bug；入口 404 时必直测 `http://127.0.0.1:8091<path>` 分流。新页面上线=portal_api 路由 + nginx location 两条都要落
- **「任务全绿但产物停更」=查写入链调度覆盖面，别信同名前缀**（10-11 K-STOCK-131 实锤）：event_shadow 停更两天，KgExtractShadow 任务天天 rc=0——但它写的是另一个文件 kg_extract_shadow.jsonl（K219 研报线）；真正写入者 k266_m3a_events.py 是 K266 收线时的人工工具，**从头就没有调度**。教训：①体检对账要精确到「文件←脚本←任务」三级链，任务名前缀相似≠管线同源；②任务收线时凡有「影子文件还在被页面读」的，必须给写入端配常驻调度或显式标记 frozen；③全量覆写式幂等脚本（如 m3a_events 重建 event_shadow）最适合日调度补缺，影子纪律靠不加 --commit 保证；④**入图过滤建议（待数据线）**：m3a_events commit 端应加 `subj_sec != obj_sec` 自环闸与弱「其他」跨边闸——10-11 人审 31 条候选中 17 自环+3 弱边占 65%，且 10-09 旧 commit 已把 12 条自环漏进图（已于 10-11 深夜人审后清除，删前备份 C:\Temp\k131_doc_event_backup_20261011.jsonl，图内现存 12 条全部跨边实边，其中合康新能-[其他]->中信证券 1 条弱边为旧commit遗留待裁决）；人审口径先例=「只入有明确关系语义的实边」（增持/减持/合作/中标/裁决等）
- **portal_api do_GET 传给页方法的 qs 是原始字符串**（10-11 修 087 遗留）：页内必须 `parse_qs(qs)` 再 .get（全库惯例）；直接 `qs.get(...)` 在带 query 的 HTTP 请求上 AttributeError 打死连接（浏览器侧 502/断连），而直调冒烟传 dict 测不出来——冒烟必须走 HTTP 层

- **K-STOCK-116 审计修复**：原型目录 `time_split` 只查 `/` 会放过伪造/零长度/倒序区间；平台注册还需读取正式端口对账表；RAG 评测必须包含 `as_of` 用例并把缺 `source_date` 判为阻断。E 盘 `.git` ACL 会阻止 `devtask.py new` 创建分支锁，需先申请 Git 元数据写权限或使用受管 worktree。
- **时序证据必须按 ISO 日期解析**：不能用字符串大小比较；`as_of/source_date` 格式非法应返回 `invalid_evidence` 并阻断评测。
- **devtask.py 传中文+圆括号原因必须整体加引号**（10-11 K-STOCK-116 drop 实锤）：PS 把裸 `空转待回收(bf4ac94…)` 当子表达式执行→命令未跑但报错埋在 CLIXML 里，exit 0 假绿——多命令 `;` 串跑时单条解析失败不挡后续，须回查 tasks.json 才知 drop 没落
- **任务单归档先辨性质再动手**（10-11 双 116 处置判例）：`detached@<sha>` 指针≠成果，先 `git merge-base --is-ancestor`+worktree status 双验——成果已代收（116-history）循 114b 判例补登 done；空转零成果（116-time-split）循主控盘点 drop，勿一刀切
- **清 worktree 前必采样写入活动**（10-11 116-history 实锤）："已合并"的 worktree 可能仍被数据管线当运行时工作区（staging 库/进度文档持续写入）；查 `git status`+目录 mtime+宿主进程三件套再动手，否则等于杀生产线。另：多 Agent 会并发从主仓根跑全量 `pytest -q` 互踩（13:02 三进程烧 3 核 2 小时），全量套件应限定路径或加超时
- **Windows 评审与导航门**：`bash.exe` 在本机只是 WSL 启动器，调用 Superpowers 的 Bash review 脚本会被 `E_ACCESSDENIED` 阻断；改用 PowerShell 等价生成评审包。运行 `tools/unify_nav.py` 会先注入缺失页，再校验 `nav.js` 与 `portal_api.py`，若源有并行未提交改动必须一并运行 `tools/export_nav.py` 后重跑守卫，不能把门禁失败误判为当前功能缺陷。
- **隔离区/回收类裁决先过 /L 双向核对门**（10-11 k116 隔离区实锤）：表面「存证三样都在」仍核出 **1,968 件隔离区较活树新**（38.8MB：知识图谱538/quant_backtest506/video270/pylibs183…）未回捞，弃置=丢数据；核对口径=robocopy `/L /E` 四档（全量 / 排older / 排newer / 三排=纯孤本），**Newer>0 一律禁删**；孤本要验「源在不在」——本次 4 件孤本全是 `__pycache__` .pyc 且 .py 源在 E: 全在位=零价值；裁决块已入台账 `C:\Temp\k116_staging_salvage.log` 尾部，清单 `C:\Temp\k116_verdict_*.txt` 可复跑
- **robocopy 源目录缺失仍打印全零摘要**（10-11 k116 看门狗审计实锤）：rc=16 才是真失败信号，只解析摘要行=全零误判（看门狗首版即「假绿+自删任务」双杀）；无人值守判定脚本铁律=isdir 硬前置（唯一合法自删路径）+ rc>=16/解析失败只跳过本轮待重试、绝不判绿绝不自杀；另两档差值在并发回捞期间只会高估 Newer（偏红安全方向），属可接受竞态
- **台账/日志文件严禁 PS 与 Python 双端追加**（10-11 k116 台账实锤）：Add-Content（GBK/ANSI）与 Python utf-8 追加混写=编码杂交，读取端 UnicodeDecodeError；归一化流程=原始字节先备份（.rawbytes.bak，防重跑覆盖）→逐行 utf-8 优先 gbk 兜底解码→写回后三断言（严格 utf-8 全文可解码/字节级行数不变/关键词在）；校验器自身注意 `splitlines()` 与 `split(b'\n')` 对末换行文件计数差 1，行数断言必须同刀字节级切分
- **评审门在 merge 前对 master 状态跑**（10-11 K-STOCK-135 实锤）：Mixin 守卫被他线半成品（P4-批7）拍板豁免未同步基线堵红，所有人都过不了门；处置=按提交信息里的拍板声明做最小外科手术（baseline JSON 精确移除豁免名，不顺手 --baseline 全量重建吸收他线未收口项），独立小提交留痕后再重跑门
- **PS 无 bash heredoc**：`git commit -m "$(cat <<'EOF'…)` 在 PowerShell 直接 ParseError（`<<` 保留字符）；多行提交消息一律写临时 txt 用 `git commit -F`，用完即删
- **lark_oapi WS 是 asyncio 单循环，handler 内禁同步重活**（10-11 K-STOCK-137 实锤）：on_card 原同步执行 ctl:board 子进程 180s/er:set 120s/dim:现抓 66s，会卡死心跳断连——on_msg/on_card 一律「薄壳解析+Thread 执行体」双壳判例；另 card.action.trigger 的 operator.open_id 是平铺属性（非嵌套），`_card_sender_id` 取值合法；事件可重投→`ctl:sub_toggle` 类翻转动作非幂等（暂以 control_ops.jsonl 审计留痕对账，未做 dedup）
- **卡片回路验收必须打失败路径**（10-11 K-STOCK-137 `_esc` 实锤）：未定义函数在 `except: log` 里被吞=用户点按钮零反馈且日志只有 300 字尾栈；发卡按钮链路自测要专门喂「查询失败/库不可达」用例，不止 happy path
- **闭包内函数平移抽到模块级，必须盘点闭包捕获名**（10-11 K-STOCK-138 实锤）：K-STOCK-137 把 on_card 执行体抽为模块级 `_handle_card`，`send_card`（原由 main() 闭包捕获局部 import）静默丢失——所有发卡按钮分支 NameError 被吞，137 冒烟却 10/10 绿，因为只测了 send_text 回执、没走到 send_card 分支；平移后用 `hasattr(module, name)` 逐名核对闭包引用集，发卡冒烟的拦截点必须是 `feishu_card_v2.send_card` 真源头（函数内局部 import 每次 re-bind，patch 模块属性即生效）
- **卡内 URL 类按钮是唯一无法本地闭环验收的环节，须跨设备实弹**（10-11 K-STOCK-140 实锤）：`PORTAL_BASE=100.110.192.72:8080` 是 10-07 旧拓扑（nginx 全接口监听），052 收敛后 nginx 仅绑 127.0.0.1 → 手机点「门户详情页」打不开，本机 Test-NetConnection 同样 False 却被忽略三周；网络拓扑每次收敛（绑回环/portproxy 变更）必须 grep 一遍仓内 http://IP:PORT 常量并实测；对外暴露配方=portproxy `listenaddress=<Tailscale IP>` → 127.0.0.1:8080 + 防火墙规则限 `RemoteAddress=100.64.0.0/10`（CGNAT 段，不裸开）；另 `devtask.py merge` 必须在主仓根跑，worktree 内报「无此工单」
- **飞书同 app 多消费者事件黑洞**（10-12 K-STOCK-141 深夜连破实锤）：`~/.lark-cli/config.json` 的 app 凭证被四方共用（门户 bot / Trae lark 插件 / PG-Robot 计划任务 `p_g_robot.py` / jev-eval `lark_card_handler.py`），飞书对同 app 多条 ws 长连接**按事件类型分发**——谁挂 `lark-cli event consume <事件>` 谁就可能截流，表现为「im.message.message_read_v1 回执正常到达、receive_v1/card.action.trigger 全黑洞」的诡异不对称；诊断三板斧：①clash `GET /connections` 查 `frontier.zijieapi.com`（**lark-cli 事件流走系统代理，直连 443 扫描看不见**）②Win32_Process 按 cmdline 搜 `event consume`（父进程链揪出真凶）③杀进程后**必掐 clash 里的僵尸上游连接**（DELETE /connections/{id}，否则飞书侧订阅还挂着继续黑洞）。Trae CN 插件杀不死会自动重连（**10-12 晨更正：冤案，见下条**），治本=一应用一消费者（独立 app）。另：PG-Robot 计划任务被禁用待架构拍板（**10-12 晨终审：01:17 复活=DockerWatchdog v2.4 守护段自动 Enable+Start，非人非 Agent，见下条**）
- **lark_oapi 动态 import 类名先 dir() 校验**（10-12 K-STOCK-141 实锤）：`ReplyMessageBody` 在已装 SDK 中实为 `ReplyMessageRequestBody`，函数内局部 import 即 ImportError——入站刚修好时「发消息没反应」≠入站没通，先查回复路径栈；bot 的 stdout（feishu_bot_out.log）藏着 SDK 级 ERROR（processor not found/连接日志），feishu_bot.log 只有业务层——双日志必须一起看；冷启动 import 链 ~38s，「进程在≠bot 就绪」，以 feishu_bot.log 出现 `bot ws starting` 为准
- **飞书黑洞复发复盘：真凶认错+冻结被无声推翻**（10-12 晨终审实锤）：①「插件杀不死」冤案——p_g_robot.py 硬编码 LARK_ROOT 指向 Trae 插件目录挑 lark-cli.exe 用，杀不死的实为 PG-Robot 的 consume 子进程（脚本 5s 重启+任务 RestartOnFailure），Trae 插件凭证是独立 app `cli_a965abcba6fadbd3` 无冲突；②「已禁用」的 PG-Robot 01:17 被无痕重新启用——**10-12 晨终审破案：非人非 Agent，是 DockerWatchdog v2.4 的 PG-Robot 守护段（C:\Temp\docker_watchdog_last.ps1 §1d）发现 Disabled 即自动 Enable+Start**；01:36 的 1-chat-id 版本属往日旧日志（无日期日志误导）；**对平台级冻结决定，光改任务状态不够，必须先排查/停用保活该服务的 watchdog，冻结令要有可核对的落痕（工单/事件层）**；③lark-cli 1.0.94 占用守卫依赖 user token，token 失效即跳过（02:16:17 实录「precheck Invalid access token → proceeding」），守卫不可信；④诊断第四板斧=`lark-cli event status`：直接列 bus PID+全部 consumer+RECEIVED/DROPPED，比进程考古快得多；⑤门户 bot（lark_oapi）与总线（Task Scheduler 起的 lark-cli）都可能**直连**飞书 443（不经 clash），clash 连接表看不见不代表没连——TCP 归属要用 Get-NetTCPConnection 按 PID 核
- **禁用常驻服务前必须先查谁在保活它，且要查全宿主**（10-12 晨 DockerWatchdog 对抗实锤×2）：system-task-deploy 模式的守护（HKLM 注册表存 base64 源码+计划任务内存 dot-source 执行）在终端/进程层**零痕迹**，且存在**双宿主陷阱**——「DockerWatchdog」与「AI工作站引擎探活」两个计划任务共用同一注册表键 `AIStation\DockerWatchdog`：06:5x 禁了 DockerWatchdog 任务后，07:19:01 引擎探活跑同一份 v2.4 source 又把 PG-Robot 拉起；**处置前必须 `Get-ScheduledTask | 扫各任务 Action 里引用该注册表键的`找全宿主**。取证三件物证：`news_collector\outbox\dw_*.txt` 告警原文、`C:\Temp\dw_*_restart.txt` 节流时间戳、TaskScheduler 日志「执行窗口罩住事件时刻的巡检任务」比对。终局（10-12 07:32）：备份 `C:\Temp\dw_source_backup_v24_20261012.b64` 后摘掉 §1d 段（v2.5，9368→7935 字节，锚点断言+写回复核），双宿主每轮实时读注册表故同时治愈，DockerWatchdog/引擎探活恢复 Ready，PG-Robot 保持 Disabled；教训：改 source 即可治愈全部宿主，但**排查时禁单个宿主任务无效**。禁用守护自身也要想到「守护的守护」，处置顺序=先改/禁全部宿主再禁目标
- **本地 RAG smoke 的主页与索引都是特殊状态**（K-STOCK-160）：`/` 返回 `text/html`，只能按 HTTP 200/Content-Type 验证，不能套 `/api/tags` 的 JSON 解码；持久化索引首次运行可不存在，smoke 应把索引缺失作为可选状态，真正必检的是主页、Ollama `/api/tags` 和 `/api/rag` 的 `sources/done` 事件。
- **运行实例与磁盘代码错位=乱象之源**（10-12 10:36 portal_api exit=1 实锤）：合并风暴（10:04-10:46 五连发）期间旧 23k 行实例仍在跑，traceback 拿旧 code object 行号去读已重构的新文件（3734 行）→ 乱码栈+假 AttributeError（_chanlun_page 丢失假象）；watchdog 重启又撞上 mid-merge 半写状态 → NameError(threading/api_data) 启动崩。诊断正解：①先跑 \	ools/_mixin_integrity.py --check\ 看磁盘真相 ②对比进程启动时间 vs 最后提交时间，错位即干净重启对齐 HEAD（Start-ScheduledTask 拉起后三类页面 curl 复核）③别按 traceback 字面行号读现在的文件

- (2026-10-13 K-STOCK-304) 推送活性判定铁则：计划任务 rc=0 ≠ 推送到达（内容可为空/目标失败仍 exit 0），活性巡检必须以日志产出签名（SEND_OK/ALERT_PUSH_OK/mtime）为准；推送设施排查先搜 Get-ScheduledTask 全表再核三配置落点（news_config push_targets / receivers.json / 任务 Action），"用户没收到"≠"没在推"。另：lark-cli im +messages-search 的 --start/--end 必须 ISO8601 带时区（2026-10-12T00:00:00+08:00），裸日期串报 99992402；PowerShell 给 python -c 传多行代码禁用 @'...'@ here-string（引号被 shell 层剥掉致 SyntaxError），一律落临时 .py 文件执行。
