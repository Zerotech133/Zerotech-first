# 第六批运行说明

本批增加人工审核声明记录API和历史状态查询。当前没有部署真实身份认证适配器，因此CLI人工登记入口保持拒绝；存在工程审核夹具也不代表真实来源已认证。来源真实性、历史可用性和训练资格仍分别阻断。

已接入E盘并完成目标复验：375项中371通过，4个既有Windows符号链接权限跳过。目标真实文件验收通过；最终证据见同目录 `2026-10-07-第六批目标目录验收.json`，完整请求/结果及独立库位于 `第六批真实文件验收/目标目录复验/`。

## 命令入口

在安装后的quant_backtest目录按需运行，使用显式初始化的专用审计库：

```powershell
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator review-history --db '<专用审计库.sqlite>' --input '<历史查询请求.json>'
& 'C:\Program Files\Python311\python.exe' -B -m ai_operator human-review --db '<专用审计库.sqlite>' --input '<人工声明请求.json>'
```

历史查询请求为 `{capture_event_id,review_event_id,as_of}`。两个ID来自第五批真实登记结果；as_of必须带时区且不能在未来。查询使用readonly，不写审计库。记录在实际落库之前不可见，旧接收或旧修订的批准不能被另一个接收继承。

人工声明请求仅包含statement/proof，格式见实施契约。当前返回JSON错误和退出码2，提示认证适配器未配置；不打开可写数据库，不记录伪人工批准。自填姓名、principal、authorized、policy、verifier或trusted都不能获得授权。不存在通过CLI指定Python模块或任意名单绕过认证的路径。

## 应用API边界

`record_human_decision` 和 `load_human_decision` 要求可信应用代码注入verifier；它必须真实验证完整规范声明与认证断言，确定人工主体和审核角色。适配器通过代码依赖注入接线，不能来自请求JSON。正式源码不自带认证成功实现。

认证断言proof用于重验签名或授权证明，不应包含密码、访问令牌或私钥；proof及完整statement被审计事件持久保存并导出。适配器接口为 `verify(statement,proof,*,received_at)`：记录传本次系统接收时间，读取传原事件保存的接收时间。必须基于可重验授权断言/归档策略按该时点核验有效期和撤销，稳定返回相同principal/authority/policy/role；不按自填签发时间接受迟到授权，也不按今天权限抹掉过去有效批准。当前测试的签名适配器仅验证工程夹具，不能复制到生产冒充身份通道。

approve要求文档全部匹配且有未来过期时间；reject可针对缺口材料；revoke只能针对同一capture/review的真实批准，由原批准主体或管理员提交。原事件不覆盖，每次调用生成新的观察。校验、认证和事件/outbox插入在同一事务中完成，失败回滚。

## 历史输出

human_status为pending、approved、rejected、revoked、expired、conflict或unverified。无适配器时不能回放成approved；没有可见人工事件为pending，有可见事件为unverified。多个事件在相同有效时点时为conflict，不用哈希顺序推断人工先后。

最新拒绝、新批准、目标撤销和到期都会影响历史状态；撤销旧批准不撤销新批准，最新批准过期也不回退旧批准。文档缺失或不一致的阻断优先于人工状态。候选超过10000条拒绝查询，不截断成伪完整历史。

即使工程夹具得到approved，status仍blocked_provenance_gaps；source_verified、historical_availability_verified、training_eligible、eligible、production_eligible全部false。第五批journal-outcome仍unknown/audit_only、目标null。

## 交付与限制

公开资料原文和下载回执用于证明本次取得了哪些材料，不能证明旧行情过去何时到达，也不能证明整个本地目录使用同一价格口径。官方日历公告不等于完整逐日日历，对照覆盖不足仍保留缺口。

身份服务、供应商下载链认证、历史特征可用时间、复权因子认证、正式持续采集尚未接通。本批不训练、不交易、不接管生产数据库、不启动常驻服务。
