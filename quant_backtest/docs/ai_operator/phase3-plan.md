# 第三批：成熟标签与时间外数据集实施计划

目标：为已交付的影子决策链路增加可重复生成的成熟结果标签、特征白名单、purge/embargo 时间切分与描述性评估。规格为 outputs/2026-10-07-云端语料与本地多AI最优研发方案.md 第 6、7 节；本批不训练模型、不执行交易、不宣称完整 M3/M5 已完成。

架构：新建两个纯函数模块，复用前两批 contracts/shadow 契约；CLI 输入输出 JSON。使用隔离目录实施，独立文件所有权，测试与复审后接入 E 盘既有三个目录，不改变数据库 DDL。原始规则 ZIP 未取得、历史行情可用时点未知的资料继续留在审计用途。

技术：Python 3.11 标准库、unittest；不拉网络行情，不调用本地模型，不修改已有策略。用户已授权并行实施，本计划作为任务台账，不再次请求设计确认。

## 约束和审查重点

- 仅使用简体中文说明和注释，英文 API 保留；apply_patch 编辑，不创建子代理、不提交 Git。
- 标签是未来收盘价格观察值，不是委托成交、卖出做空收益或收益保证；买卖动作分开，未成熟目标为 null。
- 交易日历必须显式提供，不用自然日或缺行情的 bar 数代替交易日；悬缺行情不跳过，不同复权口径不能混合。
- 来源缺可信可用时间时标 audit_only；eval 时点之后到达的价格不能形成成熟目标。
- 切分按标签结束时间和可用时间剔除越界，设置明确 embargo；同内容家族、同证券同 UTC 决策日按连通分组隔离，不允许跨折泄漏。
- 特征是显式批准的有限数值白名单；训练、验证与测试的目标及标签政策必须同一口径。统计只报告描述性结果，不进行参数选择或模型晋升。
- 五项重点：周末/休市和缺 bar；同批延迟价格到达；标签未成熟/未来可用；边界时间等价时区；家族与同日证券的传递关联。

## A：结果标签（代理）

文件所有权：ai_operator/outcomes.py、tests_ai_operator/test_outcomes.py、reports/outcomes.md。

接口：`label_decision(pack, decision, bars, calendar, source, evaluated_at, horizons=None) -> dict`。horizons 默认 [5,20]，非空的唯一正整数列表，最多 10 项且单项 <=250；输入/输出为严格 JSON。

pack 与 decision 为第二批完整对象。通过 shadow.make_shadow_decision 重建核对 decision 的完整字段与内容 ID（使用规范 JSON 比较，数字与布尔不能相混），拒绝任意篡改，environment 必须 shadow。evaluated_at 为带时区字符串，>= decision.as_of。

calendar 固定 {version,sessions}；version 非空，sessions 为非空、唯一升序的 {session,close_at} 列表，session 为 YYYY-MM-DD 且等于 close_at 所在时区的本地日期；close_at 显式时区且严格递增。日历仅为调用者提供的已核验契约，不在本模块认证官方来源。

bars 固定每项 {session,close,close_at,available_at}，session 必须是日历内唯一升序日期，close 为有限正数非bool，close_at 与日历同一瞬间；available_at 可以 null，否则必须有时区且 >=close_at。允许同批多个 bar 使用相同可用时间。最多 5000 根日历/行情，未知字段拒绝。

source 固定 {source_id,source_hash,price_basis,availability_status}；source_id 非空，source_hash 为小写 64 位 SHA256；price_basis 仅 raw/qfq_snapshot/hfq_snapshot；availability_status 仅 trusted/unknown，由调用者声明并保存，不等于本模块认证。

参考日为日历中第一个 close_at 严格晚于 decision.as_of 的 session（不取历史结构点，不用同收盘已决定时刻）。第 H 日终点为参考日在日历中再向后 H 个 session。计算 `(end_close/reference_close)-1`，target_kind='forward_close_return'，无方向翻转、无手续费成交假设。所有动作都仅观察该价格变化，持仓决策应用另行定义。

每个 horizon 返回 {horizon,status,reason,reference_session,label_end_session,label_end_at,label_available_at,reference_close,end_close,target,target_kind}。状态优先级：source unknown 或已存在窗口 bar 的 available_at=null → audit_only；参考/终点超出日历 → pending/calendar_incomplete；窗口任一期缺 bar → pending/missing_bar；任一窗口 bar 的 available_at>evaluated_at → pending/not_available；否则 matured，label_available_at 为整个窗口 available_at 最大值。非 matured 时 reference_close/end_close/target/label_available_at 为 null，label_end_at 有日历则给日历时间否则 null；不能曝光未到达未来价格。reason 以稳定英文代码表示，文档中文说明。

结果顶层 {schema_version:1,decision_id,fact_hash,code,action,environment:'shadow',evaluated_at,source,calendar_version,labels,production_eligible:false,outcome_id}。outcome_id 为除此 ID 外完整结果 SHA256；输入冻结且无副作用，同输入重复一致。

