# C:\aios junction 说明（勿再查"双副本漂移"）

> 2026-10-08 副本治理方案B第1步落盘 ｜ 完整证据链：`docs/ops/2026-10-08_Jev双副本治理方案.md`
> **2026-10-22 校正**：本机已从 E 盘迁至 C 盘，junction 目标实测为 `C:\项目\老田的AI助手`（原文写 `E:\老田的AI助手`，已过期）。

## 一句话

**`C:\aios` 是指向 `C:\项目\老田的AI助手` 的 NTFS junction（目录联接），不是第二份代码。** 两条路径访问同一份磁盘文件、同一个 git 仓库（约 2026-09-11 建立；2026-10 迁盘后目标变更，2026-10-22 实测确认）。

## 校验命令

```powershell
fsutil reparsepoint query C:\aios        # Tag 0xa0000003 = Junction
(Get-Item C:\aios).Target                # 实测应输出 C:\项目\老田的AI助手
```

## 一个真实的踩坑（2026-10-22 实测）

经 `C:\aios\...` 调用**按 `$PSScriptRoot` 推导路径的脚本**会算错根：`$PSScriptRoot` 解析为 `C:\aios\tools`，于是 workspace 变成 `C:\`、备份目标变成 `C:\_vault`——**会在 C 盘根凭空造一棵备份树**。

所以：**排计划任务或跑运维脚本，一律用物理路径 `C:\项目\老田的AI助手\...`，必要时显式传 `-VaultRoot`。** 别名只用于阅读与零星手工命令。

## 给所有 AI Agent 与新同事的约定

1. **禁止对 `C:\aios\**` 与 `C:\项目\老田的AI助手\**` 做"双目录 diff/同步"**——那是同一批文件，diff 自己跟自己。
2. 代码权威 = `C:\项目\老田的AI助手` 的 git 仓库；C 盘别名路径是兼容层（存量脚本硬编码 `C:\aios\jev-eval` 继续有效，无需改）。
3. junction 失效（移盘/误删）→ 别名路径的脚本批量瘫痪。该风险由 `ops/health_check.py` 的 `check_junction()` 每日巡检兜底（2026-10-08 加入）。
4. 新写脚本路径一律用 `$PSScriptRoot` 或配置取根，不硬编码盘符；**但用 `$PSScriptRoot` 的脚本不要经别名调用**（见上面的踩坑）。
