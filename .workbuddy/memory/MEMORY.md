# K-STOCK 项目长期记忆（E:\老田的AI助手）

## 节点链与基线（2026-10-10 下午固化）
- **S3 裁决复算基线 = KGStockAnalysis `72005b4`**（069 三批收编后工作区净）；裁决材料引用此 HEAD
- 节点链：今晚 19:10 OOS 首记 → 10-13 周一五判据（验收器 `k_stock/paper/acceptance_check.py --date 20261013`，清单 docs/ops/2026-10-13_周一撮合验收清单.md 含 §3.3 新增预检）→ 10-14 S3 裁决 → 10-15 双线合流 → ~10-20 前向复算满窗
- RiskGuard 四规则定值：R2 2.5 / R4 **0.40** / R3 3 / R1 LB125-P10+三守卫，calibrated_keys 11 键，enforce=True；每日 19:10 影子批攒 OOS，周六自动 skip R1/R2（哈希判据）
- portal 后验已切**九审可成交口径**（D+2 开盘买入）：引用数字看 pick_verdicts.json `summary.caliber.id`（exec-d2-open-20261010），与旧 close-d0 不可比

## 待老田拍板/输入（截至 10-10）
- **J-1 jev 451 治本**：typesafe systemone 自 10-09 09:18 返回 451（法律层），止损已兜（jev_backfill 连续 3 组空判短路 eec18c0）；方向①云侧处理 ②本地 Ollama 回落
- L6 登录开闸（等 Bearer 凭据）；Neo4j dump 两案（停库窗口 vs bind mount）
- 菜鸟 dump 停在 10-08：周一盘前确认 dump 更新链（R4 OOS 材料依赖）

## 本日新增坑位（复用价值高）
- **PS 5.1 dot-source 磁盘 .txt 静默不执行**（非 .ps1 扩展）：预演注册表部署脚本须"解码→.ps1 副本→dot-source"；N1Check/JEVBackfill 均为注册表部署链（HKLM part1+part2 base64）
- 沙箱拦外网 API（typesafe）：手动验证云侧调用须无沙箱跑，SIGTERM 零输出=沙箱杀
- regen 类脚本输出英文化：中文经 PS 管道捕获 GBK 错位乱码
- 告警检查只查 pending 区，`sent/` 归档（*.done）≠在告（071 实锤误报源）
- KGStockAnalysis venv 已装 pytest（新测试 pytest 风格，unittest 收集为 0）