- [x] 测试 RED：缺模块失败；周末日历 H5、窗口缺行情、缺到达时间、未来 eval 价格必须覆盖。
- [x] 最小实现 GREEN；所有状态与哈希、时区、篡改、批量到达行为覆盖，A 23 项通过。
- [x] 完成报告：标签起点/口径/可信来源声明和限制。

## B：数据集与时间切分（代理）

文件所有权：ai_operator/dataset.py、tests_ai_operator/test_dataset.py、reports/dataset.md。

接口：`split_dataset(rows, feature_allowlist, train_end, validation_end, test_end, as_of, embargo_seconds=0) -> dict`；`summarize_split(split: dict) -> dict`。

每行固定字段 {sample_id,code,content_family_id,decision_at,feature_available_at,label_end_at,label_available_at,features,target,target_kind,horizon,label_policy}；ID 唯一非空，code/label_policy 非空，content_family_id 为 null 或非空字符串。target_kind 固定 forward_close_return，horizon 为正整数<=250且全数据集同值，label_policy 全数据集同值。features 对象仅允许白名单内键，必须完整覆盖白名单，所有值为有限 int/float 非bool；target 同样有限数值。拒绝非标准 JSON，未知行字段、白名单重复/空/已知后验字段拒绝（与 shadow 后验集合保持一致，可以只读导入 _POSTERIOR_FIELDS）。允许空 rows，仍严格验证策略参数。

全部时间是显式时区字符串，UTC规范化；满足 feature_available_at<=decision_at<=label_end_at<=label_available_at。切分边界严格 train_end<validation_end<test_end<=as_of；embargo_seconds 为非bool整数>=0。任意行 label_available_at>as_of 为 excluded/immature，不入任何折，不存在未成熟 target 被当成0的流程。

候选训练行 decision_at<train_end，且 label_end_at<train_end、label_available_at<train_end；不满足后两项为 excluded/purge_train。验证 decision_at>=train_end+embargo 且 <validation_end，label_end_at与label_available_at均<validation_end，否则 purge_validation。测试 decision_at>=validation_end+embargo 且 <test_end，label_available_at<=as_of。落 embargo 时段 excluded/embargo；decision_at>=test_end excluded/outside_window。边界采用半开区间，任一标签正好等于训练/验证边界也剔除。

对全部候选行建立连通组：相同非空 content_family_id 或相同 (code,UTC decision日期) 联合分组，传递关联也同组。同组跨折时保留该组最晚一折的行，较早折的行 excluded/group_overlap；不移动行到别的时间折。各折和 excluded 按 (decision_at,sample_id) 稳定排序；excluded 为 {sample_id,reason}。摘要记录分组、purge、embargo、未成熟计数。

返回固定 {schema_version:1,policy,feature_allowlist,train,validation,test,excluded,input_hash,split_hash,production_eligible:false}，policy 包含规范边界、as_of、embargo_seconds、target_kind/horizon/label_policy（空集后两项为null）、group_policy='family_or_code_utc_day_connected'。input_hash 绑定规范化排序的全部输入行（包括剔除行特征/目标）；split_hash 覆盖结果除自身（包括input_hash），同内容行顺序不同返回一致。summarize_split 必须完整校验 split 字段、哈希和切分语义（重跑 split_dataset，输入为train+validation+test；excluded只检查字段/非重复ID/原因集合和不可与保留ID重叠；复跑结果保留折必须相同；input_hash 为64位小写SHA256，无剔除行时重算核对，有剔除行时原输入未随split保存，不能独立重建其input_hash或原excluded决定，故需保留原始输入外部核对），不允许篡改后重哈希的泄漏保留行。输出 {split_hash,partitions:{train/validation/test:{n,mean_target,positive_rate}},excluded_by_reason,production_eligible:false}；空折两个统计null，positive_rate为target>0所占比例。禁止称统计为策略净值/成交收益。

- [x] 测试 RED：缺模块失败；特征黑白名单、标签边界、延迟/未成熟、embargo与分组传递覆盖。
- [x] 最小实现 GREEN；规范时区、乱序确定性、完整结果防篡改与空折覆盖，B 24 项通过；剔除行输入哈希缺口补测 RED 后修复。
- [x] 完成报告：分组剔除、口径限制及无模型训练事实。

## C：联调与验收（主代理）

文件：ai_operator/cli.py、tests_ai_operator/test_phase3_cli.py、acceptance.py、outputs 交付文档。

- [x] CLI RED→GREEN：outcome-label --input（A全部参数）；dataset-split --input（B全部参数）；dataset-summary --input（完整split），均只读JSON输出，不需要DB，沿用8MiB输入限额和错误格式，6 项通过。
- [x] 端到端显式夹具验收：shadow事实与建议→H5/H20成熟和待成熟标签→特征白名单行→purge/embargo/group切分→描述性摘要；未补造真实历史来源时间，夹具与现有生产数据分开。
- [x] 独立审查、修复复审、全量前两批与本批回归。冻结版无阻断项；全量 192 项，190 通过、2 Windows 符号链接权限跳过，本批 53 项全通过。
- [x] 接入E盘限定已有目录，目标目录再测与源哈希核对，交付命令/验收/剩余门槛。目标目录全量 192 项，190 通过、2 权限跳过；夹具端到端通过，存量源 SHA256 不变。
