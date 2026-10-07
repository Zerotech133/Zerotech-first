# 第三批数据来源前置核验

核验日期：2026-10-07。依据本工作区 `plan.md` 的成熟标签与时间外切分契约。

## 结论

两份现有 Parquet 可以用于数据结构审计和后续来源追溯，**不能直接进入本批正式时间外切分**。实际行数只说明文件记录规模，不代表可靠、独立、成熟或无泄漏的样本量。当前正式切分可用性为未满足，不以已有 `ret5/ret10/ret20` 值非空判定成熟。

缺少可信历史特征可用时间、带时区决策时间、标签结束时间、标签可用时间、成熟状态或可重建成熟状态的到达记录，以及已核验交易日历与价格口径。无法验证 `feature_available_at <= decision_at <= label_end_at <= label_available_at`，也无法验证训练与验证边界前标签已经可用。

不能将文件修改时间、当前核验时间、`d0` 日期或其他日期字段替代历史可用时间；不能补造历史时间。未获得可信来源时间的记录应继续标为 `audit_only`。本核验没有转换、重写或训练这些数据。

## 范围与方法

- 只读检查 `E:\老田的AI助手\quant_backtest\data\distill\kg_distill.parquet` 和 `kg_distill_neg.parquet`。
- 使用 `C:\Program Files\Python311\python.exe -B` 与已安装 `pyarrow` 读取 Schema、行数、逐列 null、字符串占位标记及浮点非有限值，使用 `hashlib.sha256` 对原文件内容计算哈希。未调用项目加载器、网络、本地模型或外部数据库。
- 字符串占位标记统计先去首尾空白并转小写，集合为：空串、`unknown`、`unk`、`n/a`、`na`、`none`、`null`、`nan`、`未知`、`不详`、`缺失`、`未提供`。该统计只描述词面匹配，不等于字段业务语义未知；例如 `tag=none` 可能是有效分类，不能未经定义就当缺失。
- 下表的“占位标记”不含 Arrow null，数值列不推断零或其他数值为未知。所有非 null 浮点值的 NaN 与无穷计数均为 0。字段缺席另列为整列不可核验，不伪装成实际 null。
- 本报告为唯一写入文件；E 盘文件未修改。未输出敏感配置。

## 原文件身份

| 文件 | 字节数 | 记录行数 | 行组数 | SHA256 |
|---|---:|---:|---:|---|
| `kg_distill.parquet` | 425709 | 9708 | 1 | `0572d79010820906d53518679912c150d487db8e0ea631f91dae88a2968b6d69` |
| `kg_distill_neg.parquet` | 607786 | 19080 | 1 | `110ce5ddcbea3784744311a490897685af5ac9b7aa31b912a4f410efeb68550e` |

这两个 SHA256 只认证当前文件内容身份，不认证行情来源、历史可用性、价格复权口径或标签生成政策。

## Schema 与逐列缺失

两份文件均含 pandas Schema 元数据；除下表列出的字段外，无其他 Arrow 字段。

### kg_distill.parquet

| 列 | Arrow 类型 | null | 占位标记 |
|---|---|---:|---:|
| `tc` | large_string | 0 | 0 |
| `nm` | large_string | 0 | 0 |
| `d0` | large_string | 0 | 0 |
| `kind` | large_string | 0 | 0 |
| `conf` | double | 0 | 0 |
| `qs` | double | 0 | 0 |
| `qe` | double | 0 | 0 |
| `qh` | double | 0 | 0 |
| `tag` | large_string | 0 | 9521（均为 `none`） |
| `post` | double | 0 | 0 |
| `h1n` | int64 | 0 | 0 |
| `nd` | int64 | 0 | 0 |
| `nvol` | int64 | 0 | 0 |
| `nsig` | int64 | 0 | 0 |
| `noev` | int64 | 0 | 0 |
| `pre10` | double | 275 | 0 |
| `ret5` | double | 0 | 0 |
| `ret10` | double | 134 | 0 |
| `ret20` | double | 1285 | 0 |
| `cd_d0` | large_string | 9676 | 0 |
| `cd_seal5` | int64 | 0 | 0 |
| `cd_broken5` | int64 | 0 | 0 |
| `cd_late5` | int64 | 0 | 0 |
| `verdict` | large_string | 0 | 0 |
| `shape` | large_string | 0 | 0 |
| `conf_band` | large_string | 0 | 0 |

`d0` 的 9708 个值全为 8 位 `YYYYMMDD` 词面格式，没有时区或时刻。`cd_d0` 非 null 仅 32 个，长度分布为 4:6、6:12、9:2、10:12，均不匹配完整 `YYYY-MM-DD`、`YYYYMMDD` 或显式时区后缀。本核验不猜测其语义。

### kg_distill_neg.parquet

