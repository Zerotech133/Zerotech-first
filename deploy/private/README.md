# 零界个股分析 · 私有化版（客户交付包）

> 个股研究 / 风险解释工具。**研究分析，不构成投资建议。**
> 本包含：分析引擎源码、商业授权控制（license/配额/审计）、安装脚本、运维手册、示例报告。

## 一、系统要求

- Windows 10/11（64 位）
- Python 3.9+（安装时勾选 Add to PATH；或已装 3.10/3.11/3.12）
- 4GB+ 内存；磁盘按数据量自备
- 无需联网（离线可用；数据由客户自备或按合同提供更新服务）

## 二、安装（3 步）

```powershell
# 1) 解压交付包到任意目录（路径不要含中文/空格）
# 2) 运行安装（会创建 %LOCALAPPDATA%\ZeroBoundStockAnalysis）
.\install.ps1 -LicenseKeyFile .\license.key

# 3) 配置并激活
#    编辑 config.example.ps1 → 另存为 config.ps1（填写租户ID/数据目录）
.\activate.ps1 -ConfigFile "$env:LOCALAPPDATA\ZeroBoundStockAnalysis\config.ps1"
```

## 三、运行

```powershell
# 激活后当前窗口直接跑
python -m analysis.run --code 600337.SH --no-llm
# 报告输出：%LOCALAPPDATA%\ZeroBoundStockAnalysis\work\runs\<run_id>\report.md
# 归档（租户隔离）：%LOCALAPPDATA%\ZeroBoundStockAnalysis\tenants\<租户>\deliverables\

# 只看示例报告（无需数据源）：用包内「示例报告」目录
```

## 四、目录结构

```
install.ps1 / activate.ps1 / uninstall.ps1 / config.example.ps1
analysis/         分析引擎（快照→事件→特征→质量门→报告）
commercial/       license/配额/审计/租户隔离
deploy/private/   本说明与运维手册
sample_reports/   示例报告（markdown + h5）
VERSION / MANIFEST.sha256
```

## 五、数据与升级

- 数据由客户自备/按合同提供（curated parquet 或自有适配器）；
- 升级：解压新版本覆盖代码目录（保留 `data/`、`work/`、`license.key`、`config.ps1`），重跑 `activate.ps1`；
- 卸载：`.\uninstall.ps1`（保留数据）；彻底删除加 `-Purge`。

## 六、支持

- 运维手册：`deploy/private/OPS.md`
- 许可协议模板：`deploy/private/EULA-template.md`
- 问题反馈：交付方提供的支持邮箱/渠道

## 七、许可证

- 本软件商业授权，按 license 文件绑定租户/套餐/到期；
- 未经授权不得复制、转售或再分发；
- 第三方数据授权由客户自行取得，本包不含任何第三方数据。
