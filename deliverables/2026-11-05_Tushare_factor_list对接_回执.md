# Tushare factor_list 因子清单对接回执

- 日期：2026-11-05
- 接口：Tushare Pro `factor_list`
- 结论：**已接通并落快照**。真实调用 `code=0`，返回 **272 个因子 / 9 个分类 / 资产类型 STK**，分类计数与 Tushare 文档一致。
- 工具：`tools/data_sources/tushare_factor_list.py`
- 测试：`tools/tests/test_tushare_factor_list.py`（**18 passed**）

## 一、接口契约核对

| 项 | 实测 |
|---|---|
| 地址 | `https://api.tushare.pro` |
| api_name | `factor_list` |
| 输入 | `params` 可选：`factor_name` / `asset_type` / `factor_type`；单次拉全量，无需翻页 |
| 输出字段 | `factor_name, asset_type, factor_type, factor_desc` |
| 实测行数 | **272**（`factor_name` 唯一 272） |
| 资产类型 | **STK** 272 行（当前接口只提供股票） |
| 分类数 | **9** |

### 分类计数（实测）

| 分类 | 中文 | 数量 |
|---|---|---:|
| Alpha101 | Alpha101 量价因子 | 101 |
| Growth | 成长因子 | 15 |
| Liquidity | 流动性因子 | 35 |
| Momentum | 动量因子 | 20 |
| Quality | 质量因子 | 59 |
| Reversal | 反转因子 | 3 |
| Risk | 风险因子 | 25 |
| Size | 规模因子 | 3 |
| Value | 价值因子 | 11 |
| **合计** | | **272** |

筛选参数实测：`--factor-type Size` 返回 3 行；筛选查询只写 `snapshots/`，**不覆盖** `latest.json` / `manifest.json`，避免子集污染全量快照。

## 二、交付物

| 文件 | 说明 |
|---|---|
| `tools/data_sources/tushare_factor_list.py` | 拉取、标准化、快照、离线校验、CLI（fetch/check/status/show） |
| `tools/tests/test_tushare_factor_list.py` | 18 项单元测试（不联网，FakeSession/临时目录） |
| `research/reports/tushare_factor_list_20261105.json` | 本次快照摘要（行数/分类/sha256/token sha8） |
| `quant_backtest/data/factor_library/tushare_factor_list/` | 数据快照目录（`quant_backtest/data/` 被 gitignore，可由工具重放） |

### 快照目录结构

```
quant_backtest/data/factor_library/tushare_factor_list/
├── snapshots/
│   ├── factor_list_20261105.json           # 全量：272 行 + meta
│   ├── factor_list_20261105.parquet        # 同名 parquet（25,360 B）
│   └── factor_list_20261105_<hash>.*       # 筛选查询子集（不更新 latest）
├── latest.json                             # 最近一次全量（101,008 B）
├── latest.parquet                          # 最近一次全量（25,360 B）
└── manifest.json                           # 行数/分类/sha256/token_sha8
```

- 全量 JSON：**101,008 B**
- 全量 Parquet：**25,360 B**
- `sha256`：`80892d701a0ff737c9817c2ca8877cc037beb325e8fbcbc3fb909f38315a7f6f`
- `token_sha8`：`0fd89b25`（只记录指纹，不落明文 token）

## 三、使用方式

```powershell
cd C:\项目\老田的AI助手

# 拉取全量并写 latest/manifest（默认命令）
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py fetch

# 只看统计，不写盘
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py fetch --dry-run --json

# 按分类/资产筛选（子集只写 snapshots）
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py fetch --factor-type Risk --json
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py fetch --asset-type STK --json

# 离线校验 latest（结构/重复/空描述/分类）
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py check
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py status
C:\项目\py312\python.exe -X utf8 tools\data_sources\tushare_factor_list.py show --limit 10
```

退出码：`0` 成功；`2` 无因子库权限；`3` token 无效/过期；`4` 接口或 schema 错误；`5` 网络错误；`6` 未找到 token；`7` 本地快照缺失或校验失败。

Token 解析优先级：`TUSHARE_TOKEN` / `XT_DATA_KEY` 环境变量 → `quant_backtest/.env.local` 的 `XT_DATA_KEY` → `config/tushare_token.txt` → `C:/Temp/tushare_token.txt`。全程不打印明文，只输出 `sha8`。

## 四、错误处理

- `40203/40204`：明确提示「无『因子库』权限，需在 Tushare 权限中心勾选因子库开通」，退出码 2。
- `40101/40102`：token 无效/过期，退出码 3。
- `40002` 或限频文案：按退避重试（默认 2 次），仍失败退出码 4。
- 网络/HTTP 非 200：重试后退出码 5。
- `fields/items` 结构不符、缺必需字段、行长不一致：`TushareSchemaError`，退出码 4。
- 校验门禁：空清单、重复 `factor_name`、数量低于 200、`factor_type` 为空 → 失败；新分类、空描述 → warning（不阻断，便于接口扩容）。

## 五、测试

```powershell
C:\项目\py312\python.exe -X utf8 -m pytest tools\tests\test_tushare_factor_list.py -q
# 18 passed
```

覆盖：token 解析（env/.env.local）、成功拉取、限频重试、权限/认证/网络错误、schema 缺字段、去重排序、离线 validate、快照/latest/manifest、CLI check/status/show、CLI fetch --dry-run、筛选查询不覆盖 latest、无 token 退出码。

## 六、边界与后续

1. 本工具只接入**因子清单元数据**，不计算因子值；因子值需要按 Tushare 文档逐因子用行情/财务表实现（后续可映射到 `tools/research/factor_lib.py`）。
2. 快照放在 `data/factor_library/` 而非 `data/tushare/`：`build_catalog.py` 会把 `data/tushare/<table>/` 当时序表扫描，静态元数据放进去会污染 catalog 表数与 freshness 口径。当前**未登记 catalog**；如要在数据仓可见，需另行走资产登记（catalog 版本/registry 同步）。
3. 接口官方要求单独开通「因子库」权限（个人 2000 元/年，机构 2 万元/年）；本次真实调用 `code=0`，说明当前账号 token 已有权限；若换 token 后报 40203，按回执提示开通即可。
4. 建议后续：把 272 个因子的 `factor_type/factor_desc` 作为因子库元数据源，和现有 17 个自研因子（`tools/research/factor_lib.py`）做映射/去重，再按数据可得性分批实现。