| 列 | Arrow 类型 | null | 占位标记 |
|---|---|---:|---:|
| `tc` | large_string | 0 | 0 |
| `d0` | large_string | 0 | 0 |
| `side` | large_string | 0 | 0 |
| `ref_d0` | large_string | 0 | 0 |
| `label` | int64 | 0 | 0 |
| `nvol10` | int64 | 0 | 0 |
| `h1n` | int64 | 0 | 0 |
| `cd_d0` | large_string | 19026 | 0 |
| `cd_seal5` | int64 | 0 | 0 |
| `cd_broken5` | int64 | 0 | 0 |
| `cd_late5` | int64 | 0 | 0 |
| `ret5` | double | 37 | 0 |
| `ret10` | double | 235 | 0 |
| `ret20` | double | 1344 | 0 |

`d0`、`ref_d0` 各 19080 个值全为 8 位 `YYYYMMDD` 词面格式，没有时区或时刻。`cd_d0` 非 null 仅 54 个，长度分布为 4:14、6:13、9:7、10:20，均不匹配完整 `YYYY-MM-DD`、`YYYYMMDD` 或显式时区后缀。`label` 是 int64 字段，字段名及非空本身不能证明它是本批 `forward_close_return` 成熟目标。

## 本批字段与来源缺口

以下项目在两份 Schema 中均缺席，因此涉及的记录数分别为 9708 与 19080；这是“不可核验”的记录数，不是逐列实测 null 数。

| 必要字段或契约 | 当前证据 | 正式使用要求 |
|---|---|---|
| `decision_at` / `as_of` | 只有无时刻、无时区的日期字段 | 取得原始决策的显式时区时间与一致的事实/决策内容身份 |
| `feature_available_at` | 无字段 | 取得每条批准特征在决策前已经可用的可信记录 |
| `label_end_at`、`label_available_at` | 无字段 | 按明确交易日历、完整窗口与行情到达记录生成，不能仅由收益值推定 |
| 标签 `status` / 成熟信息 | 无字段 | 在显式 `evaluated_at` 下重建 `matured` / `pending` / `audit_only`，未成熟目标为 null |
| `content_family_id` | 无字段 | 从原资料建立家族关系；确实未知时契约可为 null，但不能宣称已完成家族隔离 |
| `price_basis` | 无字段 | 核验 `raw` / `qfq_snapshot` / `hfq_snapshot` 中唯一一致口径和快照来源 |
| 行情 `session`、`close`、`close_at`、`available_at` | 只有派生收益字段 | 取得价格观察序列与可用时间，完整窗口不跳过缺 bar |
| `source_id`、`source_hash`、`availability_status` | 无对应来源声明 | 当前文件哈希不能替代原价格来源身份与可信可用性声明 |
| `calendar_version` 与显式 `sessions` | 无字段 | 提供已核验日历，收盘时间显式带时区 |
| `sample_id`、`features`、批准特征白名单 | 未采用本批行契约 | 建立可追溯样本身份与有限数值白名单；后验列不能作决策前特征 |
| `target_kind`、`horizon`、`label_policy` | 无字段 | 核验目标是同口径 `forward_close_return`，同折集 horizon 与标签政策一致 |

`ret5/ret10/ret20` 是潜在后验列，不能加入特征白名单。其他现存数值列也不因数值完整就自动获准，仍需核验业务定义与可用时点。现有收益计算的起点、终点、交易日推进方式、缺行情处理和复权口径均未由本次只读文件证据认证。

## 日历与原始 ZIP 候选

- 使用 `rg --files -g '*calendar*' -g '*Calendar*' -g '*日历*' 'E:\老田的AI助手'` 扫描文件名，无匹配，返回码 1。这仅表示当前扫描没有命名匹配候选，不证明全部项目内容或外部系统不存在交易日历。本核验没有读取数据库，也没有调用网络日历加载器。
- `E:\老田的AI助手` 根目录一级可读，当前 12 个文件中 ZIP 为 0，名称匹配 `beidou`、`quant_kit` / `quant-kit`、`视频库`、`北斗` 的 ZIP 为 0。
- `C:\Users\Administrator\Downloads` 一级枚举被权限限制拒绝，未核验，不能写成目录不存在或没有 ZIP。未请求权限，也未反复向用户追问。先前一级寻找未取得原 ZIP 的事实不能认证其历史内容。

原规则 ZIP 与视频库的原始内容、来源哈希、发布时间和内容家族仍未取得可复核证据。已有 Parquet 文件哈希只供当前文件审计。

## 可恢复的后续路径

先取得并核验显式日历、价格口径和可信行情/特征历史到达记录，再用第三批结果标签模块生成一致标签，并按本批行契约与批准白名单建立输入。仅将满足成熟和时间条件的记录进入 purge / embargo / 内容家族与证券 UTC 同日连通分组切分。没有历史可用时间的旧资料继续保留 `audit_only`，不能通过填入当前时间或日期零点将其升级为正式评估样本。

本批显式测试夹具可以验证模块行为，但不能替代上述真实来源前置条件。当前结论不涉及策略净值、成交收益、模型可靠性或生产资格。
