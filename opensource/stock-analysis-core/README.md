# stock-analysis-core

> 个股研究流水线开源内核（open-core）：**数据适配层 → 特征 → 质量门 → 规则研判 → 报告渲染**。
> 本仓库只包含**框架与接口**，不包含任何真实行情/财务数据、密钥或供应商受限数据；示例数据为**程序生成的合成数据**。
> 研究分析工具，不构成投资建议。

## 定位

- 给"想自己搭个股研究报告流水线"的开发者/研究者一个**可跑通的骨架**：
  - 数据源：实现一个 `DataSource` 适配器（CSV / 自有库 / 任何行情接口）；
  - 质量门：G1 覆盖与时效、G2 可溯源、G3 口径一致、G4 交付完整（可扩展）；
  - 研判：可配置的因子打分 + 评级切档 + 风险覆盖（默认权重仅示例）；
  - 渲染：Markdown 报告 + `analysis.json`（H5/PDF 可自行扩展）。
- **不包含**：真实数据、数据仓库、事件复核判例、申万同业映射、评分调参结果——这些属于商业化层。

## 快速开始

```bash
# 1) 安装（无需任何第三方数据）
pip install -e .

# 2) 生成示例数据（合成数据，可重复）
stock-analysis sample --out ./demo_data

# 3) 跑一只示例股票
stock-analysis run --code SAMPLE001 --data-dir ./demo_data --out ./demo_out

# 产物：demo_out/SAMPLE001/{report.md, analysis.json}
```

也可以用 Python API：

```python
from stock_analysis_core.pipeline import Pipeline
r = Pipeline().run(code="SAMPLE001", data_dir="./demo_data", out_dir="./demo_out")
print(r["verdict"]["rating"], r["verdict"]["score"])
```

## 目录结构

```
stock_analysis_core/
  types.py          # Snapshot / Bar / Event / CheckResult / VerdictResult
  adapters/         # DataSource 协议 + CSV 实现（示例）
  features.py       # 技术面 / 估值 / 财务特征
  gates.py          # G1-G4 质量门框架
  verdict.py        # 可配置打分与评级（默认权重仅示例）
  render.py         # Markdown 报告渲染
  pipeline.py       # 编排：适配器→特征→质量门→研判→渲染
  cli.py            # stock-analysis run/sample/version
tests/              # 端到端测试（合成数据，无网络）
sample_data/        # 生成的示例 CSV（合成数据）
```

## 接入自己的数据

实现 `stock_analysis_core.adapters.base.DataSource` 的 4 个方法即可：

```python
class MySource(DataSource):
    def bars(self, code: str) -> list[Bar]: ...
    def fundamentals(self, code: str) -> list[dict]: ...
    def valuation(self, code: str) -> list[dict]: ...
    def events(self, code: str) -> list[dict]: ...
```

## 定制打分

修改 `verdict.VerdictPolicy`（权重、切档、风险覆盖）或传入自己的 policy 实例；
**默认权重仅作演示，不构成任何投资建议**。

## 许可证

Apache-2.0。第三方数据的使用/转售限制由数据提供方决定，本仓库不附带任何数据。
