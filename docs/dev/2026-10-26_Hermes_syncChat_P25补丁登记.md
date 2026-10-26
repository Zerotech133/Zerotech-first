# Hermes syncChat P2.5 profile 绑定补丁 · 登记（2026-10-26）

> K273 边界：Hermes 代码不入主仓（生产基 `Hermes/hermes-webui-next`），本文件是该磁盘改动的**唯一版本留痕**。
> 关联：工单 `K-QUANT-20261026-P25DOC`；补丁工单（数据/行为背景）见 `deliverables/2026-10-26_G2双锚cron与P2.5sync修复_五要素待批包_v1.md`。

## 1. 改动对象

- 文件：`C:\项目\老田的AI助手\Hermes\hermes-webui-next\api\routes.py`
- 函数：`_handle_chat_sync`（`POST /api/chat`，前端不使用的回退端点）
- 目的：修「per-client 切 tianfeng 后 sync `/api/chat` 仍回答默认席位满宠」的 profile 身份串号；
  UI 流式路径本无此问题。

## 2. 改动摘要（白盒）

1. `_handle_chat_sync` 的 `try` 前初始化 `_sync_home_override = None` / `_sync_home_override_token = None`；
2. AIAgent 构造前：若会话 `profile` 非 `default`，调用
   `hermes_constants.set_hermes_home_override(get_hermes_home_for_profile(profile))`；
3. 原 `finally` 起始处 `reset_hermes_home_override(token)`（default / 异常路径 no-op 或安全复位）；
4. 不改流式路径、不改 default profile 行为、不改任何配置/任务。

## 3. 备份与 diff

- 备份：`C:\项目\work\_dsh_p25_20261026\routes.py.bak-20261026-201232`
- 补丁草稿：`C:\项目\work\_dsh_p25_20261026\routes.py.patched-20261026-201232`
- 原始 diff 证据：`C:\项目\work\_dsh_audit_runtime_20261026\evidence_p25_diff.txt`（审计 B 采集）
- 语法：py_compile 通过（草稿 + 线上均验证）

## 4. 激活（写盘≠生效，2026-10-26 20:15 已执行）

- 8787 为 session-0 进程，换血需 UAC：
  `Start-Process cmd -Verb RunAs -ArgumentList '/c taskkill /PID 42948 /F & timeout /t 2 & schtasks /Run /TN "HermesWebUI-Guard30"'`
- 结果：旧 PID 42948（session 0，08:55）→ 新 PID 47992（session 2，20:15:01），health 200，
  新进程启动时间 > routes.py mtime（20:12:34）→ **新码生效**。

## 5. 验证证据

- `work/_dsh_p25_20261026/verify-sync-identity.md`：tianfeng sync → "田丰，数字军团参谋长、归口复核席。"；
  default sync → "我是满宠（伯宁）…"；全局 active=default、active_profile 未生成 → checks 全 true；
- UI 流式回归：tianfeng → 田丰身份（52.1s），未受影响；
- 回滚：恢复备份 + 同款 UAC 重启；**当前未回滚**。

## 6. 风险与边界

- 仅影响 sync `/api/chat`（前端不使用）；UI/默认满宠不变；
- 依赖 `hermes_constants.set_hermes_home_override` 上下文 API，异常路径已 try/except 收敛；
- 后续 Hermes 升级若替换 `routes.py`，需重新评估/重放本补丁（本文件为锚点）。

## 7. 审计 B 收口补充（2026-10-26 22:1x）

- **S-02 边界（已知、低风险）**：本补丁只给 sync 线程装 `hermes_constants` 的 contextvar override，
  **未像流式路径那样镜像 `HERMES_HOME`/profile `.env` 到环境变量**；因此 sync 轮内由 terminal 工具
  拉起的子进程（hermes CLI/命令）仍可能读 default profile。当前 tianfeng/zhaoyun/zhuge 均无
  `.env` 凭据，影响低；后续若 profile 加凭据或 sync 需要 spawn 子进程，应参照 streaming 的
  `_build_agent_thread_env` 对齐。
- **S-06 回滚注意**：回滚资产 `Hermes/hermes-webui` 未含本补丁；`HermesWebUI-Guard` 的触发器已过期
  不再轮值（实际守护=Guard2/Guard30）；7 个计划任务的注册命令目前只存在于
  `work/_dsh_p25_20261026/ACTIVATE.md` 与 `C:\Temp\*.ps1`，未版本化；建议后续把任务注册脚本
  收进仓库 `tools/`（ASCII 参数路径 + UAC 说明）。
- **实际进程记录**：20:15 UAC 换血后 PID=47992；21:08:05 因 `.hermes/config.yaml` 修改后
  手工启动 Guard 再换血为 **PID=57208（session 0）**，routes.py mtime 20:12:34 < 21:08:05，
  补丁仍在（22:16 复验 sync：tianfeng→田丰、default→满宠，ok=true）。
