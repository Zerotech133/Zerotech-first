# 零界（老田的AI助手）项目长期记忆（精编版 2026-11-05）

> 历史判例全文在 docs/audit/ 与 docs/plans/（旧全文备份：本目录 MEMORY.md.bak-20261105）。本文件只留仍高频复用的教训与活事实。

## 活事实/基线
- 主仓 `C:\项目\老田的AI助手`（junction `C:\aios`）；人类唯一入口 8734（nginx→8091，页面 404 先直测 8091 分流）；产品定义唯一源 `docs/plans/2026-10-24_PRODUCT_BASELINE_v1.md`；战略/排期唯一源=ROADMAP v4
- RiskGuard 四规则定值：R2 2.5 / R4 0.40 / R3 3 / R1 LB125-P10+三守卫，calibrated_keys 11 键，enforce=True；每日 19:10 影子批攒 OOS，周六自动 skip R1/R2
- portal 后验=九审可成交口径（D+2 开盘买入），引用数字看 pick_verdicts.json `summary.caliber.id`
- tushare 生产仓六表（daily/adj_factor/daily_basic/margin_detail/index_daily/moneyflow）已 qualified+全历史零缺口；增量=TushareIncrementGuard 每日 20:30 自动 held/发布（复用 `--pattern "tail_fill/*"`，禁 --allow-overwrite）；例行检查看 work/increment_guard_*.json 最新 status（OK/NO_GAP/HELD_ONLY/DEGRADED）
- JEV 凭据链：.enc 已重加密为 LocalMachine 作用域（S4U 只能解 LM）；权威源树 c:\项目 与运行树 C:\aios 两份同步；portproxy/防火墙拓扑变更后必须 grep 仓内 http://IP:PORT 常量并实测
- 本机网络：github.com 直连不通，git 已配 `http.https://github.com/.proxy=127.0.0.1:7890`（KuaiAnCore）。**7890 停机 ⇒ 一切走 local 出口的数据源全 unreachable**（2026-11-05 巡检实证：binance/fred/gdelt/okx 全 ProxyError，根因=7890 无监听，非源故障）
- 本地推理：iGPU 只适合 ≤10B（qwen3.5:9b 甜点，35b 勿本地跑）；Ollama 启动入口五处同改（用户级 env/启动文件夹 bat/程序目录孤儿 bat/ollama_watchdog TUNED_ENV/start_tuned_ollama.py），watchdog 的 TUNED_ENV 优先级高于用户级 env
- CF geo-block 型 451=走海外出口（7890）即解；云请求 timeout ≥25s

