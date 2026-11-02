# /quant 页面包重建回执（D0-4）v1

- 日期：2026-11-02 14:49
- 问题：`portal/quant/` 旧构建包仍含 `autoSession`（D0-4 安全项未收口）
- 动作：从智策前端源码（外部仓 dirty=0）重建同源静态包，替换旧包
- 结果：**autoSession=0、5175/8102/8105 字面量=0、L1 18/18 PASS**

## 一、构建

- 源码：`C:\项目\zhice-os-v2-windows-20260920\code\frontend`（外部仓 dirty=0，HEAD `f8bfac9`）
- 方式：vite 原始构建到临时目录（现有 hermes 流水线 same-origin 模式 P1 未实现，暂用等价 env）
- env：`VITE_BASE=/quant/`、`VITE_API_BASE=/quant-api/api/v1`、`VITE_ROUTER=hash`、`VITE_ENABLED_MARKETS=CN,US,HK,CRYPTO`
- 命令：`node node_modules/vite/bin/vite.js build --outDir C:\项目\deliverables\_tmp_quant_build_20261102 --emptyOutDir`
- 产物：index.html + assets/index-Bfo9dQYu.js (1.98MB) + assets/index-Dl42rMEp.css + logos
- 旧包备份：`C:\项目\backups\portal_quant_prev_20261102_144858`

## 二、安全断言

| 断言 | 结果 |
|---|---|
| `autoSession` / `zhice_auto_password` | **0** |
| `5175` / `127.0.0.1:8105` / `127.0.0.1:8102` | **0** |
| `/quant-api/api/v1` 前缀 | 存在 |
| 前端源码 autoSession 引用 | 0（`ProtectedRoute` 已恢复登录跳转） |

## 三、运行验证

- 8734 `/quant/` → **302**（auth 登录跳转，符合 L1 语义）
- 8734 `/quant/index.html` 与 assets → 302（未登录，受保护）
- `l1_acceptance.py` → **18/18 PASS，rc=0**

## 四、限制/后续

- 本次为等价 env 的 vite 构建，**未走 `hermes_acad1_build.py` same-origin P1**（该模式仍待实现）；无 VERSION.json 版本元数据；
- 需在 L1 正式验收前补：same-origin 流水线模式、VERSION.json/审计、可复现 clean build 证据；
- 回滚：恢复 `C:\项目\backups\portal_quant_prev_20261102_144858` 到 `portal/quant`。

## 五、证据

- 构建日志：`deliverables/2026-11-02_quant_rebuild_build.log`
- 本回执 + 提交（portal/quant）
