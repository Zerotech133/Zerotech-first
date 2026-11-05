# 六度选股 · 门 N5 数据源凭证就绪报告（2026-10-21 执行「现有源先接通」）

> 本文档记录 N5 八类真实数据源凭证的**现状摸底与接线声明**，非武装动作。
> 真实武装 `DATA_SOURCE_REAL_AUTH_ENABLED` 仍须 N6 密钥 ceremony + 老田五要素批准，本工位不拍板、不翻转。

## 一、执行口径

用户指令「执行 N5 八类真实数据源凭证」→ 选择「现有源先接通」。
经只读核验 `.env.local`（仅列键名、绝不打印值），发现 12 个键中 **2 个能映射到 N5 八类**，但纪律归属不同：

| N5 八类 | 规范键名 | `.env.local` 实际键 | 处置 | 纪律依据 |
|---|---|---|---|---|
| xtcloud（迅投云） | `XTCLOUD_KEY` | `XT_DATA_KEY` | **别名桥接接通（候选，待 N6 武装）** | 合法量化数据源 |
| 雪球（Snowball） | `SNOWBALL_COOKIE` | `XQ_A_TOKEN` | **脱钩（decoupled），不计入量化就绪** | WO-085：雪球归情报板块、与量化解耦，量化经情报供给层消费 |
| Tushare | `TUSHARE_TOKEN` | — | 缺 | 无现有源 |
| 东财 | `EASTMONEY_TOKEN` | — | 缺 | 无现有源 |
| 股吧 | `GUBA_COOKIE` | — | 缺 | 无现有源 |
| akshare | `AKSHARE_TOKEN` | — | 缺 | 无现有源 |
| crypto | `CRYPTO_API_KEY` | — | 缺 | 无现有源 |
| 飞书 | `FEISHU_APP_SECRET` | — | 缺 | 无现有源 |

## 二、自检器改动（`tools/verify_credential_readiness.py`）

- 新增 `KEY_ALIASES = {"XTCLOUD_KEY": "XT_DATA_KEY"}`：声明 xtcloud 现有实际键满足规范键，标记为 `[在-别名] … 待 N6 武装`。
- 新增 `DECOUPLED_SOURCES = {"Snowball": "XQ_A_TOKEN"}`：雪球键存在但**不计入量化 N5 就绪**，单独标 `[脱钩] … WO-085 归情报`，提示改由情报供给层消费。
- 自检器仅声明候选/脱钩，**绝不翻转 `DATA_SOURCE_REAL_AUTH_ENABLED`、绝不打印值、绝不注入 `os.environ`**。

## 三、就绪自检运行结果（2026-10-21，退出码 2 = 量化 N5 仍缺 6 类）

```
[缺 ] Tushare    TUSHARE_TOKEN
[在-别名] xtcloud XTCLOUD_KEY ← XT_DATA_KEY（现有源先接通，待 N6 武装）
[脱钩] Snowball   SNOWBALL_COOKIE ← XQ_A_TOKEN（WO-085 归情报，量化不直接武装，须经情报供给层）
[缺 ] 东财         EASTMONEY_TOKEN
[缺 ] 股吧         GUBA_COOKIE
[缺 ] akshare    AKSHARE_TOKEN
[缺 ] crypto     CRYPTO_API_KEY
[缺 ] 飞书         FEISHU_APP_SECRET
量化 N5 仍缺 6 类：TUSHARE_TOKEN, EASTMONEY_TOKEN, GUBA_COOKIE, AKSHARE_TOKEN, CRYPTO_API_KEY, FEISHU_APP_SECRET
```

## 四、红线与纪律校验

- `DATA_SOURCE_REAL_AUTH_ENABLED`：**仍为 False**（未武装，休眠默认）。
- 适配器 `strategy_layer/data_source_adapter.py`：**未改动**，保持纯 trial/dormant，real_auth 态仍仅占位、不读任何真实键。
- 八类数据源**零真实值注入、零网络请求、零生产 picks**。
- 雪球（XQ_A_TOKEN）**未**被直接桥接进量化 N5 real_auth，遵守 WO-085 解耦。

## 五、遗留待决（须老田拍板，AI 不代填）

1. **xtcloud 武装**：候选已就位（`XT_DATA_KEY` 存在），但真实接入须在 N6 ceremony + 老田五要素批准后翻转 `DATA_SOURCE_REAL_AUTH_ENABLED`；届时适配器 real_auth 路径可经 `SecretStore`（别名 `XT_DATA_KEY`）解析——本工位仅留接线图，不合保险丝。
2. **雪球路由裁决**：是否维持 WO-085 解耦（量化走情报供给层），还是老田明确裁决允许量化 N5 直持雪球凭证？未裁决前按解耦处理。
3. **其余 6 类缺失**：Tushare/东财/股吧/akshare/crypto/飞书 无任何现有凭证，须老田提供真实值（填 `.env.local`）后方可进入就绪。
4. **N6/N12 联动**：N5 武装本身须 N6 密钥 ceremony 批准；任何真实切流仍受 N12 R2 十前置约束（当前全 False）。

## 六、测试

- `tools/verify_credential_readiness.py` 运行退出码 2（缺 6 类，符合预期）。
- `tools/liudu_trial_smoke.py`（全 trial/合成，零生产触碰）保持通过。
- `tests/test_data_source_adapter.py` 8 例保持通过（未改动适配器常量/开关）。

## 七、老田裁定落码（记录于 2026-11-04）

老田就 N5 八类凭证给出三分裁定，AI 已落码到自检器：

1. **数据不全的源：留在原地**——路径/类别保留，不删不撤。
2. **东财、股吧：可不断丰富**——标 `[缺-丰富]`，缺凭证属预期态，后续增量补齐，不计异常。
3. **飞书：性质待裁**——老田明确「不知道是什么样的数据、有待思考」。AI 摸底结论：`FEISHU_APP_SECRET` 是**飞书开放平台应用密钥**，用途为消息/审批卡/回执（**通知/交付链路**），**不是行情数据源**，inbound 不了任何选股数据。建议（待裁决，未擅自改门定义）：将飞书从 N5 数据源清单**移出**，归位 N9 上行链/回执通知链。裁决前自检器标 `[待裁]`、**不计入量化缺项**。

**裁定后自检输出（退出码 2，量化缺 5 类）**：

```
[缺 ]     Tushare    TUSHARE_TOKEN
[在-别名] xtcloud    XTCLOUD_KEY ← XT_DATA_KEY（现有源先接通，待 N6 武装）
[脱钩]    Snowball   SNOWBALL_COOKIE ← XQ_A_TOKEN（WO-085 归情报）
[缺-丰富] 东财       EASTMONEY_TOKEN（路径保留，增量补齐）
[缺-丰富] 股吧       GUBA_COOKIE（路径保留，增量补齐）
[缺 ]     akshare    AKSHARE_TOKEN
[缺 ]     crypto     CRYPTO_API_KEY
[待裁]    飞书       FEISHU_APP_SECRET（通知链路而非行情源，不计缺项）
```

**N5 现实就绪口径（裁定后）**：量化 N5 真实武装的最小集 = xtcloud（候选已就位，待 N6）+ Tushare/东财/股吧/akshare/crypto 五类增量补齐；雪球经情报供给层；飞书待裁（大概率归通知链，不属 N5）。
