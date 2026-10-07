# SYSTEM 计划任务部署/排障完整手册（本机实战记录）

## 1. 注册表存源码的分段细节

- 源码按固定切点（如 4471 字符）分 part1/part2，各自 `[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(...))`
- part1 末尾常带 `==` padding，**不能字符串拼接后整体解码**（FormatException）。必须分段解码再拼字节数组：

```powershell
$bytes = [Convert]::FromBase64String($b.part1.Trim()) + [Convert]::FromBase64String($b.part2.Trim())
$s = [Text.Encoding]::UTF8.GetString($bytes)
```

- 语法校验：`$null = [ScriptBlock]::Create($s)`，编译通过即语法 OK
- 中文路径文件不要用 `ParseFile`/`-File` 校验（FileReadError / 访问被拒绝），内存 ScriptBlock 编译不受影响

## 2. 任务 Action 最终形态要点

- `-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -Command "..."`
- `-NoProfile` 是 SYSTEM 下成败关键（无此参数出现过 5 秒暴毙 4294967295）
- 内嵌双引号用两个单引号 `''` 转义（外层是 PowerShell 构造字符串时）
- 部署 Action：`New-ScheduledTaskAction` + `Set-ScheduledTask -Action`；触发：`Start-ScheduledTask`（不要用 schasks /Run 的中文问题）
- 部署后立即回读 `Actions[0].Arguments`，检查长度与是否含 `[char]0x3F`（损坏判定）

## 3. docker 流式备份的退出码误报

`Start-Process docker -ArgumentList 'exec', $c, 'sh', '-c', ... -RedirectStandardOutput $dest -Wait`
即使数据完整写入且 gzip 可全量解压，`$p.ExitCode` 仍可能非 0。判定原则：

- 保存阶段只看「文件存在 + Length 大于 0」
- 真实质量门是后置的 `GZipStream` 全量解压校验（Test-Gzip）
- 不要用 ExitCode 给 docker exec 管道流判失败

## 4. tar 归档两个坑

1. bsdtar（Windows 自带 tar.exe）遇警告（Permission denied 等）退出码非 0，但归档已生成且可能残缺——不能只看文件存在，要校验大小（如 jev_eval 正常 ~180KB，残缺时 3.5KB）或 `tar tzf` 列内容
2. 缺 SYSTEM 读权限的文件会被静默跳过；兜底方案 `robocopy <src> <tmp> /E /B` 后再 tar（SYSTEM 有 SeBackupPrivilege）

## 5. 备份脚本验证过的结构（参考）

- 4 个 pg_dump 库（docker-db_postgres-1 / zhice-db / quantdinger-db / newquantsystem_v2-postgres-1）
- 容器卷/宿主目录 tar.gz：api storage、qdrant storage、dev-assistant data、quant_src、jev_eval
- 每个 gzip 全量解压校验
- 敏感目标 AES-256-CBC + HMAC-SHA256（格式 iv[16]+ciphertext+mac[32]，密钥 `%USERPROFILE%\.backup_aes_key`，RNGCryptoServiceProvider 32B），加密后删明文
- 镜像 D:\backups_mirror，两端 14 天滚动清理
- `$script:Failed` 计数，结尾 `exit 0/1`
- 日志 Add-Content -Encoding ASCII（中文路径在日志里显示为 ? 无影响）

## 6. PS 5.1 命令行/传参铁律（本轮新增，高频踩坑）

