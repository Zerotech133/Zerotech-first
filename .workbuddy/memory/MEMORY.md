# K-STOCK 项目长期记忆（E:\老田的AI助手）

## 节点链与基线（2026-10-10 下午固化）
- **S3 裁决复算基线 = KGStockAnalysis `72005b4`**（069 三批收编后工作区净）；裁决材料引用此 HEAD
- 节点链：今晚 19:10 OOS 首记 → 10-13 周一五判据（验收器 `k_stock/paper/acceptance_check.py --date 20261013`，清单 docs/ops/2026-10-13_周一撮合验收清单.md 含 §3.3 新增预检）→ 10-14 S3 裁决 → 10-15 双线合流 → ~10-20 前向复算满窗
- RiskGuard 四规则定值：R2 2.5 / R4 **0.40** / R3 3 / R1 LB125-P10+三守卫，calibrated_keys 11 键，enforce=True；每日 19:10 影子批攒 OOS，周六自动 skip R1/R2（哈希判据）
- portal 后验已切**九审可成交口径**（D+2 开盘买入）：引用数字看 pick_verdicts.json `summary.caliber.id`（exec-d2-open-20261010），与旧 close-d0 不可比

## 待老田拍板/输入（截至 10-10 晚更新）
- ~~J-1 jev 451 治本~~ **✅销案（084）**：根因=CF 对大陆出口 geo-block；typesafe 走本机 7890（SG）恢复，news_ai 已接 proxies（TS_PROXY env 可覆盖）+ 本地 9b 回落二级保险（083，盲测 86.7%）
- L6 登录开闸（等 Bearer 凭据）；菜鸟 dump 停在 10-08：周一盘前确认 dump 更新链（R4 OOS 材料依赖）

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
