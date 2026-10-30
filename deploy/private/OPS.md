# 零界个股分析 · 私有化版运维手册

> 适用：客户现场运维人员。研究分析工具，不构成投资建议。

## 1. 日常运行

| 任务 | 命令 | 频率 |
|---|---|---|
| 单股分析 | `python -m analysis.run --code <代码> --no-llm` | 按需 |
| 批量分析 | `python -m analysis.batch --max 5` | 每日收盘后 |
| 观察池 | 编辑 `analysis/watchlist.txt` | 按需 |
| 查看报告 | `work/runs/<run_id>/report.md` 与 `h5.html` | 按需 |
| 商业审计 | `python -m commercial.cli audit-tail -n 20` | 每周 |
| 配额检查 | `python -m commercial.cli quota --tenant <租户>` | 每月 |

## 2. 环境变量（由 config.ps1 / activate.ps1 设置）

| 变量 | 说明 |
|---|---|
| `COMMERCIAL_MODE=commercial` | 启用商业授权控制 |
| `COMMERCIAL_TENANT` | 租户 ID（与 license 一致） |
| `COMMERCIAL_LICENSE_KEY_FILE` | license 文件路径 |
| `COMMERCIAL_LICENSE_SECRET` | 校验密钥（产品方提供，勿外传） |
| `ANALYSIS_WORK` | 工作目录（缓存/运行账/队列） |
| `PYTHONPATH` | 指向安装目录（源码直跑） |

## 3. 常见问题

| 现象 | 处理 |
|---|---|
| `未配置 COMMERCIAL_LICENSE_SECRET` | 在 config.ps1 填入产品方提供的校验密钥 |
| `license 已过期` | 联系交付方续期，替换 license.key 后重跑 activate.ps1 |
| `license 签名无效` | 检查 license 文件是否被修改/换行；重新获取 |
| `配额不足: reports_month` | 下月重置或升级套餐；`commercial.cli quota` 查看用量 |
| 报告数据为空/滞后 | 检查数据目录（`LOCAL_DATA_DIR`）与数据更新任务 |
| `模块加载失败` | 确认已运行 activate.ps1（PYTHONPATH 正确） |
| Python 版本报错 | 安装 Python 3.9+ 后重跑 install.ps1 |

## 4. 备份与恢复

- **备份**：`data/`（客户数据）+ `work/`（运行账/报告）+ `license.key` + `config.ps1`；
- **恢复**：解压新包 → 覆盖代码目录 → 恢复上述 4 项 → `activate.ps1`；
- 审计日志：`work` 同级的 `audit/audit.jsonl`（可导出对账）。

## 5. 安全建议

- `COMMERCIAL_LICENSE_SECRET` 与 license.key 分开保管，勿放入共享盘/聊天；
- 安装目录权限仅限运维账号；
- 定期导出审计日志留档（企业版支持审计导出）。

## 6. 升级流程

1. 备份（见 §4）；
2. 停掉运行中的批处理/计划任务；
3. 覆盖代码目录（保留 data/work/license/config）；
4. 重跑 `activate.ps1`，跑一个样例 `--code 600337.SH --no-llm` 验证；
5. 恢复计划任务。

## 7. 联系支持

交付方支持渠道：`<由交付方填写>`（响应时间按合同）。