## 高频坑位（跨日复用）
- **含中文 .ps1 必须 UTF-8 带 BOM**；多层 BOM=解析即死零日志（先验 BOM 再验权限）；PS 无 bash heredoc（多行 commit 用 -F）；GBK 控制台下 Python 打中文/✓ 会 UnicodeEncodeError（入口脚本 stdout/stderr reconfigure utf-8）；跨语言生成代码用 raw 字符串+生成后逐字节检查（\a\t 转义坑）
- **S4U Session0 进程**：CIM/CommandLine 不可信（全 null），判活用 Get-NetTCPConnection 按端口；杀要 UAC taskkill；JEV 四件套换血可落 `C:\Temp\jevstack_force_restart` marker + 触发 JevStack；8097 若是普通权限实例可直接 Stop-Process；杀+拉必须原子（SO_REUSEADDR 双绑陷阱）
- **Agent 拉长驻服务唯一正解=计划任务（Start-ScheduledTask）**；交互终端 spawn 的分离进程静默死；daemon 禁用工具 run_in_background（会话中断连坐杀整棵树）；启动后校验实例数=1
- **计划任务**：幽灵 Running 态 0x80070490→Stop 再 Start；Repetition Duration P1D 会过期（空/CLI 无 Duration=无限期，判停摆读 LastRunTime/NextRunTime）；rc=1 先手跑区分解析错/运行时错；一次性任务必查 StartWhenAvailable/LogonType/电池三设置；提权注册走 UAC，免提权换代用「接力任务」；停常驻服务前先查 watchdog 保活（DockerWatchdog 曾无痕复活被禁任务，注册表 system-task-deploy 宿主在终端零痕迹，处置先改 source/禁全部宿主）
- **lark-cli/飞书**：同 app 多消费者按事件类型截流（`lark-cli event status` 最快诊断；bus.log 是到达唯一真相源）；停 consume 优雅退出勿 kill -9（占全局长连接数小时）；PATCH messages 的 content 传 JSON 字符串；jev 卡片降级 v1（schema2.0 behaviors 新 app 零投递，平台侧缺口）；签字卡必须 critical=True；卡片回调验收要打失败路径+发卡分支（_esc/_handle_card 判例：闭包平移丢名、except 吞 NameError）
- **git/devtask**：merge 前必先 commit（0 提交 merge 空转+--force 清场丢内容，已防呆）；主仓同路径未跟踪副本挡 merge（先写 worktree 提交再合门）；DU 冲突静默换工作区活文档（处理前备份）；共享 stash pop 前认领条目；tasks.json 是共享账本（new 后核登记再 merge，补登格式抄邻近 done 单）；legion-* 单权威状态在平台服务，直接改文件被回滚；e2e 驱动器（K-ORG-068）worktree 自治不干预；devtask merge 前 PYTHONUTF8=1
- **数据/API 纪律**：过滤参数可能被静默忽略→必做返回行越界自证（不能只看 HTTP 200）；tushare 单次 10000 行截断（items>=10000 才翻页）；ts_code 脏行判据=含非 ASCII（勿用后缀白名单，指数 CSI/CNI 等合法后缀会被误杀）；trade_cal 先 drop_duplicates；判数据新鲜看文件名日期戳、判写者活跃看 mtime（两者不同语义）；上游停更常伪装成「任务成功+文件新」→ 健康卡读日期列（data_through 驱动）；每张表的新鲜度要看产出路径（curated 漏表判例）；隔离区/回收裁决先过 robocopy /L 双向核对门（Newer>0 禁删）
- **外部资讯 API 回填**先跑 100-200 页深度探针；分页停滞检测（连续 N 页最早时间不前移即停）；资讯历史只能靠长期日采，补不了历史
- **修复写盘≠生效**：改常驻服务必须杀旧进程换血（验证=新 PID born>文件 mtime+日志新特征行）；launcher rc=0≠server ready（留足启动窗）；判停摆先 Get-NetTCPConnection 再下结论（单次 curl 超时≠进程死）；大版本迁移先 grep upstream 是否已吸收本地 patch，吸收即免重放
- **长跑任务**：锁要有租约让位（LEASE_S/YIELD_S/tick，否则排队批饿死）；失败处理区分瞬时故障与真坏（线性退避）；「无推进就退出」加 stall 闸；聚合型健康端点 timeout≥3×实测 P95；数据根结构操作前读 `C:\Temp\kg_sync_task.log` 尾部、完成后补记
- **审计/统计口径**：时序证据按 ISO 日期解析（禁字符串比较）；低频信号×多日持有 t 值虚高→非重叠抽样/block bootstrap（tools/research/stat_utils.py）；「有函数有测试」≠接线了（死代码型回归加源码断言）；报告/证据文件必须时间戳化或 append-only；补齐类脚本重跑必须实测零缺口分支（空集合 guard）
- **缓存必须绑仓库根**（跨 worktree 假红灯）；并行会话写同一文档产生重复章节（提交前 grep "^## " 查唯一性）；平台守卫对 master 状态跑，被拍板豁免的基线漂移要外科手术修
- **危险操作红线**：重启/关机/注销、改系统级配置（代理/防火墙/服务/计划任务）、动生产机 10.205.0.118、上线发布——必须老田五要素批准（命令全文+影响面+不可逆点+替代方案+回滚；沉默≠同意），判例 K-STOCK-413、规范 K-STOCK-416
- Windows 删除一律进回收站（SHFileOperationW），禁 rm/del；长跑命令输出落文件；替换补丁前断言锚点 count==1

## 近期要点（10-25 后）
- 互斥锁租约、night_batch 线性退避已落地（c5c8716d/69224f9a）；jev_night 06:35 后不开新轮；毒丸处理=JEVTRUNC num_predict 220 + JEVSTOP stall 闸；夜跑预热（9B warmup+keep_alive）每晚保留
- PG-Robot 终局：事件总线冲突（cnt=1 是自己被 kill -9 的实例），已撤凭据保持安静；出路 A/B/C 待老田定
- 计划任务失败审计口径：0x41001/0x41306 是 running/terminated 非失败桶
- 学习 pipeline 自动任务必须含后置治理（去重+排除+effective），否则每日重建覆盖人工治理；共享报告文件名带阶段/项目作用域，消费者显式匹配勿赌 mtime（2026-11-04）