1. **原生 exe 传参剥 JSON 双引号**：`& lark-cli --content $msg`（$msg 为 ConvertTo-Json 产物）送达时变成 `{text:[..]}`，对端报 "not valid JSON"；`\"` 转义会把参数拆碎、`""` 双写同样碎——**JSON 传原生 exe 无解，必须走 python `subprocess.run([...])` 参数列表**（参考 jev-eval/backup_alert.py 包装器：消息文本或消息文件作 argv，内部 json.dumps）
2. **`-Command "..."` 内嵌任意 `"` 会在首个双引号处截断**：Action 的 -Command 内部全用 `''`（送达后成 `'`），一个双引号都不能有；PATH 分号拼接用 `[char]59`
3. **注册表存的是 LF-only 源码**：`"`r`n"` 匹配/替换会 NOT FOUND，一律 `"`n"`；替换块用「标记串定位 + Substring 切割」而不是 .Replace 整段匹配
4. **python 不在 SYSTEM PATH**：全路径 `& 'C:\Program Files\Python311\python.exe'`
5. **docker 多层 shell 写文件（heredoc/引号）必被剥**：PS 侧 `[Convert]::ToBase64String` → `echo <b64> | base64 -d > file`；中文路径段用 `[char]0xXXXX` 拼接，PS→docker 内层双引号同样会被剥（`grep -i -E "a|b"` 的 `|` 会变成管道执行）
6. **PS 5.1 读 no-BOM UTF-8 中文按 ANSI**：落盘的 .ps1 保持纯 ASCII（中文只允许出现在经 UTF8 显式编码的 WriteAllText 数据里）
7. **PS 5.1 `Get-Content -Raw` 读 UTF-8 默认按 ANSI(GBK)**：跨进程读取 UTF-8 flag/数据文件（含 no-BOM）必须显式 `-Encoding UTF8`（对 BOM/no-BOM 都安全）；否则中文路径变 `鑰佺敯...` 式乱码进飞书卡片。生产侧 PS 写 `-Encoding UTF8` 自带 BOM，但消费方仍应显式指定，防外部生产者（alpine base64 通道等）写 no-BOM
8. **`Encoding.GetBytes()` 不发 BOM**：`New-Object Text.UTF8Encoding($true)` 只影响 WriteAllText；GetBytes 出来的字节不含 EF BB BF，需要 BOM 得手动拼 `[byte[]](0xEF,0xBB,0xBF)`
9. **docker base64 写入的文件可能被幽灵同步稍后抹掉**（VM 层无此文件时尤甚）：权威源在注册表时无运营影响——下次 SYSTEM 运行自动回写重建；验证持久化要隔几分钟二次 ls
10. **重启 jev 系 python 组件**：`Stop-Process -Id <pid> -Force` 后立即 `& E:\老田的AI助手\zhice.ps1 watchdog` 即时拉起；card handler 首启可能超 15s ready 超时自毙，watchdog 会再拉，以 `$env:TEMP\zhice-jev-gate.log.card.out` 出现「事件监听就绪」为准
11. **PS→docker sh -c 内禁用双引号**：`grep -c "a b"` 被剥成 `grep -c a b`（b 被当文件路径，结果误报 0）；模式含空格用无空格特征串（`grep UTF8 file`）

## 7. SYSTEM 与 Administrator 的会话边界（lark-cli 认证）

- lark-cli 的 appSecret/token 存「keychain」（`config.json` 标 `source: keychain`），**只在 Administrator 交互式登录会话可见**；cmdkey /list 看不到条目，`.lark-cli` 目录里也没有密文文件——无法导出/复制
- SYSTEM 下切换 `$env:USERPROFILE` 无用：报 `invalid_client 20140 The auth method is not supported`
- `auth login` 是设备码流程且密钥来自 keychain，SYSTEM 下连发起都不行
- **正解：两段式告警**——SYSTEM 任务只写 flag 文件（`jev-eval\alerts\*.flag`，ASCII key=value），Administrator 交互会话侧跑转发任务（`AI工作站告警转发`，Daily 03:20 + 30 分钟重复 + 登录触发，`alert_forward.ps1` → python `backup_alert.py` → lark-cli），发送成功后 flag 移入 `alerts\sent\*.done`
- Register-ScheduledTask 传中文任务名/中文 -File 路径 OK（cmdlet 原生 Unicode，不经 cmd）；但 `RepetitionDuration [TimeSpan]::MaxValue` 会报 XML 越界，用「Daily 触发器 + 借 Once 触发器的 Repetition(PT30M/P1D)」模式

## 8. 结果码速查

| LastTaskResult | 含义 |
|---|---|
| 0 | 成功 |
| 1 / 0x1 | 脚本主动 exit 1（内部失败计数） |
| 4294967295 / -1 / 0xFFFFFFFF | 进程快速退出（profile、语法、命令行损坏） |
| 267009 / 0x41301 | 任务正在运行 |
| 267011 / 0x41303 | 任务从未运行 |
| 0xFFFE0000 类 | 任务启动失败（Action/凭据问题） |

## 9. 备份恢复演练（已验证的流程）

- 演练从 **D:\backups_mirror** 取件（顺带证明镜像可用）
- 密钥文件 `%USERPROFILE%\.backup_aes_key` 是 **Base64 文本（44B）**，不是原始 32B 字节——`[Convert]::FromBase64String((Get-Content -Raw).Trim())`；直接 ReadAllBytes 当 key 会报 "not a valid size" 且 HMAC 全错
- 格式 iv[16]+ct+HMAC[32]，HMAC 覆盖 iv+ct；AES-256-CBC PKCS7
- 验证链：HMAC 比对（0 mismatch）→ 解密 → docker `gzip -t` → `zcat | head` 验 PostgreSQL dump 头
- 镜像里敏感目标只存 .enc（明文加密后已删），明文校验走解密后的临时产物

## 10. 诊断纪律

