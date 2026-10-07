# 第二批运行说明：时点回放与影子审计

源码落点：`E:\老田的AI助手\quant_backtest\ai_operator`。本批新增三个命令：`replay`、`fact-pack`、`shadow-decision`。Python 3.11 标准库即可运行；仅工程验收脚本解析 parquet 使用已安装的 pyarrow。

## 1. 逐根回放

在 `E:\老田的AI助手\quant_backtest` 下运行：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator replay --input 'C:\研究目录\bars.json'
```

输入为 JSON 列表，每项严格包含以下字段。这里只是格式示例，示例时间不是任何存量行情的核验记录。

```json
[
  {"dt":"2026-10-06","o":10,"h":11,"l":9,"c":10.5,"v":1000,
   "close_at":"2026-10-06T15:00:00+08:00","available_at":"2026-10-06T15:00:05+08:00"}
]
```

`close_at` 与 `available_at` 须带时区且分别严格递增，后者不得早于前者；OHLC 区间合法，成交量非负。最多 2000 根，未知字段拒绝。默认算法要求 `dt` 字典序严格递增；同一证券、同一周期、同一价格口径由输入提供方保证。批量到达同一时间的数据需另行适配，本入口不支持同一 `available_at` 的多根 K 线。

默认调用现有 `chanlun_bi.analyze`，传前缀收盘价启用原有 MACD 面积/幅度口径。它与 `chanlun_signal.analyze` 是不同信号算法，不能混用版本。每次只给算法当前前缀；输出逐根快照和结构新增/撤销记录，不访问行情 API。

`pivot_at` 是原结构定位标签；`confirmed_at` 是该内容版本连续存在期间的首次观测时点。状态始终为 `observed`，并不承诺永不重绘。撤销后再现保留内容哈希、重新记录出现时点。必须按观测时间回放，不能按历史拐点直接成交。

## 2. 时点事实包

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator fact-pack --input 'C:\研究目录\fact-request.json'
```

请求严格包含 `code/period/as_of/facts/versions`。事实示例：

```json
{
  "code":"600000.SH","period":"1d","as_of":"2026-10-07T16:00:00+08:00",
  "facts":[{"id":"snapshot-1","source":"已核验来源与快照哈希",
    "occurred_at":"2026-10-07T15:00:00+08:00",
    "available_at":"2026-10-07T15:00:05+08:00","data":{"close":10.5}}],
  "versions":{"data":"snapshot-v1","algorithm":"local-chanlun-bi-v1",
    "knowledge":"none-unverified","rules":"none-unverified"}
}
```

每项满足 `occurred_at <= available_at <= as_of`；来源、版本、事实 ID 必填，ID 唯一。返回包绑定 `environment=shadow` 和完整内容 SHA256。版本字段为调用方声明，来源真实性、算法对应源码哈希、复权与发布时间仍需提供方核验；哈希用于内容一致性校验。

递归拒绝已知后验字段：`post/forward_return/label/outcome` 及本地样本的 `r5/r10/r20/r60/ret5/ret10/ret20/ret60/mature5/mature20/post10/post_hm10`。这能拦截已知误入，不能识别所有别名或证明任意事实内容都在历史当时可得。缺供应商历史可用时间的存量资料只能按当前采集时点入审计，不能回填为旧日收盘可知。

## 3. 影子建议登记

先用原有 `init --db` 显式建立独立审计库；`shadow-decision` 要求数据库已存在。输入严格包含 `pack/action/evidence_ids/rationale/valid_until`，其中 `pack` 为上一命令完整返回值。

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator init --db 'C:\研究目录\shadow.sqlite3'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator shadow-decision --db 'C:\研究目录\shadow.sqlite3' --input 'C:\研究目录\decision-request.json'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator snapshot --db 'C:\研究目录\shadow.sqlite3'
```

动作仅 `observe/candidate_buy/candidate_sell/abstain`；证据引用包内 ID，候选买卖至少一项；理由非空，有效期晚于 `as_of`。记录前重验事实包和完整决策。

依次保存 `shadow.fact_pack` 与 `shadow.decision`，通过事实事件 ID 关联。相同输入重复登记不新增；第二条写入失败可能留下事实事件，保留原输入重试即可补齐。两条事件独立提交，不是跨事件原子事务。事实与建议仍为 `confidence=null/execution_allowed=false`；建议并非风险审查通过或交易授权。

## 4. 验证与剩余门槛

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m unittest discover -s tests_ai_operator -v
```

本批是 M2 时点工程和影子审计的增量，未完成完整 M2–M6。原始开发包 ZIP、Schema、规则证据与标准算法仍待独立核验；四组结构对照、收益回测、硬风控、模拟账户、成熟标签与训练、晋升和云端发布按后续阶段实施。本批没有启动定时任务、模型、训练、消息推送或交易。
