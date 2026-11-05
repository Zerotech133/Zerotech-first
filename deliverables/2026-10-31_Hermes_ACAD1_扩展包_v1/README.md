# Zhice 前端 same-origin 构建包（`/quant/` 页面包）v1

> 日期：2026-11-04T07:29:57+08:00 ｜ 任务：P1 same-origin clean build（W1/UF-17 前置）
> 模式：**same-origin**（VITE_BASE=`/quant/`、VITE_API_BASE=`/quant-api/api/v1`）
> 版本：`20261104_072940_f8bfac91` ｜ 源 commit：`f8bfac91c8ee` ｜ 源 dirty_count：122

## 1. 一条命令重建

```powershell
cd C:\项目\老田的AI助手
python -X utf8 tools\hermes_acad1_build.py --source <zhice_frontend> `
  --base-path /quant/ --api-base /quant-api/api/v1 --same-origin `
  --router hash --markets CN,US,HK,CRYPTO --dest <stage> --deliverables <deliv> --no-verify --json
```

## 2. 构建/断言摘要

| 项 | 值 |
|---|---|
| typecheck | `pass` |
| vite_rc | `0` |
| 产物 | `C:\aios\portal\quant`（8 文件 / 2.4 MB） |
| VERSION.json 版本 | `20261104_072940_f8bfac91` |
| same-origin 断言 | ok=`True` |
| dirty_count | 122（要求 0） |
| autoSession 命中 | 0（要求 0） |
| 5175/8105 残留 | 0（要求 0） |
| 裸 /api/v1 残留 | 0（要求 0） |
| /quant-api/api/v1 命中 | 19 |

## 3. 同源补丁规则

- `location-host-to-same-origin-prefix`：`://${location.host}/api/v1` → `://${location.host}/quant-api/api/v1`
- `relative-api-to-same-origin-prefix`：`/api/v1 (relative)` → `/quant-api/api/v1`
- `root-logo-to-base`：`/logo*.png (root absolute)` → `/quant/logo*.png`

## 4. 边界 / 未做

- 仅构建 + 补丁 + 断言；**未发布**到 `portal/quant/`、未改 nginx、未启停服务、未 git commit。
- `/quant/` nginx 路由属 L2 批准项（L1_待批包 §6），本包可先落盘待批。
- 源码 `autoSession`/`zhice_auto_password` 下线属 P2 前置（本次实测未检出）。

## 5. 回滚

- 本包为独立构建目录（stage/deliverables），回滚=删除该目录；无生产/运行时改动。

## 6. 源可重现信息

- 源码仓库：`C:\项目\zhice-os-v2-windows-20260920`（branch `master`，HEAD `f8bfac91c8eeca8be9271cb7b2a4a783931f67ea`）
- 源码树摘要（src/public/构建配置）sha256：`2f607c24d0f6abff381a6d493e61a87c4021cbfb8580805ab097b91537ecf364`

---
_本文件由 `tools/hermes_acad1_build.py`（same-origin 模式）自动生成。_