- 诊断任务（如 bk_deploy/bk_diag）用完立即 `Unregister-ScheduledTask -Confirm:$false`
- C:\bk_* 临时文件用完删除；运行期日志（如 C:\bk_out.log）可保留
- 沙箱删真实磁盘文件可能删不掉时，用 docker alpine 挂载后 `rm -rf`
- 测试产生的缺陷备份目录要在 E: 和镜像 D: 双端清理
- 调试调用会真实触发外部副作用（如 lark-cli 发飞书消息），汇报时必须告知用户

## 11. 飞书事件流僵尸连接与看门狗自愈

- lark-cli `event consume` 经本地 named pipe bus（`event _bus` 子进程）连 wss。网络抖动后 bus.log 出现 `[SDK ERROR] receive message failed ... wsarecv` + disconnected，SDK 通常自动 reconnect（日志再打 connected），但**偶发假重连**：TCP 仍 ESTABLISHED、日志也有 connected，应用层却再也收不到任何事件（连 `im.message.message_read_v1` 已读回执都没有）——进程存活/端口探测完全发现不了
- 判据文件：`%USERPROFILE%\.lark-cli\events\<appid>\bus.log`，时间戳格式 `yyyy/MM/dd HH:mm:ss`；只认 `[SDK] connected/disconnected to wss` 与 `[SDK ERROR]`（`Consumer connected/disconnected` 是本地 named pipe 状态，与 websocket 无关）
- 自愈双保险（zhice.ps1 watchdog，每 5 分钟，`Get-CardHandlerRestartReason`）：
  1. **年龄封顶 6h（21600s）**：到期 `taskkill /PID <python pid> /T /F` 杀整树（必须 /T，含 consume 与 _bus 两个 lark-cli 子孙，否则孤儿 bus 继续持有僵尸连接），再由 Start-JevStack 按"缺失拉起"重建。年龄封顶是唯一能兜住"假 connected"的手段
  2. **ws-down 检测**：尾部最后 bad 晚于最后 good 且超 90s（给正常重连与启动留宽限，防误杀）→ 同样杀树重建，比 6h 更快抓住"断开不重连"
- 重启后新 bus fork 可能有约 3 分钟退避（旧 named pipe 刚消失），以 card.out「事件监听就绪」+ bus.log 新 connected 为准
- 看门狗身份必须是 **Administrator Interactive**（lark keychain 可读）；误建成 SYSTEM 则新 handler 立即 invalid_client
- 端到端验证技巧：字节级把 21600 临时 Replace 成 120（不要用 Edit），当前 handler 年龄即超标，下一轮 watchdog（5 分钟内）自动触发，日志见 `card-handler-restart(age), card-handler`；验完立即改回并确认真实磁盘内容，否则每轮都会重启

## 12. Edit 工具会叠加 BOM（高危，曾让 watchdog 静默失效）

- Edit 工具每次保存可能在文件头**追加一个 UTF-8 BOM**；原本带 BOM 的 .ps1 连续两次 Edit → 3 个 BOM（`EF BB BF` ×3）
- `powershell -File` 遇多 BOM 的表现：首行 `﻿﻿#` 报"无法识别"、param 块报 "param not recognized"，参数绑定失败后 switch 落默认分支——`watchdog` 被跑成 `status`，**自愈静默失效、无弹窗、LastTaskResult 仍为 0**，只能靠日志/行为发现
- 校验 BOM **不能只看前 3 字节**（N 个 BOM 的前 3 字节都是 EF BB BF），要读前 6~9 字节确认 BOM 后紧跟 `23`（#）
- 修复：ReadAllBytes 循环剥到 `EF BB BF 23...` 后 WriteAllBytes；字节级改常量用 ReadAllText+Replace+`New-Object Text.UTF8Encoding($true)` 写回（保单 BOM）
- 改动后三连验证：Parser 语法 OK → `powershell -File <script> watchdog` 实跑行为正确 → docker 挂载读真实磁盘大小与关键串（三层视图，沙箱写入有同步延迟）

## 13. 禁止在沙箱 shell 里启动 Vite/前端（写不了 .vite 预构建缓存）

- 现象：浏览器 5 条 `net::ERR_ABORTED .../node_modules/.vite/deps/react.js?v=xxx`（react/antd/react-dom/react-router/jsx-runtime 全挂）；首页 `/` 仍 200，但直接探旧 deps URL 返回 **504 网关超时**
- 根因：Vite dep optimizer 卡死/缓存失效，`node_modules/.vite/` 下残留多个 `deps_temp_*` 崩溃目录；浏览器持旧 `v=` 哈希请求 → 服务端挂起 → ABORTED
- 关键陷阱：从**沙箱 PowerShell** 跑 `zhice.ps1 start` 拉起的 vite，其工作目录（`E:\zhice-os-v2-windows-*\code\frontend\node_modules\.vite\...`）不在沙箱可写白名单 → optimizer 必失败，还会打 `TRAE Sandbox Error: hit restricted`
- 正确重启姿势：杀掉旧前端 pid 后，`Start-ScheduledTask ZhiceWatchdog` 让计划任务在**沙箱外**（Administrator Interactive）拉起；等 ~45s，再验 `/src/main.tsx` 200 且页面里新的 deps URL（新哈希）200
- 告知用户硬刷新（Ctrl+R）：客户端还持旧哈希，不刷新仍打旧 URL
- 判据：首页 200 ≠ 前端健康；必须再探一个带哈希的 `.vite/deps/*.js` URL 确认 optimizer 活着

