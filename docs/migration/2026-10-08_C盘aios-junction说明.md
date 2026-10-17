# C:\aios junction 说明（勿再查"双副本漂移"）

> 2026-10-08 副本治理方案B第1步落盘 ｜ 完整证据链：`docs/ops/2026-10-08_Jev双副本治理方案.md`

## 一句话

**`C:\aios` 是指向 `E:\老田的AI助手` 的 NTFS junction（目录联接），不是第二份代码。** 两条路径访问同一份磁盘文件、同一个 git 仓库（约 2026-09-11 建立）。

## 校验命令

```powershell
fsutil reparsepoint query C:\aios        # Tag 0xa0000003 = Junction
(Get-Item C:\aios).Target                # 应输出 E:\老田的AI助手
```

## 给所有 AI Agent 与新同事的约定

1. **禁止对 `C:\aios\**` 与 `E:\老田的AI助手\**` 做"双目录 diff/同步"**——那是同一批文件，diff 自己跟自己。
2. 代码权威 = E 盘工作站 git 仓库；C 盘路径是兼容别名（存量脚本硬编码 `C:\aios\jev-eval` 继续有效，无需改）。
3. junction 失效（E 盘换盘符/移盘/误删）→ C 盘脚本批量瘫痪。该风险已由 `ops/health_check.py` 的 `check_junction()` 每日 03:00 巡检兜底（2026-10-08 加入）。
4. 新写脚本路径一律用 `$PSScriptRoot` 或配置取根，不硬编码盘符。
