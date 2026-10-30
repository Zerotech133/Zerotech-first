# 零界个股分析 · 商业化底座（P2）

> 私有化部署/订阅版的商业控制层：**license 签发校验、租户隔离、配额计费、审计**。
> 依赖：仅标准库。入口：`python -m commercial.cli <子命令>`。

## 快速使用

```powershell
# 1) 签发 license（生产请设置 COMMERCIAL_LICENSE_SECRET，勿用默认值）
$env:COMMERCIAL_LICENSE_SECRET = "your-32+char-secret"
python -m commercial.cli license-gen --tenant acme --plan enterprise --days 365 --seats 5 --out license.key

# 2) 客户侧校验
python -m commercial.cli license-verify --key (Get-Content license.key)

# 3) 注册租户 + 查配额
python -m commercial.cli tenant-add --id acme --name "ACME 资管" --plan enterprise --license (Get-Content license.key)
python -m commercial.cli quota --tenant acme --metric reports_month

# 4) 审计
python -m commercial.cli audit-tail -n 20
```

## 模块

| 模块 | 职责 |
|---|---|
| `license.py` | 签发/校验（HMAC-SHA256，绑定租户/套餐/到期/席位/配额/功能）；脱敏展示 |
| `plans.py` | 套餐额度唯一来源（free/pro/enterprise：月度报告数、日 API、功能清单） |
| `tenants.py` | 租户注册 + **独立工作目录** `tenants/<id>/`（路径穿越防护） |
| `quota.py` | 按月/日周期实时扣减，超限抛 `QuotaExceeded` |
| `audit.py` | JSONL 审计（追加/检索/导出） |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `COMMERCIAL_HOME` | `C:\项目\commercial` | 商业数据根（DB/租户目录/审计） |
| `COMMERCIAL_DB` | `<HOME>/commercial.db` | 租户/用量 SQLite |
| `COMMERCIAL_AUDIT` | `<HOME>/audit/audit.jsonl` | 审计日志 |
| `COMMERCIAL_LICENSE_SECRET` | 开发默认值（仅测试） | license 签名密钥（必须替换） |

## 与业务集成（下一步）

- 报告生成前：`quota.consume(tenant, "reports_month")`
- API 入口：`quota.consume(tenant, "api_day")`
- 启动时：`license.verify(...)`，失效则拒绝服务
- 所有商业动作：`audit.append(...)`

> v1 使用共享密钥 HMAC（私有化交付 + 合同约束）；后续可升级非对称签名，上层接口不变。