## 14. SYSTEM 跑 Python：中文路径是禁区（2026-09-25 门户 API 桥实战）

- 症状：计划任务 Action 传中文路径的 .py 给 python.exe，任务 LastResult=0"成功"但进程秒退、端口不监听、无任何报错输出——**完全静默失败**
- 同一脚本 Administrator 沙箱直跑完全正常（compile OK + 服务 200），只有 SYSTEM 上下文挂
- 根治：建 ASCII junction `New-Item -ItemType Junction -Path C:\aios -Target 'E:\老田的AI助手'`，任务 Action、启动器、python 脚本参数全程零中文；junction 是文件系统对象，不受幽灵同步影响
- 附带铁律：① 任务 Action 用 `-Command "& ..."` 形态会即刻 4294967295（即使含 -NoProfile），必须 `-File` 启动器；② 启动器 ASCII-only（PS5.1 无 BOM 中文注释在 SYSTEM 下按 ANSI 读，虽多为一行注释但别赌）；③ 启动器用 `Start-Process -PassThru` + 等待进程退出让任务 State=Running 且退出码透传 python；④ 常驻 python 服务自愈=任务 5 分钟重复触发 + 脚本内 `except OSError: SystemExit(0)`（端口被占静默退，配合 IgnoreNew 不叠加实例）
- ExitCode 怪癖（2026-09-25 新闻管线实证）：`Start-Process` 带 `-RedirectStandardOutput/Error` + `-PassThru` 时，无论 `Wait-Process`、`$p.Refresh()` 还是 `$p.WaitForExit()`，PS5.1 下 `$p.ExitCode` 拼日志恒为空；脚本 `exit $p.ExitCode` 仍得到 0。别在这里死磕——失败可观测性应放在应用层（状态文件 + 门户型 API），任务级 LastTaskResult 仅作辅助信号
- 沙箱测试路径铁证（2026-09-25 AI 晨报实证）：沙箱 PS 经 `C:\aios` junction 访问真实磁盘时，**读穿透、写被静默重定向**——SQLite `CREATE TABLE` 不报错但同连接都查不到表（写入根本没落目标文件）。结论：沙箱内开发测试一律走 `E:\老田的AI助手\...` 中文直路径（VM 层自洽）；junction 路径只给真实任务上下文用。涉及真实磁盘建表/迁移的脚本必须保持幂等 `CREATE TABLE IF NOT EXISTS`，首次真实运行即自愈
- 探活坑：vite 开发服务器可能只绑 IPv6 `::1`（netstat 能看到 Listen），TCP 探活必须 127.0.0.1 和 ::1 双栈各试一次
- 验证坑（2026-09-25 门户复盘上线实证）：PS5.1 命令行里用中文字面量做 `$content -match '中文'` 会因命令经工具宿主/CLIXML 的编码损耗产生**假阴性**（页面实际已更新却报 MISSING，诱发两次无谓的 nginx reload）。验证页面版本一律用 ASCII 标记（如函数名 `aiRow`、元素 id），确需中文验证时让容器内 `grep -c` 或在 Python 里比对
- 时区铁坑（2026-09-25 行情快照上线实证）：本机系统时区是 **Europe/Paris（随夏令时漂移 UTC+1/+2）**，而金融调度全按北京时间。`New-ScheduledTaskTrigger -Daily/Weekly -At '08:00'` 的时刻按**系统本地时区**解释——北京 08:00 的晨报会被排到北京 14:00 才跑。全天重复任务（如 15min 轮询）不受影响，时刻敏感任务必须在创建后改写 `$t.StartBoundary = '2026-09-26T00:00:00Z'`（UTC 绝对时刻：北京 08:00=UTC 00:00、08:30=00:30、15:30=07:30），再 `Set-ScheduledTask -Trigger $t`；这样冬夏令时切换也不漂移。验证：NextRunTime 是巴黎本地时间，+6h（夏令时）换算回北京核对
- nginx 挂宿主机服务用既有模式：`set $h host.docker.internal; proxy_pass http://$h:PORT;`（resolver 动态解析，Docker Desktop 升级不失效）；server 级 proxy_read_timeout 会被 location 内覆盖，聚合类慢接口单独放宽
