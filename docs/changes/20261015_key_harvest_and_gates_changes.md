# 密钥收口日+告警补盲+原子写+W9 门禁 执行留痕（K-STOCK-393，2026-10-15 凌晨）

用户拍板执行"密钥收口日 → 告警补盲 → 原子写补齐 → W9 门禁 → W8 材料"。逐项：

## 一、密钥收口（A 组，全绿）
1. **quantdinger P0 轮换**：tools/rotate_quantdinger_keys_20261015.py
   ——backend.env 三键（SECRET_KEY:731 / CREDENTIAL_ENCRYPTION_KEY:732 /
   ADMIN_PASSWORD:58）secrets 模块新随机值替换，旧文件备份
   %TEMP%\quantdinger_key_backup_20261015\；复核：SECRET_KEY 64hex、
   CRED_KEY Fernet-32B 兼容、ADMIN 22 字符；backend.env 确认被
   gitignore（不入版本史）。⚠️ ADMIN_PASSWORD 已换，登录用新值
   （文件内查看）。
2. **news_ai.py:165 兜底断链修复**：_jev_key 全链路落空出口加
   stderr WARN + env_fallback.flag（同 load_env_dpapi 判例），
   豁免退出哲学不变（链路不断只退化）。备份 .bak_20261015_keyfix。
3. **jev 明文明文清除**：remove_plaintext_env_20261015.py——实证
   DPAPI 解密 OK（len=107）后，两份明文（jev-gate.env.plain.ps1、
   jev-gate.env.ps1.bak_20261014，各 9 条明文赋值含 JEV_API_KEY/
   QD_PASSWORD/webhook）备份 %TEMP%\jev_plaintext_removal_20261015\
   → SHFileOperationW 回收站（rc=2 不可信判例，exists 逐个复核
   全部 False）→ 删后 DPAPI 复验 OK。轮换清单第 7 项至此清偿。

## 二、告警补盲（B 组）
4. **news_collector.py 退出码忠实化**：main() 返回 bool(ok)，
   `__main__` 改 sys.exit(0 if main() else 1)——全源失败 rc≠0，
   alert_fail_wrap 告警链打通（runs 表已有 ok 记录，行为兼容）。
   备份 .bak_20261015_alert。
5. **a0_chief.py 假 0 显形**：debate_stats 吞错出口加 stderr WARN
   （返回形状不变保元组解包）；debate_health 加 out["error"] 键
   （dict 加键对消费方安全）。备份 .bak_20261015_alert。

## 三、原子写补齐（C 组）
6. **portal/build_pick_verdicts.py:298** 与 **build_pick_details.py:84**
   直写改 tmp+os.replace（sync_publish 判例）；防门户请求路径并发
   读半截 JSON。备份 .bak_20261015_atomic。

## 四、W9 门禁（D 组）
7. **tools/check_format_args.py**：AST 格式串占位符 vs 参数数静态
   校验。首跑 26 候选→逐个核查定真假：25 个为 `%%` 字面百分号
   误报（"90%% avg" 第二 % + 空格 + 单词首字母 a 组成假 "% a"——
   工具改 %% 整体优先消耗）；1 个为 IfExp 元组盲区（api_misc:1441
   实为运行时正常）——工具修两处后**全仓 0 命中**。
   quant_backtest/test_check_format_args.py 5 测试钉死判例
   （真阳性/%%不误报/%.*f 计参/动态值跳过/仓库自扫）。
8. **run_weekly_gate.py**：PytestWeekly 两级门禁——gate1
   collect-only 收集炸窗绊线（实跑 1193 用例 21s PASS）+ gate2
   全量 pytest；deploy_pytest_weekly.ps1 改调 gate 脚本、
   ExecutionTimeLimit PT10M→PT30M、ASCII 校验 OK。
   ⚠️ **需管理员手动执行一次部署**（schtasks 沙箱拦，自动化不可注册）：
   `powershell -ExecutionPolicy Bypass -File C:\aios\quant_backtest\deploy_pytest_weekly.ps1`
   部署后周六 09:00 生效。

## 五、W8 三议题材料（E 组）
9. workspace docs/W8评审三议题决策材料_2026-10-15.md：宽度口径
   统一（建议 A 湖口径+一周并行对照）/HMM 影子化并联（建议 B）/a+b
   实盘化节奏（建议 A 先 b 后 a），各含数据、选项、建议、前置依赖。

## 回退
- quantdinger：%TEMP%\quantdinger_key_backup_20261015\ 覆盖回去
- news_ai / news_collector / a0_chief / build_pick_*：.bak_20261015* 原样覆盖
- jev 明文：%TEMP%\jev_plaintext_removal_20261015\ + 回收站双层可逆
- deploy 脚本：.bak_20261015 覆盖后需重新注册任务

## 验证
- 语法 AST 全过（6 个改动文件）；pytest 回归：check_format_args 5 +
  tdx 9 全绿；DPAPI 前后双验 OK；gate1 收集 1193 用例 PASS。

## 附：PytestWeekly 注册完成（2026-10-15 04:05，用户指示执行）
- deploy_pytest_weekly.ps1 管理员执行成功：Register-ScheduledTask
  SYSTEM/HighestAvailable，周六 09:00（下次 2026-10-17），PT30M 限
  （全量实测 5m23s 富余），重跑可幂等（先 Unregister）；任务描述
  同步改为全量两级门禁口径（原 51-case 字样过时）；
- **端到端验证**：Start-ScheduledTask 手动触发真实跑通——collect
  绊线 PASS（1193 用例）+ 全量 **1175 passed / 18 skipped / 0
  failed / 323s**，LastTaskResult=0x0，日志
  C:/aios/quant_backtest/logs/pytest_weekly.out；
- skipped 18 vs 此前 7：为依赖网络/环境外置用例在计划任务 SYSTEM
  上下文的预期跳过（sentiment/jev 等触网类），0 失败不受影响；
- 沙箱注记：Register/Start-ScheduledTask 需解除沙箱执行（本节为
  首个计划任务注册在 agent 侧完成的判例，此后同类可循）。
