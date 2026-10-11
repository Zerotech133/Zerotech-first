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
- **portal_api do_GET 传给页方法的 qs 是原始字符串**（10-11 修 087 遗留）：页内必须 `parse_qs(qs)` 再 .get（全库惯例）；直接 `qs.get(...)` 在带 query 的 HTTP 请求上 AttributeError 打死连接（浏览器侧 502/断连），而直调冒烟传 dict 测不出来——冒烟必须走 HTTP 层

- **K-STOCK-116 审计修复**：原型目录 `time_split` 只查 `/` 会放过伪造/零长度/倒序区间；平台注册还需读取正式端口对账表；RAG 评测必须包含 `as_of` 用例并把缺 `source_date` 判为阻断。E 盘 `.git` ACL 会阻止 `devtask.py new` 创建分支锁，需先申请 Git 元数据写权限或使用受管 worktree。
- **时序证据必须按 ISO 日期解析**：不能用字符串大小比较；`as_of/source_date` 格式非法应返回 `invalid_evidence` 并阻断评测。
- **devtask.py 传中文+圆括号原因必须整体加引号**（10-11 K-STOCK-116 drop 实锤）：PS 把裸 `空转待回收(bf4ac94…)` 当子表达式执行→命令未跑但报错埋在 CLIXML 里，exit 0 假绿——多命令 `;` 串跑时单条解析失败不挡后续，须回查 tasks.json 才知 drop 没落
- **任务单归档先辨性质再动手**（10-11 双 116 处置判例）：`detached@<sha>` 指针≠成果，先 `git merge-base --is-ancestor`+worktree status 双验——成果已代收（116-history）循 114b 判例补登 done；空转零成果（116-time-split）循主控盘点 drop，勿一刀切
- **清 worktree 前必采样写入活动**（10-11 116-history 实锤）："已合并"的 worktree 可能仍被数据管线当运行时工作区（staging 库/进度文档持续写入）；查 `git status`+目录 mtime+宿主进程三件套再动手，否则等于杀生产线。另：多 Agent 会并发从主仓根跑全量 `pytest -q` 互踩（13:02 三进程烧 3 核 2 小时），全量套件应限定路径或加超时
