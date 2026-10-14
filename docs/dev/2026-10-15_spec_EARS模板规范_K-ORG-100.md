# spec 验收判据 EARS 化模板规范

> 工位编号：**K-ORG-080/N5 · K-ORG-100**（数字军团 N5 工位）
> 配套 lint：`tools/devtask.py` `_ears_lint`（开单时自动 WARN，只提示不拦单，fail-open）
> 冻结面声明：本模板不改 e2e_pipeline.py 任何一行（K-ORG-072 冻结纪律）；QA1 逐条断言
> 指令写在模板文本本身——spec 带 EARS 段 → QA1 自然按段逐条判；无段 → QA1 原行为。

## 一、EARS 句式规范

每条验收判据必须是**单一、可断言**的行为句：

```
WHEN〈触发/场景〉 THE SYSTEM SHALL〈可观察行为〉
```

- **WHEN**：明确的触发条件或输入场景（谁、在什么前提下、做什么操作）。
- **THE SYSTEM SHALL**：系统侧**可观察**行为——有明确输入就有明确期望输出。
- **可断言性红线**：判据必须能回答「QA 拿什么输入、期望看到什么输出」；
  禁用「正确处理」「合理」「酌情」及句尾「等」等模糊词。

### 正例（可断言）

1. `WHEN 用户提交手机号为空的注册表单 THE SYSTEM SHALL 返回 400，响应体含字段名 phone 与错误码 FORM_EMPTY`
2. `WHEN 端口 8100 已被占用 THE SYSTEM SHALL 从 8101 起顺序探测，返回首个 TCP 探测失败的端口并打印分配记录`

### 反例（不可断言）

1. `WHEN 表单填写有误 THE SYSTEM SHALL 正确处理用户输入` ——「正确处理」无判定标准：返回码是什么？提示文案是什么？哪个字段出错？
2. `WHEN 用户点击导出 THE SYSTEM SHALL 导出为 CSV、JSON 等` ——句尾「等」范围不闭合：QA 无法枚举应支持的格式清单。

## 二、模板骨架（工单 desc 可直接粘贴）

```text
## 目标
（一句话）

## 验收判据（EARS）
- WHEN ... THE SYSTEM SHALL ...
（≥3 条）

## 非目标
- （明确不做什么，防 scope 蔓延）

## QA1 判读指令
复核时对本节每条判据逐条给出 ✅/❌ 及证据（#ev:commit/#ev:file/#ev:trace 引用）；最终结论保持「结论：通过/退回」锚点句式不变（下游 e2e 机器解析依赖，K-ORG-071/072 判例）。
```

## 三、示例 desc（已过 `_ears_lint` 自检，0 WARN）

```text
## 目标
看板 board 命令支持按项目线过滤展示。

## 验收判据（EARS）
- WHEN 用户执行 devtask.py board --project quant THE SYSTEM SHALL 仅输出 project=quant 的工单行，其余线工单不出现
- WHEN 用户执行 devtask.py board --project 不存在的线 THE SYSTEM SHALL 输出空表并打印「过滤：project=...」提示，退出码为 0
- WHEN 用户执行 devtask.py board 不带 --project THE SYSTEM SHALL 输出全部工单，行格式与现版本一致
- WHEN 任一工单缺少 project 字段 THE SYSTEM SHALL 按 general 线归入并参与过滤

## 非目标
- 不改 list 命令的输出格式
- 不引入新的持久化字段

## QA1 判读指令
复核时对本节每条判据逐条给出 ✅/❌ 及证据（#ev:commit/#ev:file/#ev:trace 引用）；最终结论保持「结论：通过/退回」锚点句式不变（下游 e2e 机器解析依赖，K-ORG-071/072 判例）。
```

## 四、兼容性声明（N5 边界）

- 本模板**只对新开工单生效**：`devtask.py new` 的 desc 含「## 验收判据」段时触发
  `_ears_lint`；无段的旧式单不受影响，lint 直接跳过返回空。
- **存量工单与 072 金样本一律不追改**（N5 边界，K-ORG-072 冻结纪律）。
- lint 全程**只 WARN 不拦单**（fail-open，与 `_ev_precheck`/K-ORG-099 同款），
  打印格式 `[warn] EARS lint: ...`。
- QA1 verdict 锚点句式「结论：通过/退回」为 e2e 机器解析依赖（e2e L242-248 正则），
  逐条判定表可自由附在锚点之后，但锚点句式本身不可改写。
